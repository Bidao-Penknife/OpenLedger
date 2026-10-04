"""Asynchronous desktop analysis from one immutable cash-flow snapshot."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import cast

from PySide6.QtCore import QDate, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from openledger.application.dto.analytics import AnalyticsFilter, ReportData
from openledger.domain.errors import LedgerError
from openledger.infrastructure.analytics import AnalyticsService
from openledger.infrastructure.ledger import LedgerService
from openledger.presentation.charts import ChartWidget, amount_text, percentage_text
from openledger.presentation.report_export import ReportExporter
from openledger.presentation.tasks import TaskBridge


@dataclass(frozen=True)
class _Request:
    generation: int
    filters: AnalyticsFilter
    ranking_dimension: str
    ranking_metric: str


@dataclass(frozen=True)
class _Choice:
    identifier: str
    name: str
    archived: bool


@dataclass(frozen=True)
class _Loaded:
    generation: int
    report: ReportData
    choices: tuple[tuple[str, tuple[_Choice, ...]], ...]


def _load(
    ledger: LedgerService,
    analytics: AnalyticsService,
    request: _Request,
    cancelled: Callable[[], bool],
) -> _Loaded:
    """Load reference menus and a report in a worker, without touching widgets."""
    choices: list[tuple[str, tuple[_Choice, ...]]] = []
    for entity in ("book", "account", "category", "tag"):
        if cancelled():
            raise LedgerError("BACKGROUND_TASK_CANCELLED")
        rows = ledger.entities(entity, include_archived=True)
        choices.append(
            (
                entity,
                tuple(
                    _Choice(str(row["id"]), str(row["name"]), bool(row["is_archived"]))
                    for row in rows
                ),
            )
        )
    if cancelled():
        raise LedgerError("BACKGROUND_TASK_CANCELLED")
    report = analytics.build_report(
        request.filters,
        ranking_dimension=request.ranking_dimension,
        ranking_metric=request.ranking_metric,
    )
    if cancelled():
        raise LedgerError("BACKGROUND_TASK_CANCELLED")
    return _Loaded(request.generation, report, tuple(choices))


class AnalysisPage(QWidget):
    """Keep charts, summary, scope and export tied to the same displayed report."""

    categoryRequested = Signal(str)

    def __init__(self, ledger: LedgerService, today: date, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ledger = ledger
        self.analytics = AnalyticsService(ledger.database, clock=ledger.clock)
        self.report: ReportData | None = None
        self._theme = "light"
        self._generation = 0
        self._active_generation = 0
        self._pending: _Request | None = None
        self._valid = False
        self._closed = False
        self._reports = TaskBridge(self)
        self._exports = TaskBridge(self)
        self._reports.completed.connect(self._report_completed)
        self._reports.failed.connect(self._report_failed)
        self._reports.busyChanged.connect(self._report_busy_changed)
        self._exports.completed.connect(self._export_completed)
        self._exports.failed.connect(self._export_failed)
        self._exports.busyChanged.connect(self._export_busy_changed)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget(scroll)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(16)
        title = QLabel(self.tr("统计与报告"), content)
        title.setProperty("heading", True)
        layout.addWidget(title)
        caption = QLabel(self.tr("查看收支趋势、分类与消费排行，报告完全在本机生成。"), content)
        caption.setProperty("secondary", True)
        caption.setWordWrap(True)
        layout.addWidget(caption)
        filter_grid = QGridLayout()
        self.start = self._date_editor(today.replace(day=1), "analysisStart", content)
        self.end = self._date_editor(today, "analysisEnd", content)
        self.apply = QPushButton(self.tr("筛选 / 刷新"), content)
        self.apply.setObjectName("refreshAnalysis")
        self.apply.setProperty("primary", True)
        self.apply.clicked.connect(self.refresh)
        filter_grid.addWidget(QLabel(self.tr("开始日期"), content), 0, 0)
        filter_grid.addWidget(self.start, 0, 1)
        filter_grid.addWidget(QLabel(self.tr("结束日期（含当天）"), content), 0, 2)
        filter_grid.addWidget(self.end, 0, 3)
        filter_grid.addWidget(self.apply, 0, 4)
        layout.addLayout(filter_grid)
        references = QGridLayout()
        self.book = self._reference_combo(self.tr("全部账本"), "analysisBook", content)
        self.account = self._reference_combo(self.tr("全部账户"), "analysisAccount", content)
        self.category = self._reference_combo(self.tr("全部分类"), "analysisCategory", content)
        self.tag = self._reference_combo(self.tr("全部标签"), "analysisTag", content)
        self._combos = {
            "book": self.book,
            "account": self.account,
            "category": self.category,
            "tag": self.tag,
        }
        for index, (label, combo) in enumerate(
            (
                (self.tr("账本"), self.book),
                (self.tr("账户"), self.account),
                (self.tr("分类"), self.category),
                (self.tr("标签"), self.tag),
            )
        ):
            references.addWidget(QLabel(label, content), 0, index)
            references.addWidget(combo, 1, index)
        layout.addLayout(references)
        ranks = QHBoxLayout()
        ranks.addWidget(QLabel(self.tr("消费排行"), content))
        self.dimension = QComboBox(content)
        self.dimension.setObjectName("rankingDimension")
        for label, value in (
            (self.tr("按商户"), "merchant"),
            (self.tr("按对象"), "counterparty"),
            (self.tr("按分类"), "category"),
        ):
            self.dimension.addItem(label, value)
        self.ranking_metric = QComboBox(content)
        self.ranking_metric.setObjectName("rankingMetric")
        self.ranking_metric.addItem(self.tr("按金额排序"), "amount")
        self.ranking_metric.addItem(self.tr("按笔数排序"), "count")
        ranks.addWidget(self.dimension)
        ranks.addWidget(self.ranking_metric)
        ranks.addStretch()
        layout.addLayout(ranks)
        self.status = QLabel(self.tr("正在读取统计数据…"), content)
        self.status.setObjectName("analysisStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        metrics = QGridLayout()
        self.values: dict[str, QLabel] = {}
        for index, (key, label) in enumerate(
            (
                ("income", self.tr("收入")),
                ("gross_expense", self.tr("支出原额")),
                ("refund", self.tr("退款")),
                ("net_expense", self.tr("净支出")),
                ("surplus", self.tr("结余")),
                ("savings_rate", self.tr("储蓄率（结余 / 收入）")),
            )
        ):
            card = QFrame(content)
            card.setProperty("card", True)
            inner = QVBoxLayout(card)
            inner.setContentsMargins(16, 12, 16, 12)
            heading = QLabel(label, card)
            heading.setProperty("secondary", True)
            metric_value = QLabel("—", card)
            metric_value.setObjectName("analysis" + key.title().replace("_", ""))
            metric_value.setProperty("metric", True)
            metric_value.setWordWrap(True)
            inner.addWidget(heading)
            inner.addWidget(metric_value)
            self.values[key] = metric_value
            metrics.addWidget(card, index // 3, index % 3)
        layout.addLayout(metrics)
        self.scope = QLabel(content)
        self.scope.setObjectName("analysisScope")
        self.scope.setWordWrap(True)
        self.scope.setProperty("secondary", True)
        layout.addWidget(self.scope)
        self.comparison = QLabel(content)
        self.comparison.setObjectName("analysisComparison")
        self.comparison.setWordWrap(True)
        layout.addWidget(self.comparison)
        self.trend = ChartWidget("trend", content)
        self.trend.setObjectName("analysisTrend")
        self.categories = ChartWidget("categories", content)
        self.categories.setObjectName("analysisCategories")
        self.ranking = ChartWidget("ranking", content)
        self.ranking.setObjectName("analysisRanking")
        self.categories.categoryActivated.connect(self.categoryRequested.emit)
        for chart in (self.trend, self.categories, self.ranking):
            layout.addWidget(chart)
        self.table = QTableWidget(0, 3, content)
        self.table.setObjectName("analysisRankingTable")
        self.table.setHorizontalHeaderLabels(
            [self.tr("消费项"), self.tr("支出原额（元）"), self.tr("笔数")]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.verticalHeader().hide()
        self.table.setMinimumHeight(160)
        layout.addWidget(self.table)
        self.notes = QLabel(content)
        self.notes.setObjectName("analysisNotes")
        self.notes.setWordWrap(True)
        layout.addWidget(self.notes)
        exports = QHBoxLayout()
        self.pdf = QPushButton(self.tr("导出 PDF 报告"), content)
        self.pdf.setObjectName("exportPdfReport")
        self.png = QPushButton(self.tr("导出图片报告"), content)
        self.png.setObjectName("exportPngReport")
        self.cancel_export = QPushButton(self.tr("取消导出"), content)
        self.cancel_export.setObjectName("cancelReportExport")
        self.pdf.clicked.connect(lambda: self.export_report("pdf"))
        self.png.clicked.connect(lambda: self.export_report("png"))
        self.cancel_export.clicked.connect(self._cancel_export)
        for button in (self.pdf, self.png, self.cancel_export):
            exports.addWidget(button)
        exports.addStretch()
        layout.addLayout(exports)
        self.export_status = QLabel(content)
        self.export_status.setObjectName("reportExportStatus")
        self.export_status.setWordWrap(True)
        layout.addWidget(self.export_status)
        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        self.start.dateChanged.connect(self._changed)
        self.end.dateChanged.connect(self._changed)
        for combo in (*self._combos.values(), self.dimension, self.ranking_metric):
            combo.currentIndexChanged.connect(self._changed)
        self.refresh()

    @staticmethod
    def _date_editor(day: date, name: str, parent: QWidget) -> QDateEdit:
        editor = QDateEdit(parent)
        editor.setObjectName(name)
        editor.setCalendarPopup(True)
        editor.setDisplayFormat("yyyy-MM-dd")
        editor.setDate(QDate(day.year, day.month, day.day))
        return editor

    @staticmethod
    def _reference_combo(label: str, name: str, parent: QWidget) -> QComboBox:
        combo = QComboBox(parent)
        combo.setObjectName(name)
        combo.addItem(label, None)
        return combo

    def _changed(self) -> None:
        """An edited filter invalidates exports and any pending older response."""
        if self._closed:
            return
        self._generation += 1
        self._pending = None
        self._valid = False
        if self._reports.busy:
            self._reports.cancel()
        self.status.setText(self.tr("筛选条件已改变，点击「筛选 / 刷新」查看结果。"))
        self._update_export_controls()

    def _filters(self) -> AnalyticsFilter:
        selected: dict[str, tuple[str, ...]] = {}
        for entity, combo in self._combos.items():
            identifier = combo.currentData()
            selected[entity + "_ids"] = (identifier,) if isinstance(identifier, str) else ()
        return AnalyticsFilter(
            cast(date, self.start.date().toPython()),
            cast(date, self.end.date().toPython()),
            **selected,
        )

    def refresh(self) -> None:
        """Queue the latest immutable filter request without running I/O on the GUI thread."""
        if self._closed:
            return
        self._generation += 1
        self._valid = False
        self._pending = _Request(
            self._generation,
            self._filters(),
            str(self.dimension.currentData()),
            str(self.ranking_metric.currentData()),
        )
        self.status.setText(self.tr("正在读取统计数据…"))
        if self._reports.busy:
            self._reports.cancel()
        else:
            self._start_pending()
        self._update_export_controls()

    def invalidate(self) -> None:
        """Refresh the retained filter after a committed ledger mutation."""
        self.refresh()

    def _start_pending(self) -> None:
        if self._closed or self._pending is None:
            return
        request = self._pending
        self._pending = None
        self._active_generation = request.generation
        ledger, analytics = self.ledger, self.analytics
        self._reports.start(lambda cancel: _load(ledger, analytics, request, cancel))

    def _report_busy_changed(self, busy: bool) -> None:
        if not busy and self._pending is not None:
            self._start_pending()
        self._update_export_controls()

    def _report_completed(self, result: object) -> None:
        if not isinstance(result, _Loaded):
            self._report_failed("BACKGROUND_TASK_INVALID_RESULT")
            return
        if self._closed or result.generation != self._generation:
            return
        for entity, choices in result.choices:
            combo = self._combos[entity]
            previous = combo.currentData()
            all_label = combo.itemText(0)
            combo.blockSignals(True)
            try:
                combo.clear()
                combo.addItem(all_label, None)
                for choice in choices:
                    label = choice.name + (self.tr("（已归档）") if choice.archived else "")
                    combo.addItem(label, choice.identifier)
                combo.setCurrentIndex(max(0, combo.findData(previous)))
            finally:
                combo.blockSignals(False)
        self.report = result.report
        self._valid = True
        report = result.report
        totals = report.totals
        for key, amount in (
            ("income", totals.income_minor),
            ("gross_expense", totals.gross_expense_minor),
            ("refund", totals.refund_minor),
            ("net_expense", totals.net_expense_minor),
            ("surplus", totals.surplus_minor),
        ):
            self.values[key].setText(amount_text(amount) + self.tr(" 元"))
        self.values["savings_rate"].setText(percentage_text(totals.savings_rate))
        count = totals.income_count + totals.expense_count + totals.refund_count
        self.status.setText(
            self.tr("所选范围暂无收支记录，可调整日期和筛选条件。")
            if count == 0
            else self.tr("共 {count} 笔收支记录 · 数据版本 {revision}").format(
                count=count, revision=report.data_revision
            )
        )
        self.scope.setText("\n".join(report.scope_labels))
        if report.comparison_totals is None:
            self.comparison.setText(self.tr("前期比较不可用：无法构造完整的前一段等长日历日期。"))
        else:
            previous_totals = report.comparison_totals
            self.comparison.setText(
                self.tr(
                    "前一段等长日期：{start} 至 {end}（含首尾）\n"
                    "前期收入 {income} 元 · 前期净支出 {expense} 元 · 前期结余 {surplus} 元"
                ).format(
                    start=report.comparison_start,
                    end=report.comparison_end,
                    income=amount_text(previous_totals.income_minor),
                    expense=amount_text(previous_totals.net_expense_minor),
                    surplus=amount_text(previous_totals.surplus_minor),
                )
            )
        for chart in (self.trend, self.categories, self.ranking):
            chart.set_report(report, self._theme)
        self.table.setRowCount(len(report.ranking))
        for index, rank in enumerate(report.ranking):
            for column, value in enumerate(
                (rank.label, amount_text(rank.amount_minor), str(rank.count))
            ):
                self.table.setItem(index, column, QTableWidgetItem(value))
        self.notes.setText("\n".join("• " + note for note in report.notes))

    def _report_failed(self, code: str) -> None:
        if self._closed or self._active_generation != self._generation:
            return
        self._valid = False
        message = (
            self.tr("筛选条件无效：开始日期需不晚于结束日期，范围最多 120 个月。")
            if code == "INVALID_FILTER"
            else self.tr("读取统计失败，请刷新重试。")
        )
        self.status.setText(message + " " + code)

    def set_theme(self, theme: str) -> None:
        """Repaint the displayed snapshot and use this theme for future exports."""
        if theme not in {"light", "dark"}:
            raise ValueError("Unsupported analysis theme")
        self._theme = theme
        if self.report is not None:
            for chart in (self.trend, self.categories, self.ranking):
                chart.set_report(self.report, theme)

    def _update_export_controls(self) -> None:
        enabled = (
            self._valid and not self._reports.busy and not self._exports.busy and not self._closed
        )
        self.pdf.setEnabled(enabled)
        self.png.setEnabled(enabled)
        self.cancel_export.setEnabled(self._exports.busy and not self._closed)

    def export_report(self, format_name: str) -> None:
        """Choose a native destination, then export the currently displayed DTO."""
        if not self.pdf.isEnabled() or self.report is None or format_name not in {"pdf", "png"}:
            return
        previous = self.report
        filename = (
            f"OpenLedger-report-{previous.filters.start_on}-{previous.filters.end_on}.{format_name}"
        )
        label = self.tr("PDF 报告 (*.pdf)") if format_name == "pdf" else self.tr("图片报告 (*.png)")
        chosen, _ = QFileDialog.getSaveFileName(self, self.tr("导出财务报告"), filename, label)
        if not chosen:
            return
        if self.report is not previous or not self._valid:
            self.export_status.setText(self.tr("报告已发生变化，请重新选择导出。"))
            return
        path = Path(chosen)
        if not path.suffix:
            path = path.with_suffix("." + format_name)
        self._export_to(path, format_name)

    def _export_to(self, path: Path, format_name: str) -> None:
        if self.report is None or not self._valid or self._exports.busy or self._closed:
            return
        report, theme = self.report, self._theme
        exporter = ReportExporter()
        self.export_status.setText(self.tr("正在生成报告…"))
        self._exports.start(
            lambda cancel: exporter.export(report, path, format_name, theme, cancel_token=cancel)
        )
        self._update_export_controls()

    def _export_completed(self, result: object) -> None:
        if not isinstance(result, Path):
            self._export_failed("BACKGROUND_TASK_INVALID_RESULT")
            return
        self.export_status.setText(self.tr("报告已导出：") + str(result))

    def _export_failed(self, code: str) -> None:
        message = (
            self.tr("已取消报告导出，原有文件已保留。")
            if code == "REPORT_EXPORT_CANCELLED"
            else self.tr("报告导出失败，原有文件已保留。")
        )
        self.export_status.setText(message + " " + code)

    def _export_busy_changed(self, busy: bool) -> None:
        self._update_export_controls()

    def _cancel_export(self) -> None:
        self._exports.cancel()
        self.export_status.setText(self.tr("正在取消报告导出…"))

    def close_workers(self) -> None:
        """Cancel queued work and wait for workers before the owning window closes."""
        if self._closed:
            return
        self._closed = True
        self._pending = None
        self._reports.close()
        self._exports.close()
