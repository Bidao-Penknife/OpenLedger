"""Observable threading, ordering and cancellation contracts for background file work."""

from collections.abc import Callable
from threading import Event, get_ident
from time import monotonic

import pytest
from pytestqt.qtbot import QtBot

from openledger.domain.errors import LedgerError
from openledger.presentation.tasks import TaskBridge

pytestmark = pytest.mark.ui


def test_delivery_is_queued_to_gui_and_busy_false_follows_completion(qtbot: QtBot) -> None:
    bridge = TaskBridge()
    entered, release, finished = Event(), Event(), Event()
    worker_threads: list[int] = []
    events: list[tuple[str, object, int]] = []
    gui_thread = get_ident()

    def work(cancel: Callable[[], bool]) -> object:
        worker_threads.append(get_ident())
        entered.set()
        assert release.wait(3)
        finished.set()
        return ("file snapshot", 123)

    def completed(value: object) -> None:
        assert bridge.busy
        events.append(("completed", value, get_ident()))

    bridge.completed.connect(completed)
    bridge.busyChanged.connect(lambda busy: events.append(("busy", busy, get_ident())))
    try:
        assert bridge.start(work) and entered.wait(3)
        assert not bridge.start(lambda cancel: "second")
        release.set()
        assert finished.wait(3)
        assert bridge.busy and events == [("busy", True, gui_thread)]
        qtbot.waitUntil(lambda: not bridge.busy)
        assert events == [
            ("busy", True, gui_thread),
            ("completed", ("file snapshot", 123), gui_thread),
            ("busy", False, gui_thread),
        ]
        assert worker_threads == [worker_threads[0]] and worker_threads[0] != gui_thread
    finally:
        release.set()
        bridge.close()


def test_ledger_failure_emits_only_code_on_gui_before_busy_false(qtbot: QtBot) -> None:
    bridge = TaskBridge()
    errors: list[tuple[str, bool, int]] = []
    states: list[bool] = []
    bridge.failed.connect(lambda code: errors.append((code, bridge.busy, get_ident())))
    bridge.busyChanged.connect(states.append)

    def fail(cancel: Callable[[], bool]) -> object:
        raise LedgerError("EXPORT_IO_ERROR", "private source file and credentials")

    try:
        assert bridge.start(fail)
        qtbot.waitUntil(lambda: not bridge.busy)
        assert errors == [("EXPORT_IO_ERROR", True, get_ident())]
        assert states == [True, False]
    finally:
        bridge.close()


def test_unexpected_exception_is_sanitized_without_raw_contents(qtbot: QtBot) -> None:
    bridge = TaskBridge()
    errors: list[str] = []
    bridge.failed.connect(errors.append)

    def fail(cancel: Callable[[], bool]) -> object:
        raise RuntimeError("secret-account.csv contains token=private")

    try:
        assert bridge.start(fail)
        qtbot.waitUntil(lambda: not bridge.busy)
        assert errors == ["BACKGROUND_TASK_FAILED"]
    finally:
        bridge.close()


def test_cancel_is_cooperative_and_next_task_gets_fresh_token(qtbot: QtBot) -> None:
    bridge = TaskBridge()
    entered = Event()
    errors: list[str] = []
    completed: list[object] = []
    bridge.failed.connect(errors.append)
    bridge.completed.connect(completed.append)

    def cancellable(cancel: Callable[[], bool]) -> object:
        entered.set()
        deadline = monotonic() + 3
        while monotonic() < deadline:
            if cancel():
                raise LedgerError("EXCHANGE_CANCELLED")
            Event().wait(0.01)
        pytest.fail("task did not observe cancellation")

    try:
        assert bridge.start(cancellable)
        qtbot.waitUntil(entered.is_set)
        bridge.cancel()
        qtbot.waitUntil(lambda: not bridge.busy)
        assert errors == ["EXCHANGE_CANCELLED"]
        assert bridge.start(lambda cancel: cancel())
        qtbot.waitUntil(lambda: not bridge.busy)
        assert completed == [False]
    finally:
        bridge.close()


def test_busy_false_callback_can_start_next_task_after_completion(qtbot: QtBot) -> None:
    bridge = TaskBridge()
    completed: list[object] = []
    during_completion: list[bool] = []
    started_again: list[bool] = []

    def on_completed(value: object) -> None:
        completed.append(value)
        during_completion.append(bridge.start(lambda cancel: "too early"))

    def on_busy(busy: bool) -> None:
        if not busy and len(completed) == 1:
            started_again.append(bridge.start(lambda cancel: "second"))

    bridge.completed.connect(on_completed)
    bridge.busyChanged.connect(on_busy)
    try:
        assert bridge.start(lambda cancel: "first")
        qtbot.waitUntil(lambda: len(completed) == 2 and not bridge.busy)
        assert completed == ["first", "second"]
        assert during_completion == [False, False] and started_again == [True]
    finally:
        bridge.close()


def test_close_cancels_waits_and_suppresses_late_delivery(qtbot: QtBot) -> None:
    bridge = TaskBridge()
    entered, stopped = Event(), Event()
    completed: list[object] = []
    errors: list[str] = []
    bridge.completed.connect(completed.append)
    bridge.failed.connect(errors.append)

    def work(cancel: Callable[[], bool]) -> object:
        entered.set()
        deadline = monotonic() + 3
        while monotonic() < deadline:
            if cancel():
                stopped.set()
                return "closed result"
            Event().wait(0.01)
        pytest.fail("close did not cancel running work")

    assert bridge.start(work) and entered.wait(3)
    bridge.close()
    assert stopped.is_set() and not bridge.busy
    assert not bridge.start(lambda cancel: "after close")
    qtbot.wait(30)
    assert completed == [] and errors == []
    bridge.close()
