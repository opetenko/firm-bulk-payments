"""Concurrency guarantees of ADR 0001, proven against real PostgreSQL.

Each scenario starts many PayBulk calls at the same moment (threads released by
a barrier), each on its own database connection, exactly what several app
replicas look like to the database.
"""

import random
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import Engine, create_engine, func, select

from app.application.pay_bulk import PayBulk, PayBulkResult
from app.domain.bulk_payment import (
    BulkPaymentCommand,
    IdempotencyKeyReuse,
    InsufficientFunds,
    PaymentItem,
)
from app.infrastructure.tables import firms, payment_batches, payments
from tests.conftest import TEST_DATABASE_URL

PINECREST = "3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41"
LOPEZ = "8b2e4c71-0d3a-4f6e-b1c9-5a7d2e9f4c10"
NAIR = "e5f18b3c-2a9d-4c07-8e6b-1d4a7f9c3b25"
FIRMS = (PINECREST, LOPEZ, NAIR)
SEED_BALANCES = {PINECREST: 5_000_000, LOPEZ: 50_000, NAIR: 200_000}

THREADS = 20

Outcome = PayBulkResult | Exception


@pytest.fixture
def engine(clean_engine: Engine):
    """A pool big enough that every thread holds its own connection at once."""
    pooled = create_engine(TEST_DATABASE_URL, pool_size=THREADS, max_overflow=0)
    yield pooled
    pooled.dispose()


@pytest.fixture
def pay_bulk(engine: Engine) -> PayBulk:
    # Generous timeouts: these tests are about correctness, not about 503s.
    return PayBulk(engine, lock_timeout_ms=10_000, statement_timeout_ms=20_000)


def run_concurrently(
    pay_bulk: PayBulk, commands: Sequence[BulkPaymentCommand]
) -> list[Outcome]:
    """Run every command in its own thread, all released at the same moment."""
    barrier = threading.Barrier(len(commands))

    def call(command: BulkPaymentCommand) -> Outcome:
        barrier.wait()
        try:
            return pay_bulk(command)
        except Exception as exc:  # collected and asserted by the test
            return exc

    with ThreadPoolExecutor(max_workers=len(commands)) as pool:
        return list(pool.map(call, commands))


def of_type(outcomes: list[Outcome], kind: type) -> list:
    return [o for o in outcomes if isinstance(o, kind)]


def balances(engine: Engine) -> dict[str, int]:
    with engine.connect() as conn:
        return dict(conn.execute(select(firms.c.uuid, firms.c.balance_cents)).all())


def counts(engine: Engine) -> tuple[int, int]:
    with engine.connect() as conn:
        return (
            conn.scalar(select(func.count()).select_from(payment_batches)),
            conn.scalar(select(func.count()).select_from(payments)),
        )


def pay(payer: str, payee: str, cents: int, key: str) -> BulkPaymentCommand:
    return BulkPaymentCommand(payer, key, (PaymentItem(payee, cents, f"{key}"),))


def assert_conserved(engine: Engine) -> None:
    current = balances(engine)
    assert sum(current.values()) == sum(SEED_BALANCES.values())
    assert all(b >= 0 for b in current.values())


# --- no overdraft ---------------------------------------------------------------


def test_concurrent_batches_cannot_overdraw_the_payer(pay_bulk: PayBulk, engine: Engine) -> None:
    # Lopez has $500.00; 20 batches of $70.00 race for it: exactly 7 fit.
    outcomes = run_concurrently(
        pay_bulk, [pay(LOPEZ, NAIR, 7_000, f"drain-{i}") for i in range(THREADS)]
    )

    assert len(of_type(outcomes, PayBulkResult)) == 7
    assert len(of_type(outcomes, InsufficientFunds)) == THREADS - 7
    assert balances(engine) == {PINECREST: 5_000_000, LOPEZ: 1_000, NAIR: 249_000}
    assert counts(engine) == (7, 7)


# --- no deadlock ------------------------------------------------------------------


