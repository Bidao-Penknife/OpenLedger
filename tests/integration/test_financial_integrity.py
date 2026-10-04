"""Reject SQL-valid damaged financial aggregates before opening or restoring."""

from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from openledger.application.dto.ledger import RefundFields, TransactionFields, TransferFields
from openledger.domain.errors import LedgerError
from openledger.infrastructure.backup import BackupService
from openledger.infrastructure.integrity import validate_financial_integrity
from openledger.infrastructure.ledger import LedgerService

pytestmark = pytest.mark.integration


def _references(ledger: LedgerService) -> tuple[str, str, str, str]:
    accounts = ledger.entities("account")
    cash = str(next(row["id"] for row in accounts if row["account_type"] == "cash"))
    bank = str(next(row["id"] for row in accounts if row["account_type"] == "bank"))
    book = str(ledger.entities("book")[0]["id"])
    category = str(
        next(
            row["id"] for row in ledger.entities("category") if row["transaction_kind"] == "expense"
        )
    )
    return cash, bank, book, category


def _expense(ledger: LedgerService, *, bank: bool = False) -> str:
    cash, other, book, category = _references(ledger)
    identifier = str(uuid4())
    item = TransactionFields(
        "expense", 1000, other if bank else cash, book, category, date(2026, 10, 2)
    )
    ledger.record(item, request_id=str(uuid4()), transaction_id=identifier)
    return identifier


def _check(ledger: LedgerService) -> None:
    with ledger.database.read() as connection:
        validate_financial_integrity(connection)


def test_valid_funds_round_trip_passes_semantic_validation(
    ledger: LedgerService, tmp_path: Path
) -> None:
    _expense(ledger)
    _check(ledger)
    archive = BackupService(ledger.database).backup(tmp_path / "valid.olbackup")
    restored = BackupService(ledger.database).restore(archive, tmp_path / "restored")
    with restored.read() as connection:
        validate_financial_integrity(connection)


@pytest.mark.parametrize(
    "damage", ["sign", "calendar", "utc_calendar", "default", "uuid", "cutpoint"]
)
def test_sql_valid_damage_is_rejected(ledger: LedgerService, damage: str) -> None:
    identifier = _expense(ledger, bank=True)
    _, bank, book, _ = _references(ledger)
    with ledger.database.write() as connection:
        if damage == "sign":
            connection.execute(
                "UPDATE account_entries SET delta_minor=1000 WHERE transaction_id=?", (identifier,)
            )
        elif damage == "calendar":
            connection.execute(
                "UPDATE transactions SET occurred_on='2026-02-31' WHERE id=?", (identifier,)
            )
        elif damage == "utc_calendar":
            connection.execute(
                "UPDATE transactions SET created_at_utc='2026-02-31T00:00:00.000Z' WHERE id=?",
                (identifier,),
            )
        elif damage == "default":
            connection.execute("UPDATE books SET is_archived=1 WHERE id=?", (book,))
        elif damage == "uuid":
            connection.execute(
                "INSERT INTO tags(id,name,created_at_utc,updated_at_utc) VALUES(?,?,?,?)",
                (
                    str(uuid4()).upper(),
                    "broken-id",
                    "2026-10-02T00:00:00.000Z",
                    "2026-10-02T00:00:00.000Z",
                ),
            )
        else:
            connection.execute(
                "UPDATE accounts SET balance_start_on='2026-10-03' WHERE id=?", (bank,)
            )
    with pytest.raises(LedgerError, match="INTEGRITY_FAILED"):
        _check(ledger)


def test_one_sided_transfer_blocks_backup(ledger: LedgerService, tmp_path: Path) -> None:
    cash, bank, _, _ = _references(ledger)
    identifier = str(uuid4())
    ledger.transfer(
        TransferFields(cash, bank, 100, date(2026, 10, 2)),
        request_id=str(uuid4()),
        transaction_id=identifier,
    )
    with ledger.database.write() as connection:
        connection.execute(
            "DELETE FROM account_entries WHERE transaction_id=? AND account_id=?",
            (identifier, bank),
        )
    with pytest.raises(LedgerError, match="INTEGRITY_FAILED"):
        BackupService(ledger.database).backup(tmp_path / "invalid.olbackup")
    assert not (tmp_path / "invalid.olbackup").exists()


def test_sql_valid_refund_overflow_is_detected(ledger: LedgerService) -> None:
    original = _expense(ledger)
    cash, _, _, _ = _references(ledger)
    identifier = str(uuid4())
    ledger.refund(
        RefundFields(original, 100, cash, date(2026, 10, 2)),
        request_id=str(uuid4()),
        transaction_id=identifier,
    )
    with ledger.database.write() as connection:
        connection.execute("UPDATE transactions SET amount_minor=1001 WHERE id=?", (identifier,))
        connection.execute(
            "UPDATE account_entries SET delta_minor=1001 WHERE transaction_id=?", (identifier,)
        )
    with pytest.raises(LedgerError, match="INTEGRITY_FAILED"):
        _check(ledger)


