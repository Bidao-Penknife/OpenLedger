"""Atomic import batches reuse the financial invariants, receipts and backup boundary."""

import hashlib
import json
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from openledger.application.dto.ledger import RefundFields, TransactionFields, TransferFields
from openledger.domain.errors import LedgerError
from openledger.infrastructure.backup import BackupService
from openledger.infrastructure.integrity import validate_financial_integrity
from openledger.infrastructure.ledger import LedgerService

pytestmark = pytest.mark.integration
TODAY = date(2026, 10, 2)


def uid() -> str:
    return str(uuid4())


def refs(ledger: LedgerService) -> dict[str, str]:
    return {
        "cash": str(
            next(row["id"] for row in ledger.entities("account") if row["account_type"] == "cash")
        ),
        "bank": str(
            next(row["id"] for row in ledger.entities("account") if row["account_type"] == "bank")
        ),
        "book": str(ledger.entities("book")[0]["id"]),
        **{
            kind: str(
                next(
                    row["id"]
                    for row in ledger.entities("category")
                    if row["transaction_kind"] == kind
                )
            )
            for kind in ("income", "expense")
        },
    }


def fields(ledger: LedgerService, amount: int = 3_000, kind: str = "expense") -> dict[str, object]:
    r = refs(ledger)
    return asdict(TransactionFields(kind, amount, r["cash"], r["book"], r[kind], TODAY))


def row(
    value: dict[str, object], source_row: int = 2, identifier: str | None = None
) -> dict[str, object]:
    return {"id": identifier or uid(), "source_row_number": source_row, "fields": value}


def batch(
    rows: list[dict[str, object]],
    *,
    mapping: Mapping[str, object] | None = None,
    source: str = "sample",
) -> dict[str, object]:
    encoded = json.dumps(
        mapping or {"amount": "金额", "date": "日期"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "id": uid(),
        "source_format": "csv",
        "source_file_name": "测试账单.csv",
        "file_digest": hashlib.sha256(source.encode()).hexdigest(),
        "mapping_hash": hashlib.sha256(encoded.encode()).hexdigest(),
        "mapping_json": encoded,
        "format_version": 1,
        "source_row_count": len(rows),
        "rows": rows,
    }


def commit(ledger: LedgerService, payload: Mapping[str, object]) -> None:
    ledger.execute(uid(), "import.commit.v1", payload)


def revert(ledger: LedgerService, identifier: str, version: int = 1) -> None:
    ledger.execute(uid(), "import.revert.v1", {"id": identifier, "expected_version": version})


def counts(ledger: LedgerService) -> dict[str, int]:
    with ledger.database.read() as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "transactions",
                "account_entries",
                "transaction_tags",
                "import_batches",
                "audit_events",
                "change_log",
                "command_receipts",
            )
        }


def assert_integrity(ledger: LedgerService) -> None:
    with ledger.database.read() as connection:
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
        validate_financial_integrity(connection)


def all_kinds(ledger: LedgerService) -> dict[str, object]:
    r = refs(ledger)
    expense_id = uid()
    # Refund precedes its expense in the source: dependency ordering is internal.
    refund = asdict(RefundFields(expense_id, 500, r["cash"], TODAY)) | {"kind": "expense_refund"}
    transfer = asdict(TransferFields(r["cash"], r["bank"], 2_000, TODAY)) | {"kind": "transfer"}
    return batch(
        [
            row(refund, 2),
            row(fields(ledger), 3, expense_id),
            row(transfer, 4),
            row(fields(ledger, 10_000, "income"), 5),
        ]
    )


def test_commit_four_kinds_has_metadata_audit_and_one_receipt(ledger: LedgerService) -> None:
    payload = all_kinds(ledger)
    before = counts(ledger)
    result = ledger.execute(uid(), "import.commit.v1", payload)
    r = refs(ledger)
    assert ledger.balances() == {r["cash"]: 105_500, r["bank"]: 2_000}
    assert result.data == {"id": payload["id"], "accepted_row_count": 4}
    assert len(result.changed_entities) == 5
    assert counts(ledger)["command_receipts"] == before["command_receipts"] + 1
    assert ledger.import_batches()[0]["status"] == "committed"
    with ledger.database.read() as connection:
        audit = connection.execute(
            "SELECT after_json FROM audit_events WHERE request_id=? AND entity_type='transaction'",
            (result.request_id,),
        ).fetchall()
        assert len(audit) == 4
        assert all(json.loads(item[0])["import_batch_id"] == payload["id"] for item in audit)
    for imported in cast(list[dict[str, object]], payload["rows"]):
        detail = ledger.transaction(str(imported["id"]))
        assert detail["source"] == "import"
        assert detail["version"] == 1
        assert detail["import_source_row"] == imported["source_row_number"]
    assert_integrity(ledger)


