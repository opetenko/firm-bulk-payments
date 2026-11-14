"""Money is integer cents everywhere past the API boundary."""

import re

# Spec rule ^\d{1,9}(\.\d{1,2})?$, written for Python: [0-9] because \d also matches
# non-ASCII digits, and fullmatch() because $ also matches before a trailing "\n".
AMOUNT_RE = re.compile(r"[0-9]{1,9}(\.[0-9]{1,2})?")


def parse_amount_to_cents(amount: str) -> int:
    """Convert a dollar amount string to integer cents, by string splitting.

    No float and no Decimal: "1200.75" -> "1200" and "75" -> 1200 * 100 + 75.
    Raise ValueError if the format is wrong or the value is not greater than zero.
    """
    if not AMOUNT_RE.fullmatch(amount):
        raise ValueError("amount must match ^[0-9]{1,9}(\\.[0-9]{1,2})?$")
    dollars, _, fraction = amount.partition(".")
    cents = int(dollars) * 100 + int(fraction.ljust(2, "0"))  # "5" -> "50" cents
    if cents <= 0:
        raise ValueError("amount must be greater than zero")
    return cents
