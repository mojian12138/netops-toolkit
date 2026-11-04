"""工具箱插件的公共基类。

真正的实现在 ``core.contracts.DefaultPlugin``：插件元数据与页面工厂都收敛在
那里，``core`` 层要在无 Qt 环境下单独 import 时也不会出问题。
"""

from __future__ import annotations

from netops.core.contracts import DefaultPlugin

__all__ = ["PlaceholderTool"]

PlaceholderTool = DefaultPlugin
