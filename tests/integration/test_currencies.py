"""Real legacy migration, native precision and independently calculated valuations."""

import sqlite3
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

from openledger.application.dto.analytics import AnalyticsFilter
from openledger.application.dto.ledger import TransactionFields, TransferFields
from openledger.domain.currencies import convert_minor, format_minor
from openledger.domain.errors import LedgerError
from openledger.domain.money import parse_amount
from openledger.infrastructure.analytics import AnalyticsService
from openledger.infrastructure.database.database import Database, _load_migrations
from openledger.infrastructure.integrity import validate_financial_integrity
from openledger.infrastructure.ledger import LedgerService
from openledger.infrastructure.queries import LedgerQueries

pytestmark = pytest.mark.integration


def uid() -> str:
    return str(uuid4())


def add_account(ledger: LedgerService, code: str, opening: int) -> str:
    identifier = uid()
    ledger.execute(
        uid(),
        "account.create.v1",
        {
            "id": identifier,
            "name": f"合成{code}",
            "account_type": "custom",
            "currency_code": code,
            "balance_start_on": "2026-01-01",
            "opening_balance_minor": opening,
        },
    )
    return identifier


@pytest.mark.parametrize(
    ("code", "text", "minor"),
    [("JPY", "500", 500), ("USD", "12.34", 1234), ("KWD", "1.234", 1234), ("CLF", "1.2345", 12345)],
)
def test_exact_native_precision(code: str, text: str, minor: int) -> None:
    assert parse_amount(text, code) == minor
    assert format_minor(minor, code) == text
    with pytest.raises(LedgerError, match="AMOUNT_PRECISION"):
        parse_amount(text + (".1" if "." not in text else "1"), code)


def test_valuation_rounds_once_with_exact_signed_half() -> None:
    assert convert_minor(1, "JPY", "CNY", "0.045", "1") == 5
    assert convert_minor(-1, "JPY", "CNY", "0.045", "1") == -5
    assert convert_minor(1234, "KWD", "USD", "23.5", "7.2") == 403
    huge = 9007199254740993
    assert convert_minor(huge, "USD", "USD", "7.2", "7.2") == huge


def test_real_previous_release_migrates_without_rewriting_history(tmp_path: Path) -> None:
    database = Database(tmp_path / "old.sqlite3")
    database.path.parent.mkdir(exist_ok=True)
    with sqlite3.connect(database.path, isolation_level=None) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        database._migrate(connection, _load_migrations()[:1], 0)
        connection.execute("DELETE FROM app_preferences")
        connection.executescript(
            "BEGIN; PRAGMA defer_foreign_keys=ON;\n"
            + (Path(__file__).parents[1] / "fixtures/legacy-cny.sql").read_text("utf-8")
            + "\nCOMMIT;"
        )
        connection.commit()
        original = {
            table: [
                dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
            ]
            for table in (
                "accounts",
                "account_entries",
                "transactions",
                "audit_events",
                "change_log",
                "command_receipts",
                "app_preferences",
            )
        }
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    database.initialize()
    with database.read() as connection:
        validate_financial_integrity(connection)
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        for table, rows in original.items():
            after = [
                dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
            ]
            if table == "transactions":
                for row in after:
                    incoming, code = row.pop("to_amount_minor"), row.pop("to_currency_code")
                    assert incoming == (row["amount_minor"] if row["kind"] == "transfer" else None)
                    assert code == ("CNY" if row["kind"] == "transfer" else None)
            assert after == rows
        assert (
            sum(
                row[0] for row in connection.execute("SELECT balance_minor FROM v_account_balances")
            )
            == 98000
        )
    copies = list((tmp_path / "migration-backups").glob("*.sqlite3"))
    assert len(copies) == 1
    with sqlite3.connect(copies[0]) as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 1
        assert old.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    database.initialize()
    assert len(list((tmp_path / "migration-backups").glob("*.sqlite3"))) == 1


