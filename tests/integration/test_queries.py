"""Read models against real SQLite, persisted commands and concurrent commits."""

from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from sqlite3 import Connection, OperationalError
from threading import Event
from typing import cast
from uuid import uuid4

import pytest

from openledger.application.dto.ledger import RefundFields, TransactionFields, TransferFields
from openledger.application.dto.queries import TransactionFilter
from openledger.domain.errors import LedgerError
from openledger.domain.money import MAX_EVENT_MINOR, MAX_INT64
from openledger.infrastructure.database.database import Database
from openledger.infrastructure.ledger import LedgerService
from openledger.infrastructure.queries import LedgerQueries

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


def fields(
    ledger: LedgerService,
    *,
    kind: str = "expense",
    amount_minor: int = 2500,
    occurred_on: date = TODAY,
    book_id: str | None = None,
    category_id: str | None = None,
    note: str | None = None,
    merchant: str | None = None,
    counterparty: str | None = None,
    tag_ids: tuple[str, ...] = (),
    payment_method_id: str | None = None,
) -> TransactionFields:
    values = refs(ledger)
    return TransactionFields(
        kind,
        amount_minor,
        values["cash"],
        book_id or values["book"],
        category_id or values[kind],
        occurred_on,
        note=note,
        merchant=merchant,
        counterparty=counterparty,
        tag_ids=tag_ids,
        payment_method_id=payment_method_id,
    )


def record(ledger: LedgerService, item: TransactionFields) -> str:
    identifier = uid()
    ledger.record(item, request_id=uid(), transaction_id=identifier)
    return identifier


def test_empty_filters_and_month_exclude_opening(ledger: LedgerService) -> None:
    queries = LedgerQueries(ledger.database)
    page = queries.transactions(TransactionFilter())
    assert page.total == 1 and page.rows[0]["kind"] == "opening"
    assert page.rows[0]["account_name"] == "测试现金"
    assert page.rows[0]["book_name"] is None
    summary = queries.overview(TODAY)
    assert summary.total_assets_minor == 100_000
    assert summary.balances == ledger.balances()
    assert summary.income_minor == summary.expense_minor == summary.net_minor == 0
    assert summary.month_start == date(2026, 10, 1)
    assert summary.month_end == date(2026, 10, 31)
    assert summary.change_seq == page.change_seq


def test_page_order_is_stable_for_same_dates_and_created_time(ledger: LedgerService) -> None:
    identifiers = ["00000000-0000-4000-8000-00000000000" + str(number) for number in (1, 2, 3)]
    for identifier in identifiers:
        ledger.record(fields(ledger), request_id=uid(), transaction_id=identifier)
    queries = LedgerQueries(ledger.database)
    filters = TransactionFilter(start_on=TODAY, end_on=TODAY, page_size=2)
    first = queries.transactions(filters)
    second = queries.transactions(replace(filters, page=1))
    assert [row["id"] for row in first.rows] == identifiers[::-1][:2]
    assert [row["id"] for row in second.rows] == identifiers[:1]
    assert first.total == second.total == 3
    assert first.change_seq == second.change_seq
    assert queries.transactions(replace(filters, page=2)).rows == ()
    assert (second.page, second.page_size) == (1, 2)


def test_transfer_matches_either_account_once_and_has_roles(ledger: LedgerService) -> None:
    values = refs(ledger)
    identifier = uid()
    ledger.transfer(
        TransferFields(values["cash"], values["bank"], 10_000, TODAY),
        request_id=uid(),
        transaction_id=identifier,
    )
    queries = LedgerQueries(ledger.database)
    for account in (values["cash"], values["bank"]):
        result = queries.transactions(TransactionFilter(account_id=account, kind="transfer"))
        assert result.total == 1 and result.rows[0]["id"] == identifier
        row = result.rows[0]
        assert row["from_account_id"] == values["cash"]
        assert row["from_account_name"] == "测试现金"
        assert row["to_account_id"] == values["bank"]
        assert row["to_account_name"] == "测试银行"
        assert row["account_id"] is None
        entries = cast(tuple[dict[str, object], ...], row["entries"])
        assert len(entries) == 2 and sum(cast(int, entry["delta_minor"]) for entry in entries) == 0
    assert (
        queries.transactions(TransactionFilter(book_id=values["book"], kind="transfer")).total == 0
    )
    assert queries.overview(TODAY).net_minor == 0


