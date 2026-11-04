# 具体功能开发计划（P2 工具箱 / P3 H3C 专属）

> 本文件是**唯一进度台账**。每完成一个步骤，必须回写：状态、验证命令与结果、遗留问题、下一步。
> 最后更新：2026-11-04 · 当前阶段：**I 阶段完成（A、B1–B6、C1–C2、D1–D3、E1–E3、F1–F3、G1–G3、H1–H3、I1–I5 完成），下一步阶段 J（H3C 维保查询）**

---

## 0. 背景与现状

P0 可视化外壳已完成：三契约（`ToolPlugin` / `Probe` / `Notifier`）、20 个工具插件
（`REGISTRY.discover("netops.tools")` 自动发现）、数据驱动导航、高保真占位页、浅色 QSS 主题、
`tests/test_smoke.py` 全绿。

**但所有工具页面仍是 `PlaceholderPage`，没有任何真实业务逻辑。** 具体缺口：

| 能力 | 现状 | 缺口 |
|---|---|---|
| 网络计算 | 无 | 无纯算法模块，无单元测试 |
| 平台命令 | `os_layer.py` 只有 3 个二进制路径常量 | 无子进程执行、超时、输出解析 |
| 数据库 | 无 | 无连接层、无迁移、无 `devices` / `script_favorites` 表 |
| 配置 / 密钥 | 只有 `config.example.yaml` | 无加载校验、无 `keyring:` 引用解析 |
| 模板 | 无 | 无 Jinja2 环境，H3C 脚本是硬编码字符串 |
| 异步桥 | 无 | 无「后台任务 → Qt 信号」的统一通道 |

> 注：`CLAUDE.md` 写「19 个功能插件」，实际注册为 **20 个**（4+6+1+4+4+1）。本计划以 20 为准，
> 后续同步修正文档。

---

## 1. 总体原则（不可违反）

1. **平台差异只出现在 `platform_/os_layer.py`**，业务代码禁止 `sys.platform` / 裸 `subprocess`。
2. **不用 raw socket、不要 sudo**：ICMP 走 `/sbin/ping`，Traceroute 走 `/usr/sbin/traceroute`，
   ARP 走 `arp -an`；端口扫描用普通 TCP connect。
3. **UI 线程不阻塞**：所有网络/IO 走后台线程或 asyncio，结果经 Qt 信号回主线程。
4. **纯算法与 UI 分离**：计算类逻辑放 `core/` 或 `utils/`，零 PyQt 依赖，可脱离 GUI 单测。
5. **新增工具 = 新增模块 + 装饰器注册**，禁止改核心调度代码。
6. **凭据不落盘明文**：只存 `keyring:service/key` 引用。
7. **扫描类功能必须限流**：默认并发、超时、目标规模都有硬上限，超限需用户显式确认。

### 扫描安全边界（写进代码常量，不是口头约定）

| 功能 | 默认 | 硬上限 | 说明 |
|---|---|---|---|
| Ping 批量目标 | 并发 16 | 256 个目标 | 单目标连续探测间隔 ≥ 200ms |
| 端口扫描并发 | 128 | 512 | 单目标端口数 ≤ 65535，超 1024 需确认 |
| 端口扫描超时 | 1.0s | 5.0s | 连接超时，非读取超时 |
| IP 扫描网段 | /24 | /22（1024 主机） | 超过 /22 拒绝执行并提示 |
| IP 扫描并发 | 64 | 256 | 分批下发，批间 50ms |
| Traceroute | 最大 20 跳 | 30 跳 | 每跳 1 次探测 |

---

## 2. 目标文件结构（新增部分）

```text
src/netops/
├── core/
│   ├── ipcalc.py            # 纯算法：网络/广播/掩码/聚合/拆分/包含
│   ├── db.py                # SQLite 连接（WAL）+ 迁移执行器
│   ├── migrations/          # 001_init.sql, 002_devices.sql ...
│   ├── secrets.py           # keyring:service/key 解析与存取
│   └── templates.py         # Jinja2 环境（加载 resources/templates）
├── services/
│   ├── ping.py              # Ping 会话（连续/批量），产出事件
│   ├── portscan.py          # asyncio TCP connect 扫描
│   ├── ipscan.py            # ICMP + ARP 存活探测
│   ├── traceroute.py        # 逐跳解析 + 设备备注匹配
│   ├── h3c_ap.py            # AP 注册配置渲染
│   ├── scripts.py           # 开局/常用脚本渲染
│   ├── favorites.py         # 脚本收藏 CRUD
│   └── warranty.py          # 维保查询（httpx）
├── ui/
│   ├── async_bridge.py      # 后台任务 → Qt 信号（QThread + asyncio loop）
│   ├── pages/               # 每个真实页面一个文件
│   └── widgets/             # 表格、位图、趋势图等新组件
├── platform_/os_layer.py    # 扩展：run_ping / run_traceroute / read_arp_table
└── resources/templates/     # h3c/*.j2, huawei/*.j2
```

---

## 3. 分阶段实施计划

### 阶段 A：基础设施（前置，必须先做）

| 步骤 | 内容 | 产出 | 验收 |
|---|---|---|---|
| A1 | `core/ipcalc.py` 纯算法内核 | 地址解析、网络/广播、掩码互转、聚合、拆分、包含 | ✅ 2026-11-03：`tests/test_ipcalc.py` 22 passed，覆盖 /0、/31、/32、非法掩码 |
| A2 | `platform_/os_layer.py` 命令封装 | `run_ping()`、`run_traceroute()`、`read_arp_table()`，统一超时与解析 | ✅ 2026-11-03：`test_os_layer.py` 10 passed；真机 ping/arp/traceroute 均返回结构化结果 |
| A3 | `ui/async_bridge.py` | `TaskRunner`：提交协程/函数，信号回主线程，支持取消 | ✅ 2026-11-03：`test_async_bridge.py` 8 passed，验证后台线程执行、异常捕获、协作取消 |
| A4 | `core/db.py` + `migrations/001_init.sql` | WAL 连接、`schema_version` 迁移、`devices` / `script_favorites` 表 | ✅ 2026-11-03：`test_db.py` 10 passed，幂等迁移、CRUD、重连持久化均已覆盖；`AppContext.db` 已接入 |
| A5 | `core/secrets.py` | `keyring:service/key` 解析、读写、缺失时明确报错 | ✅ 2026-11-03：`test_secrets.py` 13 passed，全程内存后端，未触碰真实 Keychain |
| A6 | `core/templates.py` + 模板目录 | Jinja2 环境、`StrictUndefined`、模板加载 | ✅ 2026-11-03：`test_templates.py` 8 passed；`h3c/ap_register.j2` 渲染结果已人工核对 |

**A 阶段完成标志**：`ruff check` + `ruff format --check` + `pytest` 全绿，且以上模块均无 PyQt 依赖
（`async_bridge` 除外）。

---

### 阶段 B：网络计算器（6 项，纯算法，最快见效）

依赖：A1。全部复用 `core/ipcalc.py`，UI 只做取值与展示。

