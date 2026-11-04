"""IP 包含检测页面测试（calc.contains）。"""

from __future__ import annotations

import csv

import pytest
from PyQt6.QtWidgets import QApplication

from netops.ui.pages import contains_page
from netops.ui.pages.contains_page import ContainsPage
from netops.ui.theme import apply_theme


@pytest.fixture(scope="module")
def app() -> QApplication:
    application = QApplication.instance() or QApplication(["pytest"])
    apply_theme(application)
    return application


@pytest.fixture()
def page(app) -> ContainsPage:
    widget = ContainsPage()
    yield widget
    widget.deleteLater()


def _check(page: ContainsPage, targets: str, subjects: str):
    page.set_targets(targets)
    page.set_subjects(subjects)
    return page.check()


def test_host_inside_single_target(page: ContainsPage) -> None:
    rows = _check(page, "10.10.0.0/16", "10.10.1.1")
    assert rows is not None
    assert page.table_value(0, 0) == "10.10.1.1"
    assert page.table_value(0, 1) == "10.10.1.1/32"
    assert page.table_value(0, 2) == "完全包含"
    assert page.table_value(0, 3) == "10.10.0.0/16"
    assert page.table_value(0, 4) == "—"


def test_subject_containing_target(page: ContainsPage) -> None:
    _check(page, "10.0.0.0/16", "10.0.0.0/8")
    assert page.table_value(0, 2) == "包含目标"
    assert page.table_value(0, 3) == "—"
    assert page.table_value(0, 4).startswith("10.1.0.0/16")


def test_disjoint_subject_reports_full_uncovered(page: ContainsPage) -> None:
    _check(page, "10.0.0.0/8", "192.168.0.0/24")
    assert page.table_value(0, 2) == "互不相交"
    assert page.table_value(0, 4) == "192.168.0.0/24"


def test_partial_overlap_is_detected(page: ContainsPage) -> None:
    page.set_targets("10.0.0.0/24")
    page.set_subjects("10.0.0.0/25")
    rows = page.check()
    assert rows is not None
    # /25 完整落在 /24 内，因此是「完全包含」而不是重叠
    assert rows[0][2] == "完全包含"
    page.set_targets("10.0.0.0/25")
    page.set_subjects("10.0.0.0/24")
    rows = page.check()
    assert rows is not None
    assert rows[0][2] == "包含目标"


def test_uncovered_with_multiple_targets_union(page: ContainsPage) -> None:
    rows = _check(page, "10.0.0.0/25\n10.0.0.128/25", "10.0.0.0/24")
    assert rows is not None
    assert rows[0][2] == "包含目标"
    assert rows[0][3] == "—"
    assert rows[0][4] == "—"


def test_matrix_columns_follow_targets(page: ContainsPage) -> None:
    _check(page, "10.0.0.0/16\n192.168.0.0/24", "10.0.1.1\n192.168.0.0/16")
    assert page.matrix.rowCount() == 2
    assert page.matrix.columnCount() == 2
    assert page.matrix_value(0, 0) == "✓"
    assert page.matrix_value(0, 1) == "·"
    assert page.matrix_value(1, 0) == "·"
    assert page.matrix_value(1, 1) == "✗+"


def test_summary_counts_each_conclusion(page: ContainsPage) -> None:
    _check(page, "10.0.0.0/16", "10.0.1.1\n10.0.2.1\n172.16.0.1")
    assert page.summary_text() == "检测 3 条，完全包含 2 条，互不相交 1 条。"


def test_empty_targets_shows_error(page: ContainsPage) -> None:
    assert _check(page, "", "10.0.0.1") is None
    assert "至少输入一个" in page.error.text()
    assert not page.export_button.isEnabled()


def test_empty_subjects_shows_error(page: ContainsPage) -> None:
    assert _check(page, "10.0.0.0/16", "\n# 注释\n") is None
    assert page.error.text()


def test_invalid_subject_shows_error(page: ContainsPage) -> None:
    assert _check(page, "10.0.0.0/16", "10.0.0.0/33") is None
    assert "非法网段" in page.error.text()
    assert page.table.rowCount() == 0


def test_too_many_subjects_is_refused(page: ContainsPage, monkeypatch) -> None:
    monkeypatch.setattr(contains_page, "MAX_SUBJECTS", 2)
    assert _check(page, "10.0.0.0/16", "10.0.0.1\n10.0.0.2\n10.0.0.3") is None
    assert "超过上限 2" in page.error.text()


def test_clear_resets_everything(page: ContainsPage) -> None:
    _check(page, "10.0.0.0/16", "10.0.1.1")
    page.clear()
    assert page.targets.toPlainText() == ""
    assert page.subjects.toPlainText() == ""
    assert page.result() == []
    assert page.table.rowCount() == 0
    assert page.matrix.columnCount() == 0
    assert page.summary_text() == "等待输入"
    assert not page.export_button.isEnabled()


def test_export_csv(page: ContainsPage, tmp_path) -> None:
    _check(page, "10.0.0.0/16", "10.0.1.1\n172.16.0.1")
    target = tmp_path / "contains.csv"
    assert page.export_csv(target) == 2
    with target.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["待测对象", "规范化网段", "结论", "命中目标", "未覆盖部分"]
    assert rows[1] == ["10.0.1.1", "10.0.1.1/32", "完全包含", "10.0.0.0/16", "—"]
    assert rows[2][2] == "互不相交"
    assert target.read_bytes()[:3] == b"\xef\xbb\xbf"


def test_export_without_result_is_noop(page: ContainsPage, tmp_path) -> None:
    target = tmp_path / "empty.csv"
    assert page.export_csv(target) == 0
    assert not target.exists()
