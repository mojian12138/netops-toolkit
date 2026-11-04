"""网络计算：六个纯算法计算器（P0 仅占位）。"""

from __future__ import annotations

from netops.core.contracts import NavGroup, Stage, register_tool
from netops.tools.base import PlaceholderTool
from netops.ui import icons


@register_tool()
class IpCalculatorTool(PlaceholderTool):
    tool_id = "calc.ip"
    title = "IP 地址计算器"
    group = NavGroup.CALCULATORS[1]
    order = 201
    icon = icons.IP
    stage = Stage("P2", "已实现")
    subtitle = "输入 IP 与掩码，立即得到网络地址、广播地址、可用主机范围与通配符掩码。"
    features = [
        "同时支持 CIDR（192.168.10.1/24）与点分掩码两种录入方式",
        "输出网络地址、广播地址、首个/末个可用地址、主机数量",
        "附赠通配符掩码（ACL 用），一键复制任意字段",
        "结果分区标注私有 / 公网 / 保留段，/31、/32 按 RFC 3021 处理",
    ]
    roadmap = [
        "纯函数计算模块 core/ipcalc.py，已配套 22 条单元测试",
        "UI 只负责取值与展示，逻辑零依赖 Qt，方便脚本复用",
    ]
    spec = [
        ("输入", "IP 地址 + 掩码（CIDR / 点分 / 位长三选一）"),
        ("输出", "网络 / 广播 / 可用范围 / 主机数 / 通配符掩码"),
        ("展示", "关键结果大字号卡片，点击即复制到剪贴板"),
        ("历史", "最近 20 次计算记录，方便反复对照"),
    ]

    @classmethod
    def create_widget(cls, ctx):
        from netops.ui.pages.ip_calc_page import IpCalcPage

        return IpCalcPage()


@register_tool()
class MaskCalculatorTool(PlaceholderTool):
    tool_id = "calc.mask"
    title = "子网掩码计算器"
    group = NavGroup.CALCULATORS[1]
    order = 202
    icon = icons.SUBNET
    stage = Stage("P2", "已实现")
    subtitle = "根据所需主机数或 CIDR 前缀反推掩码，二进制 / 十六进制 / 点分十进制同屏对照。"
    features = [
        "输入「每个子网需要 N 台主机」自动给出最合适的掩码与子网数",
        "反向：给掩码反推可容纳主机数、子网数",
        "掩码按位可视化，网络位与主机位分色显示",
        "同时展示点分十进制、二进制、十六进制三种形态",
    ]
    roadmap = [
        "与 IP 计算器共用 ipcalc 内核，避免两套算法",
        "位图组件用 QPainter 绘制 32 个格子，直观展示每一位",
    ]
    spec = [
        ("模式", "按主机数 / 按子网数 / 按掩码 三种模式切换"),
        ("位图", "32 位掩码位图，悬停显示该位含义"),
        ("进制", "点分 / 二进制 / 十六进制 三栏对照"),
        ("校验", "非法掩码（网络位不连续）实时提示"),
    ]

    @classmethod
    def create_widget(cls, ctx):
        from netops.ui.pages.mask_calc_page import MaskCalcPage

        return MaskCalcPage()


@register_tool()
class BaseConvertTool(PlaceholderTool):
    tool_id = "calc.base"
    title = "IP 进制转换"
    group = NavGroup.CALCULATORS[1]
    order = 203
    icon = icons.BASE
    stage = Stage("P2", "已实现")
    subtitle = "IP 在点分十进制、二进制、十六进制、整数之间双向实时转换，输入任意一栏其余同步。"
    features = [
        "四栏联动：任改一栏，其余三栏实时刷新",
        "支持十六进制带 0x / 点分 / 无分隔等多种写法",
        "自动补零对齐，二进制按 8 位一组分段显示",
        "附带整数 ↔ 大小端字节序换算",
    ]
    roadmap = [
        "解析器要好脾气：容错空格、大小写、前导零",
        "转换结果即时校验，超出 32 位立即提示",
    ]
    spec = [
        ("点分", "192.168.1.1"),
        ("二进制", "11000000.10101000.00000001.00000001"),
        ("十六进制", "0xC0A80101"),
        ("整数", "3232235777"),
    ]

    @classmethod
    def create_widget(cls, ctx):
        from netops.ui.pages.base_convert_page import BaseConvertPage

        return BaseConvertPage()


