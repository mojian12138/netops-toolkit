"""端口扫描服务层单测：本地起临时监听端口，不访问外网。"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from datetime import datetime

import pytest

from netops.services.portscan import (
    MAX_CONCURRENCY,
    MAX_PORTS,
    MAX_TARGETS,
    PortScanError,
    PortScanner,
    check_workload,
    guess_service,
    parse_banner,
    parse_ports,
    parse_targets,
    preset_ports,
)

# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def test_parse_targets_supports_cidr_range_and_dedup() -> None:
    text = """
    192.168.1.0/30        # 网段
    192.168.1.10-12
    router.local, 192.168.1.1
    """
    targets = parse_targets(text)
    assert targets == (
        "192.168.1.1",
        "192.168.1.2",
        "192.168.1.10",
        "192.168.1.11",
        "192.168.1.12",
        "router.local",
    )


def test_parse_targets_rejects_garbage_and_ipv6() -> None:
    with pytest.raises(PortScanError, match="不是合法"):
        parse_targets("192.168.1.999")
    with pytest.raises(PortScanError, match="IPv6"):
        parse_targets("fe80::1")
    with pytest.raises(PortScanError, match="至少输入一个"):
        parse_targets("   \n  # 全是注释\n")


def test_parse_targets_enforces_limit() -> None:
    with pytest.raises(PortScanError, match="超过单次上限"):
        parse_targets("10.0.0.0/16")
    with pytest.raises(PortScanError, match="超过单次上限"):
        parse_targets("10.0.0.1,10.0.0.2", limit=1)
    assert len(parse_targets("10.0.0.0/24")) == MAX_TARGETS - 2


def test_parse_ports_ranges_and_dedup() -> None:
    assert parse_ports("80, 1-3, 3, 8080") == (1, 2, 3, 80, 8080)
    assert parse_ports("100-99") == (99, 100)
    with pytest.raises(PortScanError, match="格式不合法"):
        parse_ports("http")
    with pytest.raises(PortScanError, match="至少指定一个端口"):
        parse_ports(" , ")
    with pytest.raises(PortScanError, match="超过单次上限"):
        parse_ports("1-65535")


def test_preset_ports_merges_and_rejects_unknown() -> None:
    web = preset_ports(["Web"])
    assert 80 in web and 443 in web
    merged = preset_ports(["Web", "远程登录"])
    assert merged == tuple(sorted(set(web) | set(preset_ports(["远程登录"]))))
    with pytest.raises(PortScanError, match="未知的端口预设"):
        preset_ports(["不存在"])


def test_check_workload_warns_only_when_large() -> None:
    assert check_workload(1, 10, 32) is None
    warning = check_workload(MAX_TARGETS, 1024, MAX_CONCURRENCY)
    assert warning is not None and "防扫描" in warning


# ---------------------------------------------------------------------------
# 服务识别
# ---------------------------------------------------------------------------


def test_guess_service_prefers_table_then_falls_back() -> None:
    assert guess_service(22) == "ssh"
    assert guess_service(6379) == "redis"
    assert guess_service(54321) == ""


@pytest.mark.parametrize(
    ("raw", "port", "expect"),
    [
        (
            b"SSH-2.0-OpenSSH_9.6p1 Ubuntu-3ubuntu13\r\n",
            22,
            ("SSH-2.0-OpenSSH_9.6p1 Ubuntu-3ubuntu13", "ssh"),
        ),
        (
            b"HTTP/1.1 200 OK\r\nServer: nginx/1.24.0\r\n\r\n",
            80,
            ("HTTP/1.1 200 OK Server: nginx/1.24.0", "http"),
        ),
        # 二进制内容：不可打印字节被压成空格，服务名退回端口表。
        (b"\xff\xfe\x00\x01binary\x00junk", 3306, ("binary junk", "mysql")),
        (b"", 443, ("", "https")),
    ],
)
def test_parse_banner(raw: bytes, port: int, expect: tuple[str, str]) -> None:
    banner, service = parse_banner(raw, port)
    assert banner == expect[0]
    assert service == expect[1]


# ---------------------------------------------------------------------------
# 真实连接（本地回环）
# ---------------------------------------------------------------------------


@pytest.fixture()
def listener() -> Iterator[tuple[int, bytes]]:
    """起一个本地监听：连上后吐 banner，然后关闭。"""
    banner = b"SSH-2.0-TestServer_1.0\r\n"
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(16)
    port = server.getsockname()[1]
    stop = threading.Event()

    def serve() -> None:
        while not stop.is_set():
            try:
                server.settimeout(0.2)
                conn, _ = server.accept()
            except (TimeoutError, OSError):
                continue
            try:
                conn.sendall(banner)
            except OSError:
                pass
            finally:
                conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield port, banner
    finally:
        stop.set()
        server.close()
        thread.join(timeout=2)


def _free_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def test_scan_hits_open_port_with_banner(listener) -> None:
    port, banner = listener
    closed = _free_port()
    report = PortScanner(["127.0.0.1"], [port, closed], concurrency=8, timeout_s=1.0).run()

    assert report.total_probes == 2
    target = report.results[0]
    by_port = {item.port: item for item in target.ports}
    assert by_port[port].state == "open"
    assert by_port[port].service == "ssh"
    assert banner.decode().strip() in by_port[port].banner
    assert by_port[port].latency_ms is not None
    assert by_port[closed].state == "closed"
    assert report.open_count == 1
    assert report.alive_hosts == ("127.0.0.1",)
    assert report.cancelled is False


def test_scan_respects_banner_off(listener) -> None:
    port, _ = listener
    report = PortScanner(["127.0.0.1"], [port], grab_banner=False).run()
    item = report.results[0].ports[0]
    assert item.state == "open"
    assert item.banner == ""
    # 关掉 Banner 抓取后只剩端口表推断；监听端口是随机的，因此为空。
    assert item.service == guess_service(port)


def test_scan_cancel_returns_partial(listener) -> None:
    port, _ = listener
    scanner = PortScanner(["127.0.0.1"], [port, 1, 2, 3], should_cancel=lambda: True, concurrency=4)
    report = scanner.run()
    assert report.cancelled is True
    assert scanner.done == 0


def test_scan_reports_progress_callback(listener) -> None:
    port, _ = listener
    seen = []
    PortScanner(["127.0.0.1"], [port, 1], concurrency=2, on_result=seen.append).run()
    assert len(seen) == 2
    assert {item.state for item in seen} == {"open", "closed"}


def test_scan_callback_exception_does_not_abort(listener) -> None:
    port, _ = listener

    def boom(_result) -> None:
        raise RuntimeError("展示层炸了")

    report = PortScanner(["127.0.0.1"], [port], on_result=boom).run()
    assert report.open_count == 1


# ---------------------------------------------------------------------------
# 参数校验与导出
# ---------------------------------------------------------------------------


def test_scanner_rejects_bad_parameters() -> None:
    with pytest.raises(PortScanError, match="至少输入一个目标"):
        PortScanner([], [80])
    with pytest.raises(PortScanError, match="至少指定一个端口"):
        PortScanner(["127.0.0.1"], [])
    with pytest.raises(PortScanError, match="超出 1-65535"):
        PortScanner(["127.0.0.1"], [70000])
    with pytest.raises(PortScanError, match="并发数"):
        PortScanner(["127.0.0.1"], [80], concurrency=MAX_CONCURRENCY + 1)
    with pytest.raises(PortScanError, match="超时"):
        PortScanner(["127.0.0.1"], [80], timeout_s=30)
    with pytest.raises(PortScanError, match="超过单次上限"):
        PortScanner(["127.0.0.1"], list(range(1, MAX_PORTS + 2)))


def test_report_export_rows_and_markdown(listener) -> None:
    port, _ = listener
    report = PortScanner(["127.0.0.1"], [port, _free_port()]).run()

    rows = report.csv_rows()
    assert rows[0] == ["host", "port", "state", "service", "banner", "latency_ms", "error"]
    assert len(rows) == 3

    open_rows = report.open_rows()
    assert open_rows and open_rows[0][1] == port
    assert open_rows[0][2] == "ssh"

    text = report.to_markdown()
    assert "# 端口扫描报告" in text
    assert f"| 127.0.0.1 | {port} | ssh |" in text


def test_empty_report_markdown_mentions_no_open_ports() -> None:
    report = PortScanner(["127.0.0.1"], [_free_port()]).run()
    assert report.open_count == 0
    assert "未发现开放端口" in report.to_markdown()


def test_report_timestamps_are_ordered(listener) -> None:
    port, _ = listener
    report = PortScanner(["127.0.0.1"], [port]).run()
    assert isinstance(report.started_at, datetime)
    assert report.finished_at >= report.started_at
    assert report.duration_s >= 0.0
