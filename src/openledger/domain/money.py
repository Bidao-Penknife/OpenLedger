"""Exact integer money validation; floating point never crosses this boundary."""

import re
from decimal import Decimal, localcontext

from openledger.domain.errors import LedgerError

MAX_EVENT_MINOR = 99_999_999_999_999
MIN_INT64 = -(2**63)
MAX_INT64 = 2**63 - 1

_AMOUNT = re.compile(r"([0-9]+)(?:\.([0-9]+))?\Z")


def parse_amount(text: str) -> int:
    """Parse a positive CNY amount string into cents, without rounding or guessing."""
    if not isinstance(text, str):
        raise LedgerError("INVALID_AMOUNT")
    match = _AMOUNT.fullmatch(text.strip())
    if match is None:
        raise LedgerError("INVALID_AMOUNT")
    whole = match.group(1).lstrip("0") or "0"
    fraction = match.group(2) or ""
    if len(fraction) > 2:
        raise LedgerError("AMOUNT_PRECISION")
    if len(whole) > 12:
        raise LedgerError("AMOUNT_OUT_OF_RANGE")
    # Limit the Decimal precision locally, independent of a caller's global context.
    with localcontext() as context:
        context.prec = 16
        minor = int(Decimal(f"{whole}.{fraction or '0'}") * 100)
    return validate_minor(minor)


def validate_minor(value: object, *, signed: bool = False, allow_zero: bool = False) -> int:
    """Validate one stored event amount, rejecting booleans and implicit coercion."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise LedgerError("INVALID_AMOUNT")
    if not signed and value < 0:
        raise LedgerError("INVALID_AMOUNT")
    if value == 0 and not allow_zero:
        raise LedgerError("INVALID_AMOUNT")
    if abs(value) > MAX_EVENT_MINOR:
        raise LedgerError("AMOUNT_OUT_OF_RANGE")
    return value


def checked_aggregate(value: int) -> int:
    """Check a Python integer total against SQLite's signed 64-bit storage range."""
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < MIN_INT64
        or value > MAX_INT64
    ):
        raise LedgerError("AGGREGATE_OUT_OF_RANGE")
    return value
