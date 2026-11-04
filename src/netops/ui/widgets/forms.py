"""真实工具页共用的表单与结果组件。

与 ``widgets/common.py`` 的展示型组件互补：这里放可交互控件
（输入框、主按钮、可复制结果行、错误提示条），保证所有工具页视觉一致。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from netops.ui.widgets.common import Card, label

__all__ = [
    "ToolPage",
    "FieldRow",
    "PrimaryButton",
    "SecondaryButton",
    "ResultValue",
    "ResultGrid",
    "InlineError",
    "make_line_edit",
    "make_mono_line_edit",
    "make_text_edit",
    "make_combo",
    "mono_label",
    "copy_to_clipboard",
    "result_card",
    "separator_line",
]


def copy_to_clipboard(text: str) -> None:
    clipboard = QGuiApplication.clipboard()
    if clipboard is not None:
        clipboard.setText(text)


def mono_label(text: str, object_name: str = "ResultMono") -> QLabel:
    lb = label(text, object_name)
    lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lb


class ToolPage(QWidget):
    """真实工具页基类：统一的页面留白 + 标题区 + 内容区。

    子类只需往 ``self.body`` 里塞卡片；标题由插件元数据传入，避免重复维护。

    内容区放在 ``QScrollArea`` 里：卡片高了会自动出现纵向滚动条，而不是把
    内部控件压扁（窗口矮时尤其明显）。``self.body`` 仍指向卡片所在的纵向布局。
    """

    def __init__(
        self,
        *,
        title: str,
        subtitle: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Page")
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 22)
        root.setSpacing(14)

        head = QVBoxLayout()
        head.setSpacing(4)
        head.addWidget(label(title, "ToolPageTitle"))
        sub = label(subtitle, "ToolPageSubtitle")
        sub.setWordWrap(True)
        head.addWidget(sub)
        root.addLayout(head)

        content = QWidget()
        content.setObjectName("ToolPageContent")
        self.body = QVBoxLayout(content)
        self.body.setContentsMargins(0, 0, 8, 0)
        self.body.setSpacing(14)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("ToolPageScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(content)
        root.addWidget(self.scroll, 1)

    def add_card(self, card: QWidget, stretch: int = 0) -> QWidget:
        self.body.addWidget(card, stretch)
        return card


class FieldRow(QWidget):
    """左侧标签 + 右侧输入控件的表单项。

    标签固定宽度、输入控件至少 ``MIN_INPUT_WIDTH``，避免在窄列里被压到只剩边框；
    横向依然不够时由调用方把多个 ``FieldRow`` 分行摆放，而不是压缩输入框。
    """

    #: 输入控件的最小宽度（像素）——低于这个宽度 QSpinBox 的上下箭头会盖住数字。
    MIN_INPUT_WIDTH = 108

    def __init__(
        self,
        label_text: str,
        widget: QWidget,
        *,
        hint: str = "",
        label_width: int = 96,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        name = label(label_text, "FieldLabel")
        name.setFixedWidth(label_width)
        name.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(name)
        widget.setMinimumWidth(self.MIN_INPUT_WIDTH)
        layout.addWidget(widget, 1)
        if hint:
            tip = label(hint, "FieldHint")
            layout.addWidget(tip)


def _line_edit(placeholder: str, object_name: str = "ToolInput") -> QLineEdit:
    edit = QLineEdit()
    edit.setObjectName(object_name)
    edit.setPlaceholderText(placeholder)
    edit.setClearButtonEnabled(True)
    return edit


def make_line_edit(placeholder: str = "") -> QLineEdit:
    return _line_edit(placeholder)


def make_mono_line_edit(placeholder: str = "") -> QLineEdit:
    """等宽字体输入框，适合十六进制 / 二进制这类需要对齐的内容。"""
    return _line_edit(placeholder, "ToolInputMono")


def make_text_edit(placeholder: str = "", *, rows: int = 6) -> QPlainTextEdit:
    """多行等宽输入框，用于批量粘贴网段、SN、IP 列表。"""
    view = QPlainTextEdit()
    view.setObjectName("ToolInput")
    view.setPlaceholderText(placeholder)
    view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    view.setTabChangesFocus(True)
    metrics = view.fontMetrics()
    view.setFixedHeight(metrics.lineSpacing() * rows + 22)
    return view


def make_combo(items: Iterable[str]) -> QComboBox:
    combo = QComboBox()
    combo.setObjectName("ToolInput")
    combo.addItems(list(items))
    return combo


class PrimaryButton(QPushButton):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("PrimaryButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


class SecondaryButton(QPushButton):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("SecondaryButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


class ResultValue(QWidget):
    """一行结果：标题、等宽数值、复制按钮。点击复制按钮写入剪贴板。"""

    copied = pyqtSignal(str)

    def __init__(
        self,
        caption: str,
        value: str = "—",
        *,
        on_copy: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_copy = on_copy
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.caption = label(caption, "ResultCaption")
        self.caption.setFixedWidth(132)
        layout.addWidget(self.caption)

        self.value = mono_label(value, "ResultMono")
        self.value.setWordWrap(True)
        layout.addWidget(self.value, 1)

        self.copy_button = QPushButton("复制")
        self.copy_button.setObjectName("CopyButton")
        self.copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_button.setEnabled(bool(value) and value != "—")
        self.copy_button.clicked.connect(self._copy)
        layout.addWidget(self.copy_button, 0, Qt.AlignmentFlag.AlignRight)

    def set_value(self, value: str) -> None:
        self.value.setText(value)
        self.copy_button.setEnabled(bool(value) and value != "—")

    def text(self) -> str:
        return self.value.text()

    def _copy(self) -> None:
        text = self.value.text()
        if not text or text == "—":
            return
        copy_to_clipboard(text)
        if self._on_copy is not None:
            self._on_copy(text)
        self.copied.emit(text)


class ResultGrid(QWidget):
    """两列或三列的结果网格，适合计算器输出。"""

    def __init__(
        self,
        items: list[tuple[str, str]],
        *,
        columns: int = 2,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(10)
        self._values: dict[str, ResultValue] = {}
        for index, (caption, value) in enumerate(items):
            item = ResultValue(caption, value)
            self._values[caption] = item
            grid.addWidget(item, index // columns, index % columns)
        for column in range(columns):
            grid.setColumnStretch(column, 1)

    def set(self, caption: str, value: str) -> None:
        if caption in self._values:
            self._values[caption].set_value(value)

    def update_values(self, mapping: dict[str, str]) -> None:
        for caption, value in mapping.items():
            self.set(caption, value)

    def value(self, caption: str) -> str:
        return self._values[caption].text()


class InlineError(QLabel):
    """输入错误提示条；无错误时自动隐藏，避免布局抖动。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("InlineError")
        self.setWordWrap(True)
        self.hide()

    def show_error(self, message: str) -> None:
        self.setText(message)
        self.show()

    def clear_error(self) -> None:
        self.setText("")
        self.hide()


def result_card(title: str, hint: str = "") -> Card:
    """统一的结果卡片外观。"""
    card = Card(title=title, hint=hint)
    card.body.setSpacing(10)
    return card


def separator_line() -> QFrame:
    line = QFrame()
    line.setObjectName("ToolSeparator")
    line.setFixedHeight(1)
    return line
