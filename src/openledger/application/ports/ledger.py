"""Persistence-independent entry point for financial commands and snapshots."""

from collections.abc import Mapping
from datetime import date
from typing import Protocol

from openledger.application.dto.queries import Overview, TransactionFilter, TransactionPage
from openledger.application.dto.results import MutationResult


class LedgerPort(Protocol):
    """All mutations use one command boundary, including retries and management."""

    def execute(
        self, request_id: str, command_type: str, payload: Mapping[str, object]
    ) -> MutationResult:
        """Return success only after the transaction commits."""
        ...

    def balances(self) -> dict[str, int]:
        """Return all account balances from one consistent read snapshot."""
        ...

    def transaction(self, transaction_id: str) -> dict[str, object]:
        """Return one complete aggregate, including effective refund attribution."""
        ...


class LedgerQueriesPort(Protocol):
    """Read-only views kept separate from the command-writing capability."""

    def preferences(self) -> dict[str, object]:
        """Read the persisted default selections."""
        ...

    def transactions(self, filters: TransactionFilter) -> TransactionPage:
        """Capture rows, count and change cursor in one read snapshot."""
        ...

    def overview(self, reference_date: date) -> Overview:
        """Return exact all-account assets and the reference month's cash flow."""
        ...
