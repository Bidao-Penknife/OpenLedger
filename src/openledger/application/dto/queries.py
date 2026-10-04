"""Read models for stable transaction pages and the monthly dashboard."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class TransactionFilter:
    """Filter transactions by effective book, either transfer account and literal text."""

    book_id: str | None = None
    account_id: str | None = None
    kind: str | None = None
    start_on: date | None = None
    end_on: date | None = None
    include_deleted: bool = False
    search: str = ""
    page: int = 0
    page_size: int = 50
    category_id: str | None = None
    tag_id: str | None = None


@dataclass(frozen=True)
class TransactionPage:
    """Rows, count and revision cursor captured inside the same SQLite snapshot."""

    rows: tuple[dict[str, object], ...]
    total: int
    page: int
    page_size: int
    change_seq: int


@dataclass(frozen=True)
class Overview:
    """Exact monthly cash flow and all-account assets, including archived accounts."""

    balances: dict[str, int]
    total_assets_minor: int
    income_minor: int
    gross_expense_minor: int
    refund_minor: int
    expense_minor: int
    net_minor: int
    month_start: date
    month_end: date
    change_seq: int
