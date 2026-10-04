"""Exercise confirmed management commands without letting widgets write funds."""

from datetime import date
from typing import cast
from uuid import uuid4

import pytest
from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog
from pytestqt.qtbot import QtBot

from openledger.application.dto.ledger import RefundFields, TransactionFields, TransferFields
from openledger.domain.errors import LedgerError
from openledger.domain.money import MAX_INT64
from openledger.infrastructure.ledger import LedgerService
from openledger.presentation.views.management import (
    ArchiveDialog,
    EntityDialog,
    ManagementPage,
    OperationDialog,
)

pytestmark = pytest.mark.ui


def _ids(ledger: LedgerService) -> tuple[str, str]:
    accounts = ledger.entities("account")
    return str(accounts[0]["id"]), str(accounts[1]["id"])


def _expense(ledger: LedgerService, *, amount: int = 12_800) -> str:
    category = next(row for row in ledger.entities("category") if row["name"] == "餐饮")
    identifier = str(uuid4())
    ledger.record(
        TransactionFields(
            "expense",
            amount,
            _ids(ledger)[0],
            str(ledger.entities("book")[0]["id"]),
            str(category["id"]),
            date(2026, 10, 1),
            time_zone="Asia/Shanghai",
        ),
        request_id=str(uuid4()),
        transaction_id=identifier,
    )
    return identifier


def _choose_accounts(dialog: OperationDialog, ledger: LedgerService) -> None:
    source, destination = _ids(ledger)
    dialog.account.setCurrentIndex(dialog.account.findData(source))
    dialog.destination.setCurrentIndex(dialog.destination.findData(destination))


def test_create_account_requires_explicit_opening_and_has_no_write(
    qtbot: QtBot, ledger: LedgerService
) -> None:
    before = ledger.balances()
    dialog = EntityDialog(ledger, "account")
    qtbot.addWidget(dialog)
    dialog.name.setText("新微信账户")
    with pytest.raises(LedgerError, match="INVALID_AMOUNT"):
        dialog.payload()
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.error.text()
    dialog.opening.setText("0.00")
    payload = dialog.payload()
    assert payload["opening_balance_minor"] == 0
    assert payload["balance_start_on"] == date(2026, 10, 2)
    assert payload["id"] == dialog.payload()["id"]
    assert ledger.balances() == before
    assert len(ledger.entities("account")) == 2


def test_account_form_preserves_signed_fen_and_optimistic_edit(
    qtbot: QtBot, ledger: LedgerService
) -> None:
    create = EntityDialog(ledger, "account")
    qtbot.addWidget(create)
    create.name.setText("信用卡")
    create.opening.setText("-128.09")
    assert create.payload()["opening_balance_minor"] == -12_809
    row = ledger.entities("account")[0]
    edit = EntityDialog(ledger, "account", row)
    qtbot.addWidget(edit)
    edit.name.setText("修改后的名称")
    payload = edit.payload()
    assert payload["expected_version"] == row["version"]
    assert payload["account_type"] == row["account_type"]
    assert "opening_balance_minor" not in payload
    assert "balance_start_on" not in payload


@pytest.mark.parametrize("text", ["0.001", "1e3", "NaN", "1,000.00", "9999999999999"])
def test_opening_rejects_rounding_and_nondecimal_values(
    qtbot: QtBot, ledger: LedgerService, text: str
) -> None:
    dialog = EntityDialog(ledger, "account")
    qtbot.addWidget(dialog)
    dialog.name.setText("账户")
    dialog.opening.setText(text)
    with pytest.raises(LedgerError):
        dialog.payload()


def test_future_account_cutpoint_is_rejected(qtbot: QtBot, ledger: LedgerService) -> None:
    dialog = EntityDialog(ledger, "account")
    qtbot.addWidget(dialog)
    dialog.name.setText("账户")
    dialog.opening.setText("0")
    dialog.start.setDate(QDate(2026, 10, 3))
    with pytest.raises(LedgerError):
        dialog.payload()


