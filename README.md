# Firm Bulk Payments

A firm pays many firms from its platform balance in one request.
Design and v1 guarantees: [docs/adr/0001-bulk-payments-v1.md](docs/adr/0001-bulk-payments-v1.md).

## Run

```bash
make up      # db, migrate + seed job, 2 app replicas, nginx on :8000
curl localhost:8000/health
make down    # stop and drop the database volume
```

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
