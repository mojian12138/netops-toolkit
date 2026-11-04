"""格式转换：MAC 地址各种"奇葩格式"互转。"""

from __future__ import annotations

from netops.core.contracts import NavGroup, Stage, register_tool
from netops.tools.base import PlaceholderTool
from netops.ui import icons

_MAC_SAMPLE = """# MAC 地址格式自由转换 —— 输入任意一种，其余实时输出
#
# 输入：aabb.ccdd.eeff

aabb.ccdd.eeff        →  思科 / H3C 设备接口默认格式
AA-BB-CC-DD-EE-FF     →  Windows ipconfig / 华为 display 输出
aa:bb:cc:dd:ee:ff     →  Linux ip link / Wireshark
aabbccddeeff          →  无分隔，DHCP 绑定表常用
0xaabbccddeeff        →  十六进制带前缀
aa bb cc dd ee ff     →  空格分隔

# 附加能力：厂商 OUI 识别、MAC 合法性校验、批量转换"""


@register_tool()
class MacConverterTool(PlaceholderTool):
    tool_id = "conv.mac"
    title = "MAC 地址格式转换"
    group = NavGroup.CONVERTERS[1]
    order = 301
    icon = icons.MAC
    stage = Stage("P2", "已实现")
    subtitle = "网络工程里常见的五种 MAC 写法自由互转，批量转换 + 厂商查询一步到位。"
    features = [
        "支持 aabb.ccdd.eeff / AABB-CCDD-EEFF / aa:bb:cc:dd:ee:ff / 无分隔 / 0x 前缀 / 空格分隔",
        "输入任意一种，九种常见格式实时并列输出，点一下即复制",
        "批量模式：一行一个 MAC（也兼容逗号分隔），非法行标红、结果可导出 CSV",
        "内置 OUI 前缀库识别厂商，并提示组播 / 本地管理 / 广播地址",
    ]
    roadmap = [
        "纯字符串规范化：先去分隔符 → 转小写 → 校验 12 位十六进制",
        "OUI 库做成可更新的数据文件，避免硬编码在代码里",
    ]
    spec = [
        ("输入", "单条输入框 + 批量多行输入区"),
        ("输出", "五种目标格式并列卡片，各自独立复制按钮"),
        ("校验", "长度、非法字符、全零 / 广播地址提示"),
        ("厂商", "根据 OUI 显示厂商名称，未知前缀明确标为未收录"),
    ]

    @classmethod
    def create_widget(cls, ctx):
        from netops.ui.pages.mac_page import MacPage

        return MacPage()

    preview_code = _MAC_SAMPLE
    preview_filename = "mac-format.txt"
    preview_language = "TEXT"
