"""端口扫描页面测试（diag.portscan）：本地监听端口，不访问外网。"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

from netops.ui.pages.portscan_page import PortScanPage
from netops.ui.theme import apply_theme


@pytest.fixture(scope="module")
def app() -> QApplication:
    application = QApplication.instance() or QApplication(["pytest"])
    apply_theme(application)
    return application


@pytest.fixture()
def page(app) -> PortScanPage:
    widget = PortScanPage()
    yield widget
    widget.shutdown()
    widget.deleteLater()


@pytest.fixture()
def listener() -> Iterator[int]:
    banner = b"SSH-2.0-PageTest_1.0\r\n"
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
        yield port
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


def test_defaults_are_idle(page: PortScanPage) -> None:
    assert page.state() == "idle"
    assert page.concurrency_slider.value() > 0
    assert page.banner_check.isChecked()
    assert page.table_rows() == []
    assert page.export_csv_button.isEnabled() is False


def test_start_requires_targets(page: PortScanPage) -> None:
    page.set_presets(["Web"])
    assert page.start() is False
    assert "至少输入一个目标" in page.error_text()


def test_start_requires_ports(page: PortScanPage) -> None:
    page.set_targets("127.0.0.1")
    page.set_presets([])
    page.set_custom_ports("")
    assert page.start() is False
    assert "端口" in page.error_text()


def test_scan_local_listener_and_render_rows(page: PortScanPage, listener: int) -> None:
    closed = _free_port()
    page.set_targets("127.0.0.1")
    page.set_presets([])
    page.set_custom_ports(f"{listener},{closed}")
    assert page.start() is True
    page.run_blocking()

    assert page.state() == "finished"
    rows = page.table_rows()
    assert len(rows) == 1
    assert rows[0][0] == "127.0.0.1"
    assert rows[0][1] == str(listener)
    assert "SSH-2.0-PageTest" in rows[0][4]
    assert page.report() is not None
    assert page.report().open_count == 1
    assert page.export_csv_button.isEnabled() is True


def test_invalid_ports_show_inline_error(page: PortScanPage) -> None:
    page.set_targets("127.0.0.1")
    page.set_presets([])
    page.set_custom_ports("http")
    assert page.start() is False
    assert "端口格式不合法" in page.error_text()
    assert page.state() == "idle"


def test_concurrency_warning_text(page: PortScanPage) -> None:
    page.concurrency_slider.setValue(200)
    assert "防扫描" in page.risk_hint.text()
    page.concurrency_slider.setValue(16)
    assert page.risk_hint.text() == ""


def test_export_csv_and_markdown(page: PortScanPage, listener: int, tmp_path: Path) -> None:
    page.set_targets("127.0.0.1")
    page.set_presets([])
    page.set_custom_ports(str(listener))
    assert page.start() is True
    page.run_blocking()

    csv_path = tmp_path / "scan.csv"
    rows = page.export_csv(csv_path)
    assert rows == 1
    text = csv_path.read_text(encoding="utf-8-sig")
    assert "host,port,state,service,banner" in text
    assert "ssh" in text

    md_path = tmp_path / "scan.md"
    lines = page.export_markdown(md_path)
    assert lines > 0
    assert "# 端口扫描报告" in md_path.read_text(encoding="utf-8")


def test_clear_results_resets_table(page: PortScanPage, listener: int) -> None:
    page.set_targets("127.0.0.1")
    page.set_presets([])
    page.set_custom_ports(str(listener))
    page.start()
    page.run_blocking()
    assert page.table_rows()

    page.clear_results()
    assert page.table_rows() == []
    assert page.report() is None
    assert page.export_csv_button.isEnabled() is False
    assert page.export_md_button.isEnabled() is False
