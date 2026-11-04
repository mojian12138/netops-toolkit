"""Ping 服务层单测：全部用假 runner，不发起真实网络请求。"""

from __future__ import annotations

from datetime import datetime

import pytest

from netops.platform_.os_layer import CommandError, PingReply, PingResult
from netops.services import ping as ping_service
from netops.services.ping import (
    MAX_TARGETS,
    PingError,
    PingSession,
    parse_targets,
)


def _ok(target: str, rtt: float = 1.0, ttl: int = 64, seq: int = 0) -> PingResult:
    reply = PingReply(seq=seq, ttl=ttl, rtt_ms=rtt, from_addr=target)
    return PingResult(
        target=target,
        replies=(reply,),
        transmitted=1,
        received=1,
        packet_loss=0.0,
        min_ms=rtt,
        avg_ms=rtt,
        max_ms=rtt,
    )


def _timeout(target: str) -> PingResult:
    reply = PingReply(seq=0, ttl=None, rtt_ms=None, from_addr="", timed_out=True)
    return PingResult(
        target=target,
        replies=(reply,),
        transmitted=1,
        received=0,
        packet_loss=100.0,
        min_ms=None,
        avg_ms=None,
        max_ms=None,
    )


class FakeRunner:
    """按目标返回预设结果，并记录调用顺序。"""

    def __init__(self, mapping: dict[str, PingResult | Exception], *, default_rtt: float = 2.0):
        self.mapping = mapping
        self.default_rtt = default_rtt
        self.calls: list[str] = []

    def __call__(self, target: str, **kwargs) -> PingResult:
        assert kwargs["count"] == 1
        self.calls.append(target)
        value = self.mapping.get(target)
        if isinstance(value, Exception):
            raise value
        if value is not None:
            return value
        return _ok(target, self.default_rtt)


def _no_sleep(_seconds: float) -> None:
    """多轮测试不需要真的等间隔，避免单测跑几十秒。"""


class StepClock:
    """每次读取前进固定步长，模拟时间流逝；sleep 也推进时钟。"""

    def __init__(self, step: float = 0.05) -> None:
        self.now = 0.0
        self.step = step
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        self.now += self.step
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


# -- parse_targets ------------------------------------------------------------
def test_parse_targets_splits_and_dedupes() -> None:
    text = """
    192.168.1.1, 192.168.1.2
    192.168.1.1   # 重复
    gw.example.com；10.0.0.1
    """
    assert parse_targets(text) == (
        "192.168.1.1",
        "192.168.1.2",
        "gw.example.com",
        "10.0.0.1",
    )


def test_parse_targets_is_case_insensitive_for_hostnames() -> None:
    assert parse_targets("GW.Example.com\ngw.example.com") == ("GW.Example.com",)


def test_parse_targets_rejects_empty() -> None:
    with pytest.raises(PingError, match="至少输入一个"):
        parse_targets("  \n# 只有注释\n")


def test_parse_targets_rejects_invalid_ipv4() -> None:
    with pytest.raises(PingError, match="不是合法 IPv4"):
        parse_targets("192.168.1.999")
    with pytest.raises(PingError, match="不是合法 IPv4"):
        parse_targets("192.168.1")


def test_parse_targets_rejects_ipv6_with_explicit_message() -> None:
    with pytest.raises(PingError, match="暂不支持 IPv6"):
        parse_targets("2001:db8::1")


def test_parse_targets_rejects_over_limit() -> None:
    text = "\n".join(f"10.0.0.{index}" for index in range(1, 5))
    with pytest.raises(PingError, match="超过单次上限 3"):
        parse_targets(text, limit=3)


def test_parse_targets_accepts_limit_boundary() -> None:
    text = "\n".join(f"10.0.0.{index}" for index in range(1, 4))
    assert len(parse_targets(text, limit=3)) == 3


