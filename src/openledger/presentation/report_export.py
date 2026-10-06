"""Atomic native PDF and PNG reports from an immutable analytics snapshot."""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QByteArray, QFile, QIODevice, QMarginsF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QImageWriter,
    QPageLayout,
    QPageSize,
    QPainter,
    QPdfWriter,
    QTextLayout,
    QTextOption,
)

from openledger.application.dto.analytics import ReportData
from openledger.domain.errors import LedgerError
from openledger.presentation.charts import (
    ChartKind,
    ChartPalette,
    amount_text,
    paint_chart,
    palette_for,
    percentage_text,
    ranking_title,
    report_font,
)

_PAGE_WIDTH = 794
_PAGE_HEIGHT = 1123
_MARGIN = 48
_CONTENT_WIDTH = _PAGE_WIDTH - 2 * _MARGIN
_BODY_TOP = 136
_BODY_BOTTOM = _PAGE_HEIGHT - 76
_MAX_PAGES = 300
_PNG_SCALE = 1.5
_MAX_PNG_HEIGHT = 24_000
_MAX_PNG_PIXELS = 32_000_000


@dataclass(frozen=True)
class _Paragraph:
    text: str
    height: float
    size: int = 13
    bold: bool = False


@dataclass(frozen=True)
class _Metrics:
    height: float = 156


@dataclass(frozen=True)
class _Chart:
    report: ReportData
    kind: ChartKind
    height: float


@dataclass(frozen=True)
class _Table:
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    widths: tuple[float, ...]
    heights: tuple[float, ...]
    height: float


type _Block = _Paragraph | _Metrics | _Chart | _Table


def _line_count(value: str, width: float, font: QFont) -> int:
    """Measure the same Qt word-wrap policy used to render Chinese report text."""
    count = 0
    for paragraph in value.split("\n"):
        if not paragraph:
            count += 1
            continue
        layout = QTextLayout(paragraph, font)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        layout.beginLayout()
        try:
            while True:
                line = layout.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(max(1.0, width))
                count += 1
        finally:
            layout.endLayout()
    return max(1, count)


def _paragraph(text: str, *, size: int = 13, bold: bool = False) -> _Paragraph:
    lines = _line_count(text, _CONTENT_WIDTH, report_font(size, bold=bold))
    return _Paragraph(text, lines * (size + 7) + 8, size, bold)


def _table_blocks(
    headers: tuple[str, ...],
    rows: tuple[tuple[str, ...], ...],
    proportions: tuple[float, ...],
) -> list[_Table]:
    widths = tuple(proportion * _CONTENT_WIDTH for proportion in proportions)
    blocks: list[_Table] = []
    batch: list[tuple[str, ...]] = []
    heights: list[float] = []
    available = _BODY_BOTTOM - _BODY_TOP - 70
    current_height = 38.0
    for row in rows:
        height = float(
            max(
                _line_count(value, width - 16, report_font(11))
                for value, width in zip(row, widths, strict=True)
            )
            * 18
            + 16
        )
        if height > available - 38:
            raise LedgerError("REPORT_TOO_LARGE", "报告中的单行文字超过一页，请缩小报告范围。")
        if batch and current_height + height > available:
            blocks.append(_Table(headers, tuple(batch), widths, tuple(heights), current_height))
            batch, heights, current_height = [], [], 38.0
        batch.append(row)
        heights.append(height)
        current_height += height
    if batch:
        blocks.append(_Table(headers, tuple(batch), widths, tuple(heights), current_height))
    return blocks