def test_commit_replay_and_changed_intent(ledger: LedgerService) -> None:
    payload = batch([row(fields(ledger))])
    request = uid()
    first = ledger.execute(request, "import.commit.v1", payload)
    before = counts(ledger)
    replay = ledger.execute(request, "import.commit.v1", payload)
    assert replay.replayed and replace(replay, replayed=False) == first
    assert counts(ledger) == before
    with pytest.raises(LedgerError, match="IDEMPOTENCY_KEY_REUSED"):
        ledger.execute(request, "import.commit.v1", payload | {"source_file_name": "changed.csv"})


def test_nested_date_and_millisecond_normalization_matches_replay(ledger: LedgerService) -> None:
    imported = row(
        fields(ledger)
        | {
            "occurrence_precision": "exact",
            "occurred_at_utc": "2026-10-02T11:00:00.001234Z",
        }
    )
    payload, request = batch([imported]), uid()
    first = ledger.execute(request, "import.commit.v1", payload)
    assert ledger.transaction(str(imported["id"]))["occurred_at_utc"] == "2026-10-02T11:00:00.001Z"
    cast(dict[str, object], imported["fields"]).update(
        {
            "occurred_on": "2026-10-02",
            "occurred_at_utc": "2026-10-02T11:00:00.001Z",
        }
    )
    replay = ledger.execute(request, "import.commit.v1", payload)
    assert replay.replayed and replace(replay, replayed=False) == first


@pytest.mark.parametrize(
    "point",
    [
        "after_import_batch",
        "after_transaction",
        "after_entry",
        "after_entries",
        "after_audit",
        "after_import_row",
        "before_receipt",
        "before_commit",
    ],
)
def test_commit_fault_rolls_back_everything_then_same_request_retries(
    ledger: LedgerService, point: str
) -> None:
    def fail(stage: str) -> None:
        if stage == point:
            raise RuntimeError("injected import fault")

    failing = LedgerService(ledger.database, clock=ledger.clock, fault_hook=fail)
    payload, request = all_kinds(ledger), uid()
    before_counts, before_balances = counts(ledger), ledger.balances()
    with pytest.raises(RuntimeError, match="injected import fault"):
        failing.execute(request, "import.commit.v1", payload)
    assert counts(ledger) == before_counts
    assert ledger.balances() == before_balances
    ledger.execute(request, "import.commit.v1", payload)
    assert len(ledger.import_batches()) == 1
    assert_integrity(ledger)


@pytest.mark.parametrize(
    "problem,code",
    [
        ({"occurred_on": "2025-12-31"}, "DATE_BEFORE_BALANCE_START"),
        ({"occurred_on": "2026-10-03"}, "FUTURE_DATE"),
        ({"amount_minor": 0}, "INVALID_AMOUNT"),
        ({"currency_code": "USD"}, "CURRENCY_MISMATCH"),
        ({"kind": "adjustment"}, "UNSUPPORTED_IMPORT_KIND"),
    ],
)
def test_bad_later_row_leaves_no_batch_or_earlier_money(
    ledger: LedgerService, problem: dict[str, object], code: str
) -> None:
    payload = batch([row(fields(ledger), 2), row(fields(ledger) | problem, 3)])
    before = counts(ledger)
    with pytest.raises(LedgerError) as raised:
        commit(ledger, payload)
    assert raised.value.code == code
    assert counts(ledger) == before
    assert ledger.total_assets() == 100_000


def test_refund_limit_and_archived_references_roll_back_all_rows(ledger: LedgerService) -> None:
    payload = all_kinds(ledger)
    rows = cast(list[dict[str, object]], payload["rows"])
    cast(dict[str, object], rows[0]["fields"])["amount_minor"] = 3_001
    with pytest.raises(LedgerError, match="REFUND_LIMIT_EXCEEDED"):
        commit(ledger, payload)
    assert not ledger.import_batches() and ledger.total_assets() == 100_000
    r = refs(ledger)
    ledger.execute(
        uid(), "category.archive.v1", {"id": r["expense"], "expected_version": 1, "archived": True}
    )
    with pytest.raises(LedgerError, match="ENTITY_ARCHIVED"):
        commit(ledger, batch([row(fields(ledger) | {"category_id": r["expense"]})]))
    assert not ledger.import_batches()


def test_file_mapping_row_identity_survives_delete_and_revert(ledger: LedgerService) -> None:
    original = batch([row(fields(ledger))])
    commit(ledger, original)
    revert(ledger, str(original["id"]))
    new_payload = batch([row(fields(ledger))])
    with pytest.raises(LedgerError, match="DUPLICATE_IMPORT"):
        commit(ledger, new_payload)
    # A deliberately changed mapping has a different identity; preview must reveal it.
    changed = batch([row(fields(ledger))], mapping={"amount": "金额2", "date": "日期"})
    commit(ledger, changed)
    assert len(ledger.import_batches()) == 2
    assert_integrity(ledger)


