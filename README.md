# Firm Bulk Payments

A firm pays everyone it owes, from its platform balance, in **one request**. The batch is
all-or-nothing, a retried request never pays twice, and concurrent batches can neither
overdraw a balance nor deadlock each other, across any number of app replicas.

> This README is written as the pull-request description for teammates. The full design
> record, with the response table and every guarantee, is
> [docs/adr/0001-bulk-payments-v1.md](docs/adr/0001-bulk-payments-v1.md).

**Contents:** [Solution](#solution) · [Architecture decisions beyond the spec](#architecture-decisions-beyond-the-spec) ·
[High-level design](#high-level-design) · [Issues we ran into](#issues-we-ran-into) ·
[Assumptions](#assumptions) · [Possible improvements](#possible-improvements) ·
[Run and verify](#run-and-verify)

---

## Solution

`POST /bulk-payments` takes a payer and a list of payments. In **one PostgreSQL transaction**
it records the batch, inserts one `payments` row per entry, debits the payer by the total and
credits each payee (credits are summed per payee first).

```http
POST /bulk-payments
Idempotency-Key: 7f3c-2026-08-settlement

{"payer_firm_uuid": "3f1c9a2e-…", "payments": [
  {"amount": "6250",    "payee_firm_uuid": "e5f18b3c-…", "description": "Overflow returns"},
  {"amount": "1200.75", "payee_firm_uuid": "8b2e4c71-…", "description": "Bookkeeping cleanup"}]}

201 {"batch_uuid": "…", "payment_ids": [1, 2], "total_cents": 745075}
```

| Situation | Response |
|---|---|
| Success | `201` with `batch_uuid`, `payment_ids` (request order), `total_cents` |
| Same key, same request (retry) | `201`, byte-identical body, `Idempotent-Replayed: true` |
| Same key, different request | `400 idempotency_key_reuse` |
| Bad JSON / types / amount / list size, bad or missing key | `400 invalid_request` / `400 invalid_idempotency_key` |
| Payer listed as payee · unknown firm · not enough money | `422 self_payment` · `unknown_firm` · `insufficient_funds` |
| Locks, statement or connection pool busy too long | `503 busy` + `Retry-After` (nothing was written; retry with the same key) |

### Approach: correctness from lock order, not from retries

The core is one transaction at PostgreSQL's default `READ COMMITTED` isolation:

```
validate (400) · reject self-payment (422) · compute credits per payee, total, fingerprint
BEGIN
  lock ALL firms of the batch:  SELECT … WHERE uuid IN (…) ORDER BY id FOR NO KEY UPDATE
  any firm missing?             -> 422 unknown_firm
  batch with this key exists?   -> same fingerprint: 201 replay · different: 400 reuse
  payer balance < total?        -> 422 insufficient_funds
  INSERT payment_batches · INSERT payments (one statement) · UPDATE firms (one per firm)
COMMIT -> 201
```

Why this is enough:

- **No overdraft.** The balance is checked on a row we hold locked, so nobody can spend it
  between the check and the debit. `CHECK (balance_cents >= 0)` is the safety net.
- **No deadlock.** Every transaction takes all of its row locks in one statement, in
  ascending `id` order, before writing anything. Lock cycles are impossible, so there is no
  retry loop anywhere in the code.
- **Exactly once per key.** Two requests with the same key have the same payer, so they queue
  on the payer's row lock. The second one then sees the committed batch and replays it.
- **Stateless replicas.** All coordination lives in PostgreSQL, so correctness does not
  depend on how many app processes run or which one gets the retry.

Code is layered so the business rules have no framework imports:

```
app/domain/          money parsing (string -> int cents), BulkPaymentCommand, domain errors
app/application/     PayBulk use case: the transaction above
app/infrastructure/  engine + pool, SQLAlchemy Core tables, all SQL (store.py)
app/api/             Pydantic schemas, routes, error -> HTTP mapping
```

---

## Architecture decisions beyond the spec

The spec defined `firms` and `payments`. I added the following to make the service
correct under retries and concurrency.

### 1. New table `payment_batches`

```
payment_batches
  id                   BIGINT identity PK
  uuid                 TEXT UNIQUE            -- public id returned to the client
  payer_firm_id        BIGINT FK -> firms
  idempotency_key      TEXT NOT NULL
  request_fingerprint  TEXT NOT NULL          -- SHA-256 of the canonical request
  total_cents          BIGINT CHECK (> 0)
  payments_count       INTEGER CHECK (> 0)
  created_at           TIMESTAMPTZ
  UNIQUE (payer_firm_id, idempotency_key)
```

A bulk payment is one business operation, so it gets one record. That record is what a retry
finds, what the response's `batch_uuid` points to, and where the batch's total and size live
for auditing.

### 2. `payments.batch_id`

Every payment row points to its batch (`FK`, indexed). The spec's `payments` columns are
unchanged; this one column ties N payment rows to the operation that created them. A replay
reads `SELECT id FROM payments WHERE batch_id = … ORDER BY id`, which is what makes the
replayed body byte-identical to the original.

### 3. Idempotency: required `Idempotency-Key` + request fingerprint

- The header is **required** (1–255 printable ASCII). For money, an optional key means a
  client timeout plus retry pays twice.
- Keys are scoped **per payer**: `UNIQUE (payer_firm_id, idempotency_key)`. Two firms can
  pick the same key without colliding.
- The **fingerprint** (SHA-256 over the payer and every entry, in order, after normalising
  amounts to cents and UUIDs to lowercase) tells a true retry from a key reused for a
  different request. `"300"` and `"300.00"` are the same request; reordered entries are not.
- Only **successful** batches are recorded. A rejected request writes nothing, so the same key
  can be retried after the payer is topped up.

Also part of the frozen design: money is **integer cents** end to end (amounts arrive as
strings and are converted by string splitting, never `float`).

---

## High-level design

```
                        clients (firm back offices, UI)
                                     │  POST /bulk-payments
                                     │  Idempotency-Key: …
                                     ▼
                          ┌─────────────────────┐
                          │        nginx        │  round-robin, runtime DNS,
                          │       :8000         │  1 MB body limit
                          └──────────┬──────────┘
                     ┌───────────────┴───────────────┐
                     ▼                               ▼
          ┌─────────────────────┐         ┌─────────────────────┐
          │   app replica 1     │   ...   │   app replica N     │   stateless
          │ FastAPI             │         │                     │
          │  api ─► PayBulk ─►  │         │                     │
          │         store (SQL) │         │                     │
          │ pool: 20 conn, 2 s  │         │                     │
          └──────────┬──────────┘         └──────────┬──────────┘
                     └───────────────┬───────────────┘
                                     ▼
                ┌─────────────────────────────────────────┐
                │               PostgreSQL                │
                │                                         │
                │   firms ◄──── payments ────► payment_   │
                │  (balance,     (batch_id)     batches   │
                │   row locks)                 (key, fp)  │
                └─────────────────────────────────────────┘
                                     ▲
                          ┌──────────┴──────────┐
                          │  migrate job (once) │  alembic upgrade + seed,
                          └─────────────────────┘  before replicas start
```

---

## Issues we ran into

| Issue | What would have happened | Resolution |
|---|---|---|
| Check balance, then debit | Two concurrent batches both pass the check and overdraw | Check on a locked row (`FOR NO KEY UPDATE`) |
| A pays B while B pays A | Each locks its payer first, then waits on the other: deadlock | Lock all firms in one statement, ordered by `id` |
| Same key sent twice at once | Both miss the lookup and both pay | Payer row lock serialises them; unique key as backstop |
| `\d` and `$` in the amount regex | `"١٢٣"` (Arabic digits) and `"5\n"` pass validation | `[0-9]` with `fullmatch()` |
| `SET LOCAL lock_timeout = %s` | psycopg 3 binds server-side; `SET` rejects parameters | `SELECT set_config('lock_timeout', %s, true)` |
| `INSERT … RETURNING` order | PostgreSQL does not promise rows in input order | SQLAlchemy `sort_by_parameter_order=True` (verified in SQL logs) |
| FastAPI validation errors | Default status is 422, which the spec reserves for business errors | Handler overrides validation errors to 400 |
| nginx with 2 replicas | libc resolved `app` to one IP: all traffic hit one replica | `resolver 127.0.0.11` + `server app:8000 resolve` |
| Migrations at container start | Replicas race on `alembic upgrade` | One-shot `migrate` job; replicas wait for it |
| Demo firms in a migration | Every environment, production included, gets demo data | `scripts/seed.py`, outside the migration chain |
| Connection pool exhausted | Request waited 30 s, then failed with **500** | Fixed pool (20, no overflow), 2 s wait, mapped to 503 |
| Lint rule for private imports | `PLC2701` does not check imports inside one package | Kept for tests/scripts; documented the gap |

Every concurrency fix is proven by a test that **fails** when the fix is removed: unordered
locks produced real `DeadlockDetected` errors, and lock-free reads overdrew and double-paid.

---

## Assumptions
- **Authentication and authorization.** The caller must be authenticated and allowed to act for `payer_firm_uuid`.
- **USD only**, amounts `"1"`–`"999999999.99"` as JSON strings, at most 2 decimals, > 0.
  Leading zeros are accepted (`"007"` = $7.00).
- 1–1000 payments per batch; descriptions 1–500 characters; unknown JSON fields are rejected.
- The same payee may appear several times: separate `payments` rows, one aggregated credit.
- Error precedence: `400` validation → `422 self_payment` → `422 unknown_firm` → idempotency
  (replay / `400` reuse) → `422 insufficient_funds`. A replay therefore succeeds even after the
  payer has spent the money.
- A rejected request (`4xx`/`503`) leaves no trace; only successful batches hold a key.
- Firms are created elsewhere on the platform; this service only reads and moves balances.
  Any other code that locks **more than one** firm must lock in ascending `id` order.
- **No authentication or authorization** in v1 (explicit non-goal, see below).

---

## Possible improvements

### Webhooks via a transactional outbox (next feature)

Downstream services (notifications, accounting exports) and firms' own systems need to hear
about batches. The plan is a **transactional outbox read by CDC**:

```
BEGIN
  … the batch transaction as today …
  INSERT INTO outbox_events (id, aggregate_id, type, payload, created_at)
    one 'bulk_payment.completed' for the payer,
    one 'payment.received' per payee            ◄── same transaction
COMMIT
        │
        ▼  WAL (logical replication)
   CDC (e.g. Debezium) ──► Kafka topic (key = firm id) ──► webhook publisher ──► firm endpoints
```

Why this shape: the event commits **if and only if** the money moved. There is no "paid but
not announced" and no "announced but rolled back", and a replay writes no second event. It
adds one cheap insert inside a transaction that already holds its locks.

What it does not give for free, to design in:

- **At-least-once delivery.** CDC and webhook retries can duplicate, so every event carries a
  stable `event_id`, and the publisher sends it as the webhook's idempotency key (plus an
  HMAC signature).
- **Ordering per firm** holds only if the topic is partitioned by firm id.
- **Operations.** It needs `wal_level=logical` and a replication slot. A slot nobody consumes
  retains WAL and can fill the disk, so slot lag must be monitored and alerted on.
- **Table growth.** Rows can be deleted once captured, or the table can be replaced by
  `pg_logical_emit_message()` so no table grows at all.

### Other improvements, by priority

1. **Hot payees.** Batches crediting the same firm serialise on its row. If a payee becomes a
   bottleneck: an append-only ledger of entries with balances derived from it, so credits
   need no shared row lock.
2. **Read API**: `GET /bulk-payments/{batch_uuid}` and balance queries, so clients can
   reconcile without relying on retries.
3. **Accurate OpenAPI**: it still advertises FastAPI's 422 validation shape; document the real
   400/422/503 bodies.
4. **Observability**: metrics for lock waits, 503 rate, pool usage; a readiness check that
   pings the database (`/health` is liveness only).

---

## Run and verify

Requires Docker and [uv](https://docs.astral.sh/uv/). Python 3.13 is installed by uv.

```bash
make up      # PostgreSQL, migrate + seed job, 2 app replicas, nginx on :8000
```

Pay with the spec example, then send the identical request again:

```bash
curl -i -X POST localhost:8000/bulk-payments \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-1' \
  -d '{
    "payer_firm_uuid": "3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41",
    "payments": [
      {"amount": "6250",    "payee_firm_uuid": "e5f18b3c-2a9d-4c07-8e6b-1d4a7f9c3b25", "description": "Overflow returns, August 2026"},
      {"amount": "5800.5",  "payee_firm_uuid": "e5f18b3c-2a9d-4c07-8e6b-1d4a7f9c3b25", "description": "Amended returns, August 2026"},
      {"amount": "1200.75", "payee_firm_uuid": "8b2e4c71-0d3a-4f6e-b1c9-5a7d2e9f4c10", "description": "Bookkeeping cleanup, 3 clients"}
    ]
  }'
```

- First call: `201` with `"total_cents": 1325125`.
- Second call: same body plus `Idempotent-Replayed: true`, typically answered by the other
  replica.

Check the balances against the spec ($36,748.75 / $1,700.75 / $14,050.50):

```bash
docker compose exec db psql -U payments -d payments -c "SELECT name, balance_cents FROM firms ORDER BY id"
```

Run everything CI runs (ruff, format check, `mypy --strict`, 113 tests with coverage ≥ 95%):

```bash
make check
```

The tests run against real PostgreSQL (`make db` starts it; they use a separate
`payments_test` database):

| Suite | Proves |
|---|---|
| `test_concurrency.py` | 20 simultaneous transactions per scenario: no overdraft (exactly 7 of 20 fit), no deadlock (pairs and 3-cycles), one key pays once, money is conserved |
| `test_api.py` | Every row of the response table, including 503 on lock and pool timeouts |
| `test_pay_bulk.py` | Business rules end to end: replay after the balance is spent, rejected requests leave no trace |
| `test_store.py` | The SQL: lock order, lock timeout (`55P03`), payment ids in request order |
| `test_bulk_payment.py`, `test_money.py`, `test_request_schema.py` | Fingerprint, per-payee credits, amount parsing, request validation |

```bash
make down    # stop everything and drop the database volume
```

CI (`.github/workflows/ci.yml`) runs three jobs on every pull request: **lint** (ruff, mypy),
**test** (PostgreSQL service, coverage gate) and **build** (Docker image).
