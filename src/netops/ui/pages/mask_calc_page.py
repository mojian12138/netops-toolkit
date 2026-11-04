"""子网掩码计算器页面（calc.mask）。

三种入口：直接给掩码、按每个子网的主机数反推、按需要的子网数量反推。
结果区同时给出点分、二进制、十六进制三种形态，并用 32 位位图展示网络位/主机位。
算法全部在 ``core/ipcalc.py``，本页只做取值、校验与展示。
"""

from __future__ import annotations

from PyQt6.QtWidgets import QHBoxLayout, QSpinBox

from netops.core import ipcalc
from netops.ui.widgets.bits import MaskBitmap
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
    ("前缀长度", "—"),
    ("子网掩码", "—"),
    ("通配符掩码", "—"),
    ("二进制", "—"),
    ("十六进制", "—"),
    ("每子网可用主机", "—"),
    ("基准网段内子网数", "—"),
]

_MODE_MASK = 0
_MODE_HOSTS = 1
_MODE_SUBNETS = 2


def _spin(placeholder: str, minimum: int, maximum: int, value: int) -> QSpinBox:
    box = QSpinBox()
    box.setObjectName("ToolInput")
    box.setRange(minimum, maximum)
    box.setValue(value)
    box.setSpecialValueText(placeholder)
    return box


class MaskCalcPage(ToolPage):
    def __init__(self, parent=None) -> None:
        super().__init__(
            title="子网掩码计算器",
            subtitle="按掩码、按主机数或按子网数量反推掩码，并对照展示二进制与十六进制形态。",
            parent=parent,
        )

        form = result_card("输入", "三种模式任选其一")
        self.mode = make_combo(["按掩码计算", "按主机数反推", "按子网数量反推"])
        self.mask = make_line_edit("例如 255.255.255.0 或 24")
        self.hosts = _spin("请输入主机数", 1, 16_777_214, 254)
        self.hosts.setEnabled(False)
        self.subnets = _spin("请输入子网数量", 1, 1 << 20, 4)
        self.subnets.setEnabled(False)
        self.base = make_line_edit("可选，例如 10.0.0.0/16")
        self.base.setEnabled(False)
        self.mode.currentIndexChanged.connect(self._on_mode_changed)
        self.error = InlineError()

        form.body.addWidget(FieldRow("计算模式", self.mode))
        form.body.addWidget(FieldRow("掩码", self.mask, hint="支持 /24、24、点分三种写法"))
        form.body.addWidget(FieldRow("每子网主机数", self.hosts))
        form.body.addWidget(FieldRow("子网数量", self.subnets))
        form.body.addWidget(FieldRow("基准网段", self.base, hint="填写后可算可划分子网数"))
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

        result = result_card("掩码详情", "网络位为实心格，主机位为浅色格")
        self.bitmap = MaskBitmap(prefix=24)
        result.body.addWidget(self.bitmap)
        result.body.addWidget(HLine())
        self.results = ResultGrid(_FIELDS, columns=2)
        result.body.addWidget(self.results)
        result.body.addWidget(HLine())
        self.summary = label("等待输入", "EmptyHint")
        self.summary.setWordWrap(True)
        result.body.addWidget(self.summary)
        self.add_card(result, 1)

        self.mask.returnPressed.connect(self.calculate)
        self.base.returnPressed.connect(self.calculate)
        self.hosts.valueChanged.connect(self._clear_error_on_edit)
        self.subnets.valueChanged.connect(self._clear_error_on_edit)
        self.mask.textChanged.connect(self._clear_error_on_edit)
        self.base.textChanged.connect(self._clear_error_on_edit)

    # -- 交互 ---------------------------------------------------------------
    def _on_mode_changed(self, index: int) -> None:
        self.mask.setEnabled(index == _MODE_MASK)
        self.hosts.setEnabled(index == _MODE_HOSTS)
        self.subnets.setEnabled(index == _MODE_SUBNETS)
        self.base.setEnabled(index != _MODE_MASK)
        if index == _MODE_MASK:
            self.base.clear()
        self.error.clear_error()

    def _clear_error_on_edit(self) -> None:
        self.error.clear_error()

    def clear(self) -> None:
        self.mode.setCurrentIndex(_MODE_MASK)
        self.mask.clear()
        self.base.clear()
        self.hosts.setValue(254)
        self.subnets.setValue(4)
        self.error.clear_error()
        self.bitmap.set_prefix(24)
        self.results.update_values({caption: "—" for caption, _ in _FIELDS})
        self.summary.setText("等待输入")

    def calculate(self) -> int | None:
        """计算并展示；成功返回前缀长度，失败返回 ``None`` 并显示错误。"""
        self.error.clear_error()
        try:
            prefix, base_info = self._resolve_prefix()
        except ipcalc.CalcError as exc:
            self.error.show_error(str(exc))
            return None
        except ValueError as exc:
            self.error.show_error(f"非法输入：{exc}")
            return None
        self._show(prefix, base_info)
        return prefix

    def _resolve_prefix(self) -> tuple[int, ipcalc.NetworkInfo | None]:
        index = self.mode.currentIndex()
        base_text = self.base.text().strip()
        base_info = ipcalc.network_info(base_text) if base_text else None

        if index == _MODE_MASK:
            text = self.mask.text().strip()
            if not text:
                raise ipcalc.CalcError("请输入掩码，例如 255.255.255.0 或 /24")
            return ipcalc.parse_mask(text), base_info

        if index == _MODE_HOSTS:
            prefix = ipcalc.prefix_for_hosts(self.hosts.value())
        else:
            if base_info is None:
                raise ipcalc.CalcError("按子网数量反推需要填写基准网段，例如 10.0.0.0/16")
            prefix = ipcalc.prefix_for_subnets(base_info.prefix, self.subnets.value())

        if base_info is not None and prefix < base_info.prefix:
            raise ipcalc.CalcError(f"该需求超过基准网段 {base_info.cidr} 的容量（需要 /{prefix}）")
        return prefix, base_info

    def _show(self, prefix: int, base_info: ipcalc.NetworkInfo | None) -> None:
        hosts = ipcalc.hosts_for_prefix(prefix)
        subnets = f"{1 << (prefix - base_info.prefix):,}" if base_info is not None else "—"
        self.bitmap.set_prefix(prefix)
        self.results.update_values(
            {
                "前缀长度": f"/{prefix}",
                "子网掩码": ipcalc.prefix_to_mask(prefix),
                "通配符掩码": ipcalc.wildcard_mask(prefix),
                "二进制": ipcalc.mask_to_binary(prefix),
                "十六进制": ipcalc.mask_to_hex(prefix),
                "每子网可用主机": f"{hosts:,}",
                "基准网段内子网数": subnets,
            }
        )
        message = f"/{prefix} 每个子网可容纳 {hosts:,} 台主机"
        if base_info is not None:
            message += f"，{base_info.cidr} 可划分为 {subnets} 个这样的子网"
        self.summary.setText(message + "。")
