"""系统设置：全局参数、数据目录、外观、告警默认策略。"""

from __future__ import annotations

from netops.core.contracts import NavGroup, Stage, register_tool
from netops.tools.base import PlaceholderTool
from netops.ui import icons

_SETTINGS_SNIPPET = """# 配置文件位置
~/Library/Application Support/NetOpsToolkit/config.yaml

# P0 阶段以下项目仅做展示，写入逻辑将在 P1 落地
app:
  log_level: INFO
  data_dir: ~/Library/Application Support/NetOpsToolkit

monitor:
  default_interval_s: 30
  max_concurrency: 50

ui:
  theme: light
  sidebar_width: 220"""


@register_tool()
class SettingsTool(PlaceholderTool):
    tool_id = "sys.settings"
    title = "系统设置"
    group = NavGroup.SYSTEM[1]
    order = 901
    icon = icons.SETTINGS
    stage = Stage("P1")
    subtitle = "全局参数、数据目录、日志级别与监控默认策略；P0 阶段只展示规划中的配置项。"
    features = [
        "配置读写 config.yaml，改动即时校验并支持一键恢复默认",
        "数据目录、日志级别、监控默认周期与并发上限集中管理",
        "配置项按分组折叠，每项带一句话说明与取值范围",
        "导入 / 导出配置文件，多台机器之间快速同步偏好",
    ]
    roadmap = [
        "PyYAML 读写 + JSON Schema 校验，任何非法配置在保存前拦下",
        "配置热加载：保存后广播信号，已在运行的页面即时刷新",
    ]
    spec = [
        ("通用", "数据目录、日志级别、开机自启开关"),
        ("监控", "默认周期、并发、失败/恢复阈值、重提醒间隔"),
        ("通知", "默认渠道、免打扰时段、消息模板"),
        ("外观", "主题、侧栏宽度、列表密度"),
    ]
    preview_code = _SETTINGS_SNIPPET
    preview_filename = "config.yaml"
    preview_language = "YAML"
