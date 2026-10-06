"""Read-only SQLite composition for desktop transaction lists and dashboards."""

from __future__ import annotations

import calendar
import sqlite3
import unicodedata
from collections.abc import Mapping
from datetime import date
from typing import cast

from openledger.application.dto.queries import Overview, TransactionFilter, TransactionPage
from openledger.domain.errors import LedgerError
from openledger.domain.money import checked_aggregate
from openledger.domain.values import normalize_id
from openledger.infrastructure.currencies import Valuation, display_currency
from openledger.infrastructure.database.database import Database

_KINDS = {"income", "expense", "expense_refund", "transfer", "opening", "adjustment"}
_EFFECTIVE_BOOK = "CASE WHEN t.kind='expense_refund' THEN original.book_id ELSE t.book_id END"
_EFFECTIVE_CATEGORY = (
    "CASE WHEN t.kind='expense_refund' THEN original.category_id ELSE t.category_id END"
)
_JOINS = (
    "FROM transactions t "
    "LEFT JOIN transactions original ON original.id=t.original_transaction_id "
    f"LEFT JOIN books book ON book.id=({_EFFECTIVE_BOOK}) "
    f"LEFT JOIN categories category ON category.id=({_EFFECTIVE_CATEGORY}) "
    "LEFT JOIN payment_methods payment ON payment.id=t.payment_method_id "
)


def _change_seq(connection: sqlite3.Connection) -> int:
    return int(connection.execute("SELECT COALESCE(MAX(seq),0) FROM change_log").fetchone()[0])


def _validate(filters: TransactionFilter) -> None:
    """Reject malformed runtime callers before opening a database connection."""
    if not isinstance(filters, TransactionFilter):
        raise LedgerError("INVALID_FILTER")
    if (
        type(filters.page) is not int
        or not 0 <= filters.page <= 1_000_000
        or type(filters.page_size) is not int
        or not 1 <= filters.page_size <= 200
        or type(filters.include_deleted) is not bool
        or not isinstance(filters.search, str)
        or len(filters.search) > 4000
    ):
        raise LedgerError("INVALID_FILTER")
    if filters.kind is not None and (
        not isinstance(filters.kind, str) or filters.kind not in _KINDS
    ):
        raise LedgerError("INVALID_FILTER")
    for day in (filters.start_on, filters.end_on):
        if day is not None and type(day) is not date:
            raise LedgerError("INVALID_FILTER")
    if (
        filters.start_on is not None
        and filters.end_on is not None
        and filters.start_on > filters.end_on
    ):
        raise LedgerError("INVALID_FILTER")
    for identifier in (filters.book_id, filters.account_id, filters.category_id, filters.tag_id):
        if identifier is not None:
            if not isinstance(identifier, str):
                raise LedgerError("INVALID_FILTER")
            try:
                normalize_id(identifier)
            except LedgerError as error:
                raise LedgerError("INVALID_FILTER") from error


