"""Resolve writable application directories independently of program location."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class DataDirectoryError(ValueError):
    """The configured data location is unavailable or ambiguous."""


@dataclass(frozen=True, slots=True)
class AppPaths:
    """A single data root with explicit directory responsibilities."""

    root: Path

    @property
    def database(self) -> Path:
        """Directory for finance databases, not a database file."""
        return self.root / "database"

    @property
    def attachments(self) -> Path:
        """Directory for future user attachments."""
        return self.root / "attachments"

    @property
    def backups(self) -> Path:
        """Directory for future snapshot backups."""
        return self.root / "backups"

    @property
    def logs(self) -> Path:
        """Directory for rotating diagnostic logs."""
        return self.root / "logs"

    def ensure_directories(self) -> None:
        """Create the known data directories without modifying existing records."""
        try:
            for directory in (self.root, self.database, self.attachments, self.backups, self.logs):
                directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise DataDirectoryError(f"无法创建数据目录：{self.root}") from error


def resolve_app_paths(
    data_directory: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> AppPaths:
    """Use explicit absolute override, or Windows LOCALAPPDATA; never fall back to cwd."""
    environment = os.environ if environ is None else environ
    if data_directory is None:
        local_appdata = environment.get("LOCALAPPDATA")
        if not local_appdata:
            raise DataDirectoryError("未找到 LOCALAPPDATA，请使用 --data-dir 指定绝对路径。")
        data_directory = Path(local_appdata) / "OpenLedger"
    expanded = data_directory.expanduser()
    if not expanded.is_absolute():
        raise DataDirectoryError("数据目录必须是绝对路径。")
    return AppPaths(expanded.resolve())
