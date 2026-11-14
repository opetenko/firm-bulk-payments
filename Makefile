.PHONY: up down logs db test cov lint fmt typecheck check migrate seed

up:        ## build and start db, migrate job, 2 app replicas, nginx on :8000
	docker compose up -d --build --wait

down:      ## stop everything and drop the database volume
	docker compose down -v

logs:
	docker compose logs -f app nginx

db:        ## start only PostgreSQL (enough for tests)
	docker compose up -d --wait db

test: db
	uv run pytest -v

cov: db    ## tests with coverage report; fails under the threshold in pyproject.toml
	uv run pytest --cov --cov-report=term-missing

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:       ## auto-fix lint findings and format
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy

check: lint typecheck cov   ## everything CI runs, locally

migrate:   ## apply migrations to DATABASE_URL (default: local compose db)
	uv run alembic upgrade head

seed:      ## insert demo firms into DATABASE_URL
	uv run python -m scripts.seed