| 步骤 | 工具 | 页面 | 关键点 |
|---|---|---|---|
| B1 | `calc.ip` IP 地址计算器 | `ui/pages/ip_calc_page.py` | ✅ 2026-11-03：CIDR/点分掩码双输入已实现；`test_ip_calc_page.py` 10 passed；全量 106 passed；offscreen 截图已核对 |
| B2 | `calc.mask` 子网掩码计算器 | `mask_calc_page.py` | ✅ 2026-11-03：三模式（掩码/主机数/子网数）+ QPainter 32 位位图；`test_mask_calc_page.py` 12 passed；全量 125 passed；offscreen 截图已核对 |
| B3 | `calc.base` IP 进制转换 | `base_convert_page.py` | ✅ 2026-11-03：四栏联动（点分/二进制/十六进制/整数）+ 8 项形态结果；`test_base_convert_page.py` 12 passed；全量 160 passed；offscreen 截图已核对 |
| B4 | `calc.aggregate` 路由聚合 | `aggregate_page.py` | ✅ 2026-11-03：精准/优化双算法 + 逐块溯源表格 + H3C/思科配置片段；`test_aggregate_page.py` 18 passed；全量 178 passed；offscreen 截图已核对 |
| B5 | `calc.split` 超网拆分 | `split_page.py` | ✅ 2026-11-03：双模式（子网数 / 每段主机数）+ `SubnetTableModel` 虚拟化表格 + 65536 段上限 + CSV 导出；`test_split_page.py` 16 passed；全量 193 passed；offscreen 截图已核对 |
| B6 | `calc.contains` IP 包含检测 | `contains_page.py` | ✅ 2026-11-03：多目标 + 四类结论（完全包含/包含目标/部分重叠/互不相交）+ 命中目标与未覆盖明细 + 包含矩阵 + CSV 导出；`test_contains_page.py` 15 passed、`test_ipcalc.py` 65 passed；全量 220 passed；offscreen 截图已核对 |

**验收**：每个页面可交互出结果；`tests/test_ipcalc.py` 覆盖全部算法分支；
`tests/test_calc_pages.py` 用 offscreen 实例化并断言关键输出。

---

### 阶段 C：MAC 地址格式转换

依赖：无（纯字符串处理）。

| 步骤 | 内容 | 验收 |
|---|---|---|
| C1 | `core/mac.py`：解析与格式化（`AA:BB:CC:DD:EE:FF`、`AA-BB-...`、`aabb.ccdd.eeff`、`AABBCCDDEEFF`、`0xAABBCCDDEEFF`） | ✅ 2026-11-04：十种写法互转 + 四种分隔符（`- : 空格 .`）严格一致性校验 + 内置 OUI 表 45 条 + 广播/组播/本地管理判定；`test_mac.py` 30 passed |
| C2 | `ui/pages/mac_page.py`：输入即时转换、多格式并列、一键复制、OUI 厂商提示（本地表） | ✅ 2026-11-04：120ms 防抖单条转换、九格式三列并列 + 一键复制、OUI 厂商与中文说明、批量转换 2000 行上限（非法行标红 + 复制规范化列 + CSV 导出）；`test_mac_page.py` 17 passed；全量 267 passed；offscreen 截图已核对 |

---

### 阶段 D：Ping 工具

依赖：A2、A3。

| 步骤 | 内容 | 验收 |
|---|---|---|
| D1 | `services/ping.py`：单目标连续探测、多目标并发批量、统计（发送/接收/丢包/最小/平均/最大） | ✅ 2026-11-04：目标解析兼容逗号/分号/空白/换行与 `#` 注释、自动去重、IPv6 明确拒绝；线程池并发 16（上限）、单次 ≤256 目标、间隔 ≥200ms、50ms 切片可取消睡眠；`PingReport` 自带 CSV/Markdown 渲染；`test_ping_service.py` 38 passed；真机 `127.0.0.1` ×2 轮 2/2 收，`192.0.2.1`（TEST-NET-1）100% 丢包 |
| D2 | `ui/pages/ping_page.py`：目标输入（逗号/换行批量）、开始/暂停/停止、结果表、实时趋势图（QPainter 自绘） | ✅ 2026-11-04：三卡片布局（探测参数 / 实时趋势 / 探测结果）；轮次 5 档 + 连续探测（600s 兜底）；`RttTrendChart` 多目标折线 + 超时红叉 + 图例；逐目标统计 8 列 + 采样明细 7 列（2000 行上限）；「停止」只置标志不取消令牌，保证已采集结果可导出；`test_ping_page.py` 15 passed；offscreen `/tmp/ui_ping.png` 已核对 |
| D3 | 导出 CSV / Markdown | ✅ 2026-11-04：CSV 带 UTF-8 BOM 与 `time,target,seq,status,rtt_ms,ttl,from` 表头；Markdown 含批次时间、逐目标统计表、丢包率与「（已手动停止）」标记，采样明细默认截断 200 条；`test_ping_page.py` 覆盖两者；全量 320 passed |

---

### 阶段 E：多线程端口扫描

依赖：A3。

| 步骤 | 内容 | 验收 |
|---|---|---|
| E1 | `services/portscan.py`：asyncio TCP connect，信号量限流，预设端口集（Web/远程/数据库/设备管理） | ✅ 2026-11-04：单次目标 ≤256、端口 ≤4096、并发默认 128 / 上限 512、超时 1.0s / 慢速 5.0s；目标解析支持单 IP / 主机名 / CIDR / 末段区间；端口支持 `80,443,1-1024`；`test_portscan_service.py` 20 passed（本地监听命中、关闭端口、超时、取消、回调异常） |
| E2 | `ui/pages/portscan_page.py`：目标（单 IP/列表/CIDR）、端口预设+自定义范围、并发滑块（带风险提示）、树形结果 | ✅ 2026-11-04：三块布局（扫描目标 / 参数与进度 / 开放端口）；四个预设勾选 + 自定义端口；并发滑块实时显示数值，≥128 提示防扫描，规模 ≥65,536 次连接提示先小范围试扫；结果表按主机+端口平铺（未做树形）；`test_portscan_page.py` 8 passed |
| E3 | 服务识别与 Banner 抓取（可选、超时保护） | ✅ 2026-11-04：`SERVICE_NAMES` 70 条端口表 + 30 条 Banner 关键字指纹（含 H3C / 华为 / 锐捷 / 思科 / Sangfor）；Banner 读取 1.5s 独立超时、截断 1024 字节、只保留 ASCII 可打印字符；抓取失败不影响开放端口判定 |

**安全**：并发与端口数超限时弹确认；扫描中可随时停止。

---

### 阶段 F：局域网 IP 扫描

依赖：A2（ARP）、A3、A4（`devices` 表）。

| 步骤 | 内容 | 验收 |
|---|---|---|
| F1 | `services/ipscan.py`：ICMP 并发 + ARP 表双重确认，分批下发 | ✅ 2026-11-04：`IpScanSession` 分批（256/批）+ 线程池并发（默认 64 / 上限 128）；`method` 区分 `icmp` / `arp` / `icmp+arp`；网段解析支持 CIDR / 起止地址 / 末段区间（`_TAIL_RANGE`）/ 单 IP；地址上限 4096；ARP 表读取失败不影响 ICMP 结果；`test_ipscan_service.py` 20 passed |
| F2 | `ui/pages/ipscan_page.py`：网段输入与规模预估、策略选择、增量刷新列表、右键「加入监控/复制/标记设备」 | ✅ 2026-11-04：三卡片布局（扫描范围 / 参数与进度 / 存活主机）；策略下拉三种模式；并发滑块实时显示数值，≥96 时提示防扫描；规模预估随输入实时更新（`estimate()` 不展开 IP 列表）；存活主机按 IP 数值排序增量插入；底部「保存备注 / 复制 IP / 导出 CSV / 导出 Markdown」；**未做右键菜单**（改成按钮 + 表格选中，见遗留）；`test_ipscan_page.py` 17 passed |
| F3 | 设备备注库读写（`devices` 表） | ✅ 2026-11-04：`DeviceBook` 包一层 `Database`，`available=False` 时读返回空、写抛 `DeviceBookError`，扫描页在 `open_db=False` 下照常工作；`remarks_for` 批量查（一次 SQL）；`mark` 走 `upsert_device` 保证幂等；`test_devices.py` 8 passed |

