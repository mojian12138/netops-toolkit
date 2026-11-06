"""H3C Comware V7 script generator."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QRegularExpression, Qt, QTimer
from PyQt6.QtGui import QColor, QGuiApplication, QSyntaxHighlighter, QTextCharFormat
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from netops.core.db import Database
from netops.services.scripts import ScriptError, ScriptService
from netops.ui.widgets.forms import (
    FieldRow,
    InlineError,
    PrimaryButton,
    SecondaryButton,
    ToolPage,
    make_line_edit,
    result_card,
)
from netops.ui.widgets.sections import CollapsibleSection


class ComwareHighlighter(QSyntaxHighlighter):
    def __init__(self, document) -> None:
        super().__init__(document)
        self._comment = QTextCharFormat()
        self._comment.setForeground(QColor("#64748b"))
        self._keyword = QTextCharFormat()
        self._keyword.setForeground(QColor("#0891b2"))
        self._keyword.setFontWeight(700)
        self._value = QTextCharFormat()
        self._value.setForeground(QColor("#b45309"))
        self._rules = [
            (QRegularExpression(r"^\s*#.*$"), self._comment),
            (
                QRegularExpression(
                    r"\b(?:sys|sysname|interface|vlan|local-user|user-interface|ip|undo|save|return|quit)\b"
                ),
                self._keyword,
            ),
            (QRegularExpression(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), self._value),
        ]

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        for pattern, fmt in self._rules:
            match = pattern.match(text)
            while match.hasMatch():
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)
                match = pattern.match(text, match.capturedEnd())


class ScriptPage(ToolPage):
    def __init__(
        self, service: ScriptService | None = None, db: Database | None = None, parent=None
    ) -> None:
        super().__init__(
            title="H3C Comware V7 开局配置生成器",
            subtitle="填写参数后实时生成；密码在预览中明文显示。",
            parent=parent,
        )
        self.service = service or ScriptService()
        self.db = db
        self._current = self.service.registry.get("h3c.comware_v7")
        self._fields: dict[str, QWidget] = {}
        self._field_rows: dict[str, QWidget] = {}
        self._field_defaults: dict[str, object] = {}
        self._sections: dict[str, CollapsibleSection] = {}
        self._preset_edit_id: int | None = None
        self._preset_edit_name = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._render)
        self._build()
        self._build_fields()
        self._update_dependent_fields()
        self._render()

    def _build(self) -> None:
        header = QHBoxLayout()
        title = QLabel("H3C Comware V7")
        title.setObjectName("ToolPageTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.reset_btn = SecondaryButton("重置为默认")
        self.copy_btn = PrimaryButton("一键复制")
        self.force_generate_btn = SecondaryButton("强制生成（标记错误）")
        self.export_btn = SecondaryButton("导出配置")
        for button in (self.reset_btn, self.copy_btn, self.force_generate_btn, self.export_btn):
            header.addWidget(button)
        actions = QHBoxLayout()
        self.save_preset_btn = SecondaryButton("保存为预设")
        self.load_preset_btn = SecondaryButton("载入预设")
        self.rename_preset_btn = SecondaryButton("重命名预设")
        self.delete_preset_btn = SecondaryButton("删除预设")
        for button in (
            self.save_preset_btn,
            self.load_preset_btn,
            self.rename_preset_btn,
            self.delete_preset_btn,
        ):
            actions.addWidget(button)
        actions.addStretch(1)
        self.body.addLayout(header)
        self.body.addLayout(actions)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("ScriptSplitter")
        left = QWidget()
        left.setObjectName("ScriptFormPane")
        left.setMinimumWidth(560)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 10, 0)
        left_layout.setSpacing(10)
        self.form_host = QWidget()
        self.form_host.setObjectName("ScriptFormContent")
        self.form_layout = QVBoxLayout(self.form_host)
        self.form_layout.setContentsMargins(0, 0, 4, 0)
        self.form_layout.setSpacing(10)
        self.error = InlineError()
        self.form_layout.addWidget(self.error)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("ScriptFormScroll")
        self.scroll.viewport().setObjectName("ScriptFormViewport")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.form_host)
        left_layout.addWidget(self.scroll, 1)
        self.splitter.addWidget(left)

        self.preview_card = result_card(
            "实时代码预览", "强制生成会在配置开头标注无效字段；下发前请修正"
        )
        self.code_view = QPlainTextEdit()
        self.code_view.setObjectName("ToolInputMono")
        self.code_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.highlighter = ComwareHighlighter(self.code_view.document())
        self.preview_card.body.addWidget(self.code_view, 1)
        self.splitter.addWidget(self.preview_card)
        self.splitter.setSizes([720, 960])
        self.splitter.setStretchFactor(0, 4)
        self.splitter.setStretchFactor(1, 5)
        self.body.addWidget(self.splitter, 1)
        self.reset_btn.clicked.connect(self._reset_defaults)
        self.copy_btn.clicked.connect(self._copy_code)
        self.force_generate_btn.clicked.connect(self._force_generate)
        self.export_btn.clicked.connect(self.export_code)
        self.save_preset_btn.clicked.connect(self.save_preset)
        self.load_preset_btn.clicked.connect(self.load_preset)
        self.rename_preset_btn.clicked.connect(self.rename_selected_preset)
        self.delete_preset_btn.clicked.connect(self.delete_selected_preset)

    def _build_fields(self) -> None:
        for field in self._current.fields:
            name = str(field["name"])
            self._field_defaults[name] = field.get("default")
            section_name = field.get("section", "基础")
            section = self._sections.get(section_name)
            if section is None:
                section = CollapsibleSection(
                    section_name, expanded=section_name in {"基础与系统命名", "带内管理网络与路由"}
                )
                self._sections[section_name] = section
                self.form_layout.insertWidget(self.form_layout.count() - 1, section)
            widget = self._make_widget(field)
            self._fields[name] = widget
            row = FieldRow(str(field.get("title", name)), widget, label_width=112)
            self._field_rows[name] = row
            section.add_widget(row)

    def _make_widget(self, field: dict[str, object]) -> QWidget:
        name = str(field["name"])
        kind = field.get("type", "string")
        if name == "service_types":
            widget = QWidget()
            grid = QGridLayout(widget)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(16)
            grid.setVerticalSpacing(8)
            options = (
                ("ssh", "SSH"),
                ("telnet", "Telnet"),
                ("terminal", "Terminal"),
                ("http", "HTTP"),
                ("https", "HTTPS"),
            )
            for index, (value, label) in enumerate(options):
                check = QCheckBox(label)
                check.setObjectName("ToolCheck")
                check.setProperty("service_value", value)
                check.setChecked(value in field.get("default", []))
                check.stateChanged.connect(lambda _state: self._render_immediately())
                grid.addWidget(check, index // 3, index % 3)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            grid.setColumnStretch(2, 1)
        elif name == "password":
            widget = make_line_edit(str(field.get("default", "")))
            widget.setText(str(field.get("default", "")))
            widget.setEchoMode(QLineEdit.EchoMode.Normal)
            widget.textChanged.connect(lambda _value: self._timer.start())
        elif name in {"aggregation_members", "business_range"}:
            widget = make_line_edit(str(field.get("default", "")))
            widget.setText(str(field.get("default", "")))
            widget.setPlaceholderText("1/0/1-24 或 GE1/0/1 to GE1/0/24；多段以逗号分隔")
            widget.textChanged.connect(lambda _value: self._timer.start())
        elif name in {"hostname", "username", "mgmt_ip", "gateway", "business_permit_vlans"}:
            widget = make_line_edit(str(field.get("default", "")))
            widget.setText(str(field.get("default", "")))
            widget.textChanged.connect(lambda _value: self._timer.start())
        elif name == "vty_max":
            widget = QSpinBox()
            widget.setObjectName("ToolInput")
            widget.setRange(1, 63)
            widget.setValue(int(field.get("default", 63)))
            widget.valueChanged.connect(lambda _value: self._render_immediately())
            widget.valueChanged.connect(lambda _value: self._render_immediately())
        elif name == "show_comments":
            widget = QCheckBox("显示 # 配置描述")
            widget.setObjectName("ToolCheck")
            widget.setChecked(bool(field.get("default", True)))
            widget.stateChanged.connect(lambda _state: self._render_immediately())
        elif kind == "boolean":
            label = "启用"
            if name == "password_policy":
                label = "启用密码策略放行（关闭后保留设备默认密码策略）"
            widget = QCheckBox(label)
            widget.setObjectName("ToolCheck")
            widget.setChecked(bool(field.get("default", False)))
            widget.stateChanged.connect(
                lambda _state, field_name=name: self._on_boolean_changed(field_name)
            )
        elif kind == "integer":
            widget = QSpinBox()
            widget.setObjectName("ToolInput")
            widget.setRange(int(field.get("minimum", 0)), int(field.get("maximum", 4094)))
            widget.setValue(int(field.get("default", 0)))
            if name in {"aggregation_id", "business_vlan", "business_pvid"}:
                widget.valueChanged.connect(lambda _value: self._render_immediately())
            else:
                widget.valueChanged.connect(lambda _value: self._timer.start())
        elif name in {
            "uplink_mode",
            "aggregation_mode",
            "business_mode",
            "mgmt_vlan_desc",
            "ntp_server",
            "uplink_interface",
        }:
            widget = QComboBox()
            widget.setObjectName("ToolInput")
            widget.setEditable(name in {"mgmt_vlan_desc", "ntp_server", "uplink_interface"})
            if name == "uplink_mode":
                widget.addItem("单端口", "single")
                widget.addItem("链路聚合", "aggregation")
            elif name == "aggregation_mode":
                widget.addItem("动态 LACP", "dynamic")
                widget.addItem("静态", "static")
            elif name == "business_mode":
                widget.addItem("Access", "access")
                widget.addItem("Trunk", "trunk")
            elif name == "mgmt_vlan_desc":
                widget.addItems(["SW-Manage", "Office-Manage", "Device-Manage", "AP-Manage"])
            elif name == "ntp_server":
                widget.addItems(["ntp.aliyun.com", "ntp.tencent.com", "ntp.ntsc.ac.cn"])
            else:
                widget.addItems(
                    [
                        "Ten-GigabitEthernet 1/0/49",
                        "Ten-GigabitEthernet 1/0/50",
                        "GigabitEthernet 1/0/48",
                    ]
                )
            default = field.get("default", "")
            index = widget.findData(default)
            if index >= 0:
                widget.setCurrentIndex(index)
            elif widget.isEditable():
                widget.setCurrentText(str(default))
            widget.setEditable(name in {"mgmt_vlan_desc", "ntp_server", "uplink_interface"})
            widget.currentIndexChanged.connect(
                lambda _index, field_name=name: self._on_mode_changed(field_name)
            )
            if name in {"uplink_mode", "aggregation_mode", "business_mode"}:
                widget.setEditable(False)
        else:
            widget = make_line_edit(str(field.get("default", "")))
            widget.textChanged.connect(lambda _value: self._timer.start())
        return widget

    def _on_boolean_changed(self, _name: str) -> None:
        self._update_dependent_fields()
        self._render_immediately()

    def _on_mode_changed(self, _name: str) -> None:
        self._update_dependent_fields()
        self._render_immediately()

    def _update_dependent_fields(self) -> None:
        uplink_on = bool(
            self._fields.get("uplink_enabled") and self._fields["uplink_enabled"].isChecked()
        )
        business_on = bool(
            self._fields.get("business_enabled") and self._fields["business_enabled"].isChecked()
        )
        uplink_mode = (
            self._fields["uplink_mode"].currentData() if "uplink_mode" in self._fields else "single"
        )
        business_mode = (
            self._fields["business_mode"].currentData()
            if "business_mode" in self._fields
            else "access"
        )
        for name, row in self._field_rows.items():
            visible = True
            if name in {"uplink_mode"}:
                visible = uplink_on
            elif name == "uplink_interface":
                visible = uplink_on and uplink_mode == "single"
            elif name in {"aggregation_id", "aggregation_mode", "aggregation_members"}:
                visible = uplink_on and uplink_mode == "aggregation"
            elif name in {
                "business_range",
                "business_mode",
                "business_edge",
                "business_dhcp_trust",
            }:
                visible = business_on
            elif name == "business_vlan":
                visible = business_on and business_mode == "access"
            elif name in {"business_pvid", "business_permit_vlans"}:
                visible = business_on and business_mode == "trunk"
            row.setVisible(visible)
            self._fields[name].setEnabled(visible)

    def _values(self) -> dict[str, object]:
        values: dict[str, object] = {}
        for name, widget in self._fields.items():
            if name == "service_types":
                values[name] = [
                    check.property("service_value")
                    for check in widget.findChildren(QCheckBox)
                    if check.isChecked()
                ]
            elif isinstance(widget, QLineEdit):
                values[name] = widget.text().strip()
            elif isinstance(widget, QSpinBox):
                values[name] = widget.value()
            elif isinstance(widget, QCheckBox):
                values[name] = widget.isChecked()
            elif isinstance(widget, QComboBox):
                data = widget.currentData()
                values[name] = data if data is not None else widget.currentText().strip()
        return values

    def _reset_defaults(self) -> None:
        defaults = self.service.defaults(self._current.template_id)
        self._preset_edit_id = None
        self._preset_edit_name = ""
        for name, widget in self._fields.items():
            value = self._field_defaults.get(name, defaults.get(name))
            if name == "service_types":
                for check in widget.findChildren(QCheckBox):
                    check.setChecked(check.property("service_value") in (value or []))
            elif isinstance(widget, QLineEdit):
                widget.setText(str(value if value is not None else ""))
            elif isinstance(widget, QSpinBox):
                widget.setValue(int(value or 0))
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QComboBox):
                index = widget.findData(value)
                widget.setCurrentIndex(index if index >= 0 else 0)
                if widget.isEditable() and index < 0:
                    widget.setCurrentText(str(value if value is not None else ""))
        self.error.clear_error()
        self._update_dependent_fields()
        self._render()

    def _render_immediately(self) -> None:
        self._timer.stop()
        self._update_dependent_fields()
        self._render()

    def _force_generate(self) -> None:
        values = self._values()
        result, errors = self.service.force_render(self._current.template_id, values)
        self.code_view.setPlainText(result.content)
        self._forced = bool(errors)
        self.error.show_error("；".join(errors) if errors else "已强制生成当前配置。")

    def _render(self) -> None:
        values = self._values()
        errors = self.service.validate(self._current.template_id, values)
        if errors:
            self.error.show_error("；".join(errors))
            self.code_view.clear()
            return
        try:
            result = self.service.render(self._current.template_id, values)
        except ScriptError as exc:
            self.error.show_error(str(exc))
            self.code_view.clear()
            return
        self.error.clear_error()
        self._forced = False
        self.code_view.setPlainText(result.content)

    def _copy_code(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.generated_code())

    def save_preset(self) -> None:
        if self.db is None:
            self.error.show_error("数据库未开启，无法保存预设")
            return
        name = self._preset_edit_name
        if not name:
            name, accepted = QInputDialog.getText(self, "保存为预设", "预设名称")
            if not accepted:
                return
        try:
            if self._preset_edit_id is None:
                self.db.add_preset(
                    name,
                    self._current.template_id,
                    self._values(),
                    self.service.sensitive_fields(self._current.template_id),
                )
            else:
                self.db.update_preset(
                    self._preset_edit_id,
                    name=name,
                    values=self._values(),
                    sensitive_fields=self.service.sensitive_fields(self._current.template_id),
                )
        except ValueError as exc:
            self.error.show_error(str(exc))
            return
        self._preset_edit_id = None
        self._preset_edit_name = ""
        self._preset_feedback("预设已保存；密码不会写入数据库。")

    def load_preset(self) -> None:
        if self.db is None:
            self.error.show_error("数据库未开启，无法载入预设")
            return
        presets = self.db.list_presets()
        if not presets:
            self.error.show_error("暂无预设，请先保存。")
            return
        labels = [self._preset_label(preset) for preset in presets] + ["＋ 新建预设"]
        selected, accepted = QInputDialog.getItem(self, "载入预设", "选择预设", labels, 0, False)
        if not accepted:
            return
        index = labels.index(selected)
        if index == len(presets):
            name, accepted = QInputDialog.getText(self, "新建预设", "预设名称")
            if accepted and name.strip():
                self._preset_edit_name = name.strip()
                self.save_preset()
            return
        preset = presets[index]
        self._load_preset_values(preset)
        answer = QMessageBox.question(
            self,
            "编辑预设",
            "需要编辑并覆盖当前预设吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._preset_edit_id = preset.id
            self._preset_edit_name = preset.name
            self._preset_feedback("已进入编辑模式；密码需重新输入。")

    def _load_preset_values(self, preset) -> None:
        for name, value in preset.values.items():
            widget = self._fields.get(name)
            if widget is None:
                continue
            if name == "service_types":
                for check in widget.findChildren(QCheckBox):
                    check.setChecked(check.property("service_value") in value)
            elif isinstance(widget, QLineEdit):
                widget.setText(str(value))
            elif isinstance(widget, QSpinBox):
                widget.setValue(int(value))
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QComboBox):
                index = widget.findData(value)
                widget.setCurrentIndex(index if index >= 0 else widget.findText(str(value)))
        if "password" in preset.sensitive_fields:
            self._fields["password"].setText("h3cadmin")
        self._update_dependent_fields()
        self._render()

    def _preset_feedback(self, message: str) -> None:
        self.error.show_error(message)
        QTimer.singleShot(4000, self.error.clear_error)

    def rename_selected_preset(self) -> None:
        if self.db is None:
            return
        presets = self.db.list_presets()
        if not presets:
            self.error.show_error("暂无预设。")
            return
        labels = [self._preset_label(preset) for preset in presets]
        selected, accepted = QInputDialog.getItem(self, "重命名预设", "选择预设", labels, 0, False)
        if accepted:
            preset = presets[labels.index(selected)]
            name, accepted = QInputDialog.getText(self, "重命名预设", "预设名称", text=preset.name)
            if accepted:
                self.db.update_preset(
                    preset.id,
                    name=name,
                    values=preset.values,
                    sensitive_fields=preset.sensitive_fields,
                )

    def delete_selected_preset(self) -> None:
        if self.db is None:
            return
        presets = self.db.list_presets()
        if not presets:
            self.error.show_error("暂无预设。")
            return
        labels = [self._preset_label(preset) for preset in presets]
        selected, accepted = QInputDialog.getItem(self, "删除预设", "选择预设", labels, 0, False)
        if accepted:
            self.delete_preset(presets[labels.index(selected)].id)

    def delete_preset(self, preset_id: int) -> None:
        if self.db is None:
            return
        preset = self.db.get_preset(preset_id)
        if preset is None:
            return
        answer = QMessageBox.question(
            self,
            "删除预设",
            f"确定删除“{preset.name}”吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.db.delete_preset(preset_id)
            self._preset_feedback("预设已删除。")

    def _preset_label(self, preset) -> str:
        return f"{preset.name} · IP:<MGMT_IP> · 密码:<PASSWORD>"

    def generated_code(self) -> str:
        return self.code_view.toPlainText()

    def error_text(self) -> str:
        return self.error.text()

    def set_template(self, template_id: str) -> None:
        if template_id != self._current.template_id:
            raise ScriptError("当前生成器仅支持 H3C Comware V7 开局模板")

    def export_code(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出脚本", self._current.filename, "配置文件 (*.cfg *.txt)"
        )
        if path:
            Path(path).write_text(self.generated_code(), encoding="utf-8")
