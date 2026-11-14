# Firm Bulk Payments

A firm pays many firms from its platform balance in one request.
Design and v1 guarantees: [docs/adr/0001-bulk-payments-v1.md](docs/adr/0001-bulk-payments-v1.md).

## Run

```bash
make up      # db, migrate + seed job, 2 app replicas, nginx on :8000
curl localhost:8000/health
make down    # stop and drop the database volume
```

## Use

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

`201 {"batch_uuid": "...", "payment_ids": [1, 2, 3], "total_cents": 1325125}`. Sending the
same request again with the same key returns the same body with `Idempotent-Replayed: true`.
All responses and error codes: see the response table in the ADR.

## Test

Tests need PostgreSQL on localhost:5432 (they use a separate `payments_test` database).

```bash
make test    # starts the db container, then runs pytest
```

## Layout

```
app/domain/          money, bulk payment command, domain errors
app/application/     PayBulk use case
app/infrastructure/  db engine, Core tables, SQL store
app/api/             schemas, routes, error handlers
migrations/          Alembic (schema only)
scripts/seed.py      demo firms
```
