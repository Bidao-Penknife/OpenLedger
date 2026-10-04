"""Read-only diagnostics for actual frozen file readers and report engines."""

import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import openpyxl.xml

from openledger.application.dto.analytics import AnalyticsFilter
from openledger.application.dto.exchange import ExchangeMapping
from openledger.infrastructure.analytics import AnalyticsService
from openledger.infrastructure.exchange import ExchangeService, read_table
from openledger.infrastructure.ledger import LedgerService
from openledger.presentation.report_export import ReportExporter


def financial_fingerprint(connection: sqlite3.Connection) -> str:
    """Hash logical rows and schema, excluding SQLite's mutable storage headers."""
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
    ]
    contents = {
        table: sorted(
            (
                list(row)
                for row in connection.execute('SELECT * FROM "' + table.replace('"', '""') + '"')
            ),
            key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True),
        )
        for table in tables
    }
    schema = [
        list(row)
        for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' "
            "ORDER BY type,name"
        )
    ]
    value = {
        "tables": contents,
        "schema": schema,
        "user_version": connection.execute("PRAGMA user_version").fetchone()[0],
        "application_id": connection.execute("PRAGMA application_id").fetchone()[0],
    }
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def verify_features(ledger: LedgerService, output: Path) -> dict[str, object]:
    """Exercise packaged engines without creating accounts or committing finances."""
    output.mkdir(parents=True, exist_ok=True)
    with ledger.database.read() as connection:
        before_fingerprint = financial_fingerprint(connection)
        before = tuple(
            connection.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
            for name in ("transactions", "account_entries", "command_receipts")
        )
    today: date = ledger.clock().astimezone(ZoneInfo(ledger.time_zone)).date()
    report = AnalyticsService(ledger.database, clock=ledger.clock).build_report(
        AnalyticsFilter(today.replace(day=1), today)
    )
    files: dict[str, str] = {}
    exchange = ExchangeService(ledger)
    for format in ("csv", "xlsx"):
        path = output / ("transactions." + format)
        result = exchange.export_transactions(path, format)
        if not result.path.is_file() or result.data_revision != report.data_revision:
            raise RuntimeError("Read-only feature snapshot mismatch")
        files[format] = str(path)
    for format in ("pdf", "png"):
        path = ReportExporter().export(report, output / ("report." + format), format=format)
        signature = path.read_bytes()[:8]
        if (format == "pdf" and not signature.startswith(b"%PDF-")) or (
            format == "png" and signature != b"\x89PNG\r\n\x1a\n"
        ):
            raise RuntimeError("Rendered report signature mismatch")
        files[format] = str(path)
    for format in ("csv", "xlsx"):
        path = output / ("reader-probe." + format)
        exchange._publish(
            path,
            format,
            ("kind", "amount", "occurred_on", "note"),
            [("expense", "25.00", today.isoformat(), "=1+1")],
        )
        table = read_table(path)
        preview = exchange.preview(table, ExchangeMapping(time_zone=ledger.time_zone))
        if len(preview.rows) != 1 or table.formula_rows:
            raise RuntimeError("Packaged file reader failed")
    with ledger.database.read() as connection:
        after_fingerprint = financial_fingerprint(connection)
        after = tuple(
            connection.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
            for name in ("transactions", "account_entries", "command_receipts")
        )
    if (
        before != after
        or before_fingerprint != after_fingerprint
        or not getattr(openpyxl.xml, "DEFUSEDXML", False)
    ):
        raise RuntimeError("Diagnostics changed finances or XML protection is missing")
    return {
        "status": "passed",
        "financial_counts_unchanged": True,
        "financial_fingerprint": before_fingerprint,
        "all_financial_rows_unchanged": True,
        "data_revision": report.data_revision,
        "defusedxml_active": True,
        "csv_xlsx_readers_verified": True,
        "outputs": files,
    }