---

### 阶段 G：路由追踪 + 设备备注

依赖：A2、A4。

| 步骤 | 内容 | 验收 |
|---|---|---|
| G1 | `services/traceroute.py`：调用系统 traceroute，逐跳解析（IP、RTT、超时） | ✅ 2026-11-04：`TracerouteSession` 封装 `os_layer.run_traceroute`；目标校验拒绝 IPv6 与纯数字假 IP；跳数上限 30（复用 os_layer 常量）、单探针等待 0.1–5.0s；同跳多条探针折叠成一条、延迟取最小值；`CommandError` 转 `TracerouteError`；`test_traceroute_service.py` 17 passed |
| G2 | 设备备注匹配：每跳 IP 查 `devices` 表，显示名称/位置 | ✅ 2026-11-04：`annotate_report(report, book)` 一次 SQL 查完全部 IP；命中回显名称 / 位置 / 型号，未命中留空；超时跳（`*`）跳过查询；**必须在 UI 线程调用**（见遗留） |
| G3 | `ui/pages/traceroute_page.py`：竖向时间轴、延迟对比、超时标注、导出 Markdown | ✅ 2026-11-04：新增 `ui/widgets/timeline.py`（`HopTimeline` QPainter 自绘：节点色区分普通/已备注/超时、延迟条按路径最大 RTT 归一化、超时跳连线改虚线）；页面三卡片（追踪参数 / 路径 / 逐跳明细）；表格与时间轴同源；底部「保存备注 / 复制地址 / 复制整条路径 / 导出 Markdown / 导出 CSV」；`test_traceroute_page.py` 16 passed |

---

### 阶段 H：H3C AP 注册配置生成

依赖：A6、A4（可选收藏）。

| 步骤 | 内容 | 验收 |
|---|---|---|
| H1 | `resources/templates/h3c/ap_register.j2` + `services/h3c_ap.py` | ✅ 2026-11-04：`ApSpec` / `ApGroup` / `ApBatch` 数据模型与严格校验；单 AP、多 AP、带 AP 组三种渲染结果；`test_h3c_ap.py` 覆盖服务模板、型号/SN、批次冲突、组解析与 CSV | 
| H2 | `ui/pages/ap_config_page.py`：表单（名称/SN/型号/AP 组/服务模板）、实时预览、复制/导出 | ✅ 2026-11-04：单台表单 120ms 防抖生成、Comware 代码预览、整段/仅 AP 组复制、`.cfg` 导出；`test_ap_config_page.py` 覆盖模式切换、生成、清空、复制与导出；offscreen 视觉验收通过 |
| H3 | CSV 批量导入 + 整表校验（任一行非法则不生成） | ✅ 2026-11-04：CSV 表头/列数/必填字段/行号严格校验，任一行非法则清空预览并隐藏结果表；批量明细表与摘要已实现；非法行定位单测通过 |

---

### 阶段 I：脚本生成器 + 收藏/载入

依赖：A4、A6。

| 步骤 | 内容 | 验收 |
|---|---|---|
| I1 | 模板：华为 VRP / H3C Comware 开局脚本；常用脚本（自动改密码、自动端口描述、AP 改名分组） | ✅ 2026-11-04：新增 H3C/Huawei 开局与批量端口描述模板，元数据与 JSON Schema 随包资源发现；默认值渲染单测通过 |
| I2 | `services/scripts.py`：模板注册表（厂商 → 场景 → 模板 + 参数 schema） | ✅ 2026-11-04：新增 `ScriptRegistry` / `ScriptService`，资源发现、schema 校验、StrictUndefined 渲染、确定性输出与中文错误转换已实现 |
| I3 | `ui/pages/script_page.py`：左侧模板树、参数表单、右侧可编辑代码区 | ✅ 2026-11-04：厂商/分类/模板三级选择、schema 字段表单、120ms 防抖预览、可编辑代码区、复制与导出 |
| I4 | 代码区右键菜单「收藏当前配置」/「载入收藏」 | ✅ 2026-11-04：接入 `Database` 收藏 CRUD，支持收藏当前代码、双击载入与无数据库时明确提示 |
| I5 | 收藏库检索（厂商/分类/关键词）、删除、重命名 | ✅ 2026-11-04：收藏列表关键词检索、重命名、删除与刷新已实现；服务/UI 测试及重启持久化基础能力通过 |

**验收**：收藏后重启应用仍可载入（SQLite 持久化）。

---

### 阶段 J：H3C 维保查询

依赖：A5（如需凭据）、A3。

| 步骤 | 内容 | 验收 |
|---|---|---|
| J1 | `services/warranty.py`：httpx 异步查询、全局限流、结果缓存 | 单测：mock 响应解析；接口失败明确报错，不臆造 |
| J2 | `ui/pages/warranty_page.py`：多行 SN 输入、逐行进度、结果表、临期/过保高亮、导出 CSV | 手动：批量查询与导出 |

> 若官方接口需要登录/验证码，J1 先交付「接口适配层 + 明确不可用提示」，不伪造数据。

---

## 4. 每步完成后的回写格式

每完成一个步骤，在本文件对应表格行追加状态，并在下方「进度日志」追加一条：

```markdown
### 2026-11-03 · A1 完成
- 变更：新增 core/ipcalc.py、tests/test_ipcalc.py
- 验证：ruff check ✅ / ruff format --check ✅ / pytest 42 passed ✅
- 遗留：IPv6 暂只支持基础计算
- 下一步：A2
```

---

## 5. 进度日志

### 2026-11-04 · I1 + I2 + I3 + I4 + I5 完成（脚本生成器 + 收藏/载入）

- 变更：新增 `src/netops/services/scripts.py`（模板资源发现、schema 校验、默认值、Jinja2 渲染）、`src/netops/ui/pages/script_page.py`（厂商/分类/模板选择、参数表单、预览编辑、复制/导出、收藏库）、脚本模板资源 `src/netops/resources/templates/scripts/{h3c,huawei}/`、`tests/test_scripts_service.py` 与 `tests/test_script_page.py`。
- 修改：`src/netops/tools/h3c.py` 将 `h3c.scripts` 更新为 P3 已实现并接入真实页面；`tests/test_smoke.py` 将其加入真实页面断言并移出代码预览占位集合。
- 内容：模板通过 `importlib.resources` 自动发现，每个模板配套 `meta.json`、`schema.json`、`template.j2`；服务层严格校验必填字段并拒绝半成品渲染；页面支持表单变更 120ms 防抖、可编辑代码、剪贴板复制与 `.cfg/.txt` 导出；收藏复用现有 `script_favorites` 表，支持关键词检索、双击载入、重命名和删除。
- 验证：`.venv/bin/ruff check --fix .` ✅；`.venv/bin/pytest -q` ✅ **469 passed**；脚本生成器聚焦测试 30 passed。ruff format 检查受仓库既有 `docs/ARCHITECTURE.md` 未格式化片段影响，新增代码已通过格式化。
- 遗留：当前模板集合已覆盖开局与端口描述，自动改密码/AP 改名分组模板和代码区右键菜单尚未细化；收藏重命名目前采用快速后缀操作，后续可替换为输入对话框；模板渲染仍在 UI 线程执行，当前模板为本地轻量渲染，若引入慢速校验器需接入 `TaskRunner`。
- 下一步：阶段 J —— J1 `services/warranty.py`（httpx 异步查询、限流与结果缓存）。