def test_arbitrary_external_identity_survives_soft_delete(ledger: LedgerService) -> None:
    imported = row(fields(ledger)) | {
        "external_source": "wechat:test-account",
        "external_transaction_id": "merchant-order-2026-0001",
    }
    original = batch([imported])
    commit(ledger, original)
    ledger.execute(uid(), "transaction.delete.v1", {"id": imported["id"], "expected_version": 1})
    same_external = row(fields(ledger)) | {
        "external_source": imported["external_source"],
        "external_transaction_id": imported["external_transaction_id"],
    }
    with pytest.raises(LedgerError, match="DUPLICATE_IMPORT"):
        commit(ledger, batch([same_external], source="other-file"))
    assert len(ledger.import_batches()) == 1
    assert_integrity(ledger)


@pytest.mark.parametrize(
    "change",
    [
        "mapping_hash",
        "mapping_json",
        "source_format",
        "format_version",
        "source_file_name",
        "source_row_count",
    ],
)
def test_bad_batch_envelope_is_never_persisted(ledger: LedgerService, change: str) -> None:
    payload = batch([row(fields(ledger))])
    payload[change] = {
        "mapping_hash": "a" * 64,
        "mapping_json": '{"bad": NaN}',
        "source_format": "xls",
        "format_version": 2,
        "source_file_name": "../bill.csv",
        "source_row_count": 0,
    }[change]
    with pytest.raises(LedgerError, match="INVALID_ENVELOPE"):
        commit(ledger, payload)
    assert not ledger.import_batches()


@pytest.mark.parametrize("duplicate", ["id", "source_row_number", "external"])
def test_duplicate_inside_batch_fails_before_any_write(
    ledger: LedgerService, duplicate: str
) -> None:
    first, second = row(fields(ledger), 2), row(fields(ledger), 3)
    if duplicate == "external":
        for item in (first, second):
            item.update({"external_source": "bank", "external_transaction_id": "order-one"})
    else:
        second[duplicate] = first[duplicate]
    before = counts(ledger)
    with pytest.raises(LedgerError, match="DUPLICATE_IMPORT"):
        commit(ledger, batch([first, second]))
    assert counts(ledger) == before


def test_revert_refunds_first_preserves_all_metadata_and_receipt(ledger: LedgerService) -> None:
    payload = all_kinds(ledger)
    commit(ledger, payload)
    request = uid()
    reversal = {"id": payload["id"], "expected_version": 1}
    first = ledger.execute(request, "import.revert.v1", reversal)
    assert ledger.total_assets() == 100_000
    detail = ledger.import_batches()[0]
    assert detail["status"] == "reverted" and detail["version"] == 2
    for item in cast(list[dict[str, object]], payload["rows"]):
        transaction = ledger.transaction(str(item["id"]))
        assert transaction["version"] == 2 and transaction["deleted_at_utc"] is not None
        assert transaction["import_batch_id"] == payload["id"]
        with pytest.raises(LedgerError, match="IMPORT_BATCH_UNAVAILABLE"):
            ledger.execute(
                uid(), "transaction.restore.v1", {"id": item["id"], "expected_version": 2}
            )
    replay = ledger.execute(request, "import.revert.v1", reversal)
    assert replay.replayed and replace(replay, replayed=False) == first
    assert_integrity(ledger)


@pytest.mark.parametrize("operation", ["edit", "delete", "delete_restore"])
def test_revert_refuses_any_changed_member(ledger: LedgerService, operation: str) -> None:
    imported = row(fields(ledger))
    payload = batch([imported, row(fields(ledger, 200), 3)])
    commit(ledger, payload)
    if operation == "edit":
        ledger.execute(
            uid(),
            "transaction.update.v1",
            {
                "id": imported["id"],
                "expected_version": 1,
                "fields": fields(ledger, 1_000) | {"source": "import"},
            },
        )
    else:
        ledger.execute(
            uid(), "transaction.delete.v1", {"id": imported["id"], "expected_version": 1}
        )
        if operation == "delete_restore":
            ledger.execute(
                uid(), "transaction.restore.v1", {"id": imported["id"], "expected_version": 2}
            )
    before_counts, before_balances = counts(ledger), ledger.balances()
    with pytest.raises(LedgerError, match="IMPORT_BATCH_CHANGED"):
        revert(ledger, str(payload["id"]))
    assert counts(ledger) == before_counts and ledger.balances() == before_balances
    assert ledger.import_batches()[0]["status"] == "committed"
    assert_integrity(ledger)


