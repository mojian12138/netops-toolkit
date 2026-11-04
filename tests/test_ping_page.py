"""Ping 页面测试（diag.ping）：全部用假 runner，不发起真实网络请求。"""

from __future__ import annotations

import csv

import pytest
from PyQt6.QtWidgets import QApplication

from netops.platform_.os_layer import CommandError, PingReply, PingResult
from netops.ui.pages.ping_page import PingPage
from netops.ui.theme import apply_theme


@pytest.fixture(scope="module")
def app() -> QApplication:
    application = QApplication.instance() or QApplication(["pytest"])
    apply_theme(application)
    return application


@pytest.fixture()
def page(app) -> PingPage:
    widget = PingPage()
    yield widget
    widget.shutdown()
    widget.deleteLater()


def _ok(target: str, rtt: float = 1.5, ttl: int = 64) -> PingResult:
    reply = PingReply(seq=0, ttl=ttl, rtt_ms=rtt, from_addr=target)
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
    def __init__(self, mapping: dict[str, PingResult | Exception] | None = None) -> None:
        self.mapping = mapping or {}
        self.calls: list[str] = []

    def __call__(self, target: str, **kwargs) -> PingResult:
        self.calls.append(target)
        value = self.mapping.get(target)
        if isinstance(value, Exception):
            raise value
        return value if value is not None else _ok(target)


def _run(page: PingPage, targets: str, *, rounds: int = 2, runner=None) -> None:
    page.runner_factory = runner or FakeRunner()
    page.set_targets(targets)
    page.set_rounds(rounds)
    page.interval_spin.setValue(200)
    assert page.start()
    page.run_blocking()


def test_initial_state(page: PingPage) -> None:
    assert page.state() == "idle"
    assert page.start_button.isEnabled()
    assert not page.pause_button.isEnabled()
    assert not page.stop_button.isEnabled()
    assert not page.export_csv_button.isEnabled()
    assert page.status_text() == "等待开始"


def test_invalid_targets_show_error_and_do_not_start(page: PingPage) -> None:
    page.set_targets("192.168.1.999")
    assert page.start() is False
    assert "不是合法 IPv4" in page.error_text()
    assert page.state() == "idle"


def test_empty_targets_show_error(page: PingPage) -> None:
    page.set_targets("  \n# 注释\n")
    assert page.start() is False
    assert "至少输入一个" in page.error_text()


def test_single_target_fills_summary_and_detail(page: PingPage) -> None:
    _run(page, "127.0.0.1", rounds=2)

    assert page.state() == "finished"
    rows = page.summary_rows()
    assert len(rows) == 1
    assert rows[0][0] == "127.0.0.1"
    assert rows[0][1:4] == ["2", "2", "0%"]
    assert rows[0][7] == "正常"

    details = page.detail_rows()
    assert len(details) == 2
    assert details[0][1] == "127.0.0.1"
    assert details[0][3] == "成功"
    assert details[0][4] == "1.500"
    assert details[0][5] == "64"
    assert page.chart.point_count("127.0.0.1") == 2
    assert page.export_csv_button.isEnabled()
    assert page.export_md_button.isEnabled()


def test_batch_targets_all_appear(page: PingPage) -> None:
    runner = FakeRunner()
    _run(page, "192.168.1.1, 192.168.1.2\n10.0.0.1", rounds=1, runner=runner)

    assert sorted(runner.calls) == ["10.0.0.1", "192.168.1.1", "192.168.1.2"]
    assert [row[0] for row in page.summary_rows()] == ["192.168.1.1", "192.168.1.2", "10.0.0.1"]
    assert page.chart.series_names() == ["192.168.1.1", "192.168.1.2", "10.0.0.1"]


