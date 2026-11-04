"""网络诊断：Ping、端口扫描、主机扫描、路由追踪。"""

from __future__ import annotations

from netops.core.contracts import NavGroup, Stage, register_tool
from netops.tools.base import PlaceholderTool
from netops.ui import icons


@register_tool()
class PingTool(PlaceholderTool):
    tool_id = "diag.ping"
    title = "Ping 工具"
    group = NavGroup.DIAGNOSTICS[1]
    order = 101
    icon = icons.PING
    stage = Stage("P2", "已实现")
    subtitle = "单目标连续 Ping 与多目标并发批量探测，实时绘制延迟趋势，一键导出统计报表。"
    features = [
        "调用系统原生 ping 子进程，无需 root，macOS / Windows 行为一致",
        "单 IP 连续探测 + 多 IP 并发批量探测（并发 ≤16、单次 ≤256 个目标）",
        "实时延迟折线图，自动标注最高 / 平均 / 丢包率与连续失败次数",
        "批量结果导出 CSV / Markdown 报表，可直接贴进故障工单",
    ]
    roadmap = [
        "探测器跑在线程池，结果通过 Qt 信号回主线程，UI 不阻塞",
        "趋势图用 QPainter 自绘，避免引入图表重依赖",
        "IPv6 目标（系统 ping6）后续单独支持",
    ]
    spec = [
        ("输入区", "目标地址栏支持逗号/换行批量粘贴，自动去重与合法性校验"),
        ("控制区", "开始 / 暂停 / 停止 三态按钮，实时显示已发/已收/丢包"),
        ("结果区", "逐目标统计 + 采样明细，超时黄色、错误红色"),
        ("导出", "当前批次结果导出 CSV / Markdown，含时间戳、RTT、TTL、丢包统计"),
    ]

    @classmethod
    def create_widget(cls, ctx):
        from netops.ui.pages.ping_page import PingPage

        return PingPage()


@register_tool()
class PortScanTool(PlaceholderTool):
    tool_id = "diag.portscan"
    title = "端口扫描"
    group = NavGroup.DIAGNOSTICS[1]
    order = 102
    icon = icons.SCAN
    stage = Stage("P2", "已实现")
    subtitle = "多线程 TCP 连接探测，覆盖 Web / SSH / 网络设备管理等常见服务端口组合。"
    features = [
        "常见端口预设集：Web、远程登录、数据库、网络设备管理（22/23/161/443 等）",
        "多线程并发扫描，线程数与超时时间可调，避免把设备打爆",
        "开放端口自动识别服务类型与 Banner 指纹",
        "扫描结果按主机聚合展示，支持导出与二次复扫对比",
    ]
    roadmap = [
        "纯 asyncio 或线程池 TCP connect 探测，不依赖 raw socket",
        "预设端口集与厂商管理端口表做成数据文件，方便扩充",
    ]
    spec = [
        ("目标", "支持单 IP、IP 列表、CIDR 网段三种输入方式"),
        ("端口", "预设组合勾选 + 自定义范围（如 1-1024,3389）"),
        ("并发", "滑块调节并发数并给出风险提示"),
        ("结果", "树形表格：主机 → 端口 → 服务 → Banner"),
    ]

    @classmethod
    def create_widget(cls, ctx):
        from netops.ui.pages.portscan_page import PortScanPage

        return PortScanPage()


@register_tool()
class IpScanTool(PlaceholderTool):
    tool_id = "diag.ipscan"
    title = "局域网 IP 扫描"
    group = NavGroup.DIAGNOSTICS[1]
    order = 103
    icon = icons.HOSTS
    stage = Stage("P2", "已实现")
    subtitle = "扫描网段内存活主机，结合 ICMP 探测与系统 ARP 表识别在线设备。"
    features = [
        "ICMP 并发探测 + 读取系统 ARP 表双重确认，减少漏报",
        "识别 MAC、厂商 OUI，自动关联设备备注库中的设备名称",
        "存活主机列表实时增量刷新，扫描进度可视化",
        "结果可直接加入监控目标，形成「发现 → 盯网」闭环",
    ]
    roadmap = [
        "os_layer 提供 arp -an 解析，跨平台只改这一处",
        "扫描分批下发，避免短时间内大量 ARP 广播",
    ]
    spec = [
        ("网段", "输入 CIDR 或起始-结束地址，自动估算扫描规模"),
        ("策略", "ICMP / ARP / 两者结合 三种模式可选"),
        ("列表", "IP、MAC、厂商、备注、首次/最近发现时间"),
        ("动作", "右键：加入监控、复制 IP、标记已知设备"),
    ]

    @classmethod
    def create_widget(cls, ctx):
        from netops.services.devices import DeviceBook
        from netops.ui.pages.ipscan_page import IpScanPage

        page = IpScanPage()
        page.set_device_book(DeviceBook(ctx.db))
        return page


@register_tool()
class TracerouteTool(PlaceholderTool):
    tool_id = "diag.traceroute"
    title = "路由追踪"
    group = NavGroup.DIAGNOSTICS[1]
    order = 104
    icon = icons.ROUTE
    stage = Stage("P2", "已实现")
    subtitle = "轻量级路由跃点检测，绑定「IP ↔ 设备备注」库，直观看到每一跳是哪台设备。"
    features = [
        "调用系统 traceroute / tracert，无需提权，输出逐跳解析",
        "每一跳自动匹配设备备注库：核心交换机、汇聚、楼宇网关一目了然",
        "跃点延迟对比、超时跃点标注、路径变化高亮",
        "常用目的地一键收藏，导出整条路径作为排障证据",
    ]
    roadmap = [
        "os_layer 封装 traceroute 调用与正则解析",
        "设备备注库与监控目标共用 devices 表，一处维护多处受益",
    ]
    spec = [
        ("目标", "IP 或域名，可选最大跳数与每跳探测次数"),
        ("路径", "竖向时间轴展示跃点，自动补全设备中文备注与位置"),
        ("对比", "保存历史路径，两次结果 diff 出变化跃点"),
        ("导出", "路径快照导出图片或 Markdown，便于贴进变更单"),
    ]

    @classmethod
    def create_widget(cls, ctx):
        from netops.services.devices import DeviceBook
        from netops.ui.pages.traceroute_page import TraceroutePage

        page = TraceroutePage()
        page.set_device_book(DeviceBook(ctx.db))
        return page
