"""Native, accessible charts that consume a single immutable analytics snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from functools import lru_cache
from typing import Literal

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QToolTip, QWidget

from openledger.application.dto.analytics import ReportData

ChartKind = Literal["trend", "categories", "ranking"]


@dataclass(frozen=True)
class ChartPalette:
    """Explicit colors make reports independent of the application's stylesheet."""

    background: str
    surface: str
    text: str
    muted: str
    grid: str
    income: str
    expense: str
    accent: str


LIGHT_PALETTE = ChartPalette(
    "#f5f6f8", "#ffffff", "#18212f", "#596579", "#e1e6ef", "#247a5a", "#c05b34", "#4265bf"
)
DARK_PALETTE = ChartPalette(
    "#171b23", "#222936", "#edf1f7", "#b7c2d4", "#3b465a", "#64cca3", "#ffb189", "#91b0ff"
)


def palette_for(theme: str) -> ChartPalette:
    """Resolve a supported export or widget theme without consulting global state."""
    if theme not in {"light", "dark"}:
        raise ValueError("Unsupported chart theme")
    return DARK_PALETTE if theme == "dark" else LIGHT_PALETTE


@lru_cache(maxsize=1)
def _font_families() -> tuple[str, ...]:
    """Read installed font families once after the GUI application has started."""
    installed = set(QFontDatabase.families())
    return tuple(
        family
        for family in (
            "Microsoft YaHei UI",
            "Microsoft YaHei",
            "Noto Sans CJK SC",
            "Noto Sans SC",
            "PingFang SC",
            "SimSun",
        )
        if family in installed
    )


def report_font(pixel_size: int = 14, *, bold: bool = False) -> QFont:
    """Use installed CJK fonts; do not copy proprietary Windows font files."""
    families = _font_families()
    font = QFont(QGuiApplication.font())
    if families:
        font.setFamilies(list(families))
    font.setPixelSize(pixel_size)
    font.setBold(bold)
    return font


def amount_text(minor: int) -> str:
    """Format any integer amount exactly, without passing money through a float."""
    sign = "-" if minor < 0 else ""
    major, fraction = divmod(abs(minor), 100)
    return f"{sign}{major:,}.{fraction:02d}"


def percentage_text(value: Decimal | None) -> str:
    """Render a ratio consistently, including an explicitly unavailable denominator."""
    if value is None:
        return "—（分母为零）"
    with localcontext() as context:
        context.prec = max(28, len(value.as_tuple().digits) + 10)
        return f"{value * 100:.1f}%"


def _ratio(value: int, maximum: int) -> float:
    """Only normalized geometry becomes floating point; financial values stay integers."""
    if maximum <= 0:
        return 0.0
    with localcontext() as context:
        context.prec = 28
        return float(Decimal(value) / Decimal(maximum))


def _text(
    painter: QPainter,
    rect: QRectF,
    value: str,
    color: str,
    *,
    size: int = 13,
    bold: bool = False,
    alignment: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
) -> None:
    painter.setFont(report_font(size, bold=bold))
    painter.setPen(QColor(color))
    painter.drawText(rect, int(alignment), value)


@dataclass(frozen=True)
class ChartHit:
    """A logical hit region with exact values and an optional category selection."""

    rect: QRectF
    text: str
    category_id: str | None = None


def chart_description(report: ReportData, kind: ChartKind) -> str:
    """Provide all labels and exact amounts to assistive technologies."""
    if kind == "trend":
        rows = [
            f"{month.month}：收入 {amount_text(month.totals.income_minor)} 元，"
            f"支出 {amount_text(month.totals.gross_expense_minor)} 元，"
            f"退款 {amount_text(month.totals.refund_minor)} 元，"
            f"净支出 {amount_text(month.totals.net_expense_minor)} 元"
            for month in report.months
        ]
    elif kind == "categories":
        rows = [
            f"{category.name}：支出 {amount_text(category.gross_expense_minor)} 元，"
            f"退款 {amount_text(category.refund_minor)} 元，"
            f"净支出 {amount_text(category.net_expense_minor)} 元，"
            f"支出占比 {percentage_text(category.share)}"
            for category in report.categories
        ]
    else:
        rows = [
            f"{rank.label}：支出 {amount_text(rank.amount_minor)} 元，{rank.count} 笔"
            for rank in report.ranking
        ]
    description = "；".join(rows) if rows else "所选范围暂无交易数据。"
    return f"{ranking_title(report)}；{description}" if kind == "ranking" else description


def ranking_title(report: ReportData) -> str:
    """Keep ranking dimension and metric explicit in widgets and saved reports."""
    dimension = {"merchant": "商户", "counterparty": "对象", "category": "分类"}[
        report.ranking_dimension
    ]
    metric = "笔数" if report.ranking_metric == "count" else "支出金额"
    return f"消费排行 · {dimension} · {metric}"


