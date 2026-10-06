"""Portable backups through the exact Android JSON boundary."""

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from openledger.infrastructure.backup import BackupService
from openledger.mobile.bridge import MobileLedger
from tests.integration.test_mobile_bridge import NOW, account, digest, invoke, record_body


def test_backup_restores_in_new_directory_and_retries(tmp_path: Path) -> None:
    mobile = MobileLedger(str(tmp_path / "ledger"), clock=lambda: NOW)
    account(mobile)
    assert invoke(mobile, "record", record_body(mobile))["ok"]
    before = digest(mobile)
    created = invoke(mobile, "backup_create", {"filename": "portable.olbackup"})
    assert created["ok"], created
    archive = mobile.files.staging / "portable.olbackup"
    assert created["data"]["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    body = {"filename": archive.name, "request_id": str(uuid4())}
    restored = invoke(mobile, "backup_restore", body)
    assert restored["ok"], restored
    assert invoke(mobile, "backup_restore", body) == restored
    assert digest(mobile) == before
    other = MobileLedger(str(tmp_path / restored["data"]["directory"]), clock=lambda: NOW)
    assert invoke(other, "snapshot")["data"]["overview"]["total_assets_minor"] == 97500
    desktop = BackupService(mobile.database).restore(archive, tmp_path / "desktop-restored")
    with desktop.read() as connection:
        assert connection.execute("SELECT count(*) FROM transactions").fetchone()[0] == 2


@pytest.mark.parametrize(
    "filename", ["../outside.olbackup", "C:/outside.olbackup", "x/y.olbackup", "x.zip"]
)
def test_backup_paths_cannot_escape_staging(tmp_path: Path, filename: str) -> None:
    mobile = MobileLedger(str(tmp_path / "ledger"), clock=lambda: NOW)
    before = digest(mobile)
    assert not invoke(mobile, "backup_create", {"filename": filename})["ok"]
    assert digest(mobile) == before


def test_corrupt_archive_and_reused_restore_id_preserve_active_data(tmp_path: Path) -> None:
    mobile = MobileLedger(str(tmp_path / "ledger"), clock=lambda: NOW)
    account(mobile)
    invoke(mobile, "backup_create", {"filename": "before.olbackup"})
    request = str(uuid4())
    assert invoke(mobile, "backup_restore", {"filename": "before.olbackup", "request_id": request})[
        "ok"
    ]
    assert invoke(mobile, "record", record_body(mobile))["ok"]
    invoke(mobile, "backup_create", {"filename": "after.olbackup"})
    before = digest(mobile)
    reply = invoke(mobile, "backup_restore", {"filename": "after.olbackup", "request_id": request})
    assert reply["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    (mobile.files.staging / "broken.olbackup").write_bytes(b"not a zip")
    assert not invoke(
        mobile, "backup_restore", {"filename": "broken.olbackup", "request_id": str(uuid4())}
    )["ok"]
    assert digest(mobile) == before


def test_confirmed_restore_survives_cache_eviction_and_service_restart(tmp_path: Path) -> None:
    mobile = MobileLedger(str(tmp_path / "ledger"), clock=lambda: NOW)
    account(mobile)
    assert invoke(mobile, "record", record_body(mobile))["ok"]
    assert invoke(mobile, "backup_create", {"filename": "persistent.olbackup"})["ok"]
    before = digest(mobile)
    assert invoke(mobile, "backup_prepare", {"filename": "persistent.olbackup"})["ok"]
    (mobile.files.staging / "persistent.olbackup").unlink()
    mobile = MobileLedger(str(tmp_path / "ledger"), clock=lambda: NOW)
    body = {"filename": "persistent.olbackup", "request_id": str(uuid4())}
    result = invoke(mobile, "backup_restore", body)
    assert result["ok"], result
    assert invoke(mobile, "backup_restore", body) == result
    assert digest(mobile) == before
