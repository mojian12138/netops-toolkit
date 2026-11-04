"""Ping 会话服务：把单次 ping 命令包装成可取消的连续 / 批量探测，零 Qt 依赖。

三层分工：

- ``platform_/os_layer.py``：构造并执行系统 ``ping`` 子进程，解析成 ``PingResult``；
- 本模块：目标校验、限流、并发调度、增量统计、导出格式；
- ``ui/pages/ping_page.py``：只负责录入、展示与信号转发。

安全边界（写死在常量里）：批量目标 ≤ 256、并发 ≤ 16、连续探测间隔 ≥ 200ms。
``on_sample`` 在工作者线程里被调用，回调必须线程安全——Qt 侧用信号转发到主线程。
"""

from __future__ import annotations

import ipaddress
import logging
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta

from netops.platform_.os_layer import (
    DEFAULT_PING_PACKET_SIZE,
    DEFAULT_PING_TIMEOUT_MS,
    MAX_PING_COUNT,
    CommandError,
    PingResult,
    run_ping,
)

__all__ = [
    "MAX_TARGETS",
    "DEFAULT_CONCURRENCY",
    "MAX_CONCURRENCY",
    "MIN_INTERVAL_MS",
    "DEFAULT_INTERVAL_MS",
    "MAX_INTERVAL_MS",
    "MAX_SAMPLES",
    "CSV_HEADER",
    "PingError",
    "PingSample",
    "TargetSummary",
    "PingReport",
    "PingSession",
    "parse_targets",
]

_log = logging.getLogger(__name__)

#: 单次批量探测的目标上限。
MAX_TARGETS = 256
#: 批量探测的默认 / 最大并发。
DEFAULT_CONCURRENCY = 16
MAX_CONCURRENCY = 16
#: 连续探测的最小间隔（毫秒）——低于此值会明显加重设备负担。
MIN_INTERVAL_MS = 200
DEFAULT_INTERVAL_MS = 1000
MAX_INTERVAL_MS = 10_000
#: 报告里最多保留多少条采样（连续探测长时间运行时的内存上限）。
MAX_SAMPLES = 20_000

CSV_HEADER = ["time", "target", "seq", "status", "rtt_ms", "ttl", "from"]

#: 主机名允许字母 / 数字 / 连字符 / 点，长度遵循 DNS 限制。
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)
_SPLIT = re.compile(r"[\s,;，；]+")
#: 纯数字和点的串只可能是想输 IPv4，校验失败就报错，不要再当域名放行。
_NUMERIC = re.compile(r"^[0-9.]+$")


class PingError(ValueError):
    """用户输入或参数非法。消息面向用户，可直接展示。"""


@dataclass(frozen=True, slots=True)
class PingSample:
    """一次探测结果。``elapsed_s`` 相对会话开始时刻（单调时钟）。"""

    target: str
    seq: int
    elapsed_s: float
    rtt_ms: float | None
    ttl: int | None
    from_addr: str
    timed_out: bool
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.timed_out and self.rtt_ms is not None

    @property
    def status_text(self) -> str:
        if self.error:
            return "错误"
        return "超时" if self.timed_out else "成功"


@dataclass(frozen=True, slots=True)
class TargetSummary:
    """单个目标的累计统计快照。"""

    target: str
    sent: int
    received: int
    errors: int
    packet_loss: float
    min_ms: float | None
    avg_ms: float | None
    max_ms: float | None
    last_rtt_ms: float | None
    consecutive_failures: int
    last_error: str | None = None


