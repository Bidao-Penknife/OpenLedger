"""Verify local backup fidelity and refusal of unsafe or damaged archives."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import zipfile
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

import openledger.infrastructure.backup as backup_module
from openledger.domain.errors import LedgerError
from openledger.infrastructure.backup import BackupLimits, BackupService
from openledger.infrastructure.database.database import Database

pytestmark = pytest.mark.integration
_STAMP = "2026-10-02T00:00:00.000Z"


@pytest.fixture
def source_database(tmp_path: Path) -> Database:
    """Create synthetic ledger records; never access a user's application data."""
    database = Database(tmp_path / "source" / "database" / "openledger.sqlite3")
    database.initialize()
    account_id, transaction_id, request_id = str(uuid4()), str(uuid4()), str(uuid4())
    audit_id = str(uuid4())
    with database.write() as connection:
        connection.execute(
            "INSERT INTO accounts(id, name, account_type, balance_start_on, "
            "created_at_utc, updated_at_utc) VALUES (?, ?, 'cash', '2026-10-02', ?, ?)",
            (account_id, "测试现金", _STAMP, _STAMP),
        )
        connection.execute(
            "UPDATE app_preferences SET default_account_id=? WHERE singleton=1", (account_id,)
        )
        connection.execute(
            "INSERT INTO transactions(id, kind, amount_minor, occurred_on, time_zone, "
            "created_at_utc, updated_at_utc) "
            "VALUES (?, 'opening', 12345, '2026-10-02', 'Asia/Shanghai', ?, ?)",
            (transaction_id, _STAMP, _STAMP),
        )
        connection.execute(
            "INSERT INTO account_entries(id, transaction_id, account_id, delta_minor) "
            "VALUES (?, ?, ?, 12345)",
            (str(uuid4()), transaction_id, account_id),
        )
        connection.execute(
            "INSERT INTO command_receipts(request_id, command_type, payload_hash, "
            "result_json, completed_at_utc) VALUES (?, 'fixture', ?, '{}', ?)",
            (request_id, "0" * 64, _STAMP),
        )
        connection.execute(
            "INSERT INTO audit_events(id, request_id, entity_type, entity_id, action, "
            "new_version, after_json, created_at_utc) "
            "VALUES (?, ?, 'account', ?, 'create', 1, '{}', ?)",
            (audit_id, request_id, account_id, _STAMP),
        )
        connection.execute(
            "INSERT INTO change_log(audit_event_id, entity_type, entity_id, version, "
            "operation, changed_at_utc) VALUES (?, 'account', ?, 1, 'create', ?)",
            (audit_id, account_id, _STAMP),
        )
    return database


