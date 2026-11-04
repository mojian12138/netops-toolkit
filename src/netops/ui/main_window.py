"""主窗口：左导航 + 右工作区 + 底部状态栏。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from netops.core.context import AppContext, create_context
from netops.platform_.os_layer import PY_VERSION, platform_label
from netops.ui.icons import SEARCH, app_icon, make_icon
from netops.ui.sidebar import Sidebar

if TYPE_CHECKING:  # pragma: no cover
    from netops.core.contracts import ToolPlugin

__all__ = ["MainWindow"]

WINDOW_TITLE = "NetOps Toolkit"
PLACEHOLDER_TOOL_ID = "diag.ping"


class MainWindow(QMainWindow):
    """应用主窗口。UI 只通过注册表与 AppContext 取数据，不关心具体工具。"""

    page_changed = pyqtSignal(str)

    def __init__(
        self,
        ctx: AppContext | None = None,
        tools: list[type[ToolPlugin]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.ctx = ctx or create_context()
        if tools is None:
            from netops.core.contracts import REGISTRY

            tools = REGISTRY.tools
        self._plugins: dict[str, type[ToolPlugin]] = {cls.tool_id: cls for cls in tools}

        self.setObjectName("Root")
        self.setWindowTitle(WINDOW_TITLE)
        self.setWindowIcon(app_icon())
        self.resize(1240, 800)
        self.setMinimumSize(980, 660)

        root = QWidget()
        root.setObjectName("Root")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = Sidebar(tools)
        self.sidebar.tool_selected.connect(self._on_tool_selected)
        layout.addWidget(self.sidebar)

        workspace = QWidget()
        workspace.setObjectName("Workspace")
        ws = QVBoxLayout(workspace)
        ws.setContentsMargins(0, 0, 0, 0)
        ws.setSpacing(0)
        self.header = _PageHeader()
        ws.addWidget(self.header)
        self.stack = QStackedWidget()
        self.stack.setObjectName("PageStack")
        ws.addWidget(self.stack, 1)
        layout.addWidget(workspace, 1)

        self.setCentralWidget(root)
        self._build_status_bar()
        self._pages: dict[str, QWidget] = {}
        for tool_id in self._plugins:
            self._ensure_page(tool_id)

        QShortcut(QKeySequence("Ctrl+K"), self, activated=self.header.focus_search)
        QShortcut(QKeySequence.StandardKey.Find, self, activated=self.header.focus_search)

        if tools:
            first = (
                PLACEHOLDER_TOOL_ID if PLACEHOLDER_TOOL_ID in self._plugins else tools[0].tool_id
            )
            self.show_tool(first)

    # -- 页面管理 -----------------------------------------------------------
    def _ensure_page(self, tool_id: str) -> QWidget:
        if tool_id not in self._pages:
            plugin = self._plugins[tool_id]
            page = plugin.create_widget(self.ctx)
            page.setObjectName("Page")
            self._pages[tool_id] = page
            self.stack.addWidget(page)
        return self._pages[tool_id]

    def show_tool(self, tool_id: str) -> None:
        page = self._ensure_page(tool_id)
        self.stack.setCurrentWidget(page)
        self.sidebar.select(tool_id, emit=False)
        plugin = self._plugins[tool_id]
        self.header.set_page(plugin.group, plugin.title, plugin.stage.code)
        self.status_target.setText(f"{plugin.title} · {tool_id}")
        self.page_changed.emit(tool_id)

    @property
    def current_tool_id(self) -> str | None:
        widget = self.stack.currentWidget()
        for tid, page in self._pages.items():
            if page is widget:
                return tid
        return None

    @property
    def loaded_page_count(self) -> int:
        return len(self._pages)

    def page(self, tool_id: str) -> QWidget:
        return self._pages[tool_id]

    def _on_tool_selected(self, tool_id: str) -> None:
        self.show_tool(tool_id)

    # -- 状态栏 -------------------------------------------------------------
    def _build_status_bar(self) -> None:
        bar = QStatusBar()
        bar.setSizeGripEnabled(False)
        bar.setContentsMargins(12, 0, 12, 0)
        self.setStatusBar(bar)

        self.status_engine = QLabel("● 监控引擎未启动")
        self.status_engine.setObjectName("StatusDotIdle")
        self.status_target = QLabel("—")
        self.status_target.setObjectName("StatusLabel")
        self.status_phase = QLabel("P0 · 可视化外壳")
        self.status_phase.setObjectName("StatusLabel")
        self.status_platform = QLabel(f"{platform_label()} · Python {PY_VERSION}")
        self.status_platform.setObjectName("StatusLabel")

        bar.addWidget(self.status_engine)
        bar.addWidget(_separator())
        bar.addWidget(self.status_target)
        bar.addPermanentWidget(self.status_phase)
        bar.addPermanentWidget(_separator())
        bar.addPermanentWidget(self.status_platform)


class _PageHeader(QFrame):
    """工作区顶部：面包屑 + 页面标题 + 搜索占位。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PageHeader")
        self.setFixedHeight(72)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(26, 12, 26, 12)
        lay.setSpacing(12)

        text = QVBoxLayout()
        text.setSpacing(2)
        self.crumb = QLabel("工具箱 / —")
        self.crumb.setObjectName("PageCrumb")
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self.title = QLabel("—")
        self.title.setObjectName("PageTitle")
        self.stage = QLabel("P0")
        self.stage.setObjectName("BadgePrimary")
        title_row.addWidget(self.title)
        title_row.addWidget(self.stage, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addStretch(1)
        text.addWidget(self.crumb)
        text.addLayout(title_row)
        lay.addLayout(text, 1)

        self.search = QPushButton("搜索工具或功能")
        self.search.setObjectName("TopBarSearch")
        self.search.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search.setIcon(make_icon(SEARCH, "#94A3B8"))
        self.search.setIconSize(QSize(14, 14))
        self.search.setToolTip("全局搜索将在 P2 开放")
        lay.addWidget(self.search, 0, Qt.AlignmentFlag.AlignVCenter)

        kbd = QLabel("Ctrl K")
        kbd.setObjectName("TopBarKbd")
        lay.addWidget(kbd, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_page(self, group: str, title: str, stage_code: str) -> None:
        self.crumb.setText(f"工具箱 / {group}")
        self.title.setText(title)
        self.stage.setText(stage_code)

    def focus_search(self) -> None:
        self.search.setFocus()


def _separator() -> QLabel:
    sep = QLabel("│")
    sep.setObjectName("StatusSeparator")
    return sep