def _build_pages(report: ReportData) -> tuple[tuple[_Block, ...], ...]:
    code = report.filters.currency_code
    blocks: list[_Block] = []
    scopes = "；".join(report.scope_labels)
    if scopes and _line_count(scopes, _CONTENT_WIDTH, report_font(10)) > 1:
        blocks.append(_paragraph(f"筛选条件：{scopes}", size=11))
    blocks.append(_Metrics())
    if report.comparison_totals is not None:
        previous = report.comparison_totals
        blocks.append(
            _paragraph(
                f"对比期间：{report.comparison_start} 至 {report.comparison_end}；"
                f"收入 {amount_text(previous.income_minor, code)} {code}，"
                f"净支出 {amount_text(previous.net_expense_minor, code)} {code}，"
                f"结余 {amount_text(previous.surplus_minor, code)} {code}。"
            )
        )
    if report.notes:
        blocks.append(_paragraph("统计说明", bold=True))
        blocks.extend(_paragraph(f"• {note}", size=12) for note in report.notes)
    blocks.append(_paragraph("月度收支趋势", size=17, bold=True))
    if not report.months:
        blocks.append(_paragraph("所选范围暂无交易数据。"))
    for start in range(0, len(report.months), 12):
        month_report = replace(report, months=report.months[start : start + 12])
        blocks.append(_Chart(month_report, "trend", 300))
    blocks.extend(
        _table_blocks(
            ("月份", "收入（元）", "支出（元）", "退款（元）", "净支出（元）"),
            tuple(
                (
                    month.month,
                    amount_text(month.totals.income_minor, code),
                    amount_text(month.totals.gross_expense_minor, code),
                    amount_text(month.totals.refund_minor, code),
                    amount_text(month.totals.net_expense_minor, code),
                )
                for month in report.months
            ),
            (0.16, 0.21, 0.21, 0.20, 0.22),
        )
    )
    blocks.append(_paragraph("分类支出 · 支出占比以原支出为分母，退款单列", size=17, bold=True))
    if not report.categories:
        blocks.append(_paragraph("所选范围暂无分类支出数据。"))
    for start in range(0, len(report.categories), 9):
        rows = report.categories[start : start + 9]
        category_report = replace(report, categories=rows)
        blocks.append(_Chart(category_report, "categories", 65 + len(rows) * 39))
    blocks.extend(
        _table_blocks(
            ("分类", "支出（元）", "退款（元）", "净支出（元）", "支出占比"),
            tuple(
                (
                    category.name,
                    amount_text(category.gross_expense_minor, code),
                    amount_text(category.refund_minor, code),
                    amount_text(category.net_expense_minor, code),
                    percentage_text(category.share),
                )
                for category in report.categories
            ),
            (0.24, 0.20, 0.19, 0.20, 0.17),
        )
    )
    blocks.append(_paragraph(ranking_title(report), size=17, bold=True))
    if not report.ranking:
        blocks.append(_paragraph("所选范围暂无消费排行数据。"))
    for start in range(0, len(report.ranking), 9):
        rows_rank = report.ranking[start : start + 9]
        rank_report = replace(report, ranking=rows_rank)
        blocks.append(_Chart(rank_report, "ranking", 65 + len(rows_rank) * 39))
    blocks.extend(
        _table_blocks(
            ("消费项", "支出（元）", "笔数"),
            tuple(
                (
                    rank.label,
                    amount_text(rank.amount_minor, code),
                    str(rank.count),
                )
                for rank in report.ranking
            ),
            (0.54, 0.32, 0.14),
        )
    )
    pages: list[tuple[_Block, ...]] = []
    current: list[_Block] = []
    height = 0.0
    capacity = _BODY_BOTTOM - _BODY_TOP
    for index, block in enumerate(blocks):
        if block.height > capacity:
            raise LedgerError("REPORT_TOO_LARGE", "报告文字过长，请缩小报告范围。")
        needed_height = block.height + 12
        if isinstance(block, _Paragraph) and block.bold and index + 1 < len(blocks):
            following = blocks[index + 1]
            needed_height += following.height + 12
            if isinstance(following, _Chart) and index + 2 < len(blocks):
                table = blocks[index + 2]
                if isinstance(table, _Table) and needed_height + table.height + 12 <= capacity:
                    needed_height += table.height + 12
        elif isinstance(block, _Chart) and index + 1 < len(blocks):
            following = blocks[index + 1]
            if isinstance(following, _Table) and needed_height + following.height + 12 <= capacity:
                needed_height += following.height + 12
        if current and height + needed_height > capacity:
            pages.append(tuple(current))
            current, height = [], 0.0
        current.append(block)
        height += block.height + 12
        if len(pages) >= _MAX_PAGES:
            raise LedgerError("REPORT_TOO_LARGE", "报告页数过多，请缩小报告范围。")
    if current:
        pages.append(tuple(current))
    return tuple(pages)


