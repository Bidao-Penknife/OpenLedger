"""Financial invariants across actual transactions, versions and failures."""

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import date
from typing import cast
from uuid import uuid4

import pytest

from openledger.application.dto.ledger import RefundFields, TransactionFields, TransferFields
from openledger.application.writer import LedgerWriter
from openledger.domain.errors import LedgerError
from openledger.infrastructure.ledger import LedgerService

pytestmark = pytest.mark.integration
TODAY = date(2026, 10, 2)


def uid() -> str:
    return str(uuid4())


def refs(ledger: LedgerService) -> dict[str, str]:
    accounts = ledger.entities("account")
    return {
        "cash": str(next(row["id"] for row in accounts if row["account_type"] == "cash")),
        "bank": str(next(row["id"] for row in accounts if row["account_type"] == "bank")),
        "book": str(ledger.entities("book")[0]["id"]),
        "expense": str(
            next(
                row["id"]
                for row in ledger.entities("category")
                if row["transaction_kind"] == "expense"
            )
        ),
        "income": str(
            next(
                row["id"]
                for row in ledger.entities("category")
                if row["transaction_kind"] == "income"
            )
        ),
    }


def fields(ledger: LedgerService, amount: int = 12_800, kind: str = "expense") -> TransactionFields:
    r = refs(ledger)
    return TransactionFields(
        kind=kind,
        amount_minor=amount,
        account_id=r["cash"],
        book_id=r["book"],
        category_id=r[kind],
        occurred_on=TODAY,
    )


def record(ledger: LedgerService, amount: int = 12_800, kind: str = "expense") -> str:
    identifier = uid()
    ledger.record(fields(ledger, amount, kind), request_id=uid(), transaction_id=identifier)
    return identifier


def command(ledger: LedgerService, name: str, payload: Mapping[str, object]) -> None:
    ledger.execute(uid(), name, payload)


def assert_error(
    code: str, ledger: LedgerService, name: str, payload: Mapping[str, object]
) -> None:
    before = ledger.balances()
    with pytest.raises(LedgerError) as raised:
        command(ledger, name, payload)
    assert raised.value.code == code
    assert ledger.balances() == before


def test_income_expense_tags_and_audit_are_one_commit(ledger: LedgerService) -> None:
    tag = uid()
    command(ledger, "tag.create.v1", {"id": tag, "name": "朋友聚餐"})
    identifier = uid()
    item = replace(fields(ledger), tag_ids=(tag, tag), note="火锅", counterparty="朋友")
    result = ledger.record(item, request_id=uid(), transaction_id=identifier)
    assert result.balance_changes[0].before_minor == 100_000
    assert result.balance_changes[0].after_minor == 87_200
    detail = ledger.transaction(identifier)
    assert detail["tag_ids"] == [tag]
    assert detail["note"] == "火锅"
    record(ledger, 5_000, "income")
    assert ledger.total_assets() == 92_200
    with ledger.database.read() as connection:
        audit = connection.execute(
            "SELECT * FROM audit_events WHERE request_id=?", (result.request_id,)
        ).fetchone()
        assert audit["entity_id"] == identifier
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM change_log WHERE audit_event_id=?", (audit["id"],)
            ).fetchone()[0]
            == 1
        )
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()


def test_replay_has_original_result_and_no_duplicate_write(ledger: LedgerService) -> None:
    request, identifier = uid(), uid()
    first = ledger.record(fields(ledger), request_id=request, transaction_id=identifier)
    record(ledger, 100)
    replay = ledger.record(fields(ledger), request_id=request, transaction_id=identifier)
    assert replay.replayed
    assert replace(replay, replayed=False) == first
    assert ledger.total_assets() == 87_100
    with pytest.raises(LedgerError, match="IDEMPOTENCY_KEY_REUSED"):
        ledger.record(fields(ledger, 9_999), request_id=request, transaction_id=identifier)


def test_successful_edit_retry_precedes_version_check(ledger: LedgerService) -> None:
    identifier = record(ledger)
    request = uid()
    payload: dict[str, object] = {
        "id": identifier,
        "expected_version": 1,
        "fields": asdict(fields(ledger, 100)),
    }
    first = ledger.execute(request, "transaction.update.v1", payload)
    replay = ledger.execute(request, "transaction.update.v1", payload)
    assert replay.replayed and replace(replay, replayed=False) == first
    assert ledger.transaction(identifier)["version"] == 2
    assert_error("VERSION_CONFLICT", ledger, "transaction.update.v1", payload)