def test_outside_active_refund_blocks_batch_revert_until_deleted(ledger: LedgerService) -> None:
    imported = row(fields(ledger))
    payload = batch([imported])
    commit(ledger, payload)
    refund_id = uid()
    ledger.refund(
        RefundFields(str(imported["id"]), 100, refs(ledger)["bank"], TODAY),
        request_id=uid(),
        transaction_id=refund_id,
    )
    with pytest.raises(LedgerError, match="ACTIVE_REFUNDS_BLOCK_OPERATION"):
        revert(ledger, str(payload["id"]))
    ledger.execute(uid(), "transaction.delete.v1", {"id": refund_id, "expected_version": 1})
    revert(ledger, str(payload["id"]))
    assert ledger.total_assets() == 100_000
    with pytest.raises(LedgerError, match="ORIGINAL_EXPENSE_UNAVAILABLE"):
        ledger.execute(uid(), "transaction.restore.v1", {"id": refund_id, "expected_version": 2})
    assert_integrity(ledger)


@pytest.mark.parametrize(
    "point", ["after_import_revert_row", "after_audit", "before_receipt", "before_commit"]
)
def test_revert_fault_rolls_back_deletions_batch_and_receipt(
    ledger: LedgerService, point: str
) -> None:
    payload = all_kinds(ledger)
    commit(ledger, payload)
    before_counts, before_balances = counts(ledger), ledger.balances()

    def fail(stage: str) -> None:
        if stage == point:
            raise RuntimeError("injected reversal fault")

    failing = LedgerService(ledger.database, clock=ledger.clock, fault_hook=fail)
    with pytest.raises(RuntimeError, match="injected reversal fault"):
        revert(failing, str(payload["id"]))
    assert counts(ledger) == before_counts and ledger.balances() == before_balances
    assert ledger.import_batches()[0]["status"] == "committed"
    revert(ledger, str(payload["id"]))
    assert ledger.total_assets() == 100_000


def test_concurrent_reverts_apply_exactly_once(ledger: LedgerService) -> None:
    payload = all_kinds(ledger)
    commit(ledger, payload)

    def attempt(_index: int) -> str:
        try:
            revert(ledger, str(payload["id"]))
            return "applied"
        except LedgerError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, range(2)))
    assert sorted(outcomes) == ["VERSION_CONFLICT", "applied"]
    assert ledger.total_assets() == 100_000
    assert_integrity(ledger)


@pytest.mark.parametrize("reverted", [False, True])
def test_import_batches_survive_verified_backup_restore(
    ledger: LedgerService, tmp_path: Path, reverted: bool
) -> None:
    payload = all_kinds(ledger)
    commit(ledger, payload)
    if reverted:
        revert(ledger, str(payload["id"]))
    service = BackupService(ledger.database)
    archive = service.backup(tmp_path / "import-test.olbackup")
    restored = LedgerService(service.restore(archive, tmp_path / "restored"), clock=ledger.clock)
    assert restored.balances() == ledger.balances()
    assert restored.import_batches() == ledger.import_batches()
    assert_integrity(restored)


@pytest.mark.parametrize("tamper", ["count", "mapping", "revert"])
def test_cross_row_import_integrity_rejects_structurally_valid_damage(
    ledger: LedgerService, tamper: str
) -> None:
    payload = batch([row(fields(ledger))]) | {"source_row_count": 3}
    commit(ledger, payload)
    with ledger.database.write() as connection:
        if tamper == "count":
            connection.execute("UPDATE import_batches SET accepted_row_count=2")
        elif tamper == "mapping":
            connection.execute("UPDATE import_batches SET mapping_json='{}'")
        else:
            connection.execute(
                "UPDATE import_batches SET status='reverted',version=2,"
                "reverted_at_utc=updated_at_utc"
            )
    with ledger.database.read() as connection, pytest.raises(LedgerError, match="INTEGRITY_FAILED"):
        validate_financial_integrity(connection)


def test_import_batch_is_not_a_management_entity(ledger: LedgerService) -> None:
    with pytest.raises(LedgerError, match="INVALID_ENVELOPE"):
        ledger.entities("import_batch")


@pytest.mark.parametrize(
    "problem", ["format_type", "count_overflow", "row_overflow", "external_pair"]
)
def test_malformed_object_envelope_has_a_safe_ledger_failure(
    ledger: LedgerService, problem: str
) -> None:
    imported = row(fields(ledger))
    payload = batch([imported])
    if problem == "format_type":
        payload["source_format"] = {}
    elif problem == "count_overflow":
        payload["source_row_count"] = 2**63
    elif problem == "row_overflow":
        imported["source_row_number"] = 2**63
    else:
        imported["external_source"] = "bank"
    with pytest.raises(LedgerError, match="INVALID_ENVELOPE"):
        commit(ledger, payload)
    assert not ledger.import_batches()
