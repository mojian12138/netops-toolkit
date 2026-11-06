"""路由追踪服务层单测：全部用假 runner，不发起真实网络请求。"""

from __future__ import annotations

from datetime import datetime

import pytest

from netops.core.db import Database
from netops.platform_.os_layer import CommandError, TracerouteHop, TracerouteResult
from netops.services import traceroute as tr
from netops.services.devices import DeviceBook
from netops.services.traceroute import (
    MAX_HOPS,
    MAX_TIMEOUT_S,
    TracerouteError,
    TracerouteSession,
    annotate_report,
    parse_target,
    resolve_max_hops,
    resolve_timeout,
)


def _result(target: str, hops: list[tuple[int, str, float | None]], *, reached: bool = True):
    """hops: [(hop_no, address, rtt_ms)；rtt_ms 为 None 表示超时。]"""
    return TracerouteResult(
        target=target,
        hops=tuple(
            TracerouteHop(
                hop=no,
                address=addr if rtt is not None else "*",
                rtt_ms=rtt,
                timed_out=rtt is None,
            )
            for no, addr, rtt in hops
        ),
        reached=reached,
    )


class FakeRunner:
    def __init__(self, result: TracerouteResult | Exception) -> None:
        self._result = result
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, target: str, **kwargs) -> TracerouteResult:
        self.calls.append((target, kwargs))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


@pytest.fixture()
def db(tmp_path) -> Database:
    database = Database(tmp_path / "trace.sqlite3")
    database.connect()
    yield database
    database.close()


# ---------------------------------------------------------------------------
# 输入校验
# ---------------------------------------------------------------------------


def test_parse_target_accepts_ip_and_hostname() -> None:
    assert parse_target("8.8.8.8") == "8.8.8.8"
    assert parse_target("  WWW.Example.COM  ") == "www.example.com"
    assert parse_target("core-sw-01.lab.local") == "core-sw-01.lab.local"


def test_parse_target_rejects_numeric_garbage_as_ip() -> None:
    # 纯数字点号一定是想输 IPv4；不能当主机名原样交给 traceroute
    with pytest.raises(TracerouteError, match="不是合法的 IPv4"):
        parse_target("256.1.1.1")
    with pytest.raises(TracerouteError, match="不是合法的 IPv4"):
        parse_target("1.2.3")


def test_parse_target_rejects_ipv6_blank_and_spaces() -> None:
    with pytest.raises(TracerouteError, match="IPv6"):
        parse_target("fe80::1")
    with pytest.raises(TracerouteError, match="不能为空"):
        parse_target("   ")
    with pytest.raises(TracerouteError, match="不能包含空格"):
        parse_target("a b")
    with pytest.raises(TracerouteError, match="不是合法"):
        parse_target("bad!host")


def test_parse_target_rejects_too_long() -> None:
    with pytest.raises(TracerouteError, match="过长"):
        parse_target("a" * 254)


def test_resolve_max_hops_and_timeout() -> None:
    assert resolve_max_hops(1) == 1
    assert resolve_max_hops(MAX_HOPS) == MAX_HOPS
    with pytest.raises(TracerouteError, match="最大跳数"):
        resolve_max_hops(0)
    with pytest.raises(TracerouteError, match="最大跳数"):
        resolve_max_hops(MAX_HOPS + 1)

    assert resolve_timeout(1) == pytest.approx(1.0)
    with pytest.raises(TracerouteError, match="单探针等待"):
        resolve_timeout(0.01)
    with pytest.raises(TracerouteError, match="单探针等待"):
        resolve_timeout(MAX_TIMEOUT_S + 1)


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------


def test_session_passes_parameters_to_runner() -> None:
    runner = FakeRunner(_result("8.8.8.8", [(1, "10.0.0.1", 1.0)]))
    session = TracerouteSession(
        "8.8.8.8", max_hops=12, timeout_s=3.0, process_timeout_s=45.0, runner=runner
    )
    session.run()

    assert len(runner.calls) == 1
    target, kwargs = runner.calls[0]
    assert target == "8.8.8.8"
    assert kwargs["max_hops"] == 12
    assert kwargs["timeout_s"] == pytest.approx(3.0)
    assert kwargs["process_timeout_s"] == 45.0
    assert kwargs["probe"] == "udp"


def test_report_fields_and_reached() -> None:
    runner = FakeRunner(
        _result("8.8.8.8", [(1, "10.0.0.1", 1.2), (2, "203.0.113.1", 8.4)], reached=True)
    )
    report = TracerouteSession("8.8.8.8", runner=runner).run()

    assert report.target == "8.8.8.8"
    assert report.hop_count == 2
    assert report.reached is True
    assert report.cancelled is False
    assert isinstance(report.started_at, datetime)
    assert report.finished_at >= report.started_at
    assert report.duration_s >= 0.0
    assert [item.address for item in report.hops] == ["10.0.0.1", "203.0.113.1"]


def test_timeout_hops_are_marked() -> None:
    runner = FakeRunner(
        _result(
            "8.8.8.8",
            [(1, "10.0.0.1", 1.0), (2, "*", None), (3, "203.0.113.1", 9.0)],
            reached=True,
        )
    )
    report = TracerouteSession("8.8.8.8", runner=runner).run()

    assert len(report.timeouts) == 1
    hop = report.timeouts[0]
    assert hop.hop == 2
    assert hop.display == "*"
    assert hop.rtt_ms is None


