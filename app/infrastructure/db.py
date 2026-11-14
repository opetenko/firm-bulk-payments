from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import database_url

# Created lazily by SQLAlchemy: no connection is opened until first use.
engine = create_engine(database_url(), pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
