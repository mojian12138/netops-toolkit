# 架构设计

## 进程模型

```text
┌──────────────── UI 进程 (PyQt6) ────────────────┐
│ 工具箱 Tab │ 监控看板 │ 托盘 │ 设置              │
└──────────────────┬──────────────────────────────┘
                   │ 只读 SQLite 连接（零 IPC 成本）
┌──────────────────┴──────────────────────────────┐
│  netmon 进程（由 launchd KeepAlive 拉起）        │
│  Scheduler → Probe 池 → StateMachine            │
│            → AlertRouter(抑制/聚合/限流)         │
│            → NotifyQueue(持久化+退避) → Notifier │
└─────────────────────────────────────────────────┘
```

## 三条契约（唯一扩展点）

```python
# core/contracts.py
@dataclass(slots=True)
class ProbeResult:
    ok: bool
    latency_ms: float | None = None
    detail: str = ""
    metrics: dict = field(default_factory=dict)

class Probe(Protocol):
    type_id: str            # "icmp" / "tcp" / "http" / "snmp" / "h3c_ap"
    config_schema: dict     # UI 自动生成表单
    async def check(self, target: Target, cfg: dict) -> ProbeResult: ...

class Notifier(Protocol):
    channel_id: str         # "pushplus" / "bark" / "webhook" / "smtp"
    async def send(self, msg: NotifyMessage, cfg: dict) -> SendResult: ...

class ToolPlugin(Protocol):
    tool_id: str
    title: str
    group: str
    order: int
    def create_widget(self, ctx: AppContext) -> QWidget: ...
```

注册用 `@register_probe` / `@register_notifier` / `@register_tool`，pkgutil 自动发现
`probes/` `notifiers/` `tools/` 目录。

## 状态机

```text
UNKNOWN ─成功→ UP ─连续 N 次失败→ SUSPECT ─再失败→ DOWN
                ↑                                    │
                └────── 连续 M 次成功 ── RECOVERING ←─┘
```

默认 `fail_threshold=3`、`recover_threshold=2`。
仅在跃迁时产生事件；持续 DOWN 按 `repeat_interval` 重提醒。
`flap_window=60s` 内反复上下 → 合并为一条"链路抖动"。

## 数据表

```sql
targets(id, name, remark, probe_type, params_json, group_name,
        interval_s, fail_threshold, recover_threshold,
        enabled, depends_on_json, tags_json, created_at)
probe_result(id, target_id, ts, ok, latency_ms, detail)         -- 保留 7 天
probe_rollup(target_id, minute_ts, up_cnt, down_cnt, avg_lat)   -- 保留 90 天
events(id, target_id, ts, from_state, to_state, message, root_cause_id)
notify_queue(id, ts, payload_json, channel_id, status, retries, next_retry_ts)
notify_log(id, ts, channel_id, severity, ok, err)
devices(id, ip, remark, model, location, group_name)            -- 全局共用
script_favorites(id, name, vendor, category, content, tags, created_at)
schema_version(version, applied_at)
```

`devices` 表被 Traceroute 备注、监控目标、报表共同复用——一处维护，多处受益。

## 平台隔离（platform_/os_layer.py 唯一职责）

| 能力 | macOS 实现 |
|---|---|
| ICMP | `/sbin/ping -c 1 -W 1000 -t 2`（`-W` 毫秒，无需 sudo） |
| Traceroute | `/usr/sbin/traceroute -n -m 20 -q 1` |
| ARP 表 | `arp -an` |
| 休眠唤醒 | pyobjc 监听 `NSWorkspaceDidWakeNotification` |
| 自启 | `~/Library/LaunchAgents/com.netops.netmon.plist` |
| 托盘 | `QSystemTrayIcon` + template image |
| 数据目录 | `~/Library/Application Support/NetOpsToolkit/` |