def _draw_text(
    painter: QPainter,
    rect: QRectF,
    value: str,
    palette: ChartPalette,
    *,
    size: int = 13,
    bold: bool = False,
    muted: bool = False,
) -> None:
    painter.setFont(report_font(size, bold=bold))
    painter.setPen(QColor(palette.muted if muted else palette.text))
    painter.drawText(
        rect,
        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        | int(Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextWrapAnywhere),
        value,
    )


def _paint_metrics(
    painter: QPainter, top: float, report: ReportData, palette: ChartPalette
) -> None:
    code = report.filters.currency_code
    totals = report.totals
    values = (
        (
            "收入",
            f"{amount_text(totals.income_minor, code)} {code}",
            f"{totals.income_count} 笔",
        ),
        (
            "原支出",
            f"{amount_text(totals.gross_expense_minor, code)} {code}",
            f"{totals.expense_count} 笔",
        ),
        (
            "退款",
            f"{amount_text(totals.refund_minor, code)} {code}",
            f"{totals.refund_count} 笔",
        ),
        (
            "净支出",
            f"{amount_text(totals.net_expense_minor, code)} {code}",
            "原支出 − 退款",
        ),
        (
            "结余",
            f"{amount_text(totals.surplus_minor, code)} {code}",
            "收入 − 净支出",
        ),
        ("结余率", percentage_text(totals.savings_rate), "结余 ÷ 收入"),
    )
    width = (_CONTENT_WIDTH - 20) / 3
    for index, (label, value, detail) in enumerate(values):
        left = _MARGIN + (index % 3) * (width + 10)
        y = top + (index // 3) * 78
        rect = QRectF(left, y, width, 70)
        painter.fillRect(rect, QColor(palette.surface))
        _draw_text(painter, rect.adjusted(11, 6, -11, -48), label, palette, size=11, muted=True)
        value_size = 13
        while (
            value_size > 9
            and QFontMetricsF(report_font(value_size)).horizontalAdvance(value) > width - 22
        ):
            value_size -= 1
        _draw_text(
            painter,
            QRectF(left + 11, y + 24, width - 22, 26),
            value,
            palette,
            size=value_size,
            bold=True,
        )
        _draw_text(
            painter, QRectF(left + 11, y + 49, width - 22, 20), detail, palette, size=10, muted=True
        )


def _paint_table(painter: QPainter, top: float, block: _Table, palette: ChartPalette) -> None:
    y = top
    painter.fillRect(QRectF(_MARGIN, y, _CONTENT_WIDTH, 38), QColor(palette.surface))
    x = float(_MARGIN)
    for header, width in zip(block.headers, block.widths, strict=True):
        _draw_text(
            painter, QRectF(x + 8, y + 9, width - 16, 28), header, palette, size=11, bold=True
        )
        x += width
    y += 38
    for index, (row, height) in enumerate(zip(block.rows, block.heights, strict=True)):
        if index % 2 == 0:
            painter.fillRect(QRectF(_MARGIN, y, _CONTENT_WIDTH, height), QColor(palette.surface))
        x = float(_MARGIN)
        for value, width in zip(row, block.widths, strict=True):
            _draw_text(
                painter, QRectF(x + 8, y + 8, width - 16, height - 12), value, palette, size=11
            )
            x += width
        y += height


def _paint_page(
    painter: QPainter,
    blocks: tuple[_Block, ...],
    report: ReportData,
    palette: ChartPalette,
    number: int,
    count: int,
) -> None:
    painter.fillRect(QRectF(0, 0, _PAGE_WIDTH, _PAGE_HEIGHT), QColor(palette.background))
    _draw_text(
        painter,
        QRectF(_MARGIN, 28, _CONTENT_WIDTH, 37),
        "OpenLedger 财务报告",
        palette,
        size=24,
        bold=True,
    )
    _draw_text(
        painter,
        QRectF(_MARGIN, 72, _CONTENT_WIDTH, 24),
        f"统计期间：{report.filters.start_on} 至 {report.filters.end_on} "
        f"· 币种 {report.filters.currency_code}",
        palette,
        size=12,
    )
    scopes = "；".join(report.scope_labels) if report.scope_labels else "所有账本、账户、分类与标签"
    scope_lines = _line_count(scopes, _CONTENT_WIDTH, report_font(10))
    # The complete scope is also repeated as a measured body paragraph on the first page.
    _draw_text(
        painter,
        QRectF(_MARGIN, 101, _CONTENT_WIDTH, 26),
        scopes if scope_lines == 1 else "详细筛选条件见报告正文",
        palette,
        size=10,
        muted=True,
    )
    top = float(_BODY_TOP)
    for block in blocks:
        if isinstance(block, _Paragraph):
            _draw_text(
                painter,
                QRectF(_MARGIN, top, _CONTENT_WIDTH, block.height),
                block.text,
                palette,
                size=block.size,
                bold=block.bold,
            )
        elif isinstance(block, _Metrics):
            _paint_metrics(painter, top, report, palette)
        elif isinstance(block, _Chart):
            paint_chart(
                painter,
                QRectF(_MARGIN, top, _CONTENT_WIDTH, block.height),
                block.report,
                block.kind,
                palette,
            )
        else:
            _paint_table(painter, top, block, palette)
        top += block.height + 12
    _draw_text(
        painter,
        QRectF(_MARGIN, _PAGE_HEIGHT - 56, _CONTENT_WIDTH, 19),
        f"数据版本 {report.data_revision} · 生成时间 {report.generated_at_utc}",
        palette,
        size=9,
        muted=True,
    )
    _draw_text(
        painter,
        QRectF(_MARGIN, _PAGE_HEIGHT - 33, _CONTENT_WIDTH, 17),
        f"OpenLedger · 完全本地生成 · 第 {number} / {count} 页",
        palette,
        size=9,
        muted=True,
    )


def _validate_destination(path: Path, format_name: str) -> None:
    if not path.is_absolute() or path.suffix.lower() != f".{format_name}":
        raise LedgerError("EXPORT_PATH_INVALID", "请选择完整的 PDF 或 PNG 文件路径。")
    if not path.parent.is_dir():
        raise LedgerError("EXPORT_PATH_INVALID", "报告目标目录不存在。")
    for component in (path, *path.parents):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or bool(
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise LedgerError("EXPORT_PATH_INVALID", "报告目标路径包含链接或目录联接。")
    if path.exists() and not path.is_file():
        raise LedgerError("EXPORT_PATH_INVALID", "报告目标必须是文件。")


def _check_cancel(cancel_token: Callable[[], bool] | None) -> None:
    if cancel_token is not None and cancel_token():
        raise LedgerError("REPORT_EXPORT_CANCELLED", "已取消报告导出。")


class ReportExporter:
    """Render complete PDF/PNG reports before atomically publishing the target file."""

    def export(
        self,
        report: ReportData,
        path: Path,
        format: str = "pdf",
        theme: str = "light",
        cancel_token: Callable[[], bool] | None = None,
    ) -> Path:
        """Keep an existing report intact on cancellation, render failure, or I/O failure."""
        if format not in {"pdf", "png"} or theme not in {"light", "dark"}:
            raise LedgerError("REPORT_EXPORT_INVALID", "报告格式或主题无效。")
        if QGuiApplication.instance() is None:
            raise LedgerError("REPORT_RENDER_FAILED", "报告导出需要正在运行的桌面应用。")
        temporary: Path | None = None
        try:
            _validate_destination(path, format)
            _check_cancel(cancel_token)
            pages = _build_pages(report)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
            )
            os.close(descriptor)
            temporary = Path(temporary_name)
            palette = palette_for(theme)
            if format == "pdf":
                self._write_pdf(temporary, pages, report, palette, cancel_token)
            else:
                self._write_png(temporary, pages, report, palette, cancel_token)
            _check_cancel(cancel_token)
            with temporary.open("r+b") as stream:
                os.fsync(stream.fileno())
            _validate_destination(path, format)
            os.replace(temporary, path)
            return path
        except LedgerError:
            raise
        except OSError as error:
            raise LedgerError(
                "EXPORT_IO_ERROR", "无法写入报告，请检查目录权限和可用空间。"
            ) from error
        except Exception as error:
            raise LedgerError("REPORT_RENDER_FAILED", "报告绘制失败，原文件已保留。") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _write_pdf(
        self,
        temporary: Path,
        pages: tuple[tuple[_Block, ...], ...],
        report: ReportData,
        palette: ChartPalette,
        cancel_token: Callable[[], bool] | None,
    ) -> None:
        device = QFile(str(temporary))
        if not device.open(QIODevice.OpenModeFlag.WriteOnly):
            raise LedgerError("EXPORT_IO_ERROR", "无法创建报告临时文件。")
        try:
            writer = QPdfWriter(device)
            writer.setResolution(96)
            writer.setTitle("OpenLedger 财务报告")
            writer.setCreator("OpenLedger")
            writer.setPageLayout(
                QPageLayout(
                    QPageSize(QPageSize.PageSizeId.A4),
                    QPageLayout.Orientation.Portrait,
                    QMarginsF(0, 0, 0, 0),
                )
            )
            painter = QPainter()
            if not painter.begin(writer):
                raise LedgerError("REPORT_RENDER_FAILED", "无法开始绘制 PDF 报告。")
            try:
                for index, blocks in enumerate(pages):
                    _check_cancel(cancel_token)
                    if index and not writer.newPage():
                        raise LedgerError("REPORT_RENDER_FAILED", "无法创建 PDF 报告页面。")
                    painter.save()
                    try:
                        painter.scale(writer.width() / _PAGE_WIDTH, writer.height() / _PAGE_HEIGHT)
                        _paint_page(painter, blocks, report, palette, index + 1, len(pages))
                    finally:
                        painter.restore()
            finally:
                painter.end()
            del painter
            del writer
            if not device.flush() or device.error() != QFile.FileError.NoError:
                raise LedgerError("EXPORT_IO_ERROR", "写入 PDF 报告失败。")
        finally:
            device.close()

    def _write_png(
        self,
        temporary: Path,
        pages: tuple[tuple[_Block, ...], ...],
        report: ReportData,
        palette: ChartPalette,
        cancel_token: Callable[[], bool] | None,
    ) -> None:
        width = int(_PAGE_WIDTH * _PNG_SCALE)
        page_height = int(_PAGE_HEIGHT * _PNG_SCALE)
        height = page_height * len(pages)
        if height > _MAX_PNG_HEIGHT or width * height > _MAX_PNG_PIXELS:
            raise LedgerError("REPORT_TOO_LARGE", "完整图片过高，请缩小统计范围或改用 PDF。")
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        if image.isNull():
            raise LedgerError("REPORT_RENDER_FAILED", "无法分配报告图片内存。")
        image.fill(QColor(palette.background))
        image.setText("Title", "OpenLedger 财务报告")
        image.setText("Period", f"{report.filters.start_on}/{report.filters.end_on}")
        image.setText("DataRevision", str(report.data_revision))
        image.setText("GeneratedAtUTC", report.generated_at_utc)
        painter = QPainter(image)
        try:
            for index, blocks in enumerate(pages):
                _check_cancel(cancel_token)
                painter.save()
                try:
                    painter.translate(0, index * page_height)
                    painter.scale(_PNG_SCALE, _PNG_SCALE)
                    _paint_page(painter, blocks, report, palette, index + 1, len(pages))
                finally:
                    painter.restore()
        finally:
            painter.end()
        writer = QImageWriter(str(temporary))
        writer.setFormat(QByteArray(b"png"))
        if not writer.write(image):
            raise LedgerError("EXPORT_IO_ERROR", "写入 PNG 报告失败。")
        del writer
