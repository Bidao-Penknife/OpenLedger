"""A modeless quick-entry draft that shares the main window's command writer."""

from datetime import date
from typing import cast
from uuid import uuid4
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from openledger.application.dto.parsing import (
    ChannelAccountMapping,
    ParseChoice,
    ParsedDraft,
    ParseRequest,
    ParseResult,
)
from openledger.application.parsing import LocalParser
from openledger.domain.errors import LedgerError
from openledger.infrastructure.ledger import LedgerService
from openledger.presentation.views.transaction_form import QuickInput, TransactionForm


class QuickEntryWindow(QDialog):
    """Parse locally, require confirmation, and emit an idempotent financial intent."""

    commandRequested = Signal(str, object)

    def __init__(
        self,
        ledger: LedgerService,
        parent: QWidget | None = None,
        *,
        time_zone: str = "Asia/Shanghai",
    ) -> None:
        super().__init__(parent)
        ZoneInfo(time_zone)
        self.ledger, self.time_zone = ledger, time_zone
        self.parser = LocalParser()
        self._draft_id, self._transaction_id = str(uuid4()), str(uuid4())
        self._revision = 1
        self._result: ParseResult | None = None
        self._candidate: ParsedDraft | None = None
        self._manual = False
        self._busy = False
        self.setObjectName("quickEntryWindow")
        self.setWindowTitle(self.tr("快速记账 · OpenLedger"))
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setModal(False)
        self.resize(620, 720)
        self.setMinimumSize(420, 360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        heading = QLabel(self.tr("快速记账"), self)
        heading.setProperty("heading", True)
        layout.addWidget(heading)
        self.input = QuickInput(self)
        self.input.setObjectName("quickEntryInput")
        self.input.setPlaceholderText(
            self.tr("例如：咖啡 25元；Enter 解析，核对后 Ctrl+Enter 保存")
        )
        layout.addWidget(self.input)
        toolbar = QHBoxLayout()
        self.parse = QPushButton(self.tr("解析草稿（Enter）"), self)
        self.parse.setObjectName("quickEntryParse")
        self.parse.setProperty("primary", True)
        self.manual = QPushButton(self.tr("手工填写"), self)
        self.new = QPushButton(self.tr("新建草稿"), self)
        for button in (self.parse, self.manual, self.new):
            button.setAutoDefault(False)
            toolbar.addWidget(button)
        layout.addLayout(toolbar)
        self.status = QLabel(self.tr("确认前请核对金额、日期、账户和分类。"), self)
        self.status.setObjectName("quickEntryStatus")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.form = TransactionForm(ledger, self.today(), scroll)
        scroll.setWidget(self.form)
        layout.addWidget(scroll, 1)
        self.save = QPushButton(self.tr("确认保存（Ctrl+Enter）"), self)
        self.save.setObjectName("quickEntrySave")
        self.save.setAutoDefault(False)
        self.save.setProperty("primary", True)
        layout.addWidget(self.save)
        self.input.textChanged.connect(self._text_changed)
        self.input.parseRequested.connect(self.parse_input)
        self.input.saveRequested.connect(self.save_draft)
        self.parse.clicked.connect(self.parse_input)
        self.save.clicked.connect(self.save_draft)
        self.manual.clicked.connect(self._manual_draft)
        self.new.clicked.connect(self._reset)
        self.form.edited.connect(self._edited)
        self._reset()

    def today(self) -> date:
        return self.ledger.clock().astimezone(ZoneInfo(self.time_zone)).date()

    def _choices(self, entity: str) -> tuple[ParseChoice, ...]:
        return tuple(
            ParseChoice(
                str(row["id"]), str(row["name"]), kind=cast(str | None, row.get("transaction_kind"))
            )
            for row in self.ledger.entities(entity)
        )

    def _text_changed(self) -> None:
        self._revision += 1
        self._result, self._candidate, self._manual = None, None, False
        self.save.setEnabled(False)
        self.status.setText(self.tr("输入已变化，请重新解析，或选择手工填写。"))

    def _edited(self) -> None:
        self._revision += 1

    def _manual_draft(self) -> None:
        if self._busy or self.input.composing:
            return
        self._manual, self._candidate = True, None
        self.save.setEnabled(True)
        self.status.setText(self.tr("手工填写：保存前请核对表单中的所有字段。"))

    def parse_input(self) -> None:
        """Read reference choices and apply a single pure local draft without writing."""
        if self._busy or self.input.composing:
            return
        preferences = self.ledger.preferences()
        request = ParseRequest(
            self._draft_id,
            self._revision,
            self.input.toPlainText(),
            self.today(),
            self.time_zone,
            current_book_id=cast(
                str | None, self.form.book.currentData() or preferences["default_book_id"]
            ),
            default_account_id=cast(str | None, preferences["default_account_id"]),
            category_choices=self._choices("category"),
            account_choices=self._choices("account"),
            payment_method_choices=self._choices("payment_method"),
            channel_account_mappings=tuple(
                ChannelAccountMapping(str(row["id"]), str(row["default_account_id"]))
                for row in self.ledger.entities("payment_method")
                if row["default_account_id"]
            ),
        )
        try:
            result = self.parser.parse(request)
        except (LedgerError, ValueError):
            self.status.setText(self.tr("无法解析，请修改文字或选择手工填写。"))
            self.save.setEnabled(False)
            return
        if result.draft_id != self._draft_id or result.revision != self._revision:
            return
        self._result, self._candidate, self._manual = result, None, False
        if result.status != "single" or len(result.drafts) != 1:
            self.save.setEnabled(False)
            self.status.setText(
                self.tr("请每次输入一笔记录，并处理歧义；也可以选择手工填写。")
                + "\n"
                + "\n".join(issue.message for issue in result.issues)
            )
            return
        self._candidate = result.drafts[0]
        values: dict[str, object] = {"time_zone": self.time_zone}
        for field in (
            "kind",
            "amount_minor",
            "occurred_on",
            "time_period",
            "occurred_at_utc",
            "book_id",
            "account_id",
            "category_id",
            "payment_method_id",
            "counterparty",
            "merchant",
            "location",
            "note",
        ):
            if field not in self.form.user_fields:
                values[field] = getattr(self._candidate, field).value
        self.form.load(values)
        self.save.setEnabled(True)
        self.status.setText(
            self.tr("已生成规则建议，核对并补齐字段后确认保存。")
            + ("\n" + "\n".join(issue.message for issue in result.issues) if result.issues else "")
        )

    def save_draft(self) -> None:
        """Emit one transaction command; the main window owns its single writer."""
        if self._busy or self.input.composing:
            return
        try:
            if not self._manual:
                if self._candidate is None or self._result is None:
                    raise LedgerError("DRAFT_NOT_CONFIRMED")
                for issue in self._result.issues:
                    if issue.blocking and issue.field not in self.form.user_fields:
                        raise LedgerError("DRAFT_NOT_CONFIRMED")
            fields = self.form.fields(
                self.time_zone,
                source="manual" if self._manual else "local_rule",
                source_text=None if self._manual else self.input.toPlainText(),
            )
            self.set_busy(True)
            self.status.setText(self.tr("正在保存，请稍候…"))
            self.commandRequested.emit(
                "transaction.record.v1", {"id": self._transaction_id, "fields": fields}
            )
        except LedgerError as error:
            self.status.setText(
                self.tr("请补齐字段并处理金额、日期或识别歧义。") + f" [{error.code}]"
            )

    def set_busy(self, busy: bool) -> None:
        """Prevent edits or a second intent while the shared writer is busy."""
        self._busy = busy
        for widget in (self.input, self.form, self.parse, self.manual, self.new):
            widget.setEnabled(not busy)
        self.save.setEnabled(not busy and (self._manual or self._candidate is not None))

    def command_finished(self, success: bool, error_code: str | None = None) -> None:
        """Keep the same transaction identifier and editable input when saving fails."""
        self.set_busy(False)
        if success:
            self._reset()
            self.status.setText(self.tr("已保存，可继续输入下一笔。"))
            self.input.setFocus()
        else:
            message = self.tr("保存未完成，草稿已保留。请检查后重试。")
            if error_code:
                message += f" [{error_code}]"
            self.status.setText(message)

    def _reset(self) -> None:
        if self._busy:
            return
        self._draft_id, self._transaction_id = str(uuid4()), str(uuid4())
        self._revision += 1
        self.input.clear()
        self._result, self._candidate, self._manual = None, None, False
        self.form.user_fields.clear()
        preferences = self.ledger.preferences()
        self.form.load(
            {
                "kind": "expense",
                "amount_minor": 0,
                "occurred_on": self.today(),
                "time_period": None,
                "occurred_at_utc": None,
                "time_zone": self.time_zone,
                "book_id": preferences["default_book_id"],
                "account_id": preferences["default_account_id"],
                "category_id": None,
                "payment_method_id": None,
                "counterparty": None,
                "merchant": None,
                "location": None,
                "note": None,
                "tag_ids": (),
            }
        )
        self.save.setEnabled(False)
        self.status.setText(self.tr("输入一笔记录，解析并核对后保存；Esc 可收起并保留草稿。"))

    def refresh(self) -> None:
        """Refresh metadata while preserving unsaved edits and selected identifiers."""
        self.form.refresh()
        preferences = self.ledger.preferences()
        for key, combo in (("book_id", self.form.book), ("account_id", self.form.account)):
            if combo.currentData() is None:
                self.form.load({key: preferences["default_" + key]})

    def set_time_zone(self, time_zone: str) -> None:
        """Invalidate parsed context after a timezone change without losing raw input."""
        ZoneInfo(time_zone)
        if time_zone != self.time_zone:
            self.time_zone = time_zone
            self._revision += 1
            self._result, self._candidate = None, None
            self.form.load({"time_zone": time_zone})
            self.save.setEnabled(self._manual and not self._busy)
            self.status.setText(self.tr("时区已变化，请重新解析并核对日期。"))

    def open(self) -> None:
        """Reuse the modeless window and its draft instead of creating duplicate dialogs."""
        self.refresh()
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def reject(self) -> None:
        """Escape hides the quick window and keeps its unsaved draft."""
        self.hide()

    def closeEvent(self, event: QCloseEvent) -> None:
        """The close button retains an unsaved draft for the next quick entry."""
        event.ignore()
        self.hide()
