# 路线图

| 阶段 | 内容 | 里程碑验收 |
|---|---|---|
| **P0 地基** | 三契约、配置系统、SQLite+迁移、os_layer、日志、最小 Qt 壳 | `pytest` 全绿，`python -m netops` 能起窗口 |
| **P1 监控闭环** | icmp/tcp Probe、调度器、状态机、AlertRouter、PushPlus、持久化队列、托盘、launchd | **真机端到端：断线→微信收到 DOWN→恢复收到 RECOVERY** |
| **P2 工具箱** | Ping、子网计算器全套、MAC 转换、端口/IP 扫描、Traceroute（接备注库） | 9 项基础功能可用 |
| **P3 H3C 专用** | AP 配置生成、脚本生成器（Jinja2+收藏）、维保查询、SNMP/AP 监控 Probe | 生成配置可直接粘贴到设备 |
| **P4 打磨跨平台** | 报表看板、PyInstaller 打包、Windows 适配（仅改 os_layer） | `.app` 双击可用 |

> P1 之所以不放到最后：监控是唯一需要"长时间运行"才能验证正确性的模块，
> 越早上线，越早暴露休眠、抖动、网络异常这些坑。