"""Check cross-row financial invariants that SQLite constraints cannot express."""

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openledger.domain.currencies import currency, rate_fraction
from openledger.domain.errors import LedgerError
from openledger.domain.money import checked_aggregate, validate_minor
from openledger.domain.values import normalize_id, utc_text


def _require(condition: bool) -> None:
    if not condition:
        raise LedgerError("INTEGRITY_FAILED")


def validate_financial_integrity(connection: sqlite3.Connection) -> None:
    """Validate a read snapshot without repairing records or consulting today's clock.

    This catches structurally valid but semantically damaged databases before startup
    and restoration. Historical deleted events keep their original entries; only active
    events participate in cut points, opening uniqueness and refundable limits.
    """
    try:
        _validate(connection)
    except LedgerError as error:
        if error.code == "AGGREGATE_OUT_OF_RANGE":
            raise
        raise LedgerError("INTEGRITY_FAILED") from error
    except (
        ValueError,
        TypeError,
        KeyError,
        OverflowError,
        ZoneInfoNotFoundError,
        sqlite3.Error,
    ) as error:
        raise LedgerError("INTEGRITY_FAILED") from error


def _validate(connection: sqlite3.Connection) -> None:
    accounts = {row["id"]: dict(row) for row in connection.execute("SELECT * FROM accounts")}
    categories = {row["id"]: dict(row) for row in connection.execute("SELECT * FROM categories")}
    transactions = {
        row["id"]: dict(row) for row in connection.execute("SELECT * FROM transactions")
    }
    batches = {row["id"]: dict(row) for row in connection.execute("SELECT * FROM import_batches")}
    batch_members: dict[str, list[str]] = defaultdict(list)
    entries: dict[str, list[tuple[str, int]]] = defaultdict(list)
    balances = dict.fromkeys(accounts, 0)
    openings: set[str] = set()
    refunds: dict[str, int] = defaultdict(int)
    for _entity, table in [
        ("book", "books"),
        ("account", "accounts"),
        ("category", "categories"),
        ("tag", "tags"),
        ("payment_method", "payment_methods"),
        ("transaction", "transactions"),
        ("import_batch", "import_batches"),
    ]:
        for row in connection.execute(f"SELECT id FROM {table}"):
            _require(normalize_id(row[0]) == row[0])
    for account in accounts.values():
        currency(account["currency_code"])
        date.fromisoformat(account["balance_start_on"])
    for book in connection.execute("SELECT currency_code FROM books"):
        currency(book[0])
    for category in categories.values():
        parent_id = category["parent_id"]
        if parent_id is not None:
            parent = categories[parent_id]
            _require(parent_id != category["id"] and parent["parent_id"] is None)
            _require(parent["transaction_kind"] == category["transaction_kind"])
    for row in connection.execute("SELECT * FROM account_entries"):
        _require(normalize_id(row["id"]) == row["id"])
        _require(row["transaction_id"] in transactions and row["account_id"] in accounts)
        validate_minor(row["delta_minor"], signed=True)
        entries[row["transaction_id"]].append((row["account_id"], row["delta_minor"]))
    for identifier, transaction in transactions.items():
        batch_id = transaction["import_batch_id"]
        if batch_id is not None:
            batch = batches[batch_id]
            _require(transaction["source"] == "import")
            _require(transaction["kind"] in {"income", "expense", "transfer", "expense_refund"})
            _require(
                transaction["import_file_digest"] == batch["file_digest"]
                and transaction["import_mapping_hash"] == batch["mapping_hash"]
            )
            batch_members[batch_id].append(identifier)
        amount = validate_minor(transaction["amount_minor"])
        day = date.fromisoformat(transaction["occurred_on"])
        zone = ZoneInfo(transaction["time_zone"])
        for field in ["created_at_utc", "updated_at_utc", "deleted_at_utc"]:
            if transaction[field] is not None:
                instant = datetime.fromisoformat(transaction[field].replace("Z", "+00:00"))
                _require(utc_text(instant) == transaction[field])
        precision = transaction["occurrence_precision"]
        if precision == "exact":
            instant = datetime.fromisoformat(transaction["occurred_at_utc"].replace("Z", "+00:00"))
            _require(utc_text(instant) == transaction["occurred_at_utc"])
            _require(instant.astimezone(zone).date() == day and transaction["time_period"] is None)
        elif precision == "period":
            _require(
                transaction["time_period"] in {"morning", "noon", "afternoon", "evening", "night"}
            )
            _require(transaction["occurred_at_utc"] is None)
        else:
            _require(
                precision == "date"
                and transaction["time_period"] is None
                and transaction["occurred_at_utc"] is None
            )
        rows = entries[identifier]
        kind = transaction["kind"]
        active = transaction["deleted_at_utc"] is None
        _require(len(rows) == (2 if kind == "transfer" else 1))
        if kind == "transfer":
            debit = [(account, delta) for account, delta in rows if delta < 0]
            credit = [(account, delta) for account, delta in rows if delta > 0]
            _require(len(debit) == len(credit) == 1 and debit[0][0] != credit[0][0])
            destination_amount = transaction.get("to_amount_minor", amount)
            destination_code = transaction.get("to_currency_code", transaction["currency_code"])
            _require(debit[0][1] == -amount and credit[0][1] == validate_minor(destination_amount))
            _require(accounts[debit[0][0]]["currency_code"] == transaction["currency_code"])
            _require(accounts[credit[0][0]]["currency_code"] == destination_code)
            if destination_code == transaction["currency_code"]:
                _require(destination_amount == amount)
        elif kind == "expense":
            _require(rows[0][1] == -amount)
        elif kind in {"income", "expense_refund"}:
            _require(rows[0][1] == amount)
        else:
            _require(kind in {"opening", "adjustment"} and abs(rows[0][1]) == amount)
        currency(transaction["currency_code"])
        if kind != "transfer":
            _require(accounts[rows[0][0]]["currency_code"] == transaction["currency_code"])
        if kind in {"income", "expense"}:
            _require(categories[transaction["category_id"]]["transaction_kind"] == kind)
        if kind == "adjustment":
            checked_aggregate(transaction["balance_before_minor"])
            checked_aggregate(transaction["balance_target_minor"])
            _require(
                transaction["balance_target_minor"] - transaction["balance_before_minor"]
                == rows[0][1]
            )
        if active:
            for account_id, delta in rows:
                _require(day >= date.fromisoformat(accounts[account_id]["balance_start_on"]))
                balances[account_id] += delta
            if kind == "opening":
                account_id = rows[0][0]
                _require(
                    account_id not in openings
                    and transaction["occurred_on"] == accounts[account_id]["balance_start_on"]
                )
                openings.add(account_id)
            if kind == "expense_refund":
                original = transactions[transaction["original_transaction_id"]]
                _require(original["kind"] == "expense" and original["deleted_at_utc"] is None)
                _require(original["currency_code"] == transaction["currency_code"])
                _require(day >= date.fromisoformat(original["occurred_on"]))
                refunds[original["id"]] += amount
    for original_id, total in refunds.items():
        _require(total <= transactions[original_id]["amount_minor"])
    for identifier, batch in batches.items():
        mapping = json.loads(batch["mapping_json"])
        _require(isinstance(mapping, dict))
        _require(
            json.dumps(
                mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            == batch["mapping_json"]
        )
        _require(
            hashlib.sha256(batch["mapping_json"].encode("utf-8")).hexdigest()
            == batch["mapping_hash"]
        )
        _require(len(batch_members[identifier]) == batch["accepted_row_count"])
        for field in ("created_at_utc", "updated_at_utc", "reverted_at_utc"):
            if batch[field] is not None:
                instant = datetime.fromisoformat(batch[field].replace("Z", "+00:00"))
                _require(utc_text(instant) == batch[field])
        _require(
            batch["format_version"] == 1 and batch["created_at_utc"] <= batch["updated_at_utc"]
        )
        if batch["status"] == "reverted":
            _require(batch["version"] == 2 and batch["reverted_at_utc"] == batch["updated_at_utc"])
            _require(
                all(
                    transactions[item]["deleted_at_utc"] is not None
                    for item in batch_members[identifier]
                )
            )
        else:
            _require(batch["status"] == "committed" and batch["version"] == 1)
    for balance in balances.values():
        checked_aggregate(balance)
    native_totals: dict[str, int] = {}
    for identifier, value in balances.items():
        code = str(accounts[identifier]["currency_code"])
        native_totals[code] = native_totals.get(code, 0) + value
    for value in native_totals.values():
        checked_aggregate(value)
    if int(connection.execute("PRAGMA user_version").fetchone()[0]) >= 2:
        for rate in connection.execute("SELECT * FROM exchange_rates"):
            currency(rate["currency_code"])
            _require(rate["currency_code"] != "CNY")
            _require(date.fromisoformat(rate["effective_on"]).isoformat() == rate["effective_on"])
            rate_fraction(rate["rate_text"])
            _require(normalize_id(rate["id"]) == rate["id"])
            _require(
                rate["id"]
                == str(
                    uuid5(
                        NAMESPACE_URL,
                        f"openledger:rate:{rate['currency_code']}:{rate['effective_on']}",
                    )
                )
            )
        settings = connection.execute("SELECT * FROM currency_settings").fetchall()
        _require(len(settings) == 1)
        from openledger.infrastructure.currencies import SETTINGS_ID

        _require(settings[0]["id"] == SETTINGS_ID)
        currency(settings[0]["display_currency"])
        for item in connection.execute("SELECT * FROM captured_inputs"):
            _require(normalize_id(item["id"]) == item["id"])
            _require(
                item["id"] == str(uuid5(NAMESPACE_URL, f"openledger:capture:{item['source_key']}"))
            )
            _require(isinstance(json.loads(item["suggested_json"]), dict))
            _require(len(item["suggested_json"]) <= 16000)
            if item["image_relative_path"]:
                _require(
                    re.fullmatch(r"[0-9a-f-]{36}\.(?:png|jpg|webp)", item["image_relative_path"])
                    is not None
                )
                normalize_id(item["image_relative_path"].rsplit(".", 1)[0])
            for field in ("created_at_utc", "updated_at_utc"):
                instant = datetime.fromisoformat(item[field].replace("Z", "+00:00"))
                _require(utc_text(instant) == item[field])
            if item["state"] == "saved":
                _require(item["transaction_id"] in transactions)
    preferences = connection.execute("SELECT * FROM app_preferences").fetchall()
    _require(len(preferences) == 1 and preferences[0]["singleton"] == 1)
    for entity, table in [("book", "books"), ("account", "accounts")]:
        identifier = preferences[0]["default_" + entity + "_id"]
        active_count = connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE is_archived=0"
        ).fetchone()[0]
        if identifier is None:
            _require(active_count == 0)
        else:
            row = connection.execute(
                f"SELECT is_archived FROM {table} WHERE id=?", (identifier,)
            ).fetchone()
            _require(row is not None and row[0] == 0)
