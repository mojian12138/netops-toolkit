"""平台命令解析单测：用固定文本样例，不依赖真实网络。"""

from __future__ import annotations

from netops.platform_.os_layer import (
    _normalize_mac,
    _ping_argv,
    _traceroute_argv,
    is_macos,
    parse_arp_output,
    parse_ping_output,
    parse_traceroute_output,
)

_MACOS_PING_OK = """PING 127.0.0.1 (127.0.0.1): 56 data bytes
64 bytes from 127.0.0.1: icmp_seq=0 ttl=64 time=0.048 ms
64 bytes from 127.0.0.1: icmp_seq=1 ttl=64 time=0.061 ms
64 bytes from 127.0.0.1: icmp_seq=2 ttl=64 time=0.059 ms

--- 127.0.0.1 ping statistics ---
3 packets transmitted, 3 packets received, 0.0% packet loss
round-trip min/avg/max/stddev = 0.048/0.056/0.061/0.006 ms
"""

_MACOS_PING_LOSS = """PING 10.255.255.1 (10.255.255.1): 56 data bytes
Request timeout for icmp_seq 0
Request timeout for icmp_seq 1

--- 10.255.255.1 ping statistics ---
2 packets transmitted, 0 packets received, 100.0% packet loss
"""

_TRACEROUTE_OK = """traceroute to 8.8.8.8 (8.8.8.8), 20 hops max, 52 byte packets
 1  192.168.1.1  1.234 ms
 2  10.0.0.1  5.678 ms
 3  *
 4  203.0.113.5  12.345 ms
"""

_ARP_MACOS = """? (192.168.1.1) at 0:11:22:33:44:55 on en0 ifscope [ethernet]
? (192.168.1.20) at a4:5e:60:12:34:56 on en0 ifscope [ethernet]
? (192.168.1.99) at (incomplete) on en0 ifscope [ethernet]
"""


def test_parse_ping_success() -> None:
    result = parse_ping_output("127.0.0.1", _MACOS_PING_OK)
    assert result.ok
    assert result.transmitted == 3
    assert result.received == 3
    assert result.packet_loss == 0.0
    assert result.min_ms == 0.048
    assert result.avg_ms == 0.056
    assert len(result.replies) == 3
    assert result.replies[0].ttl == 64


def test_parse_ping_total_loss() -> None:
    result = parse_ping_output("10.255.255.1", _MACOS_PING_LOSS)
    assert not result.ok
    assert result.received == 0
    assert result.packet_loss == 100.0
    assert all(reply.timed_out for reply in result.replies)
    assert result.avg_ms is None


def test_parse_ping_without_stats_block() -> None:
    result = parse_ping_output("x", "64 bytes from 1.1.1.1: icmp_seq=0 ttl=55 time=10.5 ms\n")
    assert result.transmitted == 1
    assert result.received == 1
    assert result.avg_ms == 10.5


def test_parse_traceroute() -> None:
    result = parse_traceroute_output("8.8.8.8", _TRACEROUTE_OK)
    assert len(result.hops) == 4
    assert result.hops[0].address == "192.168.1.1"
    assert result.hops[0].rtt_ms == 1.234
    assert result.hops[2].timed_out
    assert result.reached


def test_parse_traceroute_missing_hop_marks_unreached() -> None:
    result = parse_traceroute_output("x", " 1  10.0.0.1  1.0 ms\n 2  * \n")
    assert not result.reached


def test_parse_arp_macos_skips_incomplete() -> None:
    entries = parse_arp_output(_ARP_MACOS)
    assert [entry.ip for entry in entries] == ["192.168.1.1", "192.168.1.20"]
    assert entries[0].mac == "00:11:22:33:44:55"
    assert entries[0].interface == "en0"


def test_parse_arp_windows_format() -> None:
    text = "  192.168.1.1      aa-bb-cc-dd-ee-ff     dynamic\n"
    entries = parse_arp_output(text)
    assert entries[0].mac == "aa:bb:cc:dd:ee:ff"
    assert entries[0].flags == "dynamic"


def test_normalize_mac_variants() -> None:
    assert _normalize_mac("AABB.CCDD.EEFF") == "aa:bb:cc:dd:ee:ff"
    assert _normalize_mac("aa-bb-cc-dd-ee-ff") == "aa:bb:cc:dd:ee:ff"
    assert _normalize_mac("bad") == "bad"


def test_ping_argv_shape() -> None:
    argv = _ping_argv("1.1.1.1", 3, 1000, 56)
    assert argv[-1] == "1.1.1.1"
    if is_macos():
        assert "-c" in argv and "-W" in argv


def test_traceroute_argv_shape() -> None:
    argv = _traceroute_argv("1.1.1.1", 20)
    assert argv[-1] == "1.1.1.1"
    assert "20" in argv
