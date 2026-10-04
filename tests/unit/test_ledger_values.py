"""Input normalization, calendar rules, immutable DTOs and idempotency encoding."""

import hashlib
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from openledger.application.dto.ledger import RefundFields, TransactionFields, TransferFields
from openledger.domain.errors import LedgerError
from openledger.domain.values import (
    canonical_hash,
    normalize_id,
    normalize_text,
    utc_now,
    utc_text,
    validate_occurrence,
)

FIRST = "aa000000-0000-4000-8000-000000000001"
SECOND = "bb000000-0000-4000-8000-000000000002"
NOW = datetime(2026, 10, 2, 8, 30, tzinfo=UTC)


def test_ids_are_canonical_uuid_text() -> None:
    assert normalize_id(" {" + FIRST + "} ") == FIRST
    assert normalize_id(FIRST.upper()) == FIRST
    assert normalize_id(FIRST.replace("-", "")) == FIRST


@pytest.mark.parametrize("value", ["name", "", "not-a-uuid", True, UUID(FIRST)])
def test_invalid_ids_are_not_coerced(value: object) -> None:
    with pytest.raises(LedgerError, match="INVALID_ID"):
        normalize_id(cast(str, value))


def test_text_nfc_empty_and_limits() -> None:
    assert normalize_text("  e\u0301  ", 1, required=True) == "é"
    assert normalize_text(" \n ", 10) is None
    assert normalize_text(None, 10) is None
    assert normalize_text("line 1\nline 2", 20) == "line 1\nline 2"
    with pytest.raises(LedgerError, match="MISSING_REQUIRED_FIELD"):
        normalize_text(" \n", 10, required=True)
    with pytest.raises(LedgerError, match="TEXT_TOO_LONG"):
        normalize_text("你好世界", 3)
    with pytest.raises(LedgerError, match="INVALID_TEXT"):
        normalize_text("\ud800", 10)


@pytest.mark.parametrize("value", [True, 5, b"hello"])
def test_text_rejects_non_strings(value: object) -> None:
    with pytest.raises(LedgerError, match="INVALID_TEXT"):
        normalize_text(cast(str, value), 20)


def test_utc_has_exactly_millisecond_precision_and_fixed_length() -> None:
    source = datetime(2026, 10, 2, 16, 30, 11, 987654, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert utc_text(source) == "2026-10-02T08:30:11.987Z"
    assert utc_text(datetime(1, 1, 1, tzinfo=UTC)) == "0001-01-01T00:00:00.000Z"
    assert len(utc_text(source)) == 24
    clock = utc_now()
    assert clock.utcoffset() == timedelta(0)
    assert clock.microsecond % 1000 == 0


@pytest.mark.parametrize("value", [date(2026, 10, 2), datetime(2026, 10, 2), True])
def test_utc_rejects_missing_timezone_or_non_datetime(value: object) -> None:
    with pytest.raises(LedgerError, match="INVALID_DATE"):
        utc_text(cast(datetime, value))


def test_future_business_date_uses_injected_clock_in_selected_zone() -> None:
    current = datetime(2026, 12, 31, 22, tzinfo=UTC)
    validate_occurrence(date(2027, 1, 1), "Asia/Shanghai", now=current)
    with pytest.raises(LedgerError, match="FUTURE_DATE"):
        validate_occurrence(date(2027, 1, 1), "UTC", now=current)


@pytest.mark.parametrize("value", [NOW, True, "2026-10-02", None])
def test_business_date_rejects_datetime_or_coercion(value: object) -> None:
    with pytest.raises(LedgerError, match="INVALID_DATE"):
        validate_occurrence(cast(date, value), "UTC", now=NOW)


@pytest.mark.parametrize("value", ["", "Asia/NoSuchPlace", "../UTC", True])
def test_timezone_must_be_valid_iana_identifier(value: object) -> None:
    with pytest.raises(LedgerError, match="INVALID_TIMEZONE"):
        validate_occurrence(date(2026, 10, 2), cast(str, value), now=NOW)


@pytest.mark.parametrize(
    ("precision", "period", "instant", "code"),
    [
        ("date", "evening", None, "FIELD_CONFLICT"),
        ("date", None, NOW, "FIELD_CONFLICT"),
        ("period", None, None, "MISSING_REQUIRED_FIELD"),
        ("period", "unknown", None, "FIELD_CONFLICT"),
        ("period", "morning", NOW, "FIELD_CONFLICT"),
        ("exact", None, None, "MISSING_REQUIRED_FIELD"),
        ("exact", "morning", NOW, "FIELD_CONFLICT"),
        ("unknown", None, None, "FIELD_CONFLICT"),
    ],
)
def test_occurrence_precision_does_not_invent_times(
    precision: str, period: str | None, instant: datetime | None, code: str
) -> None:
    with pytest.raises(LedgerError) as caught:
        validate_occurrence(date(2026, 10, 2), "UTC", precision, period, instant, now=NOW)
    assert caught.value.code == code


def test_exact_time_requires_consistent_local_date_and_nonfuture_instant() -> None:
    validate_occurrence(date(2026, 10, 2), "UTC", "exact", occurred_at_utc=NOW, now=NOW)
    with pytest.raises(LedgerError, match="FIELD_CONFLICT"):
        validate_occurrence(date(2026, 10, 1), "UTC", "exact", occurred_at_utc=NOW, now=NOW)
    with pytest.raises(LedgerError, match="FUTURE_DATE"):
        validate_occurrence(
            date(2026, 10, 2),
            "UTC",
            "exact",
            occurred_at_utc=NOW + timedelta(microseconds=1),
            now=NOW,
        )
    with pytest.raises(LedgerError, match="INVALID_DATE"):
        validate_occurrence(
            date(2026, 10, 2),
            "UTC",
            "exact",
            occurred_at_utc=NOW.replace(tzinfo=None),
            now=NOW,
        )


def test_explicit_dst_instants_are_distinct_without_guessing_wall_time() -> None:
    zone = ZoneInfo("America/New_York")
    first = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0)
    second = first.replace(fold=1)
    later = datetime(2026, 11, 2, tzinfo=UTC)
    for instant in (first, second):
        validate_occurrence(
            date(2026, 11, 1),
            "America/New_York",
            "exact",
            occurred_at_utc=instant,
            now=later,
        )
    assert utc_text(first) != utc_text(second)


