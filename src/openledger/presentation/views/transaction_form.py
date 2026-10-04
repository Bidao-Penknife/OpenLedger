"""Editable confirmation form and an input editor that respects Chinese IME."""

from collections.abc import Mapping
from datetime import date, datetime
from typing import cast
from zoneinfo import ZoneInfo

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QInputMethodEvent, QKeyEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QWidget,
)

from openledger.domain.errors import LedgerError
from openledger.domain.money import parse_amount
from openledger.infrastructure.ledger import LedgerService


def money_text(minor: int) -> str:
    """Format cents with integer arithmetic, including negative account balances."""
    sign = "-" if minor < 0 else ""
    whole, fraction = divmod(abs(minor), 100)
    return f"{sign}{whole:,}.{fraction:02d}"


class QuickInput(QPlainTextEdit):
    """Enter parses, Ctrl+Enter confirms, and IME composition consumes neither."""

    parseRequested = Signal()
    saveRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.composing = False
        self.setObjectName("quickInput")
        self.setPlaceholderText(self.tr("例如：昨天晚上和朋友吃火锅花了128元，微信支付"))
        self.setMaximumHeight(100)

    def inputMethodEvent(self, event: QInputMethodEvent) -> None:
        self.composing = bool(event.preeditString())
        super().inputMethodEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter} and not self.composing:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                if not event.isAutoRepeat():
                    self.saveRequested.emit()
                event.accept()
                return
            if not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                if not event.isAutoRepeat():
                    self.parseRequested.emit()
                event.accept()
                return
        super().keyPressEvent(event)


