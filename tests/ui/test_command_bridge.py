"""Queued GUI delivery, duplicate suppression and real-command retry boundaries."""

from collections.abc import Mapping
from dataclasses import asdict
from datetime import date
from threading import Event, Thread, get_ident
from typing import cast
from uuid import uuid4

import pytest
from pytestqt.qtbot import QtBot

from openledger.application.dto.ledger import TransactionFields
from openledger.application.dto.results import MutationResult
from openledger.domain.errors import LedgerError
from openledger.infrastructure.ledger import LedgerService
from openledger.presentation.commands import CommandBridge

pytestmark = pytest.mark.ui


def uid() -> str:
    return str(uuid4())


class ObservedLedger:
    """Control worker boundaries while preserving real committed ledger behavior."""

    def __init__(
        self,
        service: LedgerService,
        *,
        block: bool = False,
        failures_before: int = 0,
        fail_after_commit: bool = False,
    ) -> None:
        self.service = service
        self.block = block
        self.failures_before = failures_before
        self.fail_after_commit = fail_after_commit
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.worker_threads: list[int] = []
        self.started = Event()
        self.release = Event()
        self.finished = Event()

    def execute(
        self, request_id: str, command_type: str, payload: Mapping[str, object]
    ) -> MutationResult:
        self.calls.append((request_id, command_type, dict(payload)))
        self.worker_threads.append(get_ident())
        self.started.set()
        try:
            if self.block and not self.release.wait(5):
                raise RuntimeError("Test did not release the controlled writer")
            if self.failures_before:
                self.failures_before -= 1
                raise LedgerError("DATABASE_BUSY")
            result = self.service.execute(request_id, command_type, payload)
            if self.fail_after_commit:
                self.fail_after_commit = False
                raise LedgerError("STORAGE_IO_ERROR")
            return result
        finally:
            self.finished.set()

    def balances(self) -> dict[str, int]:
        return self.service.balances()

    def transaction(self, transaction_id: str) -> dict[str, object]:
        return self.service.transaction(transaction_id)


def test_duplicate_blocked_until_result_is_delivered_on_gui_thread(
    qtbot: QtBot, ledger: LedgerService
) -> None:
    observed = ObservedLedger(ledger, block=True)
    bridge = CommandBridge(observed)
    completed: list[MutationResult] = []
    delivery_threads: list[int] = []
    busy_states: list[bool] = []
    main_thread = get_ident()

    def committed(value: object) -> None:
        completed.append(cast(MutationResult, value))
        delivery_threads.append(get_ident())

    bridge.completed.connect(committed)
    bridge.busyChanged.connect(busy_states.append)
    identifier = uid()
    payload: dict[str, object] = {"id": identifier, "name": "排队时的名称"}
    try:
        assert bridge.submit("book.create.v1", payload)
        assert observed.started.wait(3)
        payload["name"] = "入队之后修改的名称"
        assert not bridge.submit("book.create.v1", {"id": uid(), "name": "重复按键"})
        assert len(observed.calls) == 1
        observed.release.set()
        assert observed.finished.wait(3)
        assert bridge.busy and completed == []
        qtbot.waitUntil(lambda: len(completed) == 1)
        assert not bridge.busy
        assert delivery_threads == [main_thread]
        assert observed.worker_threads[0] != main_thread
        assert busy_states == [True, False]
        assert next(row["name"] for row in ledger.entities("book") if row["id"] == identifier) == (
            "排队时的名称"
        )
        assert len(observed.calls) == 1
    finally:
        observed.release.set()
        bridge.close()


def test_failed_retry_reuses_request_and_corrected_intent_gets_new_request(
    qtbot: QtBot, ledger: LedgerService
) -> None:
    observed = ObservedLedger(ledger, failures_before=2)
    bridge = CommandBridge(observed)
    failures: list[str] = []
    completed: list[object] = []
    bridge.failed.connect(failures.append)
    bridge.completed.connect(completed.append)
    identifier = uid()
    payload: dict[str, object] = {"id": identifier, "name": "尚未保存"}
    try:
        assert bridge.submit("book.create.v1", payload)
        qtbot.waitUntil(lambda: len(failures) == 1)
        assert failures == ["DATABASE_BUSY"] and not bridge.busy
        assert bridge.submit("book.create.v1", payload)
        qtbot.waitUntil(lambda: len(failures) == 2)
        assert observed.calls[0][0] == observed.calls[1][0]
        corrected = {**payload, "name": "修改后明确确认"}
        assert bridge.submit("book.create.v1", corrected)
        qtbot.waitUntil(lambda: len(completed) == 1)
        assert observed.calls[2][0] != observed.calls[1][0]
        assert next(row["name"] for row in ledger.entities("book") if row["id"] == identifier) == (
            "修改后明确确认"
        )
    finally:
        bridge.close()


def test_lost_result_retries_committed_receipt_without_duplicate_expense(
    qtbot: QtBot, ledger: LedgerService
) -> None:
    observed = ObservedLedger(ledger, fail_after_commit=True)
    bridge = CommandBridge(observed)
    failures: list[str] = []
    completed: list[object] = []
    bridge.failed.connect(failures.append)
    bridge.completed.connect(completed.append)
    account = str(
        next(row["id"] for row in ledger.entities("account") if row["account_type"] == "cash")
    )
    category = str(
        next(
            row["id"] for row in ledger.entities("category") if row["transaction_kind"] == "expense"
        )
    )
    book = str(ledger.entities("book")[0]["id"])
    identifier = uid()
    item = TransactionFields("expense", 2500, account, book, category, date(2026, 10, 2))
    payload: dict[str, object] = {"id": identifier, "fields": asdict(item)}
    try:
        assert bridge.submit("transaction.record.v1", payload)
        qtbot.waitUntil(lambda: len(failures) == 1)
        assert failures == ["STORAGE_IO_ERROR"]
        assert ledger.balances()[account] == 97_500
        assert bridge.submit("transaction.record.v1", payload)
        qtbot.waitUntil(lambda: len(completed) == 1)
        receipt = cast(MutationResult, completed[0])
        assert receipt.replayed and receipt.request_id == observed.calls[0][0]
        assert observed.calls[0][0] == observed.calls[1][0]
        assert ledger.balances()[account] == 97_500
        with ledger.database.read() as connection:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM transactions WHERE id=?", (identifier,)
                ).fetchone()[0]
                == 1
            )
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM audit_events WHERE entity_id=?", (identifier,)
                ).fetchone()[0]
                == 1
            )
    finally:
        bridge.close()


def test_close_waits_for_running_commit_and_rejects_further_submissions(
    qtbot: QtBot, ledger: LedgerService
) -> None:
    observed = ObservedLedger(ledger, block=True)
    bridge = CommandBridge(observed)
    closed = Event()
    identifier = uid()
    assert bridge.submit("book.create.v1", {"id": identifier, "name": "退出时仍完成保存"})
    assert observed.started.wait(3)

    def close_writer() -> None:
        bridge.close()
        closed.set()

    closer = Thread(target=close_writer, name="test-bridge-close")
    closer.start()
    try:
        assert not closed.wait(0.05)
        assert not observed.finished.is_set()
        observed.release.set()
        assert closed.wait(5)
        assert observed.finished.is_set()
        assert any(row["id"] == identifier for row in ledger.entities("book"))
        assert not bridge.submit("book.create.v1", {"id": uid(), "name": "退出之后"})
        qtbot.waitUntil(lambda: not bridge.busy)
        assert len(observed.calls) == 1
    finally:
        observed.release.set()
        closer.join(timeout=5)
        bridge.close()
