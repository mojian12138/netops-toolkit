# NetOps Toolkit

> 面向网络工程师的一体化桌面工具：**工具箱 + 监控告警平台**。
> macOS 优先（Intel / Apple Silicon），单机运行，无服务端。

## 为什么做它

日常工作里 Ping、端口扫描、子网计算、MAC 转换、Traceroute、H3C AP 配置、
开局脚本生成散落在十几个网站和小脚本里；同时"设备掉线了要有人告诉我"这件事
一直缺少轻量方案。这个项目把两者合并：**白天用它干活，全天用它盯网**。

## 核心能力

| 模块 | 状态 |
|---|---|
| 监控引擎（ICMP/TCP + 状态机防抖） | 🚧 P1 |
| PushPlus 推送（微信/短信/邮件/钉钉/飞书） | 🚧 P1 |
| 告警抑制（依赖抑制/维护窗口/聚合/限流） | 🚧 P1 |
| Ping 工具（并发 + 延迟趋势图） | ⏳ P2 |
| 端口扫描 / IP 扫描 | ⏳ P2 |
| 子网计算器套件（IP/掩码/进制/聚合/拆分/包含） | ⏳ P2 |
| MAC 地址格式转换 | ⏳ P2 |
| Traceroute（带 IP↔设备备注） | ⏳ P2 |
| H3C AP 注册配置生成 | ⏳ P3 |
| 开局脚本生成（华为/华三）+ 收藏 | ⏳ P3 |
| H3C 维保查询 | ⏳ P3 |
| 可用率报表 / 看板 | ⏳ P4 |
| Windows 打包 | ⏳ P4 |

## 设计要点

- **三条可扩展契约**：`Probe` / `Notifier` / `ToolPlugin`，新功能=新插件，不动核心。
- **监控独立进程**：UI 关了、崩了，照样告警；launchd 拉起，天然看门狗。
- **不用 root**：ICMP/Traceroute 走系统自带二进制，不碰 raw socket。
- **不丢告警**：通知队列持久化 + 指数退避重试。

详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)、[docs/ROADMAP.md](docs/ROADMAP.md)。

## 快速开始

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,macos]"
cp config/config.example.yaml config/config.yaml
python -m netops
```

## 安全

Token 保存于 macOS Keychain（keyring），配置文件中只存引用；日志与导出自动脱敏。
`config/config.yaml` 与 `data/` 已被 `.gitignore` 排除。

## License

MIT