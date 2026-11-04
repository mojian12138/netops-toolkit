"""局域网扫描页面测试（diag.ipscan）：全部注入假 ping / 假 ARP，不发起真实网络请求。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

from netops.core.db import Database
from netops.platform_.os_layer import ArpEntry, CommandError, PingReply, PingResult
from netops.services.devices import DeviceBook
from netops.ui.pages.ipscan_page import CONCURRENCY_WARN, IpScanPage
from netops.ui.theme import apply_theme


@pytest.fixture(scope="module")
def app() -> QApplication:
    application = QApplication.instance() or QApplication(["pytest"])
    apply_theme(application)
    return application


@pytest.fixture()
def page(app) -> IpScanPage:
    widget = IpScanPage()
    yield widget
    widget.shutdown()
    widget.deleteLater()


@pytest.fixture()
def db(tmp_path) -> Database:
    database = Database(tmp_path / "ipscan.sqlite3")
    database.connect()
    yield database
    database.close()


def _reply(target: str, rtt: float = 2.5) -> PingResult:
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
    def __init__(self, alive: set[str] | None = None) -> None:
        self.alive = alive or set()
        self.calls: list[str] = []

    def __call__(self, target: str, **kwargs) -> PingResult:
        self.calls.append(target)
        return _reply(target) if target in self.alive else _timeout(target)


def _install(page: IpScanPage, *, alive: set[str], arp: tuple[ArpEntry, ...] = ()) -> FakePing:
    ping = FakePing(alive)
    page.ping_runner = ping
    page.arp_reader = lambda **kwargs: arp
    return ping


# ---------------------------------------------------------------------------
# 初始状态与规模预估
# ---------------------------------------------------------------------------


def test_defaults_are_idle(page: IpScanPage) -> None:
    assert page.state() == "idle"
    assert page.mode() == "both"
    assert page.table_rows() == []
    assert page.export_csv_button.isEnabled() is False
    assert page.scale_text() == "等待估算"


def test_scale_hint_updates_with_input(page: IpScanPage) -> None:
    page.set_networks("192.168.1.0/30")
    assert "2" in page.scale_text()

    page.set_networks("192.168.1.0/16")
    assert "超过单次上限" in page.scale_text()

    page.set_networks("垃圾输入")
    assert "不是合法" in page.scale_text()


def test_concurrency_warning_appears_in_scale_hint(page: IpScanPage) -> None:
    page.set_networks("10.0.0.0/24")
    page.concurrency_slider.setValue(CONCURRENCY_WARN)
    assert "防扫描" in page.scale_text()
    page.concurrency_slider.setValue(16)
    assert "防扫描" not in page.scale_text()


# ---------------------------------------------------------------------------
# 启动校验
# ---------------------------------------------------------------------------


def test_start_requires_networks(page: IpScanPage) -> None:
    assert page.start() is False
    assert "请至少输入一个网段" in page.error_text()


def test_start_rejects_oversize_network(page: IpScanPage) -> None:
    page.set_networks("10.0.0.0/16")
    assert page.start() is False
    assert "超过单次上限" in page.error_text()
    assert page.state() == "idle"


# ---------------------------------------------------------------------------
# 扫描与渲染
# ---------------------------------------------------------------------------


def test_scan_renders_alive_hosts_sorted(page: IpScanPage) -> None:
    # /28 的可用地址是 .1–.14，故意用 .2 与 .10 验证按数值而非字典序排序。
    _install(page, alive={"10.0.0.2", "10.0.0.10"})
    page.set_networks("10.0.0.0/28")
    page.set_mode("icmp")
    assert page.start() is True
    page.run_blocking()

    assert page.state() == "finished"
    rows = page.table_rows()
    assert [row[0] for row in rows] == ["10.0.0.2", "10.0.0.10"]
    assert rows[0][4] == "icmp"
    assert rows[0][5] == "2.5"
    assert page.report() is not None
    assert page.report().alive_count == 2
    assert page.export_csv_button.isEnabled() is True


def test_arp_only_hits_are_marked(page: IpScanPage) -> None:
    arp = (
        ArpEntry(ip="10.0.0.3", mac="00:1a:2b:3c:4d:5e"),
        ArpEntry(ip="10.0.0.4", mac="aa:bb:cc:dd:ee:ff"),
    )
    _install(page, alive=set(), arp=arp)
    page.set_networks("10.0.0.3-4")
    page.set_mode("arp")
    assert page.start() is True
    page.run_blocking()

    rows = page.table_rows()
    assert len(rows) == 2
    assert rows[0][1] == "00:1a:2b:3c:4d:5e"
    assert rows[0][4] == "arp"
    assert rows[0][5] == "—"


def test_arp_reader_failure_still_shows_icmp_results(page: IpScanPage) -> None:
    page.ping_runner = FakePing({"10.0.0.1"})
    page.arp_reader = lambda **kwargs: (_ for _ in ()).throw(CommandError("arp 不存在"))
    page.set_networks("10.0.0.1")
    page.set_mode("both")
    assert page.start() is True
    page.run_blocking()

    assert page.state() == "finished"
    assert [row[0] for row in page.table_rows()] == ["10.0.0.1"]


# ---------------------------------------------------------------------------
# 备注簿
# ---------------------------------------------------------------------------


def test_remark_saved_and_refreshed(page: IpScanPage, db: Database) -> None:
    page.set_device_book(DeviceBook(db))
    _install(page, alive={"10.0.0.5"})
    page.set_networks("10.0.0.5")
    page.set_mode("icmp")
    page.start()
    page.run_blocking()

    page.table.selectRow(0)
    page.remark_input.setText("3F 弱电井 AP")
    assert page.save_remark() == "10.0.0.5"
    assert page.table_rows()[0][3] == "3F 弱电井 AP"
    assert db.get_device("10.0.0.5").remark == "3F 弱电井 AP"


def test_remark_saved_before_scan_shows_on_rescan(page: IpScanPage, db: Database) -> None:
    book = DeviceBook(db)
    book.mark("10.0.0.6", remark="核心交换机")
    page.set_device_book(book)
    _install(page, alive={"10.0.0.6"})
    page.set_networks("10.0.0.6")
    page.set_mode("icmp")
    page.start()
    page.run_blocking()

    assert page.table_rows()[0][3] == "核心交换机"


def test_save_remark_without_database_reports_error(page: IpScanPage) -> None:
    _install(page, alive={"10.0.0.7"})
    page.set_networks("10.0.0.7")
    page.set_mode("icmp")
    page.start()
    page.run_blocking()

    page.table.selectRow(0)
    assert page.save_remark() is None
    assert "备注库不可用" in page.error_text()


def test_save_remark_requires_selection(page: IpScanPage) -> None:
    assert page.save_remark() is None
    assert "请先在表格中选中" in page.error_text()


# ---------------------------------------------------------------------------
# 复制与导出
# ---------------------------------------------------------------------------


def test_copy_selected_puts_ip_on_clipboard(page: IpScanPage) -> None:
    _install(page, alive={"10.0.0.8"})
    page.set_networks("10.0.0.8")
    page.set_mode("icmp")
    page.start()
    page.run_blocking()

    page.table.selectRow(0)
    assert page.copy_selected() == "10.0.0.8"
    assert QApplication.clipboard().text() == "10.0.0.8"


def test_export_csv_and_markdown(page: IpScanPage, tmp_path: Path) -> None:
    _install(page, alive={"10.0.0.9"})
    page.set_networks("10.0.0.9")
    page.set_mode("icmp")
    page.start()
    page.run_blocking()

    csv_path = tmp_path / "ipscan.csv"
    assert page.export_csv(csv_path) == 1
    text = csv_path.read_text(encoding="utf-8-sig")
    assert "ip,alive,mac,vendor,method" in text
    assert "10.0.0.9" in text

    md_path = tmp_path / "ipscan.md"
    assert page.export_markdown(md_path) > 0
    assert "# 局域网扫描报告" in md_path.read_text(encoding="utf-8")


def test_clear_results_resets_table(page: IpScanPage) -> None:
    _install(page, alive={"10.0.0.10"})
    page.set_networks("10.0.0.10")
    page.set_mode("icmp")
    page.start()
    page.run_blocking()
    assert page.table_rows()

    page.clear_results()
    assert page.table_rows() == []
    assert page.report() is None
    assert page.export_csv_button.isEnabled() is False