@pytest.mark.parametrize(
    "point",
    [
        "after_transaction",
        "after_entry",
        "after_entries",
        "after_audit",
        "before_receipt",
        "before_commit",
    ],
)
def test_failure_rolls_back_all_parts_and_same_request_can_retry(
    ledger: LedgerService, point: str
) -> None:
    def fail(stage: str) -> None:
        if stage == point:
            raise RuntimeError("injected fault")

    failing = LedgerService(ledger.database, clock=ledger.clock, fault_hook=fail)
    request, identifier = uid(), uid()
    with ledger.database.read() as connection:
        before = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in [
                "transactions",
                "account_entries",
                "transaction_tags",
                "audit_events",
                "change_log",
                "command_receipts",
            ]
        }
    with pytest.raises(RuntimeError, match="injected fault"):
        failing.record(fields(ledger), request_id=request, transaction_id=identifier)
    with ledger.database.read() as connection:
        after = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before
        }
    assert after == before
    assert ledger.total_assets() == 100_000
    assert not ledger.record(fields(ledger), request_id=request, transaction_id=identifier).replayed


def test_transfer_conserves_assets_and_both_sides_are_atomic(ledger: LedgerService) -> None:
    r = refs(ledger)
    identifier = uid()
    ledger.transfer(
        TransferFields(r["cash"], r["bank"], 20_000, TODAY),
        request_id=uid(),
        transaction_id=identifier,
    )
    assert ledger.balances() == {r["cash"]: 80_000, r["bank"]: 20_000}
    assert ledger.total_assets() == 100_000
    entries = cast(list[dict[str, object]], ledger.transaction(identifier)["entries"])
    assert len(entries) == 2 and sum(cast(int, row["delta_minor"]) for row in entries) == 0
    assert_error(
        "INVALID_TRANSFER",
        ledger,
        "transfer.record.v1",
        {"id": uid(), "fields": asdict(TransferFields(r["cash"], r["cash"], 10, TODAY))},
    )


def test_transfer_edit_rebuilds_both_sides(ledger: LedgerService) -> None:
    r = refs(ledger)
    identifier = uid()
    item = TransferFields(r["cash"], r["bank"], 20_000, TODAY)
    ledger.transfer(item, request_id=uid(), transaction_id=identifier)
    command(
        ledger,
        "transfer.update.v1",
        {
            "id": identifier,
            "expected_version": 1,
            "fields": asdict(replace(item, amount_minor=30_000)),
        },
    )
    assert ledger.balances() == {r["cash"]: 70_000, r["bank"]: 30_000}


@pytest.mark.parametrize("kind", ["income", "expense"])
def test_delete_and_restore_recompute_balance(ledger: LedgerService, kind: str) -> None:
    identifier = record(ledger, 200_000, kind)
    expected = 300_000 if kind == "income" else -100_000
    assert ledger.total_assets() == expected
    command(ledger, "transaction.delete.v1", {"id": identifier, "expected_version": 1})
    assert ledger.total_assets() == 100_000
    command(ledger, "transaction.restore.v1", {"id": identifier, "expected_version": 2})
    assert ledger.total_assets() == expected


def test_refund_limit_delete_and_restore(ledger: LedgerService) -> None:
    original = record(ledger, 10_000)
    r = refs(ledger)
    first = uid()
    refund = RefundFields(original, 6_000, r["bank"], TODAY)
    ledger.refund(refund, request_id=uid(), transaction_id=first)
    assert ledger.total_assets() == 96_000
    assert_error(
        "REFUND_LIMIT_EXCEEDED",
        ledger,
        "refund.record.v1",
        {"id": uid(), "fields": asdict(replace(refund, amount_minor=4_001))},
    )
    assert_error(
        "ACTIVE_REFUNDS_BLOCK_OPERATION",
        ledger,
        "transaction.delete.v1",
        {"id": original, "expected_version": 1},
    )
    command(ledger, "transaction.delete.v1", {"id": first, "expected_version": 1})
    ledger.refund(replace(refund, amount_minor=10_000), request_id=uid(), transaction_id=uid())
    assert_error(
        "REFUND_LIMIT_EXCEEDED",
        ledger,
        "transaction.restore.v1",
        {"id": first, "expected_version": 2},
    )


def test_refund_edit_excludes_its_own_amount(ledger: LedgerService) -> None:
    original = record(ledger, 10_000)
    item = RefundFields(original, 7_000, refs(ledger)["cash"], TODAY)
    identifier = uid()
    ledger.refund(item, request_id=uid(), transaction_id=identifier)
    command(
        ledger,
        "refund.update.v1",
        {
            "id": identifier,
            "expected_version": 1,
            "fields": asdict(replace(item, amount_minor=10_000)),
        },
    )
    assert ledger.total_assets() == 100_000


