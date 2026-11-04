"""IP 地址计算器页面（calc.ip）。

交互：输入 ``192.168.1.1/24`` 或地址 + 掩码，回车或点「计算」即时出结果；
所有结果行带复制按钮。计算全部复用 ``core/ipcalc.py``，页面不做算法。
"""

from __future__ import annotations

from PyQt6.QtWidgets import QHBoxLayout

from netops.core import ipcalc
from netops.ui.widgets.common import HLine, label
from netops.ui.widgets.forms import (
    FieldRow,
    InlineError,
    PrimaryButton,
    ResultGrid,
    SecondaryButton,
    ToolPage,
    make_combo,
    make_line_edit,
    result_card,
)

_FIELDS: list[tuple[str, str]] = [
    ("网络地址", "—"),
    ("广播地址", "—"),
    ("可用范围", "—"),
    ("子网掩码", "—"),
    ("通配符掩码", "—"),
    ("主机总数", "—"),
    ("可用主机数", "—"),
    ("地址类别", "—"),
]


class IpCalcPage(ToolPage):
    def __init__(self, parent=None) -> None:
        super().__init__(
            title="IP 地址计算器",
            subtitle="输入 IPv4 地址与掩码，立即得到网络地址、广播地址、可用范围与主机数量。",
            parent=parent,
        )

        form = result_card("输入", "支持 CIDR 或点分掩码")
        self.address = make_line_edit("例如 192.168.10.77/24")
        self.mask_mode = make_combo(["跟随 CIDR 输入", "单独指定掩码"])
        self.mask = make_line_edit("例如 255.255.255.0 或 24")
        self.mask.setEnabled(False)
        self.mask_mode.currentIndexChanged.connect(self._on_mask_mode)
        self.error = InlineError()

        form.body.addWidget(FieldRow("IP 地址", self.address, hint="回车即可计算"))
        form.body.addWidget(FieldRow("掩码方式", self.mask_mode))
        form.body.addWidget(FieldRow("掩码", self.mask))
        form.body.addWidget(self.error)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.calc_button = PrimaryButton("计算")
        self.clear_button = SecondaryButton("清空")
        self.calc_button.clicked.connect(self.calculate)
        self.clear_button.clicked.connect(self.clear)
        buttons.addWidget(self.calc_button)
        buttons.addWidget(self.clear_button)
        buttons.addStretch(1)
        form.body.addLayout(buttons)
        self.add_card(form)

        result = result_card("计算结果", "点击右侧「复制」即可写入剪贴板")
        self.results = ResultGrid(_FIELDS, columns=2)
        result.body.addWidget(self.results)
        result.body.addWidget(HLine())
        self.summary = label("等待输入", "EmptyHint")
        self.summary.setWordWrap(True)
        result.body.addWidget(self.summary)
        self.add_card(result, 1)

        self.address.returnPressed.connect(self.calculate)
        self.mask.returnPressed.connect(self.calculate)
        self.address.textChanged.connect(self._clear_error_on_edit)
        self.mask.textChanged.connect(self._clear_error_on_edit)

    # -- 交互 ---------------------------------------------------------------
    def _on_mask_mode(self, index: int) -> None:
        self.mask.setEnabled(index == 1)
        if index == 0:
            self.mask.clear()

    def _clear_error_on_edit(self) -> None:
        self.error.clear_error()

    def clear(self) -> None:
        self.address.clear()
        self.mask.clear()
        self.mask_mode.setCurrentIndex(0)
        self.error.clear_error()
        self.results.update_values({caption: "—" for caption, _ in _FIELDS})
        self.summary.setText("等待输入")

    def calculate(self) -> ipcalc.NetworkInfo | None:
        self.error.clear_error()
        text = self.address.text().strip()
        if not text:
            self.error.show_error("请输入 IP 地址或 CIDR，例如 192.168.10.77/24")
            return None
        mask = self.mask.text().strip() if self.mask_mode.currentIndex() == 1 else None
        if self.mask_mode.currentIndex() == 1 and not mask:
            self.error.show_error("已选择单独指定掩码，请填写掩码")
            return None
        try:
            info = ipcalc.network_info(text, mask)
        except ipcalc.CalcError as exc:
            self.error.show_error(str(exc))
            return None
        except ValueError as exc:
            self.error.show_error(f"非法输入：{exc}")
            return None
        self._show(info)
        return info

    def _show(self, info: ipcalc.NetworkInfo) -> None:
        self.results.update_values(
            {
                "网络地址": info.network,
                "广播地址": info.broadcast,
                "可用范围": info.host_range,
                "子网掩码": info.mask,
                "通配符掩码": info.wildcard,
                "主机总数": f"{info.total_addresses:,}",
                "可用主机数": f"{info.usable_hosts:,}",
                "地址类别": _KIND_LABELS.get(info.kind, info.kind),
            }
        )
        self.summary.setText(
            f"{info.address} 属于 {info.cidr}：{_KIND_LABELS.get(info.kind, info.kind)}，"
            f"可用主机 {info.usable_hosts:,} 个。"
        )


_KIND_LABELS = {
    "private": "私有地址（RFC 1918）",
    "public": "公网地址",
    "loopback": "环回地址",
    "link-local": "链路本地地址",
    "multicast": "组播地址",
    "reserved": "保留地址",
}
