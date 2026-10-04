"""Quick entry must preserve drafts, respect IME, and share the sole command writer."""

from collections.abc import Mapping
from uuid import uuid4

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from openledger.infrastructure.ledger import LedgerService
from openledger.presentation.commands import CommandBridge
from openledger.presentation.views.quick_entry import QuickEntryWindow

pytestmark = pytest.mark.ui


@pytest.fixture
def quick(qtbot: QtBot, ledger: LedgerService) -> QuickEntryWindow:
    window = QuickEntryWindow(ledger)
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    qtbot.addWidget(window)
    window.open()
    return window


def test_enter_is_pure_and_ctrl_enter_emits_exactly_one_intent(
    qtbot: QtBot, quick: QuickEntryWindow, ledger: LedgerService
) -> None:
    before = ledger.total_assets()
    payloads: list[object] = []
    quick.commandRequested.connect(lambda command, payload: payloads.append(payload))
    quick.input.setPlainText("咖啡25元")
    QTest.keyClick(quick.input, Qt.Key.Key_Return)
    assert quick.form.amount.text() == "25.00"
    assert ledger.total_assets() == before
    QTest.keyClick(quick.input, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(quick.input, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert len(payloads) == 1
    assert ledger.total_assets() == before
    quick.command_finished(False)
    assert quick.input.toPlainText() == "咖啡25元"
    quick.save_draft()
    assert payloads[0] == payloads[1]


def test_shared_bridge_saves_once_then_starts_a_fresh_draft(
    qtbot: QtBot, quick: QuickEntryWindow, ledger: LedgerService
) -> None:
    bridge = CommandBridge(ledger)
    before = ledger.total_assets()

    def submit(command: str, payload: object) -> None:
        assert isinstance(payload, Mapping)
        assert bridge.submit(command, payload)

    quick.commandRequested.connect(submit)
    bridge.completed.connect(lambda result: quick.command_finished(True))
    bridge.failed.connect(lambda code: quick.command_finished(False))
    try:
        quick.input.setPlainText("咖啡25元")
        quick.parse_input()
        previous = quick._transaction_id
        quick.save_draft()
        quick.save_draft()
        qtbot.waitUntil(lambda: not bridge.busy, timeout=5000)
        assert ledger.total_assets() == before - 2500
        assert quick.input.toPlainText() == ""
        assert quick._transaction_id != previous
        assert not quick.save.isEnabled()
    finally:
        bridge.close()


def test_ime_composition_prevents_parse_and_save(quick: QuickEntryWindow) -> None:
    calls: list[object] = []
    quick.commandRequested.connect(lambda command, payload: calls.append(payload))
    quick.input.setPlainText("咖啡25元")
    QApplication.sendEvent(quick.input, QInputMethodEvent("ka", []))
    QTest.keyClick(quick.input, Qt.Key.Key_Return)
    quick.save_draft()
    assert quick._result is None
    assert calls == []
    QApplication.sendEvent(quick.input, QInputMethodEvent("", []))
    quick.parse_input()
    assert quick._candidate is not None


def test_editing_raw_input_invalidates_old_parse(quick: QuickEntryWindow) -> None:
    calls: list[object] = []
    quick.commandRequested.connect(lambda command, payload: calls.append(payload))
    quick.input.setPlainText("咖啡25元")
    quick.parse_input()
    quick.input.setPlainText("咖啡30元")
    quick.save_draft()
    assert calls == []
    assert not quick.save.isEnabled()


def test_enter_in_form_does_not_implicitly_click_save(quick: QuickEntryWindow) -> None:
    calls: list[object] = []
    quick.commandRequested.connect(lambda command, payload: calls.append(payload))
    quick.input.setPlainText("咖啡25元")
    quick.parse_input()
    quick.form.note.setFocus()
    QTest.keyClick(quick.form.note, Qt.Key.Key_Return)
    assert calls == []


@pytest.mark.parametrize("text", ["咖啡25元，午饭35元", "下个月咖啡25元", "咖啡25或30元"])
def test_multiple_or_ambiguous_input_is_not_automatically_saved(
    quick: QuickEntryWindow, text: str
) -> None:
    calls: list[object] = []
    quick.commandRequested.connect(lambda command, payload: calls.append(payload))
    quick.input.setPlainText(text)
    quick.parse_input()
    quick.save_draft()
    assert calls == []


def test_close_and_escape_reopen_the_same_unsaved_draft(quick: QuickEntryWindow) -> None:
    quick.input.setPlainText("咖啡25元")
    quick.parse_input()
    previous = quick._transaction_id
    quick.form.note.setText("待确认")
    quick.close()
    assert not quick.isVisible()
    quick.open()
    assert quick.input.toPlainText() == "咖啡25元"
    assert quick.form.note.text() == "待确认"
    assert quick._transaction_id == previous
    QTest.keyClick(quick, Qt.Key.Key_Escape)
    assert not quick.isVisible()


def test_timezone_change_keeps_input_but_requires_new_parse(quick: QuickEntryWindow) -> None:
    quick.input.setPlainText("昨天咖啡25元")
    quick.parse_input()
    quick.set_time_zone("America/New_York")
    assert quick.input.toPlainText() == "昨天咖啡25元"
    assert quick._candidate is None
    assert not quick.save.isEnabled()


def test_failure_preserves_edited_fields_and_transaction_id(quick: QuickEntryWindow) -> None:
    calls: list[object] = []
    quick.commandRequested.connect(lambda command, payload: calls.append(payload))
    quick.input.setPlainText("咖啡25元")
    quick.parse_input()
    quick.form.note.setText("人工核对")
    quick.save_draft()
    identifier = quick._transaction_id
    quick.command_finished(False, "DATABASE_BUSY")
    assert "DATABASE_BUSY" in quick.status.text()
    assert quick.form.note.text() == "人工核对"
    assert quick._transaction_id == identifier
    assert quick.save.isEnabled()
    quick.save_draft()
    assert calls[0] == calls[1]


def test_manual_entry_is_explicit(quick: QuickEntryWindow, ledger: LedgerService) -> None:
    calls: list[object] = []
    quick.commandRequested.connect(lambda command, payload: calls.append(payload))
    quick._manual_draft()
    quick.form.amount.setText("10")
    food = next(row for row in ledger.entities("category") if row["name"] == "餐饮")
    quick.form.select(quick.form.category, food["id"])
    quick.save_draft()
    assert len(calls) == 1
    assert isinstance(calls[0], dict)
    assert calls[0]["fields"]["source"] == "manual"
    assert calls[0]["fields"]["source_text"] is None


def test_refresh_keeps_selected_metadata_and_long_notes(
    quick: QuickEntryWindow, ledger: LedgerService
) -> None:
    quick.input.setPlainText("咖啡25元")
    quick.parse_input()
    selected = quick.form.account.currentData()
    quick.form.note.setText("核对备注" * 60)
    ledger.execute(str(uuid4()), "tag.create.v1", {"id": str(uuid4()), "name": "新标签"})
    quick.refresh()
    assert quick.form.account.currentData() == selected
    assert quick.form.note.text() == "核对备注" * 60
