import os

DEFAULT_DATABASE_URL = "postgresql+psycopg://payments:payments@localhost:5432/payments"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
