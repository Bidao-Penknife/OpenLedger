"""Analytical correctness against real commands, snapshots and overflow boundaries."""

from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from sqlite3 import Connection, OperationalError
from threading import Event
from typing import cast
from uuid import uuid4

import pytest

from openledger.application.dto.analytics import AnalyticsFilter
from openledger.application.dto.ledger import RefundFields, TransactionFields, TransferFields
from openledger.domain.errors import LedgerError
from openledger.domain.money import MAX_EVENT_MINOR, MAX_INT64
from openledger.infrastructure.analytics import AnalyticsService
from openledger.infrastructure.ledger import LedgerService

pytestmark = pytest.mark.integration
TODAY = date(2026, 10, 2)
NOW = datetime(2026, 10, 2, 12, tzinfo=UTC)


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
        "expense": str(
            next(
                row["id"]
                for row in ledger.entities("category")
                if row["transaction_kind"] == "expense" and row["name"] == "餐饮"
            )
        ),
        "income": str(
            next(
                row["id"]
                for row in ledger.entities("category")
                if row["transaction_kind"] == "income" and row["name"] == "工资"
            )
        ),
    }


def item(
    ledger: LedgerService,
    *,
    kind: str = "expense",
    amount: int = 2500,
    day: date = TODAY,
    merchant: str | None = None,
    counterparty: str | None = None,
    tags: tuple[str, ...] = (),
) -> TransactionFields:
    values = refs(ledger)
    return TransactionFields(
        kind,
        amount,
        values["cash"],
        values["book"],
        values[kind],
        day,
        merchant=merchant,
        counterparty=counterparty,
        tag_ids=tags,
    )


def record(ledger: LedgerService, fields: TransactionFields) -> str:
    identifier = uid()
    ledger.record(fields, request_id=uid(), transaction_id=identifier)
    return identifier


def analytics(ledger: LedgerService) -> AnalyticsService:
    return AnalyticsService(ledger.database, clock=lambda: NOW)


def test_empty_report_preserves_month_gaps_and_zero_denominators(ledger: LedgerService) -> None:
    report = analytics(ledger).build_report(AnalyticsFilter(date(2026, 1, 18), TODAY))
    assert len(report.months) == 10
    assert report.months[0].month == "2026-01" and report.months[-1].month == "2026-10"
    assert all(
        month.totals.income_minor == month.totals.net_expense_minor == 0 for month in report.months
    )
    assert (
        report.totals.income_count == report.totals.expense_count == report.totals.refund_count == 0
    )
    assert report.totals.savings_rate is None
    assert report.categories == () and report.ranking == ()
    assert report.generated_at_utc == "2026-10-02T12:00:00.000Z"
    assert any("收入为零" in note for note in report.notes)
    assert any("支出原额为零" in note for note in report.notes)
    assert report.data_revision > 0


def test_months_boundaries_and_equal_length_previous_period(ledger: LedgerService) -> None:
    record(ledger, item(ledger, amount=111, day=date(2026, 1, 31)))
    record(ledger, item(ledger, amount=222, day=date(2026, 3, 1)))
    record(ledger, item(ledger, amount=333, day=date(2026, 3, 31)))
    record(ledger, item(ledger, amount=444, day=date(2026, 4, 1)))
    report = analytics(ledger).build_report(AnalyticsFilter(date(2026, 1, 31), date(2026, 3, 31)))
    assert [month.month for month in report.months] == ["2026-01", "2026-02", "2026-03"]
    assert [month.totals.gross_expense_minor for month in report.months] == [111, 0, 555]
    assert report.totals.gross_expense_minor == 666 and report.totals.expense_count == 3
    assert report.comparison_end == date(2026, 1, 30)
    assert report.comparison_start == date(2025, 12, 2)
    assert report.comparison_start_on == report.comparison_start
    assert report.comparison_end_on == report.comparison_end
    assert (
        report.comparison_totals is not None and report.comparison_totals.gross_expense_minor == 0
    )