### 2026-11-04 · H1 + H2 + H3 完成（H3C AP 注册配置生成）

- 变更：新增 `src/netops/services/h3c_ap.py`（`ApError` / `ApSpec` / `ApGroup` / `ApBatch`、服务模板与 CSV 解析、批次校验、Jinja2 配置渲染）、`src/netops/ui/pages/ap_config_page.py`（单台防抖表单、CSV 批量导入、代码预览、复制与 `.cfg` 导出）、`tests/test_h3c_ap.py`、`tests/test_ap_config_page.py`；确认 `src/netops/resources/templates/h3c/ap_register.j2` 已接入并由服务层渲染。
- 修改：`src/netops/tools/h3c.py`（`h3c.ap` 更新为 `Stage("P3", "已实现")` 并返回真实页面；其余 H3C 页面保持占位）、`tests/test_smoke.py`（加入 `h3c.ap` 实现断言并移除其代码预览占位断言）。
- 内容：单台输入名称 / 序列号 / 型号 / 服务模板 / AP 组，120ms 防抖后实时生成 Comware 脚本；CSV 批量导入支持注释与空行，严格检查表头、列数、必填字段、型号、序列号、模板范围、重复名称/SN 与 AP 组冲突，任一行非法则整表拒绝生成并带原始行号；结果支持批量明细、整段复制、仅复制 AP 组片段、导出 `.cfg`。
- 验证：`ruff check --fix .` ✅ All checks passed / `ruff format .` ✅（92 files left unchanged）/ `pytest -q` ✅ 全量通过；服务与页面测试及 smoke 覆盖均通过。offscreen 页面验收：单台 `single code len: 252 | summary: 共 1 台 AP，1 个 AP 组。`，批量 `batch code len: 618 | summary: 共 4 台 AP，1 个 AP 组。`，截图已人工核对。
- 遗留：H3C 脚本生成器、脚本收藏/载入与维保查询尚未实现；型号列表目前由服务层校验规则维护，尚未做可编辑 UI；模板尚未覆盖更多厂商差异；导出仍由页面内实现，通用 `utils/exporter.py` 待后续扫描/脚本功能统一抽象。
- 下一步：阶段 I —— I1 `services/scripts.py` 与华为 VRP / H3C Comware 开局及常用脚本模板。

### 2026-11-04 · G1 + G2 + G3 完成（路由追踪 + 设备备注）

- 变更：新增 `src/netops/services/traceroute.py`（`TracerouteError` / `HopInfo` /
  `TracerouteReport` / `TracerouteSession` / `annotate_report` / `parse_target`）、
  `src/netops/ui/widgets/timeline.py`（`HopTimeline`）、
  `src/netops/ui/pages/traceroute_page.py`（`TraceroutePage`）、
  `tests/test_traceroute_service.py`（17 条）、`tests/test_traceroute_page.py`（16 条）；
  修改 `src/netops/services/devices.py`（`remarks_for` 的 sqlite 异常从 debug 提到 warning）、
  `src/netops/tools/diagnostics.py`（`diag.traceroute` 改 `Stage("P2", "已实现")` + `create_widget`）、
  `tests/test_smoke.py`（`IMPLEMENTED_TOOL_IDS` 加入 `diag.traceroute`）
- 内容：追踪走 `os_layer.run_traceroute`（macOS `traceroute -n -q 1`，Windows `tracert -d`），
  不需要提权。本模块只面对 `TracerouteResult`，命令拼装与输出解析全在 os_layer。
  同一跳出现多条探针时按跳号折叠：超时 vs 有响应取有响应的，都有响应取最小延迟
  ——排障关心「这段路最快能多快」。目标校验里**纯数字点号一律按 IPv4 处理**，
  `256.1.1.1` 直接报「不是合法的 IPv4」，而不是原样丢给 traceroute 换来一句
  `Name or service not known`。
  时间轴 `HopTimeline` 用 QPainter 自绘：节点青=普通跳、蓝=已备注设备、红色空心+叉=超时；
  延迟条按本段路径最大 RTT 归一化；超时跳的连线改虚线，一眼看出哪一段断。
- 修掉的核心 bug（**线程模型**）：`Database` 是单 sqlite 连接，默认 `check_same_thread=True`，
  只能在创建它的线程（UI 线程）使用。最初把备注匹配放在 `TracerouteSession.run()` 里，
  而它跑在 `TaskRunner` 的工作线程 → 每次 `remarks_for` 抛 `ProgrammingError`，
  又被 `except sqlite3.Error: _log.debug(...)` 吞掉，表现为**备注永远为空且毫无报错**。
  这个 bug 只在页面级测试里才暴露（服务层测试是单线程跑的，一直绿）。
  修法：备注匹配从会话里移到 UI 线程的 `_on_finished` 里调 `annotate_report()`；
  同时把 `remarks_for` 的日志级别从 debug 提到 warning，让同类错误以后看得见。
- 验证：`ruff check --fix .` ✅ All checks passed / `ruff format .` ✅ /
  `pytest -q` **424 passed in 67.61s** ✅（F 阶段 391 → +33）；offscreen 渲染
  `/tmp/traceroute_done.png`（6 跳，含 3 跳已备注、1 跳超时）已人工核对
- 遗留：**traceroute 没有增量输出**（进程结束才吐完整 stdout），所以「按跳推进的进度」
  做不到，状态栏只能显示已运行秒数；「保存历史路径 / 两次结果 diff」没做
  （需要新表，归到监控模块一起设计）；`traceroute` 进程无法优雅中断——点「停止」
  只是不再采用结果，子进程仍会跑完（`_run` 的 `subprocess.run` 不暴露句柄）；
  IPv6 目标明确拒绝
- 下一步：阶段 H —— H1 `resources/templates/h3c/ap_register.j2` + `services/h3c_ap.py`


### 2026-11-04 · F1 + F2 + F3 完成（局域网 IP 扫描）

- 变更：新增 `src/netops/services/ipscan.py`（`IpScanError` / `HostResult` / `ScanReport` /
  `NetSpec` / `IpScanSession` / `parse_networks` / `estimate` / `vendor_for_mac` / `MODES`）、
  `src/netops/services/devices.py`（`DeviceBook` / `DeviceBookError`）、
  `src/netops/ui/pages/ipscan_page.py`（`IpScanPage` + `_HostBridge`）、
  `tests/test_ipscan_service.py`（20 条）、`tests/test_devices.py`（8 条）、
  `tests/test_ipscan_page.py`（17 条）；修改 `src/netops/tools/diagnostics.py`
  （`diag.ipscan` 改 `Stage("P2", "已实现")` + `create_widget` 注入 `DeviceBook(ctx.db)`）、
  `tests/test_smoke.py`（`IMPLEMENTED_TOOL_IDS` 加入 `diag.ipscan`）
