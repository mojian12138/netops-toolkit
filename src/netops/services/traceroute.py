"""路由追踪服务：封装系统 ``traceroute`` 调用，逐跳匹配设备备注，零 Qt 依赖。

三层分工：

- ``platform_/os_layer.py``：执行 ``traceroute -n`` / ``tracert -d`` 并正则解析；
- 本模块：目标校验、跳数与超时约束、逐跳备注匹配、结果聚合与导出渲染；
- ``ui/pages/traceroute_page.py``：只负责录入、时间轴展示与信号转发。

为什么不自己发 UDP 探针：系统 ``traceroute`` 已经处理好不同平台的差异
（macOS 默认 UDP + 大端口、Linux 默认 UDP、Windows 只有 ICMP 的 ``tracert``），
且不需要提权。代价是只能拿到它吐出来的格式，因此解析与命令拼装全部收敛在
``os_layer``，本模块只面对 ``TracerouteResult`` 这一种结构化输入。

``hops`` 里同一跳可能有多条记录（``-q 1`` 时一般只有一条），这里按跳号折叠，
延迟取该跳的最小值——排障时更关心「这段路最快能多快」而不是平均值。
"""

from __future__ import annotations

import ipaddress
import logging
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from netops.platform_.os_layer import (
    DEFAULT_TRACEROUTE_MAX_HOPS,
    MAX_TRACEROUTE_HOPS,
    CommandError,
    TracerouteHop,
    TracerouteResult,
    run_traceroute,
)
from netops.services.devices import DeviceBook

__all__ = [
    "MAX_HOPS",
    "DEFAULT_HOPS",
    "TIMEOUT_PRESETS",
    "DEFAULT_TIMEOUT_S",
    "MAX_TIMEOUT_S",
    "TracerouteError",
    "HopInfo",
    "TracerouteReport",
    "parse_target",
    "resolve_max_hops",
    "annotate_report",
    "TracerouteSession",
]

_log = logging.getLogger(__name__)

#: 最大跳数上限，直接复用 os_layer 的安全边界。
MAX_HOPS = MAX_TRACEROUTE_HOPS
DEFAULT_HOPS = DEFAULT_TRACEROUTE_MAX_HOPS
#: 单探针等待上限（秒）——超过 5s 基本可以判定该跳不回应。
MAX_TIMEOUT_S = 5.0
DEFAULT_TIMEOUT_S = 2.0
#: 下拉可选的超时档位（秒）。
TIMEOUT_PRESETS = (1.0, 2.0, 3.0, 5.0)

#: 主机名只允许字母数字与 ``.-_``，避免拼进 argv 时出现奇怪字符。
_HOSTNAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,251}[A-Za-z0-9])?$")
#: 纯数字加点只可能是想输 IPv4；解析失败就是输错了，不能退回主机名分支。
_NUMERIC = re.compile(r"^[0-9.]+$")


class TracerouteError(ValueError):
    """用户输入或参数非法。消息面向用户，可直接展示。"""


@dataclass(frozen=True, slots=True)
class HopInfo:
    """折叠后的一跳。

    ``device`` / ``location`` 来自 ``devices`` 表；未备注的设备为空串。
    """

    hop: int
    address: str
    rtt_ms: float | None
    timed_out: bool
    name: str = ""
    location: str = ""
    model: str = ""

    @property
    def display(self) -> str:
        """时间轴上显示的地址；有备注时拼成「备注 (IP)」。"""
        if self.timed_out or not self.address:
            return "*"
        if self.name:
            return f"{self.name} ({self.address})"
        return self.address

    @property
    def remark(self) -> str:
        """备注列的纯文本。"""
        parts = [part for part in (self.name, self.location, self.model) if part]
        return " · ".join(parts)