# -- 参数校验 ------------------------------------------------------------------
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"rounds": 0}, "探测轮次"),
        ({"interval_ms": 100}, "探测间隔"),
        ({"interval_ms": 20_000}, "探测间隔"),
        ({"timeout_ms": 50}, "单次等待时间"),
        ({"concurrency": 0}, "并发数"),
        ({"concurrency": 99}, "并发数"),
    ],
)
def test_session_rejects_bad_parameters(kwargs: dict, message: str) -> None:
    with pytest.raises(PingError, match=message):
        PingSession(["1.1.1.1"], **kwargs)


def test_session_rejects_empty_and_too_many_targets() -> None:
    with pytest.raises(PingError, match="至少输入一个"):
        PingSession(["  "])
    with pytest.raises(PingError, match="超过单次上限"):
        PingSession([f"10.0.0.{i}" for i in range(MAX_TARGETS + 1)])


def test_session_dedupes_targets_preserving_order() -> None:
    session = PingSession(["1.1.1.1", " 1.1.1.1 ", "8.8.8.8"])
    assert session.targets == ("1.1.1.1", "8.8.8.8")


# -- 单目标 --------------------------------------------------------------------
def test_single_target_collects_statistics() -> None:
    runner = FakeRunner({"127.0.0.1": _ok("127.0.0.1", rtt=0.5)})
    session = PingSession(["127.0.0.1"], rounds=3, runner=runner, sleep=_no_sleep)
    report = session.run()

    assert report.rounds_done == 3
    assert not report.cancelled
    assert report.transmitted == 3
    assert report.received == 3
    assert report.packet_loss == 0.0
    assert report.ok
    summary = report.summary_for("127.0.0.1")
    assert summary is not None
    assert (summary.sent, summary.received, summary.errors) == (3, 3, 0)
    assert summary.min_ms == pytest.approx(0.5)
    assert summary.avg_ms == pytest.approx(0.5)


def test_statistics_mix_success_and_timeout() -> None:
    results = iter([_ok("10.0.0.1", rtt=10.0), _timeout("10.0.0.1"), _ok("10.0.0.1", rtt=30.0)])

    def runner(target: str, **kwargs) -> PingResult:
        return next(results)

    session = PingSession(["10.0.0.1"], rounds=3, runner=runner, sleep=_no_sleep)
    report = session.run()

    summary = report.summary_for("10.0.0.1")
    assert summary is not None
    assert summary.sent == 3
    assert summary.received == 2
    assert summary.packet_loss == pytest.approx(100 / 3)
    assert (summary.min_ms, summary.max_ms) == (10.0, 30.0)
    assert summary.avg_ms == pytest.approx(20.0)
    assert [sample.status_text for sample in report.samples] == ["成功", "超时", "成功"]


def test_consecutive_failures_reset_on_success() -> None:
    results = iter([_timeout("10.0.0.1"), _timeout("10.0.0.1"), _ok("10.0.0.1")])

    def runner(target: str, **kwargs) -> PingResult:
        return next(results)

    session = PingSession(["10.0.0.1"], rounds=3, runner=runner, sleep=_no_sleep)
    report = session.run()
    summary = report.summary_for("10.0.0.1")
    assert summary is not None
    assert summary.consecutive_failures == 0
    assert summary.last_rtt_ms == 1.0


def test_runner_exception_becomes_error_sample() -> None:
    runner = FakeRunner({"10.0.0.9": CommandError("命令执行超时")})
    session = PingSession(["10.0.0.9"], rounds=1, runner=runner)
    report = session.run()

    assert not report.ok
    assert report.errors == 1
    assert report.received == 0
    sample = report.samples[0]
    assert sample.status_text == "错误"
    assert "命令执行超时" in (sample.error or "")
    summary = report.summary_for("10.0.0.9")
    assert summary is not None
    assert summary.sent == 0
    assert summary.last_error == "命令执行超时"


def test_unexpected_exception_is_captured_not_raised() -> None:
    runner = FakeRunner({"10.0.0.9": RuntimeError("boom")})
    report = PingSession(["10.0.0.9"], rounds=1, runner=runner).run()
    assert report.errors == 1
    assert "RuntimeError: boom" in (report.samples[0].error or "")


