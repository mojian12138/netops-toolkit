"""路由追踪页面测试（diag.traceroute）：全部注入假 runner，不发起真实网络请求。"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

from netops.core.db import Database
from netops.platform_.os_layer import CommandError, TracerouteHop, TracerouteResult
from netops.services.devices import DeviceBook
from netops.ui.pages.traceroute_page import TraceroutePage
from netops.ui.theme import apply_theme


@pytest.fixture(scope="module")
def app() -> QApplication:
    application = QApplication.instance() or QApplication(["pytest"])
    apply_theme(application)
    return application


@pytest.fixture()
def page(app) -> TraceroutePage:
    widget = TraceroutePage()
    yield widget
    widget.shutdown()
    widget.deleteLater()


@pytest.fixture()
def db(tmp_path) -> Database:
    database = Database(tmp_path / "trace.sqlite3")
    database.connect()
    yield database
    database.close()


def _result(hops: list[tuple[int, str, float | None]], *, target: str = "8.8.8.8"):
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
        reached=True,
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


# ---------------------------------------------------------------------------
# 初始状态与校验
# ---------------------------------------------------------------------------


def test_defaults_are_idle(page: TraceroutePage) -> None:
    assert page.state() == "idle"
    assert page.table_rows() == []
    assert page.timeline.hop_count() == 0
    assert page.export_md_button.isEnabled() is False
    assert page.copy_path_button.isEnabled() is False


def test_start_requires_target(page: TraceroutePage) -> None:
    assert page.start() is False
    assert "不能为空" in page.error_text()


def test_start_rejects_bad_target(page: TraceroutePage) -> None:
    page.set_target("256.1.1.1")
    assert page.start() is False
    assert "不是合法的 IPv4" in page.error_text()
    assert page.state() == "idle"


def test_start_rejects_ipv6(page: TraceroutePage) -> None:
    page.set_target("fe80::1")
    assert page.start() is False
    assert "IPv6" in page.error_text()


# ---------------------------------------------------------------------------
# 追踪与渲染
# ---------------------------------------------------------------------------


def test_trace_renders_timeline_and_table(page: TraceroutePage) -> None:
    page.runner_factory = FakeRunner(
        _result([(1, "10.0.0.1", 1.2), (2, "*", None), (3, "203.0.113.5", 8.4)])
    )
    page.set_target("8.8.8.8")
    assert page.start() is True
    page.run_blocking()

    assert page.state() == "finished"
    rows = page.table_rows()
    assert len(rows) == 3
    assert rows[0][0] == "1"
    assert rows[0][1] == "10.0.0.1"
    assert rows[0][2] == "1.2"
    assert rows[1][1] == "*"
    assert rows[1][2] == "—"
    assert page.timeline.hop_count() == 3
    assert "3 跳" in page.summary_text()
    assert "1 跳超时" in page.summary_text()
    assert page.export_md_button.isEnabled() is True


def test_trace_passes_parameters(page: TraceroutePage) -> None:
    runner = FakeRunner(_result([(1, "10.0.0.1", 1.0)]))
    page.runner_factory = runner
    page.set_target("8.8.8.8")
    page.set_max_hops(12)
    page.start()
    page.run_blocking()

    assert len(runner.calls) == 1
    target, kwargs = runner.calls[0]
    assert target == "8.8.8.8"
    assert kwargs["max_hops"] == 12
    assert kwargs["timeout_s"] == pytest.approx(2.0)
    assert kwargs["probe"] == "icmp"
    assert kwargs["tcp_port"] == 443


def test_probe_switch_updates_port_row_and_params(page: TraceroutePage) -> None:
    runner = FakeRunner(_result([(1, "10.0.0.1", 1.0)]))
    page.runner_factory = runner
    page.set_target("8.8.8.8")

    # TCP 模式：端口行可见、端口值随 session 传入
    page.set_probe("tcp")
    page.tcp_port_spin.setValue(8443)
    assert page.tcp_port_spin.isEnabled()
    page.start()
    page.run_blocking()
    assert runner.calls[-1][1]["probe"] == "tcp"
    assert runner.calls[-1][1]["tcp_port"] == 8443

    # 切回 UDP：端口行隐藏
    page.set_probe("udp")
    assert not page._tcp_port_row.isVisibleTo(page)
    page.start()
    page.run_blocking()
    assert runner.calls[-1][1]["probe"] == "udp"

    with pytest.raises(Exception, match="探测方式"):
        page.set_probe("gre")


def test_all_timeout_hint_mentions_probe_switch(page: TraceroutePage) -> None:
    page.runner_factory = FakeRunner(_result([(1, "*", None), (2, "*", None)]))
    page.set_target("8.8.8.8")
    page.start()
    page.run_blocking()

    assert "切换探测方式" in page.status_text()
    assert "探测：ICMP" in page.status_text()


def test_command_error_shows_inline(page: TraceroutePage) -> None:
    page.runner_factory = FakeRunner(CommandError("traceroute 不存在"))
    page.set_target("8.8.8.8")
    page.start()
    page.run_blocking()

    assert "traceroute 不存在" in page.error_text()
    assert page.state() == "idle"


# ---------------------------------------------------------------------------
# 备注
# ---------------------------------------------------------------------------


def test_remarks_show_on_trace(page: TraceroutePage, db: Database) -> None:
    book = DeviceBook(db)
    book.mark("10.0.0.1", remark="核心交换机", location="机房A")
    page.set_device_book(book)
    page.runner_factory = FakeRunner(_result([(1, "10.0.0.1", 1.0), (2, "203.0.113.5", 6.0)]))
    page.set_target("8.8.8.8")
    page.start()
    page.run_blocking()

    rows = page.table_rows()
    assert rows[0][3] == "核心交换机 · 机房A"
    assert rows[1][3] == "—"
    assert "1 跳已备注" in page.summary_text()


def test_save_remark_refreshes_report(page: TraceroutePage, db: Database) -> None:
    page.set_device_book(DeviceBook(db))
    page.runner_factory = FakeRunner(_result([(1, "10.0.0.1", 1.0)]))
    page.set_target("8.8.8.8")
    page.start()
    page.run_blocking()

    page.table.selectRow(0)
    page.mark_input.setText("楼宇汇聚 SW-3F")
    assert page.save_remark() == "10.0.0.1"
    assert page.table_rows()[0][3] == "楼宇汇聚 SW-3F"
    assert page.report() is not None
    assert page.report().hops[0].name == "楼宇汇聚 SW-3F"
    assert db.get_device("10.0.0.1").remark == "楼宇汇聚 SW-3F"


def test_save_remark_rejects_timeout_hop(page: TraceroutePage, db: Database) -> None:
    page.set_device_book(DeviceBook(db))
    page.runner_factory = FakeRunner(_result([(1, "*", None)]))
    page.set_target("8.8.8.8")
    page.start()
    page.run_blocking()

    page.table.selectRow(0)
    assert page.save_remark() is None
    assert "没有回应" in page.error_text()


def test_save_remark_without_database(page: TraceroutePage) -> None:
    page.runner_factory = FakeRunner(_result([(1, "10.0.0.1", 1.0)]))
    page.set_target("8.8.8.8")
    page.start()
    page.run_blocking()

    page.table.selectRow(0)
    assert page.save_remark() is None
    assert "备注库不可用" in page.error_text()


def test_save_remark_requires_selection(page: TraceroutePage) -> None:
    assert page.save_remark() is None
    assert "请先在表格中选中" in page.error_text()


# ---------------------------------------------------------------------------
# 复制与导出
# ---------------------------------------------------------------------------


def test_copy_path_builds_plain_text(page: TraceroutePage) -> None:
    page.runner_factory = FakeRunner(
        _result([(1, "10.0.0.1", 1.2), (2, "*", None), (3, "8.8.8.8", 9.5)])
    )
    page.set_target("8.8.8.8")
    page.start()
    page.run_blocking()

    text = page.copy_path()
    assert " 1  10.0.0.1  1.2 ms" in text
    assert " 2  *" in text
    assert " 3  8.8.8.8  9.5 ms" in text
    assert QApplication.clipboard().text() == text


def test_copy_selected_address(page: TraceroutePage) -> None:
    page.runner_factory = FakeRunner(_result([(1, "10.0.0.1", 1.0)]))
    page.set_target("8.8.8.8")
    page.start()
    page.run_blocking()

    page.table.selectRow(0)
    assert page.copy_selected() == "10.0.0.1"
    assert QApplication.clipboard().text() == "10.0.0.1"


def test_export_markdown_and_csv(page: TraceroutePage, tmp_path: Path) -> None:
    page.runner_factory = FakeRunner(_result([(1, "10.0.0.1", 1.2), (2, "8.8.8.8", 9.5)]))
    page.set_target("8.8.8.8")
    page.start()
    page.run_blocking()

    md_path = tmp_path / "trace.md"
    assert page.export_markdown(md_path) > 0
    text = md_path.read_text(encoding="utf-8")
    assert "# 路由追踪报告" in text
    assert "| 1 | 10.0.0.1 | 1.2 |" in text

    csv_path = tmp_path / "trace.csv"
    assert page.export_csv(csv_path) == 2
    rows = list(csv.reader(csv_path.read_text(encoding="utf-8-sig").splitlines()))
    assert rows[0] == ["hop", "address", "rtt_ms", "timed_out", "name", "location", "model"]
    assert rows[1][1] == "10.0.0.1"


def test_clear_results_resets_view(page: TraceroutePage) -> None:
    page.runner_factory = FakeRunner(_result([(1, "10.0.0.1", 1.0)]))
    page.set_target("8.8.8.8")
    page.start()
    page.run_blocking()
    assert page.table_rows()

    page.clear_results()
    assert page.table_rows() == []
    assert page.timeline.hop_count() == 0
    assert page.report() is None
    assert page.export_md_button.isEnabled() is False
