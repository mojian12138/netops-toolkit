"""MAC 地址格式转换页面测试（conv.mac）。"""

from __future__ import annotations

import csv

import pytest
from PyQt6.QtWidgets import QApplication

from netops.ui.pages import mac_page
from netops.ui.pages.mac_page import MacPage, clipboard_text
from netops.ui.theme import apply_theme


@pytest.fixture(scope="module")
def app() -> QApplication:
    application = QApplication.instance() or QApplication(["pytest"])
    apply_theme(application)
    return application


@pytest.fixture()
def page(app) -> MacPage:
    widget = MacPage()
    yield widget
    widget.deleteLater()


def _fill_batch(page: MacPage, text: str):
    page.batch_input.setPlainText(text)
    return page.convert_batch()


def test_single_input_fills_all_forms(page: MacPage) -> None:
    forms = page.set_input("0011.6c0a.1b2c")
    assert forms is not None
    assert page.result_value("思科 / H3C 接口") == "0011.6c0a.1b2c"
    assert page.result_value("华为 / H3C display") == "0011-6c0a-1b2c"
    assert page.result_value("Windows ipconfig") == "00-11-6c-0a-1b-2c"
    assert page.result_value("getmac / 注册表") == "0011-6C0A-1B2C"
    assert page.result_value("Linux / Wireshark") == "00:11:6c:0a:1b:2c"
    assert page.result_value("华为部分输出") == "00:11:6C:0A:1B:2C"
    assert page.result_value("空格分隔") == "00 11 6c 0a 1b 2c"
    assert page.result_value("无分隔") == "00116c0a1b2c"
    assert page.result_value("十六进制") == "0x00116c0a1b2c"
    assert page.input_form("plain") == "00116c0a1b2c"
    assert page.error_text() == ""


def test_vendor_and_notes(page: MacPage) -> None:
    page.set_input("0011.6C0A.1B2C")
    assert page.vendor_text() == "00116c  H3C"
    assert page.notes_text() == "OUI 00116c → H3C。"


def test_local_admin_note(page: MacPage) -> None:
    page.set_input("02:00:4c:12:34:56")
    # 02:00:4c 是 VirtualBox 的本地管理前缀：厂商能查到，但仍要提示本地管理属性
    assert "VirtualBox" in page.vendor_text()
    assert "本地管理地址" in page.notes_text()


def test_unknown_oui_is_reported_honestly(page: MacPage) -> None:
    page.set_input("00:ab:cd:0d:0e:0f")
    assert page.vendor_text() == "00abcd  本地库未收录"
    assert "本地库未收录" in page.notes_text()


def test_invalid_input_shows_error_and_clears_results(page: MacPage) -> None:
    page.set_input("0011.6c0a.1b2c")
    assert page.set_input("0011.6c0a.1b") is None
    assert "点分 MAC" in page.error_text()
    assert page.result_value("无分隔") == "—"
    assert page.forms() is None


def test_empty_input_resets(page: MacPage) -> None:
    page.set_input("0011.6c0a.1b2c")
    assert page.set_input("   ") is None
    assert page.result_value("无分隔") == "—"
    assert page.error_text() == ""


def test_copy_cisco_button_writes_clipboard(page: MacPage) -> None:
    page.set_input("00-11-6C-0A-1B-2C")
    page.copy_cisco.click()
    assert clipboard_text() == "0011.6c0a.1b2c"


def test_debounce_timer_converts_typed_text(page: MacPage) -> None:
    page.input.setText("00116c0a1b2c")
    page.input.textEdited.emit("00116c0a1b2c")
    assert page._timer.isActive()
    page._on_timeout()
    assert page.result_value("思科 / H3C 接口") == "0011.6c0a.1b2c"


def test_clear_resets_single(page: MacPage) -> None:
    page.set_input("0011.6c0a.1b2c")
    page.clear()
    assert page.input.text() == ""
    assert page.forms() is None
    assert page.notes_text() == "等待输入"
    assert page.vendor_text() == "—"


def test_batch_converts_and_marks_invalid_rows(page: MacPage) -> None:
    rows = _fill_batch(
        page,
        "\n# 交换机下联口\n0011.6c0a.1b2c\n00-11-6C-0A-1B-2C, 0200.4c12.3456\n\nzz:zz:zz:zz:zz:zz\n",
    )
    assert rows is not None
    assert [row[1] for row in rows] == ["00116c0a1b2c", "00116c0a1b2c", "02004c123456", "—"]
    assert [row[2] for row in rows][:3] == [
        "思科 / H3C 接口",
        "Windows",
        "思科 / H3C 接口",
    ]
    assert rows[1][4] == "H3C"
    assert rows[2][4] == "本地管理（厂商自定义）"
    assert rows[3][2] == "非法"
    assert page.table.rowCount() == 4
    assert page.batch_summary_text() == "共 4 行，转换成功 3 行，非法 1 行（已标红）。"
    assert page.export_button.isEnabled()
    assert page.copy_column_button.isEnabled()


def test_batch_empty_input_shows_error(page: MacPage) -> None:
    assert _fill_batch(page, "\n  \n# 只有注释\n") is None
    assert "至少输入一个" in page.batch_error.text()
    assert not page.export_button.isEnabled()


def test_batch_too_many_lines_is_refused(page: MacPage, monkeypatch) -> None:
    monkeypatch.setattr(mac_page, "MAX_BATCH", 2)
    assert _fill_batch(page, "0011.6c0a.1b2c\n0011.6c0a.1b2d\n0011.6c0a.1b2e") is None
    assert "超过单次上限 2" in page.batch_error.text()


def test_batch_copy_plain_column_skips_invalid(page: MacPage) -> None:
    _fill_batch(page, "0011.6c0a.1b2c\nbad-mac\n00:11:6c:0a:1b:2d")
    page._copy_plain_column()
    assert clipboard_text() == "00116c0a1b2c\n00116c0a1b2d"


def test_batch_editing_after_convert_hints_reconvert(page: MacPage) -> None:
    _fill_batch(page, "0011.6c0a.1b2c")
    page.batch_input.setPlainText("0011.6c0a.1b2d")
    assert "重新计算" in page.batch_summary_text()
    # 旧结果保留，导出仍可用
    assert len(page.batch_rows()) == 1


def test_batch_clear(page: MacPage) -> None:
    _fill_batch(page, "0011.6c0a.1b2c")
    page.clear_batch()
    assert page.batch_input.toPlainText() == ""
    assert page.batch_rows() == []
    assert page.table.rowCount() == 0
    assert page.batch_summary_text() == "等待输入"
    assert not page.export_button.isEnabled()


def test_batch_export_csv(page: MacPage, tmp_path) -> None:
    _fill_batch(page, "0011.6c0a.1b2c\n0011.6c0a.1b2d\nnope")
    target = tmp_path / "mac.csv"
    assert page.export_csv(target) == 3
    with target.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["原文", "规范化", "格式", "OUI", "厂商 / 说明"]
    assert rows[1] == ["0011.6c0a.1b2c", "00116c0a1b2c", "思科 / H3C 接口", "00116c", "H3C"]
    assert rows[3][2] == "非法"
    assert target.read_bytes()[:3] == b"\xef\xbb\xbf"


def test_export_without_result_is_noop(page: MacPage, tmp_path) -> None:
    target = tmp_path / "empty.csv"
    assert page.export_csv(target) == 0
    assert not target.exists()
