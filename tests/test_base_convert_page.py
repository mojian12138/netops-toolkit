"""IP 进制转换页面测试（calc.base）。"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication

from netops.ui.pages.base_convert_page import BaseConvertPage
from netops.ui.theme import apply_theme


@pytest.fixture(scope="module")
def app() -> QApplication:
    application = QApplication.instance() or QApplication(["pytest"])
    apply_theme(application)
    return application


@pytest.fixture()
def page(app) -> BaseConvertPage:
    widget = BaseConvertPage()
    yield widget
    widget.deleteLater()


def test_dotted_input_syncs_all_columns(page: BaseConvertPage) -> None:
    page.set_input("dotted", "192.168.1.1")
    assert page.input_text("binary") == "11000000.10101000.00000001.00000001"
    assert page.input_text("hex") == "0xC0A80101"
    assert page.input_text("integer") == "3232235777"
    assert page.results.value("二进制（连写）") == "11000000101010000000000100000001"
    assert page.results.value("大端字节序") == "1.1.168.192"
    assert "私有" in page.summary.text()


def test_hex_input_syncs(page: BaseConvertPage) -> None:
    page.set_input("hex", "0xc0a8.0101")
    assert page.input_text("dotted") == "192.168.1.1"
    assert page.input_text("integer") == "3232235777"


def test_integer_input_syncs(page: BaseConvertPage) -> None:
    page.set_input("integer", "0")
    assert page.input_text("dotted") == "0.0.0.0"
    assert page.input_text("hex") == "0x00000000"
    assert page.input_text("binary") == "00000000.00000000.00000000.00000000"
    assert page.results.value("地址类别") in ("保留地址", "私有地址（RFC 1918）")


def test_binary_input_syncs(page: BaseConvertPage) -> None:
    page.set_input("binary", "10101010.00001111.11110000.00000001")
    assert page.input_text("dotted") == "170.15.240.1"
    assert page.input_text("integer") == "2853171201"


def test_binary_short_segments_are_padded(page: BaseConvertPage) -> None:
    page.set_input("binary", "00001010.00000000.00000000.00000001")
    assert page.input_text("dotted") == "10.0.0.1"


def test_live_typing_keeps_cursor_column(page: BaseConvertPage) -> None:
    page.set_input("dotted", "192.168.001.001")
    page.set_input("dotted", "192.168.001.010")
    assert page.input_text("dotted") == "192.168.001.010"
    assert page.input_text("hex") == "0xC0A8010A"


def test_invalid_input_shows_error_and_clears_others(page: BaseConvertPage) -> None:
    page.set_input("dotted", "192.168.1.1")
    page.set_input("dotted", "192.168.1.256")
    assert page.error.text()
    assert page.input_text("dotted") == "192.168.1.256"
    assert page.input_text("hex") == ""
    assert page.results.value("点分十进制") == "—"
    assert page.summary.text() == "等待输入"


def test_overflow_integer_rejected(page: BaseConvertPage) -> None:
    page.set_input("integer", "4294967296")
    assert "32 位" in page.error.text()


def test_editing_error_then_fixing_recovers(page: BaseConvertPage) -> None:
    page.set_input("hex", "zz")
    assert page.error.text()
    page.set_input("hex", "FF")
    assert not page.error.isVisible() or page.error.text() == ""
    assert page.input_text("dotted") == "0.0.0.255"


def test_empty_input_resets_outputs(page: BaseConvertPage) -> None:
    page.set_input("dotted", "10.0.0.1")
    page.set_input("dotted", "")
    assert page.input_text("binary") == ""
    assert page.results.value("十进制整数") == "—"


def test_clear_resets_everything(page: BaseConvertPage) -> None:
    page.set_input("dotted", "10.0.0.1")
    page.clear()
    for key in ("dotted", "binary", "hex", "integer"):
        assert page.input_text(key) == ""
    assert page.summary.text() == "等待输入"
    assert page.forms() is None


def test_no_feedback_loop_on_typing(page: BaseConvertPage) -> None:
    """setText 不应再次触发联动（只有用户输入 textEdited 才联动）。"""
    page.set_input("dotted", "172.16.0.1")
    assert page.input_text("integer") == "2886729729"
    page.set_input("integer", "2886729729")
    assert page.input_text("dotted") == "172.16.0.1"
