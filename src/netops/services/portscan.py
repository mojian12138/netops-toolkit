"""端口扫描服务：asyncio TCP connect 探测 + Banner 抓取，零 Qt 依赖。

三层分工：

- 本模块：目标 / 端口解析、并发限流、连接探测、Banner 采集、结果聚合与导出；
- ``ui/pages/portscan_page.py``：只负责录入、展示与信号转发；
- 平台差异：本模块只用标准库 ``asyncio`` 的 ``open_connection``（普通 TCP
  connect），不开 raw socket、不需要 sudo，因此无需经过 ``platform_/os_layer.py``。

安全边界（写死在常量里）：单次目标 ≤ 256、端口 ≤ 4096、并发 ≤ 512。
之所以默认并发 128 而不是更高：家用 / 企业接入设备对 SYN 洪泛很敏感，
并发拉满容易把交换机管理面打挂，上限留到 512 仅给回环 / 实验室场景用。
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import re
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

__all__ = [
    "MAX_TARGETS",
    "MAX_PORTS",
    "DEFAULT_CONCURRENCY",
    "MAX_CONCURRENCY",
    "DEFAULT_TIMEOUT_S",
    "MAX_TIMEOUT_S",
    "SLOW_SERVICE_TIMEOUT_S",
    "PORT_PRESETS",
    "SERVICE_NAMES",
    "PortScanError",
    "PortResult",
    "TargetResult",
    "ScanReport",
    "parse_targets",
    "parse_ports",
    "preset_ports",
    "guess_service",
    "parse_banner",
    "PortScanner",
]

_log = logging.getLogger(__name__)

#: 单次扫描的目标上限。
MAX_TARGETS = 256
#: 单次扫描的端口上限（目标数 × 端口数 也不应过万，见 ``check_workload``）。
MAX_PORTS = 4096
#: 并发连接数：默认 128，上限 512。
DEFAULT_CONCURRENCY = 128
MAX_CONCURRENCY = 512
#: 连接超时（秒）：常规 1.0，慢速服务（跨公网 / 老设备）5.0。
DEFAULT_TIMEOUT_S = 1.0
SLOW_SERVICE_TIMEOUT_S = 5.0
MAX_TIMEOUT_S = 10.0
#: Banner 抓取单独留的超时，避免开放端口拖着整个扫描不结束。
BANNER_TIMEOUT_S = 1.5
#: 单次扫描读取 Banner 的字节上限。
MAX_BANNER_BYTES = 1024

#: ``目标数 × 端口数`` 超过这个量级时 UI 需要弹确认（约 6.5 万个连接）。
WORKLOAD_WARN = 65_536

_SPLIT = re.compile(r"[\s,;，；]+")
#: 纯数字和点的串只可能是想输 IPv4，校验失败就报错，不要再当域名放行。
_NUMERIC = re.compile(r"^[0-9.]+$")
#: 主机名允许字母 / 数字 / 连字符 / 点，长度遵循 DNS 限制。
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)
#: 端口区间 / 末段区间：``1-1024``、``192.168.1.10-12``。
_RANGE = re.compile(r"^(\d+)\s*-\s*(\d+)$")
#: 完整 IP 的末段区间，必须在整条 IP 上匹配，否则会落到 ``_RANGE`` 的分支里。
_IP_RANGE = re.compile(r"^(\d{1,3}\.\d{1,3}\.\d{1,3}\.)(\d{1,3})\s*-\s*(\d{1,3})$")


#: 常见端口预设集。键为中文名，值是端口元组——顺序即 UI 展示顺序。
PORT_PRESETS: dict[str, tuple[int, ...]] = {
    "Web": (80, 443, 8000, 8080, 8081, 8443, 8888, 9000),
    "远程登录": (22, 23, 3389, 5900, 5901, 5902, 5985, 5986),
    "数据库": (1433, 1521, 2181, 3306, 5432, 6379, 9200, 11211, 27017),
    "设备管理": (21, 161, 162, 830, 2000, 5060, 8089, 9090, 10000),
}

#: 端口 → 服务名（用于开放端口的初步识别）。
SERVICE_NAMES: dict[int, str] = {
    20: "ftp-data",
    21: "ftp",
    22: "ssh",
    23: "telnet",
    25: "smtp",
    53: "dns",
    67: "dhcp",
    69: "tftp",
    80: "http",
    110: "pop3",
    123: "ntp",
    135: "msrpc",
    137: "netbios-ns",
    139: "netbios-ssn",
    143: "imap",
    161: "snmp",
    162: "snmptrap",
    389: "ldap",
    443: "https",
    445: "smb",
    465: "smtps",
    514: "syslog",
    587: "submission",
    636: "ldaps",
    830: "netconf-ssh",
    873: "rsync",
    990: "ftps",
    993: "imaps",
    995: "pop3s",
    1080: "socks",
    1433: "mssql",
    1521: "oracle",
    1723: "pptp",
    2049: "nfs",
    2181: "zookeeper",
    2375: "docker",
    2376: "docker-tls",
    3000: "grafana",
    3306: "mysql",
    3389: "rdp",
    5432: "postgresql",
    5566: "sangfor-ssl",
    5601: "kibana",
    5672: "amqp",
    5900: "vnc",
    5938: "teamviewer",
    5985: "winrm-http",
    5986: "winrm-https",
    6379: "redis",
    6443: "kubernetes",
    7001: "weblogic",
    8000: "http-alt",
    8080: "http-proxy",
    8089: "splunk",
    8443: "https-alt",
    8888: "http-alt",
    9000: "php-fpm",
    9090: "prometheus",
    9200: "elasticsearch",
    9418: "git",
    10000: "webmin",
    11211: "memcached",
    27017: "mongodb",
}

#: Banner 里的关键字 → 服务名。按顺序匹配，先到先得（越具体越靠前）。
_BANNER_HINTS: tuple[tuple[str, str], ...] = (
    ("ssh-", "ssh"),
    ("h3c", "h3c-device"),
    ("huawei", "huawei-device"),
    ("ruijie", "ruijie-device"),
    ("cisco", "cisco-device"),
    ("comware", "h3c-device"),
    ("vsftpd", "ftp"),
    ("proftpd", "ftp"),
    ("pure-ftpd", "ftp"),
    ("filezilla", "ftp"),
    ("openssh", "ssh"),
    ("dropbear", "ssh"),
    ("microsoft smtp", "smtp"),
    ("postfix", "smtp"),
    ("exim", "smtp"),
    ("dovecot", "imap"),
    ("mysql", "mysql"),
    ("mariadb", "mysql"),
    ("postgresql", "postgresql"),
    ("redis", "redis"),
    ("mongodb", "mongodb"),
    ("memcached", "memcached"),
    ("rabbitmq", "amqp"),
    ("elasticsearch", "elasticsearch"),
    ("nginx", "http"),
    ("apache", "http"),
    ("http/1.", "http"),
    ("tomcat", "http"),
    ("weblogic", "weblogic"),
    ("sangfor", "ssl-vpn"),
    ("ssl-vpn", "ssl-vpn"),
    ("vnc", "vnc"),
    ("telnet", "telnet"),
)


class PortScanError(ValueError):
    """用户输入或参数非法。消息面向用户，可直接展示。"""


@dataclass(frozen=True, slots=True)
class PortResult:
    """一个 (目标, 端口) 的探测结果。"""

    host: str
    port: int
    state: str  # "open" / "closed" / "filtered"
    service: str
    banner: str = ""
    latency_ms: float | None = None
    error: str | None = None

    @property
    def open(self) -> bool:
        return self.state == "open"

    @property
    def display_service(self) -> str:
        return self.service or guess_service(self.port)


@dataclass(slots=True)
class TargetResult:
    """单目标的聚合结果，按主机展示时用。"""

    host: str
    ports: list[PortResult] = field(default_factory=list)
    error: str | None = None

    @property
    def open_ports(self) -> list[PortResult]:
        return [item for item in self.ports if item.open]

    @property
    def is_alive(self) -> bool:
        return bool(self.open_ports)


@dataclass(frozen=True, slots=True)
class ScanReport:
    """一次扫描的完整结果。"""

    targets: tuple[str, ...]
    ports: tuple[int, ...]
    results: tuple[TargetResult, ...]
    started_at: datetime
    finished_at: datetime
    duration_s: float
    concurrency: int
    timeout_s: float
    cancelled: bool

    @property
    def open_count(self) -> int:
        return sum(len(item.open_ports) for item in self.results)

    @property
    def total_probes(self) -> int:
        return len(self.targets) * len(self.ports)

    @property
    def alive_hosts(self) -> tuple[str, ...]:
        return tuple(item.host for item in self.results if item.is_alive)

    def open_rows(self) -> list[tuple[str, int, str, str]]:
        """``(host, port, service, banner)``，只含开放端口，按主机 / 端口排序。"""
        rows: list[tuple[str, int, str, str]] = []
        for target in self.results:
            for item in sorted(target.open_ports, key=lambda p: p.port):
                rows.append((item.host, item.port, item.display_service, item.banner))
        return rows

    def csv_rows(self) -> list[list[str]]:
        rows = [["host", "port", "state", "service", "banner", "latency_ms", "error"]]
        for target in self.results:
            for item in sorted(target.ports, key=lambda p: p.port):
                rows.append(
                    [
                        item.host,
                        str(item.port),
                        item.state,
                        item.display_service,
                        item.banner.replace("\n", " ").replace("\r", " "),
                        "" if item.latency_ms is None else f"{item.latency_ms:.3f}",
                        item.error or "",
                    ]
                )
        return rows

    def to_markdown(self) -> str:
        lines = [
            "# 端口扫描报告",
            "",
            f"- 扫描开始：{self.started_at:%Y-%m-%d %H:%M:%S}",
            f"- 扫描结束：{self.finished_at:%Y-%m-%d %H:%M:%S}",
            f"- 扫描时长：{self.duration_s:.1f} 秒",
            f"- 目标数量：{len(self.targets)}，端口数量：{len(self.ports)}",
            f"- 探测总数：{self.total_probes:,}，开放端口：{self.open_count}",
            f"- 并发 {self.concurrency}，超时 {self.timeout_s:.1f} 秒"
            + ("（已手动停止）" if self.cancelled else ""),
            "",
            "## 存活主机与开放端口",
            "",
            "| 主机 | 端口 | 服务 | Banner |",
            "|---|---:|---|---|",
        ]
        for host, port, service, banner in self.open_rows():
            safe = banner.replace("|", "\\|")
            lines.append(f"| {host} | {port} | {service} | {safe or '—'} |")
        if not self.open_count:
            lines.append("| — | — | — | 未发现开放端口 |")
        lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def parse_targets(text: str, *, limit: int = MAX_TARGETS) -> tuple[str, ...]:
    """把用户输入解析成去重后的目标列表。

    支持单 IP、主机名、CIDR 网段（``192.168.1.0/28``）与短横线区间
    （``192.168.1.10-20``）混排；换行 / 逗号 / 分号 / 空白分隔，``#`` 后为注释。
    IPv6 明确拒绝（本版未封装）。
    """
    if limit < 1:
        raise PortScanError("目标上限必须为正整数")
    targets: list[str] = []
    seen: set[str] = set()
    invalid: list[str] = []
    ipv6: list[str] = []

    def add(value: str) -> None:
        key = value.casefold()
        if key in seen:
            return
        seen.add(key)
        targets.append(value)

    for line in text.splitlines():
        body = line.split("#", 1)[0]
        for token in _SPLIT.split(body):
            value = token.strip()
            if not value:
                continue
            try:
                expanded = _expand_target(value)
            except PortScanError:
                # 规模超限是有明确原因的错误，不能混进「格式不合法」里。
                raise
            except ValueError:
                invalid.append(value)
                continue
            if expanded == "ipv6":
                ipv6.append(value)
                continue
            for host in expanded:
                add(host)
            if len(targets) > limit:
                raise PortScanError(
                    f"目标 {len(targets)} 个，超过单次上限 {limit} 个，请缩小网段或分批扫描"
                )

    if invalid:
        joined = "、".join(invalid[:5]) + ("…" if len(invalid) > 5 else "")
        raise PortScanError(f"不是合法 IPv4 / 域名 / 网段：{joined}。")
    if ipv6:
        joined = "、".join(ipv6[:5]) + ("…" if len(ipv6) > 5 else "")
        raise PortScanError(f"暂不支持 IPv6 目标：{joined}。")
    if not targets:
        raise PortScanError("请至少输入一个目标地址")
    if len(targets) > limit:
        raise PortScanError(f"目标 {len(targets)} 个，超过单次上限 {limit} 个，请分批扫描")
    return tuple(targets)


def _expand_target(value: str) -> tuple[str, ...] | str:
    """单个 token → 主机列表；``"ipv6"`` 表示明确拒绝。校验失败抛 ``ValueError``。"""
    if "/" in value:
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError as exc:
            raise ValueError(value) from exc
        if not isinstance(network, ipaddress.IPv4Network):
            return "ipv6"
        if network.num_addresses > MAX_TARGETS:
            raise PortScanError(
                f"网段 {value} 含 {network.num_addresses} 个地址，超过单次上限 {MAX_TARGETS}，"
                "请拆分成更小的网段"
            )
        return tuple(str(host) for host in network.hosts()) or (str(network.network_address),)

    dash = _IP_RANGE.match(value)
    if dash:
        prefix, last, end = dash.group(1), int(dash.group(2)), int(dash.group(3))
        if not 0 <= last <= end <= 255:
            raise ValueError(value)
        return tuple(f"{prefix}{index}" for index in range(last, end + 1))

    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        if _NUMERIC.match(value):
            raise ValueError(value) from None
        if not _HOSTNAME.match(value):
            raise ValueError(value) from None
        return (value,)
    return "ipv6" if isinstance(address, ipaddress.IPv6Address) else (value,)


def parse_ports(text: str) -> tuple[int, ...]:
    """把 ``1-1024,3389, 8080`` 这类输入解析成去重升序的端口列表。"""
    ports: set[int] = set()
    invalid: list[str] = []
    for raw in text.replace("\n", ",").split(","):
        token = raw.strip()
        if not token:
            continue
        dash = _RANGE.match(token)
        try:
            if dash:
                start, end = int(dash.group(1)), int(dash.group(2))
                if start > end:
                    start, end = end, start
                if start < 1 or end > 65535:
                    raise ValueError(token)
                ports.update(range(start, end + 1))
            elif token.isdigit():
                value = int(token)
                if not 1 <= value <= 65535:
                    raise ValueError(token)
                ports.add(value)
            else:
                invalid.append(token)
        except ValueError:
            invalid.append(token)
        if len(ports) > MAX_PORTS:
            raise PortScanError(f"端口数量超过单次上限 {MAX_PORTS} 个，请缩小范围")

    if invalid:
        joined = "、".join(invalid[:5]) + ("…" if len(invalid) > 5 else "")
        raise PortScanError(f"端口格式不合法：{joined}。可用 80 / 1-1024 / 80,443,1-1024。")
    if not ports:
        raise PortScanError("请至少指定一个端口")
    return tuple(sorted(ports))


def preset_ports(names: Iterable[str]) -> tuple[int, ...]:
    """按预设名合并端口，去重升序。未知预设名报错。"""
    merged: set[int] = set()
    unknown = [name for name in names if name not in PORT_PRESETS]
    if unknown:
        raise PortScanError(f"未知的端口预设：{'、'.join(unknown)}")
    for name in names:
        merged.update(PORT_PRESETS[name])
    return tuple(sorted(merged))


def check_workload(targets: int, ports: int, concurrency: int) -> str | None:
    """规模超限时返回给用户看的风险提示；正常返回 ``None``。"""
    if targets <= 0 or ports <= 0:
        return None
    probes = targets * ports
    if probes < WORKLOAD_WARN:
        return None
    return (
        f"本次将发起 {probes:,} 次连接（{targets} 目标 × {ports} 端口，并发 {concurrency}）。"
        "对生产设备可能触发防扫描策略，建议先小范围试扫。"
    )


def guess_service(port: int) -> str:
    return SERVICE_NAMES.get(port, "")


def parse_banner(raw: bytes | str, port: int = 0) -> tuple[str, str]:
    """从原始字节里提取 ``(banner 文本, 服务名)``。

    Banner 常含换行和不可打印字节：只保留可打印字符，压成单行并截断到
    ``MAX_BANNER_BYTES``；服务名先看关键字指纹，再看端口表，都没有则空串。
    """
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    # ``str.isprintable()`` 对 U+FFFD（解码替换符）也返回 True，二进制数据
    # 会留下一串 ``��``；因此只认 ASCII 可打印字符，其余一律压成空格。
    cleaned = "".join(char if " " <= char <= "~" else " " for char in text)
    cleaned = " ".join(cleaned.split())[:MAX_BANNER_BYTES]
    lowered = cleaned.casefold()
    for hint, service in _BANNER_HINTS:
        if hint in lowered:
            return cleaned, service
    matched = _match_version_service(lowered)
    if matched:
        return cleaned, matched
    return cleaned, guess_service(port)


def _match_version_service(lowered: str) -> str:
    """关键字的补充匹配：Banner 里带版本号但没命中主表的情况。"""
    if "http" in lowered:
        return "https" if "https" in lowered else "http"
    return ""


# ---------------------------------------------------------------------------
# 扫描器
# ---------------------------------------------------------------------------

ProbeCallback = Callable[[PortResult], None]


class PortScanner:
    """一次端口扫描会话：目标 × 端口，asyncio 信号量限流。

    ``run()`` 是阻塞调用（内部 ``asyncio.run``），交给 ``TaskRunner`` 的
    ``submit`` 执行；服务本身不依赖 Qt，也不创建线程。
    """

    def __init__(
        self,
        targets: Sequence[str],
        ports: Sequence[int],
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        grab_banner: bool = True,
        banner_timeout_s: float = BANNER_TIMEOUT_S,
        on_result: ProbeCallback | None = None,
        should_cancel: Callable[[], bool] | None = None,
        connect: Callable[..., object] | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        cleaned_targets = tuple(dict.fromkeys(item.strip() for item in targets if item.strip()))
        if not cleaned_targets:
            raise PortScanError("请至少输入一个目标地址")
        if len(cleaned_targets) > MAX_TARGETS:
            raise PortScanError(
                f"目标 {len(cleaned_targets)} 个，超过单次上限 {MAX_TARGETS} 个，请分批扫描"
            )
        cleaned_ports = tuple(dict.fromkeys(int(port) for port in ports))
        if not cleaned_ports:
            raise PortScanError("请至少指定一个端口")
        if len(cleaned_ports) > MAX_PORTS:
            raise PortScanError(f"端口 {len(cleaned_ports)} 个，超过单次上限 {MAX_PORTS} 个")
        for port in cleaned_ports:
            if not 1 <= port <= 65535:
                raise PortScanError(f"端口 {port} 超出 1-65535 范围")
        if not 1 <= concurrency <= MAX_CONCURRENCY:
            raise PortScanError(f"并发数必须在 1-{MAX_CONCURRENCY} 之间")
        if not 0.05 <= timeout_s <= MAX_TIMEOUT_S:
            raise PortScanError(f"超时必须在 0.05-{MAX_TIMEOUT_S:.0f} 秒之间")

        self.targets = cleaned_targets
        self.ports = tuple(sorted(cleaned_ports))
        self.concurrency = concurrency
        self.timeout_s = timeout_s
        self.grab_banner = grab_banner
        self.banner_timeout_s = banner_timeout_s
        self._on_result = on_result
        self._should_cancel = should_cancel or (lambda: False)
        self._connect = connect
        self._clock = clock
        self._wall_clock = wall_clock

        self._done = 0
        self._open_found = 0

    # -- 对外 ---------------------------------------------------------------
    @property
    def done(self) -> int:
        """已完成的探测数（进度展示用）。"""
        return self._done

    @property
    def total(self) -> int:
        return len(self.targets) * len(self.ports)

    @property
    def open_count(self) -> int:
        """已发现的开放端口数（进度展示用）。"""
        return self._open_found

    def run(self) -> ScanReport:
        """阻塞执行整轮扫描；``should_cancel`` 生效时返回已采集的部分。"""
        return asyncio.run(self.run_async())

    async def run_async(self) -> ScanReport:
        started_at = self._wall_clock()
        t0 = self._clock()
        cancelled = False
        semaphore = asyncio.Semaphore(self.concurrency)
        buckets: dict[str, list[PortResult]] = {host: [] for host in self.targets}

        async def one(host: str, port: int) -> None:
            nonlocal cancelled
            if self._should_cancel():
                cancelled = True
                return
            async with semaphore:
                if self._should_cancel():
                    cancelled = True
                    return
                result = await self._probe(host, port)
            buckets[host].append(result)
            self._done += 1
            if result.open:
                self._open_found += 1
            self._emit(result)

        tasks = [
            asyncio.create_task(one(host, port)) for host in self.targets for port in self.ports
        ]
        if tasks:
            await asyncio.gather(*tasks)
        cancelled = cancelled or self._should_cancel()

        results = tuple(
            TargetResult(
                host=host,
                ports=sorted(buckets[host], key=lambda item: item.port),
                error=None,
            )
            for host in self.targets
        )
        return ScanReport(
            targets=self.targets,
            ports=self.ports,
            results=results,
            started_at=started_at,
            finished_at=self._wall_clock(),
            duration_s=max(self._clock() - t0, 0.0),
            concurrency=self.concurrency,
            timeout_s=self.timeout_s,
            cancelled=cancelled,
        )

    # -- 内部 ---------------------------------------------------------------
    async def _probe(self, host: str, port: int) -> PortResult:
        start = self._clock()
        writer = None
        reader = None
        try:
            # open_connection 自身没有超时参数，统一用 wait_for 包一层。
            reader, writer = await asyncio.wait_for(self._open(host, port), timeout=self.timeout_s)
        except TimeoutError:
            return PortResult(host=host, port=port, state="filtered", service=guess_service(port))
        except (ConnectionRefusedError, OSError) as exc:
            state = "closed" if isinstance(exc, ConnectionRefusedError) else "filtered"
            return PortResult(
                host=host,
                port=port,
                state=state,
                service=guess_service(port),
                latency_ms=(self._clock() - start) * 1000,
            )
        except Exception as exc:  # noqa: BLE001 - 单点异常不能中断整轮扫描
            _log.debug("扫描 %s:%s 异常", host, port, exc_info=True)
            return PortResult(
                host=host,
                port=port,
                state="filtered",
                service=guess_service(port),
                error=f"{type(exc).__name__}: {exc}",
            )

        latency = (self._clock() - start) * 1000
        banner, service = "", guess_service(port)
        try:
            if self.grab_banner:
                banner, service = await self._read_banner(reader, port)
        finally:
            writer.close()
            # 关闭阶段的异常（对端已 RST）无所谓，不能污染探测结果。
            with contextlib.suppress(TimeoutError, OSError):
                await writer.wait_closed()
        return PortResult(
            host=host,
            port=port,
            state="open",
            service=service or guess_service(port),
            banner=banner,
            latency_ms=latency,
        )

    def _open(self, host: str, port: int):
        """建立 TCP 连接；测试可注入假 ``connect``。"""
        if self._connect is not None:
            return self._connect(host, port, timeout=self.timeout_s)
        return asyncio.open_connection(host, port, limit=MAX_BANNER_BYTES)

    async def _read_banner(self, reader: object, port: int) -> tuple[str, str]:
        """尽力读一段 Banner；读不到不算失败。"""
        read = getattr(reader, "read", None)
        if read is None:  # pragma: no cover - 防御
            return "", guess_service(port)
        try:
            raw = await asyncio.wait_for(read(MAX_BANNER_BYTES), timeout=self.banner_timeout_s)
        except (TimeoutError, OSError):
            return "", guess_service(port)
        except Exception:  # noqa: BLE001 - Banner 是附加信息，失败就留空
            _log.debug("读取 %s 端口 Banner 失败", port, exc_info=True)
            return "", guess_service(port)
        if not raw:
            return "", guess_service(port)
        return parse_banner(raw, port)

    def _emit(self, result: PortResult) -> None:
        if self._on_result is None:
            return
        try:
            self._on_result(result)
        except Exception:  # noqa: BLE001 - 展示层回调异常不影响扫描
            _log.debug("on_result 回调异常", exc_info=True)