@pytest.mark.parametrize("text", ["100%", "a_b", "C:\\账本", "' OR 1=1 --"])
def test_search_treats_wildcards_backslashes_and_sql_as_literal(
    ledger: LedgerService, text: str
) -> None:
    expected = record(ledger, fields(ledger, note="备注：" + text))
    record(ledger, fields(ledger, note="普通记录 100 aXb C:账本"))
    page = LedgerQueries(ledger.database).transactions(TransactionFilter(search=text))
    assert page.total == 1 and page.rows[0]["id"] == expected


def test_search_names_optional_fields_and_unicode_normalization(ledger: LedgerService) -> None:
    identifier = record(ledger, fields(ledger, merchant="café 小店", counterparty="朋友"))
    queries = LedgerQueries(ledger.database)
    for search in ("cafe\u0301", "朋友", "我的账本", "测试现金"):
        page = queries.transactions(TransactionFilter(kind="expense", search=search))
        assert page.total == 1 and page.rows[0]["id"] == identifier
    assert queries.transactions(TransactionFilter(search="不存在")).total == 0


def test_deleted_filter_restore_and_versions_are_current(ledger: LedgerService) -> None:
    identifier = record(ledger, fields(ledger))
    queries = LedgerQueries(ledger.database)
    first = queries.transactions(TransactionFilter(kind="expense"))
    ledger.execute(
        uid(),
        "transaction.update.v1",
        {
            "id": identifier,
            "expected_version": 1,
            "fields": asdict(fields(ledger, amount_minor=5000)),
        },
    )
    edited = queries.transactions(TransactionFilter(kind="expense"))
    assert first.rows[0]["version"] == 1 and first.rows[0]["amount_minor"] == 2500
    assert edited.rows[0]["version"] == 2 and edited.rows[0]["amount_minor"] == 5000
    assert edited.change_seq > first.change_seq
    with pytest.raises(LedgerError, match="VERSION_CONFLICT"):
        ledger.execute(uid(), "transaction.delete.v1", {"id": identifier, "expected_version": 1})
    ledger.execute(uid(), "transaction.delete.v1", {"id": identifier, "expected_version": 2})
    assert queries.transactions(TransactionFilter(kind="expense")).total == 0
    deleted = queries.transactions(TransactionFilter(kind="expense", include_deleted=True))
    assert deleted.total == 1 and deleted.rows[0]["deleted_at_utc"] is not None
    assert queries.overview(TODAY).expense_minor == 0
    ledger.execute(uid(), "transaction.restore.v1", {"id": identifier, "expected_version": 3})
    restored = queries.transactions(TransactionFilter(kind="expense"))
    assert restored.rows[0]["version"] == 4 and restored.rows[0]["deleted_at_utc"] is None
    assert queries.overview(TODAY).expense_minor == 5000


def test_refund_inherits_live_classification_and_arrival_month(ledger: LedgerService) -> None:
    values = refs(ledger)
    expense = record(ledger, fields(ledger, amount_minor=2000, occurred_on=date(2026, 9, 30)))
    refund = uid()
    ledger.refund(
        RefundFields(expense, 500, values["bank"], date(2026, 10, 1)),
        request_id=uid(),
        transaction_id=refund,
    )
    queries = LedgerQueries(ledger.database)
    page = queries.transactions(TransactionFilter(book_id=values["book"], kind="expense_refund"))
    assert page.total == 1 and page.rows[0]["id"] == refund
    assert page.rows[0]["account_name"] == "测试银行"
    assert page.rows[0]["book_id"] is None
    assert page.rows[0]["effective_book_id"] == values["book"]
    category = uid()
    ledger.execute(uid(), "category.create.v1", {"id": category, "name": "家居", "kind": "expense"})
    ledger.execute(
        uid(),
        "transaction.update.v1",
        {
            "id": expense,
            "expected_version": 1,
            "fields": asdict(
                fields(
                    ledger,
                    amount_minor=2000,
                    occurred_on=date(2026, 9, 30),
                    category_id=category,
                )
            ),
        },
    )
    assert queries.transactions(TransactionFilter(book_id=uid())).total == 0
    classified = queries.transactions(
        TransactionFilter(book_id=values["book"], kind="expense_refund")
    )
    assert classified.rows[0]["effective_category_id"] == category
    assert classified.rows[0]["book_name"] == "我的账本"
    assert classified.rows[0]["category_name"] == "家居"
    october = queries.overview(TODAY)
    assert october.gross_expense_minor == 0 and october.refund_minor == 500
    assert october.expense_minor == -500 and october.net_minor == 500
    september = queries.overview(date(2026, 9, 1))
    assert september.gross_expense_minor == september.expense_minor == 2000
    assert september.refund_minor == 0