def test_current_and_comparison_period_use_identical_filters(ledger: LedgerService) -> None:
    values = refs(ledger)
    record(ledger, item(ledger, amount=500, day=date(2026, 9, 29)))
    record(ledger, item(ledger, amount=600, day=date(2026, 9, 30)))
    record(ledger, item(ledger, amount=700, day=date(2026, 10, 1)))
    record(ledger, item(ledger, amount=800, day=TODAY))
    record(ledger, replace(item(ledger, amount=900, day=TODAY), account_id=values["bank"]))
    filters = AnalyticsFilter(date(2026, 10, 1), TODAY, account_ids=(values["cash"],))
    report = analytics(ledger).build_report(filters)
    assert report.totals.gross_expense_minor == 1500
    assert report.comparison_totals is not None
    assert report.comparison_totals.gross_expense_minor == 1100
    assert report.comparison_start == date(2026, 9, 29)
    assert report.comparison_end == date(2026, 9, 30)


def test_refund_receipt_period_account_and_live_inherited_category(ledger: LedgerService) -> None:
    values = refs(ledger)
    expense = record(ledger, item(ledger, amount=2000, day=date(2026, 9, 30)))
    ledger.refund(
        RefundFields(expense, 500, values["bank"], TODAY),
        request_id=uid(),
        transaction_id=uid(),
    )
    category = uid()
    ledger.execute(uid(), "category.create.v1", {"id": category, "name": "家居", "kind": "expense"})
    ledger.execute(
        uid(),
        "transaction.update.v1",
        {
            "id": expense,
            "expected_version": 1,
            "fields": asdict(
                replace(item(ledger, amount=2000, day=date(2026, 9, 30)), category_id=category)
            ),
        },
    )
    filters = AnalyticsFilter(
        date(2026, 10, 1),
        TODAY,
        book_ids=(values["book"],),
        account_ids=(values["bank"],),
        category_ids=(category,),
    )
    report = analytics(ledger).build_report(filters)
    assert report.totals.gross_expense_minor == report.totals.income_minor == 0
    assert report.totals.refund_minor == report.totals.surplus_minor == 500
    assert report.totals.net_expense_minor == -500
    assert report.categories[0].name == "家居"
    assert report.categories[0].share is None
    assert report.categories[0].net_expense_minor == -500 and report.ranking == ()
    assert any("净支出为负值" in note for note in report.notes)
    cash = analytics(ledger).build_report(replace(filters, account_ids=(values["cash"],)))
    assert cash.totals.refund_minor == 0


def test_category_shares_use_gross_and_income_categories_do_not_appear(
    ledger: LedgerService,
) -> None:
    values = refs(ledger)
    category = uid()
    ledger.execute(uid(), "category.create.v1", {"id": category, "name": "家居", "kind": "expense"})
    first = record(ledger, item(ledger, amount=1000))
    record(ledger, replace(item(ledger, amount=3000), category_id=category))
    record(ledger, item(ledger, kind="income", amount=10_000))
    ledger.refund(
        RefundFields(first, 500, values["cash"], TODAY),
        request_id=uid(),
        transaction_id=uid(),
    )
    report = analytics(ledger).build_report(AnalyticsFilter(TODAY, TODAY))
    assert report.totals.net_expense_minor == 3500
    assert report.totals.surplus_minor == 6500
    assert report.totals.savings_rate == Decimal("0.65")
    assert len(report.categories) == 2
    assert report.categories[0].share == Decimal("0.75")
    assert report.categories[1].share == Decimal("0.25")
    assert sum(cast(Decimal, category.share) for category in report.categories) == 1
    assert report.categories[1].refund_minor == 500