# -- 多目标 --------------------------------------------------------------------
def test_batch_probes_every_target_each_round() -> None:
    runner = FakeRunner({})
    session = PingSession(
        ["192.168.1.1", "192.168.1.2", "10.0.0.1"],
        rounds=2,
        concurrency=2,
        runner=runner,
    )
    report = session.run()

    assert report.rounds_done == 2
    assert len(runner.calls) == 6
    assert set(report.targets) == {"192.168.1.1", "192.168.1.2", "10.0.0.1"}
    assert all(item.received == 2 for item in report.summaries)


def test_partial_batch_failure_does_not_break_round() -> None:
    runner = FakeRunner({"10.0.0.2": CommandError("目标不可达")})
    session = PingSession(["10.0.0.1", "10.0.0.2"], rounds=2, runner=runner, sleep=_no_sleep)
    report = session.run()

    assert report.rounds_done == 2
    assert report.received == 2
    assert report.errors == 2
    assert report.summary_for("10.0.0.1").received == 2


def test_on_sample_callback_receives_every_sample() -> None:
    seen: list[str] = []
    runner = FakeRunner({})
    session = PingSession(
        ["1.1.1.1", "2.2.2.2"],
        rounds=2,
        runner=runner,
        on_sample=lambda sample: seen.append(sample.target),
    )
    session.run()
    assert len(seen) == 4


def test_on_sample_callback_exception_is_swallowed() -> None:
    def boom(_sample) -> None:
        raise RuntimeError("UI 已经关闭")

    runner = FakeRunner({})
    report = PingSession(
        ["1.1.1.1"], rounds=2, runner=runner, on_sample=boom, sleep=_no_sleep
    ).run()
    assert report.rounds_done == 2
    assert report.received == 2


def test_progress_callback_sees_growing_totals() -> None:
    snapshots: list[int] = []
    session: PingSession

    def watch(_sample) -> None:
        summary = session.summary_for("1.1.1.1")
        snapshots.append(summary.received if summary else -1)

    session = PingSession(
        ["1.1.1.1"], rounds=3, runner=FakeRunner({}), on_sample=watch, sleep=_no_sleep
    )
    session.run()
    assert snapshots == [1, 2, 3]


# -- 取消 / 暂停 ---------------------------------------------------------------
def test_cancel_before_first_round_returns_empty_report() -> None:
    runner = FakeRunner({})
    report = PingSession(
        ["1.1.1.1", "2.2.2.2"], rounds=5, runner=runner, should_cancel=lambda: True
    ).run()
    assert report.cancelled
    assert report.rounds_done == 0
    assert runner.calls == []
    assert report.transmitted == 0


def test_cancel_mid_session_keeps_partial_results() -> None:
    runner = FakeRunner({})
    rounds = {"done": 0}

    def cancel_after_two() -> bool:
        return rounds["done"] >= 2

    session = PingSession(
        ["1.1.1.1"],
        rounds=None,
        interval_ms=200,
        runner=runner,
        should_cancel=cancel_after_two,
    )
    original = session._probe_round

    def counting_round(seq: int) -> None:
        original(seq)
        rounds["done"] += 1

    session._probe_round = counting_round  # type: ignore[method-assign]
    report = session.run()

    assert report.cancelled
    assert report.rounds_done == 2
    assert report.received == 2


def test_infinite_session_runs_until_cancelled() -> None:
    runner = FakeRunner({})
    counter = {"n": 0}

    def cancel_after_three() -> bool:
        return counter["n"] >= 3

    session = PingSession(
        ["1.1.1.1"],
        rounds=None,
        interval_ms=200,
        runner=runner,
        should_cancel=cancel_after_three,
        sleep=_no_sleep,
    )
    original = session._probe_round

    def counting_round(seq: int) -> None:
        original(seq)
        counter["n"] += 1

    session._probe_round = counting_round  # type: ignore[method-assign]
    report = session.run()
    assert report.cancelled
    assert report.rounds_done == 3


