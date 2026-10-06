"""Toolkit-free immutable input contracts; application services validate them."""

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class TransactionFields:
    """Full input fields for an income or expense create/replacement command."""

    kind: str
    amount_minor: int
    account_id: str
    book_id: str
    category_id: str
    occurred_on: date
    time_zone: str = "UTC"
    occurrence_precision: str = "date"
    time_period: str | None = None
    occurred_at_utc: datetime | None = None
    payment_method_id: str | None = None
    counterparty: str | None = None
    merchant: str | None = None
    location: str | None = None
    note: str | None = None
    tag_ids: tuple[str, ...] = ()
    source: str = "manual"
    source_text: str | None = None
    currency_code: str = "CNY"


@dataclass(frozen=True, slots=True)
class RefundFields:
    """Refund input; book/category are inherited from the original expense."""

    original_transaction_id: str
    amount_minor: int
    account_id: str
    occurred_on: date
    time_zone: str = "UTC"
    occurrence_precision: str = "date"
    time_period: str | None = None
    occurred_at_utc: datetime | None = None
    payment_method_id: str | None = None
    note: str | None = None
    tag_ids: tuple[str, ...] = ()
    source: str = "manual"
    source_text: str | None = None
    currency_code: str = "CNY"


@dataclass(frozen=True, slots=True)
class TransferFields:
    """Conserved two-account transfer input with no book/category attribution."""

    from_account_id: str
    to_account_id: str
    amount_minor: int
    occurred_on: date
    time_zone: str = "UTC"
    occurrence_precision: str = "date"
    time_period: str | None = None
    occurred_at_utc: datetime | None = None
    note: str | None = None
    tag_ids: tuple[str, ...] = ()
    source: str = "manual"
    source_text: str | None = None
    currency_code: str = "CNY"
    to_amount_minor: int | None = None
