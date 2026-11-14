"""The bulk payment command and the business errors it can produce.

Pure Python: no FastAPI, Pydantic or SQLAlchemy imports in the domain.
Firm UUIDs are canonical lowercase strings, the form `firms.uuid` stores.
"""

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
