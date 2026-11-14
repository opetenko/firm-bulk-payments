"""Seed the three demo firms from the spec. Safe to run repeatedly.

Kept out of the migration chain so production never gets demo data.
Run after `alembic upgrade head`: python -m scripts.seed
"""

from sqlalchemy import Connection
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.db import engine
from app.infrastructure.tables import firms

SEED_FIRMS = [
    {
        "id": 1,
        "name": "Pinecrest CPA Group",
        "balance_cents": 5_000_000,
        "uuid": "3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41",
    },
    {
        "id": 2,
        "name": "Lopez Bookkeeping",
        "balance_cents": 50_000,
        "uuid": "8b2e4c71-0d3a-4f6e-b1c9-5a7d2e9f4c10",
    },
    {
        "id": 3,
        "name": "Nair Tax Services",
        "balance_cents": 200_000,
        "uuid": "e5f18b3c-2a9d-4c07-8e6b-1d4a7f9c3b25",
    },
]


def seed(conn: Connection) -> int:
    """Insert the demo firms; firms that already exist are left untouched.

    Return the number of firms actually inserted.
    """
    inserted = conn.execute(
        insert(firms)
        .values(SEED_FIRMS)
        .on_conflict_do_nothing(index_elements=["id"])
        .returning(firms.c.id)
    )
    return len(inserted.all())


if __name__ == "__main__":
    with engine.begin() as conn:
        inserted = seed(conn)
    print(f"seed: {inserted} firms inserted, {len(SEED_FIRMS) - inserted} already present")
