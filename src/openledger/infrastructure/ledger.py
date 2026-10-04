"""SQLite implementation of atomic, idempotent financial aggregate commands."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import date, datetime
from typing import cast
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openledger.application.dto.ledger import RefundFields, TransactionFields, TransferFields
from openledger.application.dto.results import BalanceChange, EntityRevision, MutationResult
from openledger.domain.errors import LedgerError
from openledger.domain.money import MAX_INT64, checked_aggregate, validate_minor
from openledger.domain.values import (
    canonical_hash,
    normalize_id,
    normalize_text,
    utc_now,
    utc_text,
    validate_occurrence,
)
from openledger.infrastructure.database.database import Database

_TABLES = {
    "book": "books",
    "account": "accounts",
    "category": "categories",
    "tag": "tags",
    "payment_method": "payment_methods",
    "transaction": "transactions",
    "import_batch": "import_batches",
}
_MANAGEMENT_ENTITIES = {"book", "account", "category", "tag", "payment_method"}
_SOURCES = {"manual", "local_rule", "ai_assisted", "import", "system"}
_KINDS = {"income", "expense", "expense_refund", "transfer", "opening", "adjustment"}
DEFAULT_BOOK_ID = str(uuid5(NAMESPACE_URL, "openledger:default-book"))


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _id(payload: Mapping[str, object], key: str = "id") -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise LedgerError("MISSING_REQUIRED_FIELD")
    return normalize_id(value)


def _integer(payload: Mapping[str, object], key: str, default: int | None = None) -> int:
    value = payload.get(key, default)
    if type(value) is not int:
        raise LedgerError("INVALID_ENVELOPE")
    return value


def _day(value: object) -> date:
    if isinstance(value, datetime):
        raise LedgerError("INVALID_DATE")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as error:
            raise LedgerError("INVALID_DATE") from error
    raise LedgerError("INVALID_DATE")


def _normalized(value: object, key: str = "") -> object:
    """Canonicalize semantics before receipt lookup, without consulting live state."""
    if isinstance(value, Mapping):
        if not all(isinstance(item, str) for item in value):
            raise LedgerError("INVALID_ENVELOPE")
        return {str(item): _normalized(content, str(item)) for item, content in value.items()}
    if key in {"occurred_on", "balance_start_on"}:
        return _day(value)
    if key == "occurred_at_utc" and isinstance(value, str):
        try:
            return datetime.fromisoformat(
                utc_text(datetime.fromisoformat(value.replace("Z", "+00:00"))).replace(
                    "Z", "+00:00"
                )
            )
        except ValueError as error:
            raise LedgerError("INVALID_DATE") from error
    if isinstance(value, datetime):
        return datetime.fromisoformat(utc_text(value).replace("Z", "+00:00"))
    if key == "tag_ids":
        if not isinstance(value, (tuple, list)) or not all(isinstance(item, str) for item in value):
            raise LedgerError("INVALID_ENVELOPE")
        return tuple(sorted({normalize_id(item) for item in value}))
    if isinstance(value, str):
        if key == "id" or (key.endswith("_id") and key != "external_transaction_id"):
            return normalize_id(value)
        return normalize_text(value, max_length=8192)
    if isinstance(value, (tuple, list)):
        return tuple(_normalized(item) for item in value)
    return value


def _keys(payload: Mapping[str, object], allowed: set[str]) -> None:
    if set(payload) - allowed:
        raise LedgerError("INVALID_ENVELOPE")


def _row(connection: sqlite3.Connection, entity: str, identifier: str) -> sqlite3.Row:
    found = connection.execute(
        f"SELECT * FROM {_TABLES[entity]} WHERE id=?", (identifier,)
    ).fetchone()
    if found is None:
        raise LedgerError("ENTITY_NOT_FOUND")
    return cast(sqlite3.Row, found)


def _balance(connection: sqlite3.Connection, account_id: str) -> int:
    rows = connection.execute(
        "SELECT e.delta_minor FROM account_entries e JOIN transactions t ON t.id=e.transaction_id "
        "WHERE e.account_id=? AND t.deleted_at_utc IS NULL",
        (account_id,),
    )
    return checked_aggregate(sum(int(row[0]) for row in rows))


def _snapshot(connection: sqlite3.Connection, entity: str, identifier: str) -> dict[str, object]:
    result: dict[str, object] = dict(_row(connection, entity, identifier))
    if entity in {"book", "account"}:
        preferences = connection.execute(
            "SELECT * FROM app_preferences WHERE singleton=1"
        ).fetchone()
        result["default_" + entity + "_id"] = preferences["default_" + entity + "_id"]
    if entity == "transaction":
        result["entries"] = [
            dict(row)
            for row in connection.execute(
                "SELECT id,account_id,delta_minor FROM account_entries "
                "WHERE transaction_id=? ORDER BY account_id",
                (identifier,),
            )
        ]
        result["tag_ids"] = [
            row[0]
            for row in connection.execute(
                "SELECT tag_id FROM transaction_tags WHERE transaction_id=? ORDER BY tag_id",
                (identifier,),
            )
        ]
    return result


class _Work:
    """Collect revisions and original balances within one database transaction."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        request_id: str,
        now: datetime,
        fault: Callable[[str], None],
    ) -> None:
        self.connection = connection
        self.request_id = request_id
        self.now = datetime.fromisoformat(utc_text(now).replace("Z", "+00:00"))
        self.timestamp = utc_text(now)
        self.fault = fault
        self.revisions: list[EntityRevision] = []
        self.before_balances: dict[str, int] = {}

    def account(self, identifier: str) -> None:
        if identifier not in self.before_balances:
            self.before_balances[identifier] = _balance(self.connection, identifier)

    def journal(
        self, entity: str, identifier: str, operation: str, before: dict[str, object] | None
    ) -> None:
        after = _snapshot(self.connection, entity, identifier)
        audit_id = str(uuid4())
        version = cast(int, after["version"])
        self.connection.execute(
            "INSERT INTO audit_events(id,request_id,entity_type,entity_id,"
            "action,old_version,new_version,"
            "before_json,after_json,created_at_utc) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                audit_id,
                self.request_id,
                entity,
                identifier,
                operation,
                None if before is None else before["version"],
                version,
                None if before is None else _json(before),
                _json(after),
                self.timestamp,
            ),
        )
        self.connection.execute(
            "INSERT INTO change_log(audit_event_id,entity_type,entity_id,"
            "version,operation,changed_at_utc) "
            "VALUES(?,?,?,?,?,?)",
            (audit_id, entity, identifier, version, operation, self.timestamp),
        )
        self.revisions.append(EntityRevision(entity, identifier, version, operation))
        self.fault("after_audit")


