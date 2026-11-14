# Firm Bulk Payments

A firm pays many firms from its platform balance in one request.
Design and v1 guarantees: [docs/adr/0001-bulk-payments-v1.md](docs/adr/0001-bulk-payments-v1.md).

## Run

```bash
docker compose up --build
curl localhost:8000/health
```

## Test

```bash
uv sync
uv run pytest
```
