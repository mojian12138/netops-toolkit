"""Schema-driven Huawei/H3C script generator with local favorites."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QSplitter,
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
    copy_to_clipboard,
    make_line_edit,
    result_card,
)


class ScriptPage(ToolPage):
    def __init__(
        self, service: ScriptService | None = None, db: Database | None = None, parent=None
    ) -> None:
        super().__init__(
            title="脚本生成器",
            subtitle="选择厂商与脚本模板，填写参数后生成可直接粘贴的配置。",
            parent=parent,
        )
        self.service = service or ScriptService()
        self.db = db
        self._current = None
        self._fields: dict[str, QLineEdit] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._render)
        self._build()
        self._load_templates()

    def _build(self) -> None:
        top = result_card("模板选择", "按厂商与分类筛选脚本模板")
        row = QHBoxLayout()
        self.vendor_combo = QComboBox()
        self.category_combo = QComboBox()
        self.template_combo = QComboBox()
        for combo in (self.vendor_combo, self.category_combo, self.template_combo):
            combo.setObjectName("ToolInput")
        row.addWidget(QLabel("厂商"))
        row.addWidget(self.vendor_combo, 1)
        row.addWidget(QLabel("分类"))
        row.addWidget(self.category_combo, 1)
        row.addWidget(QLabel("模板"))
        row.addWidget(self.template_combo, 2)
        top.body.addLayout(row)
        self.add_card(top)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        form_card = result_card("参数", "修改参数后自动刷新预览")
        self.form_host = QWidget()
        self.form_layout = QFormLayout(self.form_host)
        self.form_layout.setContentsMargins(0, 0, 0, 0)
        form_card.body.addWidget(self.form_host)
        self.error = InlineError()
        form_card.body.addWidget(self.error)
        splitter.addWidget(form_card)

        code_card = result_card("代码预览", "代码可直接编辑，收藏保存当前内容")
        self.code_view = QPlainTextEdit()
        self.code_view.setObjectName("ToolInputMono")
        self.code_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        code_card.body.addWidget(self.code_view, 1)
        buttons = QHBoxLayout()
        self.generate_btn = PrimaryButton("重新生成")
        self.copy_btn = SecondaryButton("复制")
        self.save_btn = SecondaryButton("收藏")
        self.export_btn = SecondaryButton("导出")
        for button in (self.generate_btn, self.copy_btn, self.save_btn, self.export_btn):
            buttons.addWidget(button)
        buttons.addStretch(1)
        code_card.body.addLayout(buttons)
        splitter.addWidget(code_card)
        splitter.setStretchFactor(1, 2)
        self.add_card(splitter, 1)

        fav_card = result_card("收藏库", "关键词检索；双击载入")
        fav_row = QHBoxLayout()
        self.favorite_search = make_line_edit("名称、内容或标签")
        self.favorite_search.textChanged.connect(self._refresh_favorites)
        self.rename_btn = SecondaryButton("重命名")
        self.delete_btn = SecondaryButton("删除")
        fav_row.addWidget(self.favorite_search, 1)
        fav_row.addWidget(self.rename_btn)
        fav_row.addWidget(self.delete_btn)
        fav_card.body.addLayout(fav_row)
        self.favorite_list = QListWidget()
        self.favorite_list.itemDoubleClicked.connect(self._load_favorite)
        fav_card.body.addWidget(self.favorite_list)
        self.add_card(fav_card)

        self.vendor_combo.currentTextChanged.connect(self._load_categories)
        self.category_combo.currentTextChanged.connect(self._load_template_items)
        self.template_combo.currentIndexChanged.connect(self._select_template)
        self.generate_btn.clicked.connect(self._render)
        self.copy_btn.clicked.connect(lambda: copy_to_clipboard(self.code_view.toPlainText()))
        self.save_btn.clicked.connect(self.save_favorite)
        self.export_btn.clicked.connect(self.export_code)
        self.rename_btn.clicked.connect(self.rename_favorite)
        self.delete_btn.clicked.connect(self.delete_favorite)

    def _load_templates(self) -> None:
        vendors = sorted({item.vendor for item in self.service.templates()})
        self.vendor_combo.addItems(vendors)
        self._load_categories()

    def _load_categories(self) -> None:
        vendor = self.vendor_combo.currentText()
        cats = sorted({item.category for item in self.service.templates() if item.vendor == vendor})
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItems(cats)
        self.category_combo.blockSignals(False)
        self._load_template_items()

    def _load_template_items(self) -> None:
        vendor, category = self.vendor_combo.currentText(), self.category_combo.currentText()
        items = [
            x for x in self.service.templates() if x.vendor == vendor and x.category == category
        ]
        self.template_combo.blockSignals(True)
        self.template_combo.clear()
        for item in items:
            self.template_combo.addItem(item.title, item.template_id)
        self.template_combo.blockSignals(False)
        self._select_template()

    def _select_template(self) -> None:
        template_id = self.template_combo.currentData()
        if not template_id:
            return
        self._current = self.service.registry.get(template_id)
        while self.form_layout.rowCount():
            self.form_layout.removeRow(0)
        self._fields.clear()
        for field in self._current.fields:
            edit = make_line_edit()
            edit.setText(str(field.get("default", "")))
            edit.setPlaceholderText(field.get("title", field["name"]))
            edit.textChanged.connect(lambda _text: self._timer.start())
            self._fields[field["name"]] = edit
            self.form_layout.addRow(FieldRow(field.get("title", field["name"]), edit))
        self._render()

    def _values(self) -> dict[str, str]:
        return {name: widget.text().strip() for name, widget in self._fields.items()}

    def _render(self) -> None:
        if self._current is None:
            return
        values = self._values()
        errors = self.service.validate(self._current.template_id, values)
        if errors:
            self.error.show_error("；".join(errors))
            return
        try:
            result = self.service.render(self._current.template_id, values)
        except ScriptError as exc:
            self.error.show_error(str(exc))
            return
        self.error.clear_error()
        self.code_view.setPlainText(result.content)

    def set_template(self, template_id: str) -> None:
        template = self.service.registry.get(template_id)
        vendor_index = self.vendor_combo.findText(template.vendor)
        if vendor_index >= 0:
            self.vendor_combo.setCurrentIndex(vendor_index)
        category_index = self.category_combo.findText(template.category)
        if category_index >= 0:
            self.category_combo.setCurrentIndex(category_index)
        index = self.template_combo.findData(template_id)
        if index >= 0:
            self.template_combo.setCurrentIndex(index)

    def generated_code(self) -> str:
        return self.code_view.toPlainText()

    def error_text(self) -> str:
        return self.error.text()

    def save_favorite(self) -> None:
        if self.db is None:
            self.error.show_error("数据库未开启，无法收藏")
            return
        name = self._current.title if self._current else "自定义脚本"
        self.db.add_favorite(
            name,
            self.generated_code(),
            vendor=self.vendor_combo.currentText(),
            category=self.category_combo.currentText(),
        )
        self._refresh_favorites()

    def _refresh_favorites(self) -> None:
        self.favorite_list.clear()
        if self.db is None:
            return
        for favorite in self.db.list_favorites(keyword=self.favorite_search.text().strip()):
            item = QListWidgetItem(f"{favorite.name} · {favorite.vendor} / {favorite.category}")
            item.setData(Qt.ItemDataRole.UserRole, favorite.id)
            self.favorite_list.addItem(item)

    def _load_favorite(self, item: QListWidgetItem) -> None:
        if self.db is None:
            return
        favorite = self.db.get_favorite(int(item.data(Qt.ItemDataRole.UserRole)))
        if favorite:
            self.code_view.setPlainText(favorite.content)

    def rename_favorite(self) -> None:
        if self.db is None or self.favorite_list.currentItem() is None:
            return
        favorite_id = int(self.favorite_list.currentItem().data(Qt.ItemDataRole.UserRole))
        self.db.update_favorite(
            favorite_id,
            name=f"{self.favorite_list.currentItem().text().split(' · ', 1)[0]}（已编辑）",
        )
        self._refresh_favorites()

    def delete_favorite(self) -> None:
        if self.db is None or self.favorite_list.currentItem() is None:
            return
        self.db.delete_favorite(
            int(self.favorite_list.currentItem().data(Qt.ItemDataRole.UserRole))
        )
        self._refresh_favorites()

    def export_code(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出脚本",
            self._current.filename if self._current else "script.cfg",
            "配置文件 (*.cfg *.txt)",
        )
        if path:
            Path(path).write_text(self.generated_code(), encoding="utf-8")
