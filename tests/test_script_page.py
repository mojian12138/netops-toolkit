import pytest
from PyQt6.QtWidgets import QApplication

from netops.ui.pages.script_page import ScriptPage


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["pytest"])


def test_script_page_generates_code(qapp: QApplication):
    page = ScriptPage()
    page.set_template("h3c.opening")
    assert "sysname" in page.generated_code()
    page.deleteLater()


def test_script_page_invalid_value_shows_error(qapp: QApplication):
    page = ScriptPage()
    page.set_template("h3c.opening")
    page._fields["hostname"].setText("")
    page._render()
    assert page.error_text()
    page.deleteLater()
