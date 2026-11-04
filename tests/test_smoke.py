"""冒烟测试：验证注册表、导航、占位页与主题在无显示器环境下都能正常工作。"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import qInstallMessageHandler
from PyQt6.QtWidgets import QApplication

from netops.app import _application, _load_plugins
from netops.core.context import create_context
from netops.core.contracts import REGISTRY
from netops.ui.main_window import MainWindow
from netops.ui.pages.placeholder_page import PlaceholderPage
from netops.ui.theme import apply_theme

EXPECTED_TOOL_IDS = [
    "diag.ping",
    "diag.portscan",
    "diag.ipscan",
    "diag.traceroute",
    "calc.ip",
    "calc.mask",
    "calc.base",
    "calc.aggregate",
    "calc.split",
    "calc.contains",
    "conv.mac",
    "monitor.dashboard",
    "monitor.targets",
    "monitor.channels",
    "monitor.queue",
    "h3c.ap",
    "h3c.scripts",
    "h3c.common",
    "h3c.warranty",
    "sys.settings",
]

EXPECTED_GROUPS = ["网络诊断", "网络计算", "格式转换", "监控告警", "H3C 专属", "系统设置"]

PREVIEW_WITH_CODE = {
    "monitor.targets",
    "monitor.channels",
    "h3c.common",
    "sys.settings",
}

# 已落地真实页面的工具；随着实现推进逐项加入。
IMPLEMENTED_TOOL_IDS: set[str] = {
    "calc.ip",
    "calc.mask",
    "calc.base",
    "calc.aggregate",
    "calc.split",
    "calc.contains",
    "conv.mac",
    "diag.ping",
    "diag.portscan",
    "diag.ipscan",
    "diag.traceroute",
    "h3c.ap",
    "h3c.scripts",
}


@pytest.fixture(scope="session")
def app() -> QApplication:
    """一个进程只允许存在一个 QApplication。"""
    application = _application(["pytest"])
    _load_plugins()
    apply_theme(application)
    return application


@pytest.fixture(scope="session")
def registered_tools():
    if not REGISTRY.tools:
        _load_plugins()
    return REGISTRY.tools


def test_registry_sorts_tools_by_group_then_order(registered_tools) -> None:
    tool_ids = [cls.tool_id for cls in registered_tools]
    assert len(tool_ids) == len(set(tool_ids)), "工具 ID 不应重复"
    assert set(tool_ids) == set(EXPECTED_TOOL_IDS)
    ordered = [cls.order for cls in registered_tools]
    assert ordered == sorted(ordered), "工具应按 order 升序注册"
    # 六大分组内部 order 连续，保证左右导航顺序与规划一致
    assert [cls.tool_id for cls in registered_tools][:4] == EXPECTED_TOOL_IDS[:4]
    assert [cls.tool_id for cls in registered_tools][-1] == "sys.settings"


def test_tool_ids_are_unique_and_metadata_complete(registered_tools) -> None:
    for cls in registered_tools:
        assert cls.title, f"{cls.tool_id} 缺少标题"
        assert cls.subtitle, f"{cls.tool_id} 缺少场景描述"
        assert cls.features, f"{cls.tool_id} 缺少规划特性"
        assert cls.stage.code in {"P1", "P2", "P3"}, f"{cls.tool_id} 阶段码异常"


def test_sidebar_groups_in_expected_order(app, registered_tools) -> None:
    window = MainWindow(tools=registered_tools)
    assert window.sidebar.tool_ids == [cls.tool_id for cls in registered_tools]
    grouped = REGISTRY.grouped_tools
    assert list(grouped) == EXPECTED_GROUPS


@pytest.mark.parametrize("tool_id", EXPECTED_TOOL_IDS)
def test_every_plugin_page_instantiates(app, tool_id: str) -> None:
    from netops.core.contracts import REGISTRY as registry

    plugin = next(cls for cls in registry.tools if cls.tool_id == tool_id)
    page = plugin.create_widget(create_context(open_db=False))
    if tool_id in IMPLEMENTED_TOOL_IDS:
        assert not isinstance(page, PlaceholderPage), f"{tool_id} 应渲染真实页面"
        return
    assert isinstance(page, PlaceholderPage)
    if tool_id in PREVIEW_WITH_CODE:
        assert page.code_views(), f"{tool_id} 应包含只读代码预览"
    else:
        assert page.code_views() == []


def test_main_window_loads_all_pages_without_qt_warnings(app) -> None:
    messages: list[str] = []
    qInstallMessageHandler(lambda _t, _c, message: messages.append(message))
    try:
        window = MainWindow()
        window.show()
        for tool_id in EXPECTED_TOOL_IDS:
            window.show_tool(tool_id)
            assert window.current_tool_id == tool_id
            page = window.page(tool_id)
            if tool_id in IMPLEMENTED_TOOL_IDS:
                assert not isinstance(page, PlaceholderPage)
            else:
                assert isinstance(page, PlaceholderPage)
        assert window.loaded_page_count == len(EXPECTED_TOOL_IDS)
    finally:
        qInstallMessageHandler(None)
    assert messages == [], f"Qt 回调中不应出现告警：{messages}"


def test_sidebar_collapse_toggles_width(app) -> None:
    window = MainWindow()
    expanded = window.sidebar.width()
    window.sidebar.toggle_collapsed()
    assert window.sidebar.width() < expanded
    assert window.sidebar.collapsed
    window.sidebar.toggle_collapsed()
    assert window.sidebar.width() == expanded
    assert not window.sidebar.collapsed
