"""占位页：按 ToolPlugin 契约里的元数据渲染一份"高保真规划稿"。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from netops.ui.widgets.common import (
    Badge,
    Card,
    CodeBlock,
    HeroCard,
    MetricTile,
    Notice,
    label,
)

if TYPE_CHECKING:  # pragma: no cover
    from netops.core.context import AppContext
    from netops.core.contracts import ToolPlugin

__all__ = ["PlaceholderPage"]


class PlaceholderPage(QWidget):
    """所有 P0 页面的统一渲染器。

    只消费契约字段（title / subtitle / stage / features / preview_code），
    以后某个工具真正实现时，换掉 ``create_widget`` 即可，外壳不用动。
    """

    def __init__(
        self, plugin: type[ToolPlugin], ctx: AppContext, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Page")
        self.plugin = plugin
        self.ctx = ctx

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 22)
        root.setSpacing(16)

        preview_code = getattr(plugin, "preview_code", "")
        spec = getattr(plugin, "spec", []) or []

        top = QHBoxLayout()
        top.setSpacing(16)
        top.addWidget(
            HeroCard(
                title=plugin.title,
                subtitle=plugin.subtitle,
                stage_code=plugin.stage.code,
                stage_text=plugin.stage.text,
                features=plugin.features,
            ),
            3,
        )
        if preview_code:
            top.addWidget(
                CodeBlock(
                    preview_code,
                    filename=getattr(plugin, "preview_filename", "example.cfg"),
                    language=getattr(plugin, "preview_language", ""),
                ),
                2,
            )
        else:
            top.addWidget(self._roadmap_card(), 2)
        root.addLayout(top)

        lower = QHBoxLayout()
        lower.setSpacing(16)
        lower.addWidget(self._spec_card(spec), 2)
        lower.addWidget(self._status_card(), 1)
        root.addLayout(lower, 1)

        root.addWidget(
            Notice(
                "P0 阶段仅提供可视化外壳：本页所有控件均为规划示意，尚未接入真实探测/计算/存储逻辑。",
                muted=True,
            )
        )

    # -- 各区块 -------------------------------------------------------------
    def _roadmap_card(self) -> Card:
        card = Card(title="实现要点", hint="施工顺序")
        items = getattr(self.plugin, "roadmap", []) or ["等待排期"]
        for line in items:
            card.body.addWidget(self._bullet(line))
        card.body.addStretch(1)
        return card

    def _spec_card(self, spec: list[tuple[str, str]]) -> Card:
        card = Card(title="交互骨架", hint="键位与流程将在实现阶段落位")
        if not spec:
            card.body.addWidget(label("该页面暂无额外骨架说明。", "EmptyHint"))
            card.body.addStretch(1)
            return card
        for key, text in spec:
            row = QHBoxLayout()
            row.setSpacing(12)
            badge = Badge(key, "Badge")
            badge.setFixedWidth(96)
            row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
            lb = label(text, "FeatureRow")
            lb.setWordWrap(True)
            row.addWidget(lb, 1)
            card.body.addLayout(row)
        card.body.addStretch(1)
        return card

    def _status_card(self) -> Card:
        card = Card(title="当前状态", hint="P0")
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(MetricTile(self.plugin.stage.code, "规划阶段"))
        row.addWidget(MetricTile(str(len(self.plugin.features)), "核心特性"))
        row.addWidget(MetricTile("0%", "完成度"))
        card.body.addLayout(row)
        card.body.addWidget(
            Notice(f"归属分组：{self.plugin.group} · 注册 ID：{self.plugin.tool_id}", muted=True)
        )
        card.body.addStretch(1)
        return card

    @staticmethod
    def _bullet(text: str) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(9)
        dot = QFrame()
        dot.setFixedSize(5, 5)
        dot.setStyleSheet("background-color: #94A3B8; border-radius: 2px;")
        lay.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
        lb = label(text, "FeatureRow")
        lb.setWordWrap(True)
        lay.addWidget(lb, 1)
        return row

    # -- 测试辅助 ---------------------------------------------------------
    def code_views(self) -> list[CodeBlock]:
        """返回页面内所有深色代码预览，供冒烟测试断言。"""
        return self.findChildren(CodeBlock)
