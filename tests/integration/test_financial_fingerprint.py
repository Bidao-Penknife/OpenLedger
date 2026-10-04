"""Logical backup equality must preserve metadata beyond transaction counts."""

from pathlib import Path
from uuid import uuid4

from openledger.infrastructure.backup import BackupService
from openledger.infrastructure.ledger import LedgerService
from openledger.presentation.feature_smoke import financial_fingerprint


def test_backup_restoration_has_identical_logical_rows(
    ledger: LedgerService, tmp_path: Path
) -> None:
    with ledger.database.read() as connection:
        before = financial_fingerprint(connection)
    service = BackupService(ledger.database)
    archive = service.backup(tmp_path / "snapshot.olbackup")
    restored = service.restore(archive, tmp_path / "restored")
    with restored.read() as connection:
        assert financial_fingerprint(connection) == before
    with ledger.database.read() as connection:
        assert financial_fingerprint(connection) == before


def test_metadata_change_is_detected_even_without_new_transaction(ledger: LedgerService) -> None:
    with ledger.database.read() as connection:
        before = financial_fingerprint(connection)
    account = ledger.entities("account")[0]
    ledger.execute(
        str(uuid4()),
        "account.update.v1",
        {
            "id": account["id"],
            "expected_version": account["version"],
            "name": "新的合成账户名",
            "account_type": account["account_type"],
        },
    )
    with ledger.database.read() as connection:
        assert financial_fingerprint(connection) != before
