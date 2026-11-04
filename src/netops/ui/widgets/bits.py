"""掩码位图：把 32 位前缀画成 4 组×8 位的格子。

网络位用主题色实心格，主机位用浅灰空心格，格下标注各字节的十进制值。
纯绘制组件，不消耗模型数据，``set_prefix`` 即可刷新。
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

__all__ = ["MaskBitmap"]

_NET_FILL = QColor("#0f766e")
_NET_TEXT = QColor("#ffffff")
_HOST_FILL = QColor("#f1f5f9")
_HOST_TEXT = QColor("#94a3b8")
_BORDER = QColor("#e2e8f0")
_LABEL_TEXT = QColor("#64748b")
_GROUP_GAP = 12
_CELL_GAP = 3
_MARGIN = 2


class MaskBitmap(QWidget):
    """32 位掩码位图；``prefix`` 之前为网络位。"""

    def __init__(self, parent: QWidget | None = None, *, prefix: int = 24) -> None:
        super().__init__(parent)
        self._prefix = prefix
        self.setObjectName("MaskBitmap")
        self.setMinimumHeight(74)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip("实心为网络位（1），浅色为主机位（0）")

    # -- 数据 ---------------------------------------------------------------
    @property
    def prefix(self) -> int:
        return self._prefix

    def set_prefix(self, prefix: int) -> None:
        prefix = max(0, min(32, prefix))
        if prefix != self._prefix:
            self._prefix = prefix
            self.update()

    # -- 绘制 ---------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 接口
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        cell_h = 28.0
        label_h = 18.0
        total_w = self.width() - 2 * _MARGIN
        cell_w = (total_w - 3 * _GROUP_GAP) / 32
        cell_w = max(cell_w, 4.0)

        bit_font = QFont(self.font())
        bit_font.setPointSizeF(9.0)
        bit_font.setBold(True)
        label_font = QFont(self.font())
        label_font.setPointSizeF(9.0)

        for index in range(32):
            group, offset = divmod(index, 8)
            x = _MARGIN + group * (8 * cell_w + _GROUP_GAP) + offset * cell_w
            rect = QRectF(x, 0.0, cell_w - _CELL_GAP, cell_h)
            is_network = index < self._prefix
            painter.setPen(QPen(QBrush(_BORDER if not is_network else _NET_FILL), 1.0))
            painter.setBrush(QBrush(_NET_FILL if is_network else _HOST_FILL))
            painter.drawRoundedRect(rect, 4.0, 4.0)

            if cell_w >= 13:
                painter.setFont(bit_font)
                painter.setPen(QPen(_NET_TEXT if is_network else _HOST_TEXT))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "1" if is_network else "0")

            painter.setFont(label_font)
            painter.setPen(QPen(_LABEL_TEXT))
            painter.drawText(
                QRectF(x, cell_h + 1.0, cell_w - _CELL_GAP, label_h),
                Qt.AlignmentFlag.AlignCenter,
                str(self._octet(self._prefix_mask(), group)),
            )
        painter.end()

    def sizeHint(self):
        from PyQt6.QtCore import QSize

        return QSize(560, 74)

    # -- 辅助 ---------------------------------------------------------------
    @staticmethod
    def _octet(mask_value: int, group: int) -> int:
        return (mask_value >> (24 - group * 8)) & 0xFF

    def _prefix_mask(self) -> int:
        if self._prefix == 0:
            return 0
        return ((1 << self._prefix) - 1) << (32 - self._prefix)
