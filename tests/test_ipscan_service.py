"""局域网扫描服务层单测：假 ping + 假 ARP 表，不发起真实网络请求。"""

from __future__ import annotations

from datetime import datetime

import pytest

from netops.platform_.os_layer import ArpEntry, CommandError, PingReply, PingResult
from netops.services import ipscan
from netops.services.ipscan import (
    MAX_CONCURRENCY,
    MAX_HOSTS,
    MODE_ARP,
    MODE_BOTH,
    MODE_ICMP,
    IpScanError,
    IpScanSession,
    estimate,
    parse_networks,
    vendor_for_mac,
)


def _reply(target: str, rtt: float = 1.2) -> PingResult:
    reply = PingReply(seq=0, ttl=64, rtt_ms=rtt, from_addr=target)
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


class FakePing:
    """按 IP 返回预设结果，并记录调用次数。"""

    def __init__(self, mapping: dict[str, PingResult | Exception] | None = None) -> None:
        self.mapping = mapping or {}
        self.calls: list[str] = []

    def __call__(self, target: str, **kwargs) -> PingResult:
        assert kwargs["count"] == 1
        self.calls.append(target)
        value = self.mapping.get(target)
        if isinstance(value, Exception):
            raise value
        return value if value is not None else _timeout(target)


# ---------------------------------------------------------------------------
# 网段解析
# ---------------------------------------------------------------------------


def test_parse_cidr_drops_network_and_broadcast() -> None:
    specs = parse_networks("192.168.1.0/30")
    assert len(specs) == 1
    assert specs[0].label == "192.168.1.0/30"
    assert specs[0].hosts == ("192.168.1.1", "192.168.1.2")


def test_parse_span_and_tail_range() -> None:
    assert parse_networks("10.0.0.5-10.0.0.7")[0].hosts == (
        "10.0.0.5",
        "10.0.0.6",
        "10.0.0.7",
    )
    assert parse_networks("10.0.0.250-252")[0].hosts == ("10.0.0.250", "10.0.0.251", "10.0.0.252")
    # 反向区间自动纠正
    assert parse_networks("10.0.0.7-10.0.0.5")[0].hosts[0] == "10.0.0.5"


def test_parse_dedups_across_writings() -> None:
    specs = parse_networks("192.168.1.0/30\n192.168.1.1\n192.168.1.2-2")
    assert len(specs) == 1
    assert specs[0].hosts == ("192.168.1.1", "192.168.1.2")


def test_parse_single_ip_and_comments() -> None:
    specs = parse_networks("# 核心交换机\n10.0.0.1, 10.0.0.2\n")
    assert [spec.hosts for spec in specs] == [("10.0.0.1",), ("10.0.0.2",)]


def test_parse_rejects_garbage_ipv6_and_oversize() -> None:
    with pytest.raises(IpScanError, match="不是合法"):
        parse_networks("10.0.0.999")
    with pytest.raises(IpScanError, match="IPv6"):
        parse_networks("fe80::1")
    with pytest.raises(IpScanError, match="超过单次上限"):
        parse_networks("10.0.0.0/16")
    with pytest.raises(IpScanError, match="超过单次上限"):
        parse_networks("10.0.0.1-10.0.0.2", limit=1)
    with pytest.raises(IpScanError, match="至少输入一个"):
        parse_networks("  # 只有注释\n")


def test_parse_allows_max_size_slash20() -> None:
    specs = parse_networks("10.0.0.0/20")
    assert len(specs[0].hosts) == MAX_HOSTS - 2


def test_estimate_counts_without_expanding() -> None:
    assert estimate("192.168.1.0/24") == 254
    assert estimate("10.0.0.0/30\n10.0.0.9") == 3
    assert estimate("10.0.0.10-13") == 4
    with pytest.raises(IpScanError, match="超过单次上限"):
        estimate("10.0.0.0/16")


# ---------------------------------------------------------------------------
# 厂商识别
# ---------------------------------------------------------------------------


