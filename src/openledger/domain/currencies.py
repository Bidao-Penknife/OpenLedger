"""Pinned ISO currency precision and exact conversion; no network or floating point."""

import json
import re
from dataclasses import dataclass
from fractions import Fraction
from importlib.resources import files

from openledger.domain.errors import LedgerError


@dataclass(frozen=True)
class Currency:
    """One supported ISO 4217 currency and its stored smallest-unit precision."""

    code: str
    digits: int
    name: str


_REGISTRY = json.loads(files("openledger.resources").joinpath("currencies.json").read_text("utf-8"))
CURRENCIES = {
    row["code"]: Currency(row["code"], row["digits"], row["name"])
    for row in _REGISTRY["currencies"]
}


def currency(value: object) -> Currency:
    """Reject unknown or noncanonical codes instead of guessing their precision."""
    if not isinstance(value, str) or value not in CURRENCIES:
        raise LedgerError("CURRENCY_MISMATCH")
    return CURRENCIES[value]


def format_minor(value: int, code: str = "CNY", *, grouping: bool = False) -> str:
    """Format arbitrary integer totals without losing digits in a float."""
    digits = currency(code).digits
    sign = "-" if value < 0 else ""
    whole, fraction = divmod(abs(value), 10**digits)
    whole_text = f"{whole:,}" if grouping else str(whole)
    suffix = f".{fraction:0{digits}d}" if digits else ""
    return sign + whole_text + suffix


def rate_fraction(value: object) -> Fraction:
    """Parse a positive bounded decimal quotation (CNY per one native unit)."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,13}(?:\.[0-9]{1,12})?", value):
        raise LedgerError("INVALID_EXCHANGE_RATE")
    result = Fraction(value)
    if not 0 < result <= 10**12:
        raise LedgerError("INVALID_EXCHANGE_RATE")
    return result


def convert_minor(value: int, source: str, target: str, source_rate: str, target_rate: str) -> int:
    """Convert exact smallest units, rounding once half away from zero for display."""
    ratio = rate_fraction(source_rate) / rate_fraction(target_rate)
    ratio *= Fraction(10 ** currency(target).digits, 10 ** currency(source).digits)
    numerator = abs(value) * ratio.numerator
    whole, remainder = divmod(numerator, ratio.denominator)
    rounded = whole + (remainder * 2 >= ratio.denominator)
    return -rounded if value < 0 else rounded
