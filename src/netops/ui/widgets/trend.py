"""实时延迟趋势图：QPainter 自绘，不引入图表依赖。

每个目标一条折线，超时点画成底部的小红叉；纵轴按当前最大 RTT 自适应，
横轴为会话已运行秒数。只保留最近 ``max_points`` 个采样，长时间运行也不会涨内存。
"""

from __future__ import annotations

import math
from collections import deque

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QSizePolicy, QWidget

__all__ = ["RttTrendChart", "SERIES_COLORS"]

#: 折线配色：与主题的青色主色协调，最多 8 个目标循环使用。
SERIES_COLORS = (
    QColor("#0f766e"),
    QColor("#2563eb"),
    QColor("#c2410c"),
    QColor("#7c3aed"),
    QColor("#0891b2"),
    QColor("#b45309"),
    QColor("#be123c"),
    QColor("#4d7c0f"),
)

_GRID = QColor("#e2e8f0")
_AXIS_TEXT = QColor("#64748b")
_TIMEOUT = QColor("#dc2626")
_BG = QColor("#ffffff")
_EMPTY_TEXT = QColor("#94a3b8")

_LEFT = 48.0
_RIGHT = 14.0
_TOP = 14.0
_BOTTOM = 28.0


class RttTrendChart(QWidget):
    """多目标 RTT 折线图。"""

    def __init__(self, parent: QWidget | None = None, *, max_points: int = 600) -> None:
        super().__init__(parent)
        self.setObjectName("RttTrendChart")
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._max_points = max(10, max_points)
        self._series: dict[str, deque[tuple[float, float | None]]] = {}
        self._order: list[str] = []
        self._elapsed = 0.0
        self.setToolTip("每个目标一条折线；底部红叉表示该次探测超时")

    # -- 数据 ---------------------------------------------------------------
    def add_sample(self, target: str, elapsed_s: float, rtt_ms: float | None) -> None:
        if target not in self._series:
            self._series[target] = deque(maxlen=self._max_points)
            self._order.append(target)
        self._series[target].append((max(elapsed_s, 0.0), rtt_ms))
        self._elapsed = max(self._elapsed, elapsed_s)
        self.update()

    def clear(self) -> None:
        self._series.clear()
        self._order.clear()
        self._elapsed = 0.0
        self.update()

    def series_names(self) -> list[str]:
        return list(self._order)

    def point_count(self, target: str) -> int:
        return len(self._series.get(target, ()))

    def max_rtt(self) -> float | None:
        values = [rtt for points in self._series.values() for _, rtt in points if rtt is not None]
        return max(values) if values else None

    # -- 绘制 ---------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 接口
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.fillRect(self.rect(), _BG)

        plot = QRectF(
            _LEFT,
            _TOP,
            max(self.width() - _LEFT - _RIGHT, 10.0),
            max(self.height() - _TOP - _BOTTOM, 10.0),
        )
        if not self._order:
            painter.setPen(QPen(_EMPTY_TEXT))
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, "开始探测后这里显示实时延迟趋势")
            return

        y_max = self._y_max()
        self._draw_grid(painter, plot, y_max)
        self._draw_series(painter, plot, y_max)
        self._draw_legend(painter, plot)

    def _y_max(self) -> float:
        peak = self.max_rtt() or 0.0
        # 取整到「好看」的刻度：1/2/5 × 10^n
        target = max(peak * 1.2, 5.0)
        magnitude = 10 ** math.floor(math.log10(target))
        for step in (1, 2, 5, 10):
            if target <= step * magnitude:
                return step * magnitude
        return 10 * magnitude

    def _draw_grid(self, painter: QPainter, plot: QRectF, y_max: float) -> None:
        font = QFont(self.font())
        font.setPointSizeF(9.0)
        painter.setFont(font)
        for index in range(5):
            ratio = index / 4
            y = plot.bottom() - ratio * plot.height()
            painter.setPen(QPen(_GRID, 1.0))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QPen(_AXIS_TEXT))
            value = y_max * ratio
            text = f"{value:.0f}" if value >= 10 else f"{value:.1f}"
            painter.drawText(
                QRectF(0.0, y - 8.0, _LEFT - 8.0, 16.0),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                text,
            )
        painter.setPen(QPen(_AXIS_TEXT))
        painter.drawText(
            QRectF(plot.left(), plot.bottom() + 6.0, 60.0, 16.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "0s",
        )
        painter.drawText(
            QRectF(plot.right() - 60.0, plot.bottom() + 6.0, 60.0, 16.0),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"{self._elapsed:.0f}s",
        )
        painter.drawText(
            QRectF(0.0, 0.0, _LEFT - 8.0, _TOP + 10.0),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            "ms",
        )

    def _draw_series(self, painter: QPainter, plot: QRectF, y_max: float) -> None:
        span = max(self._elapsed, 1.0)
        for index, target in enumerate(self._order):
            color = SERIES_COLORS[index % len(SERIES_COLORS)]
            points = self._series[target]
            polygon = QPolygonF()
            for elapsed, rtt in points:
                if rtt is None:
                    continue
                x = plot.left() + min(elapsed / span, 1.0) * plot.width()
                y = plot.bottom() - min(rtt / y_max, 1.0) * plot.height()
                polygon.append(QPointF(x, y))
            if polygon.size() >= 2:
                painter.setPen(QPen(color, 1.8))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPolyline(polygon)
            painter.setBrush(QBrush(color))
            painter.setPen(Qt.PenStyle.NoPen)
            for point in polygon:
                painter.drawEllipse(point, 2.2, 2.2)

            # 超时点：底部红叉，避免和折线混在一起
            painter.setPen(QPen(_TIMEOUT, 1.4))
            for elapsed, rtt in points:
                if rtt is not None:
                    continue
                x = plot.left() + min(elapsed / span, 1.0) * plot.width()
                y = plot.bottom() - 3.0
                painter.drawLine(QPointF(x - 3, y - 3), QPointF(x + 3, y + 3))
                painter.drawLine(QPointF(x - 3, y + 3), QPointF(x + 3, y - 3))

    def _draw_legend(self, painter: QPainter, plot: QRectF) -> None:
        font = QFont(self.font())
        font.setPointSizeF(9.0)
        painter.setFont(font)
        x = plot.left() + 8.0
        y = plot.top() + 4.0
        for index, target in enumerate(self._order[:8]):
            color = SERIES_COLORS[index % len(SERIES_COLORS)]
            painter.setPen(QPen(color, 2.4))
            painter.drawLine(QPointF(x, y + 7.0), QPointF(x + 14.0, y + 7.0))
            painter.setPen(QPen(_AXIS_TEXT))
            metrics = painter.fontMetrics()
            painter.drawText(QPointF(x + 18.0, y + 11.0), target)
            x += 18.0 + metrics.horizontalAdvance(target) + 16.0
            if x > plot.right() - 60.0:
                break