@dataclass(frozen=True, slots=True)
class TracerouteReport:
    """一次路由追踪的完整结果。"""

    target: str
    resolved: str
    hops: tuple[HopInfo, ...]
    reached: bool
    max_hops: int
    timeout_s: float
    started_at: datetime
    finished_at: datetime
    duration_s: float
    cancelled: bool

    @property
    def hop_count(self) -> int:
        return len(self.hops)

    @property
    def timeouts(self) -> tuple[HopInfo, ...]:
        return tuple(item for item in self.hops if item.timed_out)

    @property
    def known_devices(self) -> tuple[HopInfo, ...]:
        return tuple(item for item in self.hops if item.name)

    def csv_rows(self) -> list[list[str]]:
        rows = [["hop", "address", "rtt_ms", "timed_out", "name", "location", "model"]]
        for item in self.hops:
            rows.append(
                [
                    str(item.hop),
                    item.address,
                    "" if item.rtt_ms is None else f"{item.rtt_ms:.3f}",
                    "1" if item.timed_out else "0",
                    item.name,
                    item.location,
                    item.model,
                ]
            )
        return rows

    def to_markdown(self) -> str:
        lines = [
            "# 路由追踪报告",
            "",
            f"- 目标：{self.target}"
            + (f"（解析为 {self.resolved}）" if self.resolved != self.target else ""),
            f"- 追踪开始：{self.started_at:%Y-%m-%d %H:%M:%S}",
            f"- 追踪结束：{self.finished_at:%Y-%m-%d %H:%M:%S}",
            f"- 追踪时长：{self.duration_s:.1f} 秒",
            f"- 最大跳数：{self.max_hops}，单探针等待 {self.timeout_s:.1f} 秒",
            f"- 结果：{self.hop_count} 跳，"
            + ("已到达目标" if self.reached else "未确认到达目标")
            + ("（已手动停止）" if self.cancelled else ""),
            "",
            "## 路径",
            "",
            "| 跳 | 地址 | 延迟 (ms) | 备注 |",
            "|---:|---|---:|---|",
        ]
        for item in self.hops:
            address = "*" if item.timed_out else item.address
            rtt = "—" if item.rtt_ms is None else f"{item.rtt_ms:.1f}"
            lines.append(f"| {item.hop} | {address} | {rtt} | {item.remark or '—'} |")
        if not self.hops:
            lines.append("| — | — | — | — |")
        lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 输入校验
# ---------------------------------------------------------------------------


def parse_target(text: str) -> str:
    """校验并规范化目标地址（IP 或主机名）。

    返回规范化后的字符串：IP 走 ``ipaddress`` 归一化，主机名做小写与长度校验。
    """
    value = text.strip()
    if not value:
        raise TracerouteError("目标地址不能为空，例如 8.8.8.8 或 www.example.com")
    if len(value) > 253:
        raise TracerouteError("目标地址过长（超过 253 个字符）")
    if " " in value or "\t" in value:
        raise TracerouteError("目标地址不能包含空格")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        # 纯数字和点的串只可能是想输 IPv4，解析失败就报错，别再当主机名放行，
        # 否则 `256.1.1.1` 会被原样交给 traceroute，得到的是 `Name or service
        # not known` 这类没头没尾的系统报错。
        if _NUMERIC.match(value):
            raise TracerouteError(f"不是合法的 IPv4 地址：{value}") from None
        if not _HOSTNAME.match(value):
            raise TracerouteError(
                f"不是合法的 IP 或主机名：{value}（主机名只允许字母、数字、点、连字符、下划线）"
            ) from None
        return value.lower()
    if isinstance(address, ipaddress.IPv6Address):
        raise TracerouteError(f"暂不支持 IPv6 目标：{value}")
    return str(address)


def resolve_max_hops(value: int) -> int:
    """校验最大跳数。"""
    if not 1 <= value <= MAX_HOPS:
        raise TracerouteError(f"最大跳数必须在 1-{MAX_HOPS} 之间")
    return value


def resolve_timeout(value: float) -> float:
    """校验单探针等待时间（秒）。"""
    if not 0.1 <= value <= MAX_TIMEOUT_S:
        raise TracerouteError(f"单探针等待必须在 0.1-{MAX_TIMEOUT_S:.1f} 秒之间")
    return float(value)


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------


def _fold(raw: Iterable[TracerouteHop]) -> tuple[HopInfo, ...]:
    """把同一跳的多条探针记录折叠成一条，延迟取最小值。"""
    merged: dict[int, HopInfo] = {}
    for item in raw:
        previous = merged.get(item.hop)
        if previous is None:
            merged[item.hop] = HopInfo(
                hop=item.hop,
                address=item.address,
                rtt_ms=item.rtt_ms,
                timed_out=item.timed_out,
            )
            continue
        # 同跳有响应优先于超时；都有响应时取更小的延迟。
        if previous.timed_out and not item.timed_out:
            merged[item.hop] = HopInfo(
                hop=item.hop,
                address=item.address,
                rtt_ms=item.rtt_ms,
                timed_out=False,
            )
        elif not item.timed_out and item.rtt_ms is not None:
            best = previous.rtt_ms
            merged[item.hop] = HopInfo(
                hop=previous.hop,
                address=previous.address or item.address,
                rtt_ms=item.rtt_ms if best is None else min(best, item.rtt_ms),
                timed_out=False,
            )
    return tuple(merged[key] for key in sorted(merged))


