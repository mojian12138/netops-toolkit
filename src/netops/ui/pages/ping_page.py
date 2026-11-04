"""Ping 工具页面（diag.ping）。

三个卡片：探测参数与目标 → 实时趋势 → 逐目标统计 / 采样明细。
探测逻辑全在 ``services/ping.py``，本页只做录入、信号转发与展示；
后台线程的 ``on_sample`` 通过 ``pyqtSignal`` 回到主线程，UI 永不阻塞。
"""

from __future__ import annotations

import csv
import time
from collections.abc import Callable
from datetime import datetime, timedelta
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
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
)

from netops.services import ping as ping_service
from netops.ui.async_bridge import TaskHandle, TaskRunner
from netops.ui.widgets.common import label
from netops.ui.widgets.forms import (
    FieldRow,
    InlineError,
    PrimaryButton,
    SecondaryButton,
    ToolPage,
    make_text_edit,
    result_card,
    separator_line,
)
from netops.ui.widgets.trend import RttTrendChart

#: 连续探测最长运行时长（秒）——到点自动停止，避免用户忘了关。
MAX_DURATION_S = 600

#: 采样明细表最多渲染多少行；完整数据仍可导出。
MAX_TABLE_ROWS = 2000

_ROUND_PRESETS = [1, 5, 10, 30, 100]

_TIMEOUT_PRESETS = [500, 1000, 2000, 3000, 5000]
_INTERVAL_PRESETS = [200, 500, 1000, 2000, 5000]

_SUMMARY_COLUMNS = ["目标", "发送", "接收", "丢包率", "最小 (ms)", "平均 (ms)", "最大 (ms)", "状态"]
_DETAIL_COLUMNS = ["时间", "目标", "序号", "状态", "延迟 (ms)", "TTL", "来源"]

_DASH = "—"


