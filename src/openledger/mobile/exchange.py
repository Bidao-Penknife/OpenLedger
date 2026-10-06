"""Persist reviewed import plans and decisions outside Android's disposable cache."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from openledger.application.dto.exchange import ExchangeMapping, FileTable, ImportPreview, ImportRow
from openledger.application.dto.queries import TransactionFilter
from openledger.domain.errors import LedgerError
from openledger.domain.values import normalize_id
from openledger.infrastructure.exchange import ExchangeService, automatic_columns, read_table
from openledger.infrastructure.ledger import LedgerService
from openledger.mobile.files import MobileFiles

_MAX_PLAN_BYTES = 128 * 1024 * 1024


def _keys(body: dict[str, Any], allowed: set[str]) -> None:
    if set(body) - allowed:
        raise LedgerError("INVALID_ENVELOPE")


def _text(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or len(value) > 8192:
        raise LedgerError("INVALID_ENVELOPE")
    return value


class MobileExchange:
    """Financial writes always use the shared transaction and durable UUID receipt."""

    def __init__(self, ledger: LedgerService, files: MobileFiles, time_zone: str) -> None:
        self.ledger = ledger
        self.files = files
        self.time_zone = time_zone
        self.exchange = ExchangeService(ledger)
        self.folder = files.directory / "imports"
        if self.folder.is_symlink() or self.folder.is_junction():
            raise LedgerError("INVALID_FILE_NAME")
        self.folder.mkdir(exist_ok=True)

    def _path(self, prefix: str, identifier: str, suffix: str = "json") -> Path:
        path = self.folder / f"{prefix}-{normalize_id(identifier)}.{suffix}"
        if (
            path.is_symlink()
            or path.is_junction()
            or path.resolve().parent != self.folder.resolve()
        ):
            raise LedgerError("INVALID_FILE_NAME")
        return path

    @staticmethod
    def _write(path: Path, value: dict[str, Any]) -> str:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded) > _MAX_PLAN_BYTES:
            raise LedgerError("IMPORT_FILE_TOO_LARGE")
        digest = hashlib.sha256(encoded).hexdigest()
        if path.exists():
            if (
                path.stat().st_size > _MAX_PLAN_BYTES
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest
            ):
                raise LedgerError("IDEMPOTENCY_KEY_REUSED")
            return digest
        temporary = path.with_suffix(".tmp")
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        return digest

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        if path.stat().st_size > _MAX_PLAN_BYTES:
            raise LedgerError("IMPORT_FILE_TOO_LARGE")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as error:
            raise LedgerError("IMPORT_PREVIEW_CHANGED") from error
        if not isinstance(value, dict):
            raise LedgerError("IMPORT_PREVIEW_CHANGED")
        return value

    def _preview(self, token: str) -> ImportPreview:
        try:
            return self._load_preview(token)
        except (KeyError, TypeError, ValueError) as error:
            raise LedgerError("IMPORT_PREVIEW_CHANGED") from error

    def _load_preview(self, token: str) -> ImportPreview:
        saved = self._read(self._path("preview", token))
        table = saved["table"]
        if table["source_format"] not in {"csv", "xlsx"}:
            raise LedgerError("IMPORT_PREVIEW_CHANGED")
        source = self._path("source", token, table["source_format"])
        return ImportPreview(
            FileTable(
                source,
                table["source_format"],
                table["file_digest"],
                tuple(table["headers"]),
                tuple(tuple(row) for row in table["rows"]),
                frozenset(table["formula_rows"]),
                tuple(table["sheets"]),
            ),
            saved["mapping_json"],
            saved["mapping_hash"],
            saved["batch_id"],
            tuple(
                ImportRow(
                    row["source_row_number"],
                    row["transaction_id"],
                    row["fields"],
                    row["external_source"],
                    row["external_transaction_id"],
                    tuple(row["issues"]),
                    row["exact_duplicate"],
                    tuple(row["possible_duplicates"]),
                )
                for row in saved["rows"]
            ),
            saved["data_revision"],
            saved["plan_digest"],
        )

    def dispatch(self, action: str, body: dict[str, Any]) -> dict[str, Any]:
        """Read/write bounded private files; JSON callers never supply arbitrary paths."""
        if action == "import_headers":
            _keys(body, {"filename", "encoding", "sheet"})
            settings = ExchangeMapping(
                encoding=body.get("encoding", "utf-8-sig"), sheet=body.get("sheet")
            )
            table = read_table(self.files.path(_text(body, "filename")), settings)
            return {
                "headers": table.headers,
                "sheets": table.sheets,
                "columns": automatic_columns(table.headers),
                "row_count": len(table.rows),
            }
        if action == "import_preview":
            _keys(body, {"filename", "mapping"})
            filename = _text(body, "filename")
            input_path = self.files.path(filename)
            if input_path.suffix.lower() not in {".csv", ".xlsx"}:
                raise LedgerError("IMPORT_FORMAT_UNSUPPORTED")
            mapping = body.get("mapping", {})
            if not isinstance(mapping, dict):
                raise LedgerError("INVALID_ENVELOPE")
            _keys(
                mapping,
                {
                    "columns",
                    "book_id",
                    "account_id",
                    "income_category_id",
                    "expense_category_id",
                    "from_account_id",
                    "to_account_id",
                    "default_kind",
                    "encoding",
                    "sheet",
                },
            )
            columns = mapping.get("columns", [])
            if (
                not isinstance(columns, list)
                or len(columns) > 80
                or any(
                    not isinstance(pair, list)
                    or len(pair) != 2
                    or any(not isinstance(item, str) for item in pair)
                    for pair in columns
                )
            ):
                raise LedgerError("IMPORT_MAPPING_INVALID")
            settings = ExchangeMapping(
                **{
                    **mapping,
                    "columns": tuple(tuple(pair) for pair in columns),
                    "time_zone": self.time_zone,
                }
            )
            # Validate the selected file before preserving its immutable copy.
            read_table(input_path, settings)
            token = str(uuid4())
            source = self._path("source", token, input_path.suffix.lower()[1:])
            with input_path.open("rb") as incoming, source.open("xb") as output:
                shutil.copyfileobj(incoming, output, 64 * 1024)
                output.flush()
                os.fsync(output.fileno())
            preview = self.exchange.preview(read_table(source, settings), settings)
            saved = asdict(preview)
            saved["table"].pop("path")
            saved["table"]["formula_rows"] = sorted(preview.table.formula_rows)
            self._write(self._path("preview", token), saved)
            return {
                "preview_id": token,
                "headers": preview.table.headers,
                "sheets": preview.table.sheets,
                "rows": [
                    {
                        "number": row.source_row_number,
                        "issues": row.issues,
                        "possible_duplicates": row.possible_duplicates,
                        "fields": row.fields,
                    }
                    for row in preview.rows
                ],
                "data_revision": preview.data_revision,
            }
        if action == "import_prepare":
            _keys(body, {"preview_id", "selected_rows", "request_id"})
            selection = body.get("selected_rows")
            if (
                not isinstance(selection, list)
                or len(selection) > 10000
                or any(type(item) is not int for item in selection)
                or len(set(selection)) != len(selection)
            ):
                raise LedgerError("IMPORT_SELECTION_INVALID")
            token = normalize_id(_text(body, "request_id"))
            payload = self.exchange.commit_payload(
                self._preview(_text(body, "preview_id")), selection
            )
            digest = self._write(self._path("decision", token), payload)
            return {"request_id": token, "sha256": digest}
        if action == "import_commit":
            _keys(body, {"request_id", "sha256"})
            token = normalize_id(_text(body, "request_id"))
            path = self._path("decision", token)
            if path.stat().st_size > _MAX_PLAN_BYTES or hashlib.sha256(
                path.read_bytes()
            ).hexdigest() != _text(body, "sha256"):
                raise LedgerError("IMPORT_PREVIEW_CHANGED")
            return asdict(self.ledger.execute(token, "import.commit.v1", self._read(path)))
        if action == "export_transactions":
            _keys(body, {"filename", "format", "filters"})
            format = _text(body, "format")
            if format not in {"csv", "xlsx"}:
                raise LedgerError("EXPORT_PATH_INVALID")
            values = body.get("filters", {})
            if not isinstance(values, dict):
                raise LedgerError("INVALID_FILTER")
            from datetime import date

            values = dict(values)
            for key in ("start_on", "end_on"):
                if values.get(key):
                    values[key] = date.fromisoformat(values[key])
            filters = TransactionFilter(**values)
            result = self.exchange.export_transactions(
                self.files.path(_text(body, "filename"), suffix="." + format), format, filters
            )
            return {
                **self.files.describe(result.path),
                "row_count": result.row_count,
                "data_revision": result.data_revision,
            }
        raise LedgerError("UNSUPPORTED_ACTION")