def test_any_tag_exists_does_not_double_count_and_refund_tags_are_independent(
    ledger: LedgerService,
) -> None:
    tags = (uid(), uid(), uid())
    for number, tag in enumerate(tags):
        ledger.execute(uid(), "tag.create.v1", {"id": tag, "name": f"标签{number}"})
    values = refs(ledger)
    expense = record(ledger, item(ledger, amount=1000, tags=tags[:2]))
    record(ledger, item(ledger, amount=2000, tags=(tags[1],)))
    ledger.refund(
        RefundFields(expense, 500, values["cash"], TODAY, tag_ids=(tags[2],)),
        request_id=uid(),
        transaction_id=uid(),
    )
    filters = AnalyticsFilter(TODAY, TODAY, tag_ids=tags[:2])
    report = analytics(ledger).build_report(filters)
    assert report.totals.gross_expense_minor == 3000 and report.totals.expense_count == 2
    assert report.totals.refund_minor == 0
    refund = analytics(ledger).build_report(replace(filters, tag_ids=(tags[2],)))
    assert refund.totals.refund_minor == 500 and refund.totals.expense_count == 0


def test_archived_historical_filters_names_and_uuid_normalization(ledger: LedgerService) -> None:
    values = refs(ledger)
    book = uid()
    tag = uid()
    ledger.execute(uid(), "book.create.v1", {"id": book, "name": "旅行账本"})
    ledger.execute(uid(), "tag.create.v1", {"id": tag, "name": "历史标签"})
    record(ledger, replace(item(ledger, tags=(tag,)), book_id=book, account_id=values["bank"]))
    for entity, identifier in (
        ("book", book),
        ("account", values["bank"]),
        ("category", values["expense"]),
        ("tag", tag),
    ):
        ledger.execute(
            uid(),
            f"{entity}.archive.v1",
            {"id": identifier, "expected_version": 1, "archived": True},
        )
    report = analytics(ledger).build_report(
        AnalyticsFilter(
            TODAY,
            TODAY,
            book_ids=(book.upper(), book),
            account_ids=(values["bank"],),
            category_ids=(values["expense"],),
            tag_ids=(tag,),
        )
    )
    assert report.filters.book_ids == (book,)
    assert report.totals.gross_expense_minor == 2500
    assert sum("已归档" in label for label in report.scope_labels) == 4
    assert report.categories[0].name


def test_transfers_openings_adjustments_and_deleted_events_are_excluded(
    ledger: LedgerService,
) -> None:
    values = refs(ledger)
    identifier = record(ledger, item(ledger))
    ledger.execute(uid(), "transaction.delete.v1", {"id": identifier, "expected_version": 1})
    ledger.transfer(
        TransferFields(values["cash"], values["bank"], 1000, TODAY),
        request_id=uid(),
        transaction_id=uid(),
    )
    ledger.execute(
        uid(),
        "account.adjust.v1",
        {
            "account_id": values["bank"],
            "target_balance_minor": 5000,
            "occurred_on": TODAY,
            "reason": "合成测试",
        },
    )
    report = analytics(ledger).build_report(AnalyticsFilter(date(2026, 1, 1), TODAY))
    assert report.totals.gross_expense_minor == report.totals.income_minor == 0
    assert report.categories == () and report.ranking == ()
    ledger.execute(uid(), "transaction.restore.v1", {"id": identifier, "expected_version": 2})
    assert (
        analytics(ledger).build_report(AnalyticsFilter(TODAY, TODAY)).totals.gross_expense_minor
        == 2500
    )


def test_expense_ranking_dimension_metric_ties_missing_values_and_limit(
    ledger: LedgerService,
) -> None:
    record(ledger, item(ledger, amount=1000, merchant="A店", counterparty="朋友"))
    record(ledger, item(ledger, amount=1000, merchant="A店", counterparty="朋友"))
    record(ledger, item(ledger, amount=3000, merchant="B店", counterparty="同事"))
    record(ledger, item(ledger, amount=500))
    filters = AnalyticsFilter(TODAY, TODAY)
    service = analytics(ledger)
    amount = service.build_report(filters, rank_limit=2)
    assert [(row.label, row.amount_minor, row.count) for row in amount.ranking] == [
        ("B店", 3000, 1),
        ("A店", 2000, 2),
    ]
    count = service.build_report(filters, ranking_metric="count")
    assert count.ranking[0].label == "A店"
    assert count.ranking[-1].label == "（未填写商户）"
    people = service.build_report(filters, ranking_dimension="counterparty")
    assert people.ranking[0].label == "同事"
    category = service.build_report(filters, ranking_dimension="category")
    assert len(category.ranking) == 1 and category.ranking[0].amount_minor == 5500
    assert category.ranking[0].count == 4


