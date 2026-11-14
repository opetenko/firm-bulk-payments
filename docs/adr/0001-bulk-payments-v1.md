# ADR 0001: Bulk payments v1

- Status: Accepted (frozen for v1)
- Date: 2026-11-14

## Context

Firms on the platform hold a balance with us and settle debts with each other through it.
A firm must be able to pay many firms in one request. The v1 goal is the simplest service
that is provably correct under concurrent use: no overdraft, no double payment, no lost update,
no deadlock.

Stack: Python 3.13, uv, FastAPI, Pydantic v2, SQLAlchemy 2.x, psycopg 3, Alembic, PostgreSQL.

## Decision

### API

`POST /bulk-payments`, header `Idempotency-Key` (required, 1–255 chars, ASCII 0x20–0x7E).

```json
{
  "payer_firm_uuid": "3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41",
  "payments": [
    {"amount": "1200.75", "payee_firm_uuid": "8b2e4c71-...", "description": "Bookkeeping cleanup"}
  ]
}
```

Validation (all failures → 400):

- `amount`: JSON string matching `^\d{1,9}(\.\d{1,2})?$` (ASCII digits only, whole string),
  value greater than zero. Converted to integer cents by string splitting: no `float`,
  no `Decimal` field type. All money in the model and database is integer cents.
- `payments`: 1–1000 entries. `description`: 1–500 chars. Unknown fields are rejected.
- `payee_firm_uuid`, `payer_firm_uuid`: Pydantic `UUID`; compared with the database as
  `str(uuid)` (canonical lowercase).

Responses, in evaluation order (first match wins):

| # | Condition | Response |
|---|-----------|----------|
| 1 | Malformed JSON, wrong types, bad amount, empty/oversized `payments` | 400 `{"error": "invalid_request", "detail": [...]}` |
| 1 | Missing or malformed `Idempotency-Key` | 400 `{"error": "invalid_idempotency_key"}` |
| 2 | Payer listed as a payee | 422 `{"error": "self_payment"}` |
| 3 | Unknown payer or payee UUID | 422 `{"error": "unknown_firm"}` |
| 4 | Key already applied, different request | 400 `{"error": "idempotency_key_reuse"}` |
| 5 | Key already applied, same request | 201, original body, header `Idempotent-Replayed: true` |
| 6 | Total exceeds payer balance | 422 `{"error": "insufficient_funds"}` |
| 7 | Lock, statement or connection-pool timeout | 503 `{"error": "busy"}`, `Retry-After` header |
| 8 | Success | 201 `{"batch_uuid": "...", "payment_ids": [...], "total_cents": N}` |

Only successful requests are recorded. A request rejected with 422/503 writes nothing, so a retry
with the same key is evaluated from scratch.

### Data model

```
firms
  id             BIGINT PK
  name           TEXT NOT NULL
  balance_cents  BIGINT NOT NULL CHECK (balance_cents >= 0)
  uuid           TEXT NOT NULL UNIQUE

payment_batches
  id                   BIGINT PK GENERATED ALWAYS AS IDENTITY
  uuid                 TEXT NOT NULL UNIQUE           -- public id, uuid4 from the app
  payer_firm_id        BIGINT NOT NULL FK -> firms.id
  idempotency_key      TEXT NOT NULL
  request_fingerprint  TEXT NOT NULL                  -- SHA-256 hex, see below
  total_cents          BIGINT NOT NULL CHECK (total_cents > 0)
  payments_count       INTEGER NOT NULL CHECK (payments_count > 0)
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
  UNIQUE (payer_firm_id, idempotency_key)

payments
  id             BIGINT PK GENERATED ALWAYS AS IDENTITY
  batch_id       BIGINT NOT NULL FK -> payment_batches.id
  payer_firm_id  BIGINT NOT NULL FK -> firms.id
  payee_firm_id  BIGINT NOT NULL FK -> firms.id
  amount_cents   BIGINT NOT NULL CHECK (amount_cents > 0)
  description    TEXT NOT NULL
  CHECK (payer_firm_id <> payee_firm_id)
  INDEX (batch_id), INDEX (payer_firm_id), INDEX (payee_firm_id)
```

`request_fingerprint` = SHA-256 of canonical JSON of the validated request: payer UUID plus every
entry in request order as (lowercase payee UUID, amount in cents, description). It is computed
over the full entry list, never over aggregated credits.

