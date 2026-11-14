import pytest

from app.domain.money import parse_amount_to_cents


@pytest.mark.parametrize(
    ("amount", "cents"),
    [
        ("300", 30_000),
        ("6250", 625_000),
        ("5800.5", 580_050),
        ("1200.75", 120_075),
        ("0.01", 1),
        ("0.1", 10),
        ("007", 700),
        ("999999999.99", 99_999_999_999),
    ],
)
def test_parse_amount_to_cents_valid(amount: str, cents: int) -> None:
    assert parse_amount_to_cents(amount) == cents


@pytest.mark.parametrize(
    "amount",
    [
        "0",
        "0.00",
        "0.0",
        "-5",
        "+5",
        "1.",
        ".5",
        "1e3",
        " 5",
        "5 ",
        "5\n",
        "1,000",
        "1.234",
        "1234567890",
        "",
        "١٢٣",
        "NaN",
        "inf",
    ],
)
def test_parse_amount_to_cents_invalid(amount: str) -> None:
    with pytest.raises(ValueError, match=r"^amount must "):
        parse_amount_to_cents(amount)