class PingPage(ToolPage):
    def __init__(self, parent=None) -> None:
        super().__init__(
            title="Ping 工具",
            subtitle="单目标连续 Ping 与多目标并发批量探测，实时绘制延迟趋势，一键导出统计报表。",
            parent=parent,
        )
        self._runner = TaskRunner(self, max_threads=4)
        self._session: ping_service.PingSession | None = None
        self._handle: TaskHandle | None = None
        self._state = "idle"
        self._stop_requested = False
        self._report: ping_service.PingReport | None = None
        self._summaries: dict[str, ping_service.TargetSummary] = {}
        self._sample_count = 0
        self._started: datetime | None = None
        self._monotonic0 = 0.0
        self._cancel_reason = ""

        self._bridge = _SampleBridge(parent=self)
        self._bridge.sample.connect(self._on_sample)
        #: 真实探测走系统 ping；测试可替换成假 runner。
        self.runner_factory: Callable[..., object] = ping_service.run_ping

        self._ticker = QTimer(self)
        self._ticker.setInterval(250)
        self._ticker.timeout.connect(self._on_tick)

        self._build_input_card()
        self._build_chart_card()
        self._build_result_card()
        self._set_state("idle")

    # -- 界面搭建 -----------------------------------------------------------
    def _build_input_card(self) -> None:
        card = result_card("探测参数", "目标支持逗号 / 空格 / 换行分隔，自动去重并校验格式")
        self.targets_input = make_text_edit(
            "每行一个，也可以一行写多个：\n192.168.1.1\n127.0.0.1, 8.8.8.8\n# 井号开头为注释",
            rows=4,
        )
        card.body.addWidget(self.targets_input)

        # 四个数字参数一行放不下（会让 QSpinBox 被压到只剩边框），因此排成两行。
        params = QGridLayout()
        params.setHorizontalSpacing(18)
        params.setVerticalSpacing(10)

        self.rounds_spin = _make_spin(_ROUND_PRESETS)
        self.rounds_spin.setValue(5)
        params.addWidget(FieldRow("探测轮次", self.rounds_spin, label_width=64), 0, 0)

        self.continuous_check = QCheckBox("连续探测")
        self.continuous_check.setObjectName("ToolCheck")
        self.continuous_check.setToolTip(
            f"勾选后忽略轮次，一直探测到手动停止（最长 {MAX_DURATION_S // 60} 分钟）"
        )
        self.continuous_check.toggled.connect(
            lambda checked: self.rounds_spin.setEnabled(not checked)
        )
        params.addWidget(self.continuous_check, 0, 1)

        self.timeout_spin = _make_spin(_TIMEOUT_PRESETS)
        self.timeout_spin.setValue(ping_service.DEFAULT_PING_TIMEOUT_MS)
        params.addWidget(FieldRow("单次等待", self.timeout_spin, hint="毫秒", label_width=64), 0, 2)

        self.interval_spin = _make_spin(_INTERVAL_PRESETS)
        self.interval_spin.setValue(ping_service.DEFAULT_INTERVAL_MS)
        self.interval_spin.setMinimum(ping_service.MIN_INTERVAL_MS)
        params.addWidget(
            FieldRow("探测间隔", self.interval_spin, hint="毫秒", label_width=64), 1, 0
        )

        self.concurrency_spin = _make_spin(list(range(1, ping_service.MAX_CONCURRENCY + 1)))
        self.concurrency_spin.setValue(ping_service.DEFAULT_CONCURRENCY)
        params.addWidget(
            FieldRow("并发数", self.concurrency_spin, hint="上限 16", label_width=64), 1, 2
        )
        for column in (0, 1, 2):
            params.setColumnStretch(column, 1)
        card.body.addLayout(params)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.start_button = PrimaryButton("开始探测")
        self.start_button.clicked.connect(self.start)
        self.pause_button = SecondaryButton("暂停")
        self.pause_button.clicked.connect(self.toggle_pause)
        self.stop_button = SecondaryButton("停止")
        self.stop_button.clicked.connect(self.stop)
        self.clear_button = SecondaryButton("清空结果")
        self.clear_button.clicked.connect(self.clear_results)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.pause_button)
        buttons.addWidget(self.stop_button)
        buttons.addStretch(1)
        buttons.addWidget(self.clear_button)
        card.body.addLayout(buttons)

        self.error = InlineError()
        self.remaining = label("等待开始", "EmptyHint")
        card.body.addWidget(self.error)
        card.body.addWidget(self.remaining)
        self.add_card(card)

    def _build_chart_card(self) -> None:
        card = result_card("实时延迟趋势", "纵轴按当前峰值自适应；底部红叉为超时点")
        self.chart = RttTrendChart()
        card.body.addWidget(self.chart, 1)
        self.add_card(card, 2)

    def _build_result_card(self) -> None:
        card = result_card("探测结果", "先看统计，再看逐条采样；导出含完整采样明细")

        self.summary_table = _make_table(_SUMMARY_COLUMNS)
        for column in (1, 2, 3, 4, 5, 6):
            self.summary_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.summary_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.summary_table.horizontalHeader().setSectionResizeMode(
            7, QHeaderView.ResizeMode.Stretch
        )
        self.summary_table.setMinimumHeight(112)
        self.summary_table.setMaximumHeight(220)
        card.body.addWidget(label("逐目标统计", "CardTitle"))
        card.body.addWidget(self.summary_table)
        card.body.addWidget(separator_line())

        self.detail_table = _make_table(_DETAIL_COLUMNS)
        header = self.detail_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (2, 3, 4, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        card.body.addWidget(label("采样明细（最新在上，最多显示 2000 条）", "CardTitle"))
        card.body.addWidget(self.detail_table, 1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.detail_summary = label("等待探测", "EmptyHint")
        footer.addWidget(self.detail_summary, 1)
        self.export_csv_button = SecondaryButton("导出 CSV")
        self.export_csv_button.clicked.connect(self._choose_export_csv)
        self.export_csv_button.setEnabled(False)
        self.export_md_button = SecondaryButton("导出 Markdown")
        self.export_md_button.clicked.connect(self._choose_export_markdown)
        self.export_md_button.setEnabled(False)
        footer.addWidget(self.export_csv_button)
        footer.addWidget(self.export_md_button)
        card.body.addLayout(footer)
        self.add_card(card, 3)

    # -- 状态机 -------------------------------------------------------------
    def _set_state(self, state: str) -> None:
        self._state = state
        running = state == "running"
        paused = state == "paused"
        self.start_button.setEnabled(state in {"idle", "finished"})
        self.pause_button.setEnabled(running or paused)
        self.pause_button.setText("继续" if paused else "暂停")
        self.stop_button.setEnabled(running or paused)
        self.clear_button.setEnabled(state in {"idle", "finished"} and not self._sample_count)
        editable = state in {"idle", "finished"}
        for widget in (
            self.rounds_spin,
            self.continuous_check,
            self.timeout_spin,
            self.interval_spin,
            self.concurrency_spin,
            self.targets_input,
        ):
            widget.setEnabled(editable)
        if editable and self.continuous_check.isChecked():
            self.rounds_spin.setEnabled(False)
        self.export_csv_button.setEnabled(self._report is not None)
        self.export_md_button.setEnabled(self._report is not None)

    def state(self) -> str:
        return self._state

    # -- 控制 ---------------------------------------------------------------
    def _build_session(self) -> ping_service.PingSession:
        text = self.targets_input.toPlainText()
        targets = ping_service.parse_targets(text, limit=ping_service.MAX_TARGETS)
        rounds = None if self.continuous_check.isChecked() else self.rounds_spin.value()
        return ping_service.PingSession(
            targets,
            rounds=rounds,
            interval_ms=self.interval_spin.value(),
            timeout_ms=self.timeout_spin.value(),
            concurrency=self.concurrency_spin.value(),
            max_duration_s=MAX_DURATION_S,
            runner=self.runner_factory,
            on_sample=self._bridge.emit_sample,
            sleep=self._interruptible_sleep,
            should_cancel=self._should_cancel,
            should_pause=self._should_pause,
            wall_clock=datetime.now,
        )

    def start(self) -> bool:
        """开始新一轮探测；参数非法时返回 ``False`` 并在页面提示。"""
        self.error.clear_error()
        try:
            session = self._build_session()
        except ping_service.PingError as exc:
            self.error.show_error(str(exc))
            return False

        self.clear_results()
        self._session = session
        self._summaries = {}
        self._sample_count = 0
        self._stop_requested = False
        self._report = None
        self._cancel_reason = ""
        self._started = datetime.now()
        self._monotonic0 = time.monotonic()
        self.chart.clear()
        self.detail_table.setRowCount(0)
        self.remaining.setText("准备探测…")

        self._set_state("running")
        self._ticker.start()
        handle = self._runner.submit(session.run)
        handle.finished.connect(self._on_finished)
        handle.failed.connect(self._on_failed)
        handle.cancelled.connect(self._on_cancelled)
        self._handle = handle
        return True

    def toggle_pause(self) -> None:
        if self._state == "running":
            self._set_state("paused")
            self.remaining.setText("已暂停（本轮探测完成后挂起）")
        elif self._state == "paused":
            self._set_state("running")

    def stop(self) -> None:
        if self._state not in {"running", "paused"}:
            return
        self._cancel_reason = "已手动停止"
        self._stop_requested = True
        # 不调用 handle.cancel()：会话会自己收尾并返回已采集的报告，
        # 直接取消令牌会让 TaskRunner 丢弃结果，导出就没了。

    def clear_results(self) -> None:
        self._report = None
        self._summaries = {}
        self._sample_count = 0
        self.chart.clear()
        self.summary_table.setRowCount(0)
        self.detail_table.setRowCount(0)
        self.detail_summary.setText("等待探测")
        self.remaining.setText("等待开始")
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
    def _interruptible_sleep(self, seconds: float) -> None:
        """会话的间隔睡眠：按 50ms 切片，停止时立即返回；暂停交给实时的 should_pause。"""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._stop_requested:
                return
            time.sleep(min(0.05, max(deadline - time.monotonic(), 0.0)))

    def _should_cancel(self) -> bool:
        return self._stop_requested

    def _should_pause(self) -> bool:
        return self._state == "paused"

    # -- 信号处理 -----------------------------------------------------------
    def _on_sample(self, sample: ping_service.PingSample) -> None:
        self._sample_count += 1
        self.chart.add_sample(sample.target, sample.elapsed_s, sample.rtt_ms)
        self._append_detail(sample)
        self._update_summary(sample)
        self.detail_summary.setText(f"已采集 {self._sample_count:,} 条采样")

    def _append_detail(self, sample: ping_service.PingSample) -> None:
        view = self.detail_table
        view.insertRow(0)
        stamp = (self._started or datetime.now()) + timedelta(seconds=max(sample.elapsed_s, 0.0))
        cells = [
            stamp.strftime("%H:%M:%S.%f")[:-3],
            sample.target,
            str(sample.seq),
            sample.status_text,
            _DASH if sample.rtt_ms is None else f"{sample.rtt_ms:.3f}",
            _DASH if sample.ttl is None else str(sample.ttl),
            sample.from_addr or _DASH,
        ]
        for column, value in enumerate(cells):
            item = QTableWidgetItem(value)
            if sample.error is not None:
                item.setForeground(Qt.GlobalColor.red)
                item.setToolTip(sample.error)
            elif sample.timed_out:
                item.setForeground(Qt.GlobalColor.darkYellow)
            view.setItem(0, column, item)
        while view.rowCount() > MAX_TABLE_ROWS:
            view.removeRow(view.rowCount() - 1)

    def _update_summary(self, sample: ping_service.PingSample) -> None:
        # 统计口径以服务层为准（同一把锁保护），页面不重复实现累加逻辑
        if self._session is None:
            return
        summary = self._session.summary_for(sample.target)
        if summary is None:
            return
        self._summaries[sample.target] = summary
        self._paint_summary()
        self.remaining.setText(self._status_line())

    def _status_line(self) -> str:
        sent = sum(item.sent for item in self._summaries.values())
        received = sum(item.received for item in self._summaries.values())
        loss = 0.0 if sent == 0 else (sent - received) / sent * 100
        return (
            f"发送 {sent} / 接收 {received} / 丢包 {loss:.1f}%  ·  已运行 "
            f"{_fmt_duration(time.monotonic() - self._monotonic0)}"
        )

    def _paint_summary(self) -> None:
        targets = list(self._summaries)
        table = self.summary_table
        table.setRowCount(len(targets))
        for row, target in enumerate(targets):
            item = self._summaries[target]
            status = "正常"
            if item.errors:
                status = "错误"
            elif item.consecutive_failures:
                status = f"连续失败 {item.consecutive_failures} 次"
            elif item.sent and item.received < item.sent:
                status = "间歇丢包"
            cells = [
                target,
                str(item.sent),
                str(item.received),
                f"{item.packet_loss:.0f}%",
                _num(item.min_ms),
                _num(item.avg_ms),
                _num(item.max_ms),
                status,
            ]
            for column, value in enumerate(cells):
                cell = QTableWidgetItem(value)
                if column == 7 and status != "正常":
                    cell.setForeground(Qt.GlobalColor.red)
                table.setItem(row, column, cell)

    def _on_tick(self) -> None:
        if self._state == "idle":
            self._ticker.stop()
            return
        self.remaining.setText(self._status_line())

    def _on_finished(self, report: object) -> None:
        self._ticker.stop()
        self._handle = None
        if not isinstance(report, ping_service.PingReport):  # pragma: no cover - 防御
            self._on_failed("探测返回了意外结果")
            return
        self._report = report
        self._summaries = {item.target: item for item in report.summaries}
        self._paint_summary()
        self._set_state("finished")
        tail = "（已停止）" if report.cancelled else ""
        self.remaining.setText(
            f"{self._cancel_reason or '探测结束'}{tail}：完成 {report.rounds_done} 轮，"
            f"发送 {report.transmitted} / 接收 {report.received} / 丢包 {report.packet_loss:.1f}%"
        )

    def _on_failed(self, message: str) -> None:
        self._ticker.stop()
        self._handle = None
        self._set_state("idle")
        self.error.show_error(f"探测失败：{message}")
        self.remaining.setText("探测失败")

    def _on_cancelled(self) -> None:
        self._ticker.stop()
        self._handle = None
        self._set_state("finished")
        self.remaining.setText(f"{self._cancel_reason or '已取消'}")

    # -- 导出 ---------------------------------------------------------------
    def report(self) -> ping_service.PingReport | None:
        return self._report

    def _choose_export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 CSV", "ping-result.csv", "CSV 文件 (*.csv)"
        )
        if path:
            self.export_csv(path)

    def _choose_export_markdown(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 Markdown", "ping-report.md", "Markdown 文件 (*.md)"
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

    def set_rounds(self, rounds: int | None) -> None:
        """设置探测轮次；``None`` 表示连续探测。"""
        if rounds is None:
            self.continuous_check.setChecked(True)
            return
        self.continuous_check.setChecked(False)
        self.rounds_spin.setValue(rounds)

    def run_blocking(self, *, timeout_ms: int = 5) -> None:
        """测试辅助：等待后台任务结束并处理事件，让信号在本线程落定。"""
        if self._handle is not None:
            self._runner.wait_for_idle(30_000)
        _process_events(timeout_ms)

    def summary_rows(self) -> list[list[str]]:
        table = self.summary_table
        return [
            [table.item(row, column).text() for column in range(table.columnCount())]
            for row in range(table.rowCount())
        ]

    def detail_rows(self) -> list[list[str]]:
        table = self.detail_table
        return [
            [table.item(row, column).text() for column in range(table.columnCount())]
            for row in range(table.rowCount())
        ]

    def status_text(self) -> str:
        return self.remaining.text()

    def error_text(self) -> str:
        return self.error.text()


class _SampleBridge(QObject):
    """把后台线程的采样回调转成 Qt 信号——保证 UI 改动只发生在主线程。"""

    sample = pyqtSignal(object)

    def emit_sample(self, sample: ping_service.PingSample) -> None:
        self.sample.emit(sample)


def _make_spin(values: list[int]) -> QSpinBox:
    spin = QSpinBox()
    spin.setObjectName("ToolInput")
    spin.setRange(min(values), max(values))
    spin.setSingleStep(values[1] - values[0] if len(values) > 1 else 1)
    return spin


def _make_table(columns: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setObjectName("ResultTable")
    table.setHorizontalHeaderLabels(columns)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    return table


def _num(value: float | None) -> str:
    return _DASH if value is None else f"{value:.3f}"


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
