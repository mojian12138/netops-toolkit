"""工具箱插件包。

``REGISTRY.discover("netops.tools")`` 会遍历本包下的所有模块，
import 即完成 ``@register_tool`` 注册——新增工具不需要改动任何调度代码。
"""

from __future__ import annotations

__all__ = ["REGISTRY"]

from netops.core.contracts import REGISTRY
