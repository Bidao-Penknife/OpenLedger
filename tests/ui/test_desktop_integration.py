"""Verify shared financial writes, explicit AI drafts and native lifecycle composition."""

import json
from collections.abc import Iterator, Mapping
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import cast

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from openledger.application.dto.ai import AIConfig
from openledger.application.dto.queries import TransactionFilter
from openledger.application.dto.runtime import RuntimeInfo
from openledger.application.ports.ai import CancelCheck
from openledger.domain.errors import LedgerError
from openledger.infrastructure.ai import AIParser, AISettingsStore
from openledger.infrastructure.credentials import MemoryCredentialStore, credential_target
from openledger.infrastructure.ledger import LedgerService
from openledger.infrastructure.settings import SettingsStore
from openledger.presentation.desktop import DesktopController
from openledger.presentation.views.main_window import MainWindow

pytestmark = pytest.mark.ui


class Replies:
    """Return a synthetic response through the real provider validation boundary."""

    def __init__(self) -> None:
        self.calls: list[bytes] = []
        self.entered = Event()
        self.release = Event()
        self.release.set()
        self.error: str | None = None

    def complete(self, endpoint: str, key: str, body: bytes, cancel: CancelCheck) -> bytes:
        assert endpoint == "https://example.invalid/v1/chat/completions"
        assert key == "synthetic-key"
        self.calls.append(body)
        self.entered.set()
        assert self.release.wait(3)
        if self.error:
            raise LedgerError(self.error)
        context = json.loads(json.loads(body)["messages"][1]["content"])
        row = {
            "span": [0, len(context["text"])],
            "kind": "expense",
            "amount_minor": "2500",
            "occurred_on": context["reference_date"],
            "account_id": context["accounts"][0]["id"],
            "category_id": next(
                item["id"] for item in context["categories"] if item["name"] == "餐饮"
            ),
        }
        return json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"transactions": [row]})},
                    }
                ]
            }
        ).encode()


@pytest.fixture
def desktop_window(
    qtbot: QtBot, ledger: LedgerService, tmp_path: Path
) -> Iterator[tuple[MainWindow, Replies]]:
    config = AIConfig(True, "https://example.invalid/v1", "synthetic-model")
    AISettingsStore(tmp_path).save(config)
    credentials = MemoryCredentialStore()
    credentials.set(credential_target(tmp_path, config), "synthetic-key")
    replies = Replies()
    runtime = RuntimeInfo(
        "0.3.0.dev0", "3.12.5", "6.11.2", "6.11.2", "3.45.3", str(tmp_path), False
    )
    widget = MainWindow(
        runtime,
        ledger,
        SettingsStore(tmp_path / "settings.json"),
        credentials=credentials,
        ai_parser=AIParser(replies, clock=ledger.clock),
    )
    qtbot.addWidget(widget)
    widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    widget.quick.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    widget.show()
    yield widget, replies
    replies.release.set()
    widget.request_quit()