def test_literal_sql_like_merchant_names_remain_separate(ledger: LedgerService) -> None:
    for merchant in ("100%", "a_b", "' OR 1=1 --", "café"):
        record(ledger, item(ledger, merchant=merchant))
    report = analytics(ledger).build_report(AnalyticsFilter(TODAY, TODAY))
    assert len(report.ranking) == 4 and report.totals.expense_count == 4


def test_occurrence_calendar_date_respects_zone_without_utc_rebucketing(
    ledger: LedgerService,
) -> None:
    fields = replace(
        item(ledger),
        time_zone="Asia/Shanghai",
        occurrence_precision="exact",
        occurred_at_utc=datetime(2026, 10, 1, 23, 30, tzinfo=UTC),
    )
    record(ledger, fields)
    assert analytics(ledger).build_report(AnalyticsFilter(TODAY, TODAY)).totals.expense_count == 1
    previous = analytics(ledger).build_report(AnalyticsFilter(date(2026, 10, 1), date(2026, 10, 1)))
    assert previous.totals.expense_count == 0


def test_decimal_precision_is_independent_of_callers_context(ledger: LedgerService) -> None:
    record(ledger, item(ledger, kind="income", amount=300))
    record(ledger, item(ledger, amount=100))
    with localcontext() as context:
        context.prec = 2
        report = analytics(ledger).build_report(AnalyticsFilter(TODAY, TODAY))
    assert report.totals.savings_rate is not None
    assert str(report.totals.savings_rate).startswith("0.666666666666666666666666666666")


