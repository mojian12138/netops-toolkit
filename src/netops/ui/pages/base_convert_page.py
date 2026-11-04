"""IP 进制转换页面（calc.base）。

四栏联动：点分十进制、二进制、十六进制、十进制整数，任改一栏其余三栏实时刷新。
解析与格式化全部在 ``core/ipcalc.py``（``parse_ipv4`` / ``address_forms``），
本页只负责「谁在改 → 谁同步」的回路控制，避免互相触发形成死循环。
"""

from __future__ import annotations

from PyQt6.QtWidgets import QHBoxLayout, QLineEdit

from netops.core import ipcalc
from netops.ui.widgets.common import HLine, label
from netops.ui.widgets.forms import (
    FieldRow,
    InlineError,
    ResultGrid,
    SecondaryButton,
    ToolPage,
    make_line_edit,
    make_mono_line_edit,
    result_card,
)

_EXTRA_FIELDS: list[tuple[str, str]] = [
    ("点分十进制", "—"),
    ("二进制（分段）", "—"),
    ("二进制（连写）", "—"),
    ("十六进制", "—"),
    ("十六进制（无 0x）", "—"),
    ("十进制整数", "—"),
    ("大端字节序", "—"),
    ("地址类别", "—"),
]

_KIND_LABELS = {
    "private": "私有地址（RFC 1918）",
    "public": "公网地址",
    "loopback": "环回地址",
    "link-local": "链路本地地址",
    "multicast": "组播地址",
    "reserved": "保留地址",
}


class BaseConvertPage(ToolPage):
    def __init__(self, parent=None) -> None:
        super().__init__(
            title="IP 进制转换",
            subtitle="IP 在点分十进制、二进制、十六进制、整数之间双向实时转换，输入任意一栏其余同步。",
            parent=parent,
        )
        self._forms: ipcalc.AddressForms | None = None

        form = result_card("输入", "任改一栏，其余三栏实时联动")
        self.dotted = make_line_edit("例如 192.168.1.1")
        self.binary = make_mono_line_edit("例如 11000000.10101000.00000001.00000001")
        self.hex = make_mono_line_edit("例如 0xC0A80101 / C0A80101 / C0A8.0101")
        self.integer = make_mono_line_edit("例如 3232235777")
        self._edits: dict[str, QLineEdit] = {
            "dotted": self.dotted,
            "binary": self.binary,
            "hex": self.hex,
            "integer": self.integer,
        }
        form.body.addWidget(FieldRow("点分十进制", self.dotted, hint="每段 0-255"))
        form.body.addWidget(FieldRow("二进制", self.binary, hint="4 段×8 位"))
        form.body.addWidget(FieldRow("十六进制", self.hex, hint="带不带 0x 均可"))
        form.body.addWidget(FieldRow("十进制整数", self.integer, hint="0 - 4294967295"))
        self.error = InlineError()
        form.body.addWidget(self.error)
        for key, edit in self._edits.items():
            edit.textEdited.connect(lambda _text, source=key: self._on_edited(source))

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.clear_button = SecondaryButton("清空")
        self.clear_button.clicked.connect(self.clear)
        buttons.addWidget(self.clear_button)
        buttons.addStretch(1)
        form.body.addLayout(buttons)
        self.add_card(form)

        result = result_card("全部形态", "点击右侧「复制」即可写入剪贴板")
        self.results = ResultGrid(_EXTRA_FIELDS, columns=2)
        result.body.addWidget(self.results)
        result.body.addWidget(HLine())
        self.summary = label("等待输入", "EmptyHint")
        self.summary.setWordWrap(True)
        result.body.addWidget(self.summary)
        self.add_card(result, 1)

    # -- 交互 ---------------------------------------------------------------
    def _on_edited(self, source: str) -> None:
        self.error.clear_error()
        text = self._edits[source].text().strip()
        if not text:
            self._reset_outputs(keep=source)
            return
        try:
            forms = ipcalc.address_forms(text)
        except ipcalc.CalcError as exc:
            self.error.show_error(str(exc))
            self._reset_outputs(keep=source)
            return
        self._forms = forms
        for key, edit in self._edits.items():
            if key == source:
                continue
            edit.setText(_display(key, forms))
            edit.setCursorPosition(0)
        self._show_details(forms)

    def _reset_outputs(self, *, keep: str) -> None:
        self._forms = None
        for key, edit in self._edits.items():
            if key != keep:
                edit.clear()
        self.results.update_values({caption: "—" for caption, _ in _EXTRA_FIELDS})
        self.summary.setText("等待输入")

    def _show_details(self, forms: ipcalc.AddressForms) -> None:
        kind = _KIND_LABELS.get(forms.kind, forms.kind)
        self.results.update_values(
            {
                "点分十进制": forms.dotted,
                "二进制（分段）": forms.binary,
                "二进制（连写）": forms.binary_compact,
                "十六进制": forms.hex,
                "十六进制（无 0x）": forms.hex_bytes,
                "十进制整数": forms.integer,
                "大端字节序": forms.endian_swap,
                "地址类别": kind,
            }
        )
        self.summary.setText(f"{forms.dotted} = {forms.integer}（{forms.hex}），属于{kind}。")

    def clear(self) -> None:
        for edit in self._edits.values():
            edit.clear()
        self._forms = None
        self.error.clear_error()
        self.results.update_values({caption: "—" for caption, _ in _EXTRA_FIELDS})
        self.summary.setText("等待输入")

    # -- 对外接口 -----------------------------------------------------------
    def input_text(self, key: str) -> str:
        return self._edits[key].text()

    def set_input(self, key: str, text: str) -> None:
        """模拟用户在该栏输入（触发联动）。"""
        edit = self._edits[key]
        edit.setText(text)
        edit.textEdited.emit(text)

    def input_hint(self, key: str) -> str:
        return self._edits[key].placeholderText()

    def forms(self) -> ipcalc.AddressForms | None:
        return self._forms


def _display(key: str, forms: ipcalc.AddressForms) -> str:
    return {
        "dotted": forms.dotted,
        "binary": forms.binary,
        "hex": forms.hex,
        "integer": forms.integer,
    }[key]


__all__ = ["BaseConvertPage"]
