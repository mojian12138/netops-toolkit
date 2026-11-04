"""局域网扫描服务：ICMP 并发探测 + 系统 ARP 表双重确认，零 Qt 依赖。

三层分工：

- ``platform_/os_layer.py``：执行系统 ``ping`` 子进程、读取 ``arp -an`` / ``arp -a``；
- 本模块：网段解析与规模预估、分批并发探测、ARP 结果合并、厂商识别、导出渲染；
- ``ui/pages/ipscan_page.py``：只负责录入、展示与信号转发。

为什么两种手段都要用：ICMP 会被主机防火墙拦掉（Windows 默认就拦），
但 ARP 表在二层仍然有记录；反过来 ARP 表只覆盖同网段，跨网段必须靠 ICMP。
两者并用能把漏报压到最低，同时用 ``method`` 字段告诉用户这条结果是怎么来的。

安全边界（写死在常量里）：单次网段 ≤ 4096 个地址、并发 ≤ 128、分批 ≤ 256。
``on_host`` 在工作者线程里被调用，回调必须线程安全——Qt 侧用信号转发到主线程。
"""

from __future__ import annotations

import ipaddress
import logging
import re
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

from netops.core.mac import MacError, lookup_oui, parse_mac
from netops.platform_.os_layer import (
    ArpEntry,
    CommandError,
    PingResult,
    read_arp_table,
    run_ping,
)

__all__ = [
    "MAX_HOSTS",
    "DEFAULT_CONCURRENCY",
    "MAX_CONCURRENCY",
    "DEFAULT_TIMEOUT_MS",
    "MAX_TIMEOUT_MS",
    "BATCH_SIZE",
    "MODE_ICMP",
    "MODE_ARP",
    "MODE_BOTH",
    "MODES",
    "IpScanError",
    "HostResult",
    "ScanReport",
    "NetSpec",
    "parse_networks",
    "estimate",
    "vendor_for_mac",
    "IpScanSession",
]

_log = logging.getLogger(__name__)

#: 单次扫描的地址上限（含网络 / 广播地址）。
MAX_HOSTS = 4096
#: 并发探测数：默认 64，上限 128。
DEFAULT_CONCURRENCY = 64
MAX_CONCURRENCY = 128
#: 单次 ping 等待（毫秒）：局域网默认 500ms 足够，上限 5s。
DEFAULT_TIMEOUT_MS = 500
MAX_TIMEOUT_MS = 5000
#: 每批下发的地址数——一口气拉起上千个 ping 子进程会卡住系统。
BATCH_SIZE = 256

MODE_ICMP = "icmp"
MODE_ARP = "arp"
MODE_BOTH = "both"
#: 扫描策略名 → 中文说明。
MODES: dict[str, str] = {
    MODE_BOTH: "ICMP + ARP（推荐）",
    MODE_ICMP: "仅 ICMP",
    MODE_ARP: "仅 ARP 表",
}

_SPLIT = re.compile(r"[\s,;，；]+")
_CIDR = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3})/(\d{1,2})$")
_RANGE = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3})\s*-\s*(\d{1,3}(?:\.\d{1,3}){3})$")
#: 末段区间：``192.168.1.10-20``。
_TAIL_RANGE = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){2})\.(\d{1,3})\s*-\s*(\d{1,3})$")


class IpScanError(ValueError):
    """用户输入或参数非法。消息面向用户，可直接展示。"""


@dataclass(frozen=True, slots=True)
class NetSpec:
    """一次扫描覆盖的地址集合，附带来源描述。"""

    label: str
    hosts: tuple[str, ...]

    def __len__(self) -> int:
        return len(self.hosts)


@dataclass(frozen=True, slots=True)
class HostResult:
    """单台主机的扫描结果。

    ``method`` 取值：``icmp``（ICMP 有回应）、``arp``（仅 ARP 表里有）、
    ``icmp+arp``（两种都命中）。
    """

    ip: str
    alive: bool
    mac: str = ""
    vendor: str = ""
    method: str = ""
    rtt_ms: float | None = None
    error: str | None = None

    @property
    def discovered(self) -> bool:
        return self.alive