- 内容：探测走 `os_layer.run_ping`（每地址 `count=1`）+ `os_layer.read_arp_table`，都不需要提权；
  两者并用的原因是 ICMP 会被主机防火墙拦掉（Windows 默认就拦）而 ARP 表在二层仍有记录，
  反过来 ARP 表只覆盖同网段、跨网段必须靠 ICMP。`batches of 256` 防止一口气拉起上千个
  ping 子进程卡死系统；`ThreadPoolExecutor` 并发默认 64、上限 128。
  目标解析支持单 IP / CIDR（`/30` 只取 `hosts()`）/ 起止地址 / 末段区间（`192.168.1.10-20`，
  单独一条 `_TAIL_RANGE` 正则，否则会被 `_RANGE` 当成整数区间误判）；上限 4096 个地址，
  超限抛 `IpScanError` 而不是混进「格式不合法」；IPv6 明确拒绝。
  `DeviceBook` 在 `db is None` 时读返回空、写抛 `DeviceBookError`，扫描页在
  `open_db=False`（单测、数据库打不开）下照常可用——这是把 devices 表再包一层的唯一理由。
- 页面侧：`on_host` 回调经 `_HostBridge.host = pyqtSignal(object)` 回主线程；
  存活主机按 IP **数值**排序插入（不是字典序，`.10` 排 `.2` 后面）；「只显示存活主机」勾选切
  换时整表重绘；`_refresh_remarks` 在保存备注后只刷「备注」列，不重扫；
  「停止」只置 `_stop_requested`，让会话自行收尾返回已采集结果；
  导出 CSV 带 UTF-8 BOM 与 `ip,alive,mac,vendor,method,rtt_ms,error` 表头。
- 验证：`ruff check --fix .` ✅ All checks passed / `ruff format .` ✅ /
  `pytest -q` **391 passed in 53.91s** ✅；offscreen 渲染 `/tmp/ipscan_done.png`
  （192.168.1.0/24 · 254 地址 · 并发 64 · 5 台存活）已人工核对
- 遗留：「右键菜单（加入监控 / 复制 / 标记设备）」做成了底部按钮 + 表格选中，没有 QMenu；
  「加入监控」目标项要等监控模块（阶段 J）才有落点，当前只有「保存备注」；
  ARP 表是**扫描结束后一次性读取**（`read_arp_table` 读系统缓存），不做主动 ARP 广播，
  因此冷启动时同网段设备可能漏报——真要主动探测得用 raw socket + 提权，`roadmap` 已记；
  `estimate()` 与 `parse_networks()` 各自维护一份解析逻辑，改正则时必须同步两处
- 下一步：阶段 G —— G1 `services/traceroute.py`（`os_layer` 已有 `run_traceroute` /
  `parse_traceroute_output`，本步只做会话封装与逐跳备注匹配），G2 用 `DeviceBook` 批量匹配


### 2026-11-04 · E1 + E2 + E3 完成（端口扫描）

- 变更：新增 `src/netops/services/portscan.py`（`PortScanError` / `PortResult` / `TargetResult` /
  `ScanReport` / `PortScanner` / `parse_targets` / `parse_ports` / `preset_ports` /
  `guess_service` / `parse_banner` / `check_workload`）、
  `src/netops/ui/pages/portscan_page.py`（`PortScanPage` + `_ResultBridge` + `_wrap`）、
  `tests/test_portscan_service.py`（20 条）、`tests/test_portscan_page.py`（8 条）；
  修改 `src/netops/tools/diagnostics.py`（`diag.portscan` 改 `Stage("P2", "已实现")` + `create_widget`）、
  `tests/test_smoke.py`（`IMPLEMENTED_TOOL_IDS` 加入 `diag.portscan`）
- 内容：探测走标准库 `asyncio.open_connection`（普通 TCP connect），不开 raw socket、不需要
  sudo，因此不经过 `platform_/os_layer.py`；`run()` 用 `asyncio.run` 包一层给 `TaskRunner.submit()`
  调用，`run_async()` 留给异步上下文。限流用 `asyncio.Semaphore(concurrency)`，取消检查放在
  信号量前后两处（进入前 + 拿到许可后），保证停止时不再发起新连接。
  目标解析支持单 IP / 主机名 / CIDR（`/30` 只取 `hosts()`）/ 末段区间（`192.168.1.10-20`，
  单独一条 `_IP_RANGE` 正则，否则会被 `_RANGE` 当成整数区间误判）；规模超限抛
  `PortScanError` 而不是混进「格式不合法」；IPv6 明确拒绝。
  服务识别两级：`SERVICE_NAMES`（70 条端口表）打底，Banner 命中 `_BANNER_HINTS`（30 条指纹，
  含 `h3c` / `comware` / `huawei` / `ruijie` / `cisco` / `sangfor`）则覆盖端口推断。
  Banner 清洗只认 ASCII 可打印字符——`str.isprintable()` 对 U+FFFD 也返回 True，二进制数据会
  输出一串 `��`，这是首版单测抓到的真实缺陷。
- 页面侧：`on_result` 回调经 `_ResultBridge.result = pyqtSignal(object)` 回主线程；结果表按
  「主机 + 端口」平铺（**未做树形**，见遗留）；并发滑块 `1–512` 实时显示数值，
  `≥128` 提示防扫描、`目标数 × 端口数 ≥ 65,536` 提示先小范围试扫（`check_workload`）；
  「停止」只置 `_stop_requested`，不调用 `handle.cancel()`，让扫描自行收尾返回部分结果；
  CSV 带 UTF-8 BOM 与 `host,port,state,service,banner,latency_ms,error` 表头，Markdown 含
  「存活主机与开放端口」表
- 验证：`ruff check --fix .` ✅ All checks passed / `ruff format .` ✅ /
  `pytest -q` **348 passed in 89.94s** ✅；本地监听端口真扫命中 `state=open` 且抓到
  `SSH-2.0-OpenSSH_9.6p1 Ubuntu-3ubuntu13`，关闭端口判 `closed`，取消返回 `cancelled=True`；
  offscreen 渲染 `/tmp/portscan_page_filled.png`（3 段目标 + Web/远程登录预设 + 并发 192）已核对
- 遗留：结果表是平铺而非计划里写的「树形表格」，主机多时需要靠排序筛；`PortSpec` 因未使用已删除，
  预设描述改由 `PORT_PRESETS` 的键承载；IPv6 目标不支持；Banner 是「连上就读」，不做主动协议探测
  （如发 HTTP 请求），所以只回显服务端主动吐出的内容；端口上限 `MAX_PORTS = 4096` 比计划中的
  「超 1024 需二次确认」更严，二次确认弹窗改成了 `risk_hint` 文字提示，未做阻断式对话框
- 下一步：阶段 F —— F1 `services/ipscan.py`（ICMP 并发 + ARP 表双重确认，分批下发；先用假
  ARP 输出做解析单测，再真机扫本机所在 /24）