def test_refund_classification_follows_original_and_blocks_book_move(ledger: LedgerService) -> None:
    original = record(ledger)
    refund_id = uid()
    ledger.refund(
        RefundFields(original, 100, refs(ledger)["bank"], TODAY),
        request_id=uid(),
        transaction_id=refund_id,
    )
    other_category = str(
        next(
            row["id"]
            for row in ledger.entities("category")
            if row["transaction_kind"] == "expense" and row["id"] != refs(ledger)["expense"]
        )
    )
    item = replace(fields(ledger), category_id=other_category)
    command(
        ledger,
        "transaction.update.v1",
        {"id": original, "expected_version": 1, "fields": asdict(item)},
    )
    detail = ledger.transaction(refund_id)
    assert detail["category_id"] is None and detail["book_id"] is None
    assert detail["effective_category_id"] == other_category
    book = uid()
    command(ledger, "book.create.v1", {"id": book, "name": "旅行账本"})
    assert_error(
        "ACTIVE_REFUNDS_BLOCK_OPERATION",
        ledger,
        "transaction.update.v1",
        {"id": original, "expected_version": 2, "fields": asdict(replace(item, book_id=book))},
    )


def test_restore_expense_does_not_restore_deleted_refund(ledger: LedgerService) -> None:
    original = record(ledger)
    refund_id = uid()
    ledger.refund(
        RefundFields(original, 100, refs(ledger)["bank"], TODAY),
        request_id=uid(),
        transaction_id=refund_id,
    )
    command(ledger, "transaction.delete.v1", {"id": refund_id, "expected_version": 1})
    command(ledger, "transaction.delete.v1", {"id": original, "expected_version": 1})
    assert_error(
        "ORIGINAL_EXPENSE_UNAVAILABLE",
        ledger,
        "transaction.restore.v1",
        {"id": refund_id, "expected_version": 2},
    )
    command(ledger, "transaction.restore.v1", {"id": original, "expected_version": 2})
    assert ledger.transaction(refund_id)["deleted_at_utc"] is not None
    assert ledger.total_assets() == 87_200


def test_zero_opening_preserves_cut_point_and_nonzero_is_unique(ledger: LedgerService) -> None:
    r = refs(ledger)
    assert_error(
        "DATE_BEFORE_BALANCE_START",
        ledger,
        "transaction.record.v1",
        {
            "id": uid(),
            "fields": asdict(
                replace(fields(ledger), account_id=r["bank"], occurred_on=date(2025, 12, 31))
            ),
        },
    )
    command(
        ledger,
        "account.opening.set.v1",
        {
            "account_id": r["bank"],
            "expected_account_version": 1,
            "balance_start_on": "2026-01-01",
            "opening_balance_minor": -500,
        },
    )
    command(
        ledger,
        "account.opening.set.v1",
        {
            "account_id": r["bank"],
            "expected_account_version": 2,
            "balance_start_on": "2026-01-01",
            "opening_balance_minor": 800,
        },
    )
    with ledger.database.read() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM transactions t JOIN account_entries e "
                "ON e.transaction_id=t.id "
                "WHERE t.kind='opening' AND t.deleted_at_utc IS NULL AND e.account_id=?",
                (r["bank"],),
            ).fetchone()[0]
            == 1
        )
    assert ledger.balances()[r["bank"]] == 800
    command(
        ledger,
        "account.opening.set.v1",
        {
            "account_id": r["bank"],
            "expected_account_version": 3,
            "balance_start_on": "2026-01-01",
            "opening_balance_minor": 0,
        },
    )
    assert ledger.balances()[r["bank"]] == 0


def test_cut_point_cannot_move_past_active_event(ledger: LedgerService) -> None:
    identifier = uid()
    item = replace(fields(ledger), occurred_on=date(2026, 2, 1))
    ledger.record(item, request_id=uid(), transaction_id=identifier)
    assert_error(
        "BALANCE_START_CONFLICT",
        ledger,
        "account.opening.set.v1",
        {
            "account_id": item.account_id,
            "expected_account_version": 1,
            "balance_start_on": "2026-03-01",
            "opening_balance_minor": 0,
        },
    )