def test_fx_transfer_records_actual_both_sides_and_replays(ledger: LedgerService) -> None:
    yen = add_account(ledger, "JPY", 10000)
    cash = str(
        next(row["id"] for row in ledger.entities("account") if row["currency_code"] == "CNY")
    )
    request, transaction = uid(), uid()
    fields = TransferFields(
        amount_minor=1000,
        from_account_id=yen,
        to_account_id=cash,
        occurred_on=date(2026, 10, 2),
        currency_code="JPY",
        to_amount_minor=4800,
    )
    before = ledger.balances()
    result = ledger.transfer(fields, request_id=request, transaction_id=transaction)
    assert ledger.balances()[yen] == before[yen] - 1000
    assert ledger.balances()[cash] == before[cash] + 4800
    assert ledger.transaction(transaction)["to_currency_code"] == "CNY"
    assert ledger.transfer(fields, request_id=request, transaction_id=transaction).replayed
    assert len(result.balance_changes) == 2
    ledger.execute(uid(), "transaction.delete.v1", {"id": transaction, "expected_version": 1})
    assert ledger.balances() == before
    ledger.execute(uid(), "transaction.restore.v1", {"id": transaction, "expected_version": 2})
    with ledger.database.read() as connection:
        validate_financial_integrity(connection)


def test_missing_quote_never_displays_partial_total_and_historical_report(
    ledger: LedgerService,
) -> None:
    yen = add_account(ledger, "JPY", 10000)
    view = LedgerQueries(ledger.database).overview(date(2026, 10, 2))
    assert view.assets_complete is False and view.missing_rates
    with pytest.raises(LedgerError) as missing:
        ledger.total_assets()
    assert missing.value.code == "EXCHANGE_RATE_MISSING"
    ledger.execute(
        uid(),
        "rate.set.v1",
        {
            "currency_code": "JPY",
            "effective_on": "2026-01-01",
            "rate_text": "0.05",
            "note": "合成汇率",
        },
    )
    refs = ledger.entities("category")
    expense = next(row["id"] for row in refs if row["transaction_kind"] == "expense")
    ledger.record(
        TransactionFields(
            "expense",
            1000,
            yen,
            str(ledger.entities("book")[0]["id"]),
            str(expense),
            date(2026, 10, 1),
            currency_code="JPY",
        ),
        request_id=uid(),
        transaction_id=uid(),
    )
    ledger.execute(
        uid(),
        "rate.set.v1",
        {
            "currency_code": "JPY",
            "effective_on": "2026-10-02",
            "rate_text": "0.06",
            "note": "合成汇率",
        },
    )
    view = LedgerQueries(ledger.database).overview(date(2026, 10, 2))
    assert view.assets_complete and view.expense_complete
    assert view.total_assets_minor == 154000  # CNY 1000 + JPY 9000 * 0.06
    assert view.expense_minor == 5000  # Oct 1 uses the earlier 0.05 quotation
    analytics = AnalyticsService(ledger.database, clock=ledger.clock)
    report = analytics.build_report(
        AnalyticsFilter(date(2026, 10, 1), date(2026, 10, 2), currency_code="JPY")
    )
    assert report.totals.gross_expense_minor == 1000
    with ledger.database.read() as connection:
        validate_financial_integrity(connection)


def test_cannot_relabel_native_account_or_mix_refund_currency(ledger: LedgerService) -> None:
    account = add_account(ledger, "JPY", 500)
    with pytest.raises(LedgerError, match="ACCOUNT_CURRENCY_IMMUTABLE"):
        ledger.execute(
            uid(),
            "account.update.v1",
            {
                "id": account,
                "name": "变币种",
                "account_type": "custom",
                "currency_code": "USD",
                "expected_version": 1,
            },
        )
    # Missing destination amount must never invent an FX rate.
    cash = str(
        next(row["id"] for row in ledger.entities("account") if row["currency_code"] == "CNY")
    )
    with pytest.raises(LedgerError, match="TRANSFER_TARGET_AMOUNT_REQUIRED"):
        ledger.execute(
            uid(),
            "transfer.record.v1",
            {
                "id": uid(),
                "fields": {
                    "amount_minor": 100,
                    "currency_code": "JPY",
                    "from_account_id": account,
                    "to_account_id": cash,
                    "occurred_on": "2026-10-02",
                },
            },
        )