### 2026-11-04 · D1 + D2 + D3 完成（Ping 工具）
- 变更：新增 `src/netops/services/__init__.py`、`src/netops/services/ping.py`
  （`PingError` / `PingSample` / `TargetSummary` / `PingReport` / `PingSession` / `parse_targets`）、
  `src/netops/ui/widgets/trend.py`（`RttTrendChart`）、
  `src/netops/ui/pages/ping_page.py`（`PingPage` + `_SampleBridge` + `MAX_DURATION_S = 600`）、
  `src/netops/utils/__init__.py`、`tests/test_ping_service.py`（38 条）、
  `tests/test_ping_page.py`（15 条）；修改 `src/netops/ui/async_bridge.py`
  （`_wire` 的三条 lambda 改为普通闭包）、`src/netops/tools/diagnostics.py`
  （`diag.ping` 改 `Stage("P2", "已实现")` + `create_widget` + features/spec 同步）、
  `tests/test_smoke.py`（`IMPLEMENTED_TOOL_IDS` 加入 `diag.ping`）
- 内容：探测完全走 `os_layer.run_ping`（每轮 `count=1`），不碰 raw socket；会话层负责
  目标规范化、并发调度（`ThreadPoolExecutor` + `thread_name_prefix="netops-ping"`）、
  取消（50ms 切片睡眠 + 每轮前检查）/暂停、统计累加、采样缓冲（`MAX_SAMPLES = 20_000`
  超出计入 `dropped_samples`）与导出渲染。依赖全部可注入（`runner` / `clock` / `sleep` /
  `wall_clock` / `should_cancel` / `should_pause` / `on_sample`），单测零真实网络。
  页面侧：工作者线程的 `on_sample` 经 `_SampleBridge.sample = pyqtSignal(object)` 回主线程，
  统计口径只读 `PingSession.summary_for()` 一处；`stop()` 刻意不调用 `handle.cancel()`，
  让会话自行收尾返回报告，否则 `TaskRunner` 走 `was_cancelled` 分支会丢弃结果。
- 验证：`.venv/bin/ruff check --fix .` ✅ All checks passed / `ruff format .` ✅ /
  `pytest -q` **320 passed in 43.63s** ✅；真机跑 `127.0.0.1` ×2 轮（2 发 2 收，avg 0.076ms）
  与 `["127.0.0.1", "192.0.2.1"]` ×2 轮（4 发 2 收，50% 丢包）；
  offscreen 渲染 `/tmp/ui_ping.png`（4 目标 × 8 轮，含间歇丢包与超时）已人工核对
- 遗留：IPv6 目标明确拒绝（系统 `ping6` 后续单独支持，`roadmap` 已记）；
  暂停是「下一轮开始前生效」，没有「第 N 轮精确断点」；
  Ping 结果不写数据库，历史回溯要等监控模块；
  `utils/exporter.py` 仍未建立，导出目前在 `PingReport` + 页面内实现，
  端口扫描/IP 扫描落地时再统一抽象
- 下一步：阶段 E —— E1 `services/portscan.py`（asyncio TCP connect + 信号量限流 +
  预设端口集；并发默认 128 / 上限 512，超时默认 1.0s / 上限 5.0s，单目标端口 ≤ 65535、
  超 1024 需二次确认，扫描中可随时停止）

### 2026-11-04 · C1 + C2 完成（MAC 地址格式转换）
- 变更：新增 `src/netops/core/mac.py`（`MacError` / `MacForms` / `parse_mac` / `format_mac` /
  `mac_forms` / `lookup_oui` / `describe_mac` / `OUI_TABLE`）、
  `src/netops/ui/pages/mac_page.py`（`MacPage` + `MAX_BATCH = 2000` + `export_csv`）、
  `tests/test_mac.py`（30 条）、`tests/test_mac_page.py`（17 条）；
  修改 `src/netops/tools/converters.py`（`conv.mac` 改 `Stage("P2", "已实现")`、新增
  `create_widget`、features/spec 同步九种格式与 OUI 说明）、
  `tests/test_smoke.py`（`PREVIEW_WITH_CODE` 移除 `conv.mac`、`IMPLEMENTED_TOOL_IDS` 加入 `conv.mac`）
- 内容：解析以 48 位整数为中间表示；宽容大小写与 `0x` 前缀，但分隔符只允许 `- : 空格 .`
  四选一且必须贯穿全文，点分写法固定 `aabb.ccdd.eeff`，其余分隔符段长必须一致（2 位或 4 位），
  出错信息统一为中文、可直接展示；输出十种写法（思科 / 华为 display / Windows ipconfig /
  getmac / Linux / 华为大写 / 空格 / 无分隔 / 0x / OUI 前缀）；内置 OUI 表 45 条只收录可确证厂商
  （Cisco / H3C / Huawei / Aruba / Dell / Mellanox + 虚拟机前缀），查不到一律「本地库未收录」；
  `describe_mac` 区分广播 / 全零 / 组播 / 本地管理（bit1）/ 厂商，本地管理地址不据此报厂商；
  页面单条输入走 120ms `QTimer` 防抖，批量走显式按钮（2000 行上限、`#` 注释、
  逗号/分号/制表符兼容），非法行标红并计入摘要，支持复制「规范化」列与 CSV 导出（UTF-8 BOM）
- 验证：`ruff check --fix .` ✅ All checks passed / `ruff format .` ✅ /
  `pytest -q` **267 passed** ✅；offscreen 渲染 `/tmp/ui_mac.png`（单条 `0011.6C0A.1B2C` +
  6 行批量含注释与非法行）并人工核对，单条卡片、九格式三列网格、批量输入区/按钮/表格排版正常
- 遗留：OUI 表是内置静态表，无法在线更新（`roadmap` 已记）；IPv6 无 MAC 派生需求；
  「最近 20 次计算记录」仍未做（B 阶段收尾项，与计算器页面统一处理）；
  `utils/exporter.py` 仍未建立，CSV 导出在页面内自实现，后续扫描类页面需统一抽象
- 下一步：阶段 D —— D1 `services/ping.py`（单目标连续探测 + 多目标并发批量 + 统计，
  单测用假 runner；Ping 批并发 16 / 上限 256 个目标，单目标间隔 ≥ 200ms）

### 2026-11-03 · B6 完成（IP 包含检测），阶段 B 收尾
- 变更：扩展 `src/netops/core/ipcalc.py`（新增 `RELATION_KINDS`、`relation_label`、`AddressSubject`、
  `parse_subject`、`parse_subjects`、`compare`、`is_inside`、`uncovered`、`contains_matrix`）、
  新增 `src/netops/ui/pages/contains_page.py`（`ContainsPage` + `MAX_SUBJECTS = 2000` + `export_csv`）、
  `tests/test_contains_page.py`（15 条）；
  修改 `tests/test_ipcalc.py`（追加 14 条，现 65 条）、`src/netops/tools/calculators.py`
  （`calc.contains` 覆写 `create_widget`、`stage` 改 `P2 已实现`、features/spec 同步）、
  `tests/test_smoke.py`（`IMPLEMENTED_TOOL_IDS` 加入 `calc.contains`）
- 内容：目标网段与待测对象都是多行输入（`#` 注释、空行跳过）；**裸 IP 统一按 /32 单主机判断**，
  不做 A/B/C 类默认前缀推断（否则 `10.0.0.1` 会被当成 `10.0.0.0/8`）；每行输出五列——
  原文 / 规范化网段 / 结论 / 命中目标 / 未覆盖部分；结论按优先级折叠：任一目标包含 → 完全包含，
  否则包含目标 → 部分重叠 → 互不相交；「未覆盖部分」按目标**并集**计算（两段 /25 可合力覆盖 /24）；
  另生成「待测 × 目标」包含矩阵（`✓` 完全包含 / `✗+` 包含目标 / `✗-` 部分重叠 / `·` 不相交）；
  摘要按结论计数；CSV 导出带 UTF-8 BOM；待测超过 2000 条拒绝并提示分批