def test_overview_excludes_transfer_adjustment_and_includes_archived_assets(
    ledger: LedgerService,
) -> None:
    values = refs(ledger)
    record(ledger, fields(ledger, kind="income", category_id=values["income"], amount_minor=8000))
    record(ledger, fields(ledger, amount_minor=2000))
    ledger.transfer(
        TransferFields(values["cash"], values["bank"], 10_000, TODAY),
        request_id=uid(),
        transaction_id=uid(),
    )
    ledger.execute(
        uid(),
        "account.adjust.v1",
        {
            "account_id": values["bank"],
            "target_balance_minor": 20_000,
            "occurred_on": TODAY,
            "reason": "核对测试余额",
        },
    )
    ledger.execute(
        uid(), "account.archive.v1", {"id": values["bank"], "expected_version": 1, "archived": True}
    )
    overview = LedgerQueries(ledger.database).overview(TODAY)
    assert overview.balances == {values["cash"]: 96_000, values["bank"]: 20_000}
    assert overview.total_assets_minor == 116_000
    assert overview.income_minor == 8000 and overview.expense_minor == 2000
    assert overview.net_minor == 6000


def test_tags_payment_and_archived_names_remain_available(ledger: LedgerService) -> None:
    tag = uid()
    ledger.execute(uid(), "tag.create.v1", {"id": tag, "name": "聚餐"})
    payment = str(
        next(row["id"] for row in ledger.entities("payment_method") if row["code"] == "wechat")
    )
    identifier = record(ledger, fields(ledger, tag_ids=(tag,), payment_method_id=payment))
    values = refs(ledger)
    ledger.execute(
        uid(),
        "category.archive.v1",
        {"id": values["expense"], "expected_version": 1, "archived": True},
    )
    page = LedgerQueries(ledger.database).transactions(TransactionFilter(kind="expense"))
    row = page.rows[0]
    assert row["id"] == identifier and row["tag_ids"] == (tag,)
    assert row["payment_method_name"] == "微信"
    assert row["category_name"] is not None


def test_preferences_persist_and_default_archive_replaces_selection(ledger: LedgerService) -> None:
    values = refs(ledger)
    book = uid()
    ledger.execute(uid(), "book.create.v1", {"id": book, "name": "旅行账本"})
    ledger.execute(uid(), "book.default.set.v1", {"id": book, "expected_version": 1})
    ledger.execute(uid(), "account.default.set.v1", {"id": values["bank"], "expected_version": 1})
    reopened = LedgerQueries(Database(ledger.database.path))
    assert reopened.preferences() == {"default_book_id": book, "default_account_id": values["bank"]}
    ledger.execute(
        uid(),
        "book.archive.v1",
        {
            "id": book,
            "expected_version": 2,
            "archived": True,
            "replacement_default_id": values["book"],
        },
    )
    ledger.execute(
        uid(),
        "account.archive.v1",
        {
            "id": values["bank"],
            "expected_version": 2,
            "archived": True,
            "replacement_default_id": values["cash"],
        },
    )
    assert reopened.preferences() == {
        "default_book_id": values["book"],
        "default_account_id": values["cash"],
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"page": -1},
        {"page": True},
        {"page": 1_000_001},
        {"page_size": 0},
        {"page_size": 201},
        {"include_deleted": 1},
        {"kind": "expense; DROP TABLE accounts"},
        {"kind": []},
        {"book_id": "not-a-uuid"},
        {"account_id": 123},
        {"start_on": datetime(2026, 10, 1, tzinfo=UTC)},
        {"end_on": "2026-10-02"},
        {"start_on": TODAY, "end_on": date(2026, 10, 1)},
        {"search": None},
        {"search": "x" * 4001},
    ],
)
def test_invalid_filter_is_rejected_safely(
    ledger: LedgerService, changes: dict[str, object]
) -> None:
    malformed = TransactionFilter()
    for name, value in changes.items():
        object.__setattr__(malformed, name, value)
    with pytest.raises(LedgerError, match="INVALID_FILTER"):
        LedgerQueries(ledger.database).transactions(malformed)


