"""Exercise the actual desktop draft-to-receipt flow against disposable SQLite."""

from collections.abc import Iterator, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from pytestqt.qtbot import QtBot

from openledger.application.dto.queries import TransactionFilter
from openledger.application.dto.results import MutationResult
from openledger.application.dto.runtime import RuntimeInfo
from openledger.domain.errors import LedgerError
from openledger.infrastructure.ledger import LedgerService
from openledger.infrastructure.settings import DesktopSettings, SettingsStore
from openledger.presentation.views.main_window import MainWindow
from openledger.presentation.views.management import EntityDialog
from openledger.presentation.views.transaction_edit import EditTransactionDialog

pytestmark = pytest.mark.ui


@pytest.fixture
def window(qtbot: QtBot, ledger: LedgerService, tmp_path: Path) -> Iterator[MainWindow]:
    runtime = RuntimeInfo(
        "0.1.0.dev0", "3.12.5", "6.11.2", "6.11.2", "3.45.3", str(tmp_path), False
    )
    widget = MainWindow(runtime, ledger, SettingsStore(tmp_path / "settings.json"))
    qtbot.addWidget(widget)
    widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    widget.show()
    yield widget
    widget.close()


def records(window: MainWindow) -> tuple[dict[str, object], ...]:
    return window.queries.transactions(TransactionFilter(kind="expense")).rows


def parse(window: MainWindow, text: str) -> None:
    window.input.setPlainText(text)
    window.input.setFocus()
    QTest.keyClick(window.input, Qt.Key.Key_Return)


def finish(qtbot: QtBot, window: MainWindow) -> None:
    qtbot.waitUntil(lambda: not window.bridge.busy, timeout=5000)
    assert "已保存" in window.notice.text(), window.notice.text()


