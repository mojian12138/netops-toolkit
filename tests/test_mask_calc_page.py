"""子网掩码计算器页面测试（calc.mask）。"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication

from netops.ui.pages.mask_calc_page import MaskCalcPage
from netops.ui.theme import apply_theme
from netops.ui.widgets.bits import MaskBitmap


@pytest.fixture(scope="module")
def app() -> QApplication:
    application = QApplication.instance() or QApplication(["pytest"])
    apply_theme(application)
    return application


@pytest.fixture()
def page(app) -> MaskCalcPage:
    widget = MaskCalcPage()
    yield widget
    widget.deleteLater()


def test_mask_mode_sets_results(page: MaskCalcPage) -> None:
    page.mask.setText("255.255.255.192")
    assert page.calculate() == 26
    assert page.results.value("前缀长度") == "/26"
    assert page.results.value("子网掩码") == "255.255.255.192"
    assert page.results.value("通配符掩码") == "0.0.0.63"
    assert page.results.value("十六进制") == "0xFFFFFFC0"
    assert page.results.value("每子网可用主机") == "62"
    assert page.bitmap.prefix == 26


def test_slash_notation_accepted(page: MaskCalcPage) -> None:
    page.mask.setText("/24")
    assert page.calculate() == 24
    assert page.results.value("子网掩码") == "255.255.255.0"


def test_hosts_mode_rounds_up(page: MaskCalcPage) -> None:
    page.mode.setCurrentIndex(1)
    page.hosts.setValue(50)
    assert page.calculate() == 26
    assert page.results.value("每子网可用主机") == "62"
    assert page.results.value("基准网段内子网数") == "—"


def test_hosts_mode_with_base_network(page: MaskCalcPage) -> None:
    page.mode.setCurrentIndex(1)
    page.hosts.setValue(50)
    page.base.setText("10.0.0.0/24")
    assert page.calculate() == 26
    assert page.results.value("基准网段内子网数") == "4"
    assert "10.0.0.0/24" in page.summary.text()


def test_subnets_mode(page: MaskCalcPage) -> None:
    page.mode.setCurrentIndex(2)
    page.base.setText("172.16.0.0/16")
    page.subnets.setValue(8)
    assert page.calculate() == 19
    assert page.results.value("基准网段内子网数") == "8"


def test_subnets_mode_requires_base(page: MaskCalcPage) -> None:
    page.mode.setCurrentIndex(2)
    assert page.calculate() is None
    assert "基准网段" in page.error.text()


def test_subnet_capacity_overflow(page: MaskCalcPage) -> None:
    page.mode.setCurrentIndex(2)
    page.base.setText("10.0.0.0/30")
    page.subnets.setValue(8)
    assert page.calculate() is None
    assert "容量" in page.error.text() or "最多" in page.error.text()


def test_empty_mask_shows_error(page: MaskCalcPage) -> None:
    assert page.calculate() is None
    assert "掩码" in page.error.text()


def test_invalid_mask_shows_error(page: MaskCalcPage) -> None:
    page.mask.setText("255.0.255.0")
    assert page.calculate() is None
    assert page.error.text()


def test_mode_toggles_inputs(page: MaskCalcPage) -> None:
    assert page.mask.isEnabled()
    assert not page.hosts.isEnabled()
    page.mode.setCurrentIndex(1)
    assert page.hosts.isEnabled()
    assert not page.mask.isEnabled()
    page.mode.setCurrentIndex(2)
    assert page.subnets.isEnabled()
    assert page.base.isEnabled()


def test_clear_resets(page: MaskCalcPage) -> None:
    page.mask.setText("/20")
    page.calculate()
    page.clear()
    assert page.results.value("前缀长度") == "—"
    assert page.summary.text() == "等待输入"
    assert page.bitmap.prefix == 24


def test_bitmap_tracks_prefix(app) -> None:
    bitmap = MaskBitmap(prefix=8)
    try:
        bitmap.set_prefix(30)
        assert bitmap.prefix == 30
        bitmap.set_prefix(99)
        assert bitmap.prefix == 32
        bitmap.set_prefix(-3)
        assert bitmap.prefix == 0
    finally:
        bitmap.deleteLater()
