"""MAC 地址格式转换页面（conv.mac）。

单条或批量输入任意一种 MAC 写法，实时给出九种常见格式 + OUI 厂商说明。
解析与格式化全部在 ``core/mac.py``，本页只负责录入、展示与复制。
"""

from __future__ import annotations

import csv
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
)

from netops.core import mac
from netops.ui.widgets.common import label
from netops.ui.widgets.forms import (
    FieldRow,
    InlineError,
    PrimaryButton,
    ResultGrid,
    SecondaryButton,
    ToolPage,
    copy_to_clipboard,
    make_line_edit,
    make_text_edit,
    result_card,
)

#: 批量模式一次最多转换多少行，防止误粘贴整份 ARP 表把界面卡住。
MAX_BATCH = 2000

#: 输入停顿多久后再转换（毫秒）——批量粘贴时不逐键计算。
_DEBOUNCE_MS = 120

_FORM_FIELDS: list[tuple[str, str]] = [
    ("思科 / H3C 接口", "—"),
    ("华为 / H3C display", "—"),
    ("Windows ipconfig", "—"),
    ("getmac / 注册表", "—"),
    ("Linux / Wireshark", "—"),
    ("华为部分输出", "—"),
    ("空格分隔", "—"),
    ("无分隔", "—"),
    ("十六进制", "—"),
]

_BATCH_COLUMNS = ["原文", "规范化", "格式", "OUI", "厂商 / 说明"]


