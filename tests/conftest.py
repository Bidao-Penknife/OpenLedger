"""Isolated ledger fixtures with an injected clock and synthetic balances."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from openledger.infrastructure.database.database import Database
from openledger.infrastructure.ledger import LedgerService

NOW = datetime(2026, 10, 2, 12, tzinfo=UTC)


@pytest.fixture
def ledger(tmp_path: Path) -> LedgerService:
    """Create a disposable real SQLite ledger; never read user data."""
    database = Database(tmp_path / "ledger.sqlite3")
    database.initialize()
    service = LedgerService(database, clock=lambda: NOW)
    service.ensure_defaults()
    for name, kind, opening in [("测试现金", "cash", 100_000), ("测试银行", "bank", 0)]:
        service.execute(
            str(uuid4()),
            "account.create.v1",
            {
                "id": str(uuid4()),
                "name": name,
                "account_type": kind,
                "balance_start_on": "2026-01-01",
                "opening_balance_minor": opening,
            },
        )
    return service
