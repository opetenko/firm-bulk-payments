from sqlalchemy import create_engine

from app.config import database_url, db_pool_size, db_pool_timeout_seconds

# Created lazily by SQLAlchemy: no connection is opened until first use.
# A fixed-size pool (no overflow) with a short checkout timeout: when every
# connection is busy, a request fails fast with 503 instead of waiting 30 s
# (SQLAlchemy's default) and then failing with 500.
engine = create_engine(
    database_url(),
    pool_pre_ping=True,
    pool_size=db_pool_size(),
    max_overflow=0,
    pool_timeout=db_pool_timeout_seconds(),
)
