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
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openledger import __version__
from openledger.application.dto.ledger import TransactionFields
from openledger.application.dto.parsing import ChannelAccountMapping, ParseChoice, ParseRequest
from openledger.application.dto.queries import TransactionFilter
from openledger.application.parsing import LocalParser
from openledger.domain.currencies import CURRENCIES, currency
from openledger.domain.errors import LedgerError
from openledger.domain.money import parse_amount, validate_minor
from openledger.domain.values import normalize_id, utc_now
from openledger.infrastructure.analytics import AnalyticsService
from openledger.infrastructure.database.database import CURRENT_SCHEMA_VERSION, Database
from openledger.infrastructure.integrity import validate_financial_integrity
from openledger.infrastructure.ledger import LedgerService
from openledger.infrastructure.queries import LedgerQueries
from openledger.mobile.files import MobileFiles
from openledger.mobile.mutations import MobileMutations
from openledger.mobile.reports import report

if TYPE_CHECKING:
    from openledger.application.ports.ai import AIProvider

API_VERSION = 1
_MAX_REQUEST_BYTES = 32_768
_ZONE = "Asia/Shanghai"


def _safe_integers(value: Any) -> Any:
    """Android JSON numbers beyond int64 become doubles; preserve them as text."""
    if type(value) is int and not -(2**63) <= value < 2**63:
        return str(value)
    if isinstance(value, dict):
        return {key: _safe_integers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_integers(item) for item in value]
    return value


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

    def __init__(
        self,
        directory: str,
        *,
        clock: Callable[[], datetime] = utc_now,
        staging_directory: str | None = None,
        time_zone: str = _ZONE,
        ai_provider: AIProvider | None = None,
    ) -> None:
        root = Path(directory)
        if not root.is_absolute():
            raise LedgerError("INVALID_DATA_DIRECTORY")
        try:
            if not isinstance(time_zone, str) or len(time_zone) > 80:
                raise ValueError("Invalid zone")
            ZoneInfo(time_zone)
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise LedgerError("INVALID_TIMEZONE") from error
        self.time_zone = time_zone
        self.ai_provider = ai_provider
        self.clock = clock
        self.database = Database(root.resolve() / "database" / "openledger.sqlite3")
        self.database.initialize()
        with self.database.read() as connection:
            validate_financial_integrity(connection)
        self.ledger = LedgerService(self.database, clock=clock, time_zone=self.time_zone)
        self.ledger.ensure_defaults()
        self.queries = LedgerQueries(self.database)
        self.mutations = MobileMutations(self.ledger)
        self.files = MobileFiles(
            root.resolve(),
            Path(staging_directory) if staging_directory else root.resolve() / "staging",
            self.database,
        )
        self._lock = RLock()

    def call(self, request_json: str, secret: str | None = None) -> str:
        """Return a JSON envelope; invalid requests and previews perform no writes."""
        try:
            if not isinstance(request_json, str) or len(request_json.encode("utf-8")) > 131072:
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
            if (
                action != "import_prepare"
                and len(request_json.encode("utf-8")) > _MAX_REQUEST_BYTES
            ):
                raise LedgerError("INVALID_ENVELOPE")
            body = _object(envelope.get("body", {}))
            with self._lock:
                result = self._dispatch(action, body, secret)
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
        return json.dumps(
            _safe_integers(response), ensure_ascii=False, default=_json_default, allow_nan=False
        )

    def _today(self) -> date:
        return self.clock().astimezone(ZoneInfo(self.time_zone)).date()

    def _dispatch(
        self, action: str, body: dict[str, Any], secret: str | None = None
    ) -> dict[str, Any]:
        if action == "validate_time_zone":
            _keys(body, {"time_zone"})
            zone = _text(body, "time_zone", maximum=80)
            try:
                ZoneInfo(zone)
            except (ValueError, ZoneInfoNotFoundError) as error:
                raise LedgerError("INVALID_TIMEZONE") from error
            return {"time_zone": zone}
        if action in {"ai_preview", "validate_ai_config"}:
            return self._ai(action, body, secret)
        if action == "check_updates":
            from openledger.mobile.updates import MobileUpdateService

            _keys(body, {"current_version", "include_prerelease"})
            if type(body.get("include_prerelease", True)) is not bool:
                raise LedgerError("INVALID_ENVELOPE")
            return MobileUpdateService(_text(body, "current_version", maximum=40)).check(
                body.get("include_prerelease", True)
            )
        if action == "analytics":
            return report(AnalyticsService(self.database, clock=self.clock), body)
        if action in {
            "import_headers",
            "import_preview",
            "import_prepare",
            "import_commit",
            "export_transactions",
        }:
            from openledger.mobile.exchange import MobileExchange

            return MobileExchange(self.ledger, self.files, self.time_zone).dispatch(action, body)
        if action == "mutate":
            return self.mutations.execute(body)
        if action == "currency_state":
            _keys(body, set())
            with self.database.read() as connection:
                return {
                    "supported": [asdict(value) for value in CURRENCIES.values()],
                    "settings": dict(
                        connection.execute("SELECT * FROM currency_settings").fetchone()
                    ),
                    "rates": [
                        dict(row)
                        for row in connection.execute(
                            "SELECT * FROM exchange_rates ORDER BY currency_code,effective_on DESC"
                        )
                    ],
                }
        if action.startswith("capture_"):
            from openledger.mobile.captures import MobileCaptures

            return MobileCaptures(self).dispatch(action, body)
        if action == "transaction_detail":
            _keys(body, {"id"})
            from openledger.mobile.attachments import MobileAttachments

            identifier = normalize_id(_text(body, "id"))
            return {
                **self.ledger.transaction(identifier),
                "attachments": MobileAttachments(self.ledger, self.files).rows(identifier),
            }
        if action in {
            "attachment_prepare",
            "attachment_commit",
            "attachment_view",
            "attachment_delete",
            "attachment_restore",
        }:
            from openledger.mobile.attachments import MobileAttachments

            return MobileAttachments(self.ledger, self.files).dispatch(action, body)
        if action == "account_detail":
            _keys(body, {"id"})
            identifier = normalize_id(_text(body, "id"))
            row = next(
                (
                    item
                    for item in self.ledger.entities("account", include_archived=True)
                    if item["id"] == identifier
                ),
                None,
            )
            if row is None:
                raise LedgerError("ENTITY_NOT_FOUND")
            with self.database.read() as connection:
                entry = connection.execute(
                    "SELECT e.delta_minor FROM account_entries e "
                    "JOIN transactions t ON t.id=e.transaction_id WHERE e.account_id=? "
                    "AND t.kind='opening' AND t.deleted_at_utc IS NULL",
                    (identifier,),
                ).fetchone()
            return {
                **row,
                "balance_minor": self.ledger.balances()[identifier],
                "opening_balance_minor": 0 if entry is None else int(entry[0]),
            }
        if action == "backup_create":
            _keys(body, {"filename"})
            return self.files.backup(_text(body, "filename", maximum=120))
        if action == "backup_prepare":
            _keys(body, {"filename"})
            return self.files.prepare_restore(_text(body, "filename", maximum=120))
        if action == "backup_restore":
            _keys(body, {"filename", "request_id"})
            return self.files.restore(
                _text(body, "filename", maximum=120), normalize_id(_text(body, "request_id"))
            )
        if action == "snapshot":
            _keys(
                body,
                {
                    "page",
                    "page_size",
                    "search",
                    "book_id",
                    "account_id",
                    "kind",
                    "start_on",
                    "end_on",
                    "category_id",
                    "tag_id",
                    "include_deleted",
                },
            )
            if type(body.get("include_deleted", False)) is not bool:
                raise LedgerError("INVALID_FILTER")
            today = self._today()
            filters = TransactionFilter(
                page=_integer(body, "page", 0),
                page_size=_integer(body, "page_size", 25),
                search=_text(body, "search") if "search" in body else "",
                book_id=_nullable_text(body, "book_id"),
                account_id=_nullable_text(body, "account_id"),
                category_id=_nullable_text(body, "category_id"),
                tag_id=_nullable_text(body, "tag_id"),
                kind=_nullable_text(body, "kind"),
                start_on=_day(body, "start_on") if body.get("start_on") else None,
                end_on=_day(body, "end_on") if body.get("end_on") else None,
                include_deleted=body.get("include_deleted", False),
            )
            return {
                "core_version": __version__,
                "schema_version": CURRENT_SCHEMA_VERSION,
                "sqlite_version": sqlite3.sqlite_version,
                "today": today.isoformat(),
                "time_zone": self.time_zone,
                "preferences": self.ledger.preferences(),
                "books": self.ledger.entities("book"),
                "accounts": self.ledger.entities("account", include_archived=True),
                "categories": self.ledger.entities("category"),
                "payment_methods": self.ledger.entities("payment_method"),
                "tags": self.ledger.entities("tag"),
                "catalogs": {
                    entity: self.ledger.entities(entity, include_archived=True)
                    for entity in ("book", "account", "category", "tag", "payment_method")
                },
                "import_batches": self.ledger.import_batches(),
                "overview": asdict(self.queries.overview(today)),
                "transactions": asdict(self.queries.transactions(filters)),
            }
        if action == "preview":
            return self._preview(body)
        if action == "record":
            return self._record(body)
        if action == "create_account":
            _keys(
                body,
                {
                    "request_id",
                    "name",
                    "account_type",
                    "opening_amount",
                    "balance_start_on",
                    "currency_code",
                },
            )
            request_id = normalize_id(_text(body, "request_id"))
            payload = {
                "id": str(uuid5(NAMESPACE_URL, f"openledger:mobile:account:{request_id}")),
                "name": _text(body, "name", maximum=100),
                "account_type": _text(body, "account_type", maximum=20),
                "currency_code": currency(body.get("currency_code", "CNY")).code,
                "opening_balance_minor": self._signed_opening(body),
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
        return asdict(LocalParser().parse(self._parse_request(body)))

    @staticmethod
    def _signed_opening(body: dict[str, Any]) -> int:
        from openledger.mobile.mutations import signed_amount

        return signed_amount(_text(body, "opening_amount"), str(body.get("currency_code", "CNY")))

    def _parse_request(self, body: dict[str, Any]) -> ParseRequest:
        _keys(body, {"text", "book_id", "account_id"})
        preferences = self.ledger.preferences()
        categories = self.ledger.entities("category")
        accounts = self.ledger.entities("account")
        payments = self.ledger.entities("payment_method")
        default_account = _nullable_text(body, "account_id") or _nullable_text(
            preferences, "default_account_id"
        )
        return ParseRequest(
            draft_id=str(uuid5(NAMESPACE_URL, "openledger:mobile:preview")),
            revision=1,
            text=_text(body, "text"),
            reference_date=self._today(),
            time_zone=self.time_zone,
            current_book_id=_nullable_text(body, "book_id")
            or _nullable_text(preferences, "default_book_id"),
            default_account_id=_nullable_text(body, "account_id")
            or _nullable_text(preferences, "default_account_id"),
            category_choices=tuple(
                ParseChoice(str(row["id"]), str(row["name"]), str(row["transaction_kind"]))
                for row in categories
            ),
            account_choices=tuple(
                ParseChoice(
                    str(row["id"]), str(row["name"]), currency_code=str(row["currency_code"])
                )
                for row in accounts
            ),
            payment_method_choices=tuple(
                ParseChoice(str(row["id"]), str(row["name"])) for row in payments
            ),
            channel_account_mappings=tuple(
                ChannelAccountMapping(str(row["id"]), str(row["default_account_id"]))
                for row in payments
                if row["default_account_id"] is not None
            ),
            currency_code=self.mutations.account_currency(default_account),
        )

    def _ai(self, action: str, body: dict[str, Any], secret: str | None) -> dict[str, Any]:
        from openledger.application.dto.ai import AIConfig
        from openledger.infrastructure.ai import AIParser, validate_config

        _keys(
            body,
            {"config", "text", "book_id", "account_id"} if action == "ai_preview" else {"config"},
        )
        values = _object(body.get("config"))
        _keys(values, {"enabled", "base_url", "model", "allow_local_http"})
        config = AIConfig(**values)
        validate_config(config)
        if action == "validate_ai_config":
            return asdict(config)
        request = self._parse_request(
            {key: value for key, value in body.items() if key != "config"}
        )
        provider = self.ai_provider or AIParser(clock=self.clock)
        return asdict(provider.parse(request, config, secret or ""))

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
                "source",
                "tag_ids",
                "time_zone",
                "currency_code",
            },
        )
        precision = (
            _text(values, "occurrence_precision") if "occurrence_precision" in values else "date"
        )
        exact = _nullable_text(values, "occurred_at_utc")
        source = (
            _text(values, "source", maximum=20)
            if "source" in values
            else ("local_rule" if values.get("source_text") else "manual")
        )
        if source not in {"manual", "local_rule", "ai_assisted"}:
            raise LedgerError("INVALID_ENVELOPE")
        fields = TransactionFields(
            kind=_text(values, "kind", maximum=20),
            amount_minor=parse_amount(
                _text(values, "amount", maximum=40),
                self.mutations.account_currency(values.get("account_id")),
            ),
            currency_code=currency(
                values.get(
                    "currency_code", self.mutations.account_currency(values.get("account_id"))
                )
            ).code,
            account_id=_text(values, "account_id"),
            book_id=_text(values, "book_id"),
            category_id=_text(values, "category_id"),
            occurred_on=_day(values, "occurred_on"),
            time_zone=_text(values, "time_zone", maximum=80)
            if "time_zone" in values
            else self.time_zone,
            occurrence_precision=precision,
            time_period=_nullable_text(values, "time_period"),
            occurred_at_utc=datetime.fromisoformat(exact) if exact is not None else None,
            payment_method_id=_nullable_text(values, "payment_method_id"),
            counterparty=_nullable_text(values, "counterparty"),
            merchant=_nullable_text(values, "merchant"),
            location=_nullable_text(values, "location"),
            note=_nullable_text(values, "note"),
            source=source,
            tag_ids=tuple(values.get("tag_ids", ())),
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