def test_category_parent_options_follow_kind(qtbot: QtBot, ledger: LedgerService) -> None:
    dialog = EntityDialog(ledger, "category")
    qtbot.addWidget(dialog)
    dialog.name.setText("外卖")
    restaurant = next(row for row in ledger.entities("category") if row["name"] == "餐饮")
    salary = next(row for row in ledger.entities("category") if row["name"] == "工资")
    assert dialog.parent_category.findData(restaurant["id"]) >= 0
    assert dialog.parent_category.findData(salary["id"]) == -1
    dialog.parent_category.setCurrentIndex(dialog.parent_category.findData(restaurant["id"]))
    dialog.color.setText("#4f8cff")
    assert dialog.payload()["parent_id"] == restaurant["id"]
    dialog.kind.setCurrentIndex(dialog.kind.findData("income"))
    assert dialog.parent_category.findData(restaurant["id"]) == -1
    assert dialog.parent_category.findData(salary["id"]) >= 0
    assert dialog.payload()["parent_id"] is None


def test_channel_mapping_is_explicit_and_code_is_stable(
    qtbot: QtBot, ledger: LedgerService
) -> None:
    dialog = EntityDialog(ledger, "payment_method")
    qtbot.addWidget(dialog)
    dialog.name.setText("购物卡")
    assert dialog.payload()["default_account_id"] is None
    first = dialog.payload()
    assert first["code"] == dialog.payload()["code"]
    dialog.mapping.setCurrentIndex(dialog.mapping.findData(_ids(ledger)[0]))
    assert dialog.payload()["default_account_id"] == _ids(ledger)[0]
    existing = ledger.entities("payment_method")[0]
    edit = EntityDialog(ledger, "payment_method", existing)
    qtbot.addWidget(edit)
    assert "code" not in edit.payload()


def test_archive_default_requires_replacement_and_never_changes_balance(
    qtbot: QtBot, ledger: LedgerService
) -> None:
    default_id = ledger.preferences()["default_account_id"]
    row = next(item for item in ledger.entities("account") if item["id"] == default_id)
    before = ledger.balances()
    dialog = ArchiveDialog(ledger, "account", row, is_default=True)
    qtbot.addWidget(dialog)
    with pytest.raises(LedgerError, match="MISSING_REQUIRED_FIELD"):
        dialog.payload()
    assert dialog.replacement.findData(default_id) == -1
    dialog.replacement.setCurrentIndex(1)
    payload = dialog.payload()
    assert payload["archived"] is True
    assert payload["expected_version"] == row["version"]
    assert payload["replacement_default_id"] != default_id
    assert ledger.balances() == before
    assert ledger.preferences()["default_account_id"] == default_id


def test_transfer_requires_distinct_accounts_and_keeps_one_intent_id(
    qtbot: QtBot, ledger: LedgerService
) -> None:
    before = ledger.balances()
    dialog = OperationDialog(ledger, "transfer")
    qtbot.addWidget(dialog)
    dialog.amount.setText("25.50")
    with pytest.raises(LedgerError, match="MISSING_REQUIRED_FIELD"):
        dialog.payload()
    _choose_accounts(dialog, ledger)
    payload = dialog.payload()
    fields = cast(dict[str, object], payload["fields"])
    assert fields["amount_minor"] == 2550
    assert fields["from_account_id"] != fields["to_account_id"]
    assert payload["id"] == dialog.payload()["id"]
    assert ledger.balances() == before
    dialog.destination.setCurrentIndex(dialog.destination.findData(fields["from_account_id"]))
    with pytest.raises(LedgerError, match="INVALID_TRANSFER"):
        dialog.payload()