@dataclass(slots=True)
class _State:
    """扫描过程中的可变状态，只在会话内部使用。"""

    results: dict[str, HostResult] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScanReport:
    """一次局域网扫描的完整结果。"""

    nets: tuple[str, ...]
    hosts: tuple[str, ...]
    results: tuple[HostResult, ...]
    mode: str
    started_at: datetime
    finished_at: datetime
    duration_s: float
    concurrency: int
    timeout_ms: int
    cancelled: bool

    @property
    def total(self) -> int:
        return len(self.hosts)

    @property
    def alive(self) -> tuple[HostResult, ...]:
        return tuple(item for item in self.results if item.alive)

    @property
    def alive_count(self) -> int:
        return len(self.alive)

    @property
    def with_mac(self) -> tuple[HostResult, ...]:
        return tuple(item for item in self.alive if item.mac)

    def result_for(self, ip: str) -> HostResult | None:
        for item in self.results:
            if item.ip == ip:
                return item
        return None

    def csv_rows(self) -> list[list[str]]:
        rows = [["ip", "alive", "mac", "vendor", "method", "rtt_ms", "error"]]
        for item in self.results:
            if not item.alive:
                continue
            rows.append(
                [
                    item.ip,
                    "1",
                    item.mac,
                    item.vendor,
                    item.method,
                    "" if item.rtt_ms is None else f"{item.rtt_ms:.3f}",
                    item.error or "",
                ]
            )
        return rows

    def to_markdown(self) -> str:
        lines = [
            "# 局域网扫描报告",
            "",
            f"- 扫描开始：{self.started_at:%Y-%m-%d %H:%M:%S}",
            f"- 扫描结束：{self.finished_at:%Y-%m-%d %H:%M:%S}",
            f"- 扫描时长：{self.duration_s:.1f} 秒",
            f"- 扫描网段：{'、'.join(self.nets)}",
            f"- 地址总数：{self.total}，存活：{self.alive_count}",
            f"- 策略：{MODES.get(self.mode, self.mode)}，并发 {self.concurrency}，"
            f"超时 {self.timeout_ms} ms" + ("（已手动停止）" if self.cancelled else ""),
            "",
            "## 存活主机",
            "",
            "| IP | MAC | 厂商 | 探测方式 | 延迟 (ms) |",
            "|---|---|---|---|---:|",
        ]
        for item in self.alive:
            lines.append(
                f"| {item.ip} | {item.mac or '—'} | {item.vendor or '—'} "
                f"| {item.method or '—'} | {_fmt_ms(item.rtt_ms)} |"
            )
        if not self.alive_count:
            lines.append("| — | — | — | — | — |")
        lines.append("")
        return "\n".join(lines)


def _fmt_ms(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}"


# ---------------------------------------------------------------------------
# 网段解析
# ---------------------------------------------------------------------------


def parse_networks(text: str, *, limit: int = MAX_HOSTS) -> tuple[NetSpec, ...]:
    """把用户输入解析成一组待扫描地址。

    支持：

    - CIDR：``192.168.1.0/24``（自动去掉网络地址与广播地址）
    - 起止地址：``192.168.1.1-192.168.1.254``
    - 末段区间：``192.168.1.10-20``
    - 单个 IP：``192.168.1.1``

    换行 / 逗号 / 分号 / 空白分隔，``#`` 后为注释；结果跨写法去重。
    """
    if limit < 1:
        raise IpScanError("地址上限必须为正整数")
    specs: list[NetSpec] = []
    seen: set[str] = set()
    invalid: list[str] = []
    ipv6: list[str] = []

    for line in text.splitlines():
        body = line.split("#", 1)[0]
        for token in _SPLIT.split(body):
            value = token.strip()
            if not value:
                continue
            try:
                spec = _expand(value)
            except IpScanError:
                # 规模超限是有明确原因的错误，不能混进「格式不合法」里。
                raise
            except ValueError:
                if ":" in value:
                    ipv6.append(value)
                else:
                    invalid.append(value)
                continue
            fresh = tuple(ip for ip in spec.hosts if ip not in seen)
            for ip in fresh:
                seen.add(ip)
            if fresh:
                specs.append(NetSpec(label=spec.label, hosts=fresh))
            if len(seen) > limit:
                raise IpScanError(
                    f"地址 {len(seen)} 个，超过单次上限 {limit} 个，请缩小网段或分批扫描"
                )

    if invalid:
        joined = "、".join(invalid[:5]) + ("…" if len(invalid) > 5 else "")
        raise IpScanError(f"不是合法 IPv4 / 网段 / 区间：{joined}。")
    if ipv6:
        joined = "、".join(ipv6[:5]) + ("…" if len(ipv6) > 5 else "")
        raise IpScanError(f"暂不支持 IPv6 目标：{joined}。")
    if not specs:
        raise IpScanError("请至少输入一个网段，例如 192.168.1.0/24")
    return tuple(specs)


