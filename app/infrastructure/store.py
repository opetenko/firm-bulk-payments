"""All SQL of the bulk payment transaction (ADR 0001, "Transaction").

Every function takes a Connection that is already inside a transaction; the
caller (PayBulk) owns BEGIN / COMMIT / ROLLBACK. Inputs and outputs are plain
values, so this module knows nothing about HTTP or the domain command.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import Connection, bindparam, func, insert, select, update

from app.infrastructure.tables import firms, payment_batches, payments


@dataclass(frozen=True, slots=True)
class LockedFirm:
    id: int
    uuid: str
    balance_cents: int


@dataclass(frozen=True, slots=True)
class StoredBatch:
    uuid: str
    request_fingerprint: str
    total_cents: int
    payment_ids: list[int]


@dataclass(frozen=True, slots=True)
class NewPayment:
    payee_firm_id: int
    amount_cents: int
    description: str


def set_local_timeouts(
    conn: Connection, *, lock_timeout_ms: int, statement_timeout_ms: int
) -> None:
    """Timeouts for the current transaction only.

    set_config(..., true) instead of SET LOCAL: SET does not accept bind parameters.
    """
    conn.execute(
        select(
            func.set_config("lock_timeout", str(lock_timeout_ms), True),
            func.set_config("statement_timeout", str(statement_timeout_ms), True),
        )
    )


def lock_firms(conn: Connection, uuids: Collection[str]) -> list[LockedFirm]:
    """Lock every firm row the batch touches, in ascending id order.

    One statement, ordered by id: the deadlock-freedom rule of ADR 0001. Any code
    path that locks more than one firm must lock in ascending id order.
    FOR NO KEY UPDATE is enough for a balance change and does not block
    concurrent foreign-key checks against these rows.
    Unknown uuids are simply absent from the result.
    """
    rows = conn.execute(
        select(firms.c.id, firms.c.uuid, firms.c.balance_cents)
        .where(firms.c.uuid.in_(list(uuids)))
        .order_by(firms.c.id)
        .with_for_update(key_share=True)
    )
    return [LockedFirm(*row) for row in rows]


def find_batch(conn: Connection, payer_firm_id: int, idempotency_key: str) -> StoredBatch | None:
    """The batch already applied for this payer and key, with its payment ids in order."""
    batch = conn.execute(
        select(
            payment_batches.c.id,
            payment_batches.c.uuid,
            payment_batches.c.request_fingerprint,
            payment_batches.c.total_cents,
        ).where(
            payment_batches.c.payer_firm_id == payer_firm_id,
            payment_batches.c.idempotency_key == idempotency_key,
        )
    ).one_or_none()
    if batch is None:
        return None
    payment_ids = conn.scalars(
        select(payments.c.id).where(payments.c.batch_id == batch.id).order_by(payments.c.id)
    ).all()
    return StoredBatch(
        uuid=batch.uuid,
        request_fingerprint=batch.request_fingerprint,
        total_cents=batch.total_cents,
        payment_ids=list(payment_ids),
    )


def insert_batch(
    conn: Connection,
    *,
    uuid: str,
    payer_firm_id: int,
    idempotency_key: str,
    request_fingerprint: str,
    total_cents: int,
    payments_count: int,
) -> int:
    # Core columns are untyped (Any); the annotation states what the schema guarantees.
    batch_id: int = conn.execute(
        insert(payment_batches)
        .values(
            uuid=uuid,
            payer_firm_id=payer_firm_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            total_cents=total_cents,
            payments_count=payments_count,
        )
        .returning(payment_batches.c.id)
    ).scalar_one()
    return batch_id


def insert_payments(
    conn: Connection, *, batch_id: int, payer_firm_id: int, items: Sequence[NewPayment]
) -> list[int]:
    """Insert all payments of a batch; return their ids in the order of `items`.

    sort_by_parameter_order=True makes SQLAlchemy return RETURNING rows in
    parameter order, which PostgreSQL alone does not guarantee.
    """
    result = conn.execute(
        insert(payments).returning(payments.c.id, sort_by_parameter_order=True),
        [
            {
                "batch_id": batch_id,
                "payer_firm_id": payer_firm_id,
                "payee_firm_id": item.payee_firm_id,
                "amount_cents": item.amount_cents,
                "description": item.description,
            }
            for item in items
        ],
    )
    return list(result.scalars())


def apply_balance_deltas(conn: Connection, deltas: Mapping[int, int]) -> None:
    """balance_cents += delta for each firm id. Rows must already be locked."""
    conn.execute(
        update(firms)
        .where(firms.c.id == bindparam("firm_id"))
        .values(balance_cents=firms.c.balance_cents + bindparam("delta")),
        [{"firm_id": firm_id, "delta": delta} for firm_id, delta in deltas.items()],
    )