@register_tool()
class AggregateTool(PlaceholderTool):
    tool_id = "calc.aggregate"
    title = "路由聚合计算器"
    group = NavGroup.CALCULATORS[1]
    order = 204
    icon = icons.AGGREGATE
    stage = Stage("P2", "已实现")
    subtitle = "把一长串子网合并成最少的 CIDR 块，支持精准聚合与优化聚合两种算法。"
    features = [
        "粘贴多条网段（空格/逗号/换行分隔），自动去重、排序、剔除被包含项",
        "精准模式：只合并完全相邻的网段，结果可逐条溯源",
        "优化模式：允许包含少量多余地址，换取更少的路由条目",
        "生成 H3C/华为 与 思科两种风格的汇总路由配置",
    ]
    roadmap = [
        "核心算法：先规范化去重 → 排序 → 相邻块自底向上合并",
        "两种模式策略差异做成可选参数，算法本身只有一份",
    ]
    spec = [
        ("输入", "多行网段列表，支持逗号分隔批量粘贴"),
        ("模式", "精准聚合 / 优化聚合 单选"),
        ("结果", "聚合后的 CIDR 列表，标注每条覆盖了哪些原网段"),
        ("输出", "一键生成 ip route / ip route-static 配置片段"),
    ]

    @classmethod
    def create_widget(cls, ctx):
        from netops.ui.pages.aggregate_page import AggregatePage

        return AggregatePage()


@register_tool()
class SupernetSplitTool(PlaceholderTool):
    tool_id = "calc.split"
    title = "超网拆分计算器"
    group = NavGroup.CALCULATORS[1]
    order = 205
    icon = icons.SPLIT
    stage = Stage("P2", "已实现")
    subtitle = "把大网段按指定子网数量或指定大小等分，列出每一段的可用范围。"
    features = [
        "两种拆分方式：指定子网数量（尽量均分）或指定每段主机数",
        "表格列出每段网段、掩码、可用范围、广播地址与主机数",
        "表格模型虚拟化，拆分到 65536 段也能流畅滚动",
        "结果一键导出 CSV（UTF-8 BOM，Excel 直接打开）",
    ]
    roadmap = [
        "与 ipcalc 共用网络/广播地址计算，保证与 IP 计算器结果一致",
        "大列表用 QAbstractTableModel 虚拟化，万级子网不卡顿",
    ]
    spec = [
        ("输入", "起始网段 + 目标子网数量或每段主机数"),
        ("预览", "即时提示拆分后每段主机数与总段数"),
        ("表格", "虚拟滚动表格，支持列排序与筛选"),
        ("导出", "CSV / 配置模板 / 复制到剪贴板"),
    ]

    @classmethod
    def create_widget(cls, ctx):
        from netops.ui.pages.split_page import SplitPage

        return SplitPage()


@register_tool()
class ContainsTool(PlaceholderTool):
    tool_id = "calc.contains"
    title = "IP 包含检测"
    group = NavGroup.CALCULATORS[1]
    order = 206
    icon = icons.CONTAINS
    stage = Stage("P2", "已实现")
    subtitle = "判断某个 IP 或网段是否落在目标网络之内，排查地址规划冲突的快速手段。"
    features = [
        "一次可粘贴多个待检测对象，逐条给出完全包含 / 包含目标 / 部分重叠 / 互不相交的结论",
        "同时对照多个目标网段，生成「待测 × 目标」包含矩阵",
        "列出每条待测对象命中的目标，以及未被任何目标覆盖的部分",
        "裸 IP 按 /32 单主机参与判断，也支持十六进制、整数等写法",
    ]
    roadmap = [
        "整数化比较：把地址换算成 int 后做区间判断，规避字符串比较陷阱",
        "部分重叠需要双向区间求交，单独写测试用例覆盖边界",
    ]
    spec = [
        ("目标", "一个目标网段，如 10.10.0.0/16"),
        ("待测", "多行 IP / 网段列表"),
        ("结果", "逐条结论：完全包含 / 部分重叠 / 不属于"),
        ("矩阵", "多个目标时的包含关系一览表"),
        ("导出", "结果一键导出 CSV"),
    ]

    @classmethod
    def create_widget(cls, ctx):
        from netops.ui.pages.contains_page import ContainsPage

        return ContainsPage()