@pytest.mark.parametrize(
    "changes",
    [
        {"start_on": datetime(2026, 10, 1, tzinfo=UTC)},
        {"start_on": "2026-10-01"},
        {"end_on": None},
        {"start_on": TODAY, "end_on": date(2026, 10, 1)},
        {"start_on": date(2016, 10, 1), "end_on": TODAY},
        {"book_ids": [uid()]},
        {"account_ids": (123,)},
        {"category_ids": ("not-a-uuid",)},
        {"tag_ids": tuple(uid() for _ in range(201))},
    ],
)
def test_invalid_runtime_filter_is_rejected_before_database_read(
    ledger: LedgerService, changes: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    filters = AnalyticsFilter(TODAY, TODAY)
    for key, value in changes.items():
        object.__setattr__(filters, key, value)

    def reject_read() -> None:
        pytest.fail("invalid filters must not open the database")

    monkeypatch.setattr(ledger.database, "read", reject_read)
    with pytest.raises(LedgerError, match="INVALID_FILTER"):
        analytics(ledger).build_report(filters)


@pytest.mark.parametrize(
    "options",
    [
        {"rank_limit": 0},
        {"rank_limit": 101},
        {"rank_limit": True},
        {"ranking_dimension": "note"},
        {"ranking_dimension": []},
        {"ranking_metric": "SUM"},
    ],
)
def test_invalid_ranking_options(ledger: LedgerService, options: dict[str, object]) -> None:
    with pytest.raises(LedgerError, match="INVALID_FILTER"):
        analytics(ledger).build_report(
            AnalyticsFilter(TODAY, TODAY),
            ranking_dimension=cast(str, options.get("ranking_dimension", "merchant")),
            ranking_metric=cast(str, options.get("ranking_metric", "amount")),
            rank_limit=cast(int, options.get("rank_limit", 10)),
        )


def test_minimum_date_has_unavailable_comparison_and_maximum_date_is_supported(
    ledger: LedgerService,
) -> None:
    service = analytics(ledger)
    first = service.build_report(AnalyticsFilter(date.min, date(1, 1, 31)))
    assert first.comparison_start is None and first.comparison_end is None
    assert first.comparison_totals is None
    assert first.months[0].month == "0001-01"
    last = service.build_report(AnalyticsFilter(date(9999, 12, 1), date.max))
    assert last.months[0].month == "9999-12" and last.comparison_end == date(9999, 11, 30)
    assert (
        service.build_report(AnalyticsFilter(date(1, 1, 1), date(10, 12, 31))).months[-1].month
        == "0010-12"
    )


def test_unknown_reference_is_an_empty_explicit_scope(ledger: LedgerService) -> None:
    missing = uid()
    report = analytics(ledger).build_report(AnalyticsFilter(TODAY, TODAY, book_ids=(missing,)))
    assert report.totals.expense_count == 0
    assert any(missing in label and "未找到" in label for label in report.scope_labels)


def test_every_component_shares_snapshot_during_a_concurrent_write(
    ledger: LedgerService, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = analytics(ledger)
    filters = AnalyticsFilter(TODAY, TODAY)
    before = service.build_report(filters)
    reached_commit = Event()
    original_read = ledger.database.read
    pending: list[Future[object]] = []
    waits: list[bool] = []

    def observe(stage: str) -> None:
        if stage == "before_commit":
            reached_commit.set()

    writer = LedgerService(ledger.database, clock=ledger.clock, fault_hook=observe)
    fields = item(ledger, merchant="并发店")
    with ThreadPoolExecutor(max_workers=1) as executor:

        def trace(statement: str) -> None:
            if statement.startswith("SELECT flow.kind") and not pending:
                pending.append(
                    executor.submit(writer.record, fields, request_id=uid(), transaction_id=uid())
                )
                waits.append(reached_commit.wait(3))

        @contextmanager
        def traced_read() -> Iterator[Connection]:
            with original_read() as connection:
                connection.set_trace_callback(trace)
                yield connection

        monkeypatch.setattr(ledger.database, "read", traced_read)
        during = service.build_report(filters)
        pending[0].result(timeout=5)
    monkeypatch.setattr(ledger.database, "read", original_read)
    after = service.build_report(filters)
    assert waits == [True]
    assert during.totals == before.totals
    assert during.months == before.months and during.categories == () and during.ranking == ()
    assert during.comparison_totals == before.comparison_totals
    assert (
        during.scope_labels == before.scope_labels and during.data_revision == before.data_revision
    )
    assert after.totals.expense_count == 1 and after.ranking[0].label == "并发店"
    assert after.data_revision > during.data_revision


def test_turnover_above_int64_remains_exact_in_every_report_component(
    ledger: LedgerService,
) -> None:
    values = refs(ledger)
    pair_count = MAX_INT64 // MAX_EVENT_MINOR + 1
    with ledger.database.write() as connection:
        connection.execute(
            "WITH RECURSIVE numbers(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM numbers WHERE n<?) "
            "INSERT INTO transactions(id,kind,amount_minor,book_id,category_id,occurred_on,"
            "time_zone,merchant,created_at_utc,updated_at_utc) "
            "SELECT printf('00000000-0000-4000-8000-%012d',n),"
            "CASE WHEN n%2=1 THEN 'income' ELSE 'expense' END,?, ?,"
            "CASE WHEN n%2=1 THEN ? ELSE ? END,'2026-10-02','UTC','大额测试店',"
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
            "SELECT SUM(amount_minor) FROM transactions WHERE kind='expense'"
        ).fetchone()
    report = analytics(ledger).build_report(AnalyticsFilter(TODAY, TODAY))
    expected = pair_count * MAX_EVENT_MINOR
    assert expected > MAX_INT64
    assert report.totals.income_minor == report.totals.gross_expense_minor == expected
    assert report.months[0].totals.gross_expense_minor == expected
    assert report.categories[0].gross_expense_minor == report.ranking[0].amount_minor == expected
    assert report.categories[0].share == 1 and report.totals.savings_rate == 0
    assert report.totals.net_expense_minor == expected and report.totals.surplus_minor == 0
    assert report.totals.expense_count == report.ranking[0].count == pair_count
    assert ledger.total_assets() == 100_000