def _rows(database: Database, table: str) -> list[tuple[object, ...]]:
    assert table in {
        "accounts",
        "transactions",
        "account_entries",
        "command_receipts",
        "audit_events",
        "change_log",
    }
    with database.read() as connection:
        return [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")]


def _add_attachment(database: Database, root: Path, *, deleted: bool = False) -> Path:
    content = b"synthetic receipt bytes\n"
    path = root / "2026" / "receipt.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    with database.write() as connection:
        transaction_id = connection.execute("SELECT id FROM transactions").fetchone()[0]
        connection.execute(
            "INSERT INTO attachments(id, transaction_id, relative_path, original_file_name, "
            "mime_type, size_bytes, sha256, created_at_utc, updated_at_utc, deleted_at_utc) "
            "VALUES (?, ?, '2026/receipt.txt', 'receipt.txt', 'text/plain', ?, ?, ?, ?, ?)",
            (
                str(uuid4()),
                transaction_id,
                len(content),
                hashlib.sha256(content).hexdigest(),
                _STAMP,
                _STAMP,
                _STAMP if deleted else None,
            ),
        )
    return path


def _alter_archive(
    source: Path,
    destination: Path,
    transform: Callable[[dict[str, bytes]], None],
) -> Path:
    with zipfile.ZipFile(source) as archive:
        files = {entry.filename: archive.read(entry) for entry in archive.infolist()}
    transform(files)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return destination


def _manifest(files: dict[str, bytes]) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(files["manifest.json"])
    return result


def _write_manifest(files: dict[str, bytes], manifest: dict[str, Any]) -> None:
    files["manifest.json"] = json.dumps(manifest).encode("utf-8")


def _replace_database(files: dict[str, bytes], content: bytes) -> None:
    files["database.sqlite3"] = content
    manifest = _manifest(files)
    manifest["database_sha256"] = hashlib.sha256(content).hexdigest()
    manifest["database_size_bytes"] = len(content)
    _write_manifest(files, manifest)


def test_round_trip_preserves_funds_receipts_audit_and_source(
    source_database: Database,
    tmp_path: Path,
) -> None:
    before = source_database.path.read_bytes()
    service = BackupService(source_database)
    archive = service.backup(tmp_path / "中文 备份.olbackup")
    restored = service.restore(archive, tmp_path / "恢复 data")
    for table in (
        "accounts",
        "transactions",
        "account_entries",
        "command_receipts",
        "audit_events",
        "change_log",
    ):
        assert _rows(restored, table) == _rows(source_database, table)
    with restored.read() as connection:
        assert (
            connection.execute("SELECT SUM(delta_minor) FROM account_entries").fetchone()[0]
            == 12345
        )
    assert source_database.path.read_bytes() == before
    receipt = json.loads((tmp_path / "恢复 data" / "restore-receipt.json").read_text("utf-8"))
    assert receipt["backup_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert receipt["schema_version"] == 2
    assert not list(tmp_path.glob(".openledger-*"))


def test_online_backup_excludes_uncommitted_write(
    source_database: Database,
    tmp_path: Path,
) -> None:
    service = BackupService(source_database)
    with source_database.write() as writer:
        writer.execute("UPDATE accounts SET name = '未提交名称'")
        archive = service.backup(tmp_path / "snapshot.olbackup")
        writer.rollback()
    restored = service.restore(archive, tmp_path / "restored")
    with restored.read() as connection:
        assert connection.execute("SELECT name FROM accounts").fetchone()[0] == "测试现金"


@pytest.mark.parametrize("deleted", [False, True])
def test_only_referenced_attachments_are_backed_up(
    source_database: Database,
    tmp_path: Path,
    deleted: bool,
) -> None:
    root = tmp_path / "source" / "attachments"
    referenced = _add_attachment(source_database, root, deleted=deleted)
    (root / "unrelated-private.txt").write_bytes(b"must not be included")
    service = BackupService(source_database)
    archive = service.backup(tmp_path / "with-attachment.olbackup", root)
    with zipfile.ZipFile(archive) as zipped:
        assert set(zipped.namelist()) == {
            "manifest.json",
            "database.sqlite3",
            "attachments/2026/receipt.txt",
        }
    service.restore(archive, tmp_path / "restored")
    assert (tmp_path / "restored" / "attachments" / "2026" / "receipt.txt").read_bytes() == (
        referenced.read_bytes()
    )


@pytest.mark.parametrize("fault", ["not-provided", "missing", "wrong-hash"])
def test_attachment_failure_never_publishes_partial_archive(
    source_database: Database,
    tmp_path: Path,
    fault: str,
) -> None:
    root = tmp_path / "attachments"
    referenced = _add_attachment(source_database, root)
    if fault == "missing":
        referenced.unlink()
    elif fault == "wrong-hash":
        referenced.write_bytes(b"x" * referenced.stat().st_size)
    output = tmp_path / "failed.olbackup"
    with pytest.raises(LedgerError):
        BackupService(source_database).backup(output, None if fault == "not-provided" else root)
    assert not output.exists()
    assert not list(tmp_path.glob(".openledger-*"))


def test_backup_refuses_existing_file(source_database: Database, tmp_path: Path) -> None:
    existing = tmp_path / "existing.olbackup"
    existing.write_bytes(b"previous backup")
    with pytest.raises(LedgerError, match="备份目标已存在") as captured:
        BackupService(source_database).backup(existing)
    assert captured.value.code == "BACKUP_TARGET_EXISTS"
    assert existing.read_bytes() == b"previous backup"


def test_backup_write_failure_keeps_source_and_removes_temporary_files(
    source_database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = source_database.path.read_bytes()

    def failed_fsync(descriptor: int) -> None:
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(os, "fsync", failed_fsync)
    with pytest.raises(LedgerError) as captured:
        BackupService(source_database).backup(tmp_path / "failed.olbackup")
    assert captured.value.code == "BACKUP_FAILED"
    assert source_database.path.read_bytes() == before
    assert not list(tmp_path.glob(".openledger-*"))
    assert not (tmp_path / "failed.olbackup").exists()


def test_restore_refuses_nonempty_directory(source_database: Database, tmp_path: Path) -> None:
    destination = tmp_path / "existing-data"
    destination.mkdir()
    sentinel = destination / "keep.txt"
    sentinel.write_bytes(b"existing user data")
    with pytest.raises(LedgerError) as captured:
        BackupService(source_database).restore(tmp_path / "absent.olbackup", destination)
    assert captured.value.code == "BACKUP_TARGET_EXISTS"
    assert sentinel.read_bytes() == b"existing user data"


def test_restore_accepts_empty_directory(source_database: Database, tmp_path: Path) -> None:
    service = BackupService(source_database)
    archive = service.backup(tmp_path / "valid.olbackup")
    destination = tmp_path / "empty"
    destination.mkdir()
    result = service.restore(archive, destination)
    assert result.path == destination / "database" / "openledger.sqlite3"


@pytest.mark.parametrize(
    "name",
    [
        "../escape.txt",
        "/absolute.txt",
        "attachments/../escape.txt",
        "attachments\\escape.txt",
        "attachments/CON.txt",
        "attachments/path./file.txt",
        "attachments/COM¹.txt",
    ],
)
def test_restore_rejects_zip_slip_and_windows_unsafe_paths(
    source_database: Database,
    tmp_path: Path,
    name: str,
) -> None:
    service = BackupService(source_database)
    valid = service.backup(tmp_path / "valid.olbackup")
    damaged = _alter_archive(
        valid, tmp_path / "unsafe.olbackup", lambda files: files.update({name: b"x"})
    )
    with pytest.raises(LedgerError):
        service.restore(damaged, tmp_path / "restored")
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "restored").exists()
    assert not list(tmp_path.glob(".openledger-*"))


@pytest.mark.parametrize("duplicate", ["manifest.json", "MANIFEST.JSON"])
def test_restore_rejects_duplicate_names(
    source_database: Database,
    tmp_path: Path,
    duplicate: str,
) -> None:
    service = BackupService(source_database)
    archive = service.backup(tmp_path / "valid.olbackup")
    with zipfile.ZipFile(archive, "a") as zipped:
        if duplicate == "manifest.json":
            with pytest.warns(UserWarning, match="Duplicate name"):
                zipped.writestr(duplicate, b"{}")
        else:
            zipped.writestr(duplicate, b"{}")
    with pytest.raises(LedgerError):
        service.restore(archive, tmp_path / "restored")


def test_restore_rejects_symlink_entry(source_database: Database, tmp_path: Path) -> None:
    service = BackupService(source_database)
    archive = service.backup(tmp_path / "valid.olbackup")
    symlink = zipfile.ZipInfo("attachments/link")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "a") as zipped:
        zipped.writestr(symlink, "../../outside")
    with pytest.raises(LedgerError):
        service.restore(archive, tmp_path / "restored")


@pytest.mark.parametrize("field", ["format_version", "schema_version"])
def test_restore_rejects_future_versions(
    source_database: Database,
    tmp_path: Path,
    field: str,
) -> None:
    service = BackupService(source_database)
    archive = service.backup(tmp_path / "valid.olbackup")

    def future_version(files: dict[str, bytes]) -> None:
        manifest = _manifest(files)
        manifest[field] = 999
        _write_manifest(files, manifest)

    damaged = _alter_archive(archive, tmp_path / "future.olbackup", future_version)
    with pytest.raises(LedgerError):
        service.restore(damaged, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_restore_rejects_database_digest_mismatch(
    source_database: Database, tmp_path: Path
) -> None:
    service = BackupService(source_database)
    archive = service.backup(tmp_path / "valid.olbackup")

    def change_bytes(files: dict[str, bytes]) -> None:
        content = files["database.sqlite3"]
        files["database.sqlite3"] = b"bad" + content[3:]

    damaged = _alter_archive(archive, tmp_path / "damaged.olbackup", change_bytes)
    with pytest.raises(LedgerError):
        service.restore(damaged, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize(
    "fault", ["application-id", "user-version", "migration-digest", "foreign-key"]
)
def test_restore_validates_database_after_archive_digest(
    source_database: Database,
    tmp_path: Path,
    fault: str,
) -> None:
    service = BackupService(source_database)
    archive = service.backup(tmp_path / "valid.olbackup")

    def corrupt_database(files: dict[str, bytes]) -> None:
        path = tmp_path / "corrupt.sqlite3"
        path.write_bytes(files["database.sqlite3"])
        with closing(sqlite3.connect(path)) as connection:
            if fault == "application-id":
                connection.execute("PRAGMA application_id = 0")
            elif fault == "user-version":
                connection.execute("PRAGMA user_version = 99")
            elif fault == "migration-digest":
                connection.execute("UPDATE schema_migrations SET sha256 = ?", ("0" * 64,))
            else:
                connection.execute(
                    "INSERT INTO account_entries(id, transaction_id, account_id, delta_minor) "
                    "VALUES (?, ?, ?, 1)",
                    (str(uuid4()), str(uuid4()), str(uuid4())),
                )
            connection.commit()
        _replace_database(files, path.read_bytes())

    damaged = _alter_archive(archive, tmp_path / "invalid-database.olbackup", corrupt_database)
    before = source_database.path.read_bytes()
    with pytest.raises(LedgerError):
        service.restore(damaged, tmp_path / "restored")
    assert source_database.path.read_bytes() == before
    assert not (tmp_path / "restored").exists()
    assert not list(tmp_path.glob(".openledger-*"))


def test_restore_rejects_attachment_digest_mismatch(
    source_database: Database, tmp_path: Path
) -> None:
    root = tmp_path / "attachments"
    _add_attachment(source_database, root)
    service = BackupService(source_database)
    archive = service.backup(tmp_path / "valid.olbackup", root)

    def alter_attachment(files: dict[str, bytes]) -> None:
        name = "attachments/2026/receipt.txt"
        files[name] = b"x" * len(files[name])

    damaged = _alter_archive(archive, tmp_path / "wrong-attachment.olbackup", alter_attachment)
    with pytest.raises(LedgerError):
        service.restore(damaged, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_restore_requires_every_database_attachment(
    source_database: Database, tmp_path: Path
) -> None:
    root = tmp_path / "attachments"
    _add_attachment(source_database, root)
    service = BackupService(source_database)
    archive = service.backup(tmp_path / "valid.olbackup", root)

    def remove_reference(files: dict[str, bytes]) -> None:
        files.pop("attachments/2026/receipt.txt")
        manifest = _manifest(files)
        manifest["attachments"] = []
        _write_manifest(files, manifest)

    damaged = _alter_archive(archive, tmp_path / "missing-attachment.olbackup", remove_reference)
    with pytest.raises(LedgerError):
        service.restore(damaged, tmp_path / "restored")


def test_restore_bounds_decompression(source_database: Database, tmp_path: Path) -> None:
    service = BackupService(source_database)
    archive = service.backup(tmp_path / "valid.olbackup")
    with zipfile.ZipFile(archive, "a", compression=zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr("attachments/bomb.bin", b"x" * 4096)
    limited = BackupService(source_database, limits=BackupLimits(attachment_bytes=1024))
    with pytest.raises(LedgerError, match="大小限制"):
        limited.restore(archive, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_backup_bounds_database_size(source_database: Database, tmp_path: Path) -> None:
    limited = BackupService(source_database, limits=BackupLimits(database_bytes=1024))
    with pytest.raises(LedgerError):
        limited.backup(tmp_path / "too-large.olbackup")
    assert not (tmp_path / "too-large.olbackup").exists()


def test_backup_rejects_attachment_directory_link(
    source_database: Database, tmp_path: Path
) -> None:
    root = tmp_path / "real-attachments"
    _add_attachment(source_database, root)
    linked = tmp_path / "linked-attachments"
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(linked), str(root)],
            capture_output=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, "Synthetic directory junction creation failed"
    else:
        linked.symlink_to(root, target_is_directory=True)
    try:
        with pytest.raises(LedgerError) as captured:
            BackupService(source_database).backup(tmp_path / "link.olbackup", linked)
        assert captured.value.code == "BACKUP_INVALID_PATH"
        assert not (tmp_path / "link.olbackup").exists()
    finally:
        if os.name == "nt":
            linked.rmdir()
        else:
            linked.unlink()


def test_restore_rejects_duplicate_manifest_keys(source_database: Database, tmp_path: Path) -> None:
    service = BackupService(source_database)
    archive = service.backup(tmp_path / "valid.olbackup")
    damaged = _alter_archive(
        archive,
        tmp_path / "duplicate-fields.olbackup",
        lambda files: files.update(
            {"manifest.json": b'{"format_version": 1, "format_version": 1}'}
        ),
    )
    with pytest.raises(LedgerError):
        service.restore(damaged, tmp_path / "restored")


def test_backup_atomic_publication_does_not_replace_a_concurrent_target(
    source_database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_publish = backup_module._publish_file
    destination = tmp_path / "race.olbackup"

    def create_concurrent_target(staging: Path, output: Path) -> None:
        output.write_bytes(b"concurrently created backup")
        original_publish(staging, output)

    monkeypatch.setattr(backup_module, "_publish_file", create_concurrent_target)
    with pytest.raises(LedgerError) as captured:
        BackupService(source_database).backup(destination)
    assert captured.value.code == "BACKUP_TARGET_EXISTS"
    assert destination.read_bytes() == b"concurrently created backup"
    assert not list(tmp_path.glob(".openledger-*"))


@pytest.mark.parametrize("preexisting", [False, True])
def test_restore_publication_failure_keeps_existing_data(
    source_database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preexisting: bool,
) -> None:
    service = BackupService(source_database)
    archive = service.backup(tmp_path / "valid.olbackup")
    destination = tmp_path / "restore-target"
    if preexisting:
        destination.mkdir()
    original_rename = Path.rename

    def fail_directory_rename(self: Path, target: str | Path) -> Path:
        if self.name.startswith(".openledger-restore-"):
            raise PermissionError("synthetic publication failure")
        return original_rename(self, target)

    before = source_database.path.read_bytes()
    monkeypatch.setattr(Path, "rename", fail_directory_rename)
    with pytest.raises(LedgerError):
        service.restore(archive, destination)
    assert destination.exists() is preexisting
    if preexisting:
        assert list(destination.iterdir()) == []
    assert source_database.path.read_bytes() == before
    assert not list(tmp_path.glob(".openledger-*"))


@pytest.mark.parametrize("limit", ["total", "entries"])
def test_restore_bounds_total_size_and_entry_count(
    source_database: Database,
    tmp_path: Path,
    limit: str,
) -> None:
    archive = BackupService(source_database).backup(tmp_path / "valid.olbackup")
    with zipfile.ZipFile(archive) as zipped:
        total = sum(entry.file_size for entry in zipped.infolist())
    limits = BackupLimits(total_bytes=total - 1) if limit == "total" else BackupLimits(entries=1)
    with pytest.raises(LedgerError):
        BackupService(source_database, limits=limits).restore(archive, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_backup_missing_database_does_not_initialize_it(tmp_path: Path) -> None:
    database = Database(tmp_path / "missing.sqlite3")
    with pytest.raises(LedgerError) as captured:
        BackupService(database).backup(tmp_path / "backup.olbackup")
    assert captured.value.code == "BACKUP_SOURCE_INVALID"
    assert not database.path.exists()


def test_restore_rejects_non_sqlite_payload_with_matching_digest(
    source_database: Database,
    tmp_path: Path,
) -> None:
    service = BackupService(source_database)
    archive = service.backup(tmp_path / "valid.olbackup")
    damaged = _alter_archive(
        archive,
        tmp_path / "not-sqlite.olbackup",
        lambda files: _replace_database(files, b"synthetic invalid SQLite payload"),
    )
    with pytest.raises(LedgerError):
        service.restore(damaged, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()