def test_firms_paying_each_other_never_deadlock(pay_bulk: PayBulk, engine: Engine) -> None:
    # Every pair in both directions plus a 3-cycle in one batch: with any lock
    # order other than ascending id, some of these would deadlock.
    shapes = [
        (PINECREST, ((NAIR, 100),)),
        (NAIR, ((PINECREST, 100),)),
        (LOPEZ, ((NAIR, 100),)),
        (NAIR, ((LOPEZ, 100),)),
        (PINECREST, ((LOPEZ, 100),)),
        (LOPEZ, ((PINECREST, 100),)),
        (NAIR, ((LOPEZ, 100), (PINECREST, 100))),
        (LOPEZ, ((PINECREST, 100), (NAIR, 100))),
    ]
    commands = [
        BulkPaymentCommand(
            payer,
            f"mutual-{i}",
            tuple(PaymentItem(payee, cents, "mutual") for payee, cents in items),
        )
        for i in range(THREADS)
        for payer, items in [shapes[i % len(shapes)]]
    ]

    outcomes = run_concurrently(pay_bulk, commands)

    assert of_type(outcomes, Exception) == []  # a deadlock surfaces as an exception here
    expected = dict(SEED_BALANCES)
    for command in commands:
        expected[command.payer_firm_uuid] -= command.total_cents
        for payee, cents in command.credits_by_payee().items():
            expected[payee] += cents
    assert balances(engine) == expected


# --- exactly once per key -----------------------------------------------------------


def test_same_key_sent_concurrently_pays_exactly_once(pay_bulk: PayBulk, engine: Engine) -> None:
    command = BulkPaymentCommand(
        PINECREST,
        "same-key",
        (PaymentItem(NAIR, 625_000, "Overflow"), PaymentItem(LOPEZ, 120_075, "Cleanup")),
    )

    outcomes = run_concurrently(pay_bulk, [command] * THREADS)

    results = of_type(outcomes, PayBulkResult)
    assert len(results) == THREADS  # nobody failed
    assert sum(not r.replayed for r in results) == 1  # exactly one applied it
    assert len({(r.batch_uuid, tuple(r.payment_ids), r.total_cents) for r in results}) == 1
    assert counts(engine) == (1, 2)
    assert balances(engine)[PINECREST] == 5_000_000 - 745_075


def test_same_key_with_different_bodies_applies_only_one(pay_bulk: PayBulk, engine: Engine) -> None:
    body_a = pay(PINECREST, NAIR, 100, "contested")
    body_b = pay(PINECREST, LOPEZ, 200, "contested")

    outcomes = run_concurrently(pay_bulk, [body_a, body_b] * (THREADS // 2))

    results = of_type(outcomes, PayBulkResult)
    reused = of_type(outcomes, IdempotencyKeyReuse)
    assert len(results) + len(reused) == THREADS
    assert sum(not r.replayed for r in results) == 1
    assert len(results) == THREADS // 2  # every request with the winning body
    assert counts(engine) == (1, 1)


# --- conservation under a random mix ------------------------------------------------


def test_random_mix_conserves_money(pay_bulk: PayBulk, engine: Engine) -> None:
    rng = random.Random(20261114)
    commands = []
    for i in range(THREADS):
        payer = rng.choice(FIRMS)
        payees = [f for f in FIRMS if f != payer]
        items = tuple(
            PaymentItem(rng.choice(payees), rng.randint(1, 40_000), "mix")
            for _ in range(rng.randint(1, 4))
        )
        commands.append(BulkPaymentCommand(payer, f"mix-{i}", items))

    outcomes = run_concurrently(pay_bulk, commands)

    assert of_type(outcomes, Exception) == of_type(outcomes, InsufficientFunds)
    assert_conserved(engine)
    applied = [c for c, o in zip(commands, outcomes, strict=True) if isinstance(o, PayBulkResult)]
    assert counts(engine) == (len(applied), sum(len(c.items) for c in applied))
