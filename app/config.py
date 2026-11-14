import os

_DEFAULT_DATABASE_URL = "postgresql+psycopg://payments:payments@localhost:5432/payments"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_DATABASE_URL)


def db_pool_size() -> int:
    """Connections per replica. Replicas x pool size must stay below PostgreSQL's
    max_connections (default 100): 2 replicas x 20 leaves room to scale to 4."""
    return int(os.environ.get("DB_POOL_SIZE", "20"))


def db_pool_timeout_seconds() -> float:
    """How long a request waits for a free connection before 503 busy.
    Matches PayBulk's 2 s lock_timeout, so "busy" means the same bound everywhere."""
    return float(os.environ.get("DB_POOL_TIMEOUT_SECONDS", "2"))
