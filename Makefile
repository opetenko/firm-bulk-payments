.PHONY: up down logs db test migrate seed

up:        ## build and start db, migrate job, 2 app replicas, nginx on :8000
	docker compose up -d --build

down:      ## stop everything and drop the database volume
	docker compose down -v

logs:
	docker compose logs -f app nginx

db:        ## start only PostgreSQL (enough for tests)
	docker compose up -d --wait db

test: db
	uv run pytest -v

migrate:   ## apply migrations to DATABASE_URL (default: local compose db)
	uv run alembic upgrade head

seed:      ## insert demo firms into DATABASE_URL
	uv run python -m scripts.seed
