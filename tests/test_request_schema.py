import pytest
from pydantic import ValidationError

from app.api.schemas import MAX_PAYMENTS, BulkPaymentRequest, validate_idempotency_key
from app.domain.bulk_payment import BulkPaymentCommand, PaymentItem

PINECREST = "3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41"
LOPEZ = "8b2e4c71-0d3a-4f6e-b1c9-5a7d2e9f4c10"
NAIR = "e5f18b3c-2a9d-4c07-8e6b-1d4a7f9c3b25"


def payment(amount: object = "100", payee: str = LOPEZ, description: object = "work") -> dict:
    return {"amount": amount, "payee_firm_uuid": payee, "description": description}


def request(*payments: dict, payer: str = PINECREST) -> dict:
    return {"payer_firm_uuid": payer, "payments": list(payments) or [payment()]}


def test_spec_example_becomes_command_in_cents() -> None:
    req = BulkPaymentRequest.model_validate(
        request(
            payment("6250", NAIR, "Overflow returns, August 2026"),
            payment("5800.5", NAIR, "Amended returns, August 2026"),
            payment("1200.75", LOPEZ, "Bookkeeping cleanup, 3 clients"),
        )
    )

    command = req.to_command("key-1")

    assert command == BulkPaymentCommand(
        payer_firm_uuid=PINECREST,
        idempotency_key="key-1",
        items=(
            PaymentItem(NAIR, 625_000, "Overflow returns, August 2026"),
            PaymentItem(NAIR, 580_050, "Amended returns, August 2026"),
            PaymentItem(LOPEZ, 120_075, "Bookkeeping cleanup, 3 clients"),
        ),
    )
    assert command.total_cents == 1_325_125


def test_uuids_reach_the_domain_as_canonical_lowercase() -> None:
    req = BulkPaymentRequest.model_validate(
        request(payment(payee=LOPEZ.upper()), payer=PINECREST.upper())
    )

    command = req.to_command("key-1")

    assert command.payer_firm_uuid == PINECREST
    assert command.items[0].payee_firm_uuid == LOPEZ


def test_max_payments_accepted() -> None:
    req = BulkPaymentRequest.model_validate(request(*[payment()] * MAX_PAYMENTS))

    assert len(req.payments) == MAX_PAYMENTS


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"payer_firm_uuid": PINECREST, "payments": []}, id="empty_payments"),
        pytest.param(request(*[payment()] * (MAX_PAYMENTS + 1)), id="too_many_payments"),
        pytest.param({"payments": [payment()]}, id="missing_payer"),
        pytest.param(request(payer="not-a-uuid"), id="bad_payer_uuid"),
        pytest.param(request(payment(payee="nope")), id="bad_payee_uuid"),
        pytest.param(request(payment(amount=300)), id="amount_as_number"),
        pytest.param(request(payment(amount=300.5)), id="amount_as_float"),
        pytest.param(request(payment(amount="1.234")), id="amount_bad_format"),
        pytest.param(request(payment(description="")), id="empty_description"),
        pytest.param(request(payment(description="x" * 501)), id="long_description"),
        pytest.param(request(payment(description=5)), id="description_not_string"),
        pytest.param({**request(), "extra": 1}, id="unknown_top_level_field"),
        pytest.param(request({**payment(), "ammount": "1"}), id="unknown_payment_field"),
        pytest.param({"payer_firm_uuid": PINECREST, "payments": "x"}, id="payments_not_list"),
    ],
)
def test_invalid_body_rejected(body: dict) -> None:
    with pytest.raises(ValidationError):
        BulkPaymentRequest.model_validate(body)


@pytest.mark.parametrize("key", ["a", "x" * 255, "order-42 retry #1", "~!@ "])
def test_idempotency_key_valid(key: str) -> None:
    assert validate_idempotency_key(key) == key


@pytest.mark.parametrize(
    "key",
    [None, "", "x" * 256, "line\nbreak", "tab\there", "café", "\x7f"],
)
def test_idempotency_key_invalid(key: str | None) -> None:
    with pytest.raises(ValueError):
        validate_idempotency_key(key)
