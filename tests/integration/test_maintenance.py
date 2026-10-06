"""Exercise the installed entry point's non-GUI maintenance and safe startup failures."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from openledger.infrastructure.database.database import Database
from openledger.infrastructure.ledger import LedgerService

pytestmark = pytest.mark.integration


def _run(tmp_path: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "openledger", *arguments],
        cwd=tmp_path,
        capture_output=True,
        timeout=25,
        check=False,
    )


def test_cli_backup_restore_preserves_source_and_receipts(tmp_path: Path) -> None:
    source = tmp_path / "source"
    database = Database(source / "database" / "openledger.sqlite3")
    database.initialize()
    LedgerService(database).ensure_defaults()
    before = database.path.read_bytes()
    archive = tmp_path / "backup.olbackup"
    result = _run(tmp_path, "--data-dir", str(source), "--backup", str(archive))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "passed"
    restored = tmp_path / "恢复数据 space"
    result = _run(
        tmp_path,
        "--data-dir",
        str(source),
        "--restore",
        str(archive),
        "--restore-to",
        str(restored),
    )
    assert result.returncode == 0, result.stderr
    assert (restored / "database/openledger.sqlite3").is_file()
    assert database.path.read_bytes() == before
    recovered = Database(restored / "database/openledger.sqlite3")
    recovered.initialize()
    with recovered.read() as connection:
        assert connection.execute("SELECT COUNT(*) FROM command_receipts").fetchone()[0] == 11
        assert connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_cli_backup_refuses_existing_target(tmp_path: Path) -> None:
    source = tmp_path / "source"
    database = Database(source / "database/openledger.sqlite3")
    database.initialize()
    archive = tmp_path / "existing.olbackup"
    archive.write_bytes(b"preserve this file")
    result = _run(tmp_path, "--data-dir", str(source), "--backup", str(archive))
    assert result.returncode == 1
    assert b"BACKUP_TARGET_EXISTS" in result.stderr
    assert archive.read_bytes() == b"preserve this file"


@pytest.mark.parametrize(
    "arguments",
    [
        ("--restore", "missing.olbackup"),
        ("--restore-to", "somewhere"),
        ("--smoke-test", "--smoke-report", "test.json", "--backup", "backup.zip"),
    ],
)
def test_invalid_maintenance_arguments_are_rejected_before_data_creation(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    result = _run(tmp_path, *arguments)
    assert result.returncode == 2
    assert not list(tmp_path.iterdir())


def test_future_database_startup_fails_without_modification(tmp_path: Path) -> None:
    source = tmp_path / "source"
    database = Database(source / "database/openledger.sqlite3")
    database.initialize()
    with database.write() as connection:
        connection.execute("PRAGMA user_version=3")
    before = hashlib.sha256(database.path.read_bytes()).hexdigest()
    report = tmp_path / "failure.json"
    result = _run(
        tmp_path, "--data-dir", str(source), "--smoke-test", "--smoke-report", str(report)
    )
    assert result.returncode == 1
    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "failed"
    assert hashlib.sha256(database.path.read_bytes()).hexdigest() == before


def test_repeated_default_initialization_never_recreates_funds(tmp_path: Path) -> None:
    database = Database(tmp_path / "database.sqlite3")
    database.initialize()
    ledger = LedgerService(database)
    ledger.ensure_defaults()
    before = database.path.read_bytes()
    ledger.ensure_defaults()
    assert database.path.read_bytes() == before
    assert ledger.entities("account") == ()
    assert len(ledger.entities("book")) == 1
