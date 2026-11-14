# Firm Bulk Payments

Design, API contract and correctness guarantees: `docs/adr/0001-bulk-payments-v1.md`.
Layers: `app/domain` (pure) ← `app/application` → `app/infrastructure`; `app/api` → application.

## Typing rule (enforced in CI)

Types are how this codebase turns runtime errors into check-time errors.

- Annotate every function: all parameters and the return type, tests included.
  Enforced by ruff `ANN` and `mypy --strict`.
- No `Any` in our code. For unknown input use `object` and narrow with `isinstance`.
- Narrow untyped boundaries where they enter: SQLAlchemy Core results, `app.state`,
  third-party callbacks, JSON. Prefer a checked narrowing (`isinstance` + raise) over
  `cast()`; `cast()` and `# type: ignore[code]` need a comment saying why.
- Generic containers always carry their element types (`dict[str, int]`, not `dict`).
- Money is `int` cents everywhere past the API schemas; never `float`.

## Before handing over a change

```bash
make fmt     # ruff fix + format
make check   # ruff, ruff format --check, mypy --strict, pytest with coverage >= 95%
```

Tests need PostgreSQL on localhost:5432 (`make db`). Write business-logic and API tests;
concurrency guarantees are tested against real PostgreSQL, never SQLite.
