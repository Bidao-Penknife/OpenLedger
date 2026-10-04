"""Versioned replacement dialogs preserving financial source and fixed calibrations."""

from datetime import date
from typing import cast

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from openledger.domain.errors import LedgerError
from openledger.domain.values import normalize_text
from openledger.infrastructure.ledger import LedgerService
from openledger.presentation.views.transaction_form import TransactionForm


class EditTransactionDialog(QDialog):
    """Preserve source attribution and optimistic version through an edit."""

    command_type = "transaction.update.v1"

    def __init__(
        self,
        ledger: LedgerService,
        snapshot: dict[str, object],
        today: date,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.snapshot = snapshot
        self.setWindowTitle(self.tr("编辑交易"))
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        self.form = TransactionForm(ledger, today, self)
        self.form.refresh(include_archived=True)
        loaded = dict(snapshot)
        entries = snapshot.get("entries")
        if isinstance(entries, list) and len(entries) == 1 and isinstance(entries[0], dict):
            loaded["account_id"] = entries[0]["account_id"]
        self.form.load(loaded)
        self.form.kind.setEnabled(False)
        layout.addWidget(self.form)
        self.error = QLabel(self)
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(self.tr("保存"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self.tr("取消"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def payload(self) -> dict[str, object]:
        fields = self.form.fields(
            str(self.snapshot["time_zone"]),
            source=str(self.snapshot["source"]),
            source_text=cast(str | None, self.snapshot["source_text"]),
        )
        return {
            "id": self.snapshot["id"],
            "expected_version": self.snapshot["version"],
            "fields": fields,
        }

    def accept(self) -> None:
        try:
            self.payload()
        except LedgerError as error:
            self.error.setText(self.tr("请检查必填字段和金额。") + f" [{error.code}]")
            return
        super().accept()


class AdjustmentDialog(QDialog):
    """Only metadata of an existing fixed-delta calibration can be edited."""

    command_type = "adjustment.metadata.update.v1"

    def __init__(self, snapshot: dict[str, object], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.snapshot = snapshot
        self.setWindowTitle(self.tr("编辑校准说明"))
        layout = QFormLayout(self)
        self.note = QLineEdit(str(snapshot.get("note") or ""), self)
        self.reason = QLineEdit(str(snapshot.get("adjustment_reason") or ""), self)
        layout.addRow(self.tr("备注"), self.note)
        layout.addRow(self.tr("校准理由"), self.reason)
        self.error = QLabel(self)
        layout.addRow(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(self.tr("保存"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self.tr("取消"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def payload(self) -> dict[str, object]:
        reason = normalize_text(self.reason.text(), 1000, required=True)
        return {
            "id": self.snapshot["id"],
            "expected_version": self.snapshot["version"],
            "note": self.note.text() or None,
            "reason": reason,
            "tag_ids": self.snapshot["tag_ids"],
        }

    def accept(self) -> None:
        try:
            self.payload()
        except LedgerError as error:
            self.error.setText(self.tr("请输入校准理由。") + f" [{error.code}]")
            return
        super().accept()