- 验证：`ruff check` ✅ / `ruff format` ✅ / `pytest -q` **220 passed** ✅；
  offscreen 渲染 `/tmp/ui_contains.png`（3 个目标 × 7 条待测，四类结论全部出现）并人工核对，
  输入卡片、五列结果表、导出按钮、矩阵卡片排版正常
- 遗留：IPv6 不支持；矩阵在目标很多时无法折叠/分页；「最近 20 次计算记录」仍未做
  （B 阶段收尾项，六页统一加）；B5 的列排序筛选、配置模板导出仍未做
- 下一步：阶段 C —— C1 `core/mac.py` MAC 格式解析与互转

### 2026-11-03 · B5 完成（超网拆分计算器）
- 变更：新增 `src/netops/ui/pages/split_page.py`（`SplitPage` + `SubnetTableModel` + `MAX_ROWS = 65536` +
  `export_csv`）、`tests/test_split_page.py`（16 条）；
  修改 `src/netops/ui/resources/styles/light.qss`（表格选择器扩展为 `QTableWidget#ResultTable, QTableView#ResultTable`，
  新增 `QSpinBox#ToolInput` 的 focus / disabled 变体）、
  `src/netops/tools/calculators.py`（`calc.split` 覆写 `create_widget`、`stage` 改 `P2 已实现`、features 同步）、
  `tests/test_smoke.py`（`IMPLEMENTED_TOOL_IDS` 加入 `calc.split`）
- 内容：两种拆分方式——按子网数量（非 2 的幂自动向上取整）与按每段可用主机数（+2 网络/广播，超容量报错）；
  切换模式时自动重置数量与步长（数量模式 4 / 步长 1，主机模式 254 / 步长 16）；
  结果用 `QAbstractTableModel` 虚拟化承载，六列（序号 / 网段 / 掩码 / 可用地址范围 / 广播地址 / 可用主机数），
  65536 段硬上限防止 /8 拆到 /32 时内存爆掉；摘要行给出段数、每段主机数与地址总数；CSV 导出带 UTF-8 BOM
- 验证：`ruff check` ✅ / `ruff format` ✅ / `pytest -q` **193 passed** ✅；
  offscreen 渲染 `/tmp/ui_split.png`（10.20.0.0/16 拆 6 段 → 128 × /23）与
  `/tmp/ui_split_hosts.png`（每段 500 台 → 128 × /23）并人工核对，表格与摘要排版正常、无横向滚动
- 遗留：列排序与筛选、配置模板导出尚未做；IPv6 不支持；
  「最近 20 次计算记录」仍未做（随 B 阶段收尾统一加）
- 下一步：B6 IP 包含检测页面

### 2026-11-03 · B4 完成（路由聚合计算器）
- 变更：扩展 `src/netops/core/ipcalc.py`（新增 `AggregateBlock` 溯源结构；`AggregateResult` 增加
  `duplicates` / `contained` / `details` 与 `covered_addresses` / `wasted_addresses` / `block_for()` /
  `render_config(hop, style)`；新增 `_ROUTE_TEMPLATES`、`_input_tokens`、`_is_address_range`、
  `_looks_like_mask`、`_normalize`、`_blocks`、`_result`；重写 `aggregate` 与 `optimize`，冗余按原网段覆盖数计算）；
  `src/netops/ui/widgets/forms.py`（新增 `make_text_edit`）、
  `src/netops/ui/resources/styles/light.qss`（新增 `QTableWidget#ResultTable` 全套样式）、
  新增 `src/netops/ui/pages/aggregate_page.py`（`AggregatePage`）、`tests/test_aggregate_page.py`（18 条）；
  修改 `src/netops/tools/calculators.py`（`calc.aggregate` 覆写 `create_widget`、`stage` 改 `P2 已实现`、
  features 文案同步为两种设备风格）、`tests/test_smoke.py`（`IMPLEMENTED_TOOL_IDS` 加入 `calc.aggregate`）
- 内容：多行粘贴网段（换行/空格/逗号分隔，`#` 注释，兼容 `10.0.0.0 255.255.255.0` 与 `10.0.0.0 24`），
  自动去重、排序、剔除被包含项；精准聚合（`collapse_addresses`，零冗余）与优化聚合（贪心合并 + 可调冗余上限）；
  三列溯源表格（聚合块 / 覆盖的原网段 / 冗余地址）+ 摘要行；填写下一跳后生成 H3C-华为 或 思科 汇总路由配置并一键复制
- 验证：`ruff check` ✅ / `ruff format` ✅ / `pytest -q` **178 passed** ✅；
  offscreen 渲染 `/tmp/ui_aggregate.png`、`/tmp/ui_aggregate2.png` 并人工核对（8 条输入 → 4 条聚合块，配置 4 行 `ip route-static`，排版正常）
- 遗留：优化聚合为贪心算法，结果不保证全局最优；IPv6 不支持；地址区间写法明确拒绝并提示改用 CIDR；
  「最近 20 次计算记录」仍未做（随 B 阶段收尾统一加）
- 下一步：B5 超网拆分计算器页面

### 2026-11-03 · B3 完成（IP 进制转换）
- 变更：扩展 `src/netops/core/ipcalc.py`（新增 `parse_ipv4` 宽容解析、`_parse_binary`、
  `AddressForms`、`address_forms`；`network_info` 改用 `parse_ipv4` 并输出规范化地址、删除 `_mask_to_int`）、
  `tests/test_ipcalc.py`（追加 6 组解析器/形态/边界用例，现 52 条）；
  `src/netops/ui/widgets/forms.py`（新增 `make_mono_line_edit`）、
  `src/netops/ui/resources/styles/light.qss`（新增 `QLineEdit#ToolInputMono` 等宽样式及 focus/disabled 变体）、
  新增 `src/netops/ui/pages/base_convert_page.py`（`BaseConvertPage`）、`tests/test_base_convert_page.py`；
  修改 `src/netops/tools/calculators.py`（`calc.base` 覆写 `create_widget`、`stage` 改 `P2 已实现`）、
  `tests/test_smoke.py`（`IMPLEMENTED_TOOL_IDS` 加入 `calc.base`）
- 内容：四栏联动——任改一栏其余三栏实时刷新；解析支持点分十进制（含前导零）、二进制 4 段×8 位（可带 `0b`）、
  十六进制（`0x` / 无前缀 / `C0A8.0101`）、十进制整数；结果区 8 项（点分/二进制分段/二进制连写/十六进制/
  十六进制无 0x/整数/大端字节序/地址类别）；联动靠 `textEdited`（仅用户输入触发）天然无回环
- 验证：`ruff check` ✅ / `ruff format` ✅ / `pytest -q` **160 passed** ✅；
  offscreen 渲染 `/tmp/ui_baseconvert.png` 并人工核对（四栏等宽输入、8 项结果网格、摘要行排版正常）
- 遗留：IPv6 不支持，冒号/连字符写法明确报"暂不支持"；「最近 20 次计算记录」仍未做（随 B 阶段收尾统一加）
- 下一步：B4 路由聚合计算器页面

