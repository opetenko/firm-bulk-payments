import pytest
from sqlalchemy import Engine, func, select, update

from app.application.pay_bulk import PayBulk, PayBulkResult, ServiceBusy
from app.domain.bulk_payment import (
    BulkPaymentCommand,
    IdempotencyKeyReuse,
    InsufficientFunds,
    PaymentItem,
    SelfPayment,
    UnknownFirm,
)
from app.infrastructure import store
from app.infrastructure.tables import firms, payment_batches, payments

PINECREST = "3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41"
LOPEZ = "8b2e4c71-0d3a-4f6e-b1c9-5a7d2e9f4c10"
NAIR = "e5f18b3c-2a9d-4c07-8e6b-1d4a7f9c3b25"
UNKNOWN = "00000000-0000-4000-8000-000000000000"

SEED_BALANCES = {PINECREST: 5_000_000, LOPEZ: 50_000, NAIR: 200_000}

SPEC_ITEMS = (
    PaymentItem(NAIR, 625_000, "Overflow returns, August 2026"),
    PaymentItem(NAIR, 580_050, "Amended returns, August 2026"),
    PaymentItem(LOPEZ, 120_075, "Bookkeeping cleanup, 3 clients"),
)


def command(*items: PaymentItem, payer: str = PINECREST, key: str = "key-1") -> BulkPaymentCommand:
    return BulkPaymentCommand(payer_firm_uuid=payer, idempotency_key=key, items=items or SPEC_ITEMS)


def balances(engine: Engine) -> dict[str, int]:
    with engine.connect() as conn:
        return dict(conn.execute(select(firms.c.uuid, firms.c.balance_cents)).all())


def row_counts(engine: Engine) -> tuple[int, int]:
    with engine.connect() as conn:
        return (
            conn.execute(select(func.count()).select_from(payment_batches)).scalar_one(),
            conn.execute(select(func.count()).select_from(payments)).scalar_one(),
        )


def assert_nothing_written(engine: Engine) -> None:
    assert balances(engine) == SEED_BALANCES
    assert row_counts(engine) == (0, 0)


@pytest.fixture
def pay_bulk(clean_engine: Engine) -> PayBulk:
    return PayBulk(clean_engine)


# --- success ----------------------------------------------------------------


def test_spec_example_pays_everyone(pay_bulk: PayBulk, clean_engine: Engine) -> None:
    result = pay_bulk(command())

    assert result.total_cents == 1_325_125
    assert len(result.payment_ids) == 3
    assert result.replayed is False
    # $36,748.75 / $1,700.75 / $14,050.50
    assert balances(clean_engine) == {PINECREST: 3_674_875, LOPEZ: 170_075, NAIR: 1_405_050}
    assert row_counts(clean_engine) == (1, 3)


def test_payer_can_spend_exact_balance(pay_bulk: PayBulk, clean_engine: Engine) -> None:
    pay_bulk(command(PaymentItem(NAIR, 50_000, "all of it"), payer=LOPEZ))

    assert balances(clean_engine)[LOPEZ] == 0


# --- idempotency ------------------------------------------------------------


def test_same_key_same_request_replays_without_paying_twice(
    pay_bulk: PayBulk, clean_engine: Engine
) -> None:
    first = pay_bulk(command())
    after_first = balances(clean_engine)

    second = pay_bulk(command())

    assert second == PayBulkResult(
        batch_uuid=first.batch_uuid,
        payment_ids=first.payment_ids,
        total_cents=first.total_cents,
        replayed=True,
    )
    assert balances(clean_engine) == after_first
    assert row_counts(clean_engine) == (1, 3)


def test_same_key_different_request_is_rejected(pay_bulk: PayBulk, clean_engine: Engine) -> None:
    pay_bulk(command())
    after_first = balances(clean_engine)

    with pytest.raises(IdempotencyKeyReuse):
        pay_bulk(command(PaymentItem(NAIR, 1, "different")))

    assert balances(clean_engine) == after_first
    assert row_counts(clean_engine) == (1, 3)


def test_replay_succeeds_even_after_balance_is_spent(pay_bulk: PayBulk) -> None:
    spend_all = command(PaymentItem(NAIR, 50_000, "all of it"), payer=LOPEZ)
    first = pay_bulk(spend_all)

    # Lopez now has 0: a fresh request would be insufficient_funds, a replay is not.
    assert pay_bulk(spend_all).payment_ids == first.payment_ids


def test_same_key_is_independent_per_payer(pay_bulk: PayBulk, clean_engine: Engine) -> None:
    pay_bulk(command(PaymentItem(NAIR, 100, "x"), payer=PINECREST, key="shared"))
    result = pay_bulk(command(PaymentItem(NAIR, 100, "x"), payer=LOPEZ, key="shared"))

    assert result.replayed is False
    assert row_counts(clean_engine) == (2, 2)


def test_rejected_request_leaves_no_trace_so_retry_can_succeed(
    pay_bulk: PayBulk, clean_engine: Engine
) -> None:
    too_much = command(PaymentItem(NAIR, 60_000, "more than Lopez has"), payer=LOPEZ)
    with pytest.raises(InsufficientFunds):
        pay_bulk(too_much)

    with clean_engine.begin() as conn:  # Lopez gets paid by a client invoice
        conn.execute(update(firms).where(firms.c.uuid == LOPEZ).values(balance_cents=100_000))

    assert pay_bulk(too_much).replayed is False


# --- business errors write nothing -------------------------------------------


def test_insufficient_funds(pay_bulk: PayBulk, clean_engine: Engine) -> None:
    with pytest.raises(InsufficientFunds):
        pay_bulk(command(PaymentItem(NAIR, 50_001, "one cent too much"), payer=LOPEZ))

    assert_nothing_written(clean_engine)


@pytest.mark.parametrize(
    "cmd",
    [
        pytest.param(command(PaymentItem(NAIR, 100, "x"), payer=UNKNOWN), id="unknown_payer"),
        pytest.param(
            command(PaymentItem(NAIR, 100, "x"), PaymentItem(UNKNOWN, 100, "y")), id="unknown_payee"
        ),
    ],
)
def test_unknown_firm(pay_bulk: PayBulk, clean_engine: Engine, cmd: BulkPaymentCommand) -> None:
    with pytest.raises(UnknownFirm):
        pay_bulk(cmd)

    assert_nothing_written(clean_engine)


def test_self_payment(pay_bulk: PayBulk, clean_engine: Engine) -> None:
    with pytest.raises(SelfPayment):
        pay_bulk(command(PaymentItem(LOPEZ, 100, "x"), PaymentItem(PINECREST, 100, "me")))

    assert_nothing_written(clean_engine)


def test_unknown_firm_takes_precedence_over_key_reuse(pay_bulk: PayBulk) -> None:
    pay_bulk(command())

    # Same key, different body with an unknown payee: firms are checked first (ADR order).
    with pytest.raises(UnknownFirm):
        pay_bulk(command(PaymentItem(UNKNOWN, 100, "x")))


# --- busy ---------------------------------------------------------------------


def test_lock_timeout_raises_busy_and_writes_nothing(clean_engine: Engine) -> None:
    pay_bulk = PayBulk(clean_engine, lock_timeout_ms=100)

    with clean_engine.connect() as other:
        other.begin()
        store.lock_firms(other, [NAIR])  # another batch is crediting Nair

        with pytest.raises(ServiceBusy):
            pay_bulk(command())
        other.rollback()

    assert_nothing_written(clean_engine)