def test_canonical_hash_has_stable_contract_v1_encoding() -> None:
    payload: dict[str, object] = {"amount_minor": 12800, "expected_version": 1}
    expected = (
        '{"command_type":"transaction.record.v1","contract_version":1,'
        '"payload":{"amount_minor":"12800","expected_version":1}}'
    )
    assert (
        canonical_hash("transaction.record.v1", payload)
        == hashlib.sha256(expected.encode("utf-8")).hexdigest()
    )


def test_hash_normalizes_ids_sets_dates_and_equivalent_utc_instants() -> None:
    first: dict[str, object] = {
        "account_id": FIRST.upper(),
        "tag_ids": (SECOND, FIRST, SECOND),
        "occurred_on": date(2026, 10, 2),
        "occurred_at_utc": NOW,
        "note": " e\u0301 ",
    }
    second: dict[str, object] = {
        "note": "é",
        "occurred_at_utc": NOW.astimezone(ZoneInfo("Asia/Shanghai")),
        "occurred_on": date(2026, 10, 2),
        "tag_ids": [UUID(FIRST), UUID(SECOND)],
        "account_id": UUID(FIRST),
    }
    assert canonical_hash("test.v1", first) == canonical_hash("test.v1", second)


def test_hash_preserves_intent_version_order_and_command_type() -> None:
    payload: dict[str, object] = {"expected_version": 1, "rows": [1, 2]}
    original = canonical_hash("test.v1", payload)
    assert original != canonical_hash("other.v1", payload)
    assert original != canonical_hash("test.v1", {**payload, "expected_version": 2})
    assert original != canonical_hash("test.v1", {**payload, "rows": [2, 1]})


@pytest.mark.parametrize("key", ["request_id", "trace_id", "executed_at_utc", "committed_at_utc"])
def test_hash_rejects_transport_and_execution_metadata_inside_payload(key: str) -> None:
    with pytest.raises(LedgerError, match="INVALID_ENVELOPE"):
        canonical_hash("test.v1", {key: FIRST})


@pytest.mark.parametrize("value", [1.25, float("nan"), Decimal("1.25"), {1, 2}, object()])
def test_hash_rejects_unknown_or_lossy_values(value: object) -> None:
    with pytest.raises(LedgerError, match="INVALID_ENVELOPE"):
        canonical_hash("test.v1", {"value": value})


@pytest.mark.parametrize("value", [True, 1.0, "100"])
def test_hash_amounts_require_integer_minor_units(value: object) -> None:
    with pytest.raises(LedgerError, match="INVALID_AMOUNT"):
        canonical_hash("test.v1", {"amount_minor": value})


def test_dto_is_immutable_and_specialized_inputs_cannot_assign_refund_attribution() -> None:
    transaction = TransactionFields("expense", 2500, FIRST, FIRST, SECOND, date(2026, 10, 2))
    assert transaction.tag_ids == ()
    assert transaction.source == "manual"
    assert transaction.currency_code == "CNY"
    field_name = "amount_minor"
    with pytest.raises(FrozenInstanceError):
        setattr(transaction, field_name, 3500)
    refund = RefundFields(FIRST, 1000, SECOND, date(2026, 10, 2))
    transfer = TransferFields(FIRST, SECOND, 1000, date(2026, 10, 2))
    for dto in (refund, transfer):
        names = {item.name for item in fields(dto)}
        assert "book_id" not in names
        assert "category_id" not in names