class LedgerService:
    """Implement the ledger port; GUI callers receive DTOs, never database connections."""

    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] = utc_now,
        fault_hook: Callable[[str], None] | None = None,
        time_zone: str = "UTC",
    ) -> None:
        self.database = database
        self.clock = clock
        self._fault = fault_hook or (lambda _stage: None)
        try:
            ZoneInfo(time_zone)
        except (ZoneInfoNotFoundError, ValueError, TypeError) as error:
            raise LedgerError("INVALID_TIMEZONE") from error
        self.time_zone = time_zone

    def execute(
        self, request_id: str, command_type: str, payload: Mapping[str, object]
    ) -> MutationResult:
        """Replay before state/version checks, and report success only after commit."""
        try:
            return self._execute(request_id, command_type, payload)
        except sqlite3.Error as error:
            primary = getattr(error, "sqlite_errorcode", 0) & 255
            code = {
                sqlite3.SQLITE_BUSY: "DATABASE_BUSY",
                sqlite3.SQLITE_LOCKED: "DATABASE_BUSY",
                sqlite3.SQLITE_READONLY: "DATABASE_READ_ONLY",
                sqlite3.SQLITE_FULL: "DISK_FULL",
                sqlite3.SQLITE_IOERR: "STORAGE_IO_ERROR",
            }.get(primary, "INTEGRITY_FAILED")
            raise LedgerError(code) from error

    def _execute(
        self, request_id: str, command_type: str, payload: Mapping[str, object]
    ) -> MutationResult:
        request_id = normalize_id(request_id)
        normalized = cast(dict[str, object], _normalized(payload))
        digest = canonical_hash(command_type, normalized)
        with self.database.write() as connection:
            receipt = connection.execute(
                "SELECT * FROM command_receipts WHERE request_id=?", (request_id,)
            ).fetchone()
            if receipt is not None:
                if receipt["command_type"] != command_type or receipt["payload_hash"] != digest:
                    raise LedgerError("IDEMPOTENCY_KEY_REUSED")
                return self._replay(receipt["result_json"])
            work = _Work(connection, request_id, self.clock(), self._fault)
            data = self._dispatch(work, command_type, normalized)
            changes = tuple(
                BalanceChange(identifier, before, _balance(connection, identifier))
                for identifier, before in sorted(work.before_balances.items())
            )
            # Check total assets independently of individual account bounds.
            checked_aggregate(
                sum(
                    _balance(connection, row[0])
                    for row in connection.execute("SELECT id FROM accounts")
                )
            )
            sequence = int(
                connection.execute("SELECT COALESCE(MAX(seq),0) FROM change_log").fetchone()[0]
            )
            result = MutationResult(
                request_id,
                command_type,
                "applied" if work.revisions else "no_change",
                work.timestamp,
                tuple(item.id for item in work.revisions),
                tuple(work.revisions),
                changes,
                sequence,
                data,
            )
            self._fault("before_receipt")
            stored = asdict(result)
            stored.pop("replayed")
            connection.execute(
                "INSERT INTO command_receipts(request_id,command_type,payload_hash,"
                "result_json,completed_at_utc) "
                "VALUES(?,?,?,?,?)",
                (request_id, command_type, digest, _json(stored), work.timestamp),
            )
            self._fault("before_commit")
        return result

    @staticmethod
    def _replay(encoded: str) -> MutationResult:
        try:
            value = json.loads(encoded)
            value["entity_ids"] = tuple(value["entity_ids"])
            value["changed_entities"] = tuple(
                EntityRevision(**item) for item in value["changed_entities"]
            )
            value["balance_changes"] = tuple(
                BalanceChange(**item) for item in value["balance_changes"]
            )
            return MutationResult(**value, replayed=True)
        except (KeyError, TypeError, ValueError) as error:
            raise LedgerError("INTEGRITY_FAILED") from error

    def record(
        self, fields: TransactionFields, *, request_id: str, transaction_id: str
    ) -> MutationResult:
        """Record one confirmed income or expense; never infer an account."""
        return self.execute(
            request_id, "transaction.record.v1", {"id": transaction_id, "fields": asdict(fields)}
        )

    def refund(
        self, fields: RefundFields, *, request_id: str, transaction_id: str
    ) -> MutationResult:
        """Record a refund against a live expense."""
        return self.execute(
            request_id, "refund.record.v1", {"id": transaction_id, "fields": asdict(fields)}
        )

    def transfer(
        self, fields: TransferFields, *, request_id: str, transaction_id: str
    ) -> MutationResult:
        """Move equal integer amounts between two different accounts."""
        return self.execute(
            request_id, "transfer.record.v1", {"id": transaction_id, "fields": asdict(fields)}
        )

    def balances(self) -> dict[str, int]:
        """Include archived accounts; sum exact Python integers in one snapshot."""
        with self.database.read() as connection:
            return {
                row[0]: _balance(connection, row[0])
                for row in connection.execute("SELECT id FROM accounts ORDER BY id")
            }

    def total_assets(self) -> int:
        """Check signed int64 limits after an exact aggregate."""
        return checked_aggregate(sum(self.balances().values()))

    def preferences(self) -> dict[str, object]:
        """Expose explicit default selections without inventing account mappings."""
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT default_book_id,default_account_id FROM app_preferences WHERE singleton=1"
            ).fetchone()
            return (
                dict(row)
                if row is not None
                else {"default_book_id": None, "default_account_id": None}
            )

    def entities(
        self, entity: str, *, include_archived: bool = False
    ) -> tuple[dict[str, object], ...]:
        """Read available management entities without exposing mutable rows."""
        if entity not in _MANAGEMENT_ENTITIES:
            raise LedgerError("INVALID_ENVELOPE")
        with self.database.read() as connection:
            return tuple(
                dict(row)
                for row in connection.execute(
                    f"SELECT * FROM {_TABLES[entity]} "
                    + ("" if include_archived else "WHERE is_archived=0 ")
                    + "ORDER BY sort_order,id"
                )
            )

    def import_batches(self) -> tuple[dict[str, object], ...]:
        """List immutable source identity and each batch's current reversal status."""
        with self.database.read() as connection:
            return tuple(
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM import_batches ORDER BY created_at_utc DESC,id DESC"
                )
            )

    def transaction(self, transaction_id: str) -> dict[str, object]:
        """Get entries, tags and dynamically inherited refund classification."""
        transaction_id = normalize_id(transaction_id)
        with self.database.read() as connection:
            result = _snapshot(connection, "transaction", transaction_id)
            original = None
            if result["kind"] == "expense_refund":
                original = _row(
                    connection, "transaction", cast(str, result["original_transaction_id"])
                )
            result["effective_book_id"] = (
                original["book_id"] if original is not None else result["book_id"]
            )
            result["effective_category_id"] = (
                original["category_id"] if original is not None else result["category_id"]
            )
            if result["kind"] == "expense":
                refunded = self._refunded(connection, transaction_id)
                result["active_refunded_minor"] = refunded
                result["remaining_refundable_minor"] = cast(int, result["amount_minor"]) - refunded
            return result

    def ensure_defaults(self) -> None:
        """Seed deterministic, replayable empty-book metadata; invent no account balances."""
        items: list[tuple[str, dict[str, object]]] = [
            ("book.create.v1", {"id": DEFAULT_BOOK_ID, "name": "我的账本"})
        ]
        for kind, names in [
            ("income", ["工资", "其他收入"]),
            ("expense", ["餐饮", "交通", "学习", "其他支出"]),
        ]:
            for name in names:
                identifier = str(uuid5(NAMESPACE_URL, f"openledger:category:{kind}:{name}"))
                items.append(("category.create.v1", {"id": identifier, "name": name, "kind": kind}))
        for code, name in [
            ("cash", "现金"),
            ("bank", "银行卡"),
            ("wechat", "微信"),
            ("alipay", "支付宝"),
        ]:
            items.append(
                (
                    "payment_method.create.v1",
                    {
                        "id": str(uuid5(NAMESPACE_URL, f"openledger:payment:{code}")),
                        "code": code,
                        "name": name,
                    },
                )
            )
        for command, payload in items:
            request = str(uuid5(NAMESPACE_URL, f"openledger:seed-v1:{payload['id']}"))
            self.execute(request, command, payload)

    def _dispatch(self, work: _Work, command: str, payload: dict[str, object]) -> dict[str, object]:
        parts = command.split(".")
        if len(parts) != 3 or parts[2] != "v1":
            # opening.set and metadata.update have dedicated longer command names.
            if command == "account.opening.set.v1":
                return self._set_opening(work, payload)
            if command == "adjustment.metadata.update.v1":
                return self._adjustment_metadata(work, payload)
            if command in {"book.default.set.v1", "account.default.set.v1"}:
                return self._default(work, command.split(".")[0], payload)
            raise LedgerError("INVALID_ENVELOPE")
        entity, action, _ = parts
        if entity == "import":
            if action == "commit":
                return self._import_commit(work, payload)
            if action == "revert":
                return self._import_revert(work, payload)
        if entity in {"book", "account", "category", "tag", "payment_method"}:
            if action == "adjust" and entity == "account":
                return self._adjust(work, payload)
            if action in {"create", "update", "archive"}:
                return self._manage(work, entity, action, payload)
            if action == "default" and entity in {"book", "account"}:
                return self._default(work, entity, payload)
        if entity in {"transaction", "refund", "transfer"} and action in {"record", "update"}:
            return self._financial(work, entity, action, payload)
        if entity == "transaction" and action in {"delete", "restore"}:
            return self._delete_restore(work, action, payload)
        raise LedgerError("INVALID_ENVELOPE")

    @staticmethod
    def _version(
        row: sqlite3.Row, payload: Mapping[str, object], key: str = "expected_version"
    ) -> None:
        if _integer(payload, key) != row["version"]:
            raise LedgerError("VERSION_CONFLICT")

    @staticmethod
    def _reference(
        work: _Work, entity: str, identifier: str, retained: set[str] | None = None
    ) -> sqlite3.Row:
        row = _row(work.connection, entity, identifier)
        if row["is_archived"] and identifier not in (retained or set()):
            raise LedgerError("ENTITY_ARCHIVED")
        return row

    @staticmethod
    def _update_row(
        work: _Work, entity: str, identifier: str, values: Mapping[str, object]
    ) -> None:
        assignments = ",".join(f"{key}=?" for key in values)
        work.connection.execute(
            f"UPDATE {_TABLES[entity]} SET {assignments},"
            "version=version+1,updated_at_utc=? WHERE id=?",
            (*values.values(), work.timestamp, identifier),
        )

    def _manage(
        self, work: _Work, entity: str, action: str, payload: dict[str, object]
    ) -> dict[str, object]:
        identifier = _id(payload)
        connection = work.connection
        before = None if action == "create" else _snapshot(connection, entity, identifier)
        if action != "create":
            self._version(_row(connection, entity, identifier), payload)
        if action == "archive":
            _keys(payload, {"id", "expected_version", "archived", "replacement_default_id"})
            archived = payload.get("archived")
            if type(archived) is not bool:
                raise LedgerError("INVALID_ENVELOPE")
            if entity in {"book", "account"} and archived:
                field = "default_" + entity + "_id"
                preferences = connection.execute(
                    "SELECT * FROM app_preferences WHERE singleton=1"
                ).fetchone()
                if preferences[field] == identifier:
                    replacement = _id(payload, "replacement_default_id")
                    if replacement == identifier:
                        raise LedgerError("ENTITY_ARCHIVED")
                    self._reference(work, entity, replacement)
                    connection.execute(
                        f"UPDATE app_preferences SET {field}=? WHERE singleton=1", (replacement,)
                    )
            if (
                entity == "category"
                and archived
                and connection.execute(
                    "SELECT 1 FROM categories WHERE parent_id=? AND is_archived=0", (identifier,)
                ).fetchone()
            ):
                raise LedgerError("INVALID_CATEGORY_TREE")
            if not archived and before is not None:
                self._unique_name(connection, entity, identifier, before)
                if entity == "category" and before["parent_id"] is not None:
                    self._reference(work, "category", cast(str, before["parent_id"]))
            self._update_row(work, entity, identifier, {"is_archived": int(archived)})
            work.journal(entity, identifier, "archive" if archived else "unarchive", before)
            return {"id": identifier}
        common = {"id", "name", "sort_order"}
        if entity in {"book", "account"}:
            common |= {"description", "currency_code"}
        extra = {
            "book": set(),
            "account": {"account_type", "balance_start_on", "opening_balance_minor"},
            "category": {"kind", "parent_id", "color"},
            "tag": {"color"},
            "payment_method": {"code", "default_account_id"},
        }[entity]
        _keys(payload, common | extra | ({"expected_version"} if action == "update" else set()))
        if payload.get("currency_code", "CNY") != "CNY":
            raise LedgerError("CURRENCY_MISMATCH")
        name_value = payload.get("name")
        if not isinstance(name_value, str):
            raise LedgerError("MISSING_REQUIRED_FIELD")
        name = normalize_text(name_value, max_length=80, required=True)
        order = _integer(payload, "sort_order", 0)
        if order < 0:
            raise LedgerError("INVALID_ENVELOPE")
        values: dict[str, object] = {"name": name, "sort_order": order}
        if entity in {"book", "account"}:
            values["description"] = self._text(payload, "description", 1000) or ""
        if entity == "account":
            account_type = payload.get("account_type")
            if not isinstance(account_type, str) or account_type not in {
                "cash",
                "bank",
                "wechat",
                "alipay",
                "custom",
            }:
                raise LedgerError("INVALID_ENVELOPE")
            values["account_type"] = account_type
            if action == "create":
                if "opening_balance_minor" not in payload:
                    raise LedgerError("MISSING_REQUIRED_FIELD")
                start = _day(payload.get("balance_start_on"))
                validate_occurrence(start, self.time_zone, now=work.now)
                values["balance_start_on"] = start.isoformat()
                validate_minor(payload["opening_balance_minor"], signed=True, allow_zero=True)
            elif "balance_start_on" in payload or "opening_balance_minor" in payload:
                raise LedgerError("OPENING_REQUIRES_DEDICATED_COMMAND")
        if entity == "category":
            kind = payload.get("kind")
            if not isinstance(kind, str) or kind not in {"income", "expense"}:
                raise LedgerError("INVALID_ENVELOPE")
            if before is not None and before["transaction_kind"] != kind:
                if connection.execute(
                    "SELECT 1 FROM transactions WHERE category_id=?", (identifier,)
                ).fetchone():
                    raise LedgerError("TRANSACTION_KIND_IMMUTABLE")
                if connection.execute(
                    "SELECT 1 FROM categories WHERE parent_id=?", (identifier,)
                ).fetchone():
                    raise LedgerError("INVALID_CATEGORY_TREE")
            parent_id = payload.get("parent_id")
            if parent_id is not None:
                parent = self._reference(work, "category", _id(payload, "parent_id"))
                if (
                    parent_id == identifier
                    or parent["parent_id"] is not None
                    or parent["transaction_kind"] != kind
                ):
                    raise LedgerError("INVALID_CATEGORY_TREE")
                if connection.execute(
                    "SELECT 1 FROM categories WHERE parent_id=?", (identifier,)
                ).fetchone():
                    raise LedgerError("INVALID_CATEGORY_TREE")
            values.update(transaction_kind=kind, parent_id=parent_id)
        if entity in {"tag", "category"}:
            color = payload.get("color")
            if color is not None:
                import re

                if not isinstance(color, str) or re.fullmatch(r"#[0-9a-fA-F]{6}", color) is None:
                    raise LedgerError("INVALID_ENVELOPE")
            values["color"] = color
        if entity == "payment_method":
            if action == "create":
                code = self._text(payload, "code", 80, required=True)
                values["code"] = code
                if connection.execute(
                    "SELECT 1 FROM payment_methods WHERE code=?", (code,)
                ).fetchone():
                    raise LedgerError("NAME_CONFLICT")
            elif "code" in payload:
                raise LedgerError("FIELD_CONFLICT")
            account_id = payload.get("default_account_id")
            if account_id is not None:
                self._reference(work, "account", _id(payload, "default_account_id"))
            values["default_account_id"] = account_id
        if before is None or not before["is_archived"]:
            self._unique_name(connection, entity, identifier, values)
        if action == "create":
            if connection.execute(
                f"SELECT 1 FROM {_TABLES[entity]} WHERE id=?", (identifier,)
            ).fetchone():
                raise LedgerError("FIELD_CONFLICT")
            columns = ["id", *values, "created_at_utc", "updated_at_utc"]
            connection.execute(
                f"INSERT INTO {_TABLES[entity]}({','.join(columns)}) "
                f"VALUES({','.join('?' for _ in columns)})",
                (identifier, *values.values(), work.timestamp, work.timestamp),
            )
            if entity in {"book", "account"}:
                field = "default_" + entity + "_id"
                connection.execute(
                    f"UPDATE app_preferences SET {field}=? WHERE singleton=1 AND {field} IS NULL",
                    (identifier,),
                )
            if entity == "account":
                self._opening(
                    work,
                    identifier,
                    cast(str, values["balance_start_on"]),
                    cast(int, payload.get("opening_balance_minor", 0)),
                )
        else:
            self._update_row(work, entity, identifier, values)
        work.journal(entity, identifier, "create" if action == "create" else "update", before)
        return {"id": identifier}

    @staticmethod
    def _unique_name(
        connection: sqlite3.Connection, entity: str, identifier: str, values: Mapping[str, object]
    ) -> None:
        condition = "name=? COLLATE NOCASE AND id<>? AND is_archived=0"
        args: list[object] = [values["name"], identifier]
        if entity == "category":
            condition += " AND transaction_kind=? AND parent_id IS ?"
            args.extend((values["transaction_kind"], values.get("parent_id")))
        if connection.execute(
            f"SELECT 1 FROM {_TABLES[entity]} WHERE {condition}", args
        ).fetchone():
            raise LedgerError("NAME_CONFLICT")

    def _default(self, work: _Work, entity: str, payload: dict[str, object]) -> dict[str, object]:
        _keys(payload, {"id", "expected_version"})
        identifier = _id(payload)
        row = self._reference(work, entity, identifier)
        self._version(row, payload)
        before = _snapshot(work.connection, entity, identifier)
        work.connection.execute(
            f"UPDATE app_preferences SET default_{entity}_id=? WHERE singleton=1", (identifier,)
        )
        self._update_row(work, entity, identifier, {"name": row["name"]})
        work.journal(entity, identifier, "update", before)
        return {"id": identifier}

    @staticmethod
    def _text(
        payload: Mapping[str, object], key: str, limit: int, *, required: bool = False
    ) -> str | None:
        value = payload.get(key)
        if value is not None and not isinstance(value, str):
            raise LedgerError("INVALID_ENVELOPE")
        return normalize_text(value, max_length=limit, required=required)

    def _fields(
        self, work: _Work, fields: dict[str, object], kind: str, previous: dict[str, object] | None
    ) -> tuple[dict[str, object], list[tuple[str, int]], tuple[str, ...]]:
        common = {
            "amount_minor",
            "currency_code",
            "occurred_on",
            "occurred_at_utc",
            "time_zone",
            "occurrence_precision",
            "time_period",
            "note",
            "tag_ids",
            "source",
            "source_text",
        }
        extra = {
            "income": {
                "kind",
                "account_id",
                "book_id",
                "category_id",
                "payment_method_id",
                "counterparty",
                "merchant",
                "location",
            },
            "expense": {
                "kind",
                "account_id",
                "book_id",
                "category_id",
                "payment_method_id",
                "counterparty",
                "merchant",
                "location",
            },
            "expense_refund": {"original_transaction_id", "account_id", "payment_method_id"},
            "transfer": {"from_account_id", "to_account_id"},
        }[kind]
        _keys(fields, common | extra)
        amount = validate_minor(fields.get("amount_minor"))
        if fields.get("currency_code", "CNY") != "CNY":
            raise LedgerError("CURRENCY_MISMATCH")
        source = fields.get("source", "manual")
        if not isinstance(source, str) or source not in _SOURCES:
            raise LedgerError("INVALID_ENVELOPE")
        if previous is not None and (
            source != previous["source"]
            or self._text(fields, "source_text", 4000) != previous["source_text"]
        ):
            raise LedgerError("FIELD_CONFLICT")
        occurred_on = _day(fields.get("occurred_on"))
        zone = fields.get("time_zone", "UTC")
        precision = fields.get("occurrence_precision", "date")
        period = fields.get("time_period")
        instant = fields.get("occurred_at_utc")
        if (
            not isinstance(zone, str)
            or not isinstance(precision, str)
            or (period is not None and not isinstance(period, str))
            or (instant is not None and not isinstance(instant, datetime))
        ):
            raise LedgerError("INVALID_ENVELOPE")
        validate_occurrence(
            occurred_on,
            zone,
            precision=precision,
            time_period=period,
            occurred_at_utc=instant,
            now=work.now,
        )
        values: dict[str, object] = {
            "kind": kind,
            "amount_minor": amount,
            "currency_code": "CNY",
            "source": source,
            "occurred_on": occurred_on.isoformat(),
            "time_zone": zone,
            "occurrence_precision": precision,
            "time_period": period,
            "occurred_at_utc": None if instant is None else utc_text(instant),
            "note": self._text(fields, "note", 4000) or "",
            "source_text": self._text(fields, "source_text", 4000),
            "book_id": None,
            "category_id": None,
            "payment_method_id": None,
            "counterparty": None,
            "merchant": None,
            "location": None,
            "original_transaction_id": None,
        }
        retained: dict[str, set[str]] = {
            entity: set() for entity in ["account", "book", "category", "payment_method", "tag"]
        }
        if previous:
            for entity in ["book", "category", "payment_method"]:
                if previous[entity + "_id"]:
                    retained[entity].add(cast(str, previous[entity + "_id"]))
            retained["account"].update(
                cast(str, entry["account_id"])
                for entry in cast(list[dict[str, object]], previous["entries"])
            )
            retained["tag"].update(cast(list[str], previous["tag_ids"]))
        if kind == "transfer":
            first, second = _id(fields, "from_account_id"), _id(fields, "to_account_id")
            if first == second:
                raise LedgerError("INVALID_TRANSFER")
            entries = [(first, -amount), (second, amount)]
        else:
            account = _id(fields, "account_id")
            entries = [(account, -amount if kind == "expense" else amount)]
            channel = fields.get("payment_method_id")
            if channel is not None:
                self._reference(
                    work,
                    "payment_method",
                    _id(fields, "payment_method_id"),
                    retained["payment_method"],
                )
                values["payment_method_id"] = channel
        for account_id, _ in entries:
            account_row = self._reference(work, "account", account_id, retained["account"])
            if occurred_on < date.fromisoformat(account_row["balance_start_on"]):
                raise LedgerError("DATE_BEFORE_BALANCE_START")
            work.account(account_id)
        if kind in {"income", "expense"}:
            book_id, category_id = _id(fields, "book_id"), _id(fields, "category_id")
            self._reference(work, "book", book_id, retained["book"])
            category = self._reference(work, "category", category_id, retained["category"])
            if category["transaction_kind"] != kind:
                raise LedgerError("FIELD_CONFLICT")
            values.update(book_id=book_id, category_id=category_id)
            for key in ["counterparty", "merchant", "location"]:
                values[key] = self._text(fields, key, 200)
            if previous is not None and kind == "expense":
                self._expense_dependencies(work, previous, values)
        if kind == "expense_refund":
            original_id = _id(fields, "original_transaction_id")
            if previous is not None and original_id != previous["original_transaction_id"]:
                raise LedgerError("FIELD_CONFLICT")
            self._validate_refund(
                work,
                original_id,
                amount,
                occurred_on,
                cast(str, previous["id"]) if previous else None,
            )
            values["original_transaction_id"] = original_id
        tag_ids = cast(tuple[str, ...], fields.get("tag_ids", ()))
        for tag in tag_ids:
            self._reference(work, "tag", normalize_id(tag), retained["tag"])
        return values, entries, tag_ids

    @staticmethod
    def _refunded(
        connection: sqlite3.Connection, original_id: str, excluded: str | None = None
    ) -> int:
        return sum(
            int(row[0])
            for row in connection.execute(
                "SELECT amount_minor FROM transactions WHERE original_transaction_id=? "
                "AND deleted_at_utc IS NULL AND id<>?",
                (original_id, excluded or ""),
            )
        )

    def _validate_refund(
        self, work: _Work, original_id: str, amount: int, occurred_on: date, excluded: str | None
    ) -> None:
        original = _row(work.connection, "transaction", original_id)
        if original["kind"] != "expense":
            raise LedgerError("ORIGINAL_EXPENSE_REQUIRED")
        if original["deleted_at_utc"] is not None:
            raise LedgerError("ORIGINAL_EXPENSE_UNAVAILABLE")
        if occurred_on < date.fromisoformat(original["occurred_on"]):
            raise LedgerError("REFUND_DATE_BEFORE_EXPENSE")
        if (
            self._refunded(work.connection, original_id, excluded) + amount
            > original["amount_minor"]
        ):
            raise LedgerError("REFUND_LIMIT_EXCEEDED")

    def _expense_dependencies(
        self, work: _Work, before: dict[str, object], values: dict[str, object]
    ) -> None:
        refunds = list(
            work.connection.execute(
                "SELECT amount_minor,occurred_on FROM transactions "
                "WHERE original_transaction_id=? AND deleted_at_utc IS NULL",
                (before["id"],),
            )
        )
        if not refunds:
            return
        if values["book_id"] != before["book_id"]:
            raise LedgerError("ACTIVE_REFUNDS_BLOCK_OPERATION")
        if sum(row["amount_minor"] for row in refunds) > cast(int, values["amount_minor"]):
            raise LedgerError("REFUND_LIMIT_EXCEEDED")
        if any(row["occurred_on"] < cast(str, values["occurred_on"]) for row in refunds):
            raise LedgerError("REFUND_DATE_BEFORE_EXPENSE")

    def _financial(
        self,
        work: _Work,
        entity: str,
        action: str,
        payload: dict[str, object],
        *,
        import_metadata: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        _keys(payload, {"id", "fields"} | ({"expected_version"} if action == "update" else set()))
        identifier = _id(payload)
        fields = payload.get("fields")
        if not isinstance(fields, dict):
            raise LedgerError("INVALID_ENVELOPE")
        previous = None
        if action == "update":
            row = _row(work.connection, "transaction", identifier)
            if row["kind"] == "opening":
                raise LedgerError("OPENING_REQUIRES_DEDICATED_COMMAND")
            if row["kind"] == "adjustment":
                raise LedgerError("ADJUSTMENT_FINANCIAL_FIELDS_IMMUTABLE")
            self._version(row, payload)
            if row["deleted_at_utc"] is not None:
                raise LedgerError("ENTITY_DELETED")
            previous = _snapshot(work.connection, "transaction", identifier)
            for entry in cast(list[dict[str, object]], previous["entries"]):
                work.account(cast(str, entry["account_id"]))
        kind = {"refund": "expense_refund", "transfer": "transfer"}.get(entity, fields.get("kind"))
        if entity == "transaction" and kind not in ("income", "expense"):
            raise LedgerError("TRANSACTION_KIND_IMMUTABLE")
        if not isinstance(kind, str) or kind not in {
            "income",
            "expense",
            "expense_refund",
            "transfer",
        }:
            raise LedgerError("TRANSACTION_KIND_IMMUTABLE")
        if previous is not None and previous["kind"] != kind:
            raise LedgerError("TRANSACTION_KIND_IMMUTABLE")
        values, entries, tags = self._fields(work, cast(dict[str, object], fields), kind, previous)
        if import_metadata is not None:
            values.update(import_metadata)
        self._save_transaction(work, identifier, values, entries, tags, previous)
        return {"id": identifier}

    def _save_transaction(
        self,
        work: _Work,
        identifier: str,
        values: dict[str, object],
        entries: list[tuple[str, int]],
        tags: tuple[str, ...],
        before: dict[str, object] | None,
    ) -> None:
        connection = work.connection
        if before is None:
            if connection.execute(
                "SELECT 1 FROM transactions WHERE id=?", (identifier,)
            ).fetchone():
                raise LedgerError("FIELD_CONFLICT")
            columns = ["id", *values, "created_at_utc", "updated_at_utc"]
            connection.execute(
                f"INSERT INTO transactions({','.join(columns)}) "
                f"VALUES({','.join('?' for _ in columns)})",
                (identifier, *values.values(), work.timestamp, work.timestamp),
            )
        else:
            self._update_row(work, "transaction", identifier, values)
            connection.execute("DELETE FROM account_entries WHERE transaction_id=?", (identifier,))
            connection.execute("DELETE FROM transaction_tags WHERE transaction_id=?", (identifier,))
        work.fault("after_transaction")
        for account_id, delta in entries:
            work.account(account_id)
            connection.execute(
                "INSERT INTO account_entries(id,transaction_id,account_id,delta_minor) "
                "VALUES(?,?,?,?)",
                (str(uuid4()), identifier, account_id, delta),
            )
            work.fault("after_entry")
        for tag in tags:
            connection.execute(
                "INSERT INTO transaction_tags(transaction_id,tag_id) VALUES(?,?)", (identifier, tag)
            )
        work.fault("after_entries")
        work.journal("transaction", identifier, "create" if before is None else "update", before)

    def _delete_restore(
        self, work: _Work, action: str, payload: dict[str, object]
    ) -> dict[str, object]:
        _keys(payload, {"id", "expected_version"})
        identifier = _id(payload)
        row = _row(work.connection, "transaction", identifier)
        self._version(row, payload)
        if row["kind"] == "opening":
            raise LedgerError("OPENING_REQUIRES_DEDICATED_COMMAND")
        deleted = row["deleted_at_utc"] is not None
        if deleted == (action == "delete"):
            raise LedgerError("ENTITY_DELETED" if deleted else "FIELD_CONFLICT")
        before = _snapshot(work.connection, "transaction", identifier)
        entries = cast(list[dict[str, object]], before["entries"])
        for entry in entries:
            work.account(cast(str, entry["account_id"]))
        if (
            action == "delete"
            and row["kind"] == "expense"
            and self._refunded(work.connection, identifier)
        ):
            raise LedgerError("ACTIVE_REFUNDS_BLOCK_OPERATION")
        if action == "restore":
            if (
                row["import_batch_id"] is not None
                and _row(work.connection, "import_batch", row["import_batch_id"])["status"]
                == "reverted"
            ):
                raise LedgerError("IMPORT_BATCH_UNAVAILABLE")
            occurred_on = date.fromisoformat(row["occurred_on"])
            instant = (
                None
                if row["occurred_at_utc"] is None
                else datetime.fromisoformat(row["occurred_at_utc"].replace("Z", "+00:00"))
            )
            validate_occurrence(
                occurred_on,
                row["time_zone"],
                precision=row["occurrence_precision"],
                time_period=row["time_period"],
                occurred_at_utc=instant,
                now=work.now,
            )
            for entry in entries:
                account = _row(work.connection, "account", cast(str, entry["account_id"]))
                if occurred_on < date.fromisoformat(account["balance_start_on"]):
                    raise LedgerError("DATE_BEFORE_BALANCE_START")
            if row["kind"] == "expense_refund":
                self._validate_refund(
                    work,
                    row["original_transaction_id"],
                    row["amount_minor"],
                    occurred_on,
                    identifier,
                )
        self._update_row(
            work,
            "transaction",
            identifier,
            {"deleted_at_utc": work.timestamp if action == "delete" else None},
        )
        work.journal(
            "transaction", identifier, "soft_delete" if action == "delete" else "restore", before
        )
        return {"id": identifier}

    def _import_commit(self, work: _Work, payload: dict[str, object]) -> dict[str, object]:
        """Commit all confirmed rows, metadata and one receipt as one financial command."""
        _keys(
            payload,
            {
                "id",
                "source_format",
                "source_file_name",
                "file_digest",
                "mapping_hash",
                "mapping_json",
                "format_version",
                "source_row_count",
                "rows",
            },
        )
        identifier = _id(payload)
        source_format = payload.get("source_format")
        if not isinstance(source_format, str) or source_format not in {"csv", "xlsx"}:
            raise LedgerError("INVALID_ENVELOPE")
        source_name = self._text(payload, "source_file_name", 255, required=True)
        if source_name is None or any(character in source_name for character in "/\\:\x00"):
            raise LedgerError("INVALID_ENVELOPE")
        digests: dict[str, str] = {}
        for field in ("file_digest", "mapping_hash"):
            value = payload.get(field)
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise LedgerError("INVALID_ENVELOPE")
            digests[field] = value
        mapping_json = payload.get("mapping_json")
        if not isinstance(mapping_json, str):
            raise LedgerError("INVALID_ENVELOPE")
        try:
            mapping = json.loads(mapping_json)
            canonical_mapping = json.dumps(
                mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        except (ValueError, TypeError) as error:
            raise LedgerError("INVALID_ENVELOPE") from error
        if (
            not isinstance(mapping, dict)
            or canonical_mapping != mapping_json
            or hashlib.sha256(mapping_json.encode("utf-8")).hexdigest() != digests["mapping_hash"]
        ):
            raise LedgerError("INVALID_ENVELOPE")
        format_version = _integer(payload, "format_version", 1)
        source_count = _integer(payload, "source_row_count")
        rows = payload.get("rows")
        if (
            format_version != 1
            or not 1 <= source_count <= MAX_INT64
            or not isinstance(rows, (tuple, list))
            or not 1 <= len(rows) <= min(source_count, 10_000)
        ):
            raise LedgerError("INVALID_ENVELOPE")
        connection = work.connection
        if connection.execute("SELECT 1 FROM import_batches WHERE id=?", (identifier,)).fetchone():
            raise LedgerError("DUPLICATE_IMPORT")
        prepared: list[tuple[int, str, str, dict[str, object], dict[str, object]]] = []
        seen_ids: set[str] = set()
        seen_rows: set[int] = set()
        seen_external: set[tuple[str, str]] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise LedgerError("INVALID_ENVELOPE")
            _keys(
                row,
                {"id", "source_row_number", "fields", "external_source", "external_transaction_id"},
            )
            transaction_id = _id(row)
            source_row = _integer(row, "source_row_number")
            if not 1 <= source_row <= MAX_INT64:
                raise LedgerError("INVALID_ENVELOPE")
            if transaction_id in seen_ids or source_row in seen_rows:
                raise LedgerError("DUPLICATE_IMPORT")
            seen_ids.add(transaction_id)
            seen_rows.add(source_row)
            fields = row.get("fields")
            if not isinstance(fields, dict):
                raise LedgerError("INVALID_ENVELOPE")
            fields = dict(fields)
            kind = fields.get("kind")
            if not isinstance(kind, str) or kind not in {
                "income",
                "expense",
                "transfer",
                "expense_refund",
            }:
                raise LedgerError("UNSUPPORTED_IMPORT_KIND")
            entity = {"transfer": "transfer", "expense_refund": "refund"}.get(kind, "transaction")
            if entity != "transaction":
                fields.pop("kind")
            fields["source"] = "import"
            external_source = self._text(row, "external_source", 160)
            external_id = self._text(row, "external_transaction_id", 255)
            if (external_source is None) != (external_id is None):
                raise LedgerError("INVALID_ENVELOPE")
            if external_source is not None and external_id is not None:
                external_key = (external_source, external_id)
                if (
                    external_key in seen_external
                    or connection.execute(
                        "SELECT 1 FROM transactions WHERE external_source=? "
                        "AND external_transaction_id=?",
                        external_key,
                    ).fetchone()
                ):
                    raise LedgerError("DUPLICATE_IMPORT")
                seen_external.add(external_key)
            if connection.execute(
                "SELECT 1 FROM transactions WHERE id=? OR (import_file_digest=? "
                "AND import_mapping_hash=? AND import_source_row=?)",
                (transaction_id, digests["file_digest"], digests["mapping_hash"], source_row),
            ).fetchone():
                raise LedgerError("DUPLICATE_IMPORT")
            metadata: dict[str, object] = {
                "import_batch_id": identifier,
                "import_file_digest": digests["file_digest"],
                "import_mapping_hash": digests["mapping_hash"],
                "import_source_row": source_row,
                "external_source": external_source,
                "external_transaction_id": external_id,
            }
            prepared.append((source_row, transaction_id, entity, fields, metadata))
        connection.execute(
            "INSERT INTO import_batches(id,source_format,source_file_name,file_digest,mapping_hash,"
            "mapping_json,format_version,status,source_row_count,accepted_row_count,"
            "created_at_utc,updated_at_utc) VALUES(?,?,?,?,?,?,?,'committed',?,?,?,?)",
            (
                identifier,
                source_format,
                source_name,
                digests["file_digest"],
                digests["mapping_hash"],
                mapping_json,
                format_version,
                source_count,
                len(prepared),
                work.timestamp,
                work.timestamp,
            ),
        )
        work.fault("after_import_batch")
        # Refunds may reference an expense appearing later in the source file.
        for source_row, transaction_id, entity, fields, metadata in sorted(
            prepared, key=lambda item: item[2] == "refund"
        ):
            try:
                self._financial(
                    work,
                    entity,
                    "record",
                    {"id": transaction_id, "fields": fields},
                    import_metadata=metadata,
                )
            except LedgerError as error:
                raise LedgerError(
                    error.code, f"source_row_number={source_row}; {error.code}"
                ) from error
            work.fault("after_import_row")
        work.journal("import_batch", identifier, "create", None)
        return {"id": identifier, "accepted_row_count": len(prepared)}

    def _import_revert(self, work: _Work, payload: dict[str, object]) -> dict[str, object]:
        """Reverse an untouched batch together; permanent identity survives soft deletion."""
        _keys(payload, {"id", "expected_version"})
        identifier = _id(payload)
        connection = work.connection
        batch = _row(connection, "import_batch", identifier)
        self._version(batch, payload)
        if batch["status"] != "committed":
            raise LedgerError("IMPORT_BATCH_UNAVAILABLE")
        members = connection.execute(
            "SELECT id,kind,version,deleted_at_utc FROM transactions WHERE import_batch_id=?",
            (identifier,),
        ).fetchall()
        if len(members) != batch["accepted_row_count"]:
            raise LedgerError("INTEGRITY_FAILED")
        if any(row["version"] != 1 or row["deleted_at_utc"] is not None for row in members):
            raise LedgerError("IMPORT_BATCH_CHANGED")
        if connection.execute(
            "SELECT 1 FROM transactions refund JOIN transactions expense "
            "ON expense.id=refund.original_transaction_id WHERE expense.import_batch_id=? "
            "AND refund.deleted_at_utc IS NULL "
            "AND (refund.import_batch_id IS NULL OR refund.import_batch_id<>?)",
            (identifier, identifier),
        ).fetchone():
            raise LedgerError("ACTIVE_REFUNDS_BLOCK_OPERATION")
        before = _snapshot(connection, "import_batch", identifier)
        for row in sorted(members, key=lambda item: item["kind"] != "expense_refund"):
            self._delete_restore(work, "delete", {"id": row["id"], "expected_version": 1})
            work.fault("after_import_revert_row")
        self._update_row(
            work,
            "import_batch",
            identifier,
            {"status": "reverted", "reverted_at_utc": work.timestamp},
        )
        work.journal("import_batch", identifier, "update", before)
        return {"id": identifier, "reverted_row_count": len(members)}

    def _opening(self, work: _Work, account_id: str, start: str, amount: int) -> None:
        amount = validate_minor(amount, signed=True, allow_zero=True)
        work.account(account_id)
        active = work.connection.execute(
            "SELECT t.id FROM transactions t JOIN account_entries e ON e.transaction_id=t.id "
            "WHERE e.account_id=? AND t.kind='opening' AND t.deleted_at_utc IS NULL",
            (account_id,),
        ).fetchall()
        if len(active) > 1:
            raise LedgerError("INTEGRITY_FAILED")
        if active and amount == 0:
            identifier = active[0][0]
            before = _snapshot(work.connection, "transaction", identifier)
            self._update_row(work, "transaction", identifier, {"deleted_at_utc": work.timestamp})
            work.journal("transaction", identifier, "soft_delete", before)
        elif amount:
            identifier = active[0][0] if active else str(uuid4())
            previous = _snapshot(work.connection, "transaction", identifier) if active else None
            values: dict[str, object] = {
                "kind": "opening",
                "amount_minor": abs(amount),
                "source": "system",
                "occurred_on": start,
                "time_zone": self.time_zone,
            }
            self._save_transaction(work, identifier, values, [(account_id, amount)], (), previous)

    def _set_opening(self, work: _Work, payload: dict[str, object]) -> dict[str, object]:
        _keys(
            payload,
            {"account_id", "expected_account_version", "balance_start_on", "opening_balance_minor"},
        )
        identifier = _id(payload, "account_id")
        row = self._reference(work, "account", identifier)
        self._version(row, payload, "expected_account_version")
        start = _day(payload.get("balance_start_on"))
        validate_occurrence(start, self.time_zone, now=work.now)
        amount = validate_minor(payload.get("opening_balance_minor"), signed=True, allow_zero=True)
        if work.connection.execute(
            "SELECT 1 FROM transactions t JOIN account_entries e ON e.transaction_id=t.id "
            "WHERE e.account_id=? AND t.kind<>'opening' AND t.deleted_at_utc IS NULL "
            "AND t.occurred_on<?",
            (identifier, start.isoformat()),
        ).fetchone():
            raise LedgerError("BALANCE_START_CONFLICT")
        before = _snapshot(work.connection, "account", identifier)
        self._opening(work, identifier, start.isoformat(), amount)
        self._update_row(work, "account", identifier, {"balance_start_on": start.isoformat()})
        work.journal("account", identifier, "update", before)
        return {"id": identifier}

    def _adjust(self, work: _Work, payload: dict[str, object]) -> dict[str, object]:
        _keys(payload, {"account_id", "target_balance_minor", "occurred_on", "reason", "time_zone"})
        identifier = _id(payload, "account_id")
        account = self._reference(work, "account", identifier)
        target = payload.get("target_balance_minor")
        if type(target) is not int:
            raise LedgerError("INVALID_AMOUNT")
        target = checked_aggregate(target)
        occurred_on = _day(payload.get("occurred_on"))
        zone = payload.get("time_zone", "UTC")
        if not isinstance(zone, str):
            raise LedgerError("INVALID_TIMEZONE")
        validate_occurrence(occurred_on, zone, now=work.now)
        if occurred_on != work.now.astimezone(ZoneInfo(zone)).date():
            raise LedgerError("ADJUSTMENT_DATE_MUST_BE_TODAY")
        if occurred_on < date.fromisoformat(account["balance_start_on"]):
            raise LedgerError("DATE_BEFORE_BALANCE_START")
        reason = self._text(payload, "reason", 1000, required=True)
        work.account(identifier)
        current = _balance(work.connection, identifier)
        delta = validate_minor(target - current, signed=True, allow_zero=True)
        if delta == 0:
            return {"reason": "BALANCE_ALREADY_MATCHES", "account_id": identifier}
        transaction_id = str(uuid4())
        values: dict[str, object] = {
            "kind": "adjustment",
            "amount_minor": abs(delta),
            "source": "system",
            "occurred_on": occurred_on.isoformat(),
            "time_zone": zone,
            "adjustment_reason": reason,
            "balance_before_minor": current,
            "balance_target_minor": target,
        }
        self._save_transaction(work, transaction_id, values, [(identifier, delta)], (), None)
        return {"id": transaction_id}

    def _adjustment_metadata(self, work: _Work, payload: dict[str, object]) -> dict[str, object]:
        _keys(payload, {"id", "expected_version", "note", "reason", "tag_ids"})
        identifier = _id(payload)
        row = _row(work.connection, "transaction", identifier)
        self._version(row, payload)
        if row["kind"] != "adjustment":
            raise LedgerError("ADJUSTMENT_FINANCIAL_FIELDS_IMMUTABLE")
        if row["deleted_at_utc"] is not None:
            raise LedgerError("ENTITY_DELETED")
        before = _snapshot(work.connection, "transaction", identifier)
        tags = cast(tuple[str, ...], payload.get("tag_ids", ()))
        for tag in tags:
            self._reference(work, "tag", normalize_id(tag), set(cast(list[str], before["tag_ids"])))
        self._update_row(
            work,
            "transaction",
            identifier,
            {
                "note": self._text(payload, "note", 4000) or "",
                "adjustment_reason": self._text(payload, "reason", 1000, required=True),
            },
        )
        work.connection.execute(
            "DELETE FROM transaction_tags WHERE transaction_id=?", (identifier,)
        )
        for tag in tags:
            work.connection.execute(
                "INSERT INTO transaction_tags(transaction_id,tag_id) VALUES(?,?)", (identifier, tag)
            )
        work.journal("transaction", identifier, "update", before)
        return {"id": identifier}