def test_quick_save_uses_shared_writer_and_preserves_main_draft(
    qtbot: QtBot, desktop_window: tuple[MainWindow, Replies]
) -> None:
    window, _ = desktop_window
    before = window.ledger.total_assets()
    window.input.setPlainText("主窗口咖啡30元")
    window.parse_input()
    window.quick.open()
    window.quick.input.setPlainText("咖啡25元")
    window.quick.parse_input()
    assert window.ledger.total_assets() == before
    QTest.keyClick(window.quick.input, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(lambda: not window.bridge.busy)
    assert window.ledger.total_assets() == before - 2500
    assert window.input.toPlainText() == "主窗口咖啡30元"
    assert window.form.amount.text() == "30.00"
    assert window.quick.input.toPlainText() == ""
    assert (
        window.queries.transactions(TransactionFilter(kind="expense")).rows[0]["source"]
        == "local_rule"
    )


def test_ai_requires_confirmation_and_saved_provenance_is_ai_assisted(
    qtbot: QtBot, desktop_window: tuple[MainWindow, Replies]
) -> None:
    window, replies = desktop_window
    before = window.ledger.total_assets()
    assert replies.calls == []
    window.input.setPlainText("咖啡25元")
    window.parse_with_ai()
    qtbot.waitUntil(lambda: not window.ai_tasks.busy)
    assert window.form.amount.text() == "25.00"
    assert "AI 建议" in window.evidence.text()
    assert window.ledger.total_assets() == before
    assert window.queries.transactions(TransactionFilter(kind="expense")).rows == ()
    window.save_draft()
    qtbot.waitUntil(lambda: not window.bridge.busy)
    row = window.queries.transactions(TransactionFilter(kind="expense")).rows[0]
    assert row["source"] == "ai_assisted" and row["source_text"] == "咖啡25元"
    assert window.ledger.total_assets() == before - 2500
    assert "opening_balance" not in replies.calls[0].decode()


@pytest.mark.parametrize("edit", ["text", "form", "cancel", "local", "manual", "config"])
def test_pending_ai_cannot_replace_an_updated_or_cancelled_draft(
    qtbot: QtBot, desktop_window: tuple[MainWindow, Replies], edit: str
) -> None:
    window, replies = desktop_window
    window.input.setPlainText("咖啡25元")
    window.parse_input()
    replies.release.clear()
    window.parse_with_ai()
    qtbot.waitUntil(replies.entered.is_set)
    if edit == "text":
        window.input.setPlainText("奶茶30元")
    elif edit == "form":
        window.form.amount.selectAll()
        QTest.keyClicks(window.form.amount, "30")
    elif edit == "cancel":
        window.ai_cancel.click()
    elif edit == "local":
        window.parse_input()
    elif edit == "manual":
        window._manual_draft()
    else:
        window._ai_configuration_changed()
    replies.release.set()
    qtbot.waitUntil(lambda: not window.ai_tasks.busy)
    assert "AI 建议" not in window.evidence.text()
    assert window.queries.transactions(TransactionFilter(kind="expense")).rows == ()
    if edit == "form":
        assert window.form.amount.text() == "30"


def test_ai_failure_preserves_local_draft_and_never_discloses_key(
    qtbot: QtBot, desktop_window: tuple[MainWindow, Replies]
) -> None:
    window, replies = desktop_window
    window.input.setPlainText("咖啡25元")
    window.parse_input()
    candidate = window._candidate
    replies.error = "AI_TIMEOUT"
    window.parse_with_ai()
    qtbot.waitUntil(lambda: not window.ai_tasks.busy)
    assert window._candidate is candidate
    assert window.form.amount.text() == "25.00" and window.save.isEnabled()
    assert "AI_TIMEOUT" in window.parse_status.text()
    assert "synthetic-key" not in window.parse_status.text()


def test_user_selecting_another_local_candidate_discards_pending_ai(
    qtbot: QtBot, desktop_window: tuple[MainWindow, Replies]
) -> None:
    window, replies = desktop_window
    window.input.setPlainText("咖啡25元，朋友转我50元")
    window.parse_input()
    assert window._parsed is not None and window._parsed.status == "multiple_events"
    replies.release.clear()
    window.parse_with_ai()
    qtbot.waitUntil(replies.entered.is_set)
    window.candidates.setCurrentIndex(2)
    window.candidates.activated.emit(2)
    assert window.form.kind.currentData() == "income"
    replies.release.set()
    qtbot.waitUntil(lambda: not window.ai_tasks.busy)
    assert window.form.kind.currentData() == "income" and window.form.amount.text() == "50.00"
    assert "AI 建议" not in window.evidence.text()


def test_disabled_ai_never_calls_transport(desktop_window: tuple[MainWindow, Replies]) -> None:
    window, replies = desktop_window
    window.plugins.select("ai", None)
    window.input.setPlainText("咖啡25元")
    window.parse_with_ai()
    assert replies.calls == [] and not window.ai_tasks.busy


def test_quick_synchronous_rejection_preserves_specific_error_code(
    desktop_window: tuple[MainWindow, Replies], monkeypatch: pytest.MonkeyPatch
) -> None:
    window, _ = desktop_window

    def rejected(command_type: str, payload: Mapping[str, object]) -> bool:
        window.bridge.failed.emit("DATABASE_BUSY")
        return False

    monkeypatch.setattr(window.bridge, "submit", rejected)
    window.quick.input.setPlainText("咖啡25元")
    window.quick.parse_input()
    window.quick.save_draft()
    assert "DATABASE_BUSY" in window.quick.status.text()
    assert "COMMAND_NOT_ACCEPTED" not in window.quick.status.text()
    assert window.quick.save.isEnabled()


def test_closed_writer_does_not_leave_quick_waiting_forever(
    desktop_window: tuple[MainWindow, Replies],
) -> None:
    window, _ = desktop_window
    window.bridge.close()
    window.quick.input.setPlainText("咖啡25元")
    window.quick.parse_input()
    window.quick.save_draft()
    assert "COMMAND_NOT_ACCEPTED" in window.quick.status.text()
    assert window.quick.save.isEnabled()


def test_language_choice_preserves_unsaved_input_and_waits_for_restart(
    desktop_window: tuple[MainWindow, Replies],
) -> None:
    window, _ = desktop_window
    window.input.setPlainText("咖啡25元")
    window.parse_input()
    window.language.setCurrentIndex(window.language.findData("en_US"))
    window._apply_settings()
    assert window.settings.language == "en_US"
    assert window.input.toPlainText() == "咖啡25元" and window.form.amount.text() == "25.00"
    assert window.ai_button.text() == "AI 解析（可选）"


class FakeDesktop:
    """Exercise close policy without taking over the user's real tray or shortcuts."""

    tray_active = True

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_close_hides_to_available_tray_and_explicit_exit_releases_controller(
    desktop_window: tuple[MainWindow, Replies],
) -> None:
    window, _ = desktop_window
    native = FakeDesktop()
    window.desktop = cast(DesktopController, native)
    assert not window.close()
    assert not window.isVisible() and not native.closed
    window.show()
    window.request_quit()
    assert not window.isVisible() and native.closed


def test_unavailable_tray_closes_normally(desktop_window: tuple[MainWindow, Replies]) -> None:
    window, _ = desktop_window
    native = FakeDesktop()
    native.tray_active = False
    window.desktop = cast(DesktopController, native)
    assert window.close() and native.closed


def test_exit_during_commit_waits_for_receipt_before_cleanup(
    qtbot: QtBot, desktop_window: tuple[MainWindow, Replies]
) -> None:
    window, _ = desktop_window
    entered, release = Event(), Event()
    before = window.ledger.total_assets()

    def pending(stage: str) -> None:
        if stage == "before_commit":
            entered.set()
            assert release.wait(3)

    window.ledger._fault = pending
    window.settings = replace(window.settings, close_to_tray=False)
    window.input.setPlainText("咖啡25元")
    window.parse_input()
    window.save_draft()
    qtbot.waitUntil(entered.is_set)
    window.request_quit()
    assert window.isVisible() and window.bridge.busy
    release.set()
    qtbot.waitUntil(lambda: not window.isVisible())
    assert window.ledger.total_assets() == before - 2500
    assert len(window.queries.transactions(TransactionFilter(kind="expense")).rows) == 1
