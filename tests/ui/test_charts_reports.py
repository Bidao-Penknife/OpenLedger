"""Native chart accessibility and atomic, complete financial report export checks."""

import os
import re
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from openledger.application.dto.analytics import (
    AnalyticsFilter,
    CategoryTotals,
    ExpenseRank,
    MonthTotals,
    PeriodTotals,
    ReportData,
)
from openledger.domain.errors import LedgerError
from openledger.presentation.charts import ChartKind, ChartWidget, amount_text, percentage_text
from openledger.presentation.report_export import ReportExporter

pytestmark = pytest.mark.ui


@pytest.fixture
def report() -> ReportData:
    """One immutable, synthetic snapshot; report tests never query a real ledger."""
    totals = PeriodTotals(100_000, 24_500, 30_000, -5_500, 105_500, 1, 4, 2, Decimal("1.055"))
    return ReportData(
        filters=AnalyticsFilter(date(2026, 9, 1), date(2026, 10, 3)),
        totals=totals,
        months=(MonthTotals("2026-09", totals), MonthTotals("2026-10", totals)),
        categories=(
            CategoryTotals("food", "餐饮与朋友聚餐", 15_000, 30_000, -15_000, 3, Decimal("0.612")),
            CategoryTotals("study", "学习资料", 9_500, 0, 9_500, 1, Decimal("0.388")),
        ),
        ranking=(ExpenseRank("朋友聚餐", 15_000, 3), ExpenseRank("学习资料", 9_500, 1)),
        comparison_totals=totals,
        comparison_start=date(2026, 7, 30),
        comparison_end=date(2026, 8, 31),
        notes=("仅统计有效交易；退款按退款发生日期单列。", "转账、期初与余额校准不计入收支。"),
        scope_labels=("账本：我的账本", "账户：测试现金"),
        data_revision=37,
        generated_at_utc="2026-10-03T00:00:00.000Z",
    )


@pytest.mark.parametrize("kind", ["trend", "categories", "ranking"])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_charts_render_snapshot_with_exact_accessible_amounts(
    qtbot: QtBot, report: ReportData, kind: ChartKind, theme: str
) -> None:
    """Both themes paint real widgets and retain unabridged labels for screen readers."""
    widget = ChartWidget(kind)
    qtbot.addWidget(widget)
    widget.set_report(report, theme)
    widget.resize(640, widget.minimumHeight())
    widget.show()
    QTest.qWait(20)
    image = widget.grab().toImage()
    assert not image.isNull()
    assert widget.accessibleName()
    description = widget.accessibleDescription()
    assert "150.00" in description or "1,000.00" in description
    if kind == "trend":
        assert "-55.00" in description
    if kind == "categories":
        assert "餐饮与朋友聚餐" in description
        assert "退款 300.00" in description


def test_category_chart_emits_explicit_category_selection(qtbot: QtBot, report: ReportData) -> None:
    """A category click returns its stable ID, allowing the parent to filter transactions."""
    widget = ChartWidget("categories")
    qtbot.addWidget(widget)
    widget.set_report(report)
    widget.resize(640, 300)
    widget.show()
    QTest.qWait(20)
    with qtbot.waitSignal(widget.categoryActivated, timeout=1000) as signal:
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(100, 85))
    assert signal.args == ["food"]


def test_category_chart_supports_keyboard_selection(qtbot: QtBot, report: ReportData) -> None:
    """Keyboard focus, arrow selection, and Enter provide the same category drill-down."""
    widget = ChartWidget("categories")
    qtbot.addWidget(widget)
    widget.set_report(report)
    widget.show()
    widget.setFocus()
    QTest.keyClick(widget, Qt.Key.Key_Down)
    assert "当前分类：学习资料" in widget.accessibleDescription()
    with qtbot.waitSignal(widget.categoryActivated, timeout=1000) as signal:
        QTest.keyClick(widget, Qt.Key.Key_Return)
    assert signal.args == ["study"]


