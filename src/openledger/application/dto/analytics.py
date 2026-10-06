"""Immutable analytical snapshots shared by desktop charts and report exports."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class AnalyticsFilter:
    """Inclusive calendar dates and optional historical reference selections.

    References within a dimension match any selected ID; dimensions intersect.
    Tag selections match any selected tag on the event itself, including refunds.
    """

    start_on: date
    end_on: date
    book_ids: tuple[str, ...] = ()
    account_ids: tuple[str, ...] = ()
    category_ids: tuple[str, ...] = ()
    tag_ids: tuple[str, ...] = ()
    currency_code: str = "CNY"


@dataclass(frozen=True)
class PeriodTotals:
    """Exact CNY fen and event counts; savings rate is a ratio, not a percent."""

    income_minor: int
    gross_expense_minor: int
    refund_minor: int
    net_expense_minor: int
    surplus_minor: int
    income_count: int
    expense_count: int
    refund_count: int
    savings_rate: Decimal | None


@dataclass(frozen=True)
class MonthTotals:
    """One calendar month, including a zero total for a month without events."""

    month: str
    totals: PeriodTotals


@dataclass(frozen=True)
class CategoryTotals:
    """Expense category totals; share uses gross expenses as its denominator."""

    category_id: str
    name: str
    gross_expense_minor: int
    refund_minor: int
    net_expense_minor: int
    expense_count: int
    share: Decimal | None


@dataclass(frozen=True)
class ExpenseRank:
    """Gross expense ranking; refunds never silently alter the ranking basis."""

    label: str
    amount_minor: int
    count: int


@dataclass(frozen=True)
class ReportData:
    """All analytical components and labels captured from one SQLite snapshot."""

    filters: AnalyticsFilter
    totals: PeriodTotals
    months: tuple[MonthTotals, ...]
    categories: tuple[CategoryTotals, ...]
    ranking: tuple[ExpenseRank, ...]
    comparison_totals: PeriodTotals | None
    comparison_start: date | None
    comparison_end: date | None
    notes: tuple[str, ...]
    scope_labels: tuple[str, ...]
    data_revision: int
    generated_at_utc: str
    ranking_dimension: str = "merchant"
    ranking_metric: str = "amount"

    @property
    def comparison_start_on(self) -> date | None:
        """Expose an explicit calendar-date name for presentation clients."""
        return self.comparison_start

    @property
    def comparison_end_on(self) -> date | None:
        """Expose an explicit calendar-date name for presentation clients."""
        return self.comparison_end
