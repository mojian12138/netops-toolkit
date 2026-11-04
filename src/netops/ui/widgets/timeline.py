"""路由追踪的竖向时间轴：QPainter 自绘，不引入图表依赖。

每一跳一行：左侧跳号圆圈 + 竖向连线，右侧是地址 / 设备备注 / 延迟条。
延迟条按本段路径的最大 RTT 归一化，超时跳画成空心红圈并把连线改成虚线，
一眼能看出「哪一段断了」。
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

__all__ = ["HopTimeline"]

_LINE = QColor("#cbd5e1")
_DASH_LINE = QColor("#e2e8f0")
_NODE = QColor("#0f766e")
_NODE_TIMEOUT = QColor("#dc2626")
_NODE_KNOWN = QColor("#2563eb")
_BAR = QColor("#99f6e4")
_BAR_TIMEOUT = QColor("#fee2e2")
_TEXT = QColor("#0f172a")
_SUB = QColor("#64748b")
_MUTED = QColor("#94a3b8")
_BG = QColor("#ffffff")

#: 左侧留给跳号与圆圈的宽度。
_GUTTER = 46.0
#: 行高：够放两行文字（地址 + 备注）。
_ROW_H = 52.0
_PAD_TOP = 8.0
_PAD_BOTTOM = 8.0
_NODE_R = 9.0


class HopTimeline(QWidget):
    """路由路径的竖向时间轴。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("HopTimeline")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._hops: list[object] = []
        self.setToolTip("圆圈颜色：青色=普通路由，蓝色=已备注设备，红色空心=该跳超时")

    # -- 数据 ---------------------------------------------------------------
    def set_hops(self, hops) -> None:
        """hops: ``traceroute.HopInfo`` 序列（鸭子类型，避免 ui 依赖 services 具体类）。"""
        self._hops = list(hops)
        self.setMinimumHeight(self._content_height())
        self.update()

    def clear(self) -> None:
        self._hops = []
        self.setMinimumHeight(self._content_height())
        self.update()

    def hop_count(self) -> int:
        return len(self._hops)

    def _content_height(self) -> int:
        return int(_PAD_TOP + _ROW_H * max(len(self._hops), 1) + _PAD_BOTTOM)

    # -- 绘制 ---------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 接口
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), _BG)

        if not self._hops:
            self._paint_empty(painter)
            painter.end()
            return

        max_rtt = max(
            (item.rtt_ms for item in self._hops if item.rtt_ms is not None),
            default=0.0,
        )
        bar_left = _GUTTER + 150.0
        bar_width = max(self.width() - bar_left - 76.0, 40.0)

        for index, item in enumerate(self._hops):
            top = _PAD_TOP + _ROW_H * index
            centre_y = top + _ROW_H / 2
            self._paint_connector(painter, index, centre_y, item)
            self._paint_node(painter, centre_y, item)
            self._paint_hop_no(painter, item, centre_y)
            self._paint_text(painter, item, top, bar_left)
            self._paint_bar(painter, item, centre_y, bar_left, bar_width, max_rtt)

        painter.end()

    def _paint_empty(self, painter: QPainter) -> None:
        painter.setPen(QPen(_MUTED))
        font = QFont(self.font())
        font.setPointSizeF(12.0)
        painter.setFont(font)
        painter.drawText(
            QRectF(0, 0, self.width(), _ROW_H * 2),
            Qt.AlignmentFlag.AlignCenter,
            "填写目标后点「开始追踪」，这里会画出逐跳路径",
        )

    def _paint_connector(self, painter: QPainter, index: int, centre_y: float, item) -> None:
        x = _GUTTER / 2 + 6.0
        start_y = centre_y + _NODE_R + 3
        end_top = _PAD_TOP + _ROW_H * (index + 1) + _ROW_H / 2 - _NODE_R - 3
        end_y = end_top if index + 1 < len(self._hops) else centre_y + _NODE_R + 3
        if end_y <= start_y:
            return
        pen = QPen(_LINE if not item.timed_out else _DASH_LINE, 1.6)
        if item.timed_out:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QPointF(x, start_y), QPointF(x, end_y))

    def _paint_node(self, painter: QPainter, centre_y: float, item) -> None:
        x = _GUTTER / 2 + 6.0
        rect = QRectF(x - _NODE_R, centre_y - _NODE_R, _NODE_R * 2, _NODE_R * 2)
        if item.timed_out:
            painter.setPen(QPen(_NODE_TIMEOUT, 1.8))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(rect)
            # 空心圈里画个星号，和终端输出对应
            pen = QPen(_NODE_TIMEOUT, 1.6)
            painter.setPen(pen)
            painter.drawLine(QPointF(x - 3.5, centre_y - 3.5), QPointF(x + 3.5, centre_y + 3.5))
            painter.drawLine(QPointF(x - 3.5, centre_y + 3.5), QPointF(x + 3.5, centre_y - 3.5))
            return
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_NODE_KNOWN if item.name else _NODE)
        painter.drawEllipse(rect)

    def _paint_hop_no(self, painter: QPainter, item, centre_y: float) -> None:
        font = QFont(self.font())
        font.setPointSizeF(10.5)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(_SUB))
        painter.drawText(
            QRectF(0, centre_y - 10, _GUTTER - 12, 20),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            str(item.hop),
        )

    def _paint_text(self, painter: QPainter, item, top: float, bar_left: float) -> None:
        text_width = bar_left - _GUTTER - 12.0
        bold = QFont(self.font())
        bold.setPointSizeF(11.5)
        bold.setBold(True)
        painter.setFont(bold)
        painter.setPen(QPen(_NODE_TIMEOUT if item.timed_out else _TEXT))
        painter.drawText(
            QRectF(_GUTTER, top + 7, text_width, 20),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            _elide(painter, item.display, text_width),
        )

        sub = QFont(self.font())
        sub.setPointSizeF(10.0)
        painter.setFont(sub)
        painter.setPen(QPen(_SUB))
        painter.drawText(
            QRectF(_GUTTER, top + 26, text_width, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            _elide(painter, item.remark or "未备注设备", text_width),
        )

    def _paint_bar(
        self,
        painter: QPainter,
        item,
        centre_y: float,
        bar_left: float,
        bar_width: float,
        max_rtt: float,
    ) -> None:
        height = 8.0
        top = centre_y - height / 2
        track = QRectF(bar_left, top, bar_width, height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#f1f5f9"))
        painter.drawRoundedRect(track, height / 2, height / 2)

        if item.timed_out or item.rtt_ms is None:
            painter.setBrush(_BAR_TIMEOUT)
            painter.drawRoundedRect(track, height / 2, height / 2)
            label = "超时"
            colour = _NODE_TIMEOUT
        else:
            ratio = 0.0 if max_rtt <= 0 else min(item.rtt_ms / max_rtt, 1.0)
            filled = QRectF(bar_left, top, max(bar_width * ratio, 4.0), height)
            painter.setBrush(_BAR)
            painter.drawRoundedRect(filled, height / 2, height / 2)
            label = f"{item.rtt_ms:.1f} ms"
            colour = _SUB

        font = QFont(self.font())
        font.setPointSizeF(9.5)
        painter.setFont(font)
        painter.setPen(QPen(colour))
        painter.drawText(
            QRectF(bar_left + bar_width + 8, centre_y - 9, 68, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            label,
        )


def _elide(painter: QPainter, text: str, width: float) -> str:
    """按可用宽度截断，避免长备注把延迟条挤断。"""
    metrics: QFontMetrics = painter.fontMetrics()
    return metrics.elidedText(text, Qt.TextElideMode.ElideRight, int(max(width, 20)))