def _where(filters: TransactionFilter) -> tuple[str, list[str | int]]:
    clauses: list[str] = []
    parameters: list[str | int] = []
    if not filters.include_deleted:
        clauses.append("t.deleted_at_utc IS NULL")
    if filters.book_id is not None:
        clauses.append(f"({_EFFECTIVE_BOOK})=?")
        parameters.append(normalize_id(filters.book_id))
    if filters.account_id is not None:
        clauses.append(
            "EXISTS(SELECT 1 FROM account_entries filter_entry "
            "WHERE filter_entry.transaction_id=t.id AND filter_entry.account_id=?)"
        )
        parameters.append(normalize_id(filters.account_id))
    if filters.category_id is not None:
        clauses.append(f"({_EFFECTIVE_CATEGORY})=?")
        parameters.append(normalize_id(filters.category_id))
    if filters.tag_id is not None:
        clauses.append(
            "EXISTS(SELECT 1 FROM transaction_tags filter_tag "
            "WHERE filter_tag.transaction_id=t.id AND filter_tag.tag_id=?)"
        )
        parameters.append(normalize_id(filters.tag_id))
    if filters.kind is not None:
        clauses.append("t.kind=?")
        parameters.append(filters.kind)
    if filters.start_on is not None:
        clauses.append("t.occurred_on>=?")
        parameters.append(filters.start_on.isoformat())
    if filters.end_on is not None:
        clauses.append("t.occurred_on<=?")
        parameters.append(filters.end_on.isoformat())
    search = unicodedata.normalize("NFC", filters.search).strip()
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        columns = (
            "t.note",
            "t.counterparty",
            "t.merchant",
            "t.location",
            "t.source_text",
            "book.name",
            "category.name",
            "payment.name",
        )
        matches = [f"{column} LIKE ? ESCAPE '\\'" for column in columns]
        matches.append(
            "EXISTS(SELECT 1 FROM account_entries search_entry "
            "JOIN accounts search_account ON search_account.id=search_entry.account_id "
            "WHERE search_entry.transaction_id=t.id "
            "AND search_account.name LIKE ? ESCAPE '\\')"
        )
        clauses.append("(" + " OR ".join(matches) + ")")
        parameters.extend([pattern] * len(matches))
    return ("WHERE " + " AND ".join(clauses) + " " if clauses else ""), parameters


def _decorate(connection: sqlite3.Connection, rows: tuple[dict[str, object], ...]) -> None:
    """Attach account roles and tags without one SQL round trip per row."""
    if not rows:
        return
    identifiers = tuple(cast(str, row["id"]) for row in rows)
    placeholders = ",".join("?" for _ in identifiers)
    entries: dict[str, list[dict[str, object]]] = {identifier: [] for identifier in identifiers}
    tags: dict[str, list[str]] = {identifier: [] for identifier in identifiers}
    for entry in connection.execute(
        "SELECT e.transaction_id,e.account_id,a.name AS account_name,e.delta_minor "
        "FROM account_entries e JOIN accounts a ON a.id=e.account_id "
        f"WHERE e.transaction_id IN ({placeholders}) ORDER BY e.account_id",
        identifiers,
    ):
        entries[entry["transaction_id"]].append(
            {
                "account_id": entry["account_id"],
                "account_name": entry["account_name"],
                "delta_minor": entry["delta_minor"],
            }
        )
    for link in connection.execute(
        "SELECT transaction_id,tag_id FROM transaction_tags "
        f"WHERE transaction_id IN ({placeholders}) ORDER BY tag_id",
        identifiers,
    ):
        tags[link["transaction_id"]].append(link["tag_id"])
    for row in rows:
        identifier = cast(str, row["id"])
        account_entries = entries[identifier]
        row["entries"] = tuple(account_entries)
        row["tag_ids"] = tuple(tags[identifier])
        row["account_id"] = None
        row["account_name"] = None
        for key in ("from_account_id", "from_account_name", "to_account_id", "to_account_name"):
            row[key] = None
        if row["kind"] == "transfer":
            for entry in account_entries:
                prefix = "from" if cast(int, entry["delta_minor"]) < 0 else "to"
                row[prefix + "_account_id"] = entry["account_id"]
                row[prefix + "_account_name"] = entry["account_name"]
        elif len(account_entries) == 1:
            row["account_id"] = account_entries[0]["account_id"]
            row["account_name"] = account_entries[0]["account_name"]


