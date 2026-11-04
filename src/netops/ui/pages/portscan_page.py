"""端口扫描页面（diag.portscan）。

三块：目标与端口 → 扫描参数与进度 → 开放端口结果表。
扫描逻辑全在 ``services/portscan.py``，本页只做录入、信号转发与展示；
后台线程的 ``on_result`` 通过 ``pyqtSignal`` 回到主线程，UI 永不阻塞。
"""

from __future__ import annotations

import csv
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
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from netops.services import portscan as portscan_service
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

#: 结果表最多渲染多少行开放端口；完整数据仍可导出。
MAX_TABLE_ROWS = 2000

#: 并发超过这个值时提示「可能触发设备防扫描」。
CONCURRENCY_WARN = 128

_COLUMNS = ["主机", "端口", "服务", "延迟 (ms)", "Banner"]

_DASH = "—"

#: 超时预设：常规 1 秒，跨公网 / 老设备 5 秒。
_TIMEOUT_OPTIONS: list[tuple[str, float]] = [
    ("常规（1 秒）", portscan_service.DEFAULT_TIMEOUT_S),
    ("慢速服务（5 秒）", portscan_service.SLOW_SERVICE_TIMEOUT_S),
]


class PortScanPage(ToolPage):
    def __init__(self, parent=None) -> None:
        super().__init__(
            title="端口扫描",
            subtitle="TCP connect 并发探测常见服务端口，开放端口自动识别服务与 Banner。",
            parent=parent,
        )
        self._runner = TaskRunner(self, max_threads=4)
        self._scanner: portscan_service.PortScanner | None = None
        self._handle: TaskHandle | None = None
        self._state = "idle"
        self._stop_requested = False
        self._report: portscan_service.ScanReport | None = None
        self._open: dict[tuple[str, int], portscan_service.PortResult] = {}
        self._done = 0
        self._started: datetime | None = None
        self._monotonic0 = 0.0

        self._bridge = _ResultBridge(parent=self)
        self._bridge.result.connect(self._on_result)
        #: 测试可注入假 connect；生产走真实 asyncio.open_connection。
        self.connect_factory: Callable[..., object] | None = None

        self._ticker = QTimer(self)
        self._ticker.setInterval(250)
        self._ticker.timeout.connect(self._on_tick)

        self._build_input_card()
        self._build_result_card()
        self._set_state("idle")

    # -- 界面搭建 -----------------------------------------------------------
    def _build_input_card(self) -> None:
        card = result_card(
            "扫描目标", "支持单 IP、主机名、IP 列表与 CIDR / 末段区间，逗号或换行分隔"
        )
        self.targets_input = make_text_edit(
            "每行一个，也可以写网段：\n192.168.1.1\n192.168.1.0/28\n192.168.1.10-20",
            rows=3,
        )
        card.body.addWidget(self.targets_input)

        presets = QHBoxLayout()
        presets.setSpacing(14)
        presets.addWidget(label("端口预设", "FieldLabel"))
        self.preset_boxes: dict[str, QCheckBox] = {}
        for name, ports in portscan_service.PORT_PRESETS.items():
            box = QCheckBox(f"{name}（{len(ports)}）")
            box.setObjectName("ToolCheck")
            box.setToolTip("、".join(str(port) for port in ports))
            self.preset_boxes[name] = box
            presets.addWidget(box)
        presets.addStretch(1)
        card.body.addLayout(presets)

        self.custom_ports = make_line_edit("自定义端口，例如 80,443,1-1024（留空则只用预设）")
        card.body.addWidget(FieldRow("自定义端口", self.custom_ports, label_width=72))

        # 并发 / 超时一行，Banner 开关一行：避免四个控件挤在一行被压扁。
        params = QGridLayout()
        params.setHorizontalSpacing(18)
        params.setVerticalSpacing(10)

        self.concurrency_slider = QSlider(Qt.Orientation.Horizontal)
        self.concurrency_slider.setObjectName("ToolSlider")
        self.concurrency_slider.setRange(1, portscan_service.MAX_CONCURRENCY)
        self.concurrency_slider.setValue(portscan_service.DEFAULT_CONCURRENCY)
        self.concurrency_slider.setTickPosition(QSlider.TickPosition.NoTicks)
        self.concurrency_value = label(str(portscan_service.DEFAULT_CONCURRENCY), "FieldHint")
        self.concurrency_value.setFixedWidth(34)
        self.concurrency_slider.valueChanged.connect(self._on_concurrency_changed)
        concurrency_row = QHBoxLayout()
        concurrency_row.setContentsMargins(0, 0, 0, 0)
        concurrency_row.setSpacing(8)
        concurrency_row.addWidget(self.concurrency_slider, 1)
        concurrency_row.addWidget(self.concurrency_value)
        params.addWidget(FieldRow("并发连接", _wrap(concurrency_row), label_width=72), 0, 0)

        self.timeout_combo = make_combo([name for name, _ in _TIMEOUT_OPTIONS])
        params.addWidget(FieldRow("连接超时", self.timeout_combo, label_width=72), 0, 1)

        self.banner_check = QCheckBox("抓取 Banner 识别服务")
        self.banner_check.setObjectName("ToolCheck")
        self.banner_check.setChecked(True)
        self.banner_check.setToolTip("连接成功后读取一段响应，能识别 SSH / HTTP / 厂商设备等指纹")
        params.addWidget(self.banner_check, 1, 0)
        params.setColumnStretch(0, 1)
        params.setColumnStretch(1, 1)
        card.body.addLayout(params)

        self.risk_hint = label("", "FieldHint")
        self.risk_hint.setWordWrap(True)
        card.body.addWidget(self.risk_hint)

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

    def _build_result_card(self) -> None:
        card = result_card("开放端口", "只列出开放端口；关闭 / 被过滤的端口计入统计但不占表格")
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
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(160)
        card.body.addWidget(self.table, 1)
        card.body.addWidget(separator_line())

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.detail_summary = label("等待扫描", "EmptyHint")
        footer.addWidget(self.detail_summary, 1)
        self.export_csv_button = SecondaryButton("导出 CSV")
        self.export_csv_button.clicked.connect(self._choose_export_csv)
        self.export_md_button = SecondaryButton("导出 Markdown")
        self.export_md_button.clicked.connect(self._choose_export_markdown)
        footer.addWidget(self.export_csv_button)
        footer.addWidget(self.export_md_button)
        card.body.addLayout(footer)
        self.add_card(card, 1)

    # -- 状态机 -------------------------------------------------------------
    def _set_state(self, state: str) -> None:
        self._state = state
        running = state == "running"
        editable = state in {"idle", "finished"}
        self.start_button.setEnabled(editable)
        self.stop_button.setEnabled(running)
        # 扫完一定要能清空：结果表可能很大，用户要能立刻复位再扫。
        self.clear_button.setEnabled(editable)
        for widget in (
            self.targets_input,
            self.custom_ports,
            self.concurrency_slider,
            self.timeout_combo,
            self.banner_check,
            *self.preset_boxes.values(),
        ):
            widget.setEnabled(editable)
        self.export_csv_button.setEnabled(self._report is not None)
        self.export_md_button.setEnabled(self._report is not None)

    def state(self) -> str:
        return self._state

    # -- 参数收集 -----------------------------------------------------------
    def _selected_presets(self) -> list[str]:
        return [name for name, box in self.preset_boxes.items() if box.isChecked()]

    def _collect_ports(self) -> tuple[int, ...]:
        names = self._selected_presets()
        custom = self.custom_ports.text().strip()
        ports: set[int] = set()
        if names:
            ports.update(portscan_service.preset_ports(names))
        if custom:
            ports.update(portscan_service.parse_ports(custom))
        if not ports:
            raise portscan_service.PortScanError("请至少勾选一个端口预设，或填写自定义端口")
        if len(ports) > portscan_service.MAX_PORTS:
            raise portscan_service.PortScanError(
                f"端口 {len(ports)} 个，超过单次上限 {portscan_service.MAX_PORTS} 个"
            )
        return tuple(sorted(ports))

    def _build_scanner(self) -> portscan_service.PortScanner:
        targets = portscan_service.parse_targets(self.targets_input.toPlainText())
        ports = self._collect_ports()
        timeout = _TIMEOUT_OPTIONS[self.timeout_combo.currentIndex()][1]
        return portscan_service.PortScanner(
            targets,
            ports,
            concurrency=self.concurrency_slider.value(),
            timeout_s=timeout,
            grab_banner=self.banner_check.isChecked(),
            on_result=self._bridge.emit_result,
            should_cancel=self._should_cancel,
            connect=self.connect_factory,
            wall_clock=datetime.now,
        )

    def _on_concurrency_changed(self, value: int) -> None:
        self.concurrency_value.setText(str(value))
        self._refresh_risk_hint()

    def _refresh_risk_hint(self) -> None:
        concurrency = self.concurrency_slider.value()
        if concurrency >= CONCURRENCY_WARN:
            self.risk_hint.setText(
                f"并发 {concurrency} 会瞬间打出大量 SYN，可能触发交换机 / 防火墙的防扫描"
                "甚至封禁源 IP，生产环境建议 ≤128。"
            )
            return
        try:
            targets = len(portscan_service.parse_targets(self.targets_input.toPlainText()))
            ports = len(self._collect_ports())
        except portscan_service.PortScanError:
            self.risk_hint.setText("")
            return
        warning = portscan_service.check_workload(targets, ports, concurrency)
        self.risk_hint.setText(warning or "")

    # -- 控制 ---------------------------------------------------------------
    def start(self) -> bool:
        """开始扫描；参数非法时返回 ``False`` 并在页面提示。"""
        self.error.clear_error()
        try:
            scanner = self._build_scanner()
        except portscan_service.PortScanError as exc:
            self.error.show_error(str(exc))
            return False

        self.clear_results()
        self._scanner = scanner
        self._stop_requested = False
        self._started = datetime.now()
        self._monotonic0 = time.monotonic()
        self.status.setText("准备扫描…")

        self._set_state("running")
        self._ticker.start()
        handle = self._runner.submit(scanner.run)
        handle.finished.connect(self._on_finished)
        handle.failed.connect(self._on_failed)
        handle.cancelled.connect(self._on_cancelled)
        self._handle = handle
        return True

    def stop(self) -> None:
        if self._state != "running":
            return
        # 与 Ping 页一致：不调用 handle.cancel()，让扫描自己收尾并返回部分结果，
        # 否则 TaskRunner 会丢弃结果，导出就没了。
        self._stop_requested = True
        self.status.setText("正在停止（已发出的连接会等超时收尾）…")

    def clear_results(self) -> None:
        self._report = None
        self._open = {}
        self._done = 0
        self.table.setRowCount(0)
        self.summary.setText("暂无结果")
        self.detail_summary.setText("等待扫描")
        self.status.setText("等待开始")
        self.export_csv_button.setEnabled(False)
        self.export_md_button.setEnabled(False)

    def shutdown(self) -> None:
        """页面销毁前调用：停表、取消任务、回收线程。"""
        self._ticker.stop()
        self._runner.shutdown()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 接口
        self.shutdown()
        super().closeEvent(event)

    # -- 后台协作 -----------------------------------------------------------
    def _should_cancel(self) -> bool:
        return self._stop_requested

    # -- 信号处理 -----------------------------------------------------------
    def _on_result(self, result: portscan_service.PortResult) -> None:
        self._done += 1
        if result.open:
            self._open[(result.host, result.port)] = result
        self.detail_summary.setText(
            f"已完成 {self._done:,} 个探测，发现 {len(self._open)} 个开放端口"
        )

    def _on_tick(self) -> None:
        if self._state != "running":
            self._ticker.stop()
            return
        self._paint_table()
        self.status.setText(self._status_line())

    def _status_line(self) -> str:
        total = self._scanner.total if self._scanner is not None else 0
        elapsed = _fmt_duration(time.monotonic() - self._monotonic0)
        return (
            f"已完成 {self._done:,} / {total:,} 个探测，发现 {len(self._open)} 个开放端口"
            f"  ·  已运行 {elapsed}"
        )

    def _paint_table(self) -> None:
        rows = sorted(self._open.values(), key=lambda item: (item.host, item.port))
        rows = rows[:MAX_TABLE_ROWS]
        self.table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            cells = [
                item.host,
                str(item.port),
                item.display_service or _DASH,
                _DASH if item.latency_ms is None else f"{item.latency_ms:.1f}",
                item.banner or _DASH,
            ]
            for column, value in enumerate(cells):
                cell = QTableWidgetItem(value)
                if column == 4 and item.banner:
                    cell.setToolTip(item.banner)
                self.table.setItem(row, column, cell)

    def _on_finished(self, report: object) -> None:
        self._ticker.stop()
        self._handle = None
        if not isinstance(report, portscan_service.ScanReport):  # pragma: no cover - 防御
            self._on_failed("扫描返回了意外结果")
            return
        self._report = report
        self._open = {
            (item.host, item.port): item for target in report.results for item in target.open_ports
        }
        self._paint_table()
        self._set_state("finished")
        tail = "（已停止）" if report.cancelled else ""
        self.status.setText(
            f"扫描结束{tail}：{report.total_probes:,} 个探测，{report.open_count} 个开放端口，"
            f"耗时 {report.duration_s:.1f} 秒"
        )
        self.detail_summary.setText(
            f"{len(report.alive_hosts)} 台主机有开放端口，共 {report.open_count} 个"
        )
        self.summary.setText(self._summary_text(report))

    def _summary_text(self, report: portscan_service.ScanReport) -> str:
        if not report.open_count:
            return "未发现开放端口。若目标可达，可尝试慢速超时或换一组端口。"
        services = sorted(
            {item.display_service for target in report.results for item in target.open_ports}
        )
        services_text = "、".join(name for name in services if name)
        base = f"发现 {report.open_count} 个开放端口，涉及 {len(report.alive_hosts)} 台主机。"
        return base + (f"服务类型：{services_text}。" if services_text else "")

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

    # -- 导出 ---------------------------------------------------------------
    def report(self) -> portscan_service.ScanReport | None:
        return self._report

    def _choose_export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 CSV", "portscan-result.csv", "CSV 文件 (*.csv)"
        )
        if path:
            self.export_csv(path)

    def _choose_export_markdown(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 Markdown", "portscan-report.md", "Markdown 文件 (*.md)"
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
    def set_targets(self, text: str) -> None:
        self.targets_input.setPlainText(text)

    def set_presets(self, names: list[str]) -> None:
        for name, box in self.preset_boxes.items():
            box.setChecked(name in names)

    def set_custom_ports(self, text: str) -> None:
        self.custom_ports.setText(text)

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


class _ResultBridge(QObject):
    """把后台线程的结果回调转成 Qt 信号——UI 改动只发生在主线程。"""

    result = pyqtSignal(object)

    def emit_result(self, result: portscan_service.PortResult) -> None:
        self.result.emit(result)


def _wrap(layout: QHBoxLayout) -> QWidget:
    """把布局塞进一个容器控件，供 ``FieldRow`` 使用。"""
    container = QWidget()
    container.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    return container


def _fmt_duration(seconds: float) -> str:
    total = int(max(seconds, 0.0))
    minutes, secs = divmod(total, 60)
    if minutes:
        return f"{minutes} 分 {secs} 秒"
    return f"{secs} 秒"


def _process_events(timeout_ms: int) -> None:
    deadline = time.monotonic() + timeout_ms / 1000
    application = QApplication.instance()
    if application is None:  # pragma: no cover - 测试环境总先建 QApplication
        return
    while time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.005)
