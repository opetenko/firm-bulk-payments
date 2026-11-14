"""Pydantic models for the HTTP API (ADR 0001, "Validation").

Every validation failure here maps to HTTP 400. The request is turned into a
domain `BulkPaymentCommand` with `to_command()`; nothing past the API sees dollars
or `UUID` objects.

Firm UUIDs are Pydantic `UUID`s, so any accepted spelling (upper case, no hyphens)
is parsed. They reach the domain as `str(uuid)`: the canonical lowercase,
hyphenated form the database stores.
"""

import re
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictStr

from app.domain.bulk_payment import BulkPaymentCommand, PaymentItem
from app.domain.money import parse_amount_to_cents

MAX_PAYMENTS = 1000
MAX_DESCRIPTION_LENGTH = 500

# Printable ASCII, space included. [\x20-\x7E] is explicit, so it is unaffected by Unicode.
IDEMPOTENCY_KEY_RE = re.compile(r"[\x20-\x7E]{1,255}")


def _parse_amount(value: object) -> int:  # object, not Any: unknown input must be checked
    # A JSON number must not slip through as a str or an int: amounts are strings only.
    if not isinstance(value, str):
        raise ValueError("amount must be a string")
    return parse_amount_to_cents(value)


Cents = Annotated[int, BeforeValidator(_parse_amount)]


class PaymentIn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    amount_cents: Cents = Field(alias="amount")
    payee_firm_uuid: UUID
    description: StrictStr = Field(min_length=1, max_length=MAX_DESCRIPTION_LENGTH)


class BulkPaymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    payer_firm_uuid: UUID
    payments: list[PaymentIn] = Field(min_length=1, max_length=MAX_PAYMENTS)

    def to_command(self, idempotency_key: str) -> BulkPaymentCommand:
        return BulkPaymentCommand(
            payer_firm_uuid=str(self.payer_firm_uuid),
            idempotency_key=idempotency_key,
            items=tuple(
                PaymentItem(
                    payee_firm_uuid=str(p.payee_firm_uuid),
                    amount_cents=p.amount_cents,
                    description=p.description,
                )
                for p in self.payments
            ),
        )


class BulkPaymentResponse(BaseModel):
    batch_uuid: str
    payment_ids: list[int]
    total_cents: int


class ErrorResponse(BaseModel):
    error: str


def validate_idempotency_key(value: str | None) -> str:
    """Return the key unchanged, or raise ValueError if missing or malformed."""
    if value is None or not IDEMPOTENCY_KEY_RE.fullmatch(value):
        raise ValueError("Idempotency-Key must be 1-255 printable ASCII characters")
    return value
