"""路由聚合计算器页面（calc.aggregate）。

左侧粘贴网段列表，选择聚合算法后即时给出聚合块、冗余地址与设备配置片段。
算法全部在 ``core/ipcalc.py``（``aggregate`` / ``optimize``），本页只做取值与展示。
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDoubleSpinBox,
    QGridLayout,
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
    copy_to_clipboard,
    make_combo,
    make_line_edit,
    make_text_edit,
    result_card,
)

_STYLES: list[tuple[str, str]] = [
    ("H3C / 华为（ip route-static）", "h3c"),
    ("思科（ip route）", "cisco"),
]

_MODE_EXACT = 0
_MODE_OPTIMIZE = 1

_COLUMNS = ["聚合块", "覆盖的原网段", "冗余地址"]


class AggregatePage(ToolPage):
    def __init__(self, parent=None) -> None:
        super().__init__(
            title="路由聚合计算器",
            subtitle="粘贴一长串子网，合并成最少的路由条目，并直接生成设备配置片段。",
            parent=parent,
        )
        self._result: ipcalc.AggregateResult | None = None

        form = result_card("网段列表", "每行一条，也可用逗号或空格分隔；# 之后为注释")
        self.networks = make_text_edit("例如：\n10.0.0.0/24\n10.0.1.0/24\n10.0.2.0/24")
        self.mode = make_combo(["精准聚合（不产生多余地址）", "优化聚合（允许少量冗余）"])
        self.waste = QDoubleSpinBox()
        self.waste.setObjectName("ToolInput")
        self.waste.setRange(0.0, 0.5)
        self.waste.setSingleStep(0.05)
        self.waste.setDecimals(2)
        self.waste.setValue(0.25)
        self.waste.setSuffix(" 冗余上限")
        self.waste.setEnabled(False)
        self.hop = make_line_edit("例如 10.0.0.1")
        self.style = make_combo([name for name, _ in _STYLES])
        self.error = InlineError()

        form.body.addWidget(self.networks)

        # 算法/冗余一行，下一跳/设备风格一行：每行最多两个字段，避免输入框被挤成一条线。
        row1 = QGridLayout()
        row1.setHorizontalSpacing(18)
        row1.setVerticalSpacing(10)
        row1.addWidget(FieldRow("聚合算法", self.mode, label_width=72), 0, 0)
        row1.addWidget(FieldRow("冗余比例", self.waste, label_width=72), 0, 1)
        row1.setColumnStretch(0, 1)
        row1.setColumnStretch(1, 1)
        form.body.addLayout(row1)

        row2 = QGridLayout()
        row2.setHorizontalSpacing(18)
        row2.setVerticalSpacing(10)
        row2.addWidget(FieldRow("下一跳", self.hop, hint="用于生成配置片段", label_width=72), 0, 0)
        row2.addWidget(FieldRow("设备风格", self.style, label_width=72), 0, 1)
        row2.setColumnStretch(0, 1)
        row2.setColumnStretch(1, 1)
        form.body.addLayout(row2)
        form.body.addWidget(self.error)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.calc_button = PrimaryButton("聚合")
        self.clear_button = SecondaryButton("清空")
        self.calc_button.clicked.connect(self.calculate)
        self.clear_button.clicked.connect(self.clear)
        buttons.addWidget(self.calc_button)
        buttons.addWidget(self.clear_button)
        buttons.addStretch(1)
        form.body.addLayout(buttons)
        self.add_card(form)

        result = result_card("聚合结果", "被包含的网段会自动剔除，重复项只计一次")
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
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        result.body.addWidget(self.table)
        result.body.addWidget(HLine())

        code_head = QHBoxLayout()
        code_head.setSpacing(8)
        code_head.addWidget(label("配置片段", "CardHint"))
        code_head.addStretch(1)
        self.copy_button = SecondaryButton("复制配置")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self.copy_config)
        code_head.addWidget(self.copy_button)
        result.body.addLayout(code_head)

        self.config = make_text_edit("填写下一跳并聚合后，这里会生成可直接粘贴的配置", rows=8)
        self.config.setReadOnly(True)
        result.body.addWidget(self.config)
        self.add_card(result, 1)

        self.mode.currentIndexChanged.connect(self._on_mode_changed)
        self.hop.textChanged.connect(self._refresh_config)
        self.style.currentIndexChanged.connect(self._refresh_config)

    # -- 交互 ---------------------------------------------------------------
    def _on_mode_changed(self, index: int) -> None:
        self.waste.setEnabled(index == _MODE_OPTIMIZE)
        self.error.clear_error()

    def clear(self) -> None:
        self.networks.clear()
        self.mode.setCurrentIndex(_MODE_EXACT)
        self.waste.setValue(0.25)
        self.hop.clear()
        self.style.setCurrentIndex(0)
        self.error.clear_error()
        self._result = None
        self.table.setRowCount(0)
        self.summary.setText("等待输入")
        self.config.clear()
        self.copy_button.setEnabled(False)

    def calculate(self) -> ipcalc.AggregateResult | None:
        """执行聚合；成功返回结果，失败返回 ``None`` 并展示错误。"""
        self.error.clear_error()
        text = self.networks.toPlainText()
        if not text.strip():
            self.error.show_error("请至少粘贴一个网段，例如 10.0.0.0/24")
            return None
        try:
            if self.mode.currentIndex() == _MODE_OPTIMIZE:
                result = ipcalc.optimize(text, max_waste_ratio=self.waste.value())
            else:
                result = ipcalc.aggregate(text)
        except ipcalc.CalcError as exc:
            self.error.show_error(str(exc))
            return None
        self._result = result
        self._show(result)
        return result

    def _show(self, result: ipcalc.AggregateResult) -> None:
        self.table.setRowCount(len(result.details))
        for row, block in enumerate(result.details):
            sources = "、".join(str(source) for source in block.sources)
            cells = (block.cidr, sources, f"{block.wasted:,}" if block.wasted else "0")
            for column, text in enumerate(cells):
                self.table.setItem(row, column, QTableWidgetItem(text))
        self.table.resizeRowsToContents()

        parts = [
            f"输入 {len(result.sources) + len(result.contained)} 条",
            f"聚合为 {len(result.blocks)} 条",
        ]
        if result.duplicates:
            parts.append(f"重复 {result.duplicates} 条")
        if result.contained:
            parts.append(f"剔除被包含 {len(result.contained)} 条")
        if result.wasted_addresses:
            parts.append(f"冗余 {result.wasted_addresses:,} 个地址")
        self.summary.setText("，".join(parts) + "。")
        self._refresh_config()

    def _refresh_config(self) -> None:
        result = self._result
        if result is None:
            self.copy_button.setEnabled(False)
            return
        hop = self.hop.text().strip()
        if not hop:
            self.config.setPlainText("填写下一跳地址后，这里会生成可直接粘贴的配置。")
            self.copy_button.setEnabled(False)
            return
        style_key = _STYLES[self.style.currentIndex()][1]
        try:
            text = result.render_config(hop, style_key)
        except ipcalc.CalcError as exc:
            self.config.setPlainText(str(exc))
            self.copy_button.setEnabled(False)
            return
        self.config.setPlainText(text or "没有可生成的配置。")
        self.copy_button.setEnabled(bool(text))

    def copy_config(self) -> None:
        text = self.config.toPlainText()
        if text:
            copy_to_clipboard(text)

    # -- 对外接口 -----------------------------------------------------------
    def set_networks(self, text: str) -> None:
        self.networks.setPlainText(text)

    def set_hop(self, text: str) -> None:
        self.hop.setText(text)

    def result(self) -> ipcalc.AggregateResult | None:
        return self._result

    def summary_text(self) -> str:
        return self.summary.text()

    def config_text(self) -> str:
        return self.config.toPlainText()

    def table_value(self, row: int, column: int) -> str:
        item = self.table.item(row, column)
        return item.text() if item is not None else ""
