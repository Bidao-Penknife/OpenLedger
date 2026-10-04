"""Money conversion and aggregation must retain every cent and reject coercion."""

from decimal import localcontext
from typing import cast

import pytest

from openledger.domain.errors import LedgerError
from openledger.domain.money import (
    MAX_EVENT_MINOR,
    MAX_INT64,
    MIN_INT64,
    checked_aggregate,
    parse_amount,
    validate_minor,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0.01", 1),
        ("128", 12_800),
        ("1.2", 120),
        (" 0001.20 \n", 120),
        ("999999999999.99", MAX_EVENT_MINOR),
    ],
)
def test_parse_exact_positive_amount(text: str, expected: int) -> None:
    assert parse_amount(text) == expected


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("", "INVALID_AMOUNT"),
        ("0", "INVALID_AMOUNT"),
        ("-1", "INVALID_AMOUNT"),
        ("+1", "INVALID_AMOUNT"),
        ("1e2", "INVALID_AMOUNT"),
        ("NaN", "INVALID_AMOUNT"),
        ("Infinity", "INVALID_AMOUNT"),
        ("1,000", "INVALID_AMOUNT"),
        ("1元", "INVALID_AMOUNT"),
        ("１２.３", "INVALID_AMOUNT"),
        ("1.001", "AMOUNT_PRECISION"),
        ("1.000", "AMOUNT_PRECISION"),
        ("1000000000000", "AMOUNT_OUT_OF_RANGE"),
        ("9" * 100, "AMOUNT_OUT_OF_RANGE"),
        (True, "INVALID_AMOUNT"),
        (1.25, "INVALID_AMOUNT"),
    ],
)
def test_parse_rejects_lossy_or_ambiguous_amounts(value: object, code: str) -> None:
    with pytest.raises(LedgerError) as caught:
        parse_amount(cast(str, value))
    assert caught.value.code == code


def test_decimal_context_cannot_round_amounts() -> None:
    with localcontext() as context:
        context.prec = 2
        assert parse_amount("999999999999.99") == MAX_EVENT_MINOR


@pytest.mark.parametrize("value", [True, False, 1.0, "100", None])
def test_event_minor_rejects_implicit_conversions(value: object) -> None:
    with pytest.raises(LedgerError, match="INVALID_AMOUNT"):
        validate_minor(value, signed=True, allow_zero=True)


def test_event_range_and_zero_rules_are_separate_from_aggregate_range() -> None:
    assert validate_minor(-MAX_EVENT_MINOR, signed=True) == -MAX_EVENT_MINOR
    assert validate_minor(0, signed=True, allow_zero=True) == 0
    assert validate_minor(MAX_EVENT_MINOR) == MAX_EVENT_MINOR
    for value in (-1, 0):
        with pytest.raises(LedgerError, match="INVALID_AMOUNT"):
            validate_minor(value)
    for value in (MAX_EVENT_MINOR + 1, -MAX_EVENT_MINOR - 1):
        with pytest.raises(LedgerError, match="AMOUNT_OUT_OF_RANGE"):
            validate_minor(value, signed=True)
    assert checked_aggregate(MAX_EVENT_MINOR + 1) == MAX_EVENT_MINOR + 1


@pytest.mark.parametrize("value", [MIN_INT64, MAX_INT64, 0])
def test_aggregate_accepts_signed_int64_boundary(value: int) -> None:
    assert checked_aggregate(value) == value


@pytest.mark.parametrize("value", [MIN_INT64 - 1, MAX_INT64 + 1, True, 1.0])
def test_aggregate_rejects_overflow_or_float(value: object) -> None:
    with pytest.raises(LedgerError, match="AGGREGATE_OUT_OF_RANGE"):
        checked_aggregate(cast(int, value))


def test_aggregate_checks_final_total_after_exact_python_cancellation() -> None:
    assert checked_aggregate(sum([MAX_INT64, MAX_INT64, -MAX_INT64])) == MAX_INT64
