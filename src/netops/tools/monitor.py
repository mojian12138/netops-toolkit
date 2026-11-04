"""监控告警：总览看板、监控目标、告警通道、推送队列。"""

from __future__ import annotations

from netops.core.contracts import NavGroup, Stage, register_tool
from netops.tools.base import PlaceholderTool
from netops.ui import icons

_RULES = """# 监控规则（config/config.yaml）—— P0 仅展示，尚未生效
monitor:
  default_interval_s: 30      # 默认探测周期
  max_concurrency: 50         # 并发上限，防止把设备打高
  fail_threshold: 3           # 连续 3 次失败才判 DOWN
  recover_threshold: 2        # 连续 2 次成功才判 RECOVERING
  flap_window_s: 60           # 60s 内反复上下 → 合并为"链路抖动"

min_interval_by_probe:        # 按探测类型的下限
  icmp: 5
  tcp: 10
  http: 30
  snmp: 60"""

_PUSHPLUS = """# PushPlus 推送配置 —— token 只存 Keychain 引用，不落盘明文
notify_channels:
  - id: pushplus_main
    channel_id: pushplus
    enabled: false
    config:
      token_ref: "keyring:netops/pushplus"
      channel: wechat         # wechat / sms / mail / webhook
      template: html          # HTML 富文本告警卡片
      timeout_s: 10
      max_retries: 5

notify_policies:
  - name: 核心设备
    match: { tags: [core] }
    routes:
      - { severity: critical, channels: [pushplus_main], repeat_min: 15 }
      - { severity: recovery, channels: [pushplus_main], repeat_min: 0 }"""


@register_tool()
class MonitorDashboardTool(PlaceholderTool):
    tool_id = "monitor.dashboard"
    title = "监控总览"
    group = NavGroup.MONITOR[1]
    order = 401
    icon = icons.DASHBOARD
    stage = Stage("P1")
    subtitle = "所有监控目标的一屏总览：在线率、当前故障、今日告警瀑布，以及引擎运行状态。"
    features = [
        "顶部指标卡：目标总数、在线、DOWN、抖动中、引擎状态",
        "故障列表按持续时间倒序排列，点开可看最近 30 次探测结果",
        "24 小时告警瀑布图，一眼看出故障集中时段",
        "引擎与 UI 分进程运行，总览页只读 SQLite，零 IPC 成本",
    ]
    roadmap = [
        "P1 先出最小闭环：目标列表 + 状态色，图表后补",
        "UI 只读 WAL 模式 SQLite，避免与 netmon 进程抢写锁",
    ]
    spec = [
        ("指标", "目标数 / 在线 / 故障 / 抖动 / 平均 RTT"),
        ("目标列表", "名称、地址、状态、延迟、最近变更时间"),
        ("故障详情", "最近探测结果时间线 + 状态跃迁记录"),
        ("快照", "导出当日可用率报表（CSV / Markdown）"),
    ]


@register_tool()
class MonitorTargetsTool(PlaceholderTool):
    tool_id = "monitor.targets"
    title = "监控目标管理"
    group = NavGroup.MONITOR[1]
    order = 402
    icon = icons.ALERT
    stage = Stage("P1")
    subtitle = "增删改查监控目标：ICMP / TCP 探测、探测周期、防抖阈值与依赖关系可视化配置。"
    features = [
        "目标支持 ICMP 与 TCP 端口两种探测方式，参数表单由 config_schema 自动生成",
        "每个目标可单独设置探测周期、失败阈值、恢复阈值",
        "依赖抑制：核心设备故障时自动抑制其下游接入设备的告警",
        "维护窗口：变更期一键静默，告警不推送但照常记录",
    ]
    roadmap = [
        "Probe.config_schema 驱动表单生成，新增探测类型不改 UI 代码",
        "依赖关系用有向图校验，防止出现循环依赖",
    ]
    spec = [
        ("列表", "分组树展示，支持按标签、状态筛选"),
        ("表单", "名称、地址、类型、周期、阈值、标签、备注"),
        ("依赖", "选择上游设备，画布展示抑制链路"),
        ("批量", "CSV 导入 / 导出，快速迁移现网设备清单"),
    ]
    preview_code = _RULES
    preview_filename = "config.yaml"
    preview_language = "YAML"


@register_tool()
class MonitorChannelsTool(PlaceholderTool):
    tool_id = "monitor.channels"
    title = "告警推送渠道"
    group = NavGroup.MONITOR[1]
    order = 403
    icon = icons.SEND
    stage = Stage("P1")
    subtitle = "配置 PushPlus 等推送渠道，Token 存入 macOS Keychain，支持发送测试消息验证通路。"
    features = [
        "PushPlus 首选：微信 / 短信 / 邮件 / 钉钉 / 飞书一次配置多路送达",
        "Token 保存于系统钥匙串，配置文件里只留 keyring 引用",
        "「发送测试消息」按钮即时验证渠道是否连通",
        "渠道健康度记录：最近发送时间、失败次数、退避状态",
    ]
    roadmap = [
        "Notifier 契约 + 装饰器注册，后续加 Bark / 企业微信不改核心",
        "httpx 异步发送，失败按指数退避进持久化队列",
    ]
    spec = [
        ("渠道", "渠道卡片列表，含启用开关与健康状态"),
        ("编辑", "Token、接收人、消息模板、超时与重试次数"),
        ("策略", "严重级别 → 渠道路由与重提醒间隔"),
        ("测试", "发送一条样例告警卡片，确认微信可收到"),
    ]
    preview_code = _PUSHPLUS
    preview_filename = "notify.yaml"
    preview_language = "YAML"


@register_tool()
class MonitorQueueTool(PlaceholderTool):
    tool_id = "monitor.queue"
    title = "告警记录与队列"
    group = NavGroup.MONITOR[1]
    order = 404
    icon = icons.SEND
    stage = Stage("P1")
    subtitle = "查看未送达的告警队列与历史通知日志，断网期间告警不丢，恢复后自动补发。"
    features = [
        "通知队列持久化到 SQLite，进程重启、网络中断都不丢告警",
        "失败自动指数退避重试，队列里可看到下次重试时间",
        "通知日志保留最近 90 天，可按渠道 / 严重级别 / 结果筛选",
        "支持手动重发、单条丢弃，以及清空已成功记录",
    ]
    roadmap = [
        "队列消费与探测调度解耦，独立协程每 5 秒扫一次到期项",
        "状态机：pending → sending → sent / failed(backoff)",
    ]
    spec = [
        ("队列", "待发送项：时间、目标、严重级别、下次重试"),
        ("日志", "历史记录：渠道、结果、错误摘要"),
        ("操作", "重发 / 丢弃 / 清空已成功记录"),
        ("统计", "近 7 天发送成功率与平均延迟"),
    ]