def test_refund_respects_original_date_and_remaining_quota(
    qtbot: QtBot, ledger: LedgerService
) -> None:
    original = _expense(ledger)
    existing = str(uuid4())
    ledger.refund(
        RefundFields(original, 3000, _ids(ledger)[0], date(2026, 10, 2)),
        request_id=str(uuid4()),
        transaction_id=existing,
    )
    dialog = OperationDialog(ledger, "refund", original)
    qtbot.addWidget(dialog)
    _choose_accounts(dialog, ledger)
    dialog.amount.setText("98.00")
    assert (
        cast(dict[str, object], dialog.payload()["fields"])["original_transaction_id"] == original
    )
    dialog.amount.setText("98.01")
    with pytest.raises(LedgerError, match="REFUND_LIMIT_EXCEEDED"):
        dialog.payload()
    dialog.amount.setText("10")
    dialog.day.setDate(QDate(2026, 9, 30))
    with pytest.raises(LedgerError, match="REFUND_DATE_BEFORE_EXPENSE"):
        dialog.payload()
    edit = OperationDialog(ledger, "refund_edit", existing)
    qtbot.addWidget(edit)
    edit.amount.setText("128.00")
    payload = edit.payload()
    assert payload["id"] == existing
    assert payload["expected_version"] == 1


def test_transfer_edit_preserves_period_source_and_archived_references(
    qtbot: QtBot, ledger: LedgerService
) -> None:
    first, second = _ids(ledger)
    identifier = str(uuid4())
    ledger.transfer(
        TransferFields(
            first,
            second,
            1000,
            date(2026, 10, 1),
            time_zone="Asia/Shanghai",
            occurrence_precision="period",
            time_period="evening",
            source="local_rule",
            source_text="昨晚转账10元",
        ),
        request_id=str(uuid4()),
        transaction_id=identifier,
    )
    row = next(item for item in ledger.entities("account") if item["id"] == first)
    default_id = ledger.preferences()["default_account_id"]
    archive: dict[str, object] = {"id": first, "expected_version": row["version"], "archived": True}
    if default_id == first:
        archive["replacement_default_id"] = second
    ledger.execute(str(uuid4()), "account.archive.v1", archive)
    dialog = OperationDialog(ledger, "transfer_edit", identifier)
    qtbot.addWidget(dialog)
    assert dialog.account.currentData() == first
    assert "已归档" in dialog.account.currentText()
    assert not dialog.day.isEnabled()
    dialog.amount.setText("12.00")
    payload = dialog.payload()
    fields = cast(dict[str, object], payload["fields"])
    assert payload["expected_version"] == 1
    assert fields["source"] == "local_rule"
    assert fields["source_text"] == "昨晚转账10元"
    assert fields["occurrence_precision"] == "period"
    assert fields["time_period"] == "evening"
    assets = ledger.total_assets()
    ledger.execute(str(uuid4()), dialog.command_type, payload)
    saved = ledger.transaction(identifier)
    assert saved["amount_minor"] == 1200
    assert saved["version"] == 2
    assert ledger.total_assets() == assets


def test_adjustment_accepts_exact_int64_target_and_requires_reason(
    qtbot: QtBot, ledger: LedgerService
) -> None:
    dialog = OperationDialog(ledger, "adjust")
    qtbot.addWidget(dialog)
    _choose_accounts(dialog, ledger)
    dialog.amount.setText("92233720368547758.07")
    with pytest.raises(LedgerError, match="MISSING_REQUIRED_FIELD"):
        dialog.payload()
    dialog.reason.setText("与银行核对")
    assert dialog.payload()["target_balance_minor"] == MAX_INT64
    assert dialog.payload()["occurred_on"] == date(2026, 10, 2)
    assert not dialog.day.isEnabled()
    dialog.amount.setText("92233720368547758.08")
    with pytest.raises(LedgerError, match="AGGREGATE_OUT_OF_RANGE"):
        dialog.payload()
    dialog.amount.setText("-0.01")
    assert dialog.payload()["target_balance_minor"] == -1
    dialog.day.setDate(QDate(2026, 10, 1))
    with pytest.raises(LedgerError, match="ADJUSTMENT_DATE_MUST_BE_TODAY"):
        dialog.payload()