class TransactionForm(QWidget):
    """Carry an editable draft; validating it does not execute a financial command."""

    edited = Signal()

    def __init__(self, ledger: LedgerService, today: date, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ledger = ledger
        self._categories: tuple[dict[str, object], ...] = ()
        self._loading = False
        self.user_fields: set[str] = set()
        self._exact: datetime | str | None = None
        self._time_zone = "Asia/Shanghai"
        layout = QFormLayout(self)
        layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.kind = QComboBox(self)
        self.kind.addItem(self.tr("支出"), "expense")
        self.kind.addItem(self.tr("收入"), "income")
        self.amount = QLineEdit(self)
        self.amount.setPlaceholderText(self.tr("0.00，单位：元"))
        self.day = QDateEdit(self)
        self.day.setCalendarPopup(True)
        self.day.setDisplayFormat("yyyy-MM-dd")
        self.day.setDate(QDate(today.year, today.month, today.day))
        self.period = QComboBox(self)
        for label, value in [
            (self.tr("仅日期"), None),
            (self.tr("早上"), "morning"),
            (self.tr("中午"), "noon"),
            (self.tr("下午"), "afternoon"),
            (self.tr("晚上"), "evening"),
            (self.tr("夜间"), "night"),
        ]:
            self.period.addItem(label, value)
        self.account = QComboBox(self)
        self.exact_label = QLabel(self)
        self.exact_label.setObjectName("draftExactTime")
        self.exact_label.setWordWrap(True)
        self._show_exact()
        self.book = QComboBox(self)
        self.category = QComboBox(self)
        self.payment = QComboBox(self)
        self.counterparty = QLineEdit(self)
        self.merchant = QLineEdit(self)
        self.location = QLineEdit(self)
        self.note = QLineEdit(self)
        self.tags = QListWidget(self)
        self.tags.setMaximumHeight(74)
        rows = [
            (self.tr("类型"), self.kind, "draftKind"),
            (self.tr("金额（元）"), self.amount, "draftAmount"),
            (self.tr("日期"), self.day, "draftDate"),
            (self.tr("时段"), self.period, "draftPeriod"),
            (self.tr("精确时间"), self.exact_label, "draftExactTime"),
            (self.tr("资金账户"), self.account, "draftAccount"),
            (self.tr("账本"), self.book, "draftBook"),
            (self.tr("分类"), self.category, "draftCategory"),
            (self.tr("支付方式"), self.payment, "draftPayment"),
            (self.tr("对象"), self.counterparty, "draftCounterparty"),
            (self.tr("商户"), self.merchant, "draftMerchant"),
            (self.tr("地点"), self.location, "draftLocation"),
            (self.tr("备注"), self.note, "draftNote"),
            (self.tr("标签（可多选）"), self.tags, "draftTags"),
        ]
        for label, widget, name in rows:
            widget.setObjectName(name)
            layout.addRow(label, widget)
        self.refresh()
        self.kind.currentIndexChanged.connect(self._kind_changed)
        for widget in (
            self.kind,
            self.period,
            self.account,
            self.book,
            self.category,
            self.payment,
        ):
            widget.currentIndexChanged.connect(self._changed)
        for editor in (self.amount, self.counterparty, self.merchant, self.location, self.note):
            editor.textEdited.connect(self._changed)
        self.day.dateChanged.connect(self._date_changed)
        self.period.activated.connect(self._clear_exact)
        self.tags.itemChanged.connect(self._changed)

    def _changed(self) -> None:
        if not self._loading:
            sender = self.sender()
            name = sender.objectName() if sender else ""
            key = {
                "draftKind": "kind",
                "draftAmount": "amount_minor",
                "draftDate": "occurred_on",
                "draftPeriod": "time_period",
                "draftAccount": "account_id",
                "draftBook": "book_id",
                "draftCategory": "category_id",
                "draftPayment": "payment_method_id",
                "draftCounterparty": "counterparty",
                "draftMerchant": "merchant",
                "draftLocation": "location",
                "draftNote": "note",
                "draftTags": "tag_ids",
            }.get(name)
            if key:
                self.user_fields.add(key)
            self.edited.emit()

    def _clear_exact(self) -> None:
        self._exact = None
        self._show_exact()
        if not self._loading:
            self.user_fields.add("occurred_at_utc")
            self.edited.emit()

    def _date_changed(self) -> None:
        if not self._loading:
            self._clear_exact()
        self._changed()

    @staticmethod
    def select(combo: QComboBox, identifier: object) -> None:
        combo.setCurrentIndex(max(0, combo.findData(identifier)))

    def refresh(self, *, include_archived: bool = False) -> None:
        """Reload available references while preserving each selected identifier."""
        self._loading = True
        for entity, combo, placeholder in [
            ("account", self.account, self.tr("请选择账户")),
            ("book", self.book, self.tr("请选择账本")),
            ("payment_method", self.payment, self.tr("未指定")),
        ]:
            selected = combo.currentData()
            combo.clear()
            combo.addItem(placeholder, None)
            for row in self.ledger.entities(entity, include_archived=include_archived):
                suffix = self.tr("（已归档）") if row["is_archived"] else ""
                combo.addItem(str(row["name"]) + suffix, row["id"])
            self.select(combo, selected)
        self._categories = self.ledger.entities("category", include_archived=include_archived)
        self._kind_changed()
        checked = self.tag_ids()
        self.tags.clear()
        for row in self.ledger.entities("tag", include_archived=include_archived):
            item = QListWidgetItem(str(row["name"]), self.tags)
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if row["id"] in checked else Qt.CheckState.Unchecked
            )
        self._loading = False

    def _kind_changed(self) -> None:
        previous = self._loading
        self._loading = True
        selected = self.category.currentData()
        self.category.clear()
        self.category.addItem(self.tr("请选择分类"), None)
        for row in self._categories:
            if row["transaction_kind"] == self.kind.currentData():
                suffix = self.tr("（已归档）") if row["is_archived"] else ""
                self.category.addItem(str(row["name"]) + suffix, row["id"])
        self.select(self.category, selected)
        self._loading = previous
        self._changed()

    def tag_ids(self) -> tuple[str, ...]:
        return tuple(
            str(self.tags.item(i).data(Qt.ItemDataRole.UserRole))
            for i in range(self.tags.count())
            if self.tags.item(i).checkState() == Qt.CheckState.Checked
        )

    def fields(
        self, time_zone: str, *, source: str = "manual", source_text: str | None = None
    ) -> dict[str, object]:
        """Validate all required selections before exposing a command payload."""
        for combo in (self.account, self.book, self.category):
            if combo.currentData() is None:
                raise LedgerError("MISSING_REQUIRED_FIELD")
        period = self.period.currentData()
        return {
            "kind": self.kind.currentData(),
            "amount_minor": parse_amount(self.amount.text()),
            "account_id": self.account.currentData(),
            "book_id": self.book.currentData(),
            "category_id": self.category.currentData(),
            "occurred_on": self.day.date().toPython(),
            "time_zone": time_zone,
            "occurrence_precision": "exact" if self._exact else "period" if period else "date",
            "time_period": period if not self._exact else None,
            "occurred_at_utc": self._exact,
            "payment_method_id": self.payment.currentData(),
            "counterparty": self.counterparty.text() or None,
            "merchant": self.merchant.text() or None,
            "location": self.location.text() or None,
            "note": self.note.text() or None,
            "tag_ids": self.tag_ids(),
            "source": source,
            "source_text": source_text,
            "currency_code": "CNY",
        }

    def load(self, values: Mapping[str, object]) -> None:
        """Apply a known candidate or full aggregate without emitting user edits."""
        self._loading = True
        if "kind" in values:
            self.select(self.kind, values["kind"])
            self._kind_changed()
        if "amount_minor" in values:
            self.amount.setText(
                money_text(cast(int, values["amount_minor"])).replace(",", "")
                if values["amount_minor"] is not None
                else ""
            )
        occurred_on = values.get("occurred_on")
        if isinstance(occurred_on, str):
            occurred_on = date.fromisoformat(occurred_on)
        if isinstance(occurred_on, date):
            self.day.setDate(QDate(occurred_on.year, occurred_on.month, occurred_on.day))
        for key, combo in [
            ("account_id", self.account),
            ("book_id", self.book),
            ("category_id", self.category),
            ("payment_method_id", self.payment),
            ("time_period", self.period),
        ]:
            if key in values:
                self.select(combo, values[key])
        for key, editor in [
            ("counterparty", self.counterparty),
            ("merchant", self.merchant),
            ("location", self.location),
            ("note", self.note),
        ]:
            if key in values:
                editor.setText(str(values[key] or ""))
        if "occurred_at_utc" in values:
            self._exact = cast(datetime | str | None, values["occurred_at_utc"])
        if "time_zone" in values:
            self._time_zone = str(values["time_zone"])
        self._show_exact()
        ids = values.get("tag_ids")
        if isinstance(ids, (list, tuple)):
            for i in range(self.tags.count()):
                item = self.tags.item(i)
                item.setCheckState(
                    Qt.CheckState.Checked
                    if item.data(Qt.ItemDataRole.UserRole) in ids
                    else Qt.CheckState.Unchecked
                )
        self._loading = False

    def _show_exact(self) -> None:
        if self._exact:
            instant = self._exact
            if isinstance(instant, str):
                instant = datetime.fromisoformat(instant.replace("Z", "+00:00"))
            local = instant.astimezone(ZoneInfo(self._time_zone))
            self.exact_label.setText(
                local.strftime("%Y-%m-%d %H:%M:%S")
                + f" ({self._time_zone})"
                + self.tr("；修改日期或时段可改为模糊时间。")
            )
        else:
            self.exact_label.setText(self.tr("未指定精确时刻"))
