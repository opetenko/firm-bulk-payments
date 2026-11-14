"""SQLAlchemy Core table definitions, mirroring migration 0001 exactly.

The migrations own the schema. These definitions include the same constraints
under the same names, so `alembic check` reports no drift between the two.
"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    func,
)

metadata = MetaData()

firms = Table(
    "firms",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=False),
    Column("name", Text, nullable=False),
    Column("balance_cents", BigInteger, nullable=False),
    Column("uuid", Text, nullable=False),
    CheckConstraint("balance_cents >= 0", name="ck_firms_balance_non_negative"),
    UniqueConstraint("uuid", name="uq_firms_uuid"),
)

payment_batches = Table(
    "payment_batches",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("uuid", Text, nullable=False),
    Column("payer_firm_id", BigInteger, ForeignKey("firms.id"), nullable=False),
    Column("idempotency_key", Text, nullable=False),
    Column("request_fingerprint", Text, nullable=False),
    Column("total_cents", BigInteger, nullable=False),
    Column("payments_count", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("total_cents > 0", name="ck_payment_batches_total_positive"),
    CheckConstraint("payments_count > 0", name="ck_payment_batches_count_positive"),
    UniqueConstraint("uuid", name="uq_payment_batches_uuid"),
    UniqueConstraint(
        "payer_firm_id", "idempotency_key", name="uq_payment_batches_payer_idempotency_key"
    ),
)

payments = Table(
    "payments",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("batch_id", BigInteger, ForeignKey("payment_batches.id"), nullable=False),
    Column("payer_firm_id", BigInteger, ForeignKey("firms.id"), nullable=False),
    Column("payee_firm_id", BigInteger, ForeignKey("firms.id"), nullable=False),
    Column("amount_cents", BigInteger, nullable=False),
    Column("description", Text, nullable=False),
    CheckConstraint("amount_cents > 0", name="ck_payments_amount_positive"),
    CheckConstraint("payer_firm_id <> payee_firm_id", name="ck_payments_not_self"),
    Index("ix_payments_batch_id", "batch_id"),
    Index("ix_payments_payer_firm_id", "payer_firm_id"),
    Index("ix_payments_payee_firm_id", "payee_firm_id"),
)
