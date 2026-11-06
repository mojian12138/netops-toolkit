import pytest
from PyQt6.QtWidgets import QApplication

from netops.ui.pages.script_page import ScriptPage


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["pytest"])


def test_script_page_v7_only_has_human_friendly_controls(qapp: QApplication):
    from PyQt6.QtWidgets import QCheckBox, QComboBox, QSpinBox

    page = ScriptPage()
    assert not hasattr(page, "vendor_combo")
    assert not hasattr(page, "category_combo")
    assert isinstance(page._fields["password_policy"], QCheckBox)
    assert isinstance(page._fields["service_types"].findChild(QCheckBox), QCheckBox)
    assert isinstance(page._fields["mgmt_vlan"], QSpinBox)
    assert isinstance(page._fields["mgmt_vlan_desc"], QComboBox)
    assert len(page._sections) == 7
    page.deleteLater()


def test_invalid_ip_clears_preview(qapp: QApplication):
    page = ScriptPage()
    page._fields["mgmt_ip"].setText("999.10.50.64/24")
    page._render()
    assert page.error_text()
    assert page.generated_code() == ""
    page.deleteLater()


def test_preset_save_load_excludes_password(qapp: QApplication, tmp_path):
    from netops.core.db import Database

    db = Database(tmp_path / "presets.sqlite3")
    db.connect()
    page = ScriptPage(db=db)
    page._fields["hostname"].setText("EDGE-01")
    page._fields["password"].setText("NeverPersistMe!")
    db.add_preset(
        "Campus",
        page._current.template_id,
        page._values(),
        page.service.sensitive_fields(page._current.template_id),
    )
    saved = db.list_presets()[0]
    assert saved.values["hostname"] == "EDGE-01"
    assert "password" not in saved.values
    assert (
        "NeverPersistMe!"
        not in db.conn.execute("SELECT values_json FROM script_presets").fetchone()[0]
    )
    page.deleteLater()
    db.close()


def test_generator_defaults_reset_and_visible_password(qapp: QApplication):
    from PyQt6.QtWidgets import QLineEdit

    page = ScriptPage()
    assert page._fields["mgmt_vlan"].value() == 1
    assert page._fields["mgmt_ip"].text() == "1.1.1.1/24"
    assert page._fields["gateway"].text() == "1.1.1.254"
    assert page._fields["password"].echoMode() == QLineEdit.EchoMode.Normal
    assert page._fields["password"].text() == "h3cadmin"
    page._fields["hostname"].setText("TEMP")
    page._reset_defaults()
    assert page._fields["hostname"].text() == "H3Csw01"
    assert not hasattr(page, "redact_copy_btn")
    page.deleteLater()


def test_comment_toggle_changes_output(qapp: QApplication):
    page = ScriptPage()
    assert "# 管理网络" in page.generated_code()
    page._fields["show_comments"].setChecked(False)
    page._render()
    assert "# 管理网络" not in page.generated_code()
    page.deleteLater()


def test_script_page_generates_code(qapp: QApplication):
    page = ScriptPage()
    assert "sysname" in page.generated_code()
    page.deleteLater()


def test_script_page_invalid_value_shows_error(qapp: QApplication):
    page = ScriptPage()
    page._fields["hostname"].setText("")
    page._render()
    assert page.error_text()
    page.deleteLater()
