"""App-owned staging paths and shared-format backups for the Android boundary."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from uuid import UUID

from openledger.domain.errors import LedgerError
from openledger.infrastructure.backup import BackupService
from openledger.infrastructure.database.database import Database
from openledger.infrastructure.integrity import validate_financial_integrity

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\Z")


class MobileFiles:
    """Keep JSON callers inside an explicit application-owned staging directory."""

    def __init__(self, directory: Path, staging_directory: Path, database: Database) -> None:
        self.directory = directory.resolve()
        if not staging_directory.is_absolute():
            raise LedgerError("INVALID_DATA_DIRECTORY")
        if staging_directory.is_symlink() or staging_directory.is_junction():
            raise LedgerError("INVALID_DATA_DIRECTORY")
        self.staging = staging_directory.resolve()
        self.staging.mkdir(parents=True, exist_ok=True)
        self.database = database

    def path(self, filename: str, *, suffix: str | None = None) -> Path:
        """Resolve a bounded leaf name; paths and symbolic links are never accepted."""
        if not _NAME.fullmatch(filename) or ".." in filename:
            raise LedgerError("INVALID_FILE_NAME")
        if suffix is not None and not filename.lower().endswith(suffix):
            raise LedgerError("INVALID_FILE_FORMAT")
        path = self.staging / filename
        if path.is_symlink() or path.is_junction() or path.resolve().parent != self.staging:
            raise LedgerError("INVALID_FILE_NAME")
        return path

    def backup(self, filename: str) -> dict[str, object]:
        """Export the desktop-compatible consistent backup, including referenced images."""
        path = self.path(filename, suffix=".olbackup")
        BackupService(self.database, publish_file=self._stage_backup).backup(
            path, self.directory / "attachments"
        )
        return self.describe(path)

    @staticmethod
    def _stage_backup(source: Path, destination: Path) -> None:
        """Android forbids hard links. Exclusively stage bytes before any SAF export.

        Only the serialized mobile boundary can read this private cache. Failed
        copies remove their own partial file; an existing file is never replaced.
        """
        try:
            output = destination.open("xb")
        except FileExistsError as error:
            raise LedgerError("BACKUP_TARGET_EXISTS") from error
        try:
            with output, source.open("rb") as incoming:
                shutil.copyfileobj(incoming, output, 64 * 1024)
                output.flush()
                os.fsync(output.fileno())
        except BaseException:
            destination.unlink(missing_ok=True)
            raise

    def _restore_path(self, filename: str) -> Path:
        self.path(filename, suffix=".olbackup")
        folder = self.directory / "restore-inputs"
        if folder.is_symlink() or folder.is_junction():
            raise LedgerError("INVALID_FILE_NAME")
        folder.mkdir(exist_ok=True)
        path = folder / filename
        if path.is_symlink() or path.is_junction():
            raise LedgerError("INVALID_FILE_NAME")
        return path

    def prepare_restore(self, filename: str) -> dict[str, object]:
        """Preserve selected bytes before confirmation, independent of disposable cache."""
        source = self.path(filename, suffix=".olbackup")
        if source.stat().st_size > 1_140_850_688:
            raise LedgerError("BACKUP_LIMIT_EXCEEDED")
        destination = self._restore_path(filename)
        if destination.exists():
            if self.describe(source)["sha256"] != self.describe(destination)["sha256"]:
                raise LedgerError("IDEMPOTENCY_KEY_REUSED")
        else:
            self._stage_backup(source, destination)
        return self.describe(destination)

    def restore(self, filename: str, request_id: str) -> dict[str, object]:
        """Restore only to a fresh sibling directory, leaving the active ledger intact."""
        try:
            identifier = str(UUID(request_id))
        except (ValueError, TypeError, AttributeError) as error:
            raise LedgerError("INVALID_ID") from error
        preserved = self._restore_path(filename)
        archive = preserved if preserved.is_file() else self.path(filename, suffix=".olbackup")
        destination = self.directory.parent / f"restored-{identifier}"
        if destination.is_symlink() or destination.is_junction():
            raise LedgerError("INVALID_DATA_DIRECTORY")
        if destination.exists():
            receipt = destination / "restore-receipt.json"
            if not receipt.is_file() or receipt.stat().st_size > 8192:
                raise LedgerError("IDEMPOTENCY_KEY_REUSED")
            saved = json.loads(receipt.read_text(encoding="utf-8"))
            if (
                not isinstance(saved, dict)
                or saved.get("backup_sha256") != self.describe(archive)["sha256"]
            ):
                raise LedgerError("IDEMPOTENCY_KEY_REUSED")
            restored = Database(destination / "database" / "openledger.sqlite3")
        else:
            restored = BackupService(self.database).restore(archive, destination)
        with restored.read() as connection:
            validate_financial_integrity(connection)
            transactions = int(
                connection.execute("SELECT count(*) FROM transactions").fetchone()[0]
            )
            accounts = int(connection.execute("SELECT count(*) FROM accounts").fetchone()[0])
        return {
            "directory": destination.name,
            "transactions": transactions,
            "accounts": accounts,
            "source_directory_preserved": True,
        }

    @staticmethod
    def describe(path: Path) -> dict[str, object]:
        """Expose a leaf name and checksum without exposing application-private paths."""
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        return {"filename": path.name, "bytes": path.stat().st_size, "sha256": digest}
