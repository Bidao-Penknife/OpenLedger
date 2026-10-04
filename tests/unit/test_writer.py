"""Pending cancellation and immutable intent snapshots at the worker boundary."""

from collections.abc import Mapping
from threading import Event

from openledger.application.dto.results import MutationResult
from openledger.application.writer import LedgerWriter


class RecordingLedger:
    """Block one command so cancellation and mutation races can be reproduced."""

    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.commands: list[dict[str, object]] = []

    def execute(
        self, request_id: str, command_type: str, payload: Mapping[str, object]
    ) -> MutationResult:
        self.started.set()
        assert self.release.wait(timeout=5)
        self.commands.append(dict(payload))
        return MutationResult(
            request_id, command_type, "applied", "2026-10-02T00:00:00.000Z", (), (), (), 0, {}
        )

    def balances(self) -> dict[str, int]:
        return {}

    def transaction(self, transaction_id: str) -> dict[str, object]:
        return {}


def test_pending_cancel_has_no_execution_and_payload_is_copied_deeply() -> None:
    ledger = RecordingLedger()
    writer = LedgerWriter(ledger)
    try:
        first = writer.submit("first", "test", {"fields": {"amount_minor": 100}})
        assert ledger.started.wait(timeout=5)
        nested = {"amount_minor": 200}
        second = writer.submit("second", "test", {"fields": nested})
        cancelled = writer.submit("cancelled", "test", {})
        assert cancelled.cancel()
        nested["amount_minor"] = 999
        ledger.release.set()
        assert first.result(timeout=5).outcome == "applied"
        assert second.result(timeout=5).outcome == "applied"
        assert ledger.commands == [
            {"fields": {"amount_minor": 100}},
            {"fields": {"amount_minor": 200}},
        ]
        assert not first.cancel()
    finally:
        ledger.release.set()
        writer.close()
