"""左栏导航：数据驱动，分组与条目全部来自注册表。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from netops.core.contracts import NavGroup
from netops.ui.icons import COLOR, SIDEBAR, make_icon

if TYPE_CHECKING:  # pragma: no cover
    from netops.core.contracts import ToolPlugin

__all__ = ["Sidebar", "EXPANDED_WIDTH", "COLLAPSED_WIDTH"]

EXPANDED_WIDTH = 220
COLLAPSED_WIDTH = 68

_GROUP_ORDER: dict[str, int] = {
    NavGroup.DIAGNOSTICS[1]: NavGroup.DIAGNOSTICS[2],
    NavGroup.CALCULATORS[1]: NavGroup.CALCULATORS[2],
    NavGroup.CONVERTERS[1]: NavGroup.CONVERTERS[2],
    NavGroup.MONITOR[1]: NavGroup.MONITOR[2],
    NavGroup.VENDOR[1]: NavGroup.VENDOR[2],
    NavGroup.SYSTEM[1]: NavGroup.SYSTEM[2],
}


class Sidebar(QFrame):
    """固定宽度、可收起的左侧导航。"""

    tool_selected = pyqtSignal(str)  # tool_id
    collapsed_changed = pyqtSignal(bool)

    def __init__(self, tools: list[type[ToolPlugin]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(EXPANDED_WIDTH)
        self._collapsed = False
        self._buttons: dict[str, QPushButton] = {}
        self._section_labels: list[QLabel] = []

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_brand())
        root.addWidget(self._build_scroll(tools), 1)
        root.addWidget(self._build_footer())

    # -- 构建 ---------------------------------------------------------------
    def _build_brand(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("SidebarBrandBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 16, 10, 8)
        lay.setSpacing(10)

        mark = QLabel()
        mark.setPixmap(make_icon("dashboard", "#0F766E").pixmap(22, 22))
        mark.setFixedSize(24, 24)
        lay.addWidget(mark)

        brand = QWidget()
        box = QVBoxLayout(brand)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        name = QLabel("NetOps Toolkit")
        name.setObjectName("SidebarBrand")
        sub = QLabel("网络工程师工具箱")
        sub.setObjectName("SidebarBrandSub")
        box.addWidget(name)
        box.addWidget(sub)
        lay.addWidget(brand, 1)
        self._brand = brand
        self._mark = mark

        self.collapse_button = QToolButton()
        self.collapse_button.setObjectName("CollapseButton")
        self.collapse_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.collapse_button.setIcon(make_icon(SIDEBAR, COLOR))
        self.collapse_button.setIconSize(QSize(17, 17))
        self.collapse_button.setToolTip("收起侧边栏（Ctrl+B）")
        self.collapse_button.setShortcut("Ctrl+B")
        self.collapse_button.clicked.connect(self.toggle_collapsed)
        lay.addWidget(self.collapse_button, 0, Qt.AlignmentFlag.AlignTop)
        return bar

    def _build_scroll(self, tools: list[type[ToolPlugin]]) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        inner.setObjectName("SidebarInner")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 4, 0, 12)
        lay.setSpacing(0)

        by_group: dict[str, list[type[ToolPlugin]]] = {}
        for cls in tools:
            by_group.setdefault(cls.group, []).append(cls)

        for group in sorted(by_group, key=lambda g: _GROUP_ORDER.get(g, 999)):
            title = QLabel(group)
            title.setObjectName("NavSection")
            self._section_labels.append(title)
            lay.addWidget(title)
            for cls in by_group[group]:
                lay.addWidget(self._build_item(cls))
        lay.addStretch(1)
        area.setWidget(inner)
        return area

    def _build_item(self, cls: type[ToolPlugin]) -> QPushButton:
        btn = QPushButton(cls.title)
        btn.setObjectName("NavItem")
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(f"{cls.title}\n{cls.subtitle}")
        btn.setIcon(make_icon(getattr(cls, "icon", "ip"), COLOR))
        btn.setIconSize(QSize(17, 17))
        btn.setProperty("fullTitle", cls.title)
        btn.clicked.connect(lambda _checked=False, tid=cls.tool_id: self.select(tid))
        self._group.addButton(btn)
        self._buttons[cls.tool_id] = btn
        return btn

    def _build_footer(self) -> QWidget:
        footer = QWidget()
        footer.setObjectName("SidebarFooter")
        lay = QHBoxLayout(footer)
        lay.setContentsMargins(18, 10, 12, 12)
        lay.setSpacing(8)
        dot = QLabel("●")
        dot.setObjectName("StatusDotUp")
        ver = QLabel("v0.1.0 · P0")
        ver.setObjectName("SidebarVersion")
        lay.addWidget(dot)
        lay.addWidget(ver, 1)
        self._footer = footer
        return footer

    # -- 对外接口 -----------------------------------------------------------
    @property
    def tool_ids(self) -> list[str]:
        return list(self._buttons)

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def button(self, tool_id: str) -> QPushButton:
        return self._buttons[tool_id]

    def select(self, tool_id: str, *, emit: bool = True) -> None:
        btn = self._buttons[tool_id]
        if not btn.isChecked():
            btn.setChecked(True)
        if emit:
            self.tool_selected.emit(tool_id)

    def current_tool_id(self) -> str | None:
        for tid, btn in self._buttons.items():
            if btn.isChecked():
                return tid
        return None

    def set_collapsed(self, collapsed: bool) -> None:
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self.setFixedWidth(COLLAPSED_WIDTH if collapsed else EXPANDED_WIDTH)
        for sec in self._section_labels:
            sec.setVisible(not collapsed)
        self._brand.setVisible(not collapsed)
        self._footer.setVisible(not collapsed)
        self._mark.setVisible(collapsed)
        tip = "展开侧边栏（Ctrl+B）" if collapsed else "收起侧边栏（Ctrl+B）"
        self.collapse_button.setToolTip(tip)
        for btn in self._buttons.values():
            btn.setText("" if collapsed else btn.property("fullTitle"))
        self.collapsed_changed.emit(collapsed)

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed)