def test_opening_change_keeps_captured_account_version(qtbot: QtBot, ledger: LedgerService) -> None:
    dialog = OperationDialog(ledger, "opening")
    qtbot.addWidget(dialog)
    _choose_accounts(dialog, ledger)
    assert dialog.day.date() == QDate(2026, 1, 1)
    dialog.amount.setText("-50.25")
    original = dialog.payload()
    row = next(item for item in ledger.entities("account") if item["id"] == original["account_id"])
    ledger.execute(
        str(uuid4()),
        "account.update.v1",
        {
            "id": row["id"],
            "expected_version": row["version"],
            "name": "并发修改",
            "account_type": row["account_type"],
        },
    )
    assert dialog.payload()["expected_account_version"] == row["version"]
    assert dialog.payload()["opening_balance_minor"] == -5025
    with pytest.raises(LedgerError, match="VERSION_CONFLICT"):
        ledger.execute(str(uuid4()), dialog.command_type, dialog.payload())


def test_management_default_emits_versioned_command_without_writing(
    qtbot: QtBot, ledger: LedgerService
) -> None:
    page = ManagementPage(ledger)
    qtbot.addWidget(page)
    page.show()
    previous = ledger.preferences()
    page.table.selectRow(1)
    selected = page.selected()
    assert selected is not None
    with qtbot.waitSignal(page.commandRequested) as result:
        QTest.mouseClick(page.default, Qt.MouseButton.LeftButton)
    assert result.args == [
        "account.default.set.v1",
        {"id": selected["id"], "expected_version": selected["version"]},
    ]
    assert ledger.preferences() == previous


def test_management_includes_archived_asset_and_refreshes_snapshot(
    qtbot: QtBot, ledger: LedgerService
) -> None:
    page = ManagementPage(ledger)
    qtbot.addWidget(page)
    balance = ledger.total_assets()
    first, second = _ids(ledger)
    row = next(item for item in ledger.entities("account") if item["id"] == first)
    ledger.execute(
        str(uuid4()),
        "account.archive.v1",
        {
            "id": first,
            "expected_version": row["version"],
            "archived": True,
            "replacement_default_id": second,
        },
    )
    page.refresh()
    assert page.table.rowCount() == 1
    page.include_archived.setChecked(True)
    assert page.table.rowCount() == 2
    assert ledger.total_assets() == balance
    assert "含已归档账户" in page.status.text()


def test_management_failed_command_reopens_same_fields_and_same_id(
    qtbot: QtBot, ledger: LedgerService
) -> None:
    page = ManagementPage(ledger)
    qtbot.addWidget(page)
    page.show()
    dialog = EntityDialog(ledger, "book", parent=page)
    qtbot.addWidget(dialog)
    dialog.name.setText("旅行账本")
    QTimer.singleShot(0, dialog.accept)
    with qtbot.waitSignal(page.commandRequested) as first:
        page._confirm(dialog, dialog.command_type)
    page.command_finished(False)
    assert dialog.isVisible()
    assert dialog.name.text() == "旅行账本"
    with qtbot.waitSignal(page.commandRequested) as retried:
        dialog.accept()
    assert first.args == retried.args
    assert len(ledger.entities("book")) == 1
    page.command_finished(True)
    assert page._pending_dialog is None


def test_canceled_management_dialog_never_reappears_on_later_error(
    qtbot: QtBot, ledger: LedgerService
) -> None:
    page = ManagementPage(ledger)
    qtbot.addWidget(page)
    dialog = EntityDialog(ledger, "book", parent=page)
    dialog.name.setText("取消的新账本")
    QTimer.singleShot(0, dialog.reject)
    page._confirm(dialog, dialog.command_type)
    assert page._pending_dialog is None
    page.command_finished(False)
    assert page._pending_dialog is None
    assert len(ledger.entities("book")) == 1


def test_failed_management_draft_is_preserved_when_another_form_is_requested(
    qtbot: QtBot, ledger: LedgerService
) -> None:
    page = ManagementPage(ledger)
    qtbot.addWidget(page)
    first = EntityDialog(ledger, "book", parent=page)
    first.name.setText("保留这个旅行账本")
    QTimer.singleShot(0, first.accept)
    page._confirm(first, first.command_type)
    page.command_finished(False)
    second = EntityDialog(ledger, "book", parent=page)
    page._confirm(second, second.command_type)
    assert page._pending_dialog is first
    assert first.isVisible()
    assert first.name.text() == "保留这个旅行账本"
    first.reject()
    assert page._pending_dialog is None
