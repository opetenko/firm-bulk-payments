import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, make_url, text

from scripts.seed import seed

ROOT = Path(__file__).resolve().parent.parent

# A dedicated database, so tests never touch data in the dev database.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://payments:payments@localhost:5432/payments_test",
)


def _create_database_if_missing(url: str) -> None:
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": target.database},
        )
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    admin.dispose()


def alembic_config() -> Config:
    return Config(str(ROOT / "alembic.ini"))


@pytest.fixture(scope="session")
def migrated_engine() -> Iterator[Engine]:
    """Test database rebuilt from scratch with every migration applied."""
    _create_database_if_missing(TEST_DATABASE_URL)
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL  # read by migrations/env.py
    cfg = alembic_config()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as conn:
        seed(conn)
    yield engine
    engine.dispose()


@pytest.fixture
def db(migrated_engine: Engine) -> Iterator[Connection]:
    """Connection inside a transaction that is always rolled back."""
    with migrated_engine.connect() as conn:
        trans = conn.begin()
        yield conn
        trans.rollback()