class TracerouteSession:
    """一次路由追踪：调用 ``run_traceroute``。

    ``run()`` 是阻塞调用，交给 ``TaskRunner.submit()`` 执行；会话本身不依赖 Qt。

    **不在工作线程里读设备备注**：``Database`` 是单连接（``sqlite3`` 默认
    ``check_same_thread=True``），从 ``TaskRunner`` 的工作线程去查会直接抛
    ``ProgrammingError``。备注匹配统一由 UI 线程在拿到结果后调用
    ``annotate_report()`` 完成。
    """

    def __init__(
        self,
        target: str,
        *,
        max_hops: int = DEFAULT_HOPS,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        process_timeout_s: float = 60.0,
        probe: str = "udp",
        runner: Callable[..., TracerouteResult] = run_traceroute,
        should_cancel: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.target = parse_target(target)
        self.max_hops = resolve_max_hops(max_hops)
        self.timeout_s = resolve_timeout(timeout_s)
        self._process_timeout = process_timeout_s
        self.probe = probe
        self._runner = runner
        self._should_cancel = should_cancel or (lambda: False)
        self._clock = clock
        self._wall_clock = wall_clock

    # -- 对外 ---------------------------------------------------------------
    def run(self) -> TracerouteReport:
        """阻塞执行追踪；``should_cancel`` 生效时返回空路径的报告。"""
        started_at = self._wall_clock()
        t0 = self._clock()
        cancelled = self._should_cancel()
        resolved = self.target
        hops: tuple[HopInfo, ...] = ()
        reached = False

        if not cancelled:
            try:
                result = self._runner(
                    self.target,
                    max_hops=self.max_hops,
                    timeout_s=self.timeout_s,
                    process_timeout_s=self._process_timeout,
                    probe=self.probe,
                )
            except CommandError as exc:
                detail = f"\n命令输出：{exc.output.strip()[-1000:]}" if exc.output.strip() else ""
                raise TracerouteError(f"{exc}{detail}") from exc
            hops = _fold(result.hops)
            resolved = result.target or self.target
            reached = result.reached
            cancelled = self._should_cancel()

        return TracerouteReport(
            target=self.target,
            resolved=resolved,
            hops=hops,
            reached=reached,
            max_hops=self.max_hops,
            timeout_s=self.timeout_s,
            started_at=started_at,
            finished_at=self._wall_clock(),
            duration_s=max(self._clock() - t0, 0.0),
            cancelled=cancelled,
        )


def annotate_report(report: TracerouteReport, book: DeviceBook) -> TracerouteReport:
    """给每一跳补上设备备注，返回新报告（``hop`` 不可变，只能重建）。

    一次 SQL 查完全部 IP。**必须在 UI 线程调用**：``Database`` 单连接绑定在创建它的
    线程上，工作线程里查会抛 ``ProgrammingError``（再看 ``DeviceBook.remarks_for``
    的兜底，结果就是备注静默全空）。
    """
    addresses = [item.address for item in report.hops if not item.timed_out and item.address]
    devices = book.remarks_for(addresses)
    if not devices:
        return report
    hops = tuple(
        HopInfo(
            hop=item.hop,
            address=item.address,
            rtt_ms=item.rtt_ms,
            timed_out=item.timed_out,
            name=device.remark if (device := devices.get(item.address)) else "",
            location=device.location if device else "",
            model=device.model if device else "",
        )
        for item in report.hops
    )
    return TracerouteReport(
        target=report.target,
        resolved=report.resolved,
        hops=hops,
        reached=report.reached,
        max_hops=report.max_hops,
        timeout_s=report.timeout_s,
        started_at=report.started_at,
        finished_at=report.finished_at,
        duration_s=report.duration_s,
        cancelled=report.cancelled,
    )