def test_vendor_for_mac_handles_bad_input() -> None:
    assert vendor_for_mac("00:1A:2B:3C:4D:5E") == ""  # 未收录但合法
    assert vendor_for_mac("not-a-mac") == ""
    assert vendor_for_mac("") == ""


# ---------------------------------------------------------------------------
# 扫描会话
# ---------------------------------------------------------------------------


def test_icmp_only_mode_marks_alive_and_dead() -> None:
    ping = FakePing({"10.0.0.1": _reply("10.0.0.1")})
    session = IpScanSession(
        ["10.0.0.1", "10.0.0.2"],
        mode=MODE_ICMP,
        ping_runner=ping,
        arp_reader=lambda: pytest.fail("仅 ICMP 模式不应读 ARP 表"),
    )
    report = session.run()

    assert report.alive_count == 1
    assert report.results[0].ip == "10.0.0.1"
    assert report.results[0].alive is True
    assert report.results[0].method == MODE_ICMP
    assert report.results[0].rtt_ms == pytest.approx(1.2)
    assert report.results[1].alive is False
    assert sorted(ping.calls) == ["10.0.0.1", "10.0.0.2"]

    text = report.to_markdown()
    assert "# 局域网扫描报告" in text
    assert "| 10.0.0.1 |" in text


def test_arp_only_mode_never_pings() -> None:
    arp = (ArpEntry(ip="10.0.0.5", mac="aa:bb:cc:dd:ee:01", interface="en0"),)
    session = IpScanSession(
        ["10.0.0.5", "10.0.0.6"],
        mode=MODE_ARP,
        ping_runner=lambda *a, **k: pytest.fail("仅 ARP 模式不应执行 ping"),
        arp_reader=lambda: arp,
    )
    report = session.run()

    assert report.alive_count == 1
    assert report.results[0].method == MODE_ARP
    assert report.results[0].mac == "aa:bb:cc:dd:ee:01"


def test_both_mode_merges_arp_into_icmp_results() -> None:
    ping = FakePing({"10.0.0.1": _reply("10.0.0.1")})
    arp = (
        ArpEntry(ip="10.0.0.1", mac="aa:bb:cc:dd:ee:01"),
        ArpEntry(ip="10.0.0.2", mac="aa:bb:cc:dd:ee:02"),  # 防火墙拦了 ICMP，但 ARP 有记录
        ArpEntry(ip="10.0.0.99", mac="aa:bb:cc:dd:ee:03"),  # 不在扫描范围内
    )
    report = IpScanSession(
        ["10.0.0.1", "10.0.0.2"], mode=MODE_BOTH, ping_runner=ping, arp_reader=lambda: arp
    ).run()

    by_ip = {item.ip: item for item in report.results}
    assert by_ip["10.0.0.1"].method == "icmp+arp"
    assert by_ip["10.0.0.1"].mac == "aa:bb:cc:dd:ee:01"
    assert by_ip["10.0.0.1"].rtt_ms == pytest.approx(1.2)
    assert by_ip["10.0.0.2"].method == MODE_ARP
    assert by_ip["10.0.0.2"].alive is True
    assert "10.0.0.99" not in by_ip  # 范围外的 ARP 记录必须被丢弃
    assert report.alive_count == 2


def test_arp_failure_does_not_break_icmp_results() -> None:
    ping = FakePing({"10.0.0.1": _reply("10.0.0.1")})

    def broken_arp() -> tuple[ArpEntry, ...]:
        raise CommandError("arp 命令不存在")

    report = IpScanSession(
        ["10.0.0.1", "10.0.0.2"], mode=MODE_BOTH, ping_runner=ping, arp_reader=broken_arp
    ).run()
    assert report.alive_count == 1


def test_ping_error_is_recorded_not_raised() -> None:
    ping = FakePing({"10.0.0.1": CommandError("ping 失败"), "10.0.0.2": RuntimeError("炸了")})
    report = IpScanSession(["10.0.0.1", "10.0.0.2"], mode=MODE_ICMP, ping_runner=ping).run()
    assert report.alive_count == 0
    errors = [item.error for item in report.results]
    assert any("ping 失败" in (err or "") for err in errors)
    assert any("RuntimeError" in (err or "") for err in errors)