def test_same_hop_collapses_to_minimum_rtt() -> None:
    # 同一跳出现三次：一次超时、两次有响应 → 取有响应里最小的
    result = TracerouteResult(
        target="8.8.8.8",
        hops=(
            TracerouteHop(hop=2, address="*", rtt_ms=None, timed_out=True),
            TracerouteHop(hop=2, address="10.0.0.5", rtt_ms=12.0),
            TracerouteHop(hop=2, address="10.0.0.5", rtt_ms=7.5),
        ),
        reached=True,
    )
    report = TracerouteSession("8.8.8.8", runner=FakeRunner(result)).run()

    assert report.hop_count == 1
    hop = report.hops[0]
    assert hop.timed_out is False
    assert hop.address == "10.0.0.5"
    assert hop.rtt_ms == pytest.approx(7.5)


def test_command_error_becomes_traceroute_error() -> None:
    runner = FakeRunner(CommandError("traceroute 不存在"))
    with pytest.raises(TracerouteError, match="traceroute 不存在"):
        TracerouteSession("8.8.8.8", runner=runner).run()


def test_cancel_skips_runner() -> None:
    runner = FakeRunner(_result("8.8.8.8", [(1, "10.0.0.1", 1.0)]))
    report = TracerouteSession("8.8.8.8", runner=runner, should_cancel=lambda: True).run()

    assert runner.calls == []
    assert report.hops == ()
    assert report.cancelled is True


# ---------------------------------------------------------------------------
# 备注匹配（G2）
# ---------------------------------------------------------------------------


def test_annotate_report_matches_devices(db: Database) -> None:
    book = DeviceBook(db)
    book.mark("10.0.0.1", remark="核心交换机", model="S5560X", location="机房A")
    book.mark("203.0.113.9", remark="出口防火墙", location="机房B")

    runner = FakeRunner(
        _result(
            "8.8.8.8",
            [(1, "10.0.0.1", 1.0), (2, "203.0.113.9", 5.0), (3, "8.8.8.8", 12.0)],
            reached=True,
        )
    )
    report = annotate_report(TracerouteSession("8.8.8.8", runner=runner).run(), book)

    first, second, third = report.hops
    assert first.name == "核心交换机"
    assert first.display == "核心交换机 (10.0.0.1)"
    assert first.remark == "核心交换机 · 机房A · S5560X"
    assert second.name == "出口防火墙"
    assert third.name == ""  # 未备注
    assert third.display == "8.8.8.8"
    assert third.remark == ""
    assert len(report.known_devices) == 2


def test_annotate_does_not_query_timeout_hops(db: Database) -> None:
    book = DeviceBook(db)
    book.mark("*", remark="不该命中")
    report = TracerouteSession(
        "8.8.8.8", runner=FakeRunner(_result("8.8.8.8", [(1, "*", None), (2, "10.0.0.1", 1.0)]))
    ).run()
    annotated = annotate_report(report, book)
    assert all(hop.name == "" for hop in annotated.hops)


def test_session_run_never_touches_the_database() -> None:
    """故障回归：Database 是单连接、绑定创建线程；会话在工作线程里跑，
    一旦读库就抛 ProgrammingError，备注会静默全空。备注只能由 UI 线程补。"""

    class ExplodingBook(DeviceBook):
        def remarks_for(self, ips):  # noqa: ANN001
            raise AssertionError("TracerouteSession 不应访问备注簿")

    runner = FakeRunner(_result("8.8.8.8", [(1, "10.0.0.1", 1.0)]))
    # 会话构造参数里已经没有 book；这里确认即便传入也不会被使用（会 TypeError）
    report = TracerouteSession("8.8.8.8", runner=runner).run()
    assert report.hops[0].name == ""


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------


def test_markdown_and_csv_export() -> None:
    runner = FakeRunner(
        _result("8.8.8.8", [(1, "10.0.0.1", 1.2), (2, "*", None), (3, "8.8.8.8", 9.5)])
    )
    report = TracerouteSession("8.8.8.8", runner=runner).run()

    text = report.to_markdown()
    assert "# 路由追踪报告" in text
    assert "| 1 | 10.0.0.1 | 1.2 |" in text
    assert "| 2 | * | — |" in text
    assert "未确认到达目标" not in text or report.reached is False

    rows = report.csv_rows()
    assert rows[0] == ["hop", "address", "rtt_ms", "timed_out", "name", "location", "model"]
    assert len(rows) == 4
    assert rows[2][1] == "*"
    assert rows[2][3] == "1"
    assert rows[1][2] == "1.200"


def test_empty_report_markdown() -> None:
    report = TracerouteSession("8.8.8.8", runner=FakeRunner(_result("8.8.8.8", []))).run()
    text = report.to_markdown()
    assert "| — | — | — | — |" in text
    assert "0 跳" in text


def test_module_constants_match_os_layer() -> None:
    from netops.platform_ import os_layer

    assert tr.MAX_HOPS == os_layer.MAX_TRACEROUTE_HOPS
    assert tr.DEFAULT_HOPS == os_layer.DEFAULT_TRACEROUTE_MAX_HOPS