@dataclass(slots=True)
class _Accumulator:
    """可变累加器，只在会话内部使用。"""

    target: str
    sent: int = 0
    received: int = 0
    errors: int = 0
    total_ms: float = 0.0
    min_ms: float | None = None
    max_ms: float | None = None
    last_rtt_ms: float | None = None
    consecutive_failures: int = 0
    last_error: str | None = None

    def add(self, sample: PingSample) -> None:
        if sample.error is not None:
            self.errors += 1
            self.consecutive_failures += 1
            self.last_error = sample.error
            return
        self.sent += 1
        self.last_error = None
        rtt = sample.rtt_ms
        if sample.timed_out or rtt is None:
            self.consecutive_failures += 1
            self.last_rtt_ms = None
            return
        self.received += 1
        self.consecutive_failures = 0
        self.last_rtt_ms = rtt
        self.total_ms += rtt
        self.min_ms = rtt if self.min_ms is None else min(self.min_ms, rtt)
        self.max_ms = rtt if self.max_ms is None else max(self.max_ms, rtt)

    def summary(self) -> TargetSummary:
        loss = 0.0 if self.sent == 0 else (self.sent - self.received) / self.sent * 100
        avg = None if self.received == 0 else self.total_ms / self.received
        return TargetSummary(
            target=self.target,
            sent=self.sent,
            received=self.received,
            errors=self.errors,
            packet_loss=loss,
            min_ms=self.min_ms,
            avg_ms=avg,
            max_ms=self.max_ms,
            last_rtt_ms=self.last_rtt_ms,
            consecutive_failures=self.consecutive_failures,
            last_error=self.last_error,
        )


