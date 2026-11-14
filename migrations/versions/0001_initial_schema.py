"""Initial schema: firms, payment_batches, payments (ADR 0001).

Revision ID: 0001
Revises:
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "firms",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("balance_cents", sa.BigInteger(), nullable=False),
        sa.Column("uuid", sa.Text(), nullable=False),
        sa.CheckConstraint("balance_cents >= 0", name="ck_firms_balance_non_negative"),
        sa.UniqueConstraint("uuid", name="uq_firms_uuid"),
    )

    op.create_table(
        "payment_batches",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("uuid", sa.Text(), nullable=False),
        sa.Column("payer_firm_id", sa.BigInteger(), sa.ForeignKey("firms.id"), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("total_cents", sa.BigInteger(), nullable=False),
        sa.Column("payments_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("total_cents > 0", name="ck_payment_batches_total_positive"),
        sa.CheckConstraint("payments_count > 0", name="ck_payment_batches_count_positive"),
        sa.UniqueConstraint("uuid", name="uq_payment_batches_uuid"),
        sa.UniqueConstraint(
            "payer_firm_id", "idempotency_key", name="uq_payment_batches_payer_idempotency_key"
        ),
    )

    op.create_table(
        "payments",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "batch_id", sa.BigInteger(), sa.ForeignKey("payment_batches.id"), nullable=False
        ),
        sa.Column("payer_firm_id", sa.BigInteger(), sa.ForeignKey("firms.id"), nullable=False),
        sa.Column("payee_firm_id", sa.BigInteger(), sa.ForeignKey("firms.id"), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.CheckConstraint("amount_cents > 0", name="ck_payments_amount_positive"),
        sa.CheckConstraint("payer_firm_id <> payee_firm_id", name="ck_payments_not_self"),
    )
    op.create_index("ix_payments_batch_id", "payments", ["batch_id"])
    op.create_index("ix_payments_payer_firm_id", "payments", ["payer_firm_id"])
    op.create_index("ix_payments_payee_firm_id", "payments", ["payee_firm_id"])


def downgrade() -> None:
    op.drop_table("payments")
    op.drop_table("payment_batches")
    op.drop_table("firms")
