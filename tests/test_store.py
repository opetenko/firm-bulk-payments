import pytest
from psycopg.errors import LockNotAvailable
from sqlalchemy import Connection, Engine, select
from sqlalchemy.exc import IntegrityError, OperationalError

from app.infrastructure import store
from app.infrastructure.store import NewPayment
from app.infrastructure.tables import firms, payments

PINECREST = "3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41"
LOPEZ = "8b2e4c71-0d3a-4f6e-b1c9-5a7d2e9f4c10"
NAIR = "e5f18b3c-2a9d-4c07-8e6b-1d4a7f9c3b25"
PINECREST_ID, LOPEZ_ID, NAIR_ID = 1, 2, 3


def balances(conn: Connection) -> dict[int, int]:
    return dict(conn.execute(select(firms.c.id, firms.c.balance_cents)).all())


def insert_batch(conn: Connection, key: str = "key-1", payer_id: int = PINECREST_ID) -> int:
    return store.insert_batch(
        conn,
        uuid=f"batch-{payer_id}-{key}",
        payer_firm_id=payer_id,
        idempotency_key=key,
        request_fingerprint="fp",
        total_cents=100,
        payments_count=1,
    )


def test_lock_firms_returns_rows_in_id_order(db: Connection) -> None:
    locked = store.lock_firms(db, [NAIR, PINECREST, LOPEZ])

    assert [(f.id, f.uuid, f.balance_cents) for f in locked] == [
        (1, PINECREST, 5_000_000),
        (2, LOPEZ, 50_000),
        (3, NAIR, 200_000),
    ]


def test_lock_firms_omits_unknown_uuid(db: Connection) -> None:
    locked = store.lock_firms(db, [PINECREST, "00000000-0000-4000-8000-000000000000"])

    assert [f.id for f in locked] == [PINECREST_ID]


def test_locked_firm_blocks_another_transaction_until_lock_timeout(
    migrated_engine: Engine,
) -> None:
    with migrated_engine.connect() as holder, migrated_engine.connect() as waiter:
        holder.begin()
        store.lock_firms(holder, [NAIR])

        waiter.begin()
        store.set_local_timeouts(waiter, lock_timeout_ms=200, statement_timeout_ms=5_000)
        with pytest.raises(OperationalError) as exc:
            store.lock_firms(waiter, [LOPEZ, NAIR])

        # SQLSTATE 55P03 lock_not_available -> 503 busy
        assert isinstance(exc.value.orig, LockNotAvailable)
        waiter.rollback()
        holder.rollback()


def test_spec_example_written_and_found_again(db: Connection) -> None:
    store.lock_firms(db, [PINECREST, NAIR, LOPEZ])
    batch_id = insert_batch(db)
    items = [
        NewPayment(NAIR_ID, 625_000, "Overflow returns, August 2026"),
        NewPayment(NAIR_ID, 580_050, "Amended returns, August 2026"),
        NewPayment(LOPEZ_ID, 120_075, "Bookkeeping cleanup, 3 clients"),
    ]

    payment_ids = store.insert_payments(
        db, batch_id=batch_id, payer_firm_id=PINECREST_ID, items=items
    )
    store.apply_balance_deltas(
        db, {PINECREST_ID: -1_325_125, NAIR_ID: 1_205_050, LOPEZ_ID: 120_075}
    )

    # payment_ids[i] is the row of items[i]
    rows = db.execute(
        select(
            payments.c.id, payments.c.payee_firm_id, payments.c.amount_cents, payments.c.description
        ).where(payments.c.batch_id == batch_id)
    ).all()
    by_id = {r.id: NewPayment(r.payee_firm_id, r.amount_cents, r.description) for r in rows}
    assert [by_id[pid] for pid in payment_ids] == items

    # Balances from the spec: $36,748.75 / $1,700.75 / $14,050.50
    assert balances(db) == {PINECREST_ID: 3_674_875, LOPEZ_ID: 170_075, NAIR_ID: 1_405_050}

    # A replay sees the same ids in the same order.
    found = store.find_batch(db, PINECREST_ID, "key-1")
    assert found is not None
    assert found.payment_ids == payment_ids
    assert found.request_fingerprint == "fp"


def test_find_batch_is_scoped_to_payer(db: Connection) -> None:
    insert_batch(db, key="shared", payer_id=PINECREST_ID)

    assert store.find_batch(db, PINECREST_ID, "shared") is not None
    assert store.find_batch(db, LOPEZ_ID, "shared") is None
    assert store.find_batch(db, PINECREST_ID, "other") is None


def test_overdraft_is_stopped_by_the_database_backstop(db: Connection) -> None:
    with pytest.raises(IntegrityError):
        store.apply_balance_deltas(db, {LOPEZ_ID: -50_001})
