"""Versioned JSON boundary for Android; parsing has no authority to move money."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from openledger import __version__
from openledger.application.dto.ledger import TransactionFields
from openledger.application.dto.parsing import ChannelAccountMapping, ParseChoice, ParseRequest
from openledger.application.dto.queries import TransactionFilter
from openledger.application.parsing import LocalParser
from openledger.domain.errors import LedgerError
from openledger.domain.money import parse_amount, validate_minor
from openledger.domain.values import normalize_id, utc_now
from openledger.infrastructure.database.database import CURRENT_SCHEMA_VERSION, Database
from openledger.infrastructure.integrity import validate_financial_integrity
from openledger.infrastructure.ledger import LedgerService
from openledger.infrastructure.queries import LedgerQueries

API_VERSION = 1
_MAX_REQUEST_BYTES = 32_768
_ZONE = "Asia/Shanghai"


def _keys(value: dict[str, Any], allowed: set[str]) -> None:
    if set(value) - allowed:
        raise LedgerError("INVALID_ENVELOPE")


def _text(value: dict[str, Any], key: str, *, maximum: int = 8192) -> str:
    item = value.get(key)
    if not isinstance(item, str) or len(item) > maximum:
        raise LedgerError("INVALID_ENVELOPE")
    return item


def _nullable_text(value: dict[str, Any], key: str) -> str | None:
    return None if value.get(key) is None else _text(value, key)


def _integer(value: dict[str, Any], key: str, default: int) -> int:
    item = value.get(key, default)
    if type(item) is not int:
        raise LedgerError("INVALID_ENVELOPE")
    return item


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise LedgerError("INVALID_ENVELOPE")
    return value


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LedgerError("INVALID_ENVELOPE")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise LedgerError("INVALID_ENVELOPE")


def _json_default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError("Unsupported bridge value")


def _day(value: dict[str, Any], key: str) -> date:
    text = _text(value, key, maximum=10)
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
        raise LedgerError("INVALID_DATE")
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise LedgerError("INVALID_DATE") from error


def _opening_amount(text: str) -> int:
    """Extend the shared positive parser only for explicit zero/negative openings."""
    stripped = text.strip()
    if re.fullmatch(r"0+(?:\.0{1,2})?", stripped):
        return 0
    minor = -parse_amount(stripped[1:]) if stripped.startswith("-") else parse_amount(stripped)
    return validate_minor(minor, signed=True, allow_zero=True)


class MobileLedger:
    """One private Android data directory, serialized calls and durable retry receipts.

    The Activity passes its own files directory. This adapter imports no Qt,
    starts no network connection and never reads Windows application settings.
    """

    def __init__(self, directory: str, *, clock: Callable[[], datetime] = utc_now) -> None:
        root = Path(directory)
        if not root.is_absolute():
            raise LedgerError("INVALID_DATA_DIRECTORY")
        self.clock = clock
        self.database = Database(root.resolve() / "database" / "openledger.sqlite3")
        self.database.initialize()
        with self.database.read() as connection:
            validate_financial_integrity(connection)
        self.ledger = LedgerService(self.database, clock=clock, time_zone=_ZONE)
        self.ledger.ensure_defaults()
        self.queries = LedgerQueries(self.database)
        self._lock = RLock()

    def call(self, request_json: str) -> str:
        """Return a JSON envelope; invalid requests and previews perform no writes."""
        try:
            if (
                not isinstance(request_json, str)
                or len(request_json.encode("utf-8")) > _MAX_REQUEST_BYTES
            ):
                raise LedgerError("INVALID_ENVELOPE")
            envelope = _object(
                json.loads(request_json, object_pairs_hook=_unique, parse_constant=_reject_constant)
            )
            _keys(envelope, {"api_version", "action", "body"})
            if (
                type(envelope.get("api_version")) is not int
                or envelope["api_version"] != API_VERSION
            ):
                raise LedgerError("UNSUPPORTED_API_VERSION")
            action = _text(envelope, "action", maximum=40)
            body = _object(envelope.get("body", {}))
            with self._lock:
                result = self._dispatch(action, body)
            response = {"api_version": API_VERSION, "ok": True, "data": result}
        except LedgerError as error:
            response = {"api_version": API_VERSION, "ok": False, "error": {"code": error.code}}
        except (ValueError, TypeError, UnicodeError, RecursionError):
            response = {
                "api_version": API_VERSION,
                "ok": False,
                "error": {"code": "INVALID_ENVELOPE"},
            }
        except (OSError, sqlite3.Error):
            response = {
                "api_version": API_VERSION,
                "ok": False,
                "error": {"code": "STORAGE_IO_ERROR"},
            }
        return json.dumps(response, ensure_ascii=False, default=_json_default, allow_nan=False)

    def _today(self) -> date:
        return self.clock().astimezone(ZoneInfo(_ZONE)).date()

    def _dispatch(self, action: str, body: dict[str, Any]) -> dict[str, Any]:
        if action == "snapshot":
            _keys(body, {"page", "page_size", "search", "book_id"})
            today = self._today()
            filters = TransactionFilter(
                page=_integer(body, "page", 0),
                page_size=_integer(body, "page_size", 25),
                search=_text(body, "search") if "search" in body else "",
                book_id=_nullable_text(body, "book_id"),
            )
            return {
                "core_version": __version__,
                "schema_version": CURRENT_SCHEMA_VERSION,
                "sqlite_version": sqlite3.sqlite_version,
                "today": today.isoformat(),
                "time_zone": _ZONE,
                "preferences": self.ledger.preferences(),
                "books": self.ledger.entities("book"),
                "accounts": self.ledger.entities("account", include_archived=True),
                "categories": self.ledger.entities("category"),
                "payment_methods": self.ledger.entities("payment_method"),
                "overview": asdict(self.queries.overview(today)),
                "transactions": asdict(self.queries.transactions(filters)),
            }
        if action == "preview":
            return self._preview(body)
        if action == "record":
            return self._record(body)
        if action == "create_account":
            _keys(
                body, {"request_id", "name", "account_type", "opening_amount", "balance_start_on"}
            )
            request_id = normalize_id(_text(body, "request_id"))
            payload = {
                "id": str(uuid5(NAMESPACE_URL, f"openledger:mobile:account:{request_id}")),
                "name": _text(body, "name", maximum=100),
                "account_type": _text(body, "account_type", maximum=20),
                "opening_balance_minor": _opening_amount(_text(body, "opening_amount")),
                "balance_start_on": _day(body, "balance_start_on"),
            }
            return asdict(self.ledger.execute(request_id, "account.create.v1", payload))
        if action == "create_book":
            _keys(body, {"request_id", "name"})
            request_id = normalize_id(_text(body, "request_id"))
            return asdict(
                self.ledger.execute(
                    request_id,
                    "book.create.v1",
                    {
                        "id": str(uuid5(NAMESPACE_URL, f"openledger:mobile:book:{request_id}")),
                        "name": _text(body, "name", maximum=100),
                    },
                )
            )
        raise LedgerError("UNSUPPORTED_ACTION")

    def _preview(self, body: dict[str, Any]) -> dict[str, Any]:
        _keys(body, {"text", "book_id", "account_id"})
        preferences = self.ledger.preferences()
        categories = self.ledger.entities("category")
        accounts = self.ledger.entities("account")
        payments = self.ledger.entities("payment_method")
        request = ParseRequest(
            draft_id=str(uuid5(NAMESPACE_URL, "openledger:mobile:preview")),
            revision=1,
            text=_text(body, "text"),
            reference_date=self._today(),
            time_zone=_ZONE,
            current_book_id=_nullable_text(body, "book_id")
            or _nullable_text(preferences, "default_book_id"),
            default_account_id=_nullable_text(body, "account_id")
            or _nullable_text(preferences, "default_account_id"),
            category_choices=tuple(
                ParseChoice(str(row["id"]), str(row["name"]), str(row["transaction_kind"]))
                for row in categories
            ),
            account_choices=tuple(
                ParseChoice(str(row["id"]), str(row["name"])) for row in accounts
            ),
            payment_method_choices=tuple(
                ParseChoice(str(row["id"]), str(row["name"])) for row in payments
            ),
            channel_account_mappings=tuple(
                ChannelAccountMapping(str(row["id"]), str(row["default_account_id"]))
                for row in payments
                if row["default_account_id"] is not None
            ),
        )
        return asdict(LocalParser().parse(request))

    def _record(self, body: dict[str, Any]) -> dict[str, Any]:
        _keys(body, {"request_id", "fields"})
        request_id = normalize_id(_text(body, "request_id"))
        values = _object(body.get("fields"))
        _keys(
            values,
            {
                "kind",
                "amount",
                "account_id",
                "book_id",
                "category_id",
                "occurred_on",
                "occurrence_precision",
                "time_period",
                "occurred_at_utc",
                "payment_method_id",
                "counterparty",
                "merchant",
                "location",
                "note",
                "source_text",
            },
        )
        precision = (
            _text(values, "occurrence_precision") if "occurrence_precision" in values else "date"
        )
        exact = _nullable_text(values, "occurred_at_utc")
        fields = TransactionFields(
            kind=_text(values, "kind", maximum=20),
            amount_minor=parse_amount(_text(values, "amount", maximum=40)),
            account_id=_text(values, "account_id"),
            book_id=_text(values, "book_id"),
            category_id=_text(values, "category_id"),
            occurred_on=_day(values, "occurred_on"),
            time_zone=_ZONE,
            occurrence_precision=precision,
            time_period=_nullable_text(values, "time_period"),
            occurred_at_utc=datetime.fromisoformat(exact) if exact is not None else None,
            payment_method_id=_nullable_text(values, "payment_method_id"),
            counterparty=_nullable_text(values, "counterparty"),
            merchant=_nullable_text(values, "merchant"),
            location=_nullable_text(values, "location"),
            note=_nullable_text(values, "note"),
            source="local_rule" if values.get("source_text") else "manual",
            source_text=_nullable_text(values, "source_text"),
        )
        return asdict(
            self.ledger.record(
                fields,
                request_id=request_id,
                transaction_id=str(
                    uuid5(NAMESPACE_URL, f"openledger:mobile:transaction:{request_id}")
                ),
            )
        )