def _expand(value: str) -> NetSpec:
    """单个 token → ``NetSpec``。校验失败抛 ``ValueError`` / ``IpScanError``。"""
    cidr = _CIDR.match(value)
    if cidr:
        network = ipaddress.ip_network(value, strict=False)
        if network.num_addresses > MAX_HOSTS:
            raise IpScanError(
                f"网段 {value} 含 {network.num_addresses} 个地址，"
                f"超过单次上限 {MAX_HOSTS}，请拆分成更小的网段"
            )
        # /31 与 /32 没有网络/广播地址的概念，hosts() 会返回全部地址。
        hosts = tuple(str(host) for host in network.hosts())
        if not hosts:
            hosts = (str(network.network_address),)
        return NetSpec(label=value, hosts=hosts)

    span = _RANGE.match(value)
    if span:
        start = ipaddress.IPv4Address(span.group(1))
        end = ipaddress.IPv4Address(span.group(2))
        return _span(start, end, value)

    tail = _TAIL_RANGE.match(value)
    if tail:
        prefix, first, last = tail.group(1), int(tail.group(2)), int(tail.group(3))
        if not 0 <= first <= last <= 255:
            raise ValueError(value)
        start = ipaddress.IPv4Address(f"{prefix}.{first}")
        end = ipaddress.IPv4Address(f"{prefix}.{last}")
        return _span(start, end, value)

    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address):
        raise ValueError(value)
    return NetSpec(label=value, hosts=(str(address),))


def _span(start: ipaddress.IPv4Address, end: ipaddress.IPv4Address, label: str) -> NetSpec:
    if int(end) < int(start):
        start, end = end, start
    count = int(end) - int(start) + 1
    if count > MAX_HOSTS:
        raise IpScanError(f"区间 {label} 含 {count} 个地址，超过单次上限 {MAX_HOSTS}，请缩小范围")
    hosts = tuple(str(ipaddress.IPv4Address(value)) for value in range(int(start), int(end) + 1))
    return NetSpec(label=label, hosts=hosts)


def estimate(text: str) -> int:
    """只估算规模，不构造完整 IP 列表——给页面上的「预估」提示用。

    输入非法时抛 ``IpScanError``（消息面向用户，可直接展示），页面捕获后提示错误。
    """
    total = 0
    invalid: list[str] = []
    ipv6: list[str] = []
    for line in text.splitlines():
        body = line.split("#", 1)[0]
        for token in _SPLIT.split(body):
            value = token.strip()
            if not value:
                continue
            try:
                total += _count_only(value)
            except IpScanError:
                # 规模超限有明确原因，不能被下面的「格式不合法」吞掉。
                raise
            except ValueError:
                # 与 parse_networks 的口径保持一致：带冒号的当 IPv6 单独提示。
                (ipv6 if ":" in value else invalid).append(value)
    if invalid:
        joined = "、".join(invalid[:5]) + ("…" if len(invalid) > 5 else "")
        raise IpScanError(f"不是合法 IPv4 / 网段 / 区间：{joined}。")
    if ipv6:
        joined = "、".join(ipv6[:5]) + ("…" if len(ipv6) > 5 else "")
        raise IpScanError(f"暂不支持 IPv6 目标：{joined}。")
    if total == 0:
        raise IpScanError("请至少输入一个网段，例如 192.168.1.0/24")
    if total > MAX_HOSTS:
        raise IpScanError(f"地址 {total} 个，超过单次上限 {MAX_HOSTS} 个，请缩小网段")
    return total


