import pytest

from app.api.schemas import BulkPaymentRequest
from app.domain.bulk_payment import BulkPaymentCommand, PaymentItem, SelfPayment

PINECREST = "3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41"
LOPEZ = "8b2e4c71-0d3a-4f6e-b1c9-5a7d2e9f4c10"
NAIR = "e5f18b3c-2a9d-4c07-8e6b-1d4a7f9c3b25"

SPEC_EXAMPLE = BulkPaymentCommand(
    payer_firm_uuid=PINECREST,
    idempotency_key="key-1",
    items=(
        PaymentItem(NAIR, 625_000, "Overflow returns, August 2026"),
        PaymentItem(NAIR, 580_050, "Amended returns, August 2026"),
        PaymentItem(LOPEZ, 120_075, "Bookkeeping cleanup, 3 clients"),
    ),
)


def command(*items: PaymentItem, payer: str = PINECREST, key: str = "key-1") -> BulkPaymentCommand:
    return BulkPaymentCommand(payer_firm_uuid=payer, idempotency_key=key, items=items)


def from_json(body: dict[str, object], key: str = "key-1") -> BulkPaymentCommand:
    return BulkPaymentRequest.model_validate(body).to_command(key)


# --- credits ----------------------------------------------------------------


def test_spec_example_credits_are_aggregated_per_payee() -> None:
    assert SPEC_EXAMPLE.credits_by_payee() == {NAIR: 1_205_050, LOPEZ: 120_075}


def test_credits_sum_to_total() -> None:
    assert sum(SPEC_EXAMPLE.credits_by_payee().values()) == SPEC_EXAMPLE.total_cents == 1_325_125


# --- self payment -----------------------------------------------------------


def test_payer_listed_as_payee_is_rejected() -> None:
    cmd = command(PaymentItem(LOPEZ, 100, "a"), PaymentItem(PINECREST, 100, "b"))

    with pytest.raises(SelfPayment):
        cmd.ensure_not_self_payment()


def test_payer_in_other_case_is_still_self_payment() -> None:
    cmd = from_json(
        {
            "payer_firm_uuid": PINECREST,
            "payments": [{"amount": "1", "payee_firm_uuid": PINECREST.upper(), "description": "x"}],
        }
    )

    with pytest.raises(SelfPayment):
        cmd.ensure_not_self_payment()


def test_spec_example_is_not_self_payment() -> None:
    SPEC_EXAMPLE.ensure_not_self_payment()


# --- fingerprint ------------------------------------------------------------


def test_fingerprint_is_sha256_hex() -> None:
    fp = SPEC_EXAMPLE.fingerprint()

    assert len(fp) == 64
    assert set(fp) <= set("0123456789abcdef")


def test_fingerprint_ignores_amount_and_uuid_spelling() -> None:
    a = from_json(
        {
            "payer_firm_uuid": PINECREST,
            "payments": [{"amount": "300", "payee_firm_uuid": LOPEZ, "description": "x"}],
        }
    )
    b = from_json(
        {
            "payer_firm_uuid": PINECREST.upper(),
            "payments": [
                {"amount": "300.00", "payee_firm_uuid": LOPEZ.upper(), "description": "x"}
            ],
        }
    )

    assert a.fingerprint() == b.fingerprint()


def test_fingerprint_ignores_idempotency_key() -> None:
    assert (
        command(PaymentItem(LOPEZ, 1, "x"), key="a").fingerprint()
        == command(PaymentItem(LOPEZ, 1, "x"), key="b").fingerprint()
    )


@pytest.mark.parametrize(
    "other",
    [
        pytest.param(
            command(SPEC_EXAMPLE.items[2], SPEC_EXAMPLE.items[0], SPEC_EXAMPLE.items[1]),
            id="items_reordered",
        ),
        pytest.param(
            command(
                PaymentItem(NAIR, 625_000, "Overflow returns, Sept 2026"),
                *SPEC_EXAMPLE.items[1:],
            ),
            id="description_changed",
        ),
        pytest.param(
            command(*SPEC_EXAMPLE.items, payer=LOPEZ),
            id="payer_changed",
        ),
        pytest.param(
            # Same per-payee credits as the spec example, split differently.
            command(
                PaymentItem(NAIR, 1_205_050, "Overflow returns, August 2026"),
                PaymentItem(LOPEZ, 120_075, "Bookkeeping cleanup, 3 clients"),
            ),
            id="same_credits_different_split",
        ),
    ],
)
def test_fingerprint_differs_for_different_requests(other: BulkPaymentCommand) -> None:
    assert other.fingerprint() != SPEC_EXAMPLE.fingerprint()


def test_fingerprint_does_not_confuse_field_boundaries() -> None:
    # Naive string concatenation would make these two equal.
    a = command(PaymentItem(LOPEZ, 1, "a,b"), PaymentItem(NAIR, 1, "c"))
    b = command(PaymentItem(LOPEZ, 1, "a"), PaymentItem(NAIR, 1, "b,c"))

    assert a.fingerprint() != b.fingerprint()