@dataclass(frozen=True, slots=True)
class PingReport:
    """一次会话的完整结果：统计 + 采样明细 + 导出。"""

    targets: tuple[str, ...]
    summaries: tuple[TargetSummary, ...]
    samples: tuple[PingSample, ...]
    started_at: datetime
    finished_at: datetime
    duration_s: float
    rounds_done: int
    cancelled: bool
    dropped_samples: int = 0

    @property
    def transmitted(self) -> int:
        return sum(item.sent for item in self.summaries)

    @property
    def received(self) -> int:
        return sum(item.received for item in self.summaries)

    @property
    def errors(self) -> int:
        return sum(item.errors for item in self.summaries)

    @property
    def packet_loss(self) -> float:
        if self.transmitted == 0:
            return 0.0
        return (self.transmitted - self.received) / self.transmitted * 100

    @property
    def ok(self) -> bool:
        return self.received > 0 and self.errors == 0

    def summary_for(self, target: str) -> TargetSummary | None:
        for item in self.summaries:
            if item.target == target:
                return item
        return None

    # -- 导出 ---------------------------------------------------------------
    def csv_rows(self) -> list[list[str]]:
        """表头 + 每条采样一行。时间取「会话开始 + 相对秒数」。"""
        if self.dropped_samples:
            first_seq = self.samples[0].seq if self.samples else 0
            _log.debug(
                "采样超出保留上限，已丢弃 %d 条（序号 %d 之前）", self.dropped_samples, first_seq
            )
        rows = [list(CSV_HEADER)]
        for sample in self.samples:
            rows.append(
                [
                    _sample_time(self.started_at, sample).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                    sample.target,
                    str(sample.seq),
                    sample.status_text,
                    "" if sample.rtt_ms is None else f"{sample.rtt_ms:.3f}",
                    "" if sample.ttl is None else str(sample.ttl),
                    sample.from_addr,
                ]
            )
        return rows

    def to_markdown(self, *, sample_limit: int = 200) -> str:
        """Markdown 报告：概况 + 逐目标统计 + 最近采样明细。"""
        lines = [
            "# Ping 探测报告",
            "",
            f"- 探测开始：{self.started_at:%Y-%m-%d %H:%M:%S}",
            f"- 探测结束：{self.finished_at:%Y-%m-%d %H:%M:%S}",
            f"- 探测时长：{self.duration_s:.1f} 秒",
            f"- 目标数量：{len(self.targets)}",
            f"- 完成轮次：{self.rounds_done}" + ("（已手动停止）" if self.cancelled else ""),
            f"- 合计：发送 {self.transmitted} / 接收 {self.received} / 丢包 {self.packet_loss:.1f}%",
            "",
            "## 目标统计",
            "",
            "| 目标 | 发送 | 接收 | 丢包率 | 最小 (ms) | 平均 (ms) | 最大 (ms) | 错误 |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
        for item in self.summaries:
            lines.append(
                f"| {item.target} | {item.sent} | {item.received} | {item.packet_loss:.1f}% "
                f"| {_fmt_ms(item.min_ms)} | {_fmt_ms(item.avg_ms)} | {_fmt_ms(item.max_ms)} "
                f"| {item.last_error or '—'} |"
            )

        samples = self.samples[-sample_limit:] if sample_limit > 0 else ()
        lines += [
            "",
            f"## 采样明细（最近 {len(samples)} 条）",
            "",
            "| 时间 | 目标 | 序号 | 状态 | RTT (ms) | TTL |",
            "|---|---|---:|---|---:|---:|",
        ]
        for sample in samples:
            lines.append(
                f"| {_sample_time(self.started_at, sample):%Y-%m-%d %H:%M:%S}"
                f" | {sample.target} | {sample.seq} | {sample.status_text}"
                f" | {_fmt_ms(sample.rtt_ms)} | {sample.ttl if sample.ttl is not None else '—'} |"
            )
        if self.dropped_samples:
            lines += ["", f"> 采样超出保留上限，另有 {self.dropped_samples} 条较早记录未保留。"]
        lines.append("")
        return "\n".join(lines)


def _fmt_ms(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def _sample_time(started_at: datetime, sample: PingSample) -> datetime:
    return started_at + timedelta(seconds=sample.elapsed_s)


def parse_targets(text: str, *, limit: int = MAX_TARGETS) -> tuple[str, ...]:
    """把用户输入解析成去重后的目标列表。

    支持换行 / 逗号 / 分号 / 空白分隔，``#`` 之后为注释；IPv4 与主机名可混用。
    IPv6 明确拒绝（系统 ping 需要走 ``ping6``，本版未封装）。
    """
    if limit < 1:
        raise PingError("目标上限必须为正整数")
    targets: list[str] = []
    seen: set[str] = set()
    invalid: list[str] = []
    ipv6: list[str] = []
    for line in text.splitlines():
        body = line.split("#", 1)[0]
        for token in _SPLIT.split(body):
            value = token.strip()
            if not value:
                continue
            key = value.casefold()
            if key in seen:
                continue
            kind = _classify_target(value)
            if kind == "ipv6":
                ipv6.append(value)
                continue
            if kind == "bad":
                invalid.append(value)
                continue
            seen.add(key)
            targets.append(value)

    problems: list[str] = []
    if invalid:
        joined = "、".join(invalid[:5]) + ("…" if len(invalid) > 5 else "")
        problems.append(f"不是合法 IPv4 / 域名：{joined}")
    if ipv6:
        joined = "、".join(ipv6[:5]) + ("…" if len(ipv6) > 5 else "")
        problems.append(f"暂不支持 IPv6 目标：{joined}")
    if problems:
        raise PingError("；".join(problems) + "。")
    if not targets:
        raise PingError("请至少输入一个目标地址")
    if len(targets) > limit:
        raise PingError(f"目标 {len(targets)} 个，超过单次上限 {limit} 个，请分批探测")
    return tuple(targets)


def _classify_target(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        if _NUMERIC.match(value):
            return "bad"
        return "ok" if _HOSTNAME.match(value) else "bad"
    return "ok" if isinstance(address, ipaddress.IPv4Address) else "ipv6"


Runner = Callable[..., PingResult]
SampleCallback = Callable[[PingSample], None]


class PingSession:
    """一次 Ping 会话：1 个或多个目标 × N 轮（``rounds=None`` 为连续直到取消）。

    ``run()`` 是阻塞调用，交给 ``TaskRunner`` 或线程执行；会话本身不创建线程池
    以外的线程，也不依赖 Qt。
    """

    def __init__(
        self,
        targets: Sequence[str],
        *,
        rounds: int | None = 1,
        interval_ms: int = DEFAULT_INTERVAL_MS,
        timeout_ms: int = DEFAULT_PING_TIMEOUT_MS,
        packet_size: int | None = DEFAULT_PING_PACKET_SIZE,
        concurrency: int = DEFAULT_CONCURRENCY,
        max_duration_s: float | None = None,
        runner: Runner = run_ping,
        on_sample: SampleCallback | None = None,
        should_cancel: Callable[[], bool] | None = None,
        should_pause: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        wall_clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        cleaned = tuple(dict.fromkeys(target.strip() for target in targets if target.strip()))
        if not cleaned:
            raise PingError("请至少输入一个目标地址")
        if len(cleaned) > MAX_TARGETS:
            raise PingError(f"目标 {len(cleaned)} 个，超过单次上限 {MAX_TARGETS} 个，请分批探测")
        if rounds is not None and not 1 <= rounds <= MAX_PING_COUNT:
            raise PingError(f"探测轮次必须在 1-{MAX_PING_COUNT} 之间")
        if not MIN_INTERVAL_MS <= interval_ms <= MAX_INTERVAL_MS:
            raise PingError(f"探测间隔必须在 {MIN_INTERVAL_MS}-{MAX_INTERVAL_MS} ms 之间")
        if not 100 <= timeout_ms <= 10_000:
            raise PingError("单次等待时间必须在 100-10000 ms 之间")
        if not 1 <= concurrency <= MAX_CONCURRENCY:
            raise PingError(f"并发数必须在 1-{MAX_CONCURRENCY} 之间")
        if max_duration_s is not None and max_duration_s <= 0:
            raise PingError("最长运行时长必须大于 0 秒")

        self.targets = cleaned
        self.rounds = rounds
        self.interval_ms = interval_ms
        self.timeout_ms = timeout_ms
        self.packet_size = packet_size
        self.concurrency = concurrency
        self.max_duration_s = max_duration_s
        self._runner = runner
        self._on_sample = on_sample
        self._should_cancel = should_cancel or (lambda: False)
        self._should_pause = should_pause or (lambda: False)
        self._clock = clock
        self._sleep = sleep
        self._wall_clock = wall_clock

        self._lock = threading.Lock()
        self._stats: dict[str, _Accumulator] = {target: _Accumulator(target) for target in cleaned}
        self._samples: deque[PingSample] = deque(maxlen=MAX_SAMPLES)
        self._dropped = 0
        self._t0 = 0.0
        self._started_at = wall_clock()

    # -- 对外 ---------------------------------------------------------------
    def summaries(self) -> tuple[TargetSummary, ...]:
        with self._lock:
            return tuple(self._stats[target].summary() for target in self.targets)

    def summary_for(self, target: str) -> TargetSummary | None:
        with self._lock:
            accumulator = self._stats.get(target)
            return None if accumulator is None else accumulator.summary()

    def run(self) -> PingReport:
        """阻塞执行整个会话，返回最终报告（取消也会返回已采集的部分）。"""
        self._t0 = self._clock()
        self._started_at = self._wall_clock()
        rounds_done = 0
        cancelled = False
        while True:
            if self._should_cancel():
                cancelled = True
                break
            if self.rounds is not None and rounds_done >= self.rounds:
                break
            if self.max_duration_s is not None and self._elapsed() >= self.max_duration_s:
                cancelled = True
                break
            if not self._wait_while_paused():
                cancelled = True
                break
            self._probe_round(rounds_done + 1)
            rounds_done += 1
            if self.rounds is not None and rounds_done >= self.rounds:
                break
            if not self._sleep_interruptible(self.interval_ms / 1000):
                cancelled = True
                break
        return self._build_report(rounds_done=rounds_done, cancelled=cancelled)

    # -- 内部 ---------------------------------------------------------------
    def _build_report(self, *, rounds_done: int, cancelled: bool) -> PingReport:
        with self._lock:
            samples = tuple(self._samples)
            dropped = self._dropped
            summaries = tuple(self._stats[target].summary() for target in self.targets)
        return PingReport(
            targets=self.targets,
            summaries=summaries,
            samples=samples,
            started_at=self._started_at,
            finished_at=self._wall_clock(),
            duration_s=self._elapsed(),
            rounds_done=rounds_done,
            cancelled=cancelled,
            dropped_samples=dropped,
        )

    def _elapsed(self) -> float:
        return max(self._clock() - self._t0, 0.0)

    def _probe_round(self, seq: int) -> None:
        workers = min(self.concurrency, len(self.targets))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="netops-ping") as pool:
            futures = [pool.submit(self._probe_one, target, seq) for target in self.targets]
            for future in futures:
                future.result()

    def _probe_one(self, target: str, seq: int) -> None:
        if self._should_cancel():
            return
        process_timeout = max(3.0, self.timeout_ms / 1000 * 3)
        try:
            result = self._runner(
                target,
                count=1,
                timeout_ms=self.timeout_ms,
                packet_size=self.packet_size,
                process_timeout_s=process_timeout,
            )
        except CommandError as exc:
            self._record(self._failed(target, seq, str(exc)))
            return
        except Exception as exc:  # noqa: BLE001 - 后台任务边界，单点失败不能中断整轮
            _log.debug("ping %s 异常", target, exc_info=True)
            self._record(self._failed(target, seq, f"{type(exc).__name__}: {exc}"))
            return
        self._record(self._sample_from(target, seq, result))

    def _sample_from(self, target: str, seq: int, result: PingResult) -> PingSample:
        elapsed = self._elapsed()
        reply = _pick_reply(result)
        if reply is None:
            return PingSample(
                target=target,
                seq=seq,
                elapsed_s=elapsed,
                rtt_ms=None,
                ttl=None,
                from_addr="",
                timed_out=True,
            )
        return PingSample(
            target=target,
            seq=seq,
            elapsed_s=elapsed,
            rtt_ms=reply.rtt_ms if reply.rtt_ms is not None else result.avg_ms,
            ttl=reply.ttl,
            from_addr=reply.from_addr or target,
            timed_out=reply.timed_out or reply.rtt_ms is None,
        )

    def _failed(self, target: str, seq: int, message: str) -> PingSample:
        return PingSample(
            target=target,
            seq=seq,
            elapsed_s=self._elapsed(),
            rtt_ms=None,
            ttl=None,
            from_addr="",
            timed_out=False,
            error=message,
        )

    def _record(self, sample: PingSample) -> None:
        with self._lock:
            if len(self._samples) == self._samples.maxlen:
                self._dropped += 1
            self._samples.append(sample)
            self._stats[sample.target].add(sample)
        if self._on_sample is not None:
            try:
                self._on_sample(sample)
            except Exception:  # noqa: BLE001 - 展示层回调异常不影响探测
                _log.debug("on_sample 回调异常", exc_info=True)

    def _wait_while_paused(self) -> bool:
        """暂停期间原地等待；返回 ``False`` 表示期间被取消。"""
        while self._should_pause():
            if self._should_cancel():
                return False
            self._sleep(0.05)
        return not self._should_cancel()

    def _sleep_interruptible(self, seconds: float) -> bool:
        """按 50ms 切片睡眠，期间响应取消与暂停；返回 ``False`` 表示被取消。"""
        remaining = seconds
        while remaining > 0:
            if self._should_cancel():
                return False
            if self._should_pause():
                if not self._wait_while_paused():
                    return False
                continue
            slice_s = min(remaining, 0.05)
            self._sleep(slice_s)
            remaining -= slice_s
        return not self._should_cancel()


def _pick_reply(result: PingResult):
    """优先取成功回复；全超时时取第一条超时记录；没有记录返回 ``None``。"""
    for reply in result.replies:
        if not reply.timed_out and reply.rtt_ms is not None:
            return reply
    return result.replies[0] if result.replies else None