def test_timeout_marks_row_and_loss(page: PingPage) -> None:
    _run(page, "10.0.0.1", rounds=2, runner=FakeRunner({"10.0.0.1": _timeout("10.0.0.1")}))

    row = page.summary_rows()[0]
    assert row[1:4] == ["2", "0", "100%"]
    assert row[7] == "连续失败 2 次"
    assert page.detail_rows()[0][3] == "超时"
    assert page.detail_rows()[0][4] == "—"


def test_runner_error_is_reported(page: PingPage) -> None:
    _run(page, "10.0.0.9", rounds=1, runner=FakeRunner({"10.0.0.9": CommandError("命令执行超时")}))

    row = page.summary_rows()[0]
    assert row[7] == "错误"
    assert page.detail_rows()[0][3] == "错误"
    assert "命令执行超时" in page.detail_table.item(0, 0).toolTip()


def test_status_line_reports_totals(page: PingPage) -> None:
    _run(page, "1.1.1.1", rounds=2)
    assert "发送 2 / 接收 2 / 丢包 0.0%" in page.status_text()


def test_pause_and_resume_toggle_state(page: PingPage) -> None:
    page.runner_factory = FakeRunner()
    page.set_targets("1.1.1.1")
    page.set_rounds(5)
    page.interval_spin.setValue(200)
    assert page.start()
    page.toggle_pause()
    assert page.state() == "paused"
    assert page.pause_button.text() == "继续"
    page.toggle_pause()
    assert page.state() == "running"
    page.stop()
    page.run_blocking()


def test_stop_ends_session_early(page: PingPage) -> None:
    page.runner_factory = FakeRunner()
    page.set_targets("1.1.1.1")
    page.set_rounds(100)
    page.interval_spin.setValue(200)
    assert page.start()
    page.stop()
    page.run_blocking()

    assert page.state() == "finished"
    assert "已手动停止" in page.status_text()
    report = page.report()
    assert report is not None
    assert report.cancelled
    assert report.rounds_done < 100


def test_clear_results_resets_tables(page: PingPage) -> None:
    _run(page, "1.1.1.1", rounds=1)
    page.clear_results()
    assert page.summary_rows() == []
    assert page.detail_rows() == []
    assert page.chart.series_names() == []
    assert page.report() is None
    assert not page.export_csv_button.isEnabled()


def test_export_csv_contains_timestamp_rtt_ttl(page: PingPage, tmp_path) -> None:
    _run(page, "1.1.1.1", rounds=2)
    target = tmp_path / "ping.csv"
    assert page.export_csv(target) == 2

    with target.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["time", "target", "seq", "status", "rtt_ms", "ttl", "from"]
    assert rows[1][1:6] == ["1.1.1.1", "1", "成功", "1.500", "64"]
    assert rows[1][0][:4].isdigit()
    assert target.read_bytes()[:3] == b"\xef\xbb\xbf"


def test_export_markdown_contains_statistics(page: PingPage, tmp_path) -> None:
    _run(page, "1.1.1.1", rounds=2)
    target = tmp_path / "ping.md"
    assert page.export_markdown(target) > 0
    text = target.read_text(encoding="utf-8")
    assert "# Ping 探测报告" in text
    assert "合计：发送 2 / 接收 2 / 丢包 0.0%" in text
    assert "| 1.1.1.1 | 2 | 2 | 0.0% |" in text


def test_export_without_report_is_noop(page: PingPage, tmp_path) -> None:
    assert page.export_csv(tmp_path / "a.csv") == 0
    assert page.export_markdown(tmp_path / "a.md") == 0
    assert not (tmp_path / "a.csv").exists()
    assert not (tmp_path / "a.md").exists()


def test_chart_handles_timeouts_and_clear(page: PingPage) -> None:
    chart = page.chart
    chart.add_sample("a", 0.0, 10.0)
    chart.add_sample("a", 1.0, None)
    chart.add_sample("b", 1.0, 20.0)
    assert chart.point_count("a") == 2
    assert chart.max_rtt() == 20.0
    chart.clear()
    assert chart.series_names() == []
    assert chart.max_rtt() is None