def test_adjustment_uses_latest_balance_and_keeps_fixed_delta(ledger: LedgerService) -> None:
    r = refs(ledger)
    record(ledger, 1_000)
    result = ledger.execute(
        uid(),
        "account.adjust.v1",
        {
            "account_id": r["cash"],
            "target_balance_minor": 100_000,
            "occurred_on": TODAY,
            "reason": "核对余额",
        },
    )
    identifier = str(result.data["id"])
    detail = ledger.transaction(identifier)
    assert detail["balance_before_minor"] == 99_000 and detail["balance_target_minor"] == 100_000
    record(ledger, 500)
    assert ledger.total_assets() == 99_500
    assert ledger.transaction(identifier)["amount_minor"] == 1_000
    command(
        ledger,
        "adjustment.metadata.update.v1",
        {
            "id": identifier,
            "expected_version": 1,
            "reason": "说明修正",
            "note": "人工核对",
            "tag_ids": (),
        },
    )
    assert ledger.total_assets() == 99_500


def test_no_change_adjustment_is_receipted_before_later_balance_change(
    ledger: LedgerService,
) -> None:
    payload: dict[str, object] = {
        "account_id": refs(ledger)["cash"],
        "target_balance_minor": 100_000,
        "occurred_on": TODAY,
        "reason": "核对",
    }
    request = uid()
    result = ledger.execute(request, "account.adjust.v1", payload)
    assert result.outcome == "no_change" and not result.changed_entities
    record(ledger, 100)
    replay = ledger.execute(request, "account.adjust.v1", payload)
    assert replay.replayed and replay.outcome == "no_change"
    assert ledger.total_assets() == 99_900


def test_archived_references_can_be_retained_but_not_newly_selected(ledger: LedgerService) -> None:
    identifier = record(ledger)
    category = fields(ledger).category_id
    command(
        ledger, "category.archive.v1", {"id": category, "expected_version": 1, "archived": True}
    )
    item = replace(fields(ledger), category_id=category, amount_minor=100)
    command(
        ledger,
        "transaction.update.v1",
        {"id": identifier, "expected_version": 1, "fields": asdict(item)},
    )
    assert_error(
        "ENTITY_ARCHIVED", ledger, "transaction.record.v1", {"id": uid(), "fields": asdict(item)}
    )


def test_archiving_account_preserves_assets_and_requires_default_replacement(
    ledger: LedgerService,
) -> None:
    r = refs(ledger)
    assert_error(
        "MISSING_REQUIRED_FIELD",
        ledger,
        "account.archive.v1",
        {"id": r["cash"], "expected_version": 1, "archived": True},
    )
    command(
        ledger,
        "account.archive.v1",
        {
            "id": r["cash"],
            "expected_version": 1,
            "archived": True,
            "replacement_default_id": r["bank"],
        },
    )
    assert ledger.total_assets() == 100_000
    assert ledger.balances()[r["cash"]] == 100_000


def test_queue_and_parallel_retries_do_not_double_post(ledger: LedgerService) -> None:
    request, identifier = uid(), uid()
    item = fields(ledger)
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(ledger.record, item, request_id=request, transaction_id=identifier)
            for _ in range(4)
        ]
        results = [future.result(timeout=20) for future in futures]
    assert sum(not result.replayed for result in results) == 1
    assert ledger.total_assets() == 87_200
    writer = LedgerWriter(ledger)
    futures = [
        writer.submit(
            uid(), "transaction.record.v1", {"id": uid(), "fields": asdict(fields(ledger, 100))}
        )
        for _ in range(3)
    ]
    for future in futures:
        assert future.result(timeout=20).outcome == "applied"
    writer.close()
    assert ledger.total_assets() == 86_900
    with pytest.raises(RuntimeError, match="closed"):
        writer.submit(uid(), "transaction.record.v1", {})


@pytest.mark.parametrize(
    "mutator,code",
    [
        ("future", "FUTURE_DATE"),
        ("kind", "FIELD_CONFLICT"),
        ("negative", "INVALID_AMOUNT"),
        ("float", "INVALID_AMOUNT"),
    ],
)
def test_invalid_commands_cannot_change_balance(
    ledger: LedgerService, mutator: str, code: str
) -> None:
    item: dict[str, object] = asdict(fields(ledger))
    if mutator == "future":
        item["occurred_on"] = date(2026, 10, 3)
    elif mutator == "kind":
        item["category_id"] = refs(ledger)["income"]
    elif mutator == "negative":
        item["amount_minor"] = -1
    else:
        item["amount_minor"] = 1.2
    assert_error(code, ledger, "transaction.record.v1", {"id": uid(), "fields": item})
