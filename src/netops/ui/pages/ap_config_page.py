"""H3C AP 注册配置生成页面（h3c.ap）。

支持两种录入模式：
1. 单台表单：AP 名称 / 序列号 / 型号 / 服务模板 / AP 组，输入时 120ms 防抖自动生成
2. CSV 批量：多行批量粘贴或导入 CSV，整表先做严格校验，有任一行非法直接报出行号并拒绝生成

右侧/下方为只读代码区（带行号与语法高亮外框），支持整段复制、仅复制 AP 组配置与保存到文件。
数据模型与模板渲染完全在 ``services/h3c_ap.py``，本页只做录入、展示与信号转发。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from netops.services import h3c_ap as ap_service
from netops.ui.widgets.common import add_shadow, label
from netops.ui.widgets.forms import (
    FieldRow,
    InlineError,
    PrimaryButton,
    SecondaryButton,
    ToolPage,
    make_line_edit,
    make_text_edit,
    result_card,
    separator_line,
)

_DEBOUNCE_MS = 120

_CSV_EXAMPLE = """# CSV 批量导入示例（前 3 列必填，后 2 列可选）
name,serial,model,service_templates,ap_group
office-3f-01,210235A1B2C3D4E5F6,WA6320,10 11,office-3f
office-3f-02,210235A1B2C3D4E5F7,WA6320,10 11,office-3f
office-3f-03,210235A1B2C3D4E5F8,WA6320,10,office-3f
lobby-ap,210235A1B2C3D4E5F9,WTU430,,
"""

_TABLE_COLUMNS = ["行", "AP 名称", "序列号", "型号", "服务模板", "AP 组"]


class ApConfigPage(ToolPage):
    def __init__(self, parent=None) -> None:
        super().__init__(
            title="AP 注册配置生成",
            subtitle="填 AP 名称、序列号、型号与所属 AP 组，自动生成可直接粘贴的 H3C Comware 注册脚本。",
            parent=parent,
        )
        self._rendered_code = ""
        self._group_code = ""
        self._mode = "single"  # "single" 或 "batch"

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._on_debounce)

        self._build_single_card()
        self._build_batch_card()
        self._build_preview_card()

    # -- 界面搭建 -----------------------------------------------------------
    def _build_single_card(self) -> None:
        self.single_card = result_card("单台 AP 录入", "输入各字段后自动生成配置；右侧可直接复制")

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)

        self.name_input = make_line_edit("例如 office-3f-01")
        grid.addWidget(FieldRow("AP 名称", self.name_input, label_width=72), 0, 0)

        self.serial_input = make_line_edit("例如 210235A1B2C3D4E5F6")
        grid.addWidget(FieldRow("序列号", self.serial_input, label_width=72), 0, 1)

        self.model_input = make_line_edit("例如 WA6320 / WTU430")
        grid.addWidget(FieldRow("型号", self.model_input, label_width=72), 1, 0)

        self.templates_input = make_line_edit("例如 10 11（空格或逗号分隔，可选）")
        grid.addWidget(FieldRow("服务模板", self.templates_input, label_width=72), 1, 1)

        self.group_input = make_line_edit("例如 office-3f（可选）")
        grid.addWidget(FieldRow("AP 组", self.group_input, label_width=72), 2, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.single_card.body.addLayout(grid)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.single_clear_btn = SecondaryButton("清空表单")
        self.single_clear_btn.clicked.connect(self._clear_single)
        self.switch_to_batch_btn = SecondaryButton("切换到 CSV 批量导入")
        self.switch_to_batch_btn.clicked.connect(self._show_batch)
        buttons.addWidget(self.single_clear_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.switch_to_batch_btn)
        self.single_card.body.addLayout(buttons)

        self.single_error = InlineError()
        self.single_card.body.addWidget(self.single_error)
        self.add_card(self.single_card)

        # 监听输入变动
        for widget in (
            self.name_input,
            self.serial_input,
            self.model_input,
            self.templates_input,
            self.group_input,
        ):
            widget.textChanged.connect(self._trigger_debounce)

    def _build_batch_card(self) -> None:
        self.batch_card = result_card(
            "CSV 批量导入",
            "粘贴 CSV 内容；任何一行不合法整表不生成，避免半成品配置",
        )
        self.batch_card.hide()

        self.csv_input = make_text_edit(_CSV_EXAMPLE, rows=6)
        self.batch_card.body.addWidget(self.csv_input)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.import_file_btn = SecondaryButton("打开 CSV 文件…")
        self.import_file_btn.clicked.connect(self._choose_csv_file)
        self.batch_generate_btn = PrimaryButton("校验并生成")
        self.batch_generate_btn.clicked.connect(self._generate_batch)
        self.batch_clear_btn = SecondaryButton("清空")
        self.batch_clear_btn.clicked.connect(self._clear_batch)
        self.switch_to_single_btn = SecondaryButton("返回单台录入")
        self.switch_to_single_btn.clicked.connect(self._show_single)

        buttons.addWidget(self.import_file_btn)
        buttons.addWidget(self.batch_generate_btn)
        buttons.addWidget(self.batch_clear_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.switch_to_single_btn)
        self.batch_card.body.addLayout(buttons)

        self.batch_error = InlineError()
        self.batch_card.body.addWidget(self.batch_error)

        # 校验成功后的明细表
        self.batch_table = QTableWidget(0, len(_TABLE_COLUMNS))
        self.batch_table.setObjectName("ResultTable")
        self.batch_table.setHorizontalHeaderLabels(_TABLE_COLUMNS)
        self.batch_table.verticalHeader().setVisible(False)
        self.batch_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.batch_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.batch_table.setAlternatingRowColors(True)
        header = self.batch_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.batch_table.setMinimumHeight(120)
        self.batch_table.hide()
        self.batch_card.body.addWidget(self.batch_table)

        self.add_card(self.batch_card)

    def _build_preview_card(self) -> None:
        card = result_card("配置预览", "H3C Comware 格式，可直接复制到 AC 命令行执行")
        self.code_view = _EditableCodeView()
        card.body.addWidget(self.code_view, 1)
        card.body.addWidget(separator_line())

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.summary_label = label("等待输入", "EmptyHint")
        self.summary_label.setWordWrap(True)
        footer.addWidget(self.summary_label, 1)

        self.copy_all_btn = PrimaryButton("复制整段配置")
        self.copy_all_btn.clicked.connect(self._copy_all)
        self.copy_group_btn = SecondaryButton("仅复制 AP 组")
        self.copy_group_btn.clicked.connect(self._copy_group)
        self.export_cfg_btn = SecondaryButton("导出 .cfg 文件")
        self.export_cfg_btn.clicked.connect(self._choose_export_cfg)

        self.copy_all_btn.setEnabled(False)
        self.copy_group_btn.setEnabled(False)
        self.export_cfg_btn.setEnabled(False)

        footer.addWidget(self.copy_all_btn)
        footer.addWidget(self.copy_group_btn)
        footer.addWidget(self.export_cfg_btn)
        card.body.addLayout(footer)
        self.add_card(card, 1)

    # -- 模式切换 -----------------------------------------------------------
    def _show_batch(self) -> None:
        self._mode = "batch"
        self.single_card.hide()
        self.batch_card.show()
        self._clear_preview()

    def _show_single(self) -> None:
        self._mode = "single"
        self.batch_card.hide()
        self.single_card.show()
        self._trigger_debounce()

    def mode(self) -> str:
        return self._mode

    # -- 单台防抖生成 -------------------------------------------------------
    def _trigger_debounce(self) -> None:
        if self._mode != "single":
            return
        self._debounce.start()

    def _on_debounce(self) -> None:
        name = self.name_input.text().strip()
        serial = self.serial_input.text().strip()
        model = self.model_input.text().strip()

        # 三个必填字段任一为空时清空预览，不报错
        if not (name and serial and model):
            self.single_error.clear_error()
            self._clear_preview()
            return

        try:
            ap = ap_service.parse_ap(
                name=name,
                serial=serial,
                model=model,
                service_templates=self.templates_input.text().strip(),
                ap_group=self.group_input.text().strip(),
            )
            batch = ap_service.build_batch([ap])
            config = ap_service.render_config(batch)
        except ap_service.ApError as exc:
            self.single_error.show_error(str(exc))
            self._clear_preview()
            return

        self.single_error.clear_error()
        self._update_preview(config, batch)

    # -- 批量生成 -----------------------------------------------------------
    def _generate_batch(self) -> bool:
        text = self.csv_input.toPlainText()
        try:
            specs = ap_service.parse_csv(text)
            batch = ap_service.build_batch(specs)
            config = ap_service.render_config(batch)
        except ap_service.ApError as exc:
            self.batch_error.show_error(str(exc))
            self._clear_preview()
            self.batch_table.hide()
            return False

        self.batch_error.clear_error()
        self._update_preview(config, batch)
        self._fill_batch_table(specs)
        return True

    def _fill_batch_table(self, specs: tuple[ap_service.ApSpec, ...]) -> None:
        self.batch_table.setRowCount(len(specs))
        for row, spec in enumerate(specs):
            templates_str = " ".join(str(t) for t in spec.service_templates) or "—"
            cells = [
                str(row + 1),
                spec.name,
                spec.serial,
                spec.model,
                templates_str,
                spec.ap_group or "—",
            ]
            for col, val in enumerate(cells):
                self.batch_table.setItem(row, col, QTableWidgetItem(val))
        self.batch_table.show()

    # -- 预览更新 -----------------------------------------------------------
    def _update_preview(self, config: str, batch: ap_service.ApBatch) -> None:
        self._rendered_code = config
        self.code_view.set_code(config)

        # 提取 AP 组配置片段
        group_lines: list[str] = []
        in_group = False
        for line in config.splitlines():
            if line.startswith("wlan ap-group"):
                in_group = True
            if in_group:
                group_lines.append(line)
            if in_group and line.strip() == "#":
                in_group = False
        self._group_code = "\n".join(group_lines)

        self.copy_all_btn.setEnabled(True)
        self.copy_group_btn.setEnabled(bool(self._group_code))
        self.export_cfg_btn.setEnabled(True)

        summary_parts = [f"共 {batch.ap_count} 台 AP"]
        if batch.group_count:
            summary_parts.append(f"{batch.group_count} 个 AP 组")
        self.summary_label.setText("，".join(summary_parts) + "。")

    def _clear_preview(self) -> None:
        self._rendered_code = ""
        self._group_code = ""
        self.code_view.set_code("")
        self.copy_all_btn.setEnabled(False)
        self.copy_group_btn.setEnabled(False)
        self.export_cfg_btn.setEnabled(False)
        self.summary_label.setText("等待输入")

    # -- 清空 ---------------------------------------------------------------
    def _clear_single(self) -> None:
        for widget in (
            self.name_input,
            self.serial_input,
            self.model_input,
            self.templates_input,
            self.group_input,
        ):
            widget.clear()
        self.single_error.clear_error()
        self._clear_preview()

    def _clear_batch(self) -> None:
        self.csv_input.clear()
        self.batch_error.clear_error()
        self.batch_table.setRowCount(0)
        self.batch_table.hide()
        self._clear_preview()

    # -- 文件与复制 ---------------------------------------------------------
    def _choose_csv_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 CSV 文件", "", "CSV 文件 (*.csv);;所有文件 (*.*)"
        )
        if path:
            text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
            self.csv_input.setPlainText(text)
            self._generate_batch()

    def _choose_export_cfg(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出配置文件", "ap-register.cfg", "配置文件 (*.cfg);;文本文件 (*.txt)"
        )
        if path:
            Path(path).write_text(self._rendered_code, encoding="utf-8")

    def _copy_all(self) -> None:
        _to_clipboard(self._rendered_code)

    def _copy_group(self) -> None:
        if self._group_code:
            _to_clipboard(self._group_code)

    # -- 测试 / 外部接口 ----------------------------------------------------
    def set_single(
        self,
        *,
        name: str,
        serial: str,
        model: str,
        templates: str = "",
        group: str = "",
    ) -> None:
        self.name_input.setText(name)
        self.serial_input.setText(serial)
        self.model_input.setText(model)
        self.templates_input.setText(templates)
        self.group_input.setText(group)
        self._on_debounce()

    def set_csv(self, text: str) -> None:
        self._show_batch()
        self.csv_input.setPlainText(text)

    def generated_code(self) -> str:
        return self._rendered_code

    def group_code(self) -> str:
        return self._group_code

    def error_text(self) -> str:
        if self._mode == "single":
            return self.single_error.text()
        return self.batch_error.text()

    def summary_text(self) -> str:
        return self.summary_label.text()


# ---------------------------------------------------------------------------
# 内部组件：可编辑的代码预览框（带行号外观和深色头）
# ---------------------------------------------------------------------------


class _EditableCodeView(QFrame):
    """带标题栏和 macOS 风格三色圆点的配置预览框。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CodeBlock")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("CodeBlockHeader")
        header.setFixedHeight(34)
        hbox = QHBoxLayout(header)
        hbox.setContentsMargins(12, 0, 12, 0)
        hbox.setSpacing(6)
        for color in ("#F87171", "#FBBF24", "#34D399"):
            dot = QLabel()
            dot.setFixedSize(9, 9)
            dot.setStyleSheet(f"background-color: {color}; border-radius: 4px;")
            hbox.addWidget(dot)
        hbox.addSpacing(8)
        hbox.addWidget(label("ap-register.cfg", "CodeBlockTitle"))
        hbox.addStretch(1)
        hbox.addWidget(label("H3C Comware", "CodeBlockQ"))
        layout.addWidget(header)

        self.editor = QPlainTextEdit(self)
        self.editor.setObjectName("ToolInputMono")
        self.editor.setReadOnly(True)
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self.editor)

        add_shadow(self, blur=18, alpha=26, dy=5)

    def set_code(self, code: str) -> None:
        self.editor.setPlainText(code)


def _to_clipboard(text: str) -> None:
    app = QApplication.instance()
    if app is not None:
        clipboard = app.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