def test_count_ranking_uses_counts_for_bar_geometry(qtbot: QtBot, report: ReportData) -> None:
    """The metric controls the plotted values even when expense amounts order differently."""
    ranks = (ExpenseRank("单笔大额消费", 900_000, 1), ExpenseRank("四笔小额消费", 400, 4))
    widget = ChartWidget("ranking")
    qtbot.addWidget(widget)
    widget.set_report(
        replace(report, ranking=ranks, ranking_metric="count", ranking_dimension="counterparty")
    )
    widget.resize(640, 240)
    widget.show()
    QTest.qWait(20)
    assert "消费排行 · 对象 · 笔数" in widget.accessibleDescription()
    image = widget.grab().toImage()
    ratio = image.devicePixelRatio()
    # x=360 lies beyond the 1-count bar but within the 4-count bar at this layout width.
    first = image.pixelColor(int(360 * ratio), int(93 * ratio)).name()
    second = image.pixelColor(int(360 * ratio), int(186 * ratio)).name()
    assert first == "#ffffff"
    assert second == "#c05b34"


def test_charts_empty_range_and_long_names_remain_readable(
    qtbot: QtBot, report: ReportData
) -> None:
    """Empty snapshots have an accessible state; long category names remain fully available."""
    widget = ChartWidget("categories")
    qtbot.addWidget(widget)
    widget.set_report(replace(report, categories=()))
    widget.show()
    assert "暂无" in widget.accessibleDescription()
    long_name = "长中文分类名称" * 30
    category = replace(report.categories[0], name=long_name)
    widget.set_report(replace(report, categories=(category,)))
    widget.resize(280, 220)
    assert long_name in widget.accessibleDescription()
    assert not widget.grab().isNull()


def test_chart_maximum_integer_money_and_zero_denominator(qtbot: QtBot, report: ReportData) -> None:
    """Values near the SQLite integer limit remain exact; undefined rates are explicit."""
    value = 2**63 - 1
    totals = replace(report.totals, income_minor=value, savings_rate=None)
    widget = ChartWidget("trend")
    qtbot.addWidget(widget)
    widget.set_report(replace(report, totals=totals, months=(MonthTotals("2026-10", totals),)))
    widget.show()
    assert "92,233,720,368,547,758.07" in widget.accessibleDescription()
    assert not widget.grab().isNull()
    assert amount_text(-1) == "-0.01"
    assert percentage_text(None) == "—（分母为零）"
    assert percentage_text(Decimal("1.055")) == "105.5%"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_report_png_contains_full_pages_and_snapshot_metadata(
    qtbot: QtBot, report: ReportData, tmp_path: Path, theme: str
) -> None:
    """The image includes every report page and the exact original snapshot metadata."""
    destination = tmp_path / "中文 财务报告.png"
    assert ReportExporter().export(report, destination, "png", theme) == destination
    image = QImage(str(destination))
    assert not image.isNull()
    assert image.width() == 1191
    assert image.height() >= 3368
    assert image.text("DataRevision") == "37"
    assert image.text("GeneratedAtUTC") == report.generated_at_utc
    assert image.text("Period") == "2026-09-01/2026-10-03"
    assert not tuple(tmp_path.glob(".*.tmp"))


def test_report_pdf_is_multipage_and_embeds_font_data(
    qtbot: QtBot, report: ReportData, tmp_path: Path
) -> None:
    """Qt produces a complete, closed PDF with embedded fonts and multiple pages."""
    destination = tmp_path / "中文 财务报告.pdf"
    ReportExporter().export(report, destination)
    data = destination.read_bytes()
    assert data.startswith(b"%PDF-")
    assert b"%%EOF" in data[-1024:]
    assert len(re.findall(rb"/Type\s*/Page\b", data)) >= 2
    assert b"/FontFile2" in data or b"/FontFile3" in data
    # Successfully replacing a closed report also checks handle lifetimes on Windows.
    ReportExporter().export(report, destination, "pdf", "dark")
    assert destination.read_bytes().startswith(b"%PDF-")


def test_report_long_scope_and_maximum_money_exports_completely(
    qtbot: QtBot, report: ReportData, tmp_path: Path
) -> None:
    """Long Chinese scope labels receive measured body space instead of header clipping."""
    maximum = 2**63 - 1
    totals = replace(report.totals, income_minor=maximum, surplus_minor=maximum, savings_rate=None)
    wide = replace(report, totals=totals, scope_labels=("账本：" + "旅行与学习账本" * 40,))
    destination = tmp_path / "long.pdf"
    ReportExporter().export(wide, destination)
    assert len(re.findall(rb"/Type\s*/Page\b", destination.read_bytes())) >= 2


