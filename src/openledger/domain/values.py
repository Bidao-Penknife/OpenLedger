"""Canonical identifiers, text, timestamps and command fingerprints."""

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from datetime import UTC, date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openledger.domain.errors import LedgerError

_PERIODS = frozenset({"morning", "noon", "afternoon", "evening", "night"})
_EXECUTION_KEYS = frozenset({"request_id", "trace_id", "executed_at_utc", "committed_at_utc"})


def normalize_id(value: str) -> str:
    """Return canonical lowercase UUID text without inferring IDs from names."""
    if not isinstance(value, str):
        raise LedgerError("INVALID_ID")
    try:
        return str(UUID(value.strip()))
    except (ValueError, AttributeError) as error:
        raise LedgerError("INVALID_ID") from error


def normalize_text(value: str | None, max_length: int, *, required: bool = False) -> str | None:
    """Normalize NFC text and trim its edges; optional empty values become None."""
    if value is None:
        if required:
            raise LedgerError("MISSING_REQUIRED_FIELD")
        return None
    if not isinstance(value, str):
        raise LedgerError("INVALID_TEXT")
    normalized = unicodedata.normalize("NFC", value).strip()
    try:
        normalized.encode("utf-8")
    except UnicodeError as error:
        raise LedgerError("INVALID_TEXT") from error
    if not normalized:
        if required:
            raise LedgerError("MISSING_REQUIRED_FIELD")
        return None
    if len(normalized) > max_length:
        raise LedgerError("TEXT_TOO_LONG")
    return normalized


def _aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise LedgerError("INVALID_DATE")
    try:
        if value.tzinfo is None or value.utcoffset() is None:
            raise LedgerError("INVALID_DATE")
        return value.astimezone(UTC)
    except (ValueError, OverflowError) as error:
        raise LedgerError("INVALID_DATE") from error


def utc_text(value: datetime) -> str:
    """Encode an aware instant as fixed-length UTC milliseconds, truncating sub-ms."""
    return _aware_utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def utc_now() -> datetime:
    """Return a UTC clock value normalized to the persisted millisecond precision."""
    value = datetime.now(UTC)
    return value.replace(microsecond=value.microsecond // 1000 * 1000)


def validate_occurrence(
    occurred_on: date,
    time_zone: str,
    precision: str = "date",
    time_period: str | None = None,
    occurred_at_utc: datetime | None = None,
    *,
    now: datetime,
) -> None:
    """Validate date precision and future bounds against an injected aware clock."""
    if not isinstance(occurred_on, date) or isinstance(occurred_on, datetime):
        raise LedgerError("INVALID_DATE")
    if not isinstance(time_zone, str):
        raise LedgerError("INVALID_TIMEZONE")
    try:
        zone = ZoneInfo(time_zone)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise LedgerError("INVALID_TIMEZONE") from error
    current = _aware_utc(now)
    try:
        today = current.astimezone(zone).date()
    except (ValueError, OverflowError) as error:
        raise LedgerError("INVALID_DATE") from error
    if occurred_on > today:
        raise LedgerError("FUTURE_DATE")
    if precision == "date":
        if time_period is not None or occurred_at_utc is not None:
            raise LedgerError("FIELD_CONFLICT")
    elif precision == "period":
        if time_period is None:
            raise LedgerError("MISSING_REQUIRED_FIELD")
        if (
            not isinstance(time_period, str)
            or time_period not in _PERIODS
            or occurred_at_utc is not None
        ):
            raise LedgerError("FIELD_CONFLICT")
    elif precision == "exact":
        if occurred_at_utc is None:
            raise LedgerError("MISSING_REQUIRED_FIELD")
        if time_period is not None:
            raise LedgerError("FIELD_CONFLICT")
        instant = _aware_utc(occurred_at_utc)
        try:
            local_date = instant.astimezone(zone).date()
        except (ValueError, OverflowError) as error:
            raise LedgerError("INVALID_DATE") from error
        if local_date != occurred_on:
            raise LedgerError("FIELD_CONFLICT")
        if instant > current:
            raise LedgerError("FUTURE_DATE")
    else:
        raise LedgerError("FIELD_CONFLICT")


def _canonical(value: object, *, key: str | None = None) -> object:
    if key is not None and key.endswith("_minor"):
        if not isinstance(value, int) or isinstance(value, bool):
            raise LedgerError("INVALID_AMOUNT")
        return str(value)
    if key == "tag_ids":
        if not isinstance(value, (list, tuple)):
            raise LedgerError("INVALID_ENVELOPE")
        if any(not isinstance(item, (str, UUID)) for item in value):
            raise LedgerError("INVALID_ENVELOPE")
        return sorted({normalize_id(str(item)) for item in value})
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, datetime):
        return utc_text(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        if key == "id" or (
            key is not None and key.endswith("_id") and key != "external_transaction_id"
        ):
            return normalize_id(value)
        normalized = unicodedata.normalize("NFC", value).strip()
        try:
            normalized.encode("utf-8")
        except UnicodeError as error:
            raise LedgerError("INVALID_ENVELOPE") from error
        return normalized
    if isinstance(value, int):
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(item_key, str) for item_key in value):
            raise LedgerError("INVALID_ENVELOPE")
        if any(item_key in _EXECUTION_KEYS for item_key in value):
            raise LedgerError("INVALID_ENVELOPE")
        return {
            item_key: _canonical(item_value, key=item_key) for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    raise LedgerError("INVALID_ENVELOPE")


def canonical_hash(command_type: str, payload: Mapping[str, object]) -> str:
    """Hash semantic command content using contract-v1 canonical JSON encoding."""
    if not isinstance(command_type, str) or not command_type.strip():
        raise LedgerError("INVALID_ENVELOPE")
    if not isinstance(payload, Mapping):
        raise LedgerError("INVALID_ENVELOPE")
    envelope = {
        "command_type": command_type,
        "contract_version": 1,
        "payload": _canonical(payload),
    }
    encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
