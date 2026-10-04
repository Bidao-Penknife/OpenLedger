"""Immutable command results safe to pass from a worker to presentation code."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EntityRevision:
    """One committed revision of an aggregate."""

    entity_type: str
    id: str
    version: int
    operation: str


@dataclass(frozen=True, slots=True)
class BalanceChange:
    """An account balance before and after this particular commit."""

    account_id: str
    before_minor: int
    after_minor: int
    currency_code: str = "CNY"


@dataclass(frozen=True, slots=True)
class MutationResult:
    """A durable result; replay retains the original commit's balances and time."""

    request_id: str
    command_type: str
    outcome: str
    committed_at_utc: str
    entity_ids: tuple[str, ...]
    changed_entities: tuple[EntityRevision, ...]
    balance_changes: tuple[BalanceChange, ...]
    change_seq: int
    data: dict[str, object]
    replayed: bool = False