def test_calendar_bounds_and_invalid_overview_reference(ledger: LedgerService) -> None:
    queries = LedgerQueries(ledger.database)
    assert queries.overview(date(2024, 2, 15)).month_end == date(2024, 2, 29)
    assert queries.overview(date(9999, 12, 31)).month_end == date(9999, 12, 31)
    with pytest.raises(LedgerError, match="INVALID_FILTER"):
        queries.overview(cast(date, datetime(2026, 10, 2, tzinfo=UTC)))


def test_page_count_rows_and_cursor_share_snapshot_during_write(
    ledger: LedgerService, monkeypatch: pytest.MonkeyPatch
) -> None:
    queries = LedgerQueries(ledger.database)
    filters = TransactionFilter(kind="expense")
    before = queries.transactions(filters)
    reached_commit = Event()
    original_read = ledger.database.read
    pending: list[Future[object]] = []
    trace_wait: list[bool] = []

    def observe_commit(stage: str) -> None:
        if stage == "before_commit":
            reached_commit.set()

    writer = LedgerService(ledger.database, clock=ledger.clock, fault_hook=observe_commit)
    item = fields(ledger)
    with ThreadPoolExecutor(max_workers=1) as executor:

        def trace(statement: str) -> None:
            if statement.startswith("SELECT COUNT(*)") and not pending:
                pending.append(
                    executor.submit(writer.record, item, request_id=uid(), transaction_id=uid())
                )
                trace_wait.append(reached_commit.wait(3))

        @contextmanager
        def traced_read() -> Iterator[Connection]:
            with original_read() as connection:
                connection.set_trace_callback(trace)
                yield connection

        monkeypatch.setattr(ledger.database, "read", traced_read)
        during = queries.transactions(filters)
        assert trace_wait == [True]
        pending[0].result(timeout=5)
    monkeypatch.setattr(ledger.database, "read", original_read)
    after = queries.transactions(filters)
    assert during.total == before.total == 0 and during.rows == ()
    assert during.change_seq == before.change_seq
    assert after.total == 1 and after.change_seq > during.change_seq


def test_monthly_turnover_can_exceed_int64_without_balance_overflow(ledger: LedgerService) -> None:
    """Use synthetic paired events to exercise a real SQLite SUM overflow boundary."""
    values = refs(ledger)
    pair_count = MAX_INT64 // MAX_EVENT_MINOR + 1
    with ledger.database.write() as connection:
        connection.execute(
            "WITH RECURSIVE numbers(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM numbers WHERE n<?) "
            "INSERT INTO transactions(id,kind,amount_minor,book_id,category_id,occurred_on,"
            "time_zone,created_at_utc,updated_at_utc) "
            "SELECT printf('00000000-0000-4000-8000-%012d',n),"
            "CASE WHEN n%2=1 THEN 'income' ELSE 'expense' END,?,?,"
            "CASE WHEN n%2=1 THEN ? ELSE ? END,'2026-10-02','UTC',"
            "'2026-10-02T12:00:00.000Z','2026-10-02T12:00:00.000Z' FROM numbers",
            (pair_count * 2, MAX_EVENT_MINOR, values["book"], values["income"], values["expense"]),
        )
        connection.execute(
            "INSERT INTO account_entries(id,transaction_id,account_id,delta_minor) "
            "SELECT id,id,?,CASE WHEN kind='income' THEN amount_minor ELSE -amount_minor END "
            "FROM transactions WHERE id LIKE '00000000-0000-4000-8000-%'",
            (values["cash"],),
        )
    with (
        ledger.database.read() as connection,
        pytest.raises(OperationalError, match="integer overflow"),
    ):
        connection.execute(
            "SELECT SUM(amount_minor) FROM transactions WHERE kind='income'"
        ).fetchone()
    overview = LedgerQueries(ledger.database).overview(TODAY)
    expected = pair_count * MAX_EVENT_MINOR
    assert expected > MAX_INT64
    assert overview.income_minor == overview.gross_expense_minor == expected
    assert overview.refund_minor == overview.net_minor == 0
    assert overview.total_assets_minor == 100_000
