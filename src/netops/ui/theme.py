"""主题加载与字体装配。"""

from __future__ import annotations

from importlib import resources

from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication

from netops.platform_.os_layer import display_font_family

__all__ = ["STYLES_PACKAGE", "load_stylesheet", "mono_font_family", "apply_theme"]

STYLES_PACKAGE = "netops.ui.resources.styles"
_STYLE_FILE = "light.qss"

# QSS 里引用不存在的字体会让 Qt 扫描整个字体表，这里只挑选本机真正装了等宽字体。
_MONO_CANDIDATES = ("Menlo", "Monaco", "SF Mono", "Cascadia Mono", "Consolas", "DejaVu Sans Mono")


def mono_font_family() -> str:
    available = set(QFontDatabase.families())
    for family in _MONO_CANDIDATES:
        if family in available:
            return family
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()


def load_stylesheet(name: str = _STYLE_FILE) -> str:
    return resources.files(STYLES_PACKAGE).joinpath(name).read_text(encoding="utf-8")


def apply_theme(app: QApplication) -> None:
    font = QFont(display_font_family())
    font.setPointSizeF(13.0)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(font)
    app.setStyleSheet(load_stylesheet().replace("@MONO_FONT@", mono_font_family()))
