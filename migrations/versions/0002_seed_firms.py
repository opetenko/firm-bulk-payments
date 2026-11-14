"""Seed the three demo firms from the spec.

Kept separate from the schema revision so the demo data can be dropped
without touching the schema.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    firms = sa.table(
        "firms",
        sa.column("id", sa.BigInteger()),
        sa.column("name", sa.Text()),
        sa.column("balance_cents", sa.BigInteger()),
        sa.column("uuid", sa.Text()),
    )
    op.bulk_insert(
        firms,
        [
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
        ],
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM firms WHERE id IN (1, 2, 3)"))