@pytest.mark.parametrize("format", ["csv", "xlsx"])
def test_multicurrency_exchange_round_trip_and_revert(
    ledger: LedgerService, tmp_path: Path, format: str
) -> None:
    from openledger.application.dto.exchange import ExchangeMapping
    from openledger.application.dto.queries import TransactionFilter
    from openledger.infrastructure.exchange import ExchangeService, read_table

    yen = add_account(ledger, "JPY", 5000)
    kwd = add_account(ledger, "KWD", 1234)
    book = str(ledger.entities("book")[0]["id"])
    category = str(
        next(r["id"] for r in ledger.entities("category") if r["transaction_kind"] == "expense")
    )
    cash = str(next(r["id"] for r in ledger.entities("account") if r["currency_code"] == "CNY"))
    transaction = uid()
    ledger.transfer(
        TransferFields(
            from_account_id=yen,
            to_account_id=cash,
            amount_minor=1000,
            occurred_on=date(2026, 10, 2),
            currency_code="JPY",
            to_amount_minor=4800,
        ),
        request_id=uid(),
        transaction_id=transaction,
    )
    expense = uid()
    ledger.record(
        TransactionFields(
            "expense", 123, kwd, book, category, date(2026, 10, 2), currency_code="KWD"
        ),
        request_id=uid(),
        transaction_id=expense,
    )
    exchange = ExchangeService(ledger)
    path = tmp_path / ("synthetic." + format)
    exchange.export_transactions(
        path, format, TransactionFilter(start_on=date(2026, 10, 2), end_on=date(2026, 10, 2))
    )
    destination = Database(tmp_path / "destination.sqlite3")
    destination.initialize()
    target = LedgerService(destination, clock=ledger.clock)
    target.ensure_defaults()
    for row in ledger.entities("account"):
        target.execute(
            uid(),
            "account.create.v1",
            {
                "opening_balance_minor": 0,
                **{
                    key: row[key]
                    for key in ("id", "name", "account_type", "currency_code", "balance_start_on")
                },
            },
        )
    ledger = target
    exchange = ExchangeService(target)
    plan = ExchangeMapping()
    preview = exchange.preview(read_table(path, plan), plan)
    exported = {r.fields["kind"]: r.fields for r in preview.rows if r.fields is not None}
    assert exported["transfer"]["amount_minor"] == 1000
    assert exported["transfer"]["to_amount_minor"] == 4800
    assert exported["expense"]["amount_minor"] == 123
    assert exported["expense"]["currency_code"] == "KWD"
    valid_rows = [r for r in preview.rows if r.fields is not None]
    assert all(not r.issues for r in valid_rows)
    assert all(r.issues == ("UNSUPPORTED_IMPORT_KIND",) for r in preview.rows if r.fields is None)
    before = ledger.balances()
    ledger.execute(
        uid(),
        "import.commit.v1",
        exchange.commit_payload(preview, [r.source_row_number for r in valid_rows]),
    )
    assert ledger.balances()[yen] == before[yen] - 1000
    assert ledger.balances()[cash] == before[cash] + 4800
    assert ledger.balances()[kwd] == before[kwd] - 123
    ledger.execute(uid(), "import.revert.v1", {"id": preview.batch_id, "expected_version": 1})
    assert ledger.balances() == before


def test_real_legacy_backup_restores_to_schema_two_without_touching_archive(tmp_path: Path) -> None:
    import hashlib
    import json
    import zipfile

    from openledger.infrastructure.backup import BackupService

    old_path = tmp_path / "legacy.sqlite3"
    database = Database(old_path)
    with sqlite3.connect(old_path, isolation_level=None) as connection:
        database._migrate(connection, _load_migrations()[:1], 0)
        connection.execute("DELETE FROM app_preferences")
        connection.executescript(
            "BEGIN; PRAGMA defer_foreign_keys=ON;\n"
            + (Path(__file__).parents[1] / "fixtures/legacy-cny.sql").read_text("utf-8")
            + "\nCOMMIT;"
        )
    content = old_path.read_bytes()
    manifest = {
        "format_version": 1,
        "app_version": "1.0.0rc1",
        "schema_version": 1,
        "created_at_utc": "2026-10-06T00:00:00.000Z",
        "database_sha256": hashlib.sha256(content).hexdigest(),
        "database_size_bytes": len(content),
        "attachments": [],
    }
    archive = tmp_path / "legacy.olbackup"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("manifest.json", json.dumps(manifest))
        output.writestr("database.sqlite3", content)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    restored = BackupService(Database(tmp_path / "unused.sqlite3")).restore(
        archive, tmp_path / "restored"
    )
    with restored.read() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        validate_financial_integrity(connection)
    assert LedgerService(restored).total_assets() == 98000
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == digest
    assert old_path.read_bytes() == content
