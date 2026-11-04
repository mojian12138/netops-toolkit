"""UI 图标：全部用代码绘制，不依赖 svg / QtSvg 插件，打包时零额外依赖。"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

__all__ = [
    "COLOR",
    "make_icon",
    "app_icon",
    "DASHBOARD",
    "PING",
    "SCAN",
    "HOSTS",
    "ROUTE",
    "IP",
    "SUBNET",
    "BASE",
    "AGGREGATE",
    "SPLIT",
    "CONTAINS",
    "MAC",
    "ALERT",
    "SEND",
    "AP",
    "SCRIPT",
    "WARRANTY",
    "SETTINGS",
    "SIDEBAR",
    "SEARCH",
]

COLOR = "#475569"
_SIZE = 20


def _p(x: float, y: float) -> QPointF:
    return QPointF(x, y)


def _stroke(p: QPainter, color: QColor, width: float = 1.6) -> None:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)


def _dot(p: QPainter, x: float, y: float, r: float, color: QColor) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawEllipse(_p(x, y), r, r)


def _draw_dashboard(p: QPainter, c: QColor) -> None:
    _stroke(p, c)
    for x, y in ((3, 3), (12, 3), (3, 12), (12, 12)):
        p.drawRoundedRect(QRectF(x, y, 6, 6), 1.6, 1.6)


def _draw_ping(p: QPainter, c: QColor) -> None:
    _stroke(p, c)
    p.drawPolyline(
        [_p(2.5, 12), _p(6.5, 12), _p(8.5, 5.5), _p(11.5, 16.5), _p(13.5, 12), _p(17.5, 12)]
    )


def _draw_scan(p: QPainter, c: QColor) -> None:
    _stroke(p, c)
    p.drawLine(_p(3, 10), _p(17, 10))
    p.drawArc(QRectF(3, 3, 14, 14), 30 * 16, 300 * 16)
    _dot(p, 10, 10, 1.7, c)


def _draw_hosts(p: QPainter, c: QColor) -> None:
    _stroke(p, c)
    p.drawLine(_p(10, 3), _p(10, 17))
    p.drawLine(_p(3.5, 7), _p(16.5, 7))
    p.drawLine(_p(3.5, 13), _p(16.5, 13))
    p.drawLine(_p(10, 3), _p(3.5, 7))
    p.drawLine(_p(10, 3), _p(16.5, 7))
    p.drawLine(_p(3.5, 13), _p(10, 17))
    p.drawLine(_p(16.5, 13), _p(10, 17))
    p.drawLine(_p(3.5, 7), _p(3.5, 13))
    p.drawLine(_p(16.5, 7), _p(16.5, 13))


def _draw_route(p: QPainter, c: QColor) -> None:
    _stroke(p, c)
    for x, y, r in ((3.5, 16, 2.2), (16.5, 4, 2.2)):
        p.drawEllipse(_p(x, y), r, r)
    p.drawPolyline([_p(5.4, 14.4), _p(8, 10.5), _p(12, 9.5), _p(14.6, 5.6)])


def _draw_ip(p: QPainter, c: QColor) -> None:
    _stroke(p, c)
    p.drawRoundedRect(QRectF(2.6, 3, 14.8, 14), 3.4, 3.4)
    p.drawLine(_p(10, 6.6), _p(10, 13.4))
    _dot(p, 10, 6.6, 1.2, c)
    _dot(p, 10, 13.4, 1.2, c)


def _draw_subnet(p: QPainter, c: QColor) -> None:
    _stroke(p, c)
    c_ = QRectF(2.6, 8.6, 14.8, 8.4)
    p.drawRoundedRect(c_, 3.4, 3.4)
    p.drawLine(_p(2.6, 12.8), _p(17.4, 12.8))
    _dot(p, 8, 10.7, 1, c)
    _dot(p, 12, 15, 1, c)


def _draw_base(p: QPainter, c: QColor) -> None:
    _stroke(p, c)
    p.drawPolyline([_p(4, 7), _p(10, 3.5), _p(16, 7)])
    p.drawPolyline([_p(4, 12.5), _p(10, 9), _p(16, 12.5)])
    p.drawPolyline([_p(4, 17), _p(10, 13.5), _p(16, 17)])


def _draw_aggregate(p: QPainter, c: QColor) -> None:
    _stroke(p, c)
    p.drawLine(_p(3.5, 5), _p(8, 5))
    p.drawLine(_p(3.5, 10), _p(8, 10))
    p.drawLine(_p(3.5, 15), _p(8, 15))
    p.drawPolyline([_p(8, 5), _p(12, 10), _p(8, 15)])
    p.drawLine(_p(12, 10), _p(16.5, 10))


def _draw_split(p: QPainter, c: QColor) -> None:
    _stroke(p, c)
    p.drawLine(_p(3, 10), _p(8, 10))
    p.drawPolyline([_p(8, 10), _p(12, 5), _p(17, 5)])
    p.drawPolyline([_p(8, 10), _p(12, 15), _p(17, 15)])


def _draw_contains(p: QPainter, c: QColor) -> None:
    _stroke(p, c)
    p.drawRoundedRect(QRectF(2.6, 2.6, 14.8, 14.8), 3.6, 3.6)
    p.drawArc(QRectF(5.8, 5.8, 8.4, 8.4), 0, 360 * 16)
    _dot(p, 10, 10, 1.6, c)


def _draw_mac(p: QPainter, c: QColor) -> None:
    _stroke(p, c)
    p.drawLine(_p(5, 4), _p(5, 16))
    p.drawLine(_p(15, 4), _p(15, 16))
    p.drawPolyline([_p(3.6, 6), _p(5, 4), _p(6.4, 6)])
    p.drawPolyline([_p(13.6, 6), _p(15, 4), _p(16.4, 6)])
    for x in (8.9, 10.8):
        for y in (7, 10.2, 13.4):
            p.drawPoint(_p(x, y))


def _draw_alert(p: QPainter, c: QColor) -> None:
    _stroke(p, c)
    path = QPainterPath()
    path.moveTo(10, 3)
    path.lineTo(17.2, 16)
    path.lineTo(2.8, 16)
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(_p(10, 8.2), _p(10, 11.4))
    _dot(p, 10, 13.7, 1.1, c)


def _draw_send(p: QPainter, c: QColor) -> None:
    _stroke(p, c)
    p.drawPolyline([_p(3, 5.5), _p(17, 10), _p(3, 14.5), _p(5, 10), _p(3, 5.5)])
    p.drawLine(_p(5, 10), _p(11.5, 10))


def _draw_ap(p: QPainter, c: QColor) -> None:
    _stroke(p, c)
    p.drawRoundedRect(QRectF(8, 11.6, 4, 5.4), 1.2, 1.2)
    p.drawLine(_p(10, 11.6), _p(10, 8.4))
    for r in (3.4, 6.6):
        p.drawArc(QRectF(10 - r, 8.4 - r, r * 2, r * 2), 35 * 16, 110 * 16)


def _draw_script(p: QPainter, c: QColor) -> None:
    _stroke(p, c)
    p.drawPolyline([_p(7, 6.5), _p(3.2, 10), _p(7, 13.5)])
    p.drawPolyline([_p(13, 6.5), _p(16.8, 10), _p(13, 13.5)])
    p.drawLine(_p(11.4, 4.2), _p(8.6, 15.8))


def _draw_warranty(p: QPainter, c: QColor) -> None:
    _stroke(p, c)
    path = QPainterPath()
    path.moveTo(10, 2.6)
    path.lineTo(16.5, 5)
    path.lineTo(16.5, 10)
    path.cubicTo(16.5, 13.6, 13.6, 16.2, 10, 17.4)
    path.cubicTo(6.4, 16.2, 3.5, 13.6, 3.5, 10)
    path.lineTo(3.5, 5)
    path.closeSubpath()
    p.drawPath(path)
    p.drawPolyline([_p(7.2, 9.9), _p(9.3, 12), _p(12.9, 7.7)])


def _draw_search(p: QPainter, c: QColor) -> None:
    _stroke(p, c)
    p.drawEllipse(_p(9, 9), 5.2, 5.2)
    p.drawLine(_p(12.9, 12.9), _p(16.8, 16.8))


def _draw_sidebar(p: QPainter, c: QColor) -> None:
    _stroke(p, c)
    p.drawRoundedRect(QRectF(2.8, 3.4, 14.4, 13.2), 2.6, 2.6)
    p.drawLine(_p(8, 3.4), _p(8, 16.6))
    p.drawLine(_p(4.8, 7.2), _p(6.2, 7.2))


def _draw_settings(p: QPainter, c: QColor) -> None:
    _stroke(p, c)
    for y in (5.5, 10, 14.5):
        p.drawLine(_p(3, y), _p(17, y))
    for x, y in ((7, 5.5), (13.5, 10), (6, 14.5)):
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(_p(x, y), 1.9, 1.9)
    p.setBrush(Qt.BrushStyle.NoBrush)


DRAWERS = {
    "dashboard": _draw_dashboard,
    "ping": _draw_ping,
    "scan": _draw_scan,
    "hosts": _draw_hosts,
    "route": _draw_route,
    "ip": _draw_ip,
    "subnet": _draw_subnet,
    "base": _draw_base,
    "aggregate": _draw_aggregate,
    "split": _draw_split,
    "contains": _draw_contains,
    "mac": _draw_mac,
    "alert": _draw_alert,
    "send": _draw_send,
    "ap": _draw_ap,
    "script": _draw_script,
    "warranty": _draw_warranty,
    "settings": _draw_settings,
    "sidebar": _draw_sidebar,
    "search": _draw_search,
}

DASHBOARD = "dashboard"
PING = "ping"
SCAN = "scan"
HOSTS = "hosts"
ROUTE = "route"
IP = "ip"
SUBNET = "subnet"
BASE = "base"
AGGREGATE = "aggregate"
SPLIT = "split"
CONTAINS = "contains"
MAC = "mac"
ALERT = "alert"
SEND = "send"
AP = "ap"
SCRIPT = "script"
WARRANTY = "warranty"
SETTINGS = "settings"
SIDEBAR = "sidebar"
SEARCH = "search"


#: 绘图坐标系宽度（所有 ``_draw_*`` 都按 20×20 设计）。
_GRID = float(_SIZE)

#: 图标线条距画布边缘至少留 1 个单位，否则 17px 缩略时圆头笔帽会被裁掉。
_PADDING = 1.0


def _paint(name: str, color: str, size: int) -> QPixmap:
    """按目标逻辑尺寸绘制图标。

    关键点：**不设置** ``devicePixelRatio``。QIcon 会按请求的 ``iconSize``
    自行挑最接近的 pixmap 并做高分屏缩放；一旦这里把 DPR 写死成 2，
    一张逻辑 20×20 的图在 17×17 的图标槽里就会被裁掉一角。
    因此这里直接按像素尺寸画满，多档尺寸由 ``make_icon`` 分别提供。
    """
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    inner = (_GRID - 2 * _PADDING) / _GRID
    painter.scale(size / _GRID, size / _GRID)
    painter.translate(_PADDING, _PADDING)
    painter.scale(inner, inner)
    DRAWERS[name](painter, QColor(color))
    painter.end()
    return pm


#: QIcon 需要多档位图，否则 Qt 只能拿一张缩放，17px 档位线条会糊。
_ICON_SIZES = (16, 20, 24, 32, 40, 64)


def make_icon(name: str, color: str = COLOR) -> QIcon:
    icon = QIcon()
    for size in _ICON_SIZES:
        icon.addPixmap(_paint(name, color, size), QIcon.Mode.Normal, QIcon.State.Off)
        icon.addPixmap(_paint(name, "#94A3B8", size), QIcon.Mode.Disabled, QIcon.State.Off)
    return icon


def app_icon() -> QIcon:
    """应用图标：品牌色菱形 + 中心点。"""
    icon = QIcon()
    for size in (16, 32, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.scale(size / 22, size / 22)
        _stroke(p, QColor("#0E3A4F"), 1.4)
        p.setBrush(QColor("#0F766E"))
        path = QPainterPath()
        path.moveTo(_p(11, 2.6))
        path.lineTo(_p(19, 11))
        path.lineTo(_p(11, 19.4))
        path.lineTo(_p(3, 11))
        path.closeSubpath()
        p.drawPath(path)
        _dot(p, 11, 11, 2.4, QColor("#FFFFFF"))
        p.end()
        icon.addPixmap(pm)
    return icon
