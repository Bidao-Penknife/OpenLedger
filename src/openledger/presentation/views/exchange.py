"""Reviewable file exchange UI with bounded background IO and versioned commands."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from openledger.application.dto.exchange import (
    ExchangeMapping,
    ExportResult,
    FileTable,
    ImportPreview,
)
from openledger.application.dto.queries import TransactionFilter
from openledger.domain.errors import LedgerError
from openledger.infrastructure.exchange import ExchangeService, automatic_columns, read_table
from openledger.infrastructure.ledger import LedgerService
from openledger.presentation.tasks import TaskBridge
from openledger.presentation.views.transaction_form import money_text

_MAP_FIELDS = (
    ("kind", "类型"),
    ("amount", "金额（元）"),
    ("amount_minor", "金额（整数分）"),
    ("occurred_on", "日期"),
    ("account_name", "账户名称"),
    ("category_name", "分类名称"),
    ("book_name", "账本名称"),
    ("note", "备注"),
    ("merchant", "商户"),
    ("external_source", "外部来源"),
    ("external_transaction_id", "外部交易号"),
    ("account_id", "账户 ID"),
    ("book_id", "账本 ID"),
    ("category_id", "分类 ID"),
    ("from_account_id", "转出账户 ID"),
    ("from_account_name", "转出账户名称"),
    ("to_account_id", "转入账户 ID"),
    ("to_account_name", "转入账户名称"),
    ("original_transaction_id", "原支出交易 ID"),
)


class ExchangePage(QScrollArea):
    """The displayed preview is the only source of a confirmed import payload."""

    commandRequested = Signal(str, object)

    def __init__(self, ledger: LedgerService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ledger = ledger
        self.service = ExchangeService(ledger)
        self.tasks = TaskBridge(self)
        self.tasks.completed.connect(self._completed)
        self.tasks.failed.connect(self._failed)
        self.tasks.busyChanged.connect(self._busy)
        self._generation = 0
        self._preparing_commit = False
        self.source_table: FileTable | None = None
        self.preview: ImportPreview | None = None
        self._batch_rows: tuple[dict[str, object], ...] = ()
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget(self)
        self.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 28, 28, 28)
        title = QLabel(self.tr("导入与导出"), content)
        title.setProperty("heading", True)
        layout.addWidget(title)
        hint = QLabel(
            self.tr("先映射列并预览，再勾选确认。导入整批提交；含错误的行不能勾选。"), content
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        choose = QHBoxLayout()
        self.choose = QPushButton(self.tr("选择 CSV / Excel"), content)
        self.choose.setObjectName("chooseImportFile")
        self.choose.clicked.connect(self._choose_file)
        self.file_name = QLabel(self.tr("尚未选择文件"), content)
        self.file_name.setTextFormat(Qt.TextFormat.PlainText)
        self.file_name.setWordWrap(True)
        choose.addWidget(self.choose)
        choose.addWidget(self.file_name, 1)
        layout.addLayout(choose)
        self.mapping_tabs = QTabWidget(content)
        mapping_page = QWidget(self.mapping_tabs)
        form = QFormLayout(mapping_page)
        self.encoding = QComboBox(mapping_page)
        for label, value in [("UTF-8（推荐）", "utf-8-sig"), ("GB18030", "gb18030")]:
            self.encoding.addItem(label, value)
        self.sheet = QComboBox(mapping_page)
        self.sheet.addItem(self.tr("默认工作表"), None)
        form.addRow(self.tr("CSV 编码"), self.encoding)
        form.addRow(self.tr("Excel 工作表"), self.sheet)
        self.columns: dict[str, QComboBox] = {}
        for key, label in _MAP_FIELDS:
            combo = QComboBox(mapping_page)
            combo.setObjectName("map_" + key)
            combo.currentIndexChanged.connect(self.invalidate)
            self.columns[key] = combo
            form.addRow(self.tr(label), combo)
        mapping_scroll = QScrollArea(self.mapping_tabs)
        mapping_scroll.setWidgetResizable(True)
        mapping_scroll.setWidget(mapping_page)
        self.mapping_tabs.addTab(mapping_scroll, self.tr("列映射"))
        self.mapping_tabs.setMaximumHeight(360)
        target_page = QWidget(self.mapping_tabs)
        defaults = QFormLayout(target_page)
        self.targets: dict[str, QComboBox] = {}
        for key, label in [
            ("book_id", "默认账本"),
            ("account_id", "默认账户"),
            ("expense_category_id", "默认支出分类"),
            ("income_category_id", "默认收入分类"),
            ("from_account_id", "默认转出账户"),
            ("to_account_id", "默认转入账户"),
        ]:
            combo = QComboBox(target_page)
            combo.setObjectName("import_" + key)
            combo.currentIndexChanged.connect(self.invalidate)
            self.targets[key] = combo
            defaults.addRow(self.tr(label), combo)
        self.kind = QComboBox(target_page)
        for label, value in [
            ("支出", "expense"),
            ("收入", "income"),
            ("转账", "transfer"),
            ("退款", "expense_refund"),
        ]:
            self.kind.addItem(self.tr(label), value)
        self.kind.currentIndexChanged.connect(self.invalidate)
        defaults.addRow(self.tr("未提供类型时"), self.kind)
        self.time_zone = QLineEdit(ledger.time_zone, target_page)
        self.time_zone.textChanged.connect(self.invalidate)
        defaults.addRow(self.tr("默认时区（IANA）"), self.time_zone)
        defaults.addRow(
            QLabel(
                self.tr("已有 ID 优先，其次同名资料，最后使用明确选定的默认值；未知标签需先创建。"),
                target_page,
            )
        )
        self.mapping_tabs.addTab(target_page, self.tr("目标资料"))
        layout.addWidget(self.mapping_tabs)
        toolbar = QHBoxLayout()
        self.preview_button = QPushButton(self.tr("重新读取并生成预览"), content)
        self.preview_button.setObjectName("previewImport")
        self.preview_button.clicked.connect(self.build_preview)
        self.commit_button = QPushButton(self.tr("确认导入勾选行"), content)
        self.commit_button.setObjectName("commitImport")
        self.commit_button.setProperty("primary", True)
        self.commit_button.clicked.connect(self.confirm_import)
        self.errors_button = QPushButton(self.tr("导出错误明细"), content)
        self.errors_button.clicked.connect(self._export_errors)
        self.cancel_button = QPushButton(self.tr("取消文件任务"), content)
        self.cancel_button.clicked.connect(self.cancel_task)
        for button in (
            self.preview_button,
            self.commit_button,
            self.errors_button,
            self.cancel_button,
        ):
            toolbar.addWidget(button)
        layout.addLayout(toolbar)
        self.status = QLabel(content)
        self.status.setObjectName("exchangeStatus")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.rows = QTableWidget(0, 9, content)
        self.rows.setObjectName("importPreviewTable")
        self.rows.setHorizontalHeaderLabels(
            [
                self.tr(value)
                for value in (
                    "勾选",
                    "源行",
                    "日期",
                    "类型",
                    "金额（元）",
                    "资金账户",
                    "账本 / 分类",
                    "备注",
                    "校验结果",
                )
            ]
        )
        self.rows.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.rows.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.rows.horizontalHeader().setSectionResizeMode(8, QHeaderView.ResizeMode.Stretch)
        self.rows.setMinimumHeight(240)
        layout.addWidget(self.rows)
        export_bar = QHBoxLayout()
        for format in ("csv", "xlsx"):
            button = QPushButton(
                self.tr("导出全部有效交易 · {format}").format(format=format.upper()), content
            )
            button.setObjectName("export_" + format)
            button.clicked.connect(
                lambda checked=False, value=format: self._export_transactions(value)
            )
            export_bar.addWidget(button)
        layout.addLayout(export_bar)
        export_hint = QLabel(
            self.tr(
                "CSV / Excel 包含期初与校准供查看，普通导入支持收支、转账和退款。"
                "完整恢复使用 .olbackup 备份。单文件最多 20 MiB / 10,000 行。"
            ),
            content,
        )
        export_hint.setWordWrap(True)
        layout.addWidget(export_hint)
        history_title = QLabel(self.tr("导入批次历史"), content)
        history_title.setProperty("cardTitle", True)
        layout.addWidget(history_title)
        self.batches = QTableWidget(0, 4, content)
        self.batches.setObjectName("importBatchTable")
        self.batches.setHorizontalHeaderLabels(
            [self.tr(value) for value in ("文件", "接受行数", "状态", "创建时间（UTC）")]
        )
        self.batches.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.batches.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.batches.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.batches.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.batches.setMinimumHeight(160)
        layout.addWidget(self.batches)
        revert = QPushButton(self.tr("撤销选中批次"), content)
        revert.setObjectName("revertImportBatch")
        revert.clicked.connect(self.revert_batch)
        layout.addWidget(revert)
        self.encoding.currentIndexChanged.connect(self.invalidate)
        self.sheet.currentIndexChanged.connect(self._sheet_changed)
        self.refresh()
        self._busy(False)

    def invalidate(self) -> None:
        """Discard confirmation whenever any mapping or destination changes."""
        self._generation += 1
        self._preparing_commit = False
        self.tasks.cancel()
        self.preview = None
        self.rows.setRowCount(0)
        self.commit_button.setEnabled(False)
        self.errors_button.setEnabled(False)

    def _sheet_changed(self) -> None:
        path = self.source_table.path if self.source_table else None
        self.invalidate()
        if path and not self.tasks.busy:
            self.load_file(path)

    def cancel_task(self) -> None:
        """Invalidate queued confirmation before submitting any financial command."""
        self.tasks.cancel()
        if self._preparing_commit:
            self._generation += 1
            self._preparing_commit = False
            self.status.setText(self.tr("已取消确认准备，尚未提交资金事务。"))

    def _mapping(self) -> ExchangeMapping:
        columns = dict(automatic_columns(self.source_table.headers)) if self.source_table else {}
        for key, combo in self.columns.items():
            columns.pop(key, None)
            if combo.currentData():
                columns[key] = str(combo.currentData())
        return ExchangeMapping(
            columns=tuple(sorted(columns.items())),
            **{key: cast(str | None, combo.currentData()) for key, combo in self.targets.items()},
            default_kind=str(self.kind.currentData()),
            time_zone=self.time_zone.text(),
            encoding=str(self.encoding.currentData()),
            sheet=cast(str | None, self.sheet.currentData()),
        )

    def _choose_file(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, self.tr("选择账单"), "", "账单 (*.csv *.xlsx)")
        if name:
            self.load_file(Path(name))

    def load_file(self, path: Path) -> None:
        if self.tasks.busy:
            return
        self.invalidate()
        generation = self._generation
        mapping = replace(self._mapping(), columns=())
        self.status.setText(self.tr("正在读取文件…"))
        self.tasks.start(
            lambda cancel: (generation, "table", read_table(path, mapping, cancel=cancel))
        )

    def build_preview(self) -> None:
        if self.tasks.busy or self.source_table is None:
            return
        path = self.source_table.path
        mapping = self._mapping()
        self.invalidate()
        generation = self._generation
        self.status.setText(self.tr("正在校验所有行与重复身份…"))
        self.tasks.start(
            lambda cancel: (
                generation,
                "preview",
                self.service.preview(
                    read_table(path, mapping, cancel=cancel), mapping, cancel=cancel
                ),
            )
        )

    def _completed(self, result: object) -> None:
        if not isinstance(result, tuple) or len(result) != 3:
            return
        generation, kind, value = result
        if generation != self._generation:
            return
        if kind == "table" and isinstance(value, FileTable):
            self.source_table = value
            self.file_name.setText(str(value.path))
            automatic = dict(automatic_columns(value.headers))
            for key, combo in self.columns.items():
                combo.blockSignals(True)
                combo.clear()
                combo.addItem(self.tr("未提供 / 使用默认值"), None)
                for header in value.headers:
                    combo.addItem(header, header)
                combo.setCurrentIndex(max(0, combo.findData(automatic.get(key))))
                combo.blockSignals(False)
            previous = self.sheet.currentData()
            self.sheet.blockSignals(True)
            self.sheet.clear()
            self.sheet.addItem(self.tr("默认工作表"), None)
            for sheet in value.sheets:
                self.sheet.addItem(sheet, sheet)
            self.sheet.setCurrentIndex(max(0, self.sheet.findData(previous)))
            self.sheet.blockSignals(False)
            self.status.setText(
                self.tr("读取 {count} 行。请核对列映射和目标资料，再生成预览。").format(
                    count=len(value.rows)
                )
            )
        elif kind == "preview" and isinstance(value, ImportPreview):
            self.preview = value
            self.rows.setRowCount(len(value.rows))
            valid = 0
            for index, row in enumerate(value.rows):
                checkbox = QTableWidgetItem()
                checkbox.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                eligible = not row.issues and row.fields is not None
                if not eligible:
                    checkbox.setFlags(Qt.ItemFlag.NoItemFlags)
                checkbox.setCheckState(
                    Qt.CheckState.Checked
                    if eligible and not row.possible_duplicates
                    else Qt.CheckState.Unchecked
                )
                self.rows.setItem(index, 0, checkbox)
                fields = row.fields or {}
                issue_text = (
                    "；".join(self._issue_text(code) for code in row.issues)
                    if row.issues
                    else self.tr("可能重复，请手工核对后勾选")
                    if row.possible_duplicates
                    else self.tr("基础校验通过")
                )
                kind_label = {
                    "income": self.tr("收入"),
                    "expense": self.tr("支出"),
                    "transfer": self.tr("转账"),
                    "expense_refund": self.tr("退款"),
                }.get(str(fields.get("kind")), "—")
                account_label = self._reference_names.get(str(fields.get("account_id")), "—")
                if fields.get("kind") == "transfer":
                    account_label = (
                        self._reference_names.get(str(fields.get("from_account_id")), "—")
                        + " → "
                        + self._reference_names.get(str(fields.get("to_account_id")), "—")
                    )
                classification = " / ".join(
                    self._reference_names.get(str(fields[key]), "—")
                    for key in ("book_id", "category_id")
                    if key in fields
                )
                if fields.get("kind") == "expense_refund":
                    classification = self.tr("继承原支出")
                values = [
                    str(row.source_row_number),
                    str(fields.get("occurred_on", "")),
                    kind_label,
                    money_text(cast(int, fields["amount_minor"])) if fields else "—",
                    account_label,
                    classification or "—",
                    str(fields.get("note") or ""),
                    issue_text,
                ]
                for column, text in enumerate(values, 1):
                    self.rows.setItem(index, column, QTableWidgetItem(text))
                valid += int(eligible)
            self.status.setText(
                self.tr(
                    "预览 {total} 行，{valid} 行通过基础校验。疑似重复默认不勾选；"
                    "资金约束在整批提交时再次校验，更改映射后需重新预览。"
                ).format(total=len(value.rows), valid=valid)
            )
        elif kind == "commit" and isinstance(value, dict):
            self._preparing_commit = False
            self.commandRequested.emit("import.commit.v1", value)
        elif kind == "export" and isinstance(value, ExportResult):
            self.status.setText(
                self.tr("已导出 {count} 行：{path}（数据版本 {revision}）").format(
                    count=value.row_count, path=value.path, revision=value.data_revision
                )
            )
        elif kind == "errors" and isinstance(value, Path):
            self.status.setText(self.tr("错误明细已导出：{path}").format(path=value))

    def confirm_import(self) -> None:
        preview = self.preview
        if preview is None or self.tasks.busy:
            return
        selected = tuple(
            row.source_row_number
            for index, row in enumerate(preview.rows)
            if (item := self.rows.item(index, 0)) is not None
            and item.checkState() == Qt.CheckState.Checked
        )
        if not selected:
            self.status.setText(self.tr("请勾选至少一行有效记录。"))
            return
        if (
            QMessageBox.question(
                self,
                self.tr("确认导入"),
                self.tr("确认整批导入 {count} 行？账户余额会随之变化。").format(
                    count=len(selected)
                ),
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        generation = self._generation
        self._preparing_commit = True
        self.tasks.start(lambda cancel: self._payload_result(cancel, generation, preview, selected))

    def _payload_result(
        self,
        cancel: Callable[[], bool],
        generation: int,
        preview: ImportPreview,
        selected: tuple[int, ...],
    ) -> object:
        if cancel():
            raise LedgerError("EXCHANGE_CANCELLED")
        result = self.service.commit_payload(preview, selected)
        if cancel():
            raise LedgerError("EXCHANGE_CANCELLED")
        return generation, "commit", result

    def _failed(self, code: str) -> None:
        self._preparing_commit = False
        self.status.setText(self._issue_text(code))

    def _issue_text(self, code: str) -> str:
        messages = {
            "INVALID_AMOUNT": self.tr("金额需为大于 0 的数值"),
            "AMOUNT_PRECISION": self.tr("金额最多两位小数，不会自动舍入"),
            "INVALID_DATE": self.tr("日期格式应为 YYYY-MM-DD，请核对日期精度"),
            "FUTURE_DATE": self.tr("日期不能晚于当前记账日期"),
            "DUPLICATE_IMPORT": self.tr("此交易身份已存在，不能再次导入"),
            "IMPORT_SOURCE_ID_CONFLICT": self.tr("相同源交易 ID 的内容或来源身份矛盾"),
            "IMPORT_REFERENCE_REQUIRED": self.tr("账户、账本或分类不存在，请核对映射与资料"),
            "IMPORT_REFERENCE_CONFLICT": self.tr("资料 ID 与名称不一致，请核对映射"),
            "ENTITY_ARCHIVED": self.tr("引用的资料已归档，请先恢复或调整映射"),
            "CATEGORY_KIND_MISMATCH": self.tr("分类的收入／支出类型与交易不一致"),
            "IMPORT_EXTERNAL_ID_INVALID": self.tr("外部交易号需要同时提供来源名称"),
            "BEFORE_ACCOUNT_START": self.tr("交易日期早于账户起算日期"),
            "ORIGINAL_EXPENSE_REQUIRED": self.tr("退款缺少可用的原支出记录"),
            "IMPORT_DEPENDENCY_REQUIRED": self.tr("请同时勾选退款对应的原支出"),
            "UNSUPPORTED_IMPORT_KIND": self.tr("此类型不能普通导入；期初与校准请使用备份恢复"),
            "IMPORT_FORMULA_FORBIDDEN": self.tr("Excel 公式不能作为交易导入，请先转为准确文本"),
            "IMPORT_FILE_CHANGED": self.tr("源文件已变化，请重新生成预览"),
            "IMPORT_PREVIEW_CHANGED": self.tr("预览内容已变化，请重新生成预览"),
            "EXCHANGE_CANCELLED": self.tr("文件任务已取消，已有目标文件保留"),
            "EXPORT_IO_ERROR": self.tr("导出未完成，请检查目标文件是否被占用或目录权限"),
        }
        return messages.get(code, self.tr("操作未完成，请检查映射或输入后重试")) + f" [{code}]"

    def _busy(self, busy: bool) -> None:
        self.choose.setEnabled(not busy)
        self.mapping_tabs.setEnabled(not busy)
        self.preview_button.setEnabled(not busy and self.source_table is not None)
        self.commit_button.setEnabled(not busy and self.preview is not None)
        self.errors_button.setEnabled(not busy and self.preview is not None)
        self.cancel_button.setEnabled(busy)

    def command_finished(self, success: bool) -> None:
        if success:
            self.invalidate()
            self.status.setText(self.tr("批次操作已保存。"))
        else:
            self.status.setText(self.tr("批次未保存，预览已保留。请核对资金约束后重试。"))

    def refresh(self) -> None:
        self._reference_names = {
            str(row["id"]): str(row["name"])
            for entity in ("book", "account", "category")
            for row in self.ledger.entities(entity, include_archived=True)
        }
        for key, combo in self.targets.items():
            entity = "book" if key == "book_id" else "category" if "category" in key else "account"
            previous = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(self.tr("请明确选择"), None)
            for row in self.ledger.entities(entity):
                if entity == "category" and row["transaction_kind"] != (
                    "income" if key.startswith("income") else "expense"
                ):
                    continue
                combo.addItem(str(row["name"]), row["id"])
            combo.setCurrentIndex(max(0, combo.findData(previous)))
            combo.blockSignals(False)
        self._batch_rows = self.ledger.import_batches()
        self.batches.setRowCount(len(self._batch_rows))
        for index, row in enumerate(self._batch_rows):
            for column, key in enumerate(
                ("source_file_name", "accepted_row_count", "status", "created_at_utc")
            ):
                value = row[key]
                if key == "status":
                    value = self.tr("已提交") if value == "committed" else self.tr("已撤销")
                self.batches.setItem(index, column, QTableWidgetItem(str(value)))

    def revert_batch(self) -> None:
        index = self.batches.currentRow()
        if not 0 <= index < len(self._batch_rows) or self.tasks.busy:
            return
        row = self._batch_rows[index]
        if row["status"] != "committed":
            return
        if (
            QMessageBox.question(
                self,
                self.tr("撤销批次"),
                self.tr("确认撤销整个批次？成员已被修改、删除或关联批次外退款时会拒绝撤销。"),
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.commandRequested.emit(
                "import.revert.v1", {"id": row["id"], "expected_version": row["version"]}
            )

    def _export_transactions(self, format: str) -> None:
        if self.tasks.busy:
            return
        name, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("导出交易"),
            "OpenLedger." + format,
            format.upper() + " (*." + format + ")",
        )
        if name:
            generation = self._generation
            self.tasks.start(
                lambda cancel: (
                    generation,
                    "export",
                    self.service.export_transactions(
                        Path(name), format, TransactionFilter(), cancel=cancel
                    ),
                )
            )

    def _export_errors(self) -> None:
        preview = self.preview
        if preview is None or self.tasks.busy:
            return
        name, _ = QFileDialog.getSaveFileName(
            self, self.tr("导出错误明细"), "OpenLedger-errors.csv", "CSV (*.csv)"
        )
        if name:
            generation = self._generation
            self.tasks.start(
                lambda cancel: (
                    generation,
                    "errors",
                    self.service.export_errors(preview, Path(name), cancel=cancel),
                )
            )

    def close_workers(self) -> None:
        self.tasks.close()
