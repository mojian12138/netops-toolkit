"""三条扩展契约：ToolPlugin / Probe / Notifier。

核心调度层只认这三个协议，新增功能 = 新增模块 + 装饰器注册，
禁止在调度代码里写 ``if tool_id == ...`` 这类分支。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查用
    from PyQt6.QtWidgets import QWidget

    from netops.core.context import AppContext

__all__ = [
    "NavGroup",
    "ToolPlugin",
    "DefaultPlugin",
    "Stage",
    "ProbeResult",
    "Probe",
    "SendResult",
    "NotifyMessage",
    "Notifier",
    "register_tool",
    "register_probe",
    "register_notifier",
    "create_page",
    "REGISTRY",
]


class NavGroup:
    """导航分组。数值即左栏展示顺序，分组之间留空档便于插入新分组。"""

    DIAGNOSTICS = ("diagnostics", "网络诊断", 100)
    CALCULATORS = ("calculators", "网络计算", 200)
    CONVERTERS = ("converters", "格式转换", 300)
    MONITOR = ("monitor", "监控告警", 400)
    VENDOR = ("vendor", "H3C 专属", 500)
    SYSTEM = ("system", "系统设置", 900)


@dataclass(slots=True)
class Stage:
    """功能所处的规划阶段，用于占位页上的徽章。"""

    code: str  # "P1" / "P2" / "P3"
    label: str = ""  # 自定义说明，留空则用内置文案

    _LABELS = {  # noqa: RUF012 - 只读常量
        "P1": "监控闭环",
        "P2": "工具箱",
        "P3": "厂商专用",
        "P4": "跨平台打磨",
    }

    @property
    def text(self) -> str:
        return self.label or self._LABELS.get(self.code, "规划中")


@runtime_checkable
class ToolPlugin(Protocol):
    """工具箱插件契约：每个左栏条目就是一个 ToolPlugin。

    展示字段（subtitle/stage/features/roadmap/spec/preview_*）在 P0 阶段
    由占位页消费；真正实现功能时只需要改 ``create_widget``，外壳无需改动。
    """

    tool_id: str
    title: str
    group: str
    order: int
    subtitle: str
    stage: Stage
    features: list[str]
    roadmap: list[str]
    spec: list[tuple[str, str]]
    preview_code: str
    preview_filename: str
    preview_language: str

    def create_widget(self, ctx: AppContext) -> QWidget: ...


class DefaultPlugin:
    """插件基类：把「元数据 + 页面工厂」收敛到一处，子类只声明类属性。

    需要真实页面时覆写 ``create_widget``；P0 阶段统一返回占位页。
    """

    tool_id: ClassVar[str] = ""
    title: ClassVar[str] = ""
    group: ClassVar[str] = ""
    order: ClassVar[int] = 0
    icon: ClassVar[str] = ""
    subtitle: ClassVar[str] = ""
    stage: ClassVar[Stage] = Stage("P2")
    features: ClassVar[list[str]] = []
    roadmap: ClassVar[list[str]] = []
    spec: ClassVar[list[tuple[str, str]]] = []

    # 脚本类页面用只读代码预览替代右侧"实现要点"卡片
    preview_code: ClassVar[str] = ""
    preview_filename: ClassVar[str] = "example.cfg"
    preview_language: ClassVar[str] = ""

    # 注册的是类不是实例，所以调用方显式传类：``Plugin.create_widget(Plugin, ctx)``。
    @classmethod
    def create_widget(cls, ctx: AppContext) -> QWidget:
        return create_page(cls, ctx)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<{type(self).__name__} {self.tool_id}>"


def create_page(cls: type[ToolPlugin], ctx: AppContext) -> QWidget:
    """默认页面工厂：P0 统一渲染占位页。

    延迟导入 UI，保证 ``core`` 层单独 import 时不需要 QApplication。
    """
    from netops.ui.pages.placeholder_page import PlaceholderPage

    return PlaceholderPage(cls, ctx)


@dataclass(slots=True)
class ProbeResult:
    ok: bool
    latency_ms: float | None = None
    detail: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Probe(Protocol):
    """监控探测器契约。"""

    type_id: str
    config_schema: dict[str, Any]

    async def check(self, target: Any, cfg: dict[str, Any]) -> ProbeResult: ...


@dataclass(slots=True)
class NotifyMessage:
    title: str
    body: str
    severity: str = "info"
    refs: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class SendResult:
    ok: bool
    error: str = ""
    retryable: bool = True


@runtime_checkable
class Notifier(Protocol):
    """推送渠道契约。"""

    channel_id: str

    async def send(self, msg: NotifyMessage, cfg: dict[str, Any]) -> SendResult: ...


class _Registry:
    """全局注册表。条目按 ``(group_order, order, title)`` 稳定排序。"""

    def __init__(self) -> None:
        self._tools: dict[str, type[ToolPlugin]] = {}
        self._tool_group_order: dict[str, int] = {}
        self._probes: dict[str, type[Probe]] = {}
        self._notifiers: dict[str, type[Notifier]] = {}

    # -- 注册 ---------------------------------------------------------------
    # 两种写法都支持：``@register_tool`` 与 ``@register_tool()``。
    # 作为装饰器时返回传入的类，于是类名保持不变。
    def register_tool(self, cls: type[ToolPlugin] | None = None) -> Any:
        if cls is None:
            return lambda c: self.register_tool(c)
        if cls.tool_id in self._tools:
            raise ValueError(f"重复注册工具插件: {cls.tool_id}")
        self._tools[cls.tool_id] = cls
        group = cls.group
        self._tool_group_order[cls.tool_id] = group[2] if isinstance(group, tuple) else 0
        return cls

    def register_probe(self, cls: type[Probe] | None = None) -> Any:
        if cls is None:
            return lambda c: self.register_probe(c)
        if cls.type_id in self._probes:
            raise ValueError(f"重复注册探测器: {cls.type_id}")
        self._probes[cls.type_id] = cls
        return cls

    def register_notifier(self, cls: type[Notifier] | None = None) -> Any:
        if cls is None:
            return lambda c: self.register_notifier(c)
        if cls.channel_id in self._notifiers:
            raise ValueError(f"重复注册推送渠道: {cls.channel_id}")
        self._notifiers[cls.channel_id] = cls
        return cls

    # -- 查询 ---------------------------------------------------------------
    @property
    def tools(self) -> list[type[ToolPlugin]]:
        return sorted(self._tools.values(), key=self._sort_key)

    def _sort_key(self, cls: type[ToolPlugin]) -> tuple[int, int, str]:
        group_order = self._tool_group_order.get(cls.tool_id, 0)
        return (group_order, cls.order, cls.title)

    @property
    def grouped_tools(self) -> dict[str, list[type[ToolPlugin]]]:
        """按分组归并，保持 ``cls.group`` 的首次出现顺序。"""
        groups: dict[str, list[type[ToolPlugin]]] = {}
        for cls in self.tools:
            groups.setdefault(cls.group, []).append(cls)
        return groups

    @property
    def probes(self) -> dict[str, type[Probe]]:
        return dict(self._probes)

    @property
    def notifiers(self) -> dict[str, type[Notifier]]:
        return dict(self._notifiers)

    def clear(self) -> None:
        """仅供测试使用：清空后重新发现插件。"""
        self._tools.clear()
        self._tool_group_order.clear()
        self._probes.clear()
        self._notifiers.clear()

    # -- 插件发现 -----------------------------------------------------------
    def discover(self, package: str = "netops.tools") -> None:
        """遍历 ``netops.tools`` 下的模块，import 即完成注册。"""
        import importlib
        import pkgutil

        pkg = importlib.import_module(package)
        for info in pkgutil.iter_modules(pkg.__path__):
            if not info.ispkg and not info.name.startswith("_"):
                importlib.import_module(f"{package}.{info.name}")


REGISTRY = _Registry()
register_tool = REGISTRY.register_tool
register_probe = REGISTRY.register_probe
register_notifier = REGISTRY.register_notifier