def test_deleted_event_can_precede_later_cutpoint(ledger: LedgerService) -> None:
    identifier = _expense(ledger, bank=True)
    ledger.execute(str(uuid4()), "transaction.delete.v1", {"id": identifier, "expected_version": 1})
    _, bank, _, _ = _references(ledger)
    with ledger.database.write() as connection:
        connection.execute("UPDATE accounts SET balance_start_on='2026-10-03' WHERE id=?", (bank,))
    _check(ledger)


def test_millisecond_normalization_precedes_future_validation_and_hash(
    ledger: LedgerService,
) -> None:
    cash, _, book, category = _references(ledger)
    now = datetime(2026, 10, 2, 12, tzinfo=UTC)
    item = TransactionFields(
        "expense",
        100,
        cash,
        book,
        category,
        now.date(),
        occurrence_precision="exact",
        occurred_at_utc=now + timedelta(microseconds=1),
    )
    identifier, request = str(uuid4()), str(uuid4())
    first = ledger.record(item, request_id=request, transaction_id=identifier)
    replay = ledger.record(
        replace(item, occurred_at_utc=now), request_id=request, transaction_id=identifier
    )
    assert replay.replayed and replace(replay, replayed=False) == first
    assert ledger.transaction(identifier)["occurred_at_utc"] == "2026-10-02T12:00:00.000Z"


def test_account_cutpoint_uses_injected_business_timezone(ledger: LedgerService) -> None:
    service = LedgerService(
        ledger.database,
        clock=lambda: datetime(2026, 10, 2, 18, tzinfo=UTC),
        time_zone="Asia/Shanghai",
    )
    identifier = str(uuid4())
    service.execute(
        str(uuid4()),
        "account.create.v1",
        {
            "id": identifier,
            "name": "时区账户",
            "account_type": "custom",
            "balance_start_on": date(2026, 10, 3),
            "opening_balance_minor": 1,
        },
    )
    with ledger.database.read() as connection:
        assert (
            connection.execute(
                "SELECT time_zone FROM transactions t JOIN account_entries e "
                "ON e.transaction_id=t.id WHERE e.account_id=?",
                (identifier,),
            ).fetchone()[0]
            == "Asia/Shanghai"
        )


def test_adjustment_financial_data_cannot_be_changed_through_generic_update(
    ledger: LedgerService,
) -> None:
    cash, _, book, category = _references(ledger)
    result = ledger.execute(
        str(uuid4()),
        "account.adjust.v1",
        {
            "account_id": cash,
            "target_balance_minor": 100001,
            "occurred_on": date(2026, 10, 2),
            "reason": "test",
        },
    )
    item = TransactionFields("expense", 10, cash, book, category, date(2026, 10, 2))
    with pytest.raises(LedgerError, match="ADJUSTMENT_FINANCIAL_FIELDS_IMMUTABLE"):
        ledger.execute(
            str(uuid4()),
            "transaction.update.v1",
            {"id": result.data["id"], "expected_version": 1, "fields": asdict(item)},
        )


def test_child_cannot_be_unarchived_under_archived_parent(ledger: LedgerService) -> None:
    parent, child = str(uuid4()), str(uuid4())
    for identifier, parent_id in [(parent, None), (child, parent)]:
        ledger.execute(
            str(uuid4()),
            "category.create.v1",
            {
                "id": identifier,
                "name": identifier,
                "kind": "expense",
                "parent_id": parent_id,
            },
        )
    for identifier in [child, parent]:
        ledger.execute(
            str(uuid4()),
            "category.archive.v1",
            {
                "id": identifier,
                "expected_version": 1,
                "archived": True,
            },
        )
    with pytest.raises(LedgerError, match="ENTITY_ARCHIVED"):
        ledger.execute(
            str(uuid4()),
            "category.archive.v1",
            {
                "id": child,
                "expected_version": 2,
                "archived": False,
            },
        )


def test_new_account_requires_an_explicit_opening_balance(ledger: LedgerService) -> None:
    before = ledger.entities("account")
    with pytest.raises(LedgerError, match="MISSING_REQUIRED_FIELD"):
        ledger.execute(
            str(uuid4()),
            "account.create.v1",
            {
                "id": str(uuid4()),
                "name": "未确认余额",
                "account_type": "cash",
                "balance_start_on": date(2026, 10, 2),
            },
        )
    assert ledger.entities("account") == before
