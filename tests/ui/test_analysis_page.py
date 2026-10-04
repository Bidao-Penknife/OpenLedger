"""Exercise asynchronous analytics and exports with disposable financial records."""

from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from threading import Event, get_ident
from time import monotonic
from uuid import uuid4

import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QFileDialog
from pytestqt.qtbot import QtBot

from openledger.application.dto.analytics import AnalyticsFilter, ReportData
from openledger.application.dto.ledger import TransactionFields
from openledger.domain.errors import LedgerError
from openledger.infrastructure.analytics import AnalyticsService
from openledger.infrastructure.ledger import LedgerService
from openledger.presentation.report_export import ReportExporter
from openledger.presentation.views.analysis import AnalysisPage

pytestmark = pytest.mark.ui
TODAY = date(2026, 10, 2)


def uid() -> str:
    return str(uuid4())


def fields(
    ledger: LedgerService, *, amount: int = 2500, merchant: str = "咖啡店"
) -> TransactionFields:
    return TransactionFields(
        "expense",
        amount,
        str(next(row["id"] for row in ledger.entities("account") if row["account_type"] == "cash")),
        str(ledger.entities("book")[0]["id"]),
        str(
            next(
                row["id"]
                for row in ledger.entities("category")
                if row["transaction_kind"] == "expense"
            )
        ),
        TODAY,
        merchant=merchant,
    )


def record(ledger: LedgerService, *, amount: int = 2500, merchant: str = "咖啡店") -> None:
    ledger.record(
        fields(ledger, amount=amount, merchant=merchant), request_id=uid(), transaction_id=uid()
    )


def finish(qtbot: QtBot, page: AnalysisPage) -> None:
    qtbot.waitUntil(lambda: not page._reports.busy and page._pending is None, timeout=5000)


def cell(page: AnalysisPage, row: int, column: int) -> str:
    value = page.table.item(row, column)
    assert value is not None
    return value.text()


@pytest.fixture
def page(qtbot: QtBot, ledger: LedgerService) -> Iterator[AnalysisPage]:
    widget = AnalysisPage(ledger, TODAY)
    qtbot.addWidget(widget)
    widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    widget.resize(900, 720)
    widget.show()
    finish(qtbot, widget)
    yield widget
    widget.close_workers()
    widget.close()


def test_empty_report_has_zero_amounts_unavailable_rate_and_same_chart_dto(
    page: AnalysisPage,
) -> None:
    assert page.report is not None
    assert "暂无收支记录" in page.status.text()
    assert page.values["income"].text() == page.values["net_expense"].text() == "0.00 元"
    assert "分母为零" in page.values["savings_rate"].text()
    assert page.table.rowCount() == 0
    assert page.trend._report is page.categories._report is page.ranking._report is page.report
    assert "2026-09-29" in page.comparison.text() and "2026-09-30" in page.comparison.text()
    assert "含首尾" in page.scope.text()
    assert page.pdf.isEnabled() and page.png.isEnabled() and not page.cancel_export.isEnabled()


def test_after_write_invalidation_refreshes_metrics_charts_and_ranking(
    qtbot: QtBot, page: AnalysisPage, ledger: LedgerService
) -> None:
    previous = page.report
    record(ledger)
    page.invalidate()
    assert not page.pdf.isEnabled()
    finish(qtbot, page)
    assert page.report is not None and page.report is not previous
    assert page.report.totals.expense_count == 1
    assert page.values["gross_expense"].text() == "25.00 元"
    assert page.values["net_expense"].text() == "25.00 元"
    assert page.values["surplus"].text() == "-25.00 元"
    assert cell(page, 0, 0) == "咖啡店"
    assert cell(page, 0, 1) == "25.00" and cell(page, 0, 2) == "1"
    assert page.trend._report is page.categories._report is page.ranking._report is page.report


def test_dirty_filters_disable_export_until_explicit_refresh(
    qtbot: QtBot, page: AnalysisPage, ledger: LedgerService
) -> None:
    record(ledger)
    page.refresh()
    finish(qtbot, page)
    before = page.report
    bank = next(row["id"] for row in ledger.entities("account") if row["account_type"] == "bank")
    page.account.setCurrentIndex(page.account.findData(bank))
    assert "筛选条件已改变" in page.status.text()
    assert page.report is before and not page.pdf.isEnabled()
    page.refresh()
    finish(qtbot, page)
    assert page.report is not None and page.report.filters.account_ids == (bank,)
    assert page.report.totals.expense_count == 0
    assert "测试银行" in page.scope.text() and page.pdf.isEnabled()