def _count_only(value: str) -> int:
    """只算数量，不展开成 IP 列表。"""
    cidr = _CIDR.match(value)
    if cidr:
        network = ipaddress.ip_network(value, strict=False)
        if network.num_addresses > MAX_HOSTS:
            raise IpScanError(
                f"网段 {value} 含 {network.num_addresses} 个地址，"
                f"超过单次上限 {MAX_HOSTS}，请拆分成更小的网段"
            )
        hosts = network.num_addresses - 2 if network.prefixlen <= 30 else network.num_addresses
        return max(hosts, 1)
    span = _RANGE.match(value)
    if span:
        start = int(ipaddress.IPv4Address(span.group(1)))
        end = int(ipaddress.IPv4Address(span.group(2)))
        count = abs(end - start) + 1
        if count > MAX_HOSTS:
            raise IpScanError(f"区间 {value} 含 {count} 个地址，超过单次上限 {MAX_HOSTS}")
        return count
    tail = _TAIL_RANGE.match(value)
    if tail:
        first, last = int(tail.group(2)), int(tail.group(3))
        if not 0 <= first <= last <= 255:
            raise ValueError(value)
        return last - first + 1
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address):
        raise ValueError(value)
    return 1


def vendor_for_mac(mac: str) -> str:
    """MAC → 厂商名；解析失败或无记录返回空串（不抛异常，扫描不能因脏数据中断）。"""
    if not mac:
        return ""
    try:
        # parse_mac 返回 48 位整数，OUI 是高 24 位。
        value = parse_mac(mac)
    except (MacError, ValueError):
        return ""
    return lookup_oui(value >> 24) or ""


# ---------------------------------------------------------------------------
# 扫描会话
# ---------------------------------------------------------------------------

PingRunner = Callable[..., PingResult]
ArpReader = Callable[[], Sequence[ArpEntry]]
HostCallback = Callable[[HostResult], None]