def test_pause_holds_until_resumed() -> None:
    flags = {"paused": True, "rounds": 0}
    runner = FakeRunner({})
    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        # 第二次 50ms 睡眠时恢复，验证暂停等待用了切片睡眠
        if len(sleep_calls) >= 2:
            flags["paused"] = False

    session = PingSession(
        ["1.1.1.1"],
        rounds=1,
        runner=runner,
        should_pause=lambda: flags["paused"],
        sleep=fake_sleep,
        clock=StepClock(),
    )
    report = session.run()

    assert report.rounds_done == 1
    assert report.received == 1
    assert sleep_calls[:2] == [0.05, 0.05]


def test_interval_is_respected_between_rounds() -> None:
    clock = StepClock()
    session = PingSession(
        ["1.1.1.1"],
        rounds=2,
        interval_ms=400,
        runner=FakeRunner({}),
        clock=clock,
        sleep=clock.sleep,
    )
    session.run()
    assert sum(clock.sleeps) == pytest.approx(0.4)


def test_interval_never_below_safety_minimum() -> None:
    assert ping_service.MIN_INTERVAL_MS == 200
    with pytest.raises(PingError, match="探测间隔"):
        PingSession(["1.1.1.1"], interval_ms=ping_service.MIN_INTERVAL_MS - 1)


def test_concurrency_never_exceeds_safety_cap() -> None:
    assert ping_service.MAX_CONCURRENCY <= 16
    assert ping_service.MAX_TARGETS <= 256


# -- 报告与导出 ----------------------------------------------------------------
def _sample_report() -> ping_service.PingReport:
    results = iter([_ok("10.0.0.1", rtt=12.5, ttl=61), _timeout("10.0.0.1")])

    def runner(target: str, **kwargs) -> PingResult:
        return next(results)

    started = datetime(2026, 11, 4, 9, 30, 0)
    return PingSession(
        ["10.0.0.1"], rounds=2, runner=runner, wall_clock=lambda: started, sleep=_no_sleep
    ).run()


def test_report_csv_rows_contain_timestamps_rtt_and_ttl() -> None:
    report = _sample_report()
    rows = report.csv_rows()
    assert rows[0] == list(ping_service.CSV_HEADER)
    assert len(rows) == 3
    assert rows[1][0].startswith("2026-11-04 09:30:00")
    assert rows[1][1:] == ["10.0.0.1", "1", "成功", "12.500", "61", "10.0.0.1"]
    assert rows[2][3] == "超时"
    assert rows[2][4] == "" and rows[2][5] == ""


def test_report_markdown_contains_statistics() -> None:
    report = _sample_report()
    text = report.to_markdown()
    assert "# Ping 探测报告" in text
    assert "探测开始：2026-11-04 09:30:00" in text
    assert "合计：发送 2 / 接收 1 / 丢包 50.0%" in text
    assert "| 10.0.0.1 | 2 | 1 | 50.0% | 12.500 | 12.500 | 12.500 | — |" in text
    assert "| 10.0.0.1 | 2 | 超时 | — | — |" in text


def test_report_markdown_marks_cancelled() -> None:
    report = PingSession(
        ["1.1.1.1"], rounds=None, runner=FakeRunner({}), should_cancel=lambda: True
    ).run()
    assert "（已手动停止）" in report.to_markdown()


def test_report_markdown_limits_samples() -> None:
    runner = FakeRunner({})
    report = PingSession(["1.1.1.1"], rounds=5, runner=runner, sleep=_no_sleep).run()
    text = report.to_markdown(sample_limit=2)
    assert "| 1.1.1.1 | 4 | 成功" in text
    assert "| 1.1.1.1 | 5 | 成功" in text
    assert "| 1.1.1.1 | 3 | 成功" not in text


def test_sample_buffer_drops_oldest_beyond_capacity(monkeypatch) -> None:
    monkeypatch.setattr(ping_service, "MAX_SAMPLES", 2)
    # deque(maxlen=...) 在构造时读取模块常量，必须新建会话才生效
    report = PingSession(["1.1.1.1"], rounds=4, runner=FakeRunner({}), sleep=_no_sleep).run()
    assert len(report.samples) == 2
    assert report.dropped_samples == 2
    assert report.transmitted == 4


def test_report_summary_for_unknown_target_is_none() -> None:
    report = _sample_report()
    assert report.summary_for("192.0.2.1") is None
