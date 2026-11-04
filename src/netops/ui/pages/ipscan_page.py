"""局域网扫描页面（diag.ipscan）。

三块：网段与策略 → 参数与进度 → 存活主机列表。
扫描逻辑全在 ``services/ipscan.py``，备注读写在 ``services/devices.py``，
本页只做录入、信号转发与展示；后台线程的 ``on_host`` 通过 ``pyqtSignal``
回到主线程，UI 永不阻塞。
"""

from __future__ import annotations

import csv
import ipaddress
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QSlider,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from netops.services import ipscan as ipscan_service
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
    make_text_edit,
    result_card,
    separator_line,
)

#: 结果表最多渲染多少行存活主机；完整数据仍可导出。
MAX_TABLE_ROWS = 2000

#: 并发超过这个值时提示「可能触发防扫描 / 网关告警」。
CONCURRENCY_WARN = 96

_COLUMNS = ["IP", "MAC", "厂商", "备注", "探测方式", "延迟 (ms)"]

_DASH = "—"

#: key 与 ``ipscan_service.MODES`` 对应，顺序即下拉顺序。
_MODE_ORDER = [
    ipscan_service.MODE_BOTH,
    ipscan_service.MODE_ICMP,
    ipscan_service.MODE_ARP,
]


class IpScanPage(ToolPage):
    def __init__(self, parent=None, *, book: DeviceBook | None = None) -> None:
        super().__init__(
            title="局域网 IP 扫描",
            subtitle="ICMP 并发探测 + 系统 ARP 表双重确认，自动识别 MAC 厂商并回显设备备注。",
            parent=parent,
        )
        self._runner = TaskRunner(self, max_threads=4)
        self._session: ipscan_service.IpScanSession | None = None
        self._handle: TaskHandle | None = None
        self._state = "idle"
        self._stop_requested = False
        self._report: ipscan_service.ScanReport | None = None
        self._alive: dict[str, ipscan_service.HostResult] = {}
        self._done = 0
        self._total = 0
        self._started: datetime | None = None
        self._monotonic0 = 0.0
        self._book = book or DeviceBook(None)

        self._bridge = _HostBridge(parent=self)
        self._bridge.host.connect(self._on_host)

        #: 测试可注入假的 ping / arp。
        self.ping_runner: Callable[..., object] = ipscan_service.run_ping
        self.arp_reader: Callable[..., object] = ipscan_service.read_arp_table

        self._ticker = QTimer(self)
        self._ticker.setInterval(250)
        self._ticker.timeout.connect(self._on_tick)

        self._build_input_card()
        self._build_result_card()
        self._set_state("idle")

    # -- 对外：备注簿 --------------------------------------------------------
    def set_device_book(self, book: DeviceBook) -> None:
        """由 ``create_widget`` 注入真实备注簿；单测可换成 ``DeviceBook(None)``。"""
        self._book = book
        self._refresh_remarks()

    # -- 界面搭建 -----------------------------------------------------------
    def _build_input_card(self) -> None:
        card = result_card(
            "扫描范围",
            "支持 CIDR、起止地址与末段区间（192.168.1.10-20），多行或逗号分隔",
        )
        self.nets_input = make_text_edit(
            "每行一个网段：\n192.168.1.0/24\n10.0.0.1-10.0.0.50\n# 井号开头为注释",
            rows=3,
        )
        card.body.addWidget(self.nets_input)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)

        self.mode_combo = make_combo([ipscan_service.MODES[key] for key in _MODE_ORDER])
        grid.addWidget(FieldRow("探测策略", self.mode_combo, label_width=72), 0, 0)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setObjectName("ToolInput")
        self.timeout_spin.setRange(100, ipscan_service.MAX_TIMEOUT_MS)
        self.timeout_spin.setSingleStep(100)
        self.timeout_spin.setValue(ipscan_service.DEFAULT_TIMEOUT_MS)
        grid.addWidget(FieldRow("单次等待", self.timeout_spin, hint="毫秒", label_width=72), 0, 1)

        self.concurrency_slider = QSlider(Qt.Orientation.Horizontal)
        self.concurrency_slider.setObjectName("ToolSlider")
        self.concurrency_slider.setRange(1, ipscan_service.MAX_CONCURRENCY)
        self.concurrency_slider.setValue(ipscan_service.DEFAULT_CONCURRENCY)
        self.concurrency_value = label(str(ipscan_service.DEFAULT_CONCURRENCY), "FieldHint")
        self.concurrency_value.setFixedWidth(34)
        self.concurrency_slider.valueChanged.connect(self._on_concurrency_changed)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.concurrency_slider, 1)
        row.addWidget(self.concurrency_value)
        grid.addWidget(FieldRow("并发数", _wrap(row), label_width=72), 1, 0, 1, 2)

        self.alive_only_check = QCheckBox("只显示存活主机")
        self.alive_only_check.setObjectName("ToolCheck")
        self.alive_only_check.setChecked(True)
        self.alive_only_check.toggled.connect(self._repaint_table)
        grid.addWidget(self.alive_only_check, 2, 0)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        card.body.addLayout(grid)

        self.scale_hint = label("等待估算", "FieldHint")
        self.scale_hint.setWordWrap(True)
        card.body.addWidget(self.scale_hint)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.start_button = PrimaryButton("开始扫描")
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

        self.nets_input.textChanged.connect(self._update_scale_hint)
        self._update_scale_hint()

    def _build_result_card(self) -> None:
        card = result_card(
            "存活主机",
            "探测方式说明来源：icmp 为 ICMP 有回应，arp 为仅 ARP 表命中，icmp+arp 为两者都有",
        )
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
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(180)
        card.body.addWidget(self.table, 1)
        card.body.addWidget(separator_line())

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.remark_input = make_line_edit("给选中的主机写备注，例如「3F 弱电井 AP」")
        footer.addWidget(self.remark_input, 1)
        self.mark_button = SecondaryButton("保存备注")
        self.mark_button.clicked.connect(self.save_remark)
        self.copy_button = SecondaryButton("复制 IP")
        self.copy_button.clicked.connect(self.copy_selected)
        footer.addWidget(self.mark_button)
        footer.addWidget(self.copy_button)
        card.body.addLayout(footer)

        export = QHBoxLayout()
        export.setSpacing(8)
        self.export_csv_button = SecondaryButton("导出 CSV")
        self.export_csv_button.clicked.connect(self._choose_export_csv)
        self.export_csv_button.setEnabled(False)
        self.export_md_button = SecondaryButton("导出 Markdown")
        self.export_md_button.clicked.connect(self._choose_export_markdown)
        self.export_md_button.setEnabled(False)
        export.addStretch(1)
        export.addWidget(self.export_csv_button)
        export.addWidget(self.export_md_button)
        card.body.addLayout(export)

        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.add_card(card, 1)

    # -- 状态机 -------------------------------------------------------------
    def _set_state(self, state: str) -> None:
        self._state = state
        running = state == "running"
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        # 扫完一定要能清空：结果表可能很大，用户要能立刻复位再扫。
        self.clear_button.setEnabled(not running)
        for widget in (
            self.nets_input,
            self.mode_combo,
            self.timeout_spin,
            self.concurrency_slider,
            self.alive_only_check,
        ):
            widget.setEnabled(not running)
        self.export_csv_button.setEnabled(self._report is not None)
        self.export_md_button.setEnabled(self._report is not None)

    def state(self) -> str:
        return self._state

    # -- 规模预估 -----------------------------------------------------------
    def _update_scale_hint(self) -> None:
        text = self.nets_input.toPlainText()
        if not text.strip():
            self.scale_hint.setText("等待估算")
            return
        try:
            count = ipscan_service.estimate(text)
        except ipscan_service.IpScanError as exc:
            self.scale_hint.setText(str(exc))
            return
        except ValueError:
            self.scale_hint.setText("输入格式不正确，请检查网段写法")
            return
        self._total = count
        per_second = max(1, self.concurrency_slider.value())
        rough = count / per_second
        text_hint = (
            f"本次将扫描 {count:,} 个地址，并发 {per_second}（预计最少约 {rough:.0f} 秒，"
            "实际取决于对端是否响应）"
        )
        if per_second >= CONCURRENCY_WARN:
            text_hint += (
                f"。并发 {per_second} 偏高，会瞬间产生大量 ICMP 报文，"
                "可能触发网关 / 交换机的防扫描策略"
            )
        self.scale_hint.setText(text_hint)

    def _on_concurrency_changed(self, value: int) -> None:
        self.concurrency_value.setText(str(value))
        self._update_scale_hint()

    # -- 控制 ---------------------------------------------------------------
    def _build_session(self) -> ipscan_service.IpScanSession:
        text = self.nets_input.toPlainText()
        specs = ipscan_service.parse_networks(text)
        hosts = [ip for spec in specs for ip in spec.hosts]
        mode = _MODE_ORDER[self.mode_combo.currentIndex()]
        return ipscan_service.IpScanSession(
            hosts,
            nets=[spec.label for spec in specs],
            mode=mode,
            concurrency=self.concurrency_slider.value(),
            timeout_ms=self.timeout_spin.value(),
            on_host=self._bridge.emit_host,
            should_cancel=self._should_cancel,
            ping_runner=self.ping_runner,
            arp_reader=self.arp_reader,
        )

    def start(self) -> bool:
        """开始扫描；参数非法时返回 ``False`` 并在页面提示。"""
        self.error.clear_error()
        try:
            session = self._build_session()
        except ipscan_service.IpScanError as exc:
            self.error.show_error(str(exc))
            return False
        except ValueError as exc:
            self.error.show_error(f"网段格式不正确：{exc}")
            return False

        self.clear_results()
        self._session = session
        self._total = session.total
        self._done = 0
        self._stop_requested = False
        self._report = None
        self._started = datetime.now()
        self._monotonic0 = time.monotonic()
        self.status.setText("准备扫描…")

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
        # 不调用 handle.cancel()：会话自己收尾并返回已采集结果，
        # 直接取消令牌会让 TaskRunner 丢弃结果。
        self._stop_requested = True
        self.status.setText("正在停止（等待当前批次结束）…")

    def clear_results(self) -> None:
        self._report = None
        self._alive = {}
        self._done = 0
        self.table.setRowCount(0)
        self.summary.setText("暂无结果")
        self.status.setText("等待开始")
        self.remark_input.clear()
        self.export_csv_button.setEnabled(False)
        self.export_md_button.setEnabled(False)

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
        elapsed = time.monotonic() - self._monotonic0
        self.status.setText(
            f"已探测 {self._done:,} / {self._total:,} · 存活 {len(self._alive):,} · "
            f"已运行 {elapsed:.0f} 秒"
        )

    # -- 信号处理 -----------------------------------------------------------
    def _on_host(self, result: ipscan_service.HostResult) -> None:
        self._done += 1
        if result.alive:
            self._alive[result.ip] = result
            self._append_row(result)
            self.summary.setText(
                f"已发现 {len(self._alive):,} 台存活主机（探测 {self._done:,} 个地址）"
            )

    def _refresh_remarks(self) -> None:
        """备注簿变动后，把表格里的「备注」列刷一遍，不必重扫。"""
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is None:
                continue
            device = self._book.remark_of(item.text())
            cell = self.table.item(row, 3)
            text = device.remark if device and device.remark else _DASH
            if cell is None:
                self.table.setItem(row, 3, QTableWidgetItem(text))
            else:
                cell.setText(text)

    def _append_row(self, result: ipscan_service.HostResult) -> None:
        if not self.alive_only_check.isChecked():
            return
        if self.table.rowCount() >= MAX_TABLE_ROWS:
            return
        self._insert_row(result)

    def _insert_row(self, result: ipscan_service.HostResult) -> None:
        device = self._book.remark_of(result.ip)
        cells = [
            result.ip,
            result.mac or _DASH,
            result.vendor or _DASH,
            (device.remark if device and device.remark else _DASH),
            result.method or _DASH,
            _DASH if result.rtt_ms is None else f"{result.rtt_ms:.1f}",
        ]
        row = self.table.rowCount()
        self.table.insertRow(row)
        for column, value in enumerate(cells):
            item = QTableWidgetItem(value)
            if column == 4 and result.method == ipscan_service.MODE_ARP:
                # 只有 ARP 命中说明 ICMP 被拦了，值得提醒但不算错误
                item.setForeground(Qt.GlobalColor.darkYellow)
                item.setToolTip("ICMP 无回应，仅从 ARP 表确认在线（常见于开启了防火墙的主机）")
            self.table.setItem(row, column, item)

    def _repaint_table(self) -> None:
        """切换「只显示存活主机」时重绘整张表（未扫描时为空表）。"""
        self.table.setRowCount(0)
        if not self.alive_only_check.isChecked():
            return
        for result in sorted(self._alive.values(), key=_ip_sort_key)[:MAX_TABLE_ROWS]:
            self._insert_row(result)

    def _on_selection_changed(self) -> None:
        ip = self.selected_ip()
        if ip is None:
            self.remark_input.clear()
            return
        device = self._book.remark_of(ip)
        self.remark_input.setText(device.remark if device else "")

    def _on_finished(self, report: object) -> None:
        self._ticker.stop()
        self._handle = None
        if not isinstance(report, ipscan_service.ScanReport):  # pragma: no cover - 防御
            self._on_failed("扫描返回了意外结果")
            return
        self._report = report
        self._alive = {item.ip: item for item in report.alive}
        self._repaint_table()
        self._set_state("finished")
        tail = "（已停止）" if report.cancelled else ""
        self.status.setText(
            f"扫描结束{tail}：共 {report.total:,} 个地址，存活 {report.alive_count:,} 台，"
            f"耗时 {report.duration_s:.0f} 秒"
        )
        self.summary.setText(
            f"共 {report.total:,} 个地址，存活 {report.alive_count:,} 台，"
            f"其中 {len(report.with_mac)} 台识别到 MAC"
        )

    def _on_failed(self, message: str) -> None:
        self._ticker.stop()
        self._handle = None
        self._set_state("idle")
        self.error.show_error(f"扫描失败：{message}")
        self.status.setText("扫描失败")

    def _on_cancelled(self) -> None:
        self._ticker.stop()
        self._handle = None
        self._set_state("finished")
        self.status.setText("已取消")

    # -- 备注与复制 ---------------------------------------------------------
    def selected_ip(self) -> str | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        return item.text() if item else None

    def save_remark(self) -> str | None:
        """保存选中主机的备注；成功返回 IP，失败返回 ``None`` 并提示。"""
        self.error.clear_error()
        ip = self.selected_ip()
        if ip is None:
            self.error.show_error("请先在表格中选中一台主机")
            return None
        text = self.remark_input.text().strip()
        try:
            self._book.mark(ip, remark=text)
        except DeviceBookError as exc:
            self.error.show_error(str(exc))
            return None
        self._refresh_remarks()
        return ip

    def copy_selected(self) -> str | None:
        ip = self.selected_ip()
        if ip is None:
            self.error.show_error("请先在表格中选中一台主机")
            return None
        application = QApplication.instance()
        if application is not None:
            clipboard = application.clipboard()
            if clipboard is not None:
                clipboard.setText(ip)
        return ip

    # -- 导出 ---------------------------------------------------------------
    def report(self) -> ipscan_service.ScanReport | None:
        return self._report

    def _choose_export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 CSV", "ipscan-result.csv", "CSV 文件 (*.csv)"
        )
        if path:
            self.export_csv(path)

    def _choose_export_markdown(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 Markdown", "ipscan-report.md", "Markdown 文件 (*.md)"
        )
        if path:
            self.export_markdown(path)

    def export_csv(self, path: str | Path) -> int:
        if self._report is None:
            return 0
        rows = self._report.csv_rows()
        with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
            csv.writer(handle).writerows(rows)
        return len(rows) - 1

    def export_markdown(self, path: str | Path) -> int:
        if self._report is None:
            return 0
        text = self._report.to_markdown()
        Path(path).write_text(text, encoding="utf-8")
        return len(text.splitlines())

    # -- 测试 / 外部接口 ----------------------------------------------------
    def set_networks(self, text: str) -> None:
        self.nets_input.setPlainText(text)

    def set_mode(self, mode: str) -> None:
        if mode in _MODE_ORDER:
            self.mode_combo.setCurrentIndex(_MODE_ORDER.index(mode))

    def mode(self) -> str:
        return _MODE_ORDER[self.mode_combo.currentIndex()]

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

    def scale_text(self) -> str:
        return self.scale_hint.text()


class _HostBridge(QObject):
    """把后台线程的 on_host 回调转成 Qt 信号——UI 改动只发生在主线程。"""

    host = pyqtSignal(object)

    def emit_host(self, result: ipscan_service.HostResult) -> None:
        self.host.emit(result)


def _wrap(layout: QHBoxLayout) -> QWidget:
    """把布局塞进一个容器控件，供 ``FieldRow`` 使用。"""
    container = QWidget()
    container.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    return container


def _ip_sort_key(result: ipscan_service.HostResult) -> int:
    try:
        return int(ipaddress.IPv4Address(result.ip))
    except ValueError:  # pragma: no cover - 扫描结果都是合法 IPv4
        return 0


def _process_events(timeout_ms: int) -> None:
    deadline = time.monotonic() + timeout_ms / 1000
    application = QApplication.instance()
    if application is None:  # pragma: no cover - 测试环境总先建 QApplication
        return
    while time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.005)
