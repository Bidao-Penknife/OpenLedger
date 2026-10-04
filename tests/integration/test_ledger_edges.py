"""Adversarial command boundaries and financial totals on disposable SQLite files."""

from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from openledger.application.dto.ledger import RefundFields, TransactionFields, TransferFields
from openledger.domain.errors import LedgerError
from openledger.domain.money import MAX_EVENT_MINOR, MAX_INT64
from openledger.infrastructure.ledger import LedgerService

pytestmark = pytest.mark.integration
TODAY = date(2026, 10, 2)


def _uid() -> str:
    return str(uuid4())


def _refs(ledger: LedgerService) -> dict[str, str]:
    accounts = ledger.entities("account")
    categories = ledger.entities("category")
    return {
        "cash": str(next(row["id"] for row in accounts if row["account_type"] == "cash")),
        "bank": str(next(row["id"] for row in accounts if row["account_type"] == "bank")),
        "book": str(ledger.entities("book")[0]["id"]),
        "expense": str(
            next(row["id"] for row in categories if row["transaction_kind"] == "expense")
        ),
        "income": str(next(row["id"] for row in categories if row["transaction_kind"] == "income")),
    }


def _item(ledger: LedgerService) -> TransactionFields:
    reference = _refs(ledger)
    return TransactionFields(
        "expense", 10_000, reference["cash"], reference["book"], reference["expense"], TODAY
    )


def _state(ledger: LedgerService) -> tuple[dict[str, int], tuple[int, ...]]:
    with ledger.database.read() as connection:
        counts = tuple(
            int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "transactions",
                "account_entries",
                "audit_events",
                "change_log",
                "command_receipts",
            )
        )
    return ledger.balances(), counts


def _rejected(
    ledger: LedgerService, command: str, payload: Mapping[str, object], code: str
) -> None:
    before = _state(ledger)
    with pytest.raises(LedgerError) as caught:
        ledger.execute(_uid(), command, payload)
    assert caught.value.code == code
    assert _state(ledger) == before


@pytest.mark.parametrize("representation", ["upper", "compact", "braced"])
def test_refund_totals_query_normalizes_identifier(
    ledger: LedgerService, representation: str
) -> None:
    original = _uid()
    ledger.record(_item(ledger), request_id=_uid(), transaction_id=original)
    ledger.refund(
        RefundFields(original, 3_000, _refs(ledger)["bank"], TODAY),
        request_id=_uid(),
        transaction_id=_uid(),
    )
    changed = {
        "upper": original.upper(),
        "compact": original.replace("-", ""),
        "braced": "{" + original + "}",
    }[representation]
    result = ledger.transaction(changed)
    assert result["active_refunded_minor"] == 3_000
    assert result["remaining_refundable_minor"] == 7_000


@pytest.mark.parametrize("scope", ["outer", "fields"])
def test_unknown_fields_cannot_disappear_from_a_command(ledger: LedgerService, scope: str) -> None:
    item: dict[str, object] = asdict(_item(ledger))
    payload: dict[str, object] = {"id": _uid(), "fields": item}
    (payload if scope == "outer" else item)["unexpected"] = "value"
    _rejected(ledger, "transaction.record.v1", payload, "INVALID_ENVELOPE")


@pytest.mark.parametrize("key", ["request_id", "trace_id", "executed_at_utc", "committed_at_utc"])
def test_execution_metadata_is_not_an_ignored_payload_field(
    ledger: LedgerService, key: str
) -> None:
    _rejected(
        ledger,
        "transaction.record.v1",
        {"id": _uid(), "fields": asdict(_item(ledger)), key: _uid()},
        "INVALID_ENVELOPE",
    )


def test_request_id_is_shared_across_command_types(ledger: LedgerService) -> None:
    request = _uid()
    ledger.execute(request, "tag.create.v1", {"id": _uid(), "name": "测试标签"})
    with pytest.raises(LedgerError, match="IDEMPOTENCY_KEY_REUSED"):
        ledger.execute(request, "book.create.v1", {"id": _uid(), "name": "测试账本"})
    assert len(ledger.entities("book")) == 1


@pytest.mark.parametrize("value", [[], {}, True])
def test_source_enum_rejects_invalid_runtime_types(ledger: LedgerService, value: object) -> None:
    item: dict[str, object] = asdict(_item(ledger))
    item["source"] = value
    _rejected(ledger, "transaction.record.v1", {"id": _uid(), "fields": item}, "INVALID_ENVELOPE")


