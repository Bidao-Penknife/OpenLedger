"""Runtime diagnostics contract without toolkit or persistence dependencies."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimeInfo:
    """Versions and paths useful for diagnosing source and frozen startup."""

    app_version: str
    python_version: str
    pyside_version: str
    qt_version: str
    sqlite_version: str
    data_directory: str
    sqlite_wal_supported: bool
    database_schema_version: int | None = None
    database_journal_mode: str | None = None
