"""超网拆分计算器页面测试（calc.split）。"""

from __future__ import annotations

import csv

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from netops.ui.pages import split_page
from netops.ui.pages.split_page import SplitPage, SubnetTableModel
from netops.ui.theme import apply_theme

_MODE_HOSTS = 1


@pytest.fixture(scope="module")
def app() -> QApplication:
    application = QApplication.instance() or QApplication(["pytest"])
    apply_theme(application)
    return application


@pytest.fixture()
def page(app) -> SplitPage:
    widget = SplitPage()
    yield widget
    widget.deleteLater()


def test_split_by_count_produces_power_of_two(page: SplitPage) -> None:
    page.set_network("192.168.0.0/24")
    page.count.setValue(3)
    infos = page.calculate()
    assert infos is not None
    assert len(infos) == 4
    assert page.model.rowCount() == 4
    assert page.table_value(0, 1) == "192.168.0.0/26"
    assert page.table_value(3, 1) == "192.168.0.192/26"
    assert page.table_value(0, 0) == "1"
    assert page.table_value(3, 0) == "4"
    assert page.table_value(0, 2) == "255.255.255.192"
    assert page.table_value(0, 3) == "192.168.0.1 - 192.168.0.62"
    assert page.table_value(0, 4) == "192.168.0.63"
    assert page.table_value(0, 5) == "62"


def test_split_by_count_one_keeps_network(page: SplitPage) -> None:
    page.set_network("10.0.0.0/24")
    page.count.setValue(1)
    infos = page.calculate()
    assert infos is not None
    assert [info.cidr for info in infos] == ["10.0.0.0/24"]
    assert page.summary_text().startswith("拆分为 1 段 /24")


def test_split_with_mask_notation_input(page: SplitPage) -> None:
    page.set_network("10.0.0.0 255.255.0.0")
    page.count.setValue(2)
    infos = page.calculate()
    assert infos is not None
    assert [info.cidr for info in infos] == ["10.0.0.0/17", "10.0.128.0/17"]


def test_split_by_hosts(page: SplitPage) -> None:
    page.set_network("10.0.0.0/24")
    page.mode.setCurrentIndex(_MODE_HOSTS)
    page.count.setValue(50)
    infos = page.calculate()
    assert infos is not None
    assert len(infos) == 4
    assert all(info.usable_hosts >= 50 for info in infos)
    assert page.table_value(1, 5) == "62"


def test_host_mode_rounds_to_power_of_two(page: SplitPage) -> None:
    page.set_network("10.0.0.0/16")
    page.mode.setCurrentIndex(_MODE_HOSTS)
    page.count.setValue(100)
    infos = page.calculate()
    assert infos is not None
    assert all(info.cidr.endswith("/25") for info in infos)
    assert len(infos) == 512


def test_host_mode_switch_resets_count_and_hint(page: SplitPage) -> None:
    assert page.count.value() == 4
    assert page.hint.text() == "拆成 4 个子网"
    page.mode.setCurrentIndex(_MODE_HOSTS)
    assert page.count.value() == 254
    assert "254" in page.hint.text()
    page.mode.setCurrentIndex(0)
    assert page.count.value() == 4
    assert page.hint.text() == "拆成 4 个子网"


def test_host_mode_exceeding_capacity_shows_error(page: SplitPage) -> None:
    page.set_network("10.0.0.0/24")
    page.mode.setCurrentIndex(_MODE_HOSTS)
    page.count.setValue(300)
    assert page.calculate() is None
    assert "超过该网段容量" in page.error.text()
    assert page.model.rowCount() == 0


def test_invalid_network_shows_error(page: SplitPage) -> None:
    page.set_network("10.0.0.0/33")
    assert page.calculate() is None
    assert page.error.text()
    assert not page.export_button.isEnabled()


def test_empty_input_shows_error(page: SplitPage) -> None:
    assert page.calculate() is None
    assert "起始网段" in page.error.text()


def test_too_many_rows_is_refused(page: SplitPage, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(split_page, "MAX_ROWS", 16)
    page.set_network("10.0.0.0/16")
    page.count.setValue(32)
    assert page.calculate() is None
    assert "超过上限 16" in page.error.text()
    assert page.model.rowCount() == 0


def test_summary_reports_totals(page: SplitPage) -> None:
    page.set_network("10.0.0.0/16")
    page.count.setValue(256)
    page.calculate()
    assert page.summary_text() == "拆分为 256 段 /24，每段 254 台可用主机，共 65,536 个地址。"
    assert page.export_button.isEnabled()


def test_clear_resets_everything(page: SplitPage) -> None:
    page.set_network("10.0.0.0/16")
    page.mode.setCurrentIndex(_MODE_HOSTS)
    page.calculate()
    page.clear()
    assert page.network.text() == ""
    assert page.mode.currentIndex() == 0
    assert page.count.value() == 4
    assert page.model.rowCount() == 0
    assert page.summary_text() == "等待输入"
    assert page.result() == []
    assert not page.export_button.isEnabled()


def test_export_csv_content(page: SplitPage, tmp_path) -> None:
    page.set_network("10.0.0.0/24")
    page.count.setValue(2)
    page.calculate()
    target = tmp_path / "subnets.csv"
    assert page.export_csv(target) == 2
    with target.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["序号", "网段", "掩码", "可用地址范围", "广播地址", "可用主机数"]
    assert rows[1] == [
        "1",
        "10.0.0.0/25",
        "255.255.255.128",
        "10.0.0.1 - 10.0.0.126",
        "10.0.0.127",
        "126",
    ]
    assert rows[2][1] == "10.0.0.128/25"
    assert target.read_bytes()[:3] == b"\xef\xbb\xbf"


def test_export_without_result_is_noop(page: SplitPage, tmp_path) -> None:
    target = tmp_path / "empty.csv"
    assert page.export_csv(target) == 0
    assert not target.exists()


def test_model_exposes_no_vertical_headers() -> None:
    model = SubnetTableModel()
    assert model.headerData(0, Qt.Orientation.Vertical) is None
    assert model.columnCount() == 6
    assert model.rowCount() == 0
    assert model.headerData(0, Qt.Orientation.Horizontal) == "序号"
