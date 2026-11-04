"""路由追踪页面（diag.traceroute）。

三块：目标与参数 → 路径时间轴 → 逐跳明细表。
追踪逻辑全在 ``services/traceroute.py``，备注读写在 ``services/devices.py``，
本页只做录入、结果展示与导出；追踪是**阻塞子进程调用**，走 ``TaskRunner``，
UI 线程不阻塞。

注意：``traceroute`` 没有增量输出（进程结束才有完整 stdout），所以进度条只能
转圈，不能按跳推进。
"""

from __future__ import annotations

import csv
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
)

from netops.services import traceroute as tr_service
from netops.services.devices import DeviceBook, DeviceBookError
from netops.ui.async_bridge import TaskHandle, TaskRunner
from netops.ui.widgets.common import label
from netops.ui.widgets.forms import (
    FieldRow,
    InlineError,
    PrimaryButton,
    SecondaryButton,
    ToolPage,
    make_combo,
    make_line_edit,
    result_card,
    separator_line,
)
from netops.ui.widgets.timeline import HopTimeline

_COLUMNS = ["跳", "地址", "延迟 (ms)", "设备备注"]

_DASH = "—"

#: 超时行的文字颜色，与时间轴的红色圈一致。
_TIMEOUT_COLOR = "#dc2626"

#: 超时档位与 ``tr_service.TIMEOUT_PRESETS`` 顺序一致。
_TIMEOUT_LABELS = ["1 秒", "2 秒", "3 秒", "5 秒"]

#: 进程超时：比「最大跳数 × 单探针等待」再留一点余量，防止卡死。
_PROCESS_TIMEOUT_BASE_S = 15.0


