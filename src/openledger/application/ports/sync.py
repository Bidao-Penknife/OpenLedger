"""Reserved versioned synchronization DTOs and ports; no transport is implemented.

Money values ending in ``_minor`` cross the future boundary as decimal strings.
Incoming changes carry expected versions and must return conflicts instead of
silently choosing a winner. Implementations must reuse the ledger command path.
"""

import re
from dataclasses import dataclass
from typing import Literal, Protocol

from openledger.domain.errors import LedgerError


@dataclass(frozen=True, slots=True)
class SyncChange:
    """One versioned change with immutable serialized fields and exact integer money."""

    entity: str
    entity_id: str
    version: int
    change_seq: int
    operation: Literal["create", "update", "soft_delete", "restore", "archive", "unarchive"]
    fields: tuple[tuple[str, str], ...]
    expected_version: int | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.entity, str)
            or not self.entity
            or not isinstance(self.entity_id, str)
            or not self.entity_id
            or type(self.version) is not int
            or self.version < 1
            or type(self.change_seq) is not int
            or self.change_seq < 1
            or self.operation
            not in {"create", "update", "soft_delete", "restore", "archive", "unarchive"}
            or (
                self.expected_version is not None
                and (type(self.expected_version) is not int or self.expected_version < 1)
            )
            or (self.operation != "create" and self.expected_version is None)
        ):
            raise LedgerError("INVALID_SYNC_CHANGE")
        names: set[str] = set()
        if not isinstance(self.fields, tuple):
            raise LedgerError("INVALID_SYNC_CHANGE")
        for pair in self.fields:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise LedgerError("INVALID_SYNC_CHANGE")
            name, value = pair
            if (
                not isinstance(name, str)
                or not name
                or name in names
                or not isinstance(value, str)
                or (name.endswith("_minor") and re.fullmatch(r"-?(?:0|[1-9][0-9]*)", value) is None)
            ):
                raise LedgerError("INVALID_SYNC_CHANGE")
            names.add(name)


@dataclass(frozen=True, slots=True)
class SyncEnvelope:
    """A source revision and ordered change sequence for a future transport adapter."""

    device_id: str
    after_change_seq: int
    through_change_seq: int
    changes: tuple[SyncChange, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != 1
            or not isinstance(self.device_id, str)
            or not self.device_id
            or type(self.after_change_seq) is not int
            or type(self.through_change_seq) is not int
            or self.after_change_seq < 0
            or self.through_change_seq < self.after_change_seq
            or not isinstance(self.changes, tuple)
        ):
            raise LedgerError("INVALID_SYNC_ENVELOPE")
        previous = self.after_change_seq
        for change in self.changes:
            if not isinstance(change, SyncChange) or not previous < change.change_seq <= (
                self.through_change_seq
            ):
                raise LedgerError("INVALID_SYNC_ENVELOPE")
            previous = change.change_seq


@dataclass(frozen=True, slots=True)
class SyncConflict:
    """An explicit version conflict requiring application policy or user resolution."""

    entity: str
    entity_id: str
    expected_version: int
    current_version: int | None
    reason_code: str


@dataclass(frozen=True, slots=True)
class SyncResult:
    """Acknowledge an accepted sequence while exposing unresolved conflicts."""

    accepted_through_change_seq: int
    conflicts: tuple[SyncConflict, ...] = ()


class CloudSyncPort(Protocol):
    """Future cloud transport; it receives serialized DTOs, never SQLite handles."""

    def push(self, envelope: SyncEnvelope) -> SyncResult:
        """Send reviewed local changes and return version conflicts explicitly."""
        ...

    def pull(self, *, after_change_seq: int, limit: int) -> SyncEnvelope:
        """Retrieve a bounded immutable envelope without applying its contents."""
        ...


class DataSyncPort(Protocol):
    """Future application boundary for revisioned export and validated import."""

    def export_changes(self, *, after_change_seq: int, limit: int) -> SyncEnvelope:
        """Read one stable local revision and serialize money as integer strings."""
        ...

    def apply_changes(self, envelope: SyncEnvelope, *, expected_revision: int) -> SyncResult:
        """Use ledger transactions and optimistic versions; never overwrite conflicts."""
        ...