### Transaction (READ COMMITTED)

```
before BEGIN:
  validate (400), reject self_payment (422)
  credits = payee -> summed cents; total = sum(credits); fingerprint = sha256(canonical request)

BEGIN
  SELECT set_config('lock_timeout', ..., true); SELECT set_config('statement_timeout', ..., true)

  SELECT id, uuid, balance_cents FROM firms
    WHERE uuid = ANY(:payer_and_payees) ORDER BY id FOR NO KEY UPDATE
  missing firm                         -> ROLLBACK, 422 unknown_firm

  SELECT ... FROM payment_batches WHERE payer_firm_id = :payer_id AND idempotency_key = :key
  found, fingerprint differs           -> ROLLBACK, 400 idempotency_key_reuse
  found                                -> read payment ids ORDER BY id, COMMIT (read-only), 201 replay

  payer.balance_cents < total          -> ROLLBACK, 422 insufficient_funds

  INSERT payment_batches ... RETURNING id
  INSERT payments (one statement, request order) RETURNING id   -- sort_by_parameter_order=True
  UPDATE firms SET balance_cents = balance_cents + :delta WHERE id = :id
    -- payer: -total; each payee: aggregated credit
COMMIT -> 201
```

### Correctness guarantees

1. **No overdraft.** The balance is checked after the payer row is locked; `FOR NO KEY UPDATE`
   returns the latest committed version. `CHECK (balance_cents >= 0)` is the backstop.
2. **No deadlock.** Every transaction locks all firm rows it touches in one statement, ordered by
   `id`, before any write. Later updates touch only rows already held. *Rule for all other code:
   any path that locks more than one firm must lock in ascending `id` order.*
3. **Exactly-once per key.** Requests with the same key share the payer, so they serialize on the
   payer row lock. The waiting request's idempotency `SELECT` gets a fresh snapshot and sees the
   committed batch. `UNIQUE (payer_firm_id, idempotency_key)` is the backstop.
4. **Atomicity.** Batch, payments and all balance changes commit together or not at all.
5. **Conservation.** Sum of all balances is unchanged by any batch (debit = sum of credits).
6. **Stable replay.** Payment ids are returned in request order and replayed with `ORDER BY id`,
   so the replay body is byte-identical to the original.

Verified by `tests/test_concurrency.py` against real PostgreSQL (not SQLite), 20 concurrent
transactions per scenario: drain of one payer, firms paying each other (pairs and 3-cycles),
one key sent concurrently with the same and with different bodies, and a random mix checked
for conservation. Variants with unordered or missing locks fail these tests.

### Code structure

```
app/domain/          money parsing, BulkPaymentCommand, domain errors   (pure Python)
app/application/     PayBulk use case: transaction and idempotency       (no retry loop)
app/infrastructure/  engine, Core tables, the SQL of the transaction
app/api/             Pydantic schemas, routes, exception handlers
```

Dependencies: api → application → domain, application → infrastructure. The domain
imports nothing from other layers.

### Deployment

The app is stateless: two or more replicas behind nginx (round-robin, runtime DNS).
Correctness relies only on PostgreSQL row locks, so it holds across processes. Each replica
has a fixed pool (`DB_POOL_SIZE`, default 20, no overflow; replicas × pool size must stay
below PostgreSQL's `max_connections`). A request that gets no connection within
`DB_POOL_TIMEOUT_SECONDS` (default 2, equal to `lock_timeout`) gets 503 busy. Migrations
run once in a dedicated job before the replicas start. Demo firms are inserted by
`scripts/seed.py`, never by a migration.

## Consequences

Pros:

- One code path, one isolation level, no retry loops; correctness follows from lock order.
- Idempotency needs no extra table or "in progress" state.
- Rejected requests leave no trace, so retries are simple for clients.
- Schema is compatible with moving to an append-only ledger later.

Cons:

- Batches touching the same firm run one at a time; a popular payee is a hot row and caps throughput.
- Replays take the same locks as payments and wait behind in-flight batches.
- Mapping `payment_ids[i]` to `payments[i]` relies on SQLAlchemy's `sort_by_parameter_order`.
- Idempotency keys never expire.

Out of scope for v1 (explicit non-goals):

- Authentication and authorization: anyone knowing a payer UUID can pay from it.
- Netting of mutual debts, partial success, scheduling, currencies other than USD.
- Reading batches or balances through the API.
