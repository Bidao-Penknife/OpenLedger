"""Immutable plans for bounded and reviewable file exchange."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExchangeMapping:
    """Explicit header mapping and destination defaults; never round amounts."""

    columns: tuple[tuple[str, str], ...] = ()
    book_id: str | None = None
    account_id: str | None = None
    income_category_id: str | None = None
    expense_category_id: str | None = None
    from_account_id: str | None = None
    to_account_id: str | None = None
    default_kind: str = "expense"
    time_zone: str = "Asia/Shanghai"
    encoding: str = "utf-8-sig"
    sheet: str | None = None


@dataclass(frozen=True)
class FileTable:
    """Source bytes identified by a digest, with logical data row numbers."""

    path: Path
    source_format: str
    file_digest: str
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    formula_rows: frozenset[int] = frozenset()
    sheets: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImportRow:
    """Validated candidate; possible duplicates require explicit selection."""

    source_row_number: int
    transaction_id: str
    fields: dict[str, object] | None
    external_source: str | None
    external_transaction_id: str | None
    issues: tuple[str, ...]
    exact_duplicate: bool = False
    possible_duplicates: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImportPreview:
    """Bind confirmation to source bytes, mapping and displayed row identities."""

    table: FileTable
    mapping_json: str
    mapping_hash: str
    batch_id: str
    rows: tuple[ImportRow, ...]
    data_revision: int
    plan_digest: str = ""


@dataclass(frozen=True)
class ExportResult:
    """Published destination and the single database snapshot it contains."""

    path: Path
    row_count: int
    data_revision: int
