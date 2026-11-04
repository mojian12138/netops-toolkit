"""路由聚合计算器页面测试（calc.aggregate）。"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication

from netops.ui.pages.aggregate_page import AggregatePage
from netops.ui.theme import apply_theme


@pytest.fixture(scope="module")
def app() -> QApplication:
    application = QApplication.instance() or QApplication(["pytest"])
    apply_theme(application)
    return application


@pytest.fixture()
def page(app) -> AggregatePage:
    widget = AggregatePage()
    yield widget
    widget.deleteLater()


def _paste(page: AggregatePage, text: str) -> None:
    page.set_networks(text)


def test_exact_aggregation_merges_neighbours(page: AggregatePage) -> None:
    _paste(page, "10.0.0.0/24\n10.0.1.0/24\n")
    result = page.calculate()
    assert result is not None
    assert result.blocks == ("10.0.0.0/23",)
    assert page.table.rowCount() == 1
    assert page.table_value(0, 0) == "10.0.0.0/23"
    assert result.wasted_addresses == 0
    assert "聚合为 1 条" in page.summary_text()


def test_contained_and_duplicates_are_dropped(page: AggregatePage) -> None:
    _paste(page, "10.0.0.0/16\n10.0.1.0/24\n10.0.1.0/24\n")
    result = page.calculate()
    assert result is not None
    assert result.blocks == ("10.0.0.0/16",)
    assert result.duplicates == 1
    assert result.contained == ("10.0.1.0/24",)
    assert "重复 1 条" in page.summary_text()
    assert "剔除被包含 1 条" in page.summary_text()


def test_sources_are_traced_per_block(page: AggregatePage) -> None:
    _paste(page, "10.0.0.0/24\n10.0.1.0/24\n192.168.1.0/25\n192.168.1.0/24")
    result = page.calculate()
    assert result is not None
    assert page.table.rowCount() == 2
    assert page.table_value(0, 0) == "10.0.0.0/23"
    assert page.table_value(0, 1) == "10.0.0.0/24、10.0.1.0/24"
    assert page.table_value(1, 0) == "192.168.1.0/24"


def test_space_and_comma_separators(page: AggregatePage) -> None:
    _paste(page, "10.0.0.0/24 10.0.1.0/24\n192.168.1.0/24, 192.168.2.0/24")
    result = page.calculate()
    assert result is not None
    assert result.blocks == ("10.0.0.0/23", "192.168.1.0/24", "192.168.2.0/24")


def test_task_given_mask_notation(page: AggregatePage) -> None:
    _paste(page, "172.16.0.0 255.255.255.0\n172.16.1.0/24")
    result = page.calculate()
    assert result is not None
    assert result.blocks == ("172.16.0.0/23",)


def test_optimize_mode_merges_with_waste(page: AggregatePage) -> None:
    _paste(page, "10.0.0.0/24\n10.0.2.0/24")
    page.mode.setCurrentIndex(1)
    page.waste.setValue(0.5)
    result = page.calculate()
    assert result is not None
    assert result.blocks == ("10.0.0.0/22",)
    assert result.wasted_addresses == 512
    assert page.table_value(0, 2) == "512"
    assert "冗余 512 个地址" in page.summary_text()


def test_optimize_zero_ratio_keeps_blocks(page: AggregatePage) -> None:
    _paste(page, "10.0.0.0/24\n10.0.2.0/24")
    page.mode.setCurrentIndex(1)
    page.waste.setValue(0.0)
    result = page.calculate()
    assert result is not None
    assert result.blocks == ("10.0.0.0/24", "10.0.2.0/24")


def test_waste_spinbox_follows_mode(page: AggregatePage) -> None:
    assert not page.waste.isEnabled()
    page.mode.setCurrentIndex(1)
    assert page.waste.isEnabled()
    page.mode.setCurrentIndex(0)
    assert not page.waste.isEnabled()


def test_config_uses_h3c_style_by_default(page: AggregatePage) -> None:
    _paste(page, "10.0.0.0/24\n10.0.1.0/24")
    page.calculate()
    page.set_hop("10.0.0.1")
    assert page.config_text() == "ip route-static 10.0.0.0 23 10.0.0.1"
    assert page.copy_button.isEnabled()


def test_config_switches_to_cisco(page: AggregatePage) -> None:
    _paste(page, "10.0.0.0/24\n10.0.1.0/24")
    page.calculate()
    page.set_hop("10.0.0.1")
    page.style.setCurrentIndex(1)
    assert page.config_text() == "ip route 10.0.0.0 23 10.0.0.1"


def test_config_waits_for_hop(page: AggregatePage) -> None:
    _paste(page, "10.0.0.0/24")
    page.calculate()
    assert "下一跳" in page.config_text()
    assert not page.copy_button.isEnabled()


def test_invalid_hop_is_reported(page: AggregatePage) -> None:
    _paste(page, "10.0.0.0/24")
    page.calculate()
    page.set_hop("not-an-ip")
    assert "非法下一跳" in page.config_text()
    assert not page.copy_button.isEnabled()


def test_invalid_network_shows_error(page: AggregatePage) -> None:
    _paste(page, "10.0.0.0/24\nnot-a-network")
    assert page.calculate() is None
    assert "非法网段" in page.error.text()


def test_range_notation_is_rejected_with_hint(page: AggregatePage) -> None:
    _paste(page, "10.0.0.0-10.0.0.100")
    assert page.calculate() is None
    assert "CIDR" in page.error.text()


def test_empty_input_shows_error(page: AggregatePage) -> None:
    assert page.calculate() is None
    assert "网段" in page.error.text()


def test_comment_only_input_shows_error(page: AggregatePage) -> None:
    _paste(page, "# 10.0.0.0/24\n\n")
    assert page.calculate() is None
    assert page.error.text()


def test_clear_resets_everything(page: AggregatePage) -> None:
    _paste(page, "10.0.0.0/24\n10.0.1.0/24")
    page.calculate()
    page.set_hop("10.0.0.1")
    page.clear()
    assert page.networks.toPlainText() == ""
    assert page.table.rowCount() == 0
    assert page.summary_text() == "等待输入"
    assert page.config_text() == ""
    assert page.result() is None
    assert not page.copy_button.isEnabled()


def test_copy_puts_config_on_clipboard(page: AggregatePage) -> None:
    _paste(page, "10.0.0.0/24\n10.0.1.0/24")
    page.calculate()
    page.set_hop("10.0.0.1")
    page.copy_config()
    assert QApplication.clipboard().text() == "ip route-static 10.0.0.0 23 10.0.0.1"