def test_empty_report_has_a_complete_single_page(
    qtbot: QtBot, report: ReportData, tmp_path: Path
) -> None:
    """An empty dataset still exports scope, zero totals, and explicit no-data sections."""
    zeros = PeriodTotals(0, 0, 0, 0, 0, 0, 0, 0, None)
    empty = replace(
        report,
        totals=zeros,
        months=(),
        categories=(),
        ranking=(),
        comparison_totals=None,
        comparison_start=None,
        comparison_end=None,
        notes=(),
    )
    destination = tmp_path / "empty.pdf"
    ReportExporter().export(empty, destination)
    assert len(re.findall(rb"/Type\s*/Page\b", destination.read_bytes())) == 1
    png = tmp_path / "empty.png"
    ReportExporter().export(empty, png, "png")
    assert QImage(str(png)).height() == 1684


def test_report_renderer_failure_preserves_existing_destination(
    qtbot: QtBot, report: ReportData, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partial temporary write cannot destroy a user's previous exported report."""
    destination = tmp_path / "previous.pdf"
    destination.write_bytes(b"previous report")

    def broken_render(*arguments: object, **keywords: object) -> None:
        raise RuntimeError("synthetic renderer failure")

    monkeypatch.setattr(ReportExporter, "_write_pdf", broken_render)
    with pytest.raises(LedgerError) as error:
        ReportExporter().export(report, destination)
    assert error.value.code == "REPORT_RENDER_FAILED"
    assert destination.read_bytes() == b"previous report"
    assert not tuple(tmp_path.glob(".*.tmp"))


def test_report_fsync_failure_preserves_previous_file(
    qtbot: QtBot, report: ReportData, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Atomic publication waits for the rendered file to be durably flushed."""
    destination = tmp_path / "previous.pdf"
    destination.write_bytes(b"previous report")

    def failed_sync(descriptor: int) -> None:
        raise OSError("synthetic flush failure")

    monkeypatch.setattr(os, "fsync", failed_sync)
    with pytest.raises(LedgerError) as error:
        ReportExporter().export(report, destination)
    assert error.value.code == "EXPORT_IO_ERROR"
    assert destination.read_bytes() == b"previous report"
    assert not tuple(tmp_path.glob(".*.tmp"))


def test_report_cancellation_closes_handles_and_preserves_previous_report(
    qtbot: QtBot, report: ReportData, tmp_path: Path
) -> None:
    """Cancellation between PDF pages leaves no published partial file or leaked handle."""
    destination = tmp_path / "previous.pdf"
    destination.write_bytes(b"previous report")
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    with pytest.raises(LedgerError) as error:
        ReportExporter().export(report, destination, cancel_token=cancelled)
    assert error.value.code == "REPORT_EXPORT_CANCELLED"
    assert destination.read_bytes() == b"previous report"
    assert not tuple(tmp_path.glob(".*.tmp"))


def test_overlong_png_fails_without_clipping_or_overwriting(
    qtbot: QtBot, report: ReportData, tmp_path: Path
) -> None:
    """An image beyond the safe height is refused; PDF remains the complete-report option."""
    categories = tuple(
        replace(report.categories[0], name=f"合成分类 {index}") for index in range(400)
    )
    destination = tmp_path / "previous.png"
    destination.write_bytes(b"previous image")
    with pytest.raises(LedgerError) as error:
        ReportExporter().export(replace(report, categories=categories), destination, "png")
    assert error.value.code == "REPORT_TOO_LARGE"
    assert destination.read_bytes() == b"previous image"
    assert not tuple(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize("format_name,theme", [("svg", "light"), ("pdf", "system")])
def test_report_invalid_format_or_theme_is_safe(
    qtbot: QtBot, report: ReportData, tmp_path: Path, format_name: str, theme: str
) -> None:
    """Unsupported report options never create or modify an output file."""
    destination = tmp_path / "report.pdf"
    with pytest.raises(LedgerError) as error:
        ReportExporter().export(report, destination, format_name, theme)
    assert error.value.code == "REPORT_EXPORT_INVALID"
    assert not destination.exists()


def test_report_requires_matching_absolute_destination(qtbot: QtBot, report: ReportData) -> None:
    """Report output never falls back to the process working directory."""
    with pytest.raises(LedgerError) as error:
        ReportExporter().export(report, Path("report.pdf"))
    assert error.value.code == "EXPORT_PATH_INVALID"
