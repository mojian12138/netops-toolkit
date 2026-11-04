"""占位页与仪表盘共用的可复用小部件。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from netops.ui.icons import make_icon

__all__ = [
    "HLine",
    "VLine",
    "Card",
    "HeroCard",
    "Badge",
    "FeatureList",
    "CodeBlock",
    "MetricTile",
    "Notice",
    "add_shadow",
]


def label(text: str, object_name: str, parent: QWidget | None = None) -> QLabel:
    lb = QLabel(text, parent)
    lb.setObjectName(object_name)
    return lb


def add_shadow(widget: QWidget, blur: int = 22, alpha: int = 22, dy: int = 4) -> None:
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, dy)
    effect.setColor(QColor(15, 23, 42, alpha))
    widget.setGraphicsEffect(effect)


def HLine(parent: QWidget | None = None) -> QFrame:
    line = QFrame(parent)
    line.setFixedHeight(1)
    line.setStyleSheet("background-color: #EEF2F7; border: none;")
    return line


def VLine(parent: QWidget | None = None) -> QFrame:
    line = QFrame(parent)
    line.setFixedWidth(1)
    line.setStyleSheet("background-color: #EEF2F7; border: none;")
    return line


class Card(QFrame):
    """白底圆角卡片，可选标题行。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str = "",
        hint: str = "",
        padding: int = 18,
        spacing: int = 12,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(padding, padding, padding, padding)
        self.body.setSpacing(spacing)

        if title:
            head = QHBoxLayout()
            head.setSpacing(8)
            head.addWidget(label(title, "CardTitle"))
            head.addStretch(1)
            if hint:
                head.addWidget(label(hint, "CardHint"))
            self.body.addLayout(head)


class HeroCard(QFrame):
    """页面顶部的大标题卡片：阶段徽章 + 主副标题 + 特性要点。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str,
        subtitle: str,
        stage_code: str,
        stage_text: str,
        features: list[str],
    ) -> None:
        super().__init__(parent)
        self.setObjectName("HeroCard")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        accent = QFrame()
        accent.setObjectName("HeroAccent")
        accent.setFixedWidth(4)
        outer.addWidget(accent)

        body = QVBoxLayout()
        body.setContentsMargins(22, 20, 22, 20)
        body.setSpacing(10)
        outer.addLayout(body, 1)

        head = QHBoxLayout()
        head.setSpacing(10)
        title_label = label(title, "HeroTitle")
        title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        head.addWidget(title_label)
        head.addStretch(1)
        head.addWidget(Badge(f"规划阶段 · {stage_code}", "BadgeOutline"))
        head.addWidget(Badge(stage_text, "StagePill"))
        body.addLayout(head)

        sub = label(subtitle, "HeroSubtitle")
        sub.setWordWrap(True)
        body.addWidget(sub)

        body.addSpacing(4)
        body.addWidget(FeatureList(features))
        add_shadow(self, blur=26, alpha=18, dy=6)


class FeatureList(QWidget):
    """带序号圆点的特性清单。"""

    def __init__(self, features: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        for index, feature in enumerate(features, start=1):
            row = QHBoxLayout()
            row.setSpacing(10)
            idx = label(f"{index:02d}", "FeatureIndex")
            idx.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(idx, 0, Qt.AlignmentFlag.AlignTop)
            text = label(feature, "FeatureRow")
            text.setWordWrap(True)
            row.addWidget(text, 1)
            layout.addLayout(row)


class Badge(QLabel):
    def __init__(
        self, text: str, object_name: str = "Badge", parent: QWidget | None = None
    ) -> None:
        super().__init__(text, parent)
        self.setObjectName(object_name)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


class MetricTile(QFrame):
    """仪表盘上的小指标块。"""

    def __init__(self, value: str, caption: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Metric")
        self.setStyleSheet(
            "QFrame#Metric { background-color: #F8FAFC; border: 1px solid #EEF2F7; border-radius: 10px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(2)
        layout.addWidget(label(value, "MetricValue"))
        layout.addWidget(label(caption, "MetricLabel"))


class Notice(QLabel):
    """一条淡色提示条。"""

    def __init__(self, text: str, *, muted: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("NoticeMuted" if muted else "Notice")
        self.setWordWrap(True)


class CodeBlock(QFrame):
    """只读代码预览：深色底 + 标题栏 + 行号，给后续脚本编辑区定调。"""

    def __init__(
        self,
        code: str,
        parent: QWidget | None = None,
        *,
        filename: str = "startup.cfg",
        language: str = "",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CodeBlock")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("CodeBlockHeader")
        header.setFixedHeight(34)
        hbox = QHBoxLayout(header)
        hbox.setContentsMargins(12, 0, 12, 0)
        hbox.setSpacing(6)
        for color in ("#F87171", "#FBBF24", "#34D399"):
            dot = QLabel()
            dot.setFixedSize(9, 9)
            dot.setStyleSheet(f"background-color: {color}; border-radius: 4px;")
            hbox.addWidget(dot)
        hbox.addSpacing(8)
        hbox.addWidget(label(filename, "CodeBlockTitle"))
        hbox.addStretch(1)
        if language:
            hbox.addWidget(label(language, "CodeBlockQ"))
        layout.addWidget(header)

        layout.addWidget(_CodeView(code))

        add_shadow(self, blur=18, alpha=26, dy=5)


class _CodeView(QWidget):
    def __init__(self, code: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        view = QPlainTextEdit(self)
        view.setReadOnly(True)
        view.setPlainText(code)
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        view.setFrameShape(QFrame.Shape.NoFrame)
        view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        view.setCursorWidth(0)
        view.setMaximumHeight(self._height_for(code))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(view)

    @staticmethod
    def _height_for(code: str) -> int:
        lines = max(code.count("\n") + 1, 3)
        return min(lines * 19 + 26, 420)


def icon_label(name: str, color: str, size: int = 16, parent: QWidget | None = None) -> QLabel:
    lb = QLabel(parent)
    lb.setPixmap(make_icon(name, color).pixmap(size, size))
    return lb
