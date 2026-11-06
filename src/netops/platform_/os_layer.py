"""平台差异的唯一收口点。

业务层禁止出现 ``sys.platform`` / ``platform.system()`` 判断，
需要平台相关能力时一律从这里取。

本模块同时负责调用系统原生命令（ping / traceroute / arp）并解析输出，
全部无需提权；返回结构化结果，调用方不接触原始命令行。
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "NetOpsToolkit"

PY_VERSION = f"{sys.version_info.major}.{sys.version_info.minor}"

# 原生二进制路径：ICMP / Traceroute / ARP 都靠它们，全部无需提权。
PING_BIN = "/sbin/ping"
TRACEROUTE_BIN = "/usr/sbin/traceroute"
ARP_BIN = "/usr/sbin/arp"

# 命令默认值：业务层可覆盖，但不得突破安全上限。
DEFAULT_PING_TIMEOUT_MS = 1000
DEFAULT_PING_PACKET_SIZE = 56
MAX_PING_COUNT = 1000
DEFAULT_TRACEROUTE_MAX_HOPS = 20
MAX_TRACEROUTE_HOPS = 30
#: traceroute 支持的探测协议；默认 ICMP——UDP 高端口普遍被沿途防火墙过滤成全 ``*``。
TRACEROUTE_PROBES = ("icmp", "udp", "tcp")
DEFAULT_TRACEROUTE_PROBE = "icmp"
DEFAULT_TRACEROUTE_TCP_PORT = 443

__all__ = [
    "APP_NAME",
    "PY_VERSION",
    "PING_BIN",
    "TRACEROUTE_BIN",
    "ARP_BIN",
    "DEFAULT_PING_TIMEOUT_MS",
    "DEFAULT_PING_PACKET_SIZE",
    "MAX_PING_COUNT",
    "DEFAULT_TRACEROUTE_MAX_HOPS",
    "MAX_TRACEROUTE_HOPS",
    "TRACEROUTE_PROBES",
    "DEFAULT_TRACEROUTE_PROBE",
    "DEFAULT_TRACEROUTE_TCP_PORT",
    "CommandError",
    "PingReply",
    "PingResult",
    "TracerouteHop",
    "TracerouteResult",
    "ArpEntry",
    "is_macos",
    "is_windows",
    "platform_label",
    "data_dir",
    "display_font_family",
    "run_ping",
    "run_traceroute",
    "run_traceroute_stream",
    "parse_traceroute_hop_line",
    "read_arp_table",
]


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_windows() -> bool:
    return sys.platform == "win32"


def platform_label() -> str:
    """状态栏展示用的平台名。"""
    if is_macos():
        return "macOS"
    if is_windows():
        return "Windows"
    return "Linux"


def data_dir() -> Path:
    """用户数据目录（不自动创建，由使用方决定）。"""
    if is_macos():
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if is_windows():
        base = Path.home() / "AppData" / "Local"
        return base / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def display_font_family() -> str:
    """UI 首选字体。macOS 用系统 UI 字体，Windows 后面再补 Segoe UI。"""
    if is_macos():
        return "PingFang SC"
    if is_windows():
        return "Microsoft YaHei UI"
    return "Noto Sans CJK SC"


# -- 命令执行 -----------------------------------------------------------------


class CommandError(RuntimeError):
    """系统命令不可用或超时。消息面向用户，可直接展示。"""

    def __init__(self, message: str, *, output: str = "") -> None:
        super().__init__(message)
        self.output = output


@dataclass(frozen=True, slots=True)
class PingReply:
    seq: int
    ttl: int | None
    rtt_ms: float | None
    from_addr: str
    timed_out: bool = False


@dataclass(frozen=True, slots=True)
class PingResult:
    target: str
    replies: tuple[PingReply, ...]
    transmitted: int
    received: int
    packet_loss: float
    min_ms: float | None
    avg_ms: float | None
    max_ms: float | None
    raw: str = ""

    @property
    def ok(self) -> bool:
        return self.received > 0


@dataclass(frozen=True, slots=True)
class TracerouteHop:
    hop: int
    address: str
    rtt_ms: float | None
    timed_out: bool = False


@dataclass(frozen=True, slots=True)
class TracerouteResult:
    target: str
    hops: tuple[TracerouteHop, ...]
    reached: bool
    raw: str = ""


@dataclass(frozen=True, slots=True)
class ArpEntry:
    ip: str
    mac: str
    interface: str = ""
    flags: str = ""


def _run(argv: list[str], timeout_s: float) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CommandError(f"系统命令不存在：{argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or b""
        partial_err = exc.stderr or b""
        output = "\n".join(
            part.decode(errors="replace") if isinstance(part, bytes) else part
            for part in (partial, partial_err)
            if part
        )
        raise CommandError(
            f"命令执行超时（{timeout_s:.1f}s）：{' '.join(argv)}", output=output
        ) from exc
    output = (completed.stdout or "") + (completed.stderr or "")
    return completed.returncode, output


_PING_REPLY = re.compile(
    r"(\d+)\s+bytes from\s+([^:\s]+):.*?icmp_seq[=:](\d+)\s+ttl[=:](\d+)"
    r".*?time[=<]([\d.]+)\s*ms",
    re.IGNORECASE,
)
_PING_REPLY_NO_TTL = re.compile(
    r"(\d+)\s+bytes from\s+([^:\s]+):.*?icmp_seq[=:](\d+)"
    r".*?time[=<]([\d.]+)\s*ms",
    re.IGNORECASE,
)
_PING_TIMEOUT = re.compile(r"Request timeout for icmp_seq\s+(\d+)", re.IGNORECASE)
_PING_STATS = re.compile(
    r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets )?received.*?([\d.]+)% packet loss",
    re.IGNORECASE,
)
_PING_RTT = re.compile(
    r"(?:round-trip|rtt)\s+min/avg/max(?:/stddev)?\s*=\s*([\d.]+)/([\d.]+)/([\d.]+)", re.IGNORECASE
)


def _ping_argv(
    target: str,
    count: int,
    timeout_ms: int,
    packet_size: int | None,
) -> list[str]:
    """构造 ping 命令。差异只在这里出现。"""
    if is_windows():
        argv = ["ping", "-n", str(count), "-w", str(timeout_ms)]
        if packet_size is not None:
            argv += ["-l", str(packet_size)]
        return [*argv, target]
    # macOS / Linux：-W 是毫秒级等待，-t 是总超时（秒）
    timeout_s = max(int(timeout_ms / 1000), 1)
    argv = [PING_BIN, "-c", str(count), "-W", str(timeout_ms), "-t", str(timeout_s)]
    if packet_size is not None:
        argv += ["-s", str(packet_size)]
    return [*argv, target]


def parse_ping_output(target: str, output: str) -> PingResult:
    """解析 ping 文本。暴露为公共函数便于用固定样例做单测。"""
    replies: list[PingReply] = []
    for match in _PING_REPLY.finditer(output):
        replies.append(
            PingReply(
                seq=int(match.group(3)),
                ttl=int(match.group(4)),
                rtt_ms=float(match.group(5)),
                from_addr=match.group(2),
            )
        )
    for match in _PING_REPLY_NO_TTL.finditer(output):
        seq = int(match.group(3))
        if any(reply.seq == seq for reply in replies):
            continue
        replies.append(
            PingReply(
                seq=seq,
                ttl=None,
                rtt_ms=float(match.group(4)),
                from_addr=match.group(2),
            )
        )
    for match in _PING_TIMEOUT.finditer(output):
        replies.append(
            PingReply(
                seq=int(match.group(1)),
                ttl=None,
                rtt_ms=None,
                from_addr="",
                timed_out=True,
            )
        )
    replies.sort(key=lambda r: r.seq)

    stats = _PING_STATS.search(output)
    if stats:
        transmitted = int(stats.group(1))
        received = int(stats.group(2))
        loss = float(stats.group(3))
    else:
        transmitted = len(replies)
        received = sum(1 for r in replies if not r.timed_out)
        loss = 0.0 if transmitted == 0 else (transmitted - received) / transmitted * 100

    rtt = _PING_RTT.search(output)
    min_ms = float(rtt.group(1)) if rtt else None
    avg_ms = float(rtt.group(2)) if rtt else None
    max_ms = float(rtt.group(3)) if rtt else None
    if avg_ms is None:
        values = [r.rtt_ms for r in replies if r.rtt_ms is not None]
        if values:
            min_ms, avg_ms, max_ms = min(values), sum(values) / len(values), max(values)

    return PingResult(
        target=target,
        replies=tuple(replies),
        transmitted=transmitted,
        received=received,
        packet_loss=loss,
        min_ms=min_ms,
        avg_ms=avg_ms,
        max_ms=max_ms,
        raw=output,
    )


def run_ping(
    target: str,
    *,
    count: int = 1,
    timeout_ms: int = DEFAULT_PING_TIMEOUT_MS,
    packet_size: int | None = DEFAULT_PING_PACKET_SIZE,
    process_timeout_s: float = 10.0,
) -> PingResult:
    """执行一次 ping。count > 1 时为连续探测，阻塞直到结束，调用方需放线程池。"""
    if not target.strip():
        raise CommandError("目标地址不能为空")
    if not 1 <= count <= MAX_PING_COUNT:
        raise CommandError(f"探测次数必须在 1-{MAX_PING_COUNT} 之间")
    if not 100 <= timeout_ms <= 10_000:
        raise CommandError("单次等待时间必须在 100-10000 ms 之间")
    argv = _ping_argv(target.strip(), count, timeout_ms, packet_size)
    _, output = _run(argv, process_timeout_s)
    return parse_ping_output(target.strip(), output)


_HOP_LINE = re.compile(r"^\s*(\d+)\s+(.*)$")
_STAR = "*"
#: 单行里的 IPv4 地址（处理「域名 (IP)」和「IP」两种形态）。
_HOP_ADDR = re.compile(r"([0-9]{1,3}(?:\.[0-9]{1,3}){3})")
#: 单行里的延迟数值（``12.345 ms`` / ``12 ms``）。
_HOP_RTT = re.compile(r"(\d+(?:\.\d+)?)\s*ms")


def parse_traceroute_hop_line(line: str) -> TracerouteHop | None:
    """解析 traceroute / tracert 的单行输出。

    兼容三种形态：`` 1  10.0.0.1  12.3 ms``、`` 2  *``、`` 3  * * *``，
    以及带主机名的 `` 1  gw (10.0.0.1)  12 ms``。不匹配返回 ``None``。
    """
    match = _HOP_LINE.match(line)
    if not match:
        return None
    hop_no = int(match.group(1))
    rest = match.group(2).strip()
    if rest.startswith(_STAR):
        return TracerouteHop(hop=hop_no, address="*", rtt_ms=None, timed_out=True)
    address = _HOP_ADDR.search(rest)
    if not address:
        return None
    rtts = [float(value) for value in _HOP_RTT.findall(rest)]
    return TracerouteHop(
        hop=hop_no,
        address=address.group(1),
        rtt_ms=rtts[0] if rtts else None,
    )


def parse_traceroute_output(target: str, output: str) -> TracerouteResult:
    hops: list[TracerouteHop] = []
    for line in output.splitlines():
        hop = parse_traceroute_hop_line(line)
        if hop is not None:
            hops.append(hop)
    reached = bool(hops) and not hops[-1].timed_out
    return TracerouteResult(target=target, hops=tuple(hops), reached=reached, raw=output)


def _traceroute_argv(
    target: str,
    max_hops: int,
    timeout_s: float,
    probe: str = DEFAULT_TRACEROUTE_PROBE,
    tcp_port: int = DEFAULT_TRACEROUTE_TCP_PORT,
) -> list[str]:
    """构造 traceroute 命令。探测协议差异只在这里出现。

    - macOS：``-I``=ICMP、默认=UDP 高端口、``-P tcp`` 走 pcap（本机实测
      ``pcap_activate() failed``，见 ``run_traceroute`` 的降级逻辑）；
    - Windows ``tracert`` 只有 ICMP，忽略 probe。
    """
    if probe not in TRACEROUTE_PROBES:
        raise CommandError("探测协议仅支持 ICMP、UDP 或 TCP")
    if not 1 <= tcp_port <= 65535:
        raise CommandError("TCP 端口必须在 1-65535 之间")
    if is_windows():
        return ["tracert", "-d", "-h", str(max_hops), "-w", str(int(timeout_s * 1000)), target]
    argv = [TRACEROUTE_BIN, "-n", "-m", str(max_hops), "-q", "1", "-w", f"{timeout_s:g}"]
    if probe == "icmp":
        argv.append("-I")
    elif probe == "tcp":
        # macOS 的 traceroute 需要 -P tcp + -p 端口；-T 是 Linux 变体写法。
        argv += ["-P", "tcp", "-p", str(tcp_port)]
    return [*argv, target]


def run_traceroute(
    target: str,
    *,
    max_hops: int = DEFAULT_TRACEROUTE_MAX_HOPS,
    timeout_s: float = 2.0,
    process_timeout_s: float = 60.0,
    probe: str = DEFAULT_TRACEROUTE_PROBE,
    tcp_port: int = DEFAULT_TRACEROUTE_TCP_PORT,
) -> TracerouteResult:
    if not target.strip():
        raise CommandError("目标地址不能为空")
    if not 1 <= max_hops <= MAX_TRACEROUTE_HOPS:
        raise CommandError(f"最大跳数必须在 1-{MAX_TRACEROUTE_HOPS} 之间")
    if not 0.1 <= timeout_s <= 5.0:
        raise CommandError("单跳等待时间必须在 0.1-5 秒之间")
    argv = _traceroute_argv(target.strip(), max_hops, timeout_s, probe, tcp_port)
    returncode, output = _run(argv, process_timeout_s)
    # macOS 的 TCP 探测依赖 pcap，部分机器上初始化失败（pcap_activate 报错、退出码 71）。
    # 这不是用户能解决的，自动降级成 ICMP 再跑一次，错误消息里带上原因。
    if returncode != 0 and probe == "tcp" and "pcap" in output.lower():
        fallback = _traceroute_argv(
            target.strip(), max_hops, timeout_s, "icmp", DEFAULT_TRACEROUTE_TCP_PORT
        )
        returncode, output = _run(fallback, process_timeout_s)
        probe = "icmp"
    if returncode != 0 and not output.strip():
        raise CommandError(f"traceroute 退出码为 {returncode}，没有返回诊断信息")
    result = parse_traceroute_output(target.strip(), output)
    if not result.hops:
        detail = output.strip()[-1000:] or "命令没有输出"
        raise CommandError(f"traceroute 未返回可识别的跳信息：{detail}", output=output)
    if returncode != 0:
        raise CommandError(
            f"traceroute 退出码为 {returncode}；请检查目标地址、权限或系统命令输出",
            output=output,
        )
    return result


def run_traceroute_stream(
    target: str,
    *,
    max_hops: int = DEFAULT_TRACEROUTE_MAX_HOPS,
    timeout_s: float = 2.0,
    process_timeout_s: float = 60.0,
    probe: str = DEFAULT_TRACEROUTE_PROBE,
    tcp_port: int = DEFAULT_TRACEROUTE_TCP_PORT,
    on_hop: Callable[[TracerouteHop], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> TracerouteResult:
    """流式 traceroute：每跳一到达就回调 ``on_hop``，不等进程结束。

    用 ``subprocess.Popen`` + 逐行 ``readline``（traceroute 的 stdout 是无缓冲的
    行式输出，每跳 2s 实时出一行），``on_hop`` 在工作线程被调用，UI 侧必须
    用信号桥接回主线程。``should_cancel()`` 生效时 SIGTERM 掉进程，
    已收到的跳保留在返回结果里。Windows 用 ``tracert``，同样是行式输出。
    """
    if not target.strip():
        raise CommandError("目标地址不能为空")
    if not 1 <= max_hops <= MAX_TRACEROUTE_HOPS:
        raise CommandError(f"最大跳数必须在 1-{MAX_TRACEROUTE_HOPS} 之间")
    if not 0.1 <= timeout_s <= 5.0:
        raise CommandError("单跳等待时间必须在 0.1-5 秒之间")
    check = should_cancel or (lambda: False)
    emit = on_hop or (lambda _hop: None)

    argv = _traceroute_argv(target.strip(), max_hops, timeout_s, probe, tcp_port)
    deadline = time.monotonic() + process_timeout_s
    hops: list[TracerouteHop] = []
    lines: list[str] = []
    try:
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as exc:
        raise CommandError(f"系统命令不存在：{argv[0]}") from exc

    cancelled = False
    try:
        for line in process.stdout:  # type: ignore[union-attr]
            lines.append(line)
            hop = parse_traceroute_hop_line(line)
            if hop is not None:
                hops.append(hop)
                emit(hop)
            if check():
                cancelled = True
                break
            if time.monotonic() > deadline:
                cancelled = True
                break
    finally:
        if cancelled and process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - 兜底
            process.kill()
            process.wait(timeout=2)
        if process.stdout is not None:  # type: ignore[union-attr]
            process.stdout.close()

    raw = "".join(lines)
    resolved = target.strip()
    header = re.match(r"traceroute to (\S+)", raw)
    if header:
        resolved = header.group(1)
    reached = bool(hops) and not hops[-1].timed_out
    return TracerouteResult(target=resolved, hops=tuple(hops), reached=reached, raw=raw)


_ARP_MACOS = re.compile(
    r"^(?P<host>\S+)\s+\((?P<ip>[\d.]+)\)\s+at\s+(?P<mac>[0-9a-f:]+)\s+on\s+(?P<if>\S+)(?:\s+(?P<flags>.*))?$",
    re.IGNORECASE,
)
_ARP_WINDOWS = re.compile(
    r"^\s*(?P<ip>[\d.]+)\s+(?P<mac>[0-9a-f-]{17})\s+(?P<type>\w+)$",
    re.IGNORECASE,
)


def parse_arp_output(output: str) -> tuple[ArpEntry, ...]:
    entries: list[ArpEntry] = []
    for line in output.splitlines():
        match = _ARP_MACOS.match(line.strip())
        if match:
            mac = match.group("mac")
            if mac in {"incomplete", "(incomplete)"}:
                continue
            entries.append(
                ArpEntry(
                    ip=match.group("ip"),
                    mac=_normalize_mac(mac),
                    interface=match.group("if"),
                    flags=(match.group("flags") or "").strip(),
                )
            )
            continue
        match = _ARP_WINDOWS.match(line.strip())
        if match:
            entries.append(
                ArpEntry(
                    ip=match.group("ip"),
                    mac=_normalize_mac(match.group("mac")),
                    flags=match.group("type"),
                )
            )
    return tuple(entries)


def _normalize_mac(mac: str) -> str:
    """统一成 ``aa:bb:cc:dd:ee:ff``；macOS 会省略前导零（``0:11:...``）。"""
    parts = re.split(r"[:.-]", mac.strip())
    if len(parts) == 6 and all(1 <= len(part) <= 2 for part in parts):
        try:
            return ":".join(f"{int(part, 16):02x}" for part in parts)
        except ValueError:
            return mac.lower()
    digits = re.sub(r"[^0-9a-fA-F]", "", mac).lower()
    if len(digits) != 12:
        return mac.lower()
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


def read_arp_table(*, process_timeout_s: float = 10.0) -> tuple[ArpEntry, ...]:
    """读取系统 ARP 表。macOS/Linux 用 ``arp -an``，Windows 用 ``arp -a``。"""
    argv = ["arp", "-a"] if is_windows() else [ARP_BIN, "-an"]
    _, output = _run(argv, process_timeout_s)
    return parse_arp_output(output)
