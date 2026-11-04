"""超网拆分计算器页面（calc.split）。

两种拆分方式：指定子网数量、指定每段可用主机数；结果用 ``QAbstractTableModel``
承载，万级子网也只绘制可见行。算法全部在 ``core/ipcalc.py``，本页只做取值与展示。
"""

from __future__ import annotations

import csv
from pathlib import Path

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QSpinBox,
    QTableView,
)

from netops.core import ipcalc
from netops.ui.widgets.common import HLine, label
from netops.ui.widgets.forms import (
    FieldRow,
    InlineError,
    PrimaryButton,
    SecondaryButton,
    ToolPage,
    make_combo,
    make_line_edit,
    result_card,
)

_MODE_COUNT = 0
_MODE_HOSTS = 1

#: 一次最多展示 / 导出多少段，防止 /8 这类输入把内存吃满（65536 段 = /16 拆到 /32）。
MAX_ROWS = 65536

_COLUMNS = ["序号", "网段", "掩码", "可用地址范围", "广播地址", "可用主机数"]

#: Qt 在调用 rowCount / columnCount 时会传入无效索引，这里复用一个常量避免默认参数里构造。
_ROOT = QModelIndex()


class SubnetTableModel(QAbstractTableModel):
    """把 ``NetworkInfo`` 列表虚拟化成表格，行数再大也只绘制可见部分。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[ipcalc.NetworkInfo] = []

    def set_rows(self, rows: list[ipcalc.NetworkInfo]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> list[ipcalc.NetworkInfo]:
        return self._rows

    def rowCount(self, parent: QModelIndex = _ROOT) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = _ROOT) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(_COLUMNS)

    def headerData(self, section: int, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return _COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        row = self._rows[index.row()]
        cells = (
            str(index.row() + 1),
            row.cidr,
            row.mask,
            row.host_range,
            row.broadcast,
            f"{row.usable_hosts:,}",
        )
        return cells[index.column()]


class SplitPage(ToolPage):
    def __init__(self, parent=None) -> None:
        super().__init__(
            title="超网拆分计算器",
            subtitle="把大网段按子网数量或每段主机数等分，列出每一段的可用范围，可导出 CSV。",
            parent=parent,
        )
        self._infos: list[ipcalc.NetworkInfo] = []

        form = result_card("拆分设置", "数量不是 2 的幂时自动向上取整")
        self.network = make_line_edit("例如 10.0.0.0/16 或 10.0.0.0 255.255.0.0")
        self.mode = make_combo(["按子网数量拆分", "按每段主机数拆分"])
        self.count = QSpinBox()
        self.count.setObjectName("ToolInput")
        self.count.setRange(1, MAX_ROWS)
        self.count.setValue(4)
        self.count.setSingleStep(1)
        self.hint = label("拆成 4 个子网", "FieldHint")
        self.error = InlineError()

        form.body.addWidget(FieldRow("起始网段", self.network))
        form.body.addWidget(FieldRow("拆分方式", self.mode))
        form.body.addWidget(FieldRow("目标数量", self.count, label_width=96))
        form.body.addWidget(self.hint)
        form.body.addWidget(self.error)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.calc_button = PrimaryButton("拆分")
        self.clear_button = SecondaryButton("清空")
        self.calc_button.clicked.connect(self.calculate)
        self.clear_button.clicked.connect(self.clear)
        buttons.addWidget(self.calc_button)
        buttons.addWidget(self.clear_button)
        buttons.addStretch(1)
        form.body.addLayout(buttons)
        self.add_card(form)

        result = result_card("拆分结果", "表格按需绘制，万级子网也不卡顿")
        self.summary = label("等待输入", "EmptyHint")
        self.summary.setWordWrap(True)
        result.body.addWidget(self.summary)

        self.model = SubnetTableModel(self)
        self.table = QTableView()
        self.table.setObjectName("ResultTable")
        self.table.setModel(self.model)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        result.body.addWidget(self.table, 1)
        result.body.addWidget(HLine())

        code_head = QHBoxLayout()
        code_head.setSpacing(8)
        code_head.addWidget(label("导出", "CardHint"))
        code_head.addStretch(1)
        self.export_button = SecondaryButton("导出 CSV")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self._choose_export)
        code_head.addWidget(self.export_button)
        result.body.addLayout(code_head)
        self.add_card(result, 1)

        self.mode.currentIndexChanged.connect(self._on_mode_changed)
        self.count.valueChanged.connect(self._refresh_hint)

    # -- 交互 ---------------------------------------------------------------
    def _on_mode_changed(self, index: int) -> None:
        self.error.clear_error()
        if index == _MODE_HOSTS:
            self.count.setRange(1, 65536)
            self.count.setSingleStep(16)
            self.count.setValue(254)
        else:
            self.count.setRange(1, MAX_ROWS)
            self.count.setSingleStep(1)
            self.count.setValue(4)
        self._refresh_hint()

    def _refresh_hint(self) -> None:
        amount = self.count.value()
        if self.mode.currentIndex() == _MODE_HOSTS:
            self.hint.setText(f"每段至少容纳 {amount} 台可用主机")
        else:
            self.hint.setText(f"拆成 {amount} 个子网")

    def clear(self) -> None:
        self.network.clear()
        self.mode.setCurrentIndex(_MODE_COUNT)
        self.count.setRange(1, MAX_ROWS)
        self.count.setSingleStep(1)
        self.count.setValue(4)
        self.error.clear_error()
        self._infos = []
        self.model.set_rows([])
        self.summary.setText("等待输入")
        self.export_button.setEnabled(False)
        self._refresh_hint()

    def calculate(self) -> list[ipcalc.NetworkInfo] | None:
        """执行拆分；成功返回子网列表，失败返回 ``None`` 并展示错误。"""
        self.error.clear_error()
        text = self.network.text().strip()
        if not text:
            self.error.show_error("请填写起始网段，例如 10.0.0.0/16")
            return None
        try:
            if self.mode.currentIndex() == _MODE_HOSTS:
                infos = ipcalc.split_by_hosts(text, self.count.value())
            else:
                infos = ipcalc.split_by_count(text, self.count.value())
        except (ipcalc.CalcError, ValueError) as exc:
            message = (
                str(exc)
                if isinstance(exc, ipcalc.CalcError)
                else "网段格式不正确，请使用 10.0.0.0/16 形式"
            )
            self.error.show_error(message)
            return None
        if len(infos) > MAX_ROWS:
            self.error.show_error(
                f"拆分结果 {len(infos):,} 段，超过上限 {MAX_ROWS:,} 段，请减小拆分粒度"
            )
            return None
        self._infos = infos
        self.model.set_rows(infos)
        self._show(infos)
        return infos

    def _show(self, infos: list[ipcalc.NetworkInfo]) -> None:
        first = infos[0]
        self.summary.setText(
            f"拆分为 {len(infos):,} 段 /{first.prefix}，"
            f"每段 {first.usable_hosts:,} 台可用主机，"
            f"共 {first.total_addresses * len(infos):,} 个地址。"
        )
        self.export_button.setEnabled(bool(infos))

    def _choose_export(self) -> None:
        if not self._infos:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出拆分结果", "subnets.csv", "CSV 文件 (*.csv)"
        )
        if path:
            self.export_csv(path)

    def export_csv(self, path: str | Path) -> int:
        """把当前结果写成 CSV（UTF-8 BOM，Excel 直接打开不乱码），返回行数。"""
        if not self._infos:
            return 0
        target = Path(path)
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(_COLUMNS)
            for index, info in enumerate(self._infos, start=1):
                writer.writerow(
                    [
                        index,
                        info.cidr,
                        info.mask,
                        info.host_range,
                        info.broadcast,
                        info.usable_hosts,
                    ]
                )
        return len(self._infos)

    # -- 对外接口 -----------------------------------------------------------
    def set_network(self, text: str) -> None:
        self.network.setText(text)

    def result(self) -> list[ipcalc.NetworkInfo]:
        return list(self._infos)

    def summary_text(self) -> str:
        return self.summary.text()

    def table_value(self, row: int, column: int) -> str:
        index = self.model.index(row, column)
        return str(self.model.data(index, Qt.ItemDataRole.DisplayRole) or "")