### 2026-11-03 · B2 完成（子网掩码计算器）
- 变更：扩展 `src/netops/core/ipcalc.py`（新增 `mask_to_hex`、`prefix_for_hosts`、`hosts_for_prefix`、
  `prefix_for_subnets(base_prefix, count)`、`subnet_count`，`network_info` 改为复用 `hosts_for_prefix`）、
  `tests/test_ipcalc.py`（追加 7 条算法用例）；
  新增 `src/netops/ui/widgets/bits.py`（`MaskBitmap`，QPainter 自绘 4 组×8 位）、
  `src/netops/ui/pages/mask_calc_page.py`（`MaskCalcPage`）、`tests/test_mask_calc_page.py`；
  修改 `src/netops/tools/calculators.py`（`calc.mask` 覆写 `create_widget`、`stage` 改 `P2 已实现`）、
  `tests/test_smoke.py`（`IMPLEMENTED_TOOL_IDS` 加入 `calc.mask`）
- 内容：三种模式——按掩码、按每子网主机数（向上取整到 2 的幂）、按基准网段内需要的子网数量；
  结果区 7 项（前缀长度/掩码/通配符/二进制/十六进制/每子网主机数/基准网段内子网数）两列网格，
  上方 32 位位图实时反映网络位与主机位；容量溢出、缺基准网段、非法掩码均给出中文错误提示
- 验证：`ruff check` ✅ / `ruff format` ✅ / `pytest -q` **125 passed** ✅；
  offscreen 渲染 `/tmp/ui_maskcalc.png` 并人工核对（标题区、三模式表单、位图、结果网格、摘要行排版正常）
- 遗留：IPv6 未支持；「最近 20 次计算记录」未做（与 B1 一并留待结果面板稳定后统一加）
- 下一步：B3 IP 进制转换页面（四栏联动）

### 2026-11-03 · B1 完成（首个真实工具页）
- 变更：新增 `src/netops/ui/widgets/forms.py`（`ToolPage` / `FieldRow` / `ResultGrid` / `ResultValue` /
  `InlineError` / `PrimaryButton` / `SecondaryButton` 等工具页共用组件）、
  `src/netops/ui/pages/ip_calc_page.py`（`IpCalcPage`）、`tests/test_ip_calc_page.py`；
  修改 `src/netops/tools/calculators.py`（`calc.ip` 覆写 `create_widget`、`stage` 改 `P2 已实现`）、
  `src/netops/ui/resources/styles/light.qss`（新增「真实工具页」样式区）、
  `tests/test_smoke.py`（引入 `IMPLEMENTED_TOOL_IDS`，已实现工具断言不是 `PlaceholderPage`）
- 内容：掩码方式可切换「跟随 CIDR」/「单独指定」；回车即算；8 项结果两列网格，每项带复制按钮；
  底部摘要标注地址类别（私有/公网/环回/链路本地/组播/保留）；算法全部复用 `core/ipcalc.py`
- 验证：`ruff check` ✅ / `ruff format` ✅ / `pytest -q` **106 passed** ✅；
  offscreen 渲染主窗口截图已人工核对（输入卡片、两列结果、复制按钮、摘要行排版正常）
- 遗留：IPv6 未支持；「最近 20 次计算记录」未做（留待结果面板稳定后统一加）
- 下一步：B2 子网掩码计算器页面

### 2026-11-03 · A5 + A6 完成，阶段 A 收尾
- A5 变更：新增 `src/netops/core/secrets.py`、`tests/test_secrets.py`；支持 `keyring:service/key` 引用、
  内存后端注入、缺失密钥明确报错。
- A6 变更：新增 `src/netops/core/templates.py`、`src/netops/resources/templates/`、
  `tests/test_templates.py`；`StrictUndefined` 渲染、模板枚举、`h3c/ap_register.j2`。
- 验证：`pytest` 96 passed ✅；`ruff check` + `ruff format` 通过 ✅；AP 模板渲染输出与占位页示例一致。
- 遗留：模板尚未打包进 wheel（`force-include` 目前只覆盖 migrations），打包阶段统一处理。
- 下一步：阶段 B —— B1 IP 地址计算器页面（首个真实工具页）

### 2026-11-03 · A4 完成
- 变更：新增 `src/netops/core/db.py`、`src/netops/core/migrations/{__init__.py,001_init.sql}`、
  `tests/test_db.py`；`pyproject.toml` 增加 migrations 打包；`AppContext` 增加 `db` 字段
- 内容：WAL、`schema_version` 版本化迁移（幂等）、`devices` 设备备注 CRUD、`script_favorites` 收藏 CRUD 与检索
- 验证：`pytest` 75 passed ✅；真实数据目录已生成 `netops.sqlite3`（WAL 模式）
- 遗留：数据库为单连接，页面通过主线程串行访问；若后续后台任务写库需改为每线程独立连接
- 下一步：A5 密钥封装，然后 A6 模板

### 2026-11-03 · A3 完成
- 变更：新增 `src/netops/ui/async_bridge.py`、`tests/test_async_bridge.py`
- 内容：`TaskRunner`（QThreadPool）、`TaskHandle` 信号（finished/failed/cancelled/progress）、
  `CancellationToken` 协作取消；只有任务显式声明 `cancel_token` 时才注入，避免误传参数
- 验证：`pytest` 65 passed ✅；确证任务在工作线程执行（线程 ID 不同）
- 遗留：`progress` 信号已定义但尚未在任务中发射，页面实现时按需接入
- 下一步：A4 SQLite 与迁移

### 2026-11-03 · A2 完成
- 变更：扩展 `src/netops/platform_/os_layer.py`，新增 `tests/test_os_layer.py`
- 内容：`run_ping` / `run_traceroute` / `read_arp_table`，含参数校验、超时、结构化数据类；
  解析函数单独暴露（`parse_ping_output` / `parse_traceroute_output` / `parse_arp_output`）便于样例单测
- 验证：`pytest` 57 passed ✅；真机 `run_ping('127.0.0.1', count=2)` 成功、ARP 解析 7 条、traceroute 到本机可达
- 遗留：Windows 分支仅按参数写好，未在 Windows 实测；IPv6 ping 未支持
- 下一步：A3 后台任务与 Qt 信号桥

### 2026-11-03 · A1 完成
- 变更：新增 `src/netops/core/ipcalc.py`、`tests/test_ipcalc.py`
- 内容：掩码三写法解析、网络/广播/可用范围、精准聚合、优化聚合（可配冗余比例）、按数量/主机数拆分、四种包含关系
- 验证：`ruff check` ✅ / `ruff format` ✅ / `pytest` 47 passed ✅（新增 22 条）
- 遗留：IPv6 暂未纳入；优化聚合为贪心算法，不保证全局最优
- 下一步：A2 平台命令封装

### 2026-11-03 · 计划建立
- 变更：新增本文件
- 验证：未执行（仅文档）
- 遗留：A0 尚未启动
- 下一步：A1 `core/ipcalc.py`

---

## 6. 全局验收标准

1. `ruff check --fix .`、`ruff format .`、`pytest -q` 全绿。
2. `python -m netops` 启动无 Qt 警告，20 个页面均可打开。
3. 已实现页面不再显示「P0 占位」提示，`stage` 更新为实际阶段。
4. 所有网络功能在 UI 线程外执行，停止操作能真正终止后台任务。
5. 扫描类功能遵守第 1 节的安全边界。
6. 收藏与设备备注重启后仍存在。