@pytest.mark.parametrize("entity,key", [("account", "account_type"), ("category", "kind")])
def test_management_enum_rejects_unhashable_inputs(
    ledger: LedgerService, entity: str, key: str
) -> None:
    payload: dict[str, object] = {
        "id": _uid(),
        "name": "无效实体",
        key: [],
        "balance_start_on": TODAY,
    }
    if entity == "category":
        payload.pop("balance_start_on")
    _rejected(ledger, entity + ".create.v1", payload, "INVALID_ENVELOPE")


def test_cross_table_uuid_collision_cannot_authorize_a_new_archived_reference(
    ledger: LedgerService,
) -> None:
    item = _item(ledger)
    transaction_id = _uid()
    ledger.record(item, request_id=_uid(), transaction_id=transaction_id)
    # IDs are unique within each table. An unrelated category may share an account UUID.
    ledger.execute(
        _uid(),
        "category.create.v1",
        {
            "id": item.account_id,
            "name": "与账户同 UUID 的分类",
            "kind": "expense",
        },
    )
    ledger.execute(
        _uid(),
        "category.archive.v1",
        {
            "id": item.account_id,
            "expected_version": 1,
            "archived": True,
        },
    )
    _rejected(
        ledger,
        "transaction.update.v1",
        {
            "id": transaction_id,
            "expected_version": 1,
            "fields": asdict(replace(item, category_id=item.account_id)),
        },
        "ENTITY_ARCHIVED",
    )


def test_restore_retains_archived_account_and_classification(ledger: LedgerService) -> None:
    item = _item(ledger)
    transaction_id = _uid()
    ledger.record(item, request_id=_uid(), transaction_id=transaction_id)
    ledger.execute(
        _uid(),
        "transaction.delete.v1",
        {
            "id": transaction_id,
            "expected_version": 1,
        },
    )
    ledger.execute(
        _uid(),
        "account.archive.v1",
        {
            "id": item.account_id,
            "expected_version": 1,
            "archived": True,
            "replacement_default_id": _refs(ledger)["bank"],
        },
    )
    ledger.execute(
        _uid(),
        "category.archive.v1",
        {
            "id": item.category_id,
            "expected_version": 1,
            "archived": True,
        },
    )
    ledger.execute(
        _uid(),
        "transaction.restore.v1",
        {
            "id": transaction_id,
            "expected_version": 2,
        },
    )
    assert ledger.total_assets() == 90_000
    assert ledger.transaction(transaction_id)["deleted_at_utc"] is None


def test_new_refund_inherits_archived_classification_but_needs_live_payment_account(
    ledger: LedgerService,
) -> None:
    item = _item(ledger)
    original = _uid()
    ledger.record(item, request_id=_uid(), transaction_id=original)
    ledger.execute(
        _uid(),
        "category.archive.v1",
        {
            "id": item.category_id,
            "expected_version": 1,
            "archived": True,
        },
    )
    refund = RefundFields(original, 1_000, _refs(ledger)["bank"], TODAY)
    refund_id = _uid()
    ledger.refund(refund, request_id=_uid(), transaction_id=refund_id)
    assert ledger.transaction(refund_id)["effective_category_id"] == item.category_id
    ledger.execute(
        _uid(),
        "account.archive.v1",
        {
            "id": refund.account_id,
            "expected_version": 1,
            "archived": True,
        },
    )
    _rejected(
        ledger,
        "refund.record.v1",
        {
            "id": _uid(),
            "fields": asdict(refund),
        },
        "ENTITY_ARCHIVED",
    )