def test_archived_references_remain_selectable(
    qtbot: QtBot, page: AnalysisPage, ledger: LedgerService
) -> None:
    category = fields(ledger).category_id
    record(ledger)
    ledger.execute(
        uid(), "category.archive.v1", {"id": category, "expected_version": 1, "archived": True}
    )
    page.invalidate()
    finish(qtbot, page)
    index = page.category.findData(category)
    assert index >= 0 and "已归档" in page.category.itemText(index)
    page.category.setCurrentIndex(index)
    page.refresh()
    finish(qtbot, page)
    assert page.report is not None and page.report.totals.expense_count == 1
    assert any("已归档" in label for label in page.report.scope_labels)


def test_counts_ranking_and_category_drilldown_signal(
    qtbot: QtBot, page: AnalysisPage, ledger: LedgerService
) -> None:
    record(ledger, amount=1000, merchant="A店")
    record(ledger, amount=1000, merchant="A店")
    record(ledger, amount=3000, merchant="B店")
    page.ranking_metric.setCurrentIndex(page.ranking_metric.findData("count"))
    page.refresh()
    finish(qtbot, page)
    assert page.report is not None and page.report.ranking_metric == "count"
    assert page.report.ranking[0].label == cell(page, 0, 0) == "A店"
    assert cell(page, 0, 2) == "2"
    identifier = page.report.categories[0].category_id
    with qtbot.waitSignal(page.categoryRequested, timeout=1000) as emitted:
        page.categories.categoryActivated.emit(identifier)
    assert emitted.args == [identifier]