class LedgerQueries:
    """Capture each read model in one snapshot; never mutate business data."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def preferences(self) -> dict[str, object]:
        """Read selections saved atomically by the existing management commands."""
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT default_book_id,default_account_id FROM app_preferences WHERE singleton=1"
            ).fetchone()
            if row is None:
                raise LedgerError("DATABASE_INTEGRITY_ERROR")
            return dict(row)

    def transactions(self, filters: TransactionFilter) -> TransactionPage:
        """Return a stable page including archived references and inherited refunds."""
        _validate(filters)
        where, parameters = _where(filters)
        with self.database.read() as connection:
            change_seq = _change_seq(connection)
            total = int(
                connection.execute("SELECT COUNT(*) " + _JOINS + where, parameters).fetchone()[0]
            )
            rows: tuple[dict[str, object], ...] = tuple(
                dict(row)
                for row in connection.execute(
                    f"SELECT t.*,({_EFFECTIVE_BOOK}) AS effective_book_id,"
                    f"({_EFFECTIVE_CATEGORY}) AS effective_category_id,"
                    "book.name AS book_name,category.name AS category_name,"
                    "payment.name AS payment_method_name "
                    + _JOINS
                    + where
                    + "ORDER BY t.occurred_on DESC,t.created_at_utc DESC,t.id DESC "
                    "LIMIT ? OFFSET ?",
                    [*parameters, filters.page_size, filters.page * filters.page_size],
                )
            )
            _decorate(connection, rows)
        return TransactionPage(rows, total, filters.page, filters.page_size, change_seq)

    def overview(self, reference_date: date) -> Overview:
        """Sum Python integers exactly and net refunds on their actual receipt month."""
        if type(reference_date) is not date:
            raise LedgerError("INVALID_FILTER")
        month_start = reference_date.replace(day=1)
        month_end = reference_date.replace(
            day=calendar.monthrange(reference_date.year, reference_date.month)[1]
        )
        with self.database.read() as connection:
            change_seq = _change_seq(connection)
            balances = {
                str(row[0]): 0 for row in connection.execute("SELECT id FROM accounts ORDER BY id")
            }
            for entry in connection.execute(
                "SELECT e.account_id,e.delta_minor FROM account_entries e "
                "JOIN transactions t ON t.id=e.transaction_id WHERE t.deleted_at_utc IS NULL"
            ):
                balances[entry[0]] += int(entry[1])
            balances = {key: checked_aggregate(value) for key, value in balances.items()}
            target = display_currency(connection)
            valuation = Valuation(connection, target)
            missing: set[str] = set()
            assets_complete = income_complete = expense_complete = True
            total_assets = 0
            for account in connection.execute("SELECT id,currency_code FROM accounts"):
                try:
                    total_assets += valuation.convert(
                        balances[account["id"]], account["currency_code"], reference_date
                    )
                except LedgerError as error:
                    if error.code != "EXCHANGE_RATE_MISSING":
                        raise
                    missing.add(str(error))
                    assets_complete = False
            # Valuations are display integers, not stored native balances; retain all digits.
            totals = {"income": 0, "expense": 0, "expense_refund": 0}
            for row in connection.execute(
                "SELECT kind,amount_minor,currency_code,occurred_on FROM v_cashflow_transactions "
                "WHERE occurred_on>=? AND occurred_on<=?",
                (month_start.isoformat(), month_end.isoformat()),
            ):
                try:
                    totals[row[0]] += valuation.convert(
                        int(row[1]), row["currency_code"], date.fromisoformat(row["occurred_on"])
                    )
                except LedgerError as error:
                    if error.code != "EXCHANGE_RATE_MISSING":
                        raise
                    missing.add(str(error))
                    if row[0] == "income":
                        income_complete = False
                    else:
                        expense_complete = False
            summary: Mapping[str, int] = totals
        expense = summary["expense"] - summary["expense_refund"]
        return Overview(
            balances=balances,
            total_assets_minor=total_assets,
            income_minor=summary["income"],
            gross_expense_minor=summary["expense"],
            refund_minor=summary["expense_refund"],
            expense_minor=expense,
            net_minor=summary["income"] - expense,
            month_start=month_start,
            month_end=month_end,
            change_seq=change_seq,
            currency_code=target,
            assets_complete=assets_complete,
            income_complete=income_complete,
            expense_complete=expense_complete,
            missing_rates=tuple(sorted(missing)),
        )