def test_cancel_stops_issuing_new_probes() -> None:
    ping = FakePing()
    session = IpScanSession(
        ["10.0.0.1", "10.0.0.2", "10.0.0.3"],
        mode=MODE_ICMP,
        ping_runner=ping,
        should_cancel=lambda: True,
        arp_reader=lambda: (),
    )
    report = session.run()
    assert report.cancelled is True
    assert ping.calls == []


def test_batching_splits_probes() -> None:
    ping = FakePing()
    session = IpScanSession(
        [f"10.0.0.{index}" for index in range(1, 11)],
        mode=MODE_ICMP,
        concurrency=4,
        batch_size=3,
        ping_runner=ping,
    )
    report = session.run()
    assert len(ping.calls) == 10
    assert report.total == 10


def test_progress_callback_receives_every_result() -> None:
    seen = []
    ping = FakePing({"10.0.0.1": _reply("10.0.0.1")})
    IpScanSession(
        ["10.0.0.1", "10.0.0.2"], mode=MODE_ICMP, ping_runner=ping, on_host=seen.append
    ).run()
    assert {item.ip for item in seen} == {"10.0.0.1", "10.0.0.2"}


def test_callback_exception_does_not_abort() -> None:
    ping = FakePing({"10.0.0.1": _reply("10.0.0.1")})

    def boom(_result) -> None:
        raise RuntimeError("展示层炸了")

    report = IpScanSession(["10.0.0.1"], mode=MODE_ICMP, ping_runner=ping, on_host=boom).run()
    assert report.alive_count == 1


# ---------------------------------------------------------------------------
# 参数校验与导出
# ---------------------------------------------------------------------------


def test_session_rejects_bad_parameters() -> None:
    with pytest.raises(IpScanError, match="至少输入一个"):
        IpScanSession([], mode=MODE_ICMP)
    with pytest.raises(IpScanError, match="未知的扫描策略"):
        IpScanSession(["10.0.0.1"], mode="magic")
    with pytest.raises(IpScanError, match="并发数"):
        IpScanSession(["10.0.0.1"], concurrency=MAX_CONCURRENCY + 1)
    with pytest.raises(IpScanError, match="单次等待时间"):
        IpScanSession(["10.0.0.1"], timeout_ms=50)
    with pytest.raises(IpScanError, match="超过单次上限"):
        IpScanSession([f"10.0.{i // 250}.{i % 250 + 1}" for i in range(MAX_HOSTS + 1)])


def test_export_rows_skip_dead_hosts() -> None:
    ping = FakePing({"10.0.0.1": _reply("10.0.0.1")})
    arp = (ArpEntry(ip="10.0.0.2", mac="aa:bb:cc:dd:ee:02"),)
    report = IpScanSession(
        ["10.0.0.1", "10.0.0.2", "10.0.0.3"],
        mode=MODE_BOTH,
        ping_runner=ping,
        arp_reader=lambda: arp,
    ).run()

    rows = report.csv_rows()
    assert rows[0] == ["ip", "alive", "mac", "vendor", "method", "rtt_ms", "error"]
    assert [row[0] for row in rows[1:]] == ["10.0.0.1", "10.0.0.2"]
    assert report.alive_count == 2
    assert report.with_mac[0].ip == "10.0.0.2"


def test_report_timestamps_and_counts() -> None:
    ping = FakePing({"10.0.0.1": _reply("10.0.0.1")})
    report = IpScanSession(
        ["10.0.0.1"], mode=MODE_ICMP, nets=["10.0.0.0/30"], ping_runner=ping
    ).run()
    assert isinstance(report.started_at, datetime)
    assert report.finished_at >= report.started_at
    assert report.duration_s >= 0.0
    assert report.nets == ("10.0.0.0/30",)
    assert report.total == 1
    assert ipscan.MAX_HOSTS == 4096