def test_enter_parses_without_writing_and_ctrl_enter_commits_once(
    qtbot: QtBot, window: MainWindow, ledger: LedgerService
) -> None:
    before = ledger.total_assets()
    parse(window, "昨天晚上和朋友吃火锅花了128元，微信支付")
    assert window.form.amount.text() == "128.00"
    assert window.form.day.date().toPython() == date(2026, 10, 1)
    assert window.form.period.currentData() == "evening"
    assert window.form.category.currentText() == "餐饮"
    assert window.form.payment.currentText() == "微信"
    assert window.form.counterparty.text() == "朋友"
    assert window.form.location.text() == ""
    assert records(window) == ()
    assert ledger.total_assets() == before
    QTest.keyClick(window.input, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(window.input, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    finish(qtbot, window)
    saved = records(window)
    assert len(saved) == 1
    assert saved[0]["amount_minor"] == 12800
    assert saved[0]["source"] == "local_rule"
    assert saved[0]["source_text"] == "昨天晚上和朋友吃火锅花了128元，微信支付"
    assert ledger.total_assets() == before - 12800
    assert window.input.toPlainText() == ""
    assert window.form.note.text() == ""


def test_chinese_ime_preedit_does_not_parse_or_save(window: MainWindow) -> None:
    window.input.setPlainText("咖啡25元")
    QApplication.sendEvent(window.input, QInputMethodEvent("ka", []))
    assert window.input.composing
    QTest.keyClick(window.input, Qt.Key.Key_Return)
    window.save_draft()
    assert window._parsed is None
    assert records(window) == ()
    QApplication.sendEvent(window.input, QInputMethodEvent("", []))
    assert not window.input.composing


def test_changed_input_invalidates_old_parse(window: MainWindow) -> None:
    parse(window, "咖啡25元")
    assert window.save.isEnabled()
    window.input.setPlainText("咖啡30元")
    window.save_draft()
    assert records(window) == ()
    assert "DRAFT_NOT_CONFIRMED" in window.notice.text()
    assert not window.save.isEnabled()


def test_multiple_events_require_selection_and_saved_candidate_stays_consumed(
    qtbot: QtBot, window: MainWindow
) -> None:
    parse(window, "昨天吃饭128元，朋友转我50元")
    assert window._parsed is not None and window._parsed.status == "multiple_events"
    assert window.candidates.count() == 3
    window.save_draft()
    assert records(window) == ()
    window.candidates.setCurrentIndex(1)
    window._select_candidate(1)
    window.save_draft()
    finish(qtbot, window)
    assert len(records(window)) == 1
    assert window.candidates.count() == 2
    window.parse_input()
    assert window.candidates.count() == 2
    assert "吃饭128" not in window.candidates.itemText(1)
    window.save_draft()
    assert len(records(window)) == 1


def test_ambiguous_total_requires_explicit_manual_resolution(window: MainWindow) -> None:
    parse(window, "25元还是30元")
    assert not window.save.isEnabled()
    window.save_draft()
    assert records(window) == ()
    window._manual_draft()
    assert window.save.isEnabled()


def test_user_kind_amount_and_tags_survive_reparse(
    window: MainWindow, ledger: LedgerService
) -> None:
    tag_id = str(uuid4())
    ledger.execute(str(uuid4()), "tag.create.v1", {"id": tag_id, "name": "测试标签"})
    window.form.refresh()
    parse(window, "咖啡25元")
    window.form.kind.setCurrentIndex(1)
    window.form.amount.selectAll()
    QTest.keyClicks(window.form.amount, "30")
    window.form.tags.item(0).setCheckState(Qt.CheckState.Checked)
    window.parse_input()
    assert window.form.kind.currentData() == "income"
    assert window.form.amount.text() == "30"
    assert window.form.tag_ids() == (tag_id,)


def test_explicit_channel_mapping_overrides_default_but_preserves_user_selection(
    window: MainWindow, ledger: LedgerService
) -> None:
    default = ledger.preferences()["default_account_id"]
    bank = next(row for row in ledger.entities("account") if row["id"] != default)
    payment = next(row for row in ledger.entities("payment_method") if row["code"] == "wechat")
    ledger.execute(
        str(uuid4()),
        "payment_method.update.v1",
        {
            "id": payment["id"],
            "expected_version": payment["version"],
            "name": payment["name"],
            "default_account_id": bank["id"],
        },
    )
    assert "account_id" not in window.form.user_fields
    parse(window, "咖啡25元微信支付")
    assert window.form.account.currentData() == bank["id"]
    assert "默认值" in window.evidence.text()
    window.form.account.setCurrentIndex(window.form.account.findData(default))
    window.parse_input()
    assert window.form.account.currentData() == default


def test_management_confirmation_commits_via_root_bridge(qtbot: QtBot, window: MainWindow) -> None:
    def confirm() -> None:
        dialog = window.management._pending_dialog
        assert isinstance(dialog, EntityDialog)
        dialog.name.setText("界面创建账户")
        dialog.opening.setText("12.34")
        dialog.accept()

    QTimer.singleShot(0, confirm)
    before = window.ledger.total_assets()
    window.management._create()
    finish(qtbot, window)
    accounts = window.ledger.entities("account")
    created = next(row for row in accounts if row["name"] == "界面创建账户")
    assert window.ledger.balances()[str(created["id"])] == 1234
    assert window.ledger.total_assets() == before + 1234
    assert window.management._pending_dialog is None


def test_exact_time_is_visible_and_editing_date_removes_exact_precision(window: MainWindow) -> None:
    parse(window, "昨天15:30咖啡25元")
    assert window.form._exact is not None
    assert "2026-10-01 15:30:00" in window.form.exact_label.text()
    assert "Asia/Shanghai" in window.form.exact_label.text()
    window.form.day.setDate(QDate(2026, 10, 2))
    assert window.form._exact is None
    assert window.form.fields("Asia/Shanghai")["occurrence_precision"] == "date"


def test_new_draft_clears_optional_fields_and_tags(
    window: MainWindow, ledger: LedgerService
) -> None:
    ledger.execute(str(uuid4()), "tag.create.v1", {"id": str(uuid4()), "name": "标签"})
    window.form.refresh()
    parse(window, "昨天15:30咖啡25元")
    window.form.tags.item(0).setCheckState(Qt.CheckState.Checked)
    window.form.merchant.setText("测试商户")
    window._reset_draft()
    assert window.form.tag_ids() == ()
    assert window.form._exact is None
    assert window.form.merchant.text() == ""
    assert window.form.note.text() == ""


def test_missing_account_does_not_invent_an_account(window: MainWindow) -> None:
    parse(window, "咖啡25元")
    window.form.account.setCurrentIndex(0)
    window.save_draft()
    assert "MISSING_REQUIRED_FIELD" in window.notice.text()
    assert len(window.ledger.entities("account")) == 2
    assert records(window) == ()


def test_failed_commit_keeps_input_and_changed_amount_can_retry(
    qtbot: QtBot, window: MainWindow, ledger: LedgerService, monkeypatch: pytest.MonkeyPatch
) -> None:
    parse(window, "咖啡25元")
    original = ledger.execute

    def fail(request_id: str, command_type: str, payload: Mapping[str, object]) -> MutationResult:
        raise LedgerError("DATABASE_BUSY")

    monkeypatch.setattr(ledger, "execute", fail)
    window.save_draft()
    qtbot.waitUntil(lambda: not window.bridge.busy)
    assert window.input.toPlainText() == "咖啡25元"
    assert window.form.amount.text() == "25.00"
    assert "DATABASE_BUSY" in window.notice.text()
    monkeypatch.setattr(ledger, "execute", original)
    window.form.amount.selectAll()
    QTest.keyClicks(window.form.amount, "26")
    window.save_draft()
    finish(qtbot, window)
    assert records(window)[0]["amount_minor"] == 2600


def test_edit_delete_restore_updates_committed_assets(
    qtbot: QtBot, window: MainWindow, ledger: LedgerService, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = ledger.total_assets()
    parse(window, "咖啡25元")
    window.save_draft()
    finish(qtbot, window)
    row = records(window)[0]
    window._transaction_action("edit", row)
    dialogs = window.findChildren(EditTransactionDialog)
    dialog = dialogs[-1]
    assert dialog.form.account.currentData() == row["account_id"]
    dialog.form.amount.setText("20")
    dialog.accept()
    finish(qtbot, window)
    updated = ledger.transaction(str(row["id"]))
    assert updated["amount_minor"] == 2000
    assert updated["source"] == "local_rule"
    assert ledger.total_assets() == before - 2000
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes
    )
    window._transaction_action("delete", records(window)[0])
    finish(qtbot, window)
    assert ledger.total_assets() == before
    window.transactions.include_deleted.setChecked(True)
    window.transactions.refresh()
    deleted = next(row for row in window.transactions._rows if row["id"] == updated["id"])
    window._transaction_action("restore", deleted)
    finish(qtbot, window)
    assert ledger.total_assets() == before - 2000


def test_stale_edit_returns_conflict_and_preserves_dialog(
    qtbot: QtBot, window: MainWindow, ledger: LedgerService
) -> None:
    parse(window, "咖啡25元")
    window.save_draft()
    finish(qtbot, window)
    row = records(window)[0]
    window._transaction_action("edit", row)
    dialog = window.findChildren(EditTransactionDialog)[-1]
    replacement = dialog.payload()
    ledger.execute(str(uuid4()), dialog.command_type, replacement)
    dialog.form.amount.setText("28")
    dialog.accept()
    qtbot.waitUntil(lambda: not window.bridge.busy)
    assert "VERSION_CONFLICT" in window.notice.text()
    assert dialog.isVisible()
    assert dialog.form.amount.text() == "28"
    assert records(window)[0]["amount_minor"] == 2500


def test_theme_and_timezone_persist_and_invalidate_draft(window: MainWindow) -> None:
    parse(window, "咖啡25元")
    window.theme.setCurrentIndex(window.theme.findData("dark"))
    window.time_zone.setCurrentText("UTC")
    window._apply_settings()
    assert window.settings == DesktopSettings("dark", "UTC")
    assert window.ledger.time_zone == "UTC"
    assert window.settings_store is not None
    assert window.settings_store.load() == window.settings
    assert "#1b1e25" in window.styleSheet()
    assert window._parsed is None
    assert not window.save.isEnabled()


def test_next_local_day_account_creation_uses_same_timezone(
    window: MainWindow, ledger: LedgerService
) -> None:
    ledger.clock = lambda: datetime(2026, 10, 2, 18, tzinfo=UTC)
    assert window.today() == date(2026, 10, 3)
    dialog = EntityDialog(ledger, "account", time_zone="Asia/Shanghai", parent=window)
    dialog.name.setText("次日账户")
    dialog.opening.setText("0")
    payload = dialog.payload()
    ledger.execute(str(uuid4()), dialog.command_type, payload)
    created = next(row for row in ledger.entities("account") if row["id"] == payload["id"])
    assert created["balance_start_on"] == "2026-10-03"


def test_timezone_change_preserves_consumed_multi_event_spans(
    qtbot: QtBot, window: MainWindow
) -> None:
    parse(window, "昨天吃饭128元，朋友转我50元")
    window.candidates.setCurrentIndex(1)
    window._select_candidate(1)
    window.save_draft()
    finish(qtbot, window)
    window.time_zone.setCurrentText("UTC")
    window._apply_settings()
    window.parse_input()
    assert window.candidates.count() == 2
    assert "吃饭128" not in window.candidates.itemText(1)


def test_one_modal_edit_prevents_replacing_unsaved_dialog(qtbot: QtBot, window: MainWindow) -> None:
    parse(window, "咖啡25元")
    window.save_draft()
    finish(qtbot, window)
    row = records(window)[0]
    window._transaction_action("edit", row)
    dialog = window._pending_dialog
    assert dialog is not None
    assert dialog.windowModality() == Qt.WindowModality.WindowModal
    window._transaction_action("edit", row)
    assert window._pending_dialog is dialog
    assert len(window.findChildren(EditTransactionDialog)) == 1
    dialog.reject()
    assert window._pending_dialog is None


def test_edit_preserves_original_archived_account(
    qtbot: QtBot, window: MainWindow, ledger: LedgerService
) -> None:
    parse(window, "咖啡25元")
    window.save_draft()
    finish(qtbot, window)
    row = records(window)[0]
    account = next(item for item in ledger.entities("account") if item["id"] == row["account_id"])
    replacement = next(item for item in ledger.entities("account") if item["id"] != account["id"])
    ledger.execute(
        str(uuid4()),
        "account.archive.v1",
        {
            "id": account["id"],
            "expected_version": account["version"],
            "archived": True,
            "replacement_default_id": replacement["id"],
        },
    )
    window._transaction_action("edit", row)
    dialog = window.findChildren(EditTransactionDialog)[-1]
    assert "已归档" in dialog.form.account.currentText()
    assert dialog.form.account.currentData() == account["id"]
    dialog.form.amount.setText("20")
    dialog.accept()
    finish(qtbot, window)
    assert records(window)[0]["amount_minor"] == 2000


def test_failed_management_dialog_survives_unrelated_draft_save(
    qtbot: QtBot, window: MainWindow
) -> None:
    retained = EntityDialog(window.ledger, "tag", parent=window.management)
    retained.name.setText("未保存标签")
    window.management._pending_dialog = retained
    parse(window, "咖啡25元")
    window.save_draft()
    finish(qtbot, window)
    assert window.management._pending_dialog is retained
    assert retained.name.text() == "未保存标签"
