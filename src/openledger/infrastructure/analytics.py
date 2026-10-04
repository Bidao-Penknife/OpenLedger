"""Read-only, exact analytical composition over a single SQLite snapshot."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext

from openledger.application.dto.analytics import (
    AnalyticsFilter,
    CategoryTotals,
    ExpenseRank,
    MonthTotals,
    PeriodTotals,
    ReportData,
)
from openledger.domain.errors import LedgerError
from openledger.domain.values import normalize_id, utc_now, utc_text
from openledger.infrastructure.database.database import Database

_DIMENSIONS = {"merchant": "商户", "counterparty": "对象", "category": "分类"}
_METRICS = {"amount": "金额", "count": "笔数"}
_REFERENCE_TABLES = (
    ("book_ids", "books", "账本"),
    ("account_ids", "accounts", "账户"),
    ("category_ids", "categories", "分类"),
    ("tag_ids", "tags", "标签"),
)


def _ratio(numerator: int, denominator: int) -> Decimal | None:
    """Compute a display ratio without float conversion or global Decimal state."""
    if denominator == 0:
        return None
    with localcontext() as context:
        context.prec = 40
        return Decimal(numerator) / Decimal(denominator)


@dataclass
class _Totals:
    income: int = 0
    expense: int = 0
    refund: int = 0
    income_count: int = 0
    expense_count: int = 0
    refund_count: int = 0

    def add(self, kind: str, amount: int) -> None:
        if kind == "income":
            self.income += amount
            self.income_count += 1
        elif kind == "expense":
            self.expense += amount
            self.expense_count += 1
        elif kind == "expense_refund":
            self.refund += amount
            self.refund_count += 1

    def freeze(self) -> PeriodTotals:
        net = self.expense - self.refund
        surplus = self.income - net
        return PeriodTotals(
            self.income,
            self.expense,
            self.refund,
            net,
            surplus,
            self.income_count,
            self.expense_count,
            self.refund_count,
            _ratio(surplus, self.income),
        )


def _validate(filters: AnalyticsFilter) -> AnalyticsFilter:
    if not isinstance(filters, AnalyticsFilter):
        raise LedgerError("INVALID_FILTER")
    if (
        type(filters.start_on) is not date
        or type(filters.end_on) is not date
        or filters.start_on > filters.end_on
        or (filters.end_on.year - filters.start_on.year) * 12
        + filters.end_on.month
        - filters.start_on.month
        + 1
        > 120
    ):
        raise LedgerError("INVALID_FILTER")
    identifiers: dict[str, tuple[str, ...]] = {}
    for attribute, _, _ in _REFERENCE_TABLES:
        values = getattr(filters, attribute)
        if not isinstance(values, tuple) or len(values) > 200:
            raise LedgerError("INVALID_FILTER")
        try:
            identifiers[attribute] = tuple(dict.fromkeys(normalize_id(value) for value in values))
        except LedgerError as error:
            raise LedgerError("INVALID_FILTER") from error
    return AnalyticsFilter(filters.start_on, filters.end_on, **identifiers)


def _months(start: date, end: date) -> Iterator[str]:
    current = start.year * 12 + start.month - 1
    last = end.year * 12 + end.month - 1
    while current <= last:
        year, zero_month = divmod(current, 12)
        yield f"{year:04d}-{zero_month + 1:02d}"
        current += 1


def _comparison(filters: AnalyticsFilter) -> tuple[date | None, date | None]:
    length = (filters.end_on - filters.start_on).days + 1
    try:
        return filters.start_on - timedelta(days=length), filters.start_on - timedelta(days=1)
    except OverflowError:
        return None, None


def _where(filters: AnalyticsFilter, start: date, end: date) -> tuple[str, list[str]]:
    clauses = ["flow.occurred_on>=?", "flow.occurred_on<=?"]
    parameters = [start.isoformat(), end.isoformat()]
    for attribute, column in (
        ("book_ids", "book_id"),
        ("account_ids", "account_id"),
        ("category_ids", "category_id"),
    ):
        values = getattr(filters, attribute)
        if values:
            clauses.append(f"flow.{column} IN ({','.join('?' for _ in values)})")
            parameters.extend(values)
    if filters.tag_ids:
        clauses.append(
            "EXISTS(SELECT 1 FROM transaction_tags selected_tag "
            "WHERE selected_tag.transaction_id=flow.transaction_id "
            f"AND selected_tag.tag_id IN ({','.join('?' for _ in filters.tag_ids)}))"
        )
        parameters.extend(filters.tag_ids)
    return " AND ".join(clauses), parameters


def _scope_labels(connection: sqlite3.Connection, filters: AnalyticsFilter) -> tuple[str, ...]:
    labels = [f"日期：{filters.start_on.isoformat()} 至 {filters.end_on.isoformat()}（含首尾日期）"]
    for attribute, table, heading in _REFERENCE_TABLES:
        values: tuple[str, ...] = getattr(filters, attribute)
        if not values:
            labels.append(heading + "：全部（包含已归档历史）")
            continue
        placeholders = ",".join("?" for _ in values)
        names = {
            str(row["id"]): str(row["name"]) + ("（已归档）" if row["is_archived"] else "")
            for row in connection.execute(
                f"SELECT id,name,is_archived FROM {table} WHERE id IN ({placeholders})", values
            )
        }
        labels.append(
            heading + "：" + "、".join(names.get(value, f"未找到（{value}）") for value in values)
        )
    labels.append("标签筛选：任一选中标签匹配；退款使用退款记录自身的标签")
    return tuple(labels)


def _notes(
    totals: PeriodTotals,
    comparison_start: date | None,
    comparison_end: date | None,
    ranking_dimension: str,
    ranking_metric: str,
) -> tuple[str, ...]:
    notes = [
        f"本期记录 {totals.income_count} 笔收入、{totals.expense_count} 笔支出、"
        f"{totals.refund_count} 笔退款。",
        "净支出＝支出原额－退款；结余＝收入－净支出。退款按实际到账日期及到账账户统计，"
        "账本和分类继承原支出。",
        "转账、期初余额及余额校准不计入收入和支出；已删除记录不计入统计。",
        "分类占比以支出原额为分母，退款单列，避免负数占比。",
        f"消费排行仅统计支出原额，不抵扣退款；按{_DIMENSIONS[ranking_dimension]}分组，"
        f"按{_METRICS[ranking_metric]}排序。",
    ]
    if comparison_start is not None and comparison_end is not None:
        notes.append(
            f"比较期间为前一段等长日历日期：{comparison_start.isoformat()} 至 "
            f"{comparison_end.isoformat()}（含首尾日期），使用相同筛选条件。"
        )
    else:
        notes.append("所选日期接近日历起点，无法构造完整前期，前期比较不可用。")
    if totals.income_minor == 0:
        notes.append("本期收入为零，结余率没有定义，不以零或百分之百替代。")
    if totals.gross_expense_minor == 0:
        notes.append("本期支出原额为零，分类占比没有定义。")
    if totals.net_expense_minor < 0:
        notes.append("本期退款超过本期支出原额，净支出为负值。")
    notes.append("金额均为人民币；收入、支出及退款按记录的发生日期统计。")
    return tuple(notes)


class AnalyticsService:
    """Build read-only cash-flow reports using Python integers for every sum."""

    def __init__(self, database: Database, clock: Callable[[], datetime] = utc_now) -> None:
        self.database = database
        self.clock = clock

    def build_report(
        self,
        filters: AnalyticsFilter,
        *,
        ranking_dimension: str = "merchant",
        ranking_metric: str = "amount",
        rank_limit: int = 10,
    ) -> ReportData:
        """Capture totals, months, categories, ranks, labels and comparison together.

        Filter dates are inclusive calendar dates already persisted by the writer.
        The previous period contains the same number of calendar days. SQLite SUM
        is deliberately unused: valid turnover can exceed its signed 64-bit range.
        """
        filters = _validate(filters)
        if (
            not isinstance(ranking_dimension, str)
            or ranking_dimension not in _DIMENSIONS
            or not isinstance(ranking_metric, str)
            or ranking_metric not in _METRICS
            or type(rank_limit) is not int
            or not 1 <= rank_limit <= 100
        ):
            raise LedgerError("INVALID_FILTER")
        generated_at = utc_text(self.clock())
        comparison_start, comparison_end = _comparison(filters)
        totals = _Totals()
        comparison = _Totals()
        months = {month: _Totals() for month in _months(filters.start_on, filters.end_on)}
        categories: dict[str, tuple[str, _Totals]] = {}
        ranking: dict[str, tuple[str, int, int]] = {}
        scan_start = comparison_start if comparison_start is not None else filters.start_on
        where, parameters = _where(filters, scan_start, filters.end_on)
        with self.database.read() as connection:
            revision = int(
                connection.execute("SELECT COALESCE(MAX(seq),0) FROM change_log").fetchone()[0]
            )
            labels = _scope_labels(connection, filters)
            for row in connection.execute(
                "SELECT flow.kind,flow.amount_minor,flow.occurred_on,flow.category_id,"
                "flow.merchant,flow.counterparty,category.name AS category_name "
                "FROM v_cashflow_transactions flow "
                "LEFT JOIN categories category ON category.id=flow.category_id WHERE " + where,
                parameters,
            ):
                kind = str(row["kind"])
                amount = int(row["amount_minor"])
                day = str(row["occurred_on"])
                if day < filters.start_on.isoformat():
                    comparison.add(kind, amount)
                    continue
                totals.add(kind, amount)
                months[day[:7]].add(kind, amount)
                if kind in {"expense", "expense_refund"}:
                    category_id = str(row["category_id"])
                    category_name = str(row["category_name"])
                    if category_id not in categories:
                        categories[category_id] = (category_name, _Totals())
                    categories[category_id][1].add(kind, amount)
                if kind == "expense":
                    if ranking_dimension == "category":
                        key = str(row["category_id"])
                        label = str(row["category_name"])
                    else:
                        label = str(
                            row[ranking_dimension] or f"（未填写{_DIMENSIONS[ranking_dimension]}）"
                        )
                        key = "missing:" if row[ranking_dimension] is None else "value:" + label
                    previous = ranking.get(key, (label, 0, 0))
                    ranking[key] = (label, previous[1] + amount, previous[2] + 1)
        frozen_totals = totals.freeze()
        frozen_categories = tuple(
            sorted(
                (
                    CategoryTotals(
                        category_id,
                        label,
                        amounts.expense,
                        amounts.refund,
                        amounts.expense - amounts.refund,
                        amounts.expense_count,
                        _ratio(amounts.expense, totals.expense),
                    )
                    for category_id, (label, amounts) in categories.items()
                ),
                key=lambda item: (-item.gross_expense_minor, item.name, item.category_id),
            )
        )
        ordered_ranks = sorted(
            ranking.items(),
            key=(
                (lambda item: (-item[1][1], -item[1][2], item[1][0], item[0]))
                if ranking_metric == "amount"
                else (lambda item: (-item[1][2], -item[1][1], item[1][0], item[0]))
            ),
        )
        return ReportData(
            filters,
            frozen_totals,
            tuple(MonthTotals(month, amounts.freeze()) for month, amounts in months.items()),
            frozen_categories,
            tuple(
                ExpenseRank(label, amount, count)
                for _, (label, amount, count) in ordered_ranks[:rank_limit]
            ),
            comparison.freeze() if comparison_start is not None else None,
            comparison_start,
            comparison_end,
            _notes(
                frozen_totals,
                comparison_start,
                comparison_end,
                ranking_dimension,
                ranking_metric,
            ),
            labels,
            revision,
            generated_at,
            ranking_dimension,
            ranking_metric,
        )