class TraceroutePage(ToolPage):
    def __init__(self, parent=None, *, book: DeviceBook | None = None) -> None:
        super().__init__(
            title="路由追踪",
            subtitle="调用系统 traceroute 逐跳探测，自动匹配设备备注库，一眼看出每一跳是哪台设备。",
            parent=parent,
        )
        self._runner = TaskRunner(self, max_threads=2)
        self._handle: TaskHandle | None = None
        self._state = "idle"
        self._stop_requested = False
        self._report: tr_service.TracerouteReport | None = None
        self._started: datetime | None = None
        self._monotonic0 = 0.0
        self._book = book or DeviceBook(None)

        #: 测试可注入假 runner。
        self.runner_factory: Callable[..., object] = tr_service.run_traceroute

        self._ticker = QTimer(self)
        self._ticker.setInterval(250)
        self._ticker.timeout.connect(self._on_tick)

        self._build_input_card()
        self._build_path_card()
        self._build_table_card()
        self._set_state("idle")

    # -- 对外：备注簿 --------------------------------------------------------
    def set_device_book(self, book: DeviceBook) -> None:
        """由 ``create_widget`` 注入真实备注簿；单测可换成 ``DeviceBook(None)``。"""
        self._book = book

    # -- 界面搭建 -----------------------------------------------------------
    def _build_input_card(self) -> None:
        card = result_card("追踪参数", "支持 IPv4 地址或主机名；IPv6 暂不支持")
        self.target_input = make_line_edit("例如 8.8.8.8 或 www.example.com")
        card.body.addWidget(FieldRow("目标", self.target_input, label_width=64))

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)

        self.hops_spin = QSpinBox()
        self.hops_spin.setObjectName("ToolInput")
        self.hops_spin.setRange(1, tr_service.MAX_HOPS)
        self.hops_spin.setValue(tr_service.DEFAULT_HOPS)
        grid.addWidget(FieldRow("最大跳数", self.hops_spin, hint="上限 30", label_width=64), 0, 0)

        self.timeout_combo = make_combo(_TIMEOUT_LABELS)
        self.timeout_combo.setCurrentIndex(1 if tr_service.DEFAULT_TIMEOUT_S == 2.0 else 0)
        grid.addWidget(FieldRow("单跳等待", self.timeout_combo, label_width=64), 0, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        card.body.addLayout(grid)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.start_button = PrimaryButton("开始追踪")
        self.start_button.clicked.connect(self.start)
        self.stop_button = SecondaryButton("停止")
        self.stop_button.clicked.connect(self.stop)
        self.clear_button = SecondaryButton("清空结果")
        self.clear_button.clicked.connect(self.clear_results)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        buttons.addWidget(self.clear_button)
        buttons.addStretch(1)
        card.body.addLayout(buttons)

        self.error = InlineError()
        self.status = label("等待开始", "EmptyHint")
        card.body.addWidget(self.error)
        card.body.addWidget(self.status)
        self.add_card(card)

    def _build_path_card(self) -> None:
        card = result_card("路径", "蓝色节点表示该跳在设备备注库中有记录；红色空心圈表示该跳不回应")
        self.timeline = HopTimeline()
        card.body.addWidget(self.timeline, 1)
        self.add_card(card, 1)

    def _build_table_card(self) -> None:
        card = result_card("逐跳明细", "与上方时间轴同源；导出含完整字段")
        self.summary = label("暂无结果", "EmptyHint")
        self.summary.setWordWrap(True)
        card.body.addWidget(self.summary)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setObjectName("ResultTable")
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(140)
        card.body.addWidget(self.table, 1)
        card.body.addWidget(separator_line())

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.mark_input = make_line_edit("给选中的跳写设备备注，例如「楼宇汇聚 SW-3F」")
        footer.addWidget(self.mark_input, 1)
        self.mark_button = SecondaryButton("保存备注")
        self.mark_button.clicked.connect(self.save_remark)
        self.copy_button = SecondaryButton("复制地址")
        self.copy_button.clicked.connect(self.copy_selected)
        footer.addWidget(self.mark_button)
        footer.addWidget(self.copy_button)
        card.body.addLayout(footer)

        export = QHBoxLayout()
        export.setSpacing(8)
        self.copy_path_button = SecondaryButton("复制整条路径")
        self.copy_path_button.clicked.connect(self.copy_path)
        self.export_md_button = SecondaryButton("导出 Markdown")
        self.export_md_button.clicked.connect(self._choose_export_markdown)
        self.export_csv_button = SecondaryButton("导出 CSV")
        self.export_csv_button.clicked.connect(self._choose_export_csv)
        export.addWidget(self.copy_path_button)
        export.addStretch(1)
        export.addWidget(self.export_md_button)
        export.addWidget(self.export_csv_button)
        card.body.addLayout(export)

        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.add_card(card, 1)

    # -- 状态机 -------------------------------------------------------------
    def _set_state(self, state: str) -> None:
        self._state = state
        running = state == "running"
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.clear_button.setEnabled(not running)
        for widget in (self.target_input, self.hops_spin, self.timeout_combo):
            widget.setEnabled(not running)
        self.export_md_button.setEnabled(self._report is not None)
        self.export_csv_button.setEnabled(self._report is not None)
        self.copy_path_button.setEnabled(self._report is not None)

    def state(self) -> str:
        return self._state

    # -- 控制 ---------------------------------------------------------------
    def _timeout_value(self) -> float:
        index = max(0, min(self.timeout_combo.currentIndex(), len(tr_service.TIMEOUT_PRESETS) - 1))
        return tr_service.TIMEOUT_PRESETS[index]

    def _build_session(self) -> tr_service.TracerouteSession:
        hops = self.hops_spin.value()
        timeout = self._timeout_value()
        return tr_service.TracerouteSession(
            self.target_input.text(),
            max_hops=hops,
            timeout_s=timeout,
            # 每跳最多等 timeout 秒，跳数用满也算一次上限
            process_timeout_s=max(_PROCESS_TIMEOUT_BASE_S, hops * timeout * 3),
            runner=self.runner_factory,
            should_cancel=self._should_cancel,
        )

    def start(self) -> bool:
        """开始追踪；参数非法时返回 ``False`` 并在页面提示。"""
        self.error.clear_error()
        try:
            session = self._build_session()
        except tr_service.TracerouteError as exc:
            self.error.show_error(str(exc))
            return False

        self.clear_results()
        self._stop_requested = False
        self._report = None
        self._started = datetime.now()
        self._monotonic0 = time.monotonic()
        self.status.setText("正在追踪…（traceroute 结束后才会返回完整路径）")

        self._set_state("running")
        self._ticker.start()
        handle = self._runner.submit(session.run)
        handle.finished.connect(self._on_finished)
        handle.failed.connect(self._on_failed)
        handle.cancelled.connect(self._on_cancelled)
        self._handle = handle
        return True

    def stop(self) -> None:
        if self._state != "running":
            return
        # 不调用 handle.cancel()：会话自己收尾返回已采集结果；
        # 直接取消令牌会让 TaskRunner 丢弃结果。
        self._stop_requested = True
        self.status.setText("正在停止（等待 traceroute 进程结束）…")

    def clear_results(self) -> None:
        self._report = None
        self.table.setRowCount(0)
        self.timeline.clear()
        self.summary.setText("暂无结果")
        self.status.setText("等待开始")
        self.mark_input.clear()
        self._set_state(self._state if self._state == "running" else "idle")

    def shutdown(self) -> None:
        self._ticker.stop()
        self._runner.shutdown()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 接口
        self.shutdown()
        super().closeEvent(event)

    # -- 后台协作 -----------------------------------------------------------
    def _should_cancel(self) -> bool:
        return self._stop_requested

    def _on_tick(self) -> None:
        if self._state != "running":
            self._ticker.stop()
            return
        self.status.setText(
            f"正在追踪 {self.target_input.text().strip() or '(空)'}…"
            f"已运行 {time.monotonic() - self._monotonic0:.0f} 秒"
        )

    def _on_finished(self, report: object) -> None:
        self._ticker.stop()
        self._handle = None
        if not isinstance(report, tr_service.TracerouteReport):  # pragma: no cover - 防御
            self._on_failed("追踪返回了意外结果")
            return
        # 备注匹配放在主线程：Database 是单连接，工作线程里查会抛 ProgrammingError
        self._report = tr_service.annotate_report(report, self._book)
        self._paint(self._report)
        self._set_state("finished")
        tail = "（已停止）" if report.cancelled else ""
        verdict = "已到达目标" if report.reached else "未确认到达目标"
        self.status.setText(
            f"追踪结束{tail}：{report.hop_count} 跳，{verdict}，耗时 {report.duration_s:.1f} 秒"
        )

    def _on_failed(self, message: str) -> None:
        self._ticker.stop()
        self._handle = None
        self._set_state("idle")
        self.error.show_error(f"追踪失败：{message}")
        self.status.setText("追踪失败")

    def _on_cancelled(self) -> None:
        self._ticker.stop()
        self._handle = None
        self._set_state("finished")
        self.status.setText("已取消")

    # -- 渲染 ---------------------------------------------------------------
    def _paint(self, report: tr_service.TracerouteReport) -> None:
        self.timeline.set_hops(report.hops)
        self.table.setRowCount(len(report.hops))
        for row, hop in enumerate(report.hops):
            cells = [
                str(hop.hop),
                "*" if hop.timed_out else hop.address,
                _DASH if hop.rtt_ms is None else f"{hop.rtt_ms:.1f}",
                hop.remark or _DASH,
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if hop.timed_out:
                    item.setForeground(QColor(_TIMEOUT_COLOR))
                    item.setToolTip("该跳没有响应（可能是防火墙丢弃了 TTL 超时报文）")
                elif hop.name:
                    item.setToolTip(f"{hop.name} · {hop.location or '未填位置'}")
                self.table.setItem(row, column, item)

        timeout_count = len(report.timeouts)
        known = len(report.known_devices)
        parts = [f"{report.hop_count} 跳"]
        if known:
            parts.append(f"{known} 跳已备注")
        if timeout_count:
            parts.append(f"{timeout_count} 跳超时")
        if report.resolved != report.target:
            parts.append(f"解析为 {report.resolved}")
        self.summary.setText("，".join(parts) + "。")

    # -- 备注与复制 ---------------------------------------------------------
    def selected_hop(self) -> tr_service.HopInfo | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows or self._report is None:
            return None
        index = rows[0].row()
        if 0 <= index < len(self._report.hops):
            return self._report.hops[index]
        return None

    def save_remark(self) -> str | None:
        """保存选中跳的备注；成功返回 IP，失败返回 ``None`` 并提示。"""
        self.error.clear_error()
        hop = self.selected_hop()
        if hop is None:
            self.error.show_error("请先在表格中选中一跳")
            return None
        if hop.timed_out or not hop.address:
            self.error.show_error("这一跳没有回应，没有可备注的地址")
            return None
        try:
            self._book.mark(hop.address, remark=self.mark_input.text().strip())
        except DeviceBookError as exc:
            self.error.show_error(str(exc))
            return None
        if self._report is not None:
            self._report = tr_service.annotate_report(self._report, self._book)
            self._paint(self._report)
        return hop.address

    def _on_selection_changed(self) -> None:
        hop = self.selected_hop()
        if hop is None:
            self.mark_input.clear()
            return
        self.mark_input.setText(hop.name)

    def copy_selected(self) -> str | None:
        hop = self.selected_hop()
        if hop is None or hop.timed_out:
            self.error.show_error("请先在表格中选中一个有效的跳")
            return None
        _to_clipboard(hop.address)
        return hop.address

    def copy_path(self) -> str:
        """把整条路径复制成「1  10.0.0.1  备注」这样的纯文本。"""
        if self._report is None:
            return ""
        lines = []
        for hop in self._report.hops:
            address = "*" if hop.timed_out else hop.address
            rtt = "" if hop.rtt_ms is None else f"  {hop.rtt_ms:.1f} ms"
            remark = f"  [{hop.name}]" if hop.name else ""
            lines.append(f"{hop.hop:>2}  {address}{rtt}{remark}")
        text = "\n".join(lines)
        if text:
            _to_clipboard(text)
        return text

    # -- 导出 ---------------------------------------------------------------
    def report(self) -> tr_service.TracerouteReport | None:
        return self._report

    def _choose_export_markdown(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 Markdown", "traceroute-report.md", "Markdown 文件 (*.md)"
        )
        if path:
            self.export_markdown(path)

    def _choose_export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 CSV", "traceroute-result.csv", "CSV 文件 (*.csv)"
        )
        if path:
            self.export_csv(path)

    def export_markdown(self, path: str | Path) -> int:
        if self._report is None:
            return 0
        text = self._report.to_markdown()
        Path(path).write_text(text, encoding="utf-8")
        return len(text.splitlines())

    def export_csv(self, path: str | Path) -> int:
        if self._report is None:
            return 0
        rows = self._report.csv_rows()
        with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
            csv.writer(handle).writerows(rows)
        return len(rows) - 1

    # -- 测试 / 外部接口 ----------------------------------------------------
    def set_target(self, text: str) -> None:
        self.target_input.setText(text)

    def set_max_hops(self, value: int) -> None:
        self.hops_spin.setValue(value)

    def run_blocking(self, *, timeout_ms: int = 5) -> None:
        """测试辅助：等待后台任务结束并处理事件，让信号在本线程落定。"""
        if self._handle is not None:
            self._runner.wait_for_idle(30_000)
        _process_events(timeout_ms)

    def table_rows(self) -> list[list[str]]:
        table = self.table
        return [
            [table.item(row, column).text() for column in range(table.columnCount())]
            for row in range(table.rowCount())
        ]

    def status_text(self) -> str:
        return self.status.text()

    def error_text(self) -> str:
        return self.error.text()

    def summary_text(self) -> str:
        return self.summary.text()


def _to_clipboard(text: str) -> None:
    application = QApplication.instance()
    if application is None:
        return
    clipboard = application.clipboard()
    if clipboard is not None:
        clipboard.setText(text)


def _process_events(timeout_ms: int) -> None:
    deadline = time.monotonic() + timeout_ms / 1000
    application = QApplication.instance()
    if application is None:  # pragma: no cover - 测试环境总先建 QApplication
        return
    while time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.005)