def test_error_retains_previous_dto_and_recovers_on_refresh(
    qtbot: QtBot, page: AnalysisPage, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous = page.report
    original = page.analytics.build_report

    def fail(filters: AnalyticsFilter, **options: object) -> ReportData:
        raise LedgerError("DATABASE_BUSY")

    monkeypatch.setattr(page.analytics, "build_report", fail)
    page.refresh()
    finish(qtbot, page)
    assert "读取统计失败" in page.status.text() and "DATABASE_BUSY" in page.status.text()
    assert page.report is previous and not page.pdf.isEnabled()
    monkeypatch.setattr(page.analytics, "build_report", original)
    page.refresh()
    finish(qtbot, page)
    assert page.pdf.isEnabled() and "DATABASE_BUSY" not in page.status.text()


def test_invalid_calendar_range_is_visible_without_overwriting_report(
    qtbot: QtBot, page: AnalysisPage
) -> None:
    previous = page.report
    page.start.setDate(QDate(2026, 10, 2))
    page.end.setDate(QDate(2026, 10, 1))
    page.refresh()
    finish(qtbot, page)
    assert "开始日期需不晚于结束日期" in page.status.text()
    assert page.report is previous and not page.pdf.isEnabled()


def test_latest_generation_wins_when_an_older_query_finishes(
    qtbot: QtBot, page: AnalysisPage, ledger: LedgerService, monkeypatch: pytest.MonkeyPatch
) -> None:
    record(ledger)
    original = page.analytics.build_report
    entered, release = Event(), Event()
    calls: list[AnalyticsFilter] = []

    def delayed(filters: AnalyticsFilter, **options: object) -> ReportData:
        calls.append(filters)
        if len(calls) == 1:
            entered.set()
            assert release.wait(3)
        return original(filters)

    monkeypatch.setattr(page.analytics, "build_report", delayed)
    page.refresh()
    qtbot.waitUntil(entered.is_set, timeout=3000)
    try:
        page.end.setDate(QDate(2026, 10, 1))
        page.refresh()
    finally:
        release.set()
    finish(qtbot, page)
    assert len(calls) == 2 and calls[0].end_on == TODAY
    assert page.report is not None and page.report.filters.end_on == date(2026, 10, 1)
    assert page.report.totals.expense_count == 0
    assert page.values["gross_expense"].text() == "0.00 元"


def test_reference_and_analytic_io_execute_off_the_gui_thread(
    qtbot: QtBot, ledger: LedgerService, monkeypatch: pytest.MonkeyPatch
) -> None:
    gui_thread = get_ident()
    observed: list[int] = []
    original_entities = ledger.entities
    original_report = AnalyticsService.build_report

    def entities(entity: str, *, include_archived: bool = False) -> tuple[dict[str, object], ...]:
        observed.append(get_ident())
        return original_entities(entity, include_archived=include_archived)

    def report(
        service: AnalyticsService, filters: AnalyticsFilter, **options: object
    ) -> ReportData:
        observed.append(get_ident())
        return original_report(service, filters)

    monkeypatch.setattr(ledger, "entities", entities)
    monkeypatch.setattr(AnalyticsService, "build_report", report)
    widget = AnalysisPage(ledger, TODAY)
    qtbot.addWidget(widget)
    try:
        finish(qtbot, widget)
        assert len(observed) == 5 and all(thread != gui_thread for thread in observed)
    finally:
        widget.close_workers()


def test_export_uses_displayed_snapshot_and_captured_theme_in_worker(
    qtbot: QtBot, page: AnalysisPage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[tuple[ReportData, str, int]] = []
    report = page.report
    assert report is not None
    page.set_theme("dark")
    entered, release = Event(), Event()
    target = tmp_path / "中文报告.png"

    def export(
        exporter: ReportExporter,
        source: ReportData,
        path: Path,
        format: str = "pdf",
        theme: str = "light",
        cancel_token: Callable[[], bool] | None = None,
    ) -> Path:
        captured.append((source, theme, get_ident()))
        entered.set()
        assert release.wait(3)
        return path

    monkeypatch.setattr(ReportExporter, "export", export)
    page._export_to(target, "png")
    qtbot.waitUntil(entered.is_set, timeout=3000)
    try:
        page.set_theme("light")
        assert not page.pdf.isEnabled() and page.cancel_export.isEnabled()
    finally:
        release.set()
    qtbot.waitUntil(lambda: not page._exports.busy, timeout=5000)
    assert captured == [(report, "dark", captured[0][2])]
    assert captured[0][2] != get_ident()
    assert str(target) in page.export_status.text() and "已导出" in page.export_status.text()
    assert page.trend._report is report and page.trend._palette.background == "#f5f6f8"


def test_native_file_dialog_cancellation_does_not_start_export(
    page: AnalysisPage, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: ("", ""))
    page.export_report("pdf")
    assert not page._exports.busy and page.export_status.text() == ""


def test_export_failure_keeps_target_and_shows_error(
    qtbot: QtBot, page: AnalysisPage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "existing.pdf"
    target.write_bytes(b"existing report")

    def fail(
        exporter: ReportExporter,
        source: ReportData,
        path: Path,
        format: str = "pdf",
        theme: str = "light",
        cancel_token: Callable[[], bool] | None = None,
    ) -> Path:
        raise LedgerError("EXPORT_IO_ERROR")

    monkeypatch.setattr(ReportExporter, "export", fail)
    page._export_to(target, "pdf")
    qtbot.waitUntil(lambda: not page._exports.busy, timeout=5000)
    assert (
        "导出失败" in page.export_status.text() and "EXPORT_IO_ERROR" in page.export_status.text()
    )
    assert target.read_bytes() == b"existing report"


def test_user_can_cancel_running_export(
    qtbot: QtBot, page: AnalysisPage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = Event()
    target = tmp_path / "cancelled.pdf"

    def export(
        exporter: ReportExporter,
        source: ReportData,
        path: Path,
        format: str = "pdf",
        theme: str = "light",
        cancel_token: Callable[[], bool] | None = None,
    ) -> Path:
        entered.set()
        deadline = monotonic() + 3
        while monotonic() < deadline:
            if cancel_token is not None and cancel_token():
                raise LedgerError("REPORT_EXPORT_CANCELLED")
            Event().wait(0.01)
        pytest.fail("export cancellation was not delivered")

    monkeypatch.setattr(ReportExporter, "export", export)
    page._export_to(target, "pdf")
    qtbot.waitUntil(entered.is_set, timeout=3000)
    page._cancel_export()
    qtbot.waitUntil(lambda: not page._exports.busy, timeout=5000)
    assert "已取消报告导出" in page.export_status.text()
    assert not target.exists() and page.pdf.isEnabled()


@pytest.mark.parametrize("format_name", ["pdf", "png"])
def test_actual_native_report_export_from_current_page(
    qtbot: QtBot, page: AnalysisPage, tmp_path: Path, format_name: str
) -> None:
    target = tmp_path / f"实际财务报告.{format_name}"
    page._export_to(target, format_name)
    qtbot.waitUntil(lambda: not page._exports.busy, timeout=10_000)
    assert "已导出" in page.export_status.text(), page.export_status.text()
    assert target.stat().st_size > 1000
    if format_name == "pdf":
        assert target.read_bytes().startswith(b"%PDF-")
    else:
        assert not QImage(str(target)).isNull()


def test_closed_page_stops_accepting_refresh_or_export(page: AnalysisPage, tmp_path: Path) -> None:
    page.close_workers()
    generation = page._generation
    page.refresh()
    page.invalidate()
    page._export_to(tmp_path / "closed.pdf", "pdf")
    assert page._generation == generation and not page._reports.busy and not page._exports.busy
