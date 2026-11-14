"""PayBulk use case: one READ COMMITTED transaction with idempotency (ADR 0001).

No retry loop: firms are locked in id order, so deadlocks cannot happen. A lock
timeout, statement timeout or connection-pool timeout raises ServiceBusy
(HTTP 503) and the client retries safely with the same Idempotency-Key, because
nothing was written.
"""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.domain.bulk_payment import (
    BulkPaymentCommand,
    IdempotencyKeyReuse,
    InsufficientFunds,
    UnknownFirm,
)
from app.infrastructure import store
from app.infrastructure.store import NewPayment

# lock_not_available (lock_timeout), query_canceled (statement_timeout)
_BUSY_SQLSTATES = frozenset({"55P03", "57014"})


class ServiceBusy(Exception):
    """The transaction gave up waiting for locks or time. Nothing was written."""


@dataclass(frozen=True, slots=True)
class PayBulkResult:
    batch_uuid: str
    payment_ids: list[int]
    total_cents: int
    replayed: bool


class PayBulk:
    def __init__(
        self,
        engine: Engine,
        *,
        lock_timeout_ms: int = 2_000,
        statement_timeout_ms: int = 5_000,
        new_batch_uuid: Callable[[], str] = lambda: str(uuid4()),
    ) -> None:
        self._engine = engine
        self._lock_timeout_ms = lock_timeout_ms
        self._statement_timeout_ms = statement_timeout_ms
        self._new_batch_uuid = new_batch_uuid

    def __call__(self, command: BulkPaymentCommand) -> PayBulkResult:
        # Before BEGIN: pure checks and computations, no locks held.
        command.ensure_not_self_payment()
        credits = command.credits_by_payee()
        total_cents = command.total_cents
        fingerprint = command.fingerprint()

        try:
            # begin(): COMMIT on normal exit, ROLLBACK if anything raises.
            with self._engine.begin() as conn:
                store.set_local_timeouts(
                    conn,
                    lock_timeout_ms=self._lock_timeout_ms,
                    statement_timeout_ms=self._statement_timeout_ms,
                )

                locked = {
                    firm.uuid: firm
                    for firm in store.lock_firms(conn, [command.payer_firm_uuid, *credits])
                }
                if len(locked) != 1 + len(credits):
                    raise UnknownFirm()
                payer = locked[command.payer_firm_uuid]

                # After the payer lock: a concurrent request with the same key has
                # committed or rolled back by now, so this read sees its outcome.
                existing = store.find_batch(conn, payer.id, command.idempotency_key)
                if existing is not None:
                    if existing.request_fingerprint != fingerprint:
                        raise IdempotencyKeyReuse()
                    return PayBulkResult(
                        batch_uuid=existing.uuid,
                        payment_ids=existing.payment_ids,
                        total_cents=existing.total_cents,
                        replayed=True,
                    )

                if payer.balance_cents < total_cents:
                    raise InsufficientFunds()

                batch_uuid = self._new_batch_uuid()
                batch_id = store.insert_batch(
                    conn,
                    uuid=batch_uuid,
                    payer_firm_id=payer.id,
                    idempotency_key=command.idempotency_key,
                    request_fingerprint=fingerprint,
                    total_cents=total_cents,
                    payments_count=len(command.items),
                )
                payment_ids = store.insert_payments(
                    conn,
                    batch_id=batch_id,
                    payer_firm_id=payer.id,
                    items=[
                        NewPayment(
                            payee_firm_id=locked[item.payee_firm_uuid].id,
                            amount_cents=item.amount_cents,
                            description=item.description,
                        )
                        for item in command.items
                    ],
                )
                # The payer is never a payee (checked above), so keys do not collide.
                store.apply_balance_deltas(
                    conn,
                    {payer.id: -total_cents}
                    | {locked[uuid].id: cents for uuid, cents in credits.items()},
                )
        except PoolTimeoutError as exc:  # no free connection within pool_timeout
            raise ServiceBusy() from exc
        except OperationalError as exc:
            if getattr(exc.orig, "sqlstate", None) in _BUSY_SQLSTATES:
                raise ServiceBusy() from exc
            raise

        return PayBulkResult(
            batch_uuid=batch_uuid,
            payment_ids=payment_ids,
            total_cents=total_cents,
            replayed=False,
        )