class MacPage(ToolPage):
    def __init__(self, parent=None) -> None:
        super().__init__(
            title="MAC 地址格式转换",
            subtitle="网络工程里常见的各种 MAC 写法自由互转，批量转换 + OUI 厂商查询一步到位。",
            parent=parent,
        )
        self._forms: mac.MacForms | None = None
        self._rows: list[tuple[str, str, str, str, str]] = []

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_DEBOUNCE_MS)
        self._timer.timeout.connect(self._on_timeout)

        form = result_card("单条转换", "输入任意一种写法，其余格式实时刷新")
        self.input = make_line_edit("例如 0011.6c0a.1b2c / 00-11-6C-0A-1B-2C / 0x00116c0a1b2c")
        self.error = InlineError()
        self.notes = label("等待输入", "EmptyHint")
        self.notes.setWordWrap(True)
        form.body.addWidget(FieldRow("MAC 地址", self.input, label_width=80))
        form.body.addWidget(self.error)

        vendor_row = QHBoxLayout()
        vendor_row.setSpacing(10)
        name = label("OUI 厂商", "FieldLabel")
        name.setFixedWidth(80)
        name.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        vendor_row.addWidget(name)
        self.vendor = label("—", "ResultMono")
        self.vendor.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        vendor_row.addWidget(self.vendor, 1)
        form.body.addLayout(vendor_row)
        form.body.addWidget(self.notes)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.copy_cisco = PrimaryButton("复制思科格式")
        self.copy_cisco.clicked.connect(lambda: self._copy(self.input_form("cisco")))
        self.clear_button = SecondaryButton("清空")
        self.clear_button.clicked.connect(self.clear)
        buttons.addWidget(self.copy_cisco)
        buttons.addWidget(self.clear_button)
        buttons.addStretch(1)
        form.body.addLayout(buttons)
        self.input.textEdited.connect(lambda _text: self._schedule())
        self.add_card(form)

        result = result_card("全部写法", "点击右侧「复制」即可写入剪贴板")
        self.results = ResultGrid(_FORM_FIELDS, columns=3)
        result.body.addWidget(self.results)
        self.add_card(result)

        batch = result_card("批量转换", "每行一个；# 开头为注释，非法行逐条标出")
        self.batch_input = make_text_edit(
            "一大段贴进来即可，例如：\n0011.6c0a.1b2c\n00-11-6C-0A-1B-2C\n02:00:4c:12:34:56",
            rows=5,
        )
        batch.body.addWidget(self.batch_input)
        batch_buttons = QHBoxLayout()
        batch_buttons.setSpacing(8)
        self.convert_button = PrimaryButton("批量转换")
        self.convert_button.clicked.connect(self.convert_batch)
        self.copy_column_button = SecondaryButton("复制「规范化」列")
        self.copy_column_button.clicked.connect(self._copy_plain_column)
        self.copy_column_button.setEnabled(False)
        self.export_button = SecondaryButton("导出 CSV")
        self.export_button.clicked.connect(self._choose_export)
        self.export_button.setEnabled(False)
        self.clear_batch_button = SecondaryButton("清空")
        self.clear_batch_button.clicked.connect(self.clear_batch)
        batch_buttons.addWidget(self.convert_button)
        batch_buttons.addWidget(self.copy_column_button)
        batch_buttons.addWidget(self.export_button)
        batch_buttons.addStretch(1)
        batch_buttons.addWidget(self.clear_batch_button)
        batch.body.addLayout(batch_buttons)

        self.batch_error = InlineError()
        self.batch_summary = label("等待输入", "EmptyHint")
        batch.body.addWidget(self.batch_error)
        batch.body.addWidget(self.batch_summary)

        self.table = QTableWidget(0, len(_BATCH_COLUMNS))
        self.table.setObjectName("ResultTable")
        self.table.setHorizontalHeaderLabels(_BATCH_COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for column in (0, 1):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        for column in (2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(200)
        batch.body.addWidget(self.table, 1)
        self.add_card(batch, 1)
        self.batch_input.textChanged.connect(self._on_batch_changed)

    # -- 单条 ---------------------------------------------------------------
    def _schedule(self) -> None:
        self._timer.start()

    def _on_timeout(self) -> None:
        self.convert_text(self.input.text())

    def convert_text(self, text: str) -> mac.MacForms | None:
        """解析并展示；非法输入返回 ``None`` 并在页面提示。"""
        self.error.clear_error()
        if not text.strip():
            self._reset_single()
            return None
        try:
            forms = mac.mac_forms(text)
        except mac.MacError as exc:
            self._reset_single(clear_error=False)
            self.error.show_error(str(exc))
            return None
        notes = mac.describe_mac(forms)
        self._forms = forms
        self.results.update_values(
            {
                "思科 / H3C 接口": forms.cisco,
                "华为 / H3C display": forms.huawei,
                "Windows ipconfig": forms.hyphen,
                "getmac / 注册表": forms.windows,
                "Linux / Wireshark": forms.colon,
                "华为部分输出": forms.colon_upper,
                "空格分隔": forms.spaced,
                "无分隔": forms.plain,
                "十六进制": forms.prefixed,
            }
        )
        vendor = mac.lookup_oui(forms.oui)
        self.vendor.setText(f"{forms.oui_bytes}  {vendor or '本地库未收录'}")
        self.notes.setText("；".join(notes) + "。")
        return forms

    def _reset_single(self, *, clear_error: bool = True) -> None:
        self._forms = None
        if clear_error:
            self.error.clear_error()
        self.results.update_values({caption: "—" for caption, _ in _FORM_FIELDS})
        self.vendor.setText("—")
        self.notes.setText("等待输入")

    def clear(self) -> None:
        self.input.clear()
        self._reset_single()

    # -- 批量 ---------------------------------------------------------------
    def _on_batch_changed(self) -> None:
        if self._rows:
            self.batch_summary.setText("输入已变化，点击「批量转换」重新计算。")

    def convert_batch(self) -> list[tuple[str, str, str, str, str]] | None:
        lines = _batch_lines(self.batch_input.toPlainText())
        self.batch_error.clear_error()
        if not lines:
            self._reset_batch()
            self.batch_error.show_error("请至少输入一个 MAC 地址")
            return None
        if len(lines) > MAX_BATCH:
            self.batch_error.show_error(
                f"共 {len(lines):,} 行，超过单次上限 {MAX_BATCH:,} 行，请分批转换"
            )
            return None

        rows: list[tuple[str, str, str, str, str]] = []
        bad: list[str] = []
        for line in lines:
            try:
                forms = mac.mac_forms(line)
            except mac.MacError:
                bad.append(line)
                rows.append((line, "—", "非法", "—", "无法解析"))
                continue
            vendor = mac.lookup_oui(forms.oui)
            is_local = bool((forms.value >> 40) & 0x02)
            if is_local:
                vendor = "本地管理（厂商自定义）"
            elif not vendor:
                vendor = "本地库未收录"
            rows.append((line, forms.plain, _guess_format(line), forms.oui_bytes, vendor))

        self._rows = rows
        self.table.setRowCount(len(rows))
        for index, values in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if values[2] == "非法":
                    item.setForeground(Qt.GlobalColor.red)
                self.table.setItem(index, column, item)

        ok = len(rows) - len(bad)
        message = f"共 {len(rows)} 行，转换成功 {ok} 行"
        if bad:
            message += f"，非法 {len(bad)} 行（已标红）"
        self.batch_summary.setText(message + "。")
        self.export_button.setEnabled(True)
        self.copy_column_button.setEnabled(ok > 0)
        return rows

    def _reset_batch(self) -> None:
        self._rows = []
        self.table.setRowCount(0)
        self.batch_summary.setText("等待输入")
        self.export_button.setEnabled(False)
        self.copy_column_button.setEnabled(False)

    def clear_batch(self) -> None:
        self.batch_input.clear()
        self.batch_error.clear_error()
        self._reset_batch()

    def _copy_plain_column(self) -> None:
        values = [row[1] for row in self._rows if row[2] != "非法"]
        if values:
            copy_to_clipboard("\n".join(values))

    # -- 复制 / 导出 --------------------------------------------------------
    def _copy(self, text: str) -> None:
        copy_to_clipboard(text)

    def _choose_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "导出 CSV", "mac-list.csv", "CSV 文件 (*.csv)")
        if path:
            self.export_csv(path)

    def export_csv(self, path: str | Path) -> int:
        if not self._rows:
            return 0
        with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(_BATCH_COLUMNS)
            writer.writerows(self._rows)
        return len(self._rows)

    # -- 对外接口 -----------------------------------------------------------
    def set_input(self, text: str) -> mac.MacForms | None:
        """模拟用户输入（立即转换，不等待防抖）。"""
        self.input.setText(text)
        return self.convert_text(text)

    def input_form(self, key: str) -> str:
        """取某个格式的结果文本；无结果时返回空串。"""
        if self._forms is None:
            return ""
        return str(getattr(self._forms, key))

    def forms(self) -> mac.MacForms | None:
        return self._forms

    def result_value(self, caption: str) -> str:
        return self.results.value(caption)

    def vendor_text(self) -> str:
        return self.vendor.text()

    def notes_text(self) -> str:
        return self.notes.text()

    def error_text(self) -> str:
        return self.error.text()

    def batch_rows(self) -> list[tuple[str, str, str, str, str]]:
        return list(self._rows)

    def batch_summary_text(self) -> str:
        return self.batch_summary.text()


def _batch_lines(text: str) -> list[str]:
    lines: list[str] = []
    for line in text.splitlines():
        value = line.split("#", 1)[0].strip()
        if value:
            # 兼容一行贴多个（逗号 / 分号 / 制表符分隔）
            for part in value.replace(";", ",").replace("\t", ",").split(","):
                if part.strip():
                    lines.append(part.strip())
    return lines


def _guess_format(text: str) -> str:
    """按输入的分隔符猜它原本是哪种设备的写法，便于在 ARP 表里对照。"""
    body = text.strip()
    if body.lower().startswith("0x"):
        return "十六进制（0x 前缀）"
    if "." in body:
        return "思科 / H3C 接口"
    if " " in body:
        return "空格分隔"
    if ":" in body:
        return "Linux / Wireshark"
    if "-" in body:
        pairs = body.split("-")
        return "华为 / H3C display" if all(len(pair) == 4 for pair in pairs) else "Windows"
    return "无分隔"


# 保留给测试与外部调用的剪贴板读取入口（Qt 剪贴板在 offscreen 下同样可用）。
def clipboard_text() -> str:
    clipboard = QGuiApplication.clipboard()
    return clipboard.text() if clipboard is not None else ""


__all__ = ["MacPage", "MAX_BATCH", "clipboard_text"]
