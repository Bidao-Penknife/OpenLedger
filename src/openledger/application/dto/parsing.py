"""Immutable, evidence-bearing parsing contracts with no persistence authority."""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

Origin = Literal["explicit", "rule_suggestion", "default", "ai_suggestion", "user"]
ParseStatus = Literal["single", "multiple_events", "ambiguous", "unsupported"]


@dataclass(frozen=True)
class Span:
    """Half-open character indices into the unmodified request text."""

    start: int
    end: int


@dataclass(frozen=True)
class ParseChoice:
    """A permitted identifier and display label, without financial history."""

    id: str
    name: str
    kind: str | None = None
    aliases: tuple[str, ...] = ()
    currency_code: str = "CNY"


@dataclass(frozen=True)
class ChannelAccountMapping:
    """An account association explicitly configured by the user."""

    payment_method_id: str
    account_id: str


@dataclass(frozen=True)
class ParseRequest:
    """A revisioned draft and its minimal, caller-supplied business context."""

    draft_id: str
    revision: int
    text: str
    reference_date: date
    time_zone: str
    current_book_id: str | None = None
    default_account_id: str | None = None
    category_choices: tuple[ParseChoice, ...] = ()
    account_choices: tuple[ParseChoice, ...] = ()
    payment_method_choices: tuple[ParseChoice, ...] = ()
    channel_account_mappings: tuple[ChannelAccountMapping, ...] = ()
    locale: str = "zh_CN"
    currency_code: str = "CNY"


@dataclass(frozen=True)
class FieldCandidate[T]:
    """A value, its source and alternatives; a candidate is never a saved value."""

    value: T | None
    origin: Origin = "explicit"
    evidence_spans: tuple[Span, ...] = ()
    rule_id: str | None = None
    alternatives: tuple[T, ...] = ()
    requires_confirmation: bool = True
    reason_code: str | None = None


def _missing[T]() -> FieldCandidate[T]:
    return FieldCandidate(None, reason_code="MISSING_REQUIRED_FIELD")


@dataclass(frozen=True)
class ParsedDraft:
    """One editable transaction suggestion, deliberately separate from write DTOs."""

    candidate_id: str
    span: Span
    time_zone: str
    kind: FieldCandidate[str] = field(default_factory=_missing)
    amount_minor: FieldCandidate[int] = field(default_factory=_missing)
    occurred_on: FieldCandidate[date] = field(default_factory=_missing)
    time_period: FieldCandidate[str] = field(default_factory=lambda: FieldCandidate(None))
    occurred_at_utc: FieldCandidate[datetime] = field(default_factory=lambda: FieldCandidate(None))
    book_id: FieldCandidate[str] = field(default_factory=_missing)
    account_id: FieldCandidate[str] = field(default_factory=_missing)
    category_id: FieldCandidate[str] = field(default_factory=_missing)
    payment_method_id: FieldCandidate[str] = field(default_factory=lambda: FieldCandidate(None))
    counterparty: FieldCandidate[str] = field(default_factory=lambda: FieldCandidate(None))
    merchant: FieldCandidate[str] = field(default_factory=lambda: FieldCandidate(None))
    location: FieldCandidate[str] = field(default_factory=lambda: FieldCandidate(None))
    note: FieldCandidate[str] = field(default_factory=lambda: FieldCandidate(None))
    currency_code: str = "CNY"

    @property
    def occurrence_precision(self) -> str:
        """Derive precision without inventing an instant for a vague time period."""
        if self.occurred_at_utc.value is not None:
            return "exact"
        if self.time_period.value is not None:
            return "period"
        return "date"


@dataclass(frozen=True)
class ParseIssue:
    """A user-visible, optionally blocking issue scoped to a candidate or request."""

    code: str
    field: str
    message: str
    span: Span | None = None
    candidate_id: str | None = None
    blocking: bool = True


@dataclass(frozen=True)
class ParseResult:
    """A parse result whose draft identifier and revision must match before applying."""

    draft_id: str
    revision: int
    provider_id: str
    status: ParseStatus
    drafts: tuple[ParsedDraft, ...]
    issues: tuple[ParseIssue, ...]
    unassigned_spans: tuple[Span, ...] = ()
