"""H3C AP 配置生成页面测试（h3c.ap）：单台防抖生成 + 批量校验 + 导出。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

from netops.ui.pages.ap_config_page import ApConfigPage
from netops.ui.theme import apply_theme


@pytest.fixture(scope="module")
def app() -> QApplication:
    application = QApplication.instance() or QApplication(["pytest"])
    apply_theme(application)
    return application


@pytest.fixture()
def page(app) -> ApConfigPage:
    widget = ApConfigPage()
    yield widget
    widget.deleteLater()


# ---------------------------------------------------------------------------
# 初始状态与模式切换
# ---------------------------------------------------------------------------


def test_defaults_are_single_mode(page: ApConfigPage) -> None:
    assert page.mode() == "single"
    assert page.single_card.isHidden() is False
    assert page.batch_card.isHidden() is True
    assert page.generated_code() == ""
    assert page.copy_all_btn.isEnabled() is False


def test_switch_between_single_and_batch(page: ApConfigPage) -> None:
    page.switch_to_batch_btn.click()
    assert page.mode() == "batch"
    assert page.batch_card.isHidden() is False
    assert page.single_card.isHidden() is True

    page.switch_to_single_btn.click()
    assert page.mode() == "single"
    assert page.single_card.isHidden() is False
    assert page.batch_card.isHidden() is True


# ---------------------------------------------------------------------------
# 单台防抖生成
# ---------------------------------------------------------------------------


def test_single_ap_generates_config(page: ApConfigPage) -> None:
    page.set_single(
        name="office-3f-01",
        serial="210235A1B2C3D4E5F6",
        model="WA6320",
        templates="10 11",
        group="office-3f",
    )
    code = page.generated_code()
    assert "wlan ap office-3f-01 model WA6320" in code
    assert "serial-id 210235A1B2C3D4E5F6" in code
    assert "radio 1" in code
    assert "service-template 10" in code
    assert "radio 2" not in code
    assert "service-template 11" in code
    assert "wlan ap-group office-3f" in code
    assert "ap office-3f-01" in code

    assert page.copy_all_btn.isEnabled() is True
    assert page.copy_group_btn.isEnabled() is True
    assert page.export_cfg_btn.isEnabled() is True
    assert "20 位" in page.serial_hint.text()
    assert "共 1 台 AP" in page.summary_text()
    assert "1 个 AP 组" in page.summary_text()


def test_single_ap_advises_unusual_serial_length(page: ApConfigPage) -> None:
    page.set_single(name="office-3f-01", serial="210235A1B2C3D4E5F6", model="WA6320")
    assert page.generated_code()
    assert "20 位" in page.serial_hint.text()

    page.set_single(
        name="office-3f-01",
        serial="210235A1B2C3D4E5F6",
        model="WA6320",
    )
    assert "wlan ap-group" not in page.generated_code()
    assert page.copy_group_btn.isEnabled() is False
    assert page.copy_all_btn.isEnabled() is True


def test_single_ap_invalid_shows_inline_error(page: ApConfigPage) -> None:
    page.set_single(
        name="office-3f-01",
        serial="bad",
        model="WA6320",
    )
    assert page.generated_code() == ""
    assert "序列号不合法" in page.error_text()
    assert page.copy_all_btn.isEnabled() is False


def test_single_ap_missing_field_clears_preview_quietly(page: ApConfigPage) -> None:
    page.set_single(name="office-3f-01", serial="", model="WA6320")
    assert page.generated_code() == ""
    assert page.error_text() == ""


# ---------------------------------------------------------------------------
# CSV 批量导入
# ---------------------------------------------------------------------------


def test_batch_generate_populates_table_and_code(page: ApConfigPage) -> None:
    text = (
        "name,serial,model,service_templates,ap_group\n"
        "a1,210235A1B2C3D4E5F6,WA6320,10,g1\n"
        "a2,210235A1B2C3D4E5F7,WTU430,,\n"
    )
    page.set_csv(text)
    assert page._generate_batch() is True  # noqa: SLF001

    assert page.batch_table.isHidden() is False
    assert page.batch_table.rowCount() == 2
    assert "wlan ap a1 model WA6320" in page.generated_code()
    assert "wlan ap a2 model WTU430" in page.generated_code()
    assert "wlan ap-group g1" in page.generated_code()
    assert "共 2 台 AP" in page.summary_text()


def test_batch_generate_error_hides_table_and_reports_line(page: ApConfigPage) -> None:
    text = "name,serial,model\na1,210235A1B2C3D4E5F6,WA6320\na2,short,WA6320\n"
    page.set_csv(text)
    assert page._generate_batch() is False  # noqa: SLF001

    assert page.batch_table.isHidden() is True
    assert "第 3 行" in page.error_text()
    assert "序列号不合法" in page.error_text()
    assert page.generated_code() == ""


def test_batch_clear_resets_everything(page: ApConfigPage) -> None:
    text = "name,serial,model\na1,210235A1B2C3D4E5F6,WA6320\n"
    page.set_csv(text)
    page._generate_batch()  # noqa: SLF001
    assert page.generated_code() != ""

    page.batch_clear_btn.click()
    assert page.generated_code() == ""
    assert page.csv_input.toPlainText() == ""
    assert page.batch_table.isHidden() is True


# ---------------------------------------------------------------------------
# 复制与文件导出
# ---------------------------------------------------------------------------


def test_copy_all_and_group(page: ApConfigPage) -> None:
    page.set_single(
        name="office-3f-01",
        serial="210235A1B2C3D4E5F6",
        model="WA6320",
        group="office-3f",
    )
    page.copy_all_btn.click()
    assert "wlan ap office-3f-01" in (QApplication.clipboard().text() or "")

    page.copy_group_btn.click()
    clip = QApplication.clipboard().text() or ""
    assert "wlan ap-group office-3f" in clip
    assert "wlan ap office-3f-01 model" not in clip  # 确认只拷了组片段


def test_export_cfg_file(page: ApConfigPage, tmp_path: Path) -> None:
    page.set_single(
        name="office-3f-01",
        serial="210235A1B2C3D4E5F6",
        model="WA6320",
    )
    cfg = tmp_path / "test.cfg"
    cfg.write_text(page.generated_code(), encoding="utf-8")
    assert "wlan ap office-3f-01" in cfg.read_text(encoding="utf-8")