@pytest.mark.parametrize("entity", ["transfer", "expense_refund"])
def test_generic_record_command_cannot_create_dedicated_financial_types(
    ledger: LedgerService, entity: str
) -> None:
    reference = _refs(ledger)
    if entity == "transfer":
        item: dict[str, object] = asdict(
            TransferFields(reference["cash"], reference["bank"], 10, TODAY)
        )
    else:
        original = _uid()
        ledger.record(_item(ledger), request_id=_uid(), transaction_id=original)
        item = asdict(RefundFields(original, 10, reference["bank"], TODAY))
    item["kind"] = entity
    _rejected(
        ledger,
        "transaction.record.v1",
        {
            "id": _uid(),
            "fields": item,
        },
        "TRANSACTION_KIND_IMMUTABLE",
    )


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"occurred_on": datetime(2026, 10, 2, tzinfo=UTC)}, "INVALID_DATE"),
        ({"time_zone": "Asia/NoSuchPlace"}, "INVALID_TIMEZONE"),
        ({"occurrence_precision": "period", "time_period": "evening"}, None),
        ({"occurrence_precision": "period"}, "MISSING_REQUIRED_FIELD"),
        ({"time_period": "evening"}, "FIELD_CONFLICT"),
        (
            {
                "occurrence_precision": "exact",
                "occurred_at_utc": datetime(2026, 10, 1, 23, tzinfo=UTC),
            },
            "FIELD_CONFLICT",
        ),
        (
            {
                "occurrence_precision": "exact",
                "occurred_at_utc": datetime(2026, 10, 2, 13, tzinfo=UTC),
            },
            "FUTURE_DATE",
        ),
        ({"occurred_on": TODAY + timedelta(days=1)}, "FUTURE_DATE"),
    ],
)
def test_date_precision_and_timezone_are_validated_before_any_commit(
    ledger: LedgerService, changes: dict[str, object], code: str | None
) -> None:
    item: dict[str, object] = asdict(_item(ledger))
    item.update(changes)
    payload: dict[str, object] = {"id": _uid(), "fields": item}
    if code is None:
        ledger.execute(_uid(), "transaction.record.v1", payload)
        assert ledger.total_assets() == 90_000
    else:
        _rejected(ledger, "transaction.record.v1", payload, code)


def test_unarchive_detects_a_conflicting_active_name(ledger: LedgerService) -> None:
    old_id, new_id = _uid(), _uid()
    ledger.execute(_uid(), "tag.create.v1", {"id": old_id, "name": "重复名称"})
    ledger.execute(
        _uid(), "tag.archive.v1", {"id": old_id, "expected_version": 1, "archived": True}
    )
    ledger.execute(_uid(), "tag.create.v1", {"id": new_id, "name": "重复名称"})
    _rejected(
        ledger,
        "tag.archive.v1",
        {
            "id": old_id,
            "expected_version": 2,
            "archived": False,
        },
        "NAME_CONFLICT",
    )


def test_total_assets_overflow_rolls_back_a_valid_individual_account_change(
    ledger: LedgerService,
) -> None:
    reference = _refs(ledger)
    # Seed ~92k bounded, valid synthetic income rows efficiently. Each account remains
    # within int64; adding one cent to a different account overflows only total assets.
    full_count, tail = divmod(MAX_INT64 - 100_000, MAX_EVENT_MINOR)
    with ledger.database.write() as connection:
        connection.execute(
            "WITH RECURSIVE numbers(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM numbers "
            "WHERE i<?) INSERT INTO transactions(id,kind,amount_minor,book_id,category_id,"
            "occurred_on,time_zone,created_at_utc,updated_at_utc) "
            "SELECT '00000000-0000-4000-8000-'||printf('%012x',i),'income',?,?,?,"
            "'2026-10-02','UTC','2026-10-02T12:00:00.000Z','2026-10-02T12:00:00.000Z' FROM numbers",
            (full_count, MAX_EVENT_MINOR, reference["book"], reference["income"]),
        )
        connection.execute(
            "INSERT INTO account_entries(id,transaction_id,account_id,delta_minor) "
            "SELECT id,id,?,amount_minor FROM transactions WHERE kind='income'",
            (reference["cash"],),
        )
        tail_id = _uid()
        connection.execute(
            "INSERT INTO transactions(id,kind,amount_minor,book_id,category_id,occurred_on,"
            "time_zone,created_at_utc,updated_at_utc) VALUES(?,'income',?,?,?,'2026-10-02','UTC',"
            "'2026-10-02T12:00:00.000Z','2026-10-02T12:00:00.000Z')",
            (tail_id, tail, reference["book"], reference["income"]),
        )
        connection.execute(
            "INSERT INTO account_entries(id,transaction_id,account_id,delta_minor) VALUES(?,?,?,?)",
            (_uid(), tail_id, reference["cash"], tail),
        )
    assert ledger.balances()[reference["cash"]] == MAX_INT64
    assert ledger.total_assets() == MAX_INT64
    item = replace(
        _item(ledger),
        kind="income",
        category_id=reference["income"],
        account_id=reference["bank"],
        amount_minor=1,
    )
    _rejected(
        ledger,
        "transaction.record.v1",
        {
            "id": _uid(),
            "fields": asdict(item),
        },
        "AGGREGATE_OUT_OF_RANGE",
    )
    assert ledger.balances()[reference["bank"]] == 0
    assert ledger.total_assets() == MAX_INT64
