"""Filtered, paginated transaction browser with explicit versioned actions."""

from datetime import date
from typing import cast

from PySide6.QtCore import QDate, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from openledger.application.dto.queries import TransactionFilter
from openledger.infrastructure.ledger import LedgerService
from openledger.infrastructure.queries import LedgerQueries
from openledger.presentation.views.transaction_form import money_text


class TransactionsPage(QWidget):
    """Use bounded read snapshots; hidden IDs and versions come from those rows."""

    actionRequested = Signal(str, object)

    def __init__(
        self,
        ledger: LedgerService,
        queries: LedgerQueries,
        today: date,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.ledger, self.queries = ledger, queries
        self._page = 0
        self._change_seq: int | None = None
        self._rows: tuple[dict[str, object], ...] = ()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        title = QLabel(self.tr("交易记录"), self)
        title.setProperty("heading", True)
        layout.addWidget(title)
        filters = QHBoxLayout()
        self.book = QComboBox(self)
        self.account = QComboBox(self)
        self.kind = QComboBox(self)
        for label, value in [
            (self.tr("全部类型"), None),
            (self.tr("支出"), "expense"),
            (self.tr("收入"), "income"),
            (self.tr("退款"), "expense_refund"),
            (self.tr("转账"), "transfer"),
            (self.tr("期初"), "opening"),
            (self.tr("校准"), "adjustment"),
        ]:
            self.kind.addItem(label, value)
        self.search = QLineEdit(self)
        self.search.setObjectName("transactionSearch")
        self.search.setPlaceholderText(self.tr("搜索备注、对象、商户、地点"))
        for widget in (self.book, self.account, self.kind, self.search):
            filters.addWidget(widget)
        layout.addLayout(filters)
        classification = QHBoxLayout()
        self.category = QComboBox(self)
        self.category.setObjectName("transactionCategory")
        self.tag = QComboBox(self)
        self.tag.setObjectName("transactionTag")
        classification.addWidget(self.category)
        classification.addWidget(self.tag)
        classification.addStretch()
        layout.addLayout(classification)
        dates = QHBoxLayout()
        self.limit_dates = QCheckBox(self.tr("限定日期"), self)
        self.start = QDateEdit(self)
        self.end = QDateEdit(self)
        for editor, day_value in [(self.start, today.replace(day=1)), (self.end, today)]:
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("yyyy-MM-dd")
            editor.setDate(QDate(day_value.year, day_value.month, day_value.day))
        self.include_deleted = QCheckBox(self.tr("显示已删除"), self)
        self.include_deleted.setObjectName("includeDeleted")
        apply = QPushButton(self.tr("筛选 / 刷新"), self)
        apply.setObjectName("applyFilters")
        apply.clicked.connect(self.reset_page)
        for date_widget in (self.limit_dates, self.start, self.end, self.include_deleted, apply):
            dates.addWidget(date_widget)
        dates.addStretch()
        layout.addLayout(dates)
        self.status = QLabel(self)
        self.status.setObjectName("transactionStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 7, self)
        self.table.setObjectName("transactionTable")
        self.table.setHorizontalHeaderLabels(
            [
                self.tr("日期"),
                self.tr("类型"),
                self.tr("金额（账户币种）"),
                self.tr("账户"),
                self.tr("账本 / 分类"),
                self.tr("备注"),
                self.tr("状态"),
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemDoubleClicked.connect(lambda: self._act("edit"))
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.buttons: dict[str, QPushButton] = {}
        for key, label in [
            ("edit", self.tr("编辑")),
            ("delete", self.tr("删除")),
            ("restore", self.tr("恢复")),
            ("refund", self.tr("退款")),
        ]:
            button = QPushButton(label, self)
            button.setObjectName(key + "Transaction")
            button.clicked.connect(lambda checked=False, action=key: self._act(action))
            self.buttons[key] = button
            actions.addWidget(button)
        actions.addStretch()
        self.previous = QPushButton(self.tr("上一页"), self)
        self.next = QPushButton(self.tr("下一页"), self)
        self.previous.clicked.connect(lambda: self._turn(-1))
        self.next.clicked.connect(lambda: self._turn(1))
        actions.addWidget(self.previous)
        actions.addWidget(self.next)
        layout.addLayout(actions)
        self.search.returnPressed.connect(self.reset_page)
        self.refresh()

    def reset_page(self) -> None:
        self._page = 0
        self.refresh()

    def _turn(self, step: int) -> None:
        self._page += step
        self.refresh()

    def selected(self) -> dict[str, object] | None:
        index = self.table.currentRow()
        return self._rows[index] if 0 <= index < len(self._rows) else None

    def _selection_changed(self) -> None:
        row = self.selected()
        live = row is not None and not row.get("deleted_at_utc")
        kind = row.get("kind") if row else None
        self.buttons["edit"].setEnabled(live and kind != "opening")
        self.buttons["delete"].setEnabled(live and kind != "opening")
        self.buttons["restore"].setEnabled(row is not None and not live and kind != "opening")
        self.buttons["refund"].setEnabled(live and kind == "expense")

    def _act(self, action: str) -> None:
        row = self.selected()
        if row is not None and self.buttons.get(action, self.buttons["edit"]).isEnabled():
            self.actionRequested.emit(action, row)

    def refresh(self) -> None:
        """Retain filters and selection while refreshing an immutable page snapshot."""
        selected = self.selected()
        selected_id = selected.get("id") if selected else None
        for entity, combo, label in [
            ("book", self.book, self.tr("全部账本")),
            ("account", self.account, self.tr("全部账户")),
            ("category", self.category, self.tr("全部分类")),
            ("tag", self.tag, self.tr("全部标签")),
        ]:
            previous = combo.currentData()
            combo.clear()
            combo.addItem(label, None)
            for row in self.ledger.entities(entity, include_archived=True):
                combo.addItem(str(row["name"]), row["id"])
            combo.setCurrentIndex(max(0, combo.findData(previous)))
        try:
            page = self.queries.transactions(
                TransactionFilter(
                    book_id=self.book.currentData(),
                    account_id=self.account.currentData(),
                    kind=self.kind.currentData(),
                    start_on=cast(date, self.start.date().toPython())
                    if self.limit_dates.isChecked()
                    else None,
                    end_on=cast(date, self.end.date().toPython())
                    if self.limit_dates.isChecked()
                    else None,
                    include_deleted=self.include_deleted.isChecked(),
                    search=self.search.text(),
                    page=self._page,
                    category_id=self.category.currentData(),
                    tag_id=self.tag.currentData(),
                )
            )
            changed = self._change_seq is not None and self._change_seq != page.change_seq
            self._change_seq = page.change_seq
            if changed and self._page:
                self._page = 0
                self.refresh()
                self.status.setText(
                    self.tr("记录发生变化，已返回第一页。") + " " + self.status.text()
                )
                return
            if self._page and not page.rows:
                self._page = max(0, (page.total - 1) // page.page_size)
                self.refresh()
                return
            self._rows = page.rows
            self.table.setRowCount(len(self._rows))
            kinds = {
                "income": self.tr("收入"),
                "expense": self.tr("支出"),
                "expense_refund": self.tr("退款"),
                "transfer": self.tr("转账"),
                "opening": self.tr("期初"),
                "adjustment": self.tr("校准"),
            }
            for index, row in enumerate(self._rows):
                classification = (
                    " / ".join(
                        str(row[key]) for key in ("book_name", "category_name") if row.get(key)
                    )
                    or "—"
                )
                values = [
                    str(row["occurred_on"]),
                    kinds.get(str(row["kind"]), str(row["kind"])),
                    str(row["currency_code"])
                    + " "
                    + money_text(int(str(row["amount_minor"])), str(row["currency_code"])),
                    (str(row.get("from_account_name")) + " → " + str(row.get("to_account_name")))
                    if row["kind"] == "transfer"
                    else str(row.get("account_name") or "—"),
                    classification,
                    str(row.get("note") or ""),
                    self.tr("已删除") if row.get("deleted_at_utc") else self.tr("有效"),
                ]
                for column, value in enumerate(values):
                    self.table.setItem(index, column, QTableWidgetItem(value))
                if row["id"] == selected_id:
                    self.table.selectRow(index)
            self.status.setText(
                self.tr("共 {total} 笔 · 第 {page} 页").format(
                    total=page.total, page=self._page + 1
                )
                if page.total
                else self.tr("还没有符合筛选条件的交易。")
            )
            self.previous.setEnabled(self._page > 0)
            self.next.setEnabled((self._page + 1) * page.page_size < page.total)
        except Exception:
            self._rows = ()
            self.table.setRowCount(0)
            self.status.setText(self.tr("读取失败，请检查筛选日期后重试。"))
            self.previous.setEnabled(False)
            self.next.setEnabled(False)
        self._selection_changed()
