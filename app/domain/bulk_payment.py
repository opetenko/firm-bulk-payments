"""The bulk payment command and the business errors it can produce.

Pure Python: no FastAPI, Pydantic or SQLAlchemy imports in the domain.
Firm UUIDs are canonical lowercase strings, the form `firms.uuid` stores.
"""

import hashlib
import json
from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PaymentItem:
    payee_firm_uuid: str
    amount_cents: int
    description: str


@dataclass(frozen=True, slots=True)
class BulkPaymentCommand:
    payer_firm_uuid: str
    idempotency_key: str
    items: tuple[PaymentItem, ...]

    @property
    def total_cents(self) -> int:
        return sum(item.amount_cents for item in self.items)

    def ensure_not_self_payment(self) -> None:
        """Raise SelfPayment if the payer is listed as a payee."""
        if any(item.payee_firm_uuid == self.payer_firm_uuid for item in self.items):
            raise SelfPayment()

    def credits_by_payee(self) -> dict[str, int]:
        """Cents to credit each payee: one balance update per payee, not per item."""
        credits: Counter[str] = Counter()
        for item in self.items:
            credits[item.payee_firm_uuid] += item.amount_cents
        return dict(credits)

    def fingerprint(self) -> str:
        """SHA-256 hex of the canonical request, to detect Idempotency-Key reuse.

        Covers the payer and every item in request order. It is not built from
        credits_by_payee(): two different requests can have equal per-payee totals.
        The key itself is excluded: it identifies the request, not its content.
        """
        canonical = json.dumps(
            {
                "payer_firm_uuid": self.payer_firm_uuid,
                "items": [
                    [item.payee_firm_uuid, item.amount_cents, item.description]
                    for item in self.items
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DomainError(Exception):
    """A business rule rejected the request. `code` is the public error string."""

    code: str = "domain_error"


class SelfPayment(DomainError):
    code = "self_payment"


class UnknownFirm(DomainError):
    code = "unknown_firm"


class IdempotencyKeyReuse(DomainError):
    code = "idempotency_key_reuse"


class InsufficientFunds(DomainError):
    code = "insufficient_funds"
