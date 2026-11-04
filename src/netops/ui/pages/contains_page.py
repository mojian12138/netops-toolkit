"""IP 包含检测页面（calc.contains）。

一个或多个目标网段 + 一批待测 IP / 网段，逐条给出「完全包含 / 包含目标 / 部分重叠 /
互不相交」的结论，并附一张目标 × 待测的包含矩阵。算法全部在 ``core/ipcalc.py``。
"""

from __future__ import annotations

import csv
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
)

from netops.core import ipcalc
from netops.ui.widgets.common import HLine, label
from netops.ui.widgets.forms import (
    FieldRow,
    InlineError,
    PrimaryButton,
    SecondaryButton,
    ToolPage,
    make_text_edit,
    result_card,
)

#: 一次最多检测多少条，防止误粘贴整份地址表把界面卡住。
MAX_SUBJECTS = 2000

_COLUMNS = ["待测对象", "规范化网段", "结论", "命中目标", "未覆盖部分"]


class ContainsPage(ToolPage):
    def __init__(self, parent=None) -> None:
        super().__init__(
            title="IP 包含检测",
            subtitle="判断 IP 或网段是否落在目标网络之内，支持多目标对照与包含矩阵。",
            parent=parent,
        )
        self._targets: list[ipcalc.AddressSubject] = []
        self._subjects: list[ipcalc.AddressSubject] = []
        self._rows: list[tuple[str, str, str, str, str]] = []

        form = result_card("检测设置", "每行一个；裸 IP 按 /32 判断，可用 # 注释")
        self.targets = make_text_edit("目标网段，例如：\n10.10.0.0/16\n192.168.0.0/24", rows=3)
        self.subjects = make_text_edit(
            "待测 IP / 网段，例如：\n10.10.1.1\n10.10.0.0/24\n172.16.0.0/12", rows=6
        )
        self.error = InlineError()

        form.body.addWidget(FieldRow("目标网段", self.targets, label_width=96))
        form.body.addWidget(FieldRow("待测对象", self.subjects, label_width=96))
        form.body.addWidget(self.error)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.check_button = PrimaryButton("检测")
        self.clear_button = SecondaryButton("清空")
        self.check_button.clicked.connect(self.check)
        self.clear_button.clicked.connect(self.clear)
        buttons.addWidget(self.check_button)
        buttons.addWidget(self.clear_button)
        buttons.addStretch(1)
        form.body.addLayout(buttons)
        self.add_card(form)

        result = result_card("检测结果", "✓ 完全包含 · ✗+ 包含目标 · ✗- 部分重叠 · · 不相交")
        self.summary = label("等待输入", "EmptyHint")
        self.summary.setWordWrap(True)
        result.body.addWidget(self.summary)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setObjectName("ResultTable")
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        result.body.addWidget(self.table, 1)
        result.body.addWidget(HLine())

        export_row = QHBoxLayout()
        export_row.setSpacing(8)
        export_row.addWidget(label("导出", "CardHint"))
        export_row.addStretch(1)
        self.export_button = SecondaryButton("导出 CSV")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self._choose_export)
        export_row.addWidget(self.export_button)
        result.body.addLayout(export_row)
        self.add_card(result, 1)

        matrix = result_card("包含矩阵", "行 = 待测对象，列 = 目标网段")
        self.matrix = QTableWidget(0, 0)
        self.matrix.setObjectName("ResultTable")
        self.matrix.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.matrix.setAlternatingRowColors(True)
        self.matrix.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.matrix.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        matrix.body.addWidget(self.matrix, 1)
        self.add_card(matrix, 1)

    # -- 交互 ---------------------------------------------------------------
    def clear(self) -> None:
        self.targets.clear()
        self.subjects.clear()
        self.error.clear_error()
        self._targets = []
        self._subjects = []
        self._rows = []
        self.table.setRowCount(0)
        self.matrix.clear()
        self.matrix.setRowCount(0)
        self.matrix.setColumnCount(0)
        self.summary.setText("等待输入")
        self.export_button.setEnabled(False)

    def check(self) -> list[tuple[str, str, str, str, str]] | None:
        """执行检测；成功返回结果行，失败返回 ``None`` 并展示错误。"""
        self.error.clear_error()
        try:
            targets = ipcalc.parse_subjects(self.targets.toPlainText())
            subjects = ipcalc.parse_subjects(self.subjects.toPlainText())
        except ipcalc.CalcError as exc:
            self.error.show_error(str(exc))
            return None
        if len(subjects) > MAX_SUBJECTS:
            self.error.show_error(
                f"待测对象 {len(subjects):,} 条，超过上限 {MAX_SUBJECTS:,} 条，请分批检测"
            )
            return None

        rows = [self._row_for(targets, subject) for subject in subjects]
        self._targets = targets
        self._subjects = subjects
        self._rows = rows
        self._fill_table(rows)
        self._fill_matrix(targets, subjects)
        self._show_summary(rows)
        self.export_button.setEnabled(bool(rows))
        return rows

    def _row_for(
        self, targets: list[ipcalc.AddressSubject], subject: ipcalc.AddressSubject
    ) -> tuple[str, str, str, str, str]:
        kinds = ipcalc.compare(targets, subject)
        hits = [
            str(target.network)
            for target, kind in zip(targets, kinds, strict=True)
            if kind == "contained"
        ]
        missing = ipcalc.uncovered(targets, subject)
        return (
            subject.text,
            subject.cidr,
            self._conclusion(kinds),
            "、".join(hits) if hits else "—",
            "、".join(str(net) for net in missing) if missing else "—",
        )

    @staticmethod
    def _conclusion(kinds: tuple[str, ...]) -> str:
        if "contained" in kinds:
            return "完全包含"
        if "contains" in kinds:
            return "包含目标"
        if "overlap" in kinds:
            return "部分重叠"
        return "互不相交"

    def _fill_table(self, rows: list[tuple[str, str, str, str, str]]) -> None:
        self.table.setRowCount(len(rows))
        for row, cells in enumerate(rows):
            for column, text in enumerate(cells):
                self.table.setItem(row, column, QTableWidgetItem(text))
        self.table.resizeRowsToContents()

    def _fill_matrix(
        self, targets: list[ipcalc.AddressSubject], subjects: list[ipcalc.AddressSubject]
    ) -> None:
        marks = ipcalc.contains_matrix(targets, subjects)
        self.matrix.clear()
        self.matrix.setRowCount(len(subjects))
        self.matrix.setColumnCount(len(targets))
        self.matrix.setHorizontalHeaderLabels([str(target.network) for target in targets])
        self.matrix.setVerticalHeaderLabels([subject.text for subject in subjects])
        for row, cells in enumerate(marks):
            for column, mark in enumerate(cells):
                item = QTableWidgetItem(mark)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.matrix.setItem(row, column, item)

    def _show_summary(self, rows: list[tuple[str, str, str, str, str]]) -> None:
        counts = {name: 0 for name in ("完全包含", "包含目标", "部分重叠", "互不相交")}
        for row in rows:
            counts[row[2]] = counts.get(row[2], 0) + 1
        parts = [f"检测 {len(rows):,} 条"]
        parts.extend(f"{name} {count:,} 条" for name, count in counts.items() if count)
        self.summary.setText("，".join(parts) + "。")

    def _choose_export(self) -> None:
        if not self._rows:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出检测结果", "contains.csv", "CSV 文件 (*.csv)"
        )
        if path:
            self.export_csv(path)

    def export_csv(self, path: str | Path) -> int:
        """把当前结果写成 CSV（UTF-8 BOM），返回行数。"""
        if not self._rows:
            return 0
        with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(_COLUMNS)
            writer.writerows(self._rows)
        return len(self._rows)

    # -- 对外接口 -----------------------------------------------------------
    def set_targets(self, text: str) -> None:
        self.targets.setPlainText(text)

    def set_subjects(self, text: str) -> None:
        self.subjects.setPlainText(text)

    def result(self) -> list[tuple[str, str, str, str, str]]:
        return list(self._rows)

    def summary_text(self) -> str:
        return self.summary.text()

    def table_value(self, row: int, column: int) -> str:
        item = self.table.item(row, column)
        return item.text() if item is not None else ""

    def matrix_value(self, row: int, column: int) -> str:
        item = self.matrix.item(row, column)
        return item.text() if item is not None else ""