class IpScanSession:
    """一次局域网扫描：ICMP 分批并发 + ARP 表合并。

    ``run()`` 是阻塞调用，交给 ``TaskRunner.submit()`` 执行；会话本身不依赖 Qt。
    """

    def __init__(
        self,
        hosts: Sequence[str],
        *,
        nets: Sequence[str] = (),
        mode: str = MODE_BOTH,
        concurrency: int = DEFAULT_CONCURRENCY,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        batch_size: int = BATCH_SIZE,
        on_host: HostCallback | None = None,
        should_cancel: Callable[[], bool] | None = None,
        ping_runner: PingRunner = run_ping,
        arp_reader: ArpReader = read_arp_table,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        cleaned = tuple(dict.fromkeys(item.strip() for item in hosts if item.strip()))
        if not cleaned:
            raise IpScanError("请至少输入一个网段，例如 192.168.1.0/24")
        if len(cleaned) > MAX_HOSTS:
            raise IpScanError(f"地址 {len(cleaned)} 个，超过单次上限 {MAX_HOSTS} 个，请分批扫描")
        if mode not in MODES:
            raise IpScanError(f"未知的扫描策略：{mode}（可选 {'、'.join(MODES)}）")
        if not 1 <= concurrency <= MAX_CONCURRENCY:
            raise IpScanError(f"并发数必须在 1-{MAX_CONCURRENCY} 之间")
        if not 100 <= timeout_ms <= MAX_TIMEOUT_MS:
            raise IpScanError(f"单次等待时间必须在 100-{MAX_TIMEOUT_MS} ms 之间")
        if not 1 <= batch_size <= MAX_HOSTS:
            raise IpScanError(f"分批大小必须在 1-{MAX_HOSTS} 之间")

        self.hosts = cleaned
        self.nets = tuple(dict.fromkeys(nets)) or cleaned
        self.mode = mode
        self.concurrency = concurrency
        self.timeout_ms = timeout_ms
        self.batch_size = batch_size
        self._on_host = on_host
        self._should_cancel = should_cancel or (lambda: False)
        self._ping = ping_runner
        self._arp = arp_reader
        self._clock = clock
        self._wall_clock = wall_clock
        self._state = _State()

    # -- 对外 ---------------------------------------------------------------
    @property
    def done(self) -> int:
        return len(self._state.results)

    @property
    def total(self) -> int:
        return len(self.hosts)

    @property
    def alive_count(self) -> int:
        return sum(1 for item in self._state.results.values() if item.alive)

    def run(self) -> ScanReport:
        """阻塞执行整轮扫描；``should_cancel`` 生效时返回已采集的部分。"""
        started_at = self._wall_clock()
        t0 = self._clock()
        cancelled = False

        if self.mode in {MODE_ICMP, MODE_BOTH}:
            cancelled = self._probe_icmp()
        if not cancelled and self.mode in {MODE_ARP, MODE_BOTH}:
            self._merge_arp()

        # 没被任何手段命中的地址也要出现在结果里，页面才能区分「扫描过但不在线」。
        for ip in self.hosts:
            if ip not in self._state.results:
                missing = HostResult(ip=ip, alive=False)
                self._state.results[ip] = missing

        results = tuple(self._state.results[ip] for ip in self.hosts if ip in self._state.results)
        return ScanReport(
            nets=self.nets,
            hosts=self.hosts,
            results=results,
            mode=self.mode,
            started_at=started_at,
            finished_at=self._wall_clock(),
            duration_s=max(self._clock() - t0, 0.0),
            concurrency=self.concurrency,
            timeout_ms=self.timeout_ms,
            cancelled=cancelled,
        )

    # -- 内部 ---------------------------------------------------------------
    def _probe_icmp(self) -> bool:
        """分批并发 ping；返回 ``True`` 表示中途被取消。"""
        cancelled = False
        for start in range(0, len(self.hosts), self.batch_size):
            if self._should_cancel():
                return True
            batch = self.hosts[start : start + self.batch_size]
            workers = min(self.concurrency, len(batch))
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="netops-ipscan"
            ) as pool:
                futures = [pool.submit(self._probe_one, ip) for ip in batch]
                for future in futures:
                    future.result()
            if self._should_cancel():
                cancelled = True
                break
        return cancelled

    def _probe_one(self, ip: str) -> None:
        if self._should_cancel():
            return
        process_timeout = max(2.0, self.timeout_ms / 1000 * 3)
        try:
            result = self._ping(
                ip,
                count=1,
                timeout_ms=self.timeout_ms,
                process_timeout_s=process_timeout,
            )
        except CommandError as exc:
            self._record(HostResult(ip=ip, alive=False, error=str(exc)))
            return
        except Exception as exc:  # noqa: BLE001 - 后台任务边界，单点失败不能中断整批
            _log.debug("扫描 %s 异常", ip, exc_info=True)
            self._record(HostResult(ip=ip, alive=False, error=f"{type(exc).__name__}: {exc}"))
            return

        if result.received <= 0:
            self._record(HostResult(ip=ip, alive=False))
            return
        self._record(
            HostResult(
                ip=ip,
                alive=True,
                method=MODE_ICMP,
                rtt_ms=result.avg_ms,
            )
        )

    def _merge_arp(self) -> None:
        """读 ARP 表，把 ICMP 漏掉的主机补上（仅限本次扫描范围内）。"""
        try:
            entries = self._arp()
        except CommandError as exc:
            _log.info("读取 ARP 表失败：%s", exc)
            return
        except Exception:  # noqa: BLE001 - ARP 是补充手段，失败不影响 ICMP 结果
            _log.debug("读取 ARP 表异常", exc_info=True)
            return

        wanted = set(self.hosts)
        for entry in entries:
            ip = entry.ip.strip()
            if ip not in wanted:
                continue
            existing = self._state.results.get(ip)
            vendor = vendor_for_mac(entry.mac)
            if existing is not None and existing.alive:
                merged = HostResult(
                    ip=ip,
                    alive=True,
                    mac=entry.mac,
                    vendor=vendor,
                    method=(
                        "icmp+arp" if existing.method == MODE_ICMP else existing.method or MODE_ARP
                    ),
                    rtt_ms=existing.rtt_ms,
                    error=existing.error,
                )
            else:
                merged = HostResult(
                    ip=ip, alive=True, mac=entry.mac, vendor=vendor, method=MODE_ARP
                )
            self._record(merged)

    def _record(self, result: HostResult) -> None:
        self._state.results[result.ip] = result
        if self._on_host is None:
            return
        try:
            self._on_host(result)
        except Exception:  # noqa: BLE001 - 展示层回调异常不影响扫描
            _log.debug("on_host 回调异常", exc_info=True)
