# NetOps Toolkit — 项目约定（Claude Code 必读）

## 1. 项目是什么

面向网络工程师（H3C/华为设备为主）的跨平台桌面工具，双核心：

- **工具箱**：Ping、端口扫描、IP 扫描、子网/路由计算、MAC 转换、Traceroute、
  H3C AP 配置生成、开局/常用脚本生成、维保查询
- **监控告警平台**：常驻监控目标连通性/服务状态，状态跃迁时通过 PushPlus 等通道
  推送微信/短信

单机运行、轻量化、无服务端。**当前唯一开发平台：macOS（Intel, x86_64, Python 3.11）**，
Windows 适配留到 P4，任何平台判断都必须收敛到 `platform_/os_layer.py`。

## 2. 技术栈（不得擅自更换）

| 用途 | 选型 |
|---|---|
| GUI | PyQt6 |
| 并发 | asyncio + 信号量限流 |
| 进程 | UI 主进程 + 独立监控进程 `netops.netmon` |
| 存储 | SQLite（WAL 模式） |
| 模板 | Jinja2 |
| HTTP | httpx（async） |
| 密钥 | keyring（macOS Keychain） |
| 配置 | YAML + JSON Schema 校验 |
| 日志 | 标准库 logging + RotatingFileHandler |
| 打包 | PyInstaller（onedir，排除未用 Qt 模块） |
| 质量 | ruff + pytest |

## 3. 架构铁律（违反即返工）

1. **三条扩展契约**：`Probe` / `Notifier` / `ToolPlugin`，定义于
   `src/netops/core/contracts.py`。新增探测类型、推送渠道、工具页 =
   新增一个模块 + 装饰器注册，**禁止修改核心调度代码**。
2. **监控引擎独立进程**：`netops.netmon` 不 import 任何 PyQt。UI 崩溃/关闭不影响告警。
3. **单向依赖**：`core/` 不得 import `ui/`；UI 通过 `AppContext` 取服务。
4. **平台差异只允许出现在 `platform_/os_layer.py`**，其余代码禁止出现 `if sys.platform`。
5. **不用 raw socket、不要 sudo**：ICMP 走 `/sbin/ping` 子进程，Traceroute 走
   `/usr/sbin/traceroute`，MAC 表走 `arp -an`。macOS 下三者均无需提权；一旦需要
   sudo，打包分发体验即崩溃。
6. **UI 线程不阻塞**：网络/SSH/SNMP 调用一律 async 或线程池，UI 只接信号。
7. **凭据不落盘明文**：token 存 Keychain，配置只存 `keyring:service/key` 引用；
   日志与导出必须脱敏。

## 4. 关键实现约定

- 计时一律 `time.monotonic()`，**禁止**用 `datetime.now()` 差值算超时。
- macOS 休眠唤醒必须补偿：唤醒即补探测一轮并重置计时，否则误报长时间故障。
- 告警**只在状态跃迁时发**；持续 DOWN 按 `repeat_interval` 重提醒。
- 通知队列**持久化到 SQLite**，失败指数退避重试（断网不丢告警）。
- 告警抑制四件套必须有：依赖抑制 / 维护窗口 / 分组聚合 / 全局限流。
- 数据库变更必须带 `schema_version` 迁移脚本。

## 5. 代码规范

- Python 3.11，全量 type hints，`from __future__ import annotations`
- 结构化数据用 `@dataclass(slots=True)`，不用裸 dict 传结构
- 日志 `logging.getLogger(__name__)`，禁止 `print`（CLI 交互除外）
- 提交前必须 `ruff check --fix` + `ruff format` + `pytest` 全绿
- Conventional Commits：`feat:` `fix:` `refactor:` `docs:` `test:` `chore:`

## 6. 常用命令

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,macos]"
ruff check --fix . && ruff format . && pytest -q
python -m netops                # GUI
python -m netops.netmon         # 监控引擎（前台，P1 才有）
```

本机 pyenv 里 3.11 需要显式指定：`PYENV_VERSION=3.11.9 python -m venv .venv`。

## 7. 目录

```text
src/netops/
├── core/        contracts.py, config.py, state_machine.py, alert_router.py,
│                scheduler.py, db.py, migrations/
├── probes/      icmp.py, tcp.py, http.py, snmp.py, h3c_ap.py
├── notifiers/   pushplus.py, bark.py, webhook.py, smtp.py
├── netmon/      __main__.py（监控进程入口）
├── tools/       base.py（PlaceholderTool）+ 按分组一个模块：
│                diagnostics / calculators / converters / monitor / h3c / settings
├── ui/          main_window.py, sidebar.py, theme.py, icons.py,
│                pages/placeholder_page.py, widgets/common.py,
│                resources/styles/light.qss
├── platform_/   os_layer.py  ← 唯一允许写平台分支的地方
└── utils/       logging_setup.py, validators.py, exporter.py
```

## 8. 当前阶段

P0 可视化外壳已完成：三契约、19 个功能插件（注册表自动发现）、数据驱动导航、
高保真占位页、浅色 QSS 主题、`tests/test_smoke.py`。

P0 的硬性约束：

- 占位页只消费契约字段（title/subtitle/stage/features/roadmap/spec/preview_*）
- 插件注册用 `@register_tool` 或 `@register_tool()`，由 `REGISTRY.discover("netops.tools")` 自动 import
- `DefaultPlugin.create_widget` 是 classmethod（注册的是类，不是实例）
- QSS 里等宽字体用 `@MONO_FONT@` 占位，由 `ui/theme.py` 运行期替换成本机存在的字体，
  否则 Qt 会打印 "missing font family" 警告，冒烟测试会因此失败
- 冒烟测试在 offscreen 后端跑，任何 Qt 警告都会让 `test_main_window_loads_all_pages_without_qt_warnings` 失败

下一步是 P1 监控最小闭环；P2 之后的业务工具不要在未要求时实现，只保留契约占位。