def paint_chart(
    painter: QPainter,
    rect: QRectF,
    report: ReportData,
    kind: ChartKind,
    palette: ChartPalette,
) -> tuple[ChartHit, ...]:
    """Draw one chart onto a widget, PDF page, or image using the same snapshot."""
    painter.save()
    try:
        painter.setClipRect(rect)
        painter.fillRect(rect, QColor(palette.surface))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if kind == "trend":
            return _paint_trend(painter, rect, report, palette)
        return _paint_bars(painter, rect, report, kind, palette)
    finally:
        painter.restore()


def _paint_trend(
    painter: QPainter, rect: QRectF, report: ReportData, palette: ChartPalette
) -> tuple[ChartHit, ...]:
    _text(
        painter,
        QRectF(rect.x() + 16, rect.y() + 10, rect.width() - 32, 24),
        "月度收入与净支出 · 元",
        palette.text,
        bold=True,
    )
    _text(painter, QRectF(rect.x() + 16, rect.y() + 37, 110, 22), "● 收入", palette.income)
    _text(painter, QRectF(rect.x() + 120, rect.y() + 37, 160, 22), "● 净支出", palette.expense)
    if not report.months:
        _text(
            painter,
            rect.adjusted(16, 65, -16, -16),
            "所选范围暂无交易数据",
            palette.muted,
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        return ()
    plot = rect.adjusted(82, 79, -18, -38)
    if plot.width() <= 1 or plot.height() <= 1:
        return ()
    values = [
        value
        for month in report.months
        for value in (month.totals.income_minor, month.totals.net_expense_minor)
    ]
    upper = max(0, *values)
    lower = min(0, *values)
    if upper == lower:
        upper = 100
    span = upper - lower
    baseline = plot.bottom() - plot.height() * _ratio(-lower, span)
    painter.setPen(QPen(QColor(palette.grid), 1))
    hits: list[ChartHit] = []
    for index in range(5):
        value = lower + span * index // 4
        y = plot.bottom() - plot.height() * _ratio(value - lower, span)
        painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        _text(
            painter,
            QRectF(rect.x() + 2, y - 10, 74, 20),
            amount_text(value),
            palette.muted,
            size=10,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
    group_width = plot.width() / len(report.months)
    bar_width = min(27.0, max(1.0, group_width * 0.29))
    label_stride = max(1, (len(report.months) + 7) // 8)
    for index, month in enumerate(report.months):
        center = plot.left() + (index + 0.5) * group_width
        detail = (
            f"{month.month}\n收入：{amount_text(month.totals.income_minor)} 元\n"
            f"支出：{amount_text(month.totals.gross_expense_minor)} 元\n"
            f"退款：{amount_text(month.totals.refund_minor)} 元\n"
            f"净支出：{amount_text(month.totals.net_expense_minor)} 元"
        )
        for offset, value, color in (
            (-bar_width - 1, month.totals.income_minor, palette.income),
            (1.0, month.totals.net_expense_minor, palette.expense),
        ):
            end_y = plot.bottom() - plot.height() * _ratio(value - lower, span)
            bar = QRectF(
                center + offset, min(end_y, baseline), bar_width, max(1.0, abs(end_y - baseline))
            )
            painter.fillRect(bar, QColor(color))
        hits.append(
            ChartHit(
                QRectF(center - group_width / 2, plot.top(), group_width, plot.height()), detail
            )
        )
        if index % label_stride == 0 or index == len(report.months) - 1:
            _text(
                painter,
                QRectF(center - 32, plot.bottom() + 6, 64, 22),
                month.month,
                palette.muted,
                size=10,
                alignment=Qt.AlignmentFlag.AlignCenter,
            )
    painter.setPen(QPen(QColor(palette.muted), 1))
    painter.drawLine(QPointF(plot.left(), baseline), QPointF(plot.right(), baseline))
    return tuple(hits)


def _paint_bars(
    painter: QPainter,
    rect: QRectF,
    report: ReportData,
    kind: Literal["categories", "ranking"],
    palette: ChartPalette,
) -> tuple[ChartHit, ...]:
    title = "分类支出占比 · 退款单列" if kind == "categories" else ranking_title(report)
    _text(
        painter,
        QRectF(rect.x() + 16, rect.y() + 10, rect.width() - 32, 28),
        title,
        palette.text,
        bold=True,
    )
    rows = (
        [
            (
                row.name,
                row.gross_expense_minor,
                row.category_id,
                f"{row.name}\n支出：{amount_text(row.gross_expense_minor)} 元\n"
                f"退款：{amount_text(row.refund_minor)} 元\n"
                f"净支出：{amount_text(row.net_expense_minor)} 元\n"
                f"支出占比：{percentage_text(row.share)}",
            )
            for row in report.categories
        ]
        if kind == "categories"
        else [
            (
                row.label,
                row.count if report.ranking_metric == "count" else row.amount_minor,
                None,
                f"{row.label}\n支出：{amount_text(row.amount_minor)} 元\n{row.count} 笔",
            )
            for row in report.ranking
        ]
    )
    if not rows:
        _text(
            painter,
            rect.adjusted(16, 50, -16, -16),
            "所选范围暂无交易数据",
            palette.muted,
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        return ()
    maximum = max(1, *(value for _, value, _, _ in rows))
    available = rect.height() - 55
    row_height = available / len(rows)
    value_width = min(210.0, max(115.0, rect.width() * 0.31))
    label_width = min(220.0, max(80.0, rect.width() * 0.30))
    plot_left = rect.x() + label_width + 20
    plot_width = max(4.0, rect.width() - label_width - value_width - 42)
    hits: list[ChartHit] = []
    font = report_font(12)
    metrics = QFontMetricsF(font)
    for index, (label, value, category_id, detail) in enumerate(rows):
        y = rect.y() + 47 + row_height * index
        display = metrics.elidedText(label, Qt.TextElideMode.ElideRight, label_width - 6)
        _text(
            painter,
            QRectF(rect.x() + 16, y, label_width - 4, row_height),
            display,
            palette.text,
            size=12,
        )
        height = min(19.0, max(2.0, row_height * 0.48))
        bar = QRectF(
            plot_left,
            y + (row_height - height) / 2,
            plot_width * _ratio(max(0, value), maximum),
            height,
        )
        painter.fillRect(bar, QColor(palette.accent if kind == "categories" else palette.expense))
        _text(
            painter,
            QRectF(rect.right() - value_width - 14, y, value_width, row_height),
            f"{value} 笔"
            if kind == "ranking" and report.ranking_metric == "count"
            else f"{amount_text(value)} 元",
            palette.text,
            size=11,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        hits.append(
            ChartHit(QRectF(rect.x() + 10, y, rect.width() - 20, row_height), detail, category_id)
        )
    return tuple(hits)


class ChartWidget(QWidget):
    """Keyboard-readable native chart with exact-value tooltips and category clicks."""

    categoryActivated = Signal(str)

    def __init__(self, kind: ChartKind = "trend", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if kind not in {"trend", "categories", "ranking"}:
            raise ValueError("Unsupported chart kind")
        self.kind = kind
        self._report: ReportData | None = None
        self._palette = LIGHT_PALETTE
        self._hits: tuple[ChartHit, ...] = ()
        self._selected_category = 0
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(280, 220)
        self.setAccessibleName(
            {"trend": "月度收支趋势", "categories": "分类支出", "ranking": "消费排行"}[kind]
        )
        self.setAccessibleDescription("所选范围暂无交易数据。")

    def minimumSizeHint(self) -> QSize:
        """Keep labels readable when placed inside a desktop scroll area."""
        return QSize(360, self.minimumHeight())

    def set_report(self, report: ReportData, theme: str = "light") -> None:
        """Replace the complete chart snapshot without querying or mutating the ledger."""
        self._palette = palette_for(theme)
        self._report = report
        self._hits = ()
        self._selected_category = 0
        count = len(report.categories) if self.kind == "categories" else len(report.ranking)
        self.setMinimumHeight(260 if self.kind == "trend" else max(220, 52 + count * 37))
        self.setAccessibleDescription(chart_description(report, self.kind))
        self.updateGeometry()
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint from DTO values; repainting has no financial side effects."""
        painter = QPainter(self)
        try:
            rect = QRectF(self.rect())
            if self._report is None:
                painter.fillRect(rect, QColor(self._palette.surface))
                _text(
                    painter,
                    rect,
                    "所选范围暂无交易数据",
                    self._palette.muted,
                    alignment=Qt.AlignmentFlag.AlignCenter,
                )
            else:
                self._hits = paint_chart(painter, rect, self._report, self.kind, self._palette)
                if self.kind == "categories" and self.hasFocus() and self._hits:
                    painter.setPen(QPen(QColor(self._palette.accent), 1, Qt.PenStyle.DashLine))
                    painter.drawRect(
                        self._hits[self._selected_category].rect.adjusted(1, 1, -1, -1)
                    )
        finally:
            painter.end()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Reveal exact amounts and unabbreviated labels under the cursor."""
        for hit in self._hits:
            if hit.rect.contains(event.position()):
                QToolTip.showText(event.globalPosition().toPoint(), hit.text, self)
                return
        QToolTip.hideText()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Emit a category identifier only after an explicit left-button selection."""
        if event.button() == Qt.MouseButton.LeftButton:
            for index, hit in enumerate(self._hits):
                if hit.category_id is not None and hit.rect.contains(event.position()):
                    self._selected_category = index
                    self.categoryActivated.emit(hit.category_id)
                    self.update()
                    event.accept()
                    return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Allow focused categories to be selected without a pointing device."""
        if self.kind == "categories" and self._report is not None and self._report.categories:
            categories = self._report.categories
            if event.key() in {Qt.Key.Key_Down, Qt.Key.Key_Up}:
                step = 1 if event.key() == Qt.Key.Key_Down else -1
                self._selected_category = (self._selected_category + step) % len(categories)
                selected = categories[self._selected_category]
                self.setAccessibleDescription(
                    f"当前分类：{selected.name}；{chart_description(self._report, self.kind)}"
                )
                self.update()
                event.accept()
                return
            if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space}:
                self.categoryActivated.emit(categories[self._selected_category].category_id)
                event.accept()
                return
        super().keyPressEvent(event)
