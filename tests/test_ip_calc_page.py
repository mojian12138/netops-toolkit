"""真实工具页测试：先覆盖 IP 地址计算器。"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication

from netops.core import ipcalc
from netops.ui.pages.ip_calc_page import IpCalcPage
from netops.ui.theme import apply_theme


@pytest.fixture(scope="module")
def app() -> QApplication:
    application = QApplication.instance() or QApplication(["pytest"])
    apply_theme(application)
    return application


@pytest.fixture()
def page(app) -> IpCalcPage:
    widget = IpCalcPage()
    yield widget
    widget.deleteLater()


def test_cidr_calculation(page: IpCalcPage) -> None:
    page.address.setText("192.168.1.77/24")
    info = page.calculate()
    assert info is not None
    assert page.results.value("网络地址") == "192.168.1.0"
    assert page.results.value("广播地址") == "192.168.1.255"
    assert page.results.value("可用范围") == "192.168.1.1 - 192.168.1.254"
    assert page.results.value("子网掩码") == "255.255.255.0"
    assert page.results.value("通配符掩码") == "0.0.0.255"
    assert page.results.value("可用主机数") == "254"


def test_dotted_mask_mode(page: IpCalcPage) -> None:
    page.address.setText("10.1.2.3")
    page.mask_mode.setCurrentIndex(1)
    page.mask.setText("255.255.0.0")
    assert page.calculate() is not None
    assert page.results.value("网络地址") == "10.1.0.0"
    assert page.results.value("可用主机数") == "65,534"


def test_mask_mode_toggles_input(page: IpCalcPage) -> None:
    assert not page.mask.isEnabled()
    page.mask_mode.setCurrentIndex(1)
    assert page.mask.isEnabled()
    page.mask_mode.setCurrentIndex(0)
    assert not page.mask.isEnabled()


def test_invalid_input_shows_error(page: IpCalcPage) -> None:
    page.address.setText("999.1.1.1/24")
    assert page.calculate() is None
    assert page.error.isVisible() or page.error.text()


def test_empty_input_shows_error(page: IpCalcPage) -> None:
    assert page.calculate() is None
    assert "请输入" in page.error.text()


def test_mask_mode_requires_mask(page: IpCalcPage) -> None:
    page.address.setText("10.0.0.1")
    page.mask_mode.setCurrentIndex(1)
    assert page.calculate() is None
    assert "掩码" in page.error.text()


def test_clear_resets_page(page: IpCalcPage) -> None:
    page.address.setText("192.168.1.1/24")
    page.calculate()
    page.clear()
    assert page.address.text() == ""
    assert page.results.value("网络地址") == "—"
    assert page.summary.text() == "等待输入"


def test_slash_31(page: IpCalcPage) -> None:
    page.address.setText("10.0.0.0/31")
    page.calculate()
    assert page.results.value("可用主机数") == "2"


def test_kind_label(page: IpCalcPage) -> None:
    page.address.setText("8.8.8.8/24")
    page.calculate()
    assert page.results.value("地址类别") == "公网地址"


def test_page_uses_ipcalc_core(page: IpCalcPage) -> None:
    page.address.setText("172.16.5.9/20")
    info = page.calculate()
    assert isinstance(info, ipcalc.NetworkInfo)
    assert info.cidr == "172.16.0.0/20"
