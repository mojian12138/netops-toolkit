"""H3C 维保查询页面（h3c.warranty）。

三个区域：查询输入（多行 SN + 间隔/第三方）→ 验证码横幅（触发时出现）→ 结果表 + 明细。
查询逻辑全在 ``services/warranty.py``：串行请求 + 可调间隔，验证码即熔断；
工作者线程的 ``on_result`` 经 ``pyqtSignal`` 回主线程逐行刷新，UI 永不阻塞。
"""

from __future__ import annotations

import csv
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
)

from netops.services import warranty as warranty_service
from netops.services.warranty import WarrantyError
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

#: 结果表列（与 ``WarrantyReport.csv_rows`` 的表头一致，导出直接复用；共 10 列含行号）。
_COLUMNS = [
    "#",
    "序列号",
    "产品号",
    "状态",
    "产品型号",
    "产品描述",
    "产品线",
    "维保截止",
    "剩余天数",
    "备注 / 错误",
]

#: 明细表列：选中结果行后展示保修 / 合同服务项（与服务层 DETAIL_HEADER 一致，
#: 序列号列在表格里冗余展示，便于截图 / 复制单行时信息完整）。
_DETAIL_COLUMNS = [
    "序列号",
    "来源",
    "合同号",
    "服务项",
    "提供方",
    "开始日期",
    "结束日期",
    "状态",
    "描述",
]

_INPUT_PLACEHOLDER = (
    "每行一个序列号，也可写「SN 产品号」，# 开头为注释：\n"
    "219801A7KQ5259C00001\n"
    "210235A1B2C3D4E5F6 9801A7KQ\n"
    "# 上面第二行带产品号，用于 SN 命中多个产品时精确查询"
)

_VALID_COLOR = "#15803d"  # 有效
_EXPIRING_COLOR = "#b45309"  # 临期（≤90 天）
_EXPIRED_COLOR = "#b91c1c"  # 已过保
_ERROR_COLOR = "#b91c1c"  # 查询失败
_MUTED_COLOR = "#64748b"  # 验证码 / 未查询
_PENDING_COLOR = "#94a3b8"  # 待查询 / 重试中

_MAX_DELAY_MS = 10_000


class WarrantyPage(ToolPage):
    def __init__(self, parent=None) -> None:
        super().__init__(
            title="H3C 维保查询",
            subtitle="按序列号批量查询设备维保状态，临期与过保高亮；触发官方验证码时熔断并引导浏览器验证。",
            parent=parent,
        )
        self._runner = TaskRunner(self, max_threads=1)
        self._handle: TaskHandle | None = None
        self._state = "idle"
        self._stop_requested = False
        self._report: warranty_service.WarrantyReport | None = None
        #: 行号 → (SN, PN, 显示序号)；重试时保持原显示序号不变。
        self._row_pairs: list[tuple[str, str, int]] = []
        #: 显示序号 → 最新结果。用表格第 0 列的显示序号做键（而不是物理行号），
        #: 首查与重试共用一套行↔结果映射，避免两套下标体系错位。
        self._results: dict[int, warranty_service.WarrantyResult] = {}

        #: 测试注入点（对齐 ping 页 runner_factory 惯例）：替换服务构造。
        self.service_factory: Callable[..., warranty_service.WarrantyQueryService] = (
            warranty_service.WarrantyQueryService
        )

        self._bridge = _ResultBridge(parent=self)
        self._bridge.result.connect(self._on_result)

        self._build_input_card()
        self._build_captcha_banner()
        self._build_result_card()
        self._set_state("idle")

    # -- 界面搭建 -----------------------------------------------------------
    def _build_input_card(self) -> None:
        card = result_card("查询输入", "每行一个序列号，也可写「SN 产品号」；# 开头为注释")
        self.serials_input = make_text_edit(_INPUT_PLACEHOLDER, rows=6)
        card.body.addWidget(self.serials_input)

        params = QGridLayout()
        params.setHorizontalSpacing(18)
        params.setVerticalSpacing(10)

        self.delay_spin = QSpinBox()
        self.delay_spin.setObjectName("ToolInput")
        self.delay_spin.setRange(100, _MAX_DELAY_MS)
        self.delay_spin.setSingleStep(100)
        self.delay_spin.setValue(int(warranty_service.DEFAULT_DELAY_S * 1000))
        self.delay_spin.setSuffix(" ms")
        self.delay_spin.setToolTip(
            "两条查询之间的间隔。连续查询约 "
            f"{warranty_service.CAPTCHA_HINT_QUERIES} 次可能触发官方滑块验证码，"
            "触发后本批自动停止，可在浏览器验证后重试失败项"
        )
        params.addWidget(FieldRow("查询间隔", self.delay_spin, hint="毫秒", label_width=72), 0, 0)

        self.third_party_check = QCheckBox("第三方设备（mvs=Y）")
        self.third_party_check.setObjectName("ToolCheck")
        params.addWidget(self.third_party_check, 0, 1)

        # 手动验证入口：任何时候都可以打开官方页面（查询/验证码配额异常时自行验证）
        self.official_site_button = SecondaryButton("打开验证网站")
        self.official_site_button.setToolTip(
            f"打开 H3C 官方维保查询页（{warranty_service.PAGE_URL}），"
            "可手动查询或完成滑块验证后回来重试失败项"
        )
        self.official_site_button.clicked.connect(self._open_browser)
        params.addWidget(self.official_site_button, 0, 2)
        params.setColumnStretch(0, 1)
        params.setColumnStretch(1, 1)
        params.setColumnStretch(2, 1)
        card.body.addLayout(params)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.start_button = PrimaryButton("开始查询")
        self.start_button.clicked.connect(self.start)
        self.stop_button = SecondaryButton("停止")
        self.stop_button.clicked.connect(self.stop)
        self.retry_button = SecondaryButton("重试失败项")
        self.retry_button.clicked.connect(self.retry_failed)
        self.import_button = SecondaryButton("导入文件…")
        self.import_button.clicked.connect(self._choose_import_file)
        self.clear_button = SecondaryButton("清空结果")
        self.clear_button.clicked.connect(self.clear_results)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        buttons.addWidget(self.retry_button)
        buttons.addWidget(self.import_button)
        buttons.addStretch(1)
        buttons.addWidget(self.clear_button)
        card.body.addLayout(buttons)

        self.error = InlineError()
        card.body.addWidget(self.error)
        self.progress_label = label("等待开始", "EmptyHint")
        card.body.addWidget(self.progress_label)
        self.add_card(card)

    def _build_captcha_banner(self) -> None:
        """验证码横幅：默认隐藏，触发 429 时出现，引导浏览器验证后重试。"""
        self.captcha_banner = QFrame()
        self.captcha_banner.setObjectName("WarnNotice")
        self.captcha_banner.hide()
        layout = QHBoxLayout(self.captcha_banner)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)
        text = label(
            "已触发官方滑块验证码，本批查询停止。请在浏览器打开官方页面完成一次验证，"
            "然后点「验证后重试失败项」。",
            "WarnNoticeText",
        )
        text.setWordWrap(True)
        layout.addWidget(text, 1)
        self.open_page_button = PrimaryButton("打开官方页面")
        self.open_page_button.clicked.connect(self._open_browser)
        layout.addWidget(self.open_page_button)
        self.retry_banner_button = SecondaryButton("验证后重试失败项")
        self.retry_banner_button.clicked.connect(self.retry_failed)
        layout.addWidget(self.retry_banner_button)
        self.body.addWidget(self.captcha_banner)

    def _build_result_card(self) -> None:
        card = result_card("查询结果", "临期（90 天内）与已过保行高亮；点击行查看保修 / 合同明细")

        self.table = _make_table(_COLUMNS)
        header = self.table.horizontalHeader()
        for column in (0, 2, 3, 4, 7, 8):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (5, 6, 9):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(200)
        self.table.setMaximumHeight(280)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        card.body.addWidget(self.table)

        card.body.addWidget(separator_line())
        card.body.addWidget(label("服务明细（选中结果行后显示）", "CardTitle"))
        self.detail_table = _make_table(_DETAIL_COLUMNS)
        detail_header = self.detail_table.horizontalHeader()
        for column in (0, 1, 2, 3, 4, 7):
            detail_header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        detail_header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        detail_header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        detail_header.setSectionResizeMode(8, QHeaderView.ResizeMode.Stretch)
        self.detail_table.setMinimumHeight(120)
        self.detail_table.setMaximumHeight(220)
        card.body.addWidget(self.detail_table)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.summary_label = label("等待查询", "EmptyHint")
        footer.addWidget(self.summary_label, 1)
        self.export_csv_button = SecondaryButton("导出 CSV")
        self.export_csv_button.clicked.connect(self._choose_export_csv)
        self.export_csv_button.setEnabled(False)
        footer.addWidget(self.export_csv_button)
        card.body.addLayout(footer)
        self.add_card(card, 1)

    # -- 状态机 -------------------------------------------------------------
    def _set_state(self, state: str) -> None:
        self._state = state
        running = state == "running"
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        editable = not running
        for widget in (
            self.serials_input,
            self.delay_spin,
            self.third_party_check,
            self.import_button,
        ):
            widget.setEnabled(editable)
        self.clear_button.setEnabled(not running and self._has_any_result())
        self.export_csv_button.setEnabled(self._report is not None and bool(self._report.results))
        self._refresh_retry_buttons()
        # 打开验证网站始终可用（不依赖查询状态），验证码横幅内的按钮查询中禁用
        self.official_site_button.setEnabled(True)
        for widget in (self.open_page_button, self.retry_banner_button):
            widget.setEnabled(not running)

    def _refresh_retry_buttons(self) -> None:
        has_retryable = any(item.retryable for item in self._results.values())
        self.retry_button.setEnabled(self._state != "running" and has_retryable)

    def _has_any_result(self) -> bool:
        return bool(self._results)

    def state(self) -> str:
        return self._state

    # -- 控制 ---------------------------------------------------------------
    def start(self) -> bool:
        """开始新一轮查询；输入非法时返回 ``False`` 并在页面提示。"""
        self.error.clear_error()
        try:
            pairs = warranty_service.parse_pairs(self.serials_input.toPlainText())
        except WarrantyError as exc:
            self.error.show_error(str(exc))
            return False
        return self._run_batch(pairs, retry=False)

    def retry_failed(self) -> bool:
        """只重查 captcha / error / skipped 的行，结果按原显示序号回写。"""
        if self._state == "running":
            return False
        self.error.clear_error()
        failed = [item for item in self._results.values() if item.retryable]
        if not failed:
            self.error.show_error("没有需要重试的行")
            return False
        pairs = tuple((item.serial_number, item.product_number) for item in failed)
        return self._run_batch(pairs, retry=True)

    def _run_batch(self, pairs: tuple[tuple[str, str], ...], *, retry: bool) -> bool:
        if retry:
            # 重试不清表：沿用原显示序号，把原行状态改「重试中」。
            row_pairs = [
                (sn, pn, self._display_no_of(sn, pn, offset + 1))
                for offset, (sn, pn) in enumerate(pairs)
            ]
        else:
            self.clear_results()
            row_pairs = [(sn, pn, offset + 1) for offset, (sn, pn) in enumerate(pairs)]
        self._row_pairs = row_pairs
        if not retry:
            self._ensure_table_rows()
        else:
            self._mark_retry_rows()
        self._hide_captcha_banner()

        session = self._build_session(pairs)
        self._stop_requested = False
        self._set_state("running")
        self.progress_label.setText(f"准备{'重试' if retry else '查询'} {len(pairs)} 条…")
        handle = self._runner.submit_async(session.run_async)
        handle.finished.connect(self._on_finished)
        handle.failed.connect(self._on_failed)
        handle.cancelled.connect(self._on_cancelled)
        self._handle = handle
        return True

    def _build_session(self, pairs: tuple[tuple[str, str], ...]):
        """构造服务会话：把本批行的显示序号透传给服务，结果按序号回写。"""
        display_nos = [display_no for _sn, _pn, display_no in self._row_pairs]
        return self.service_factory(
            pairs,
            delay_s=self.delay_spin.value() / 1000,
            third_party=self.third_party_check.isChecked(),
            on_result=self._bridge.emit_result,
            should_cancel=self._should_cancel,
            display_nos=display_nos,
        )

    def stop(self) -> None:
        if self._state != "running":
            return
        # 与 Ping 页一致：不调用 handle.cancel()，让服务自己收尾并返回部分结果，
        # 直接取消令牌会让 TaskRunner 丢弃结果，已查到的行就没了。
        self._stop_requested = True
        self.progress_label.setText("正在停止（当前查询完成后收尾）…")

    def clear_results(self) -> None:
        self._report = None
        self._results = {}
        self._row_pairs = []
        self.table.setRowCount(0)
        self.detail_table.setRowCount(0)
        self.summary_label.setText("等待查询")
        self.progress_label.setText("等待开始")
        self.export_csv_button.setEnabled(False)
        self.retry_button.setEnabled(False)
        self.clear_button.setEnabled(False)
        self._hide_captcha_banner()

    def shutdown(self) -> None:
        """页面销毁前调用：取消任务、回收线程。"""
        self._runner.shutdown()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 接口
        self.shutdown()
        super().closeEvent(event)

    def _should_cancel(self) -> bool:
        # 除了停止按钮，页面销毁（shutdown → cancel_all）也要能中断整批查询。
        return self._stop_requested or (self._handle is not None and self._handle.token.cancelled)

    # -- 验证码横幅 ----------------------------------------------------------
    def _open_browser(self) -> bool:
        """打开官方查询页完成滑块验证；测试里会被替换。"""
        return QDesktopServices.openUrl(QUrl(warranty_service.PAGE_URL))

    def _show_captcha_banner(self) -> None:
        self.captcha_banner.show()

    def _hide_captcha_banner(self) -> None:
        self.captcha_banner.hide()

    def captcha_banner_visible(self) -> bool:
        return not self.captcha_banner.isHidden()

    # -- 表格渲染 ------------------------------------------------------------
    def _ensure_table_rows(self) -> None:
        """新批次：为每条输入预建一行，状态「待查询」。"""
        self.table.setRowCount(len(self._row_pairs))
        for row, (sn, pn, display_no) in enumerate(self._row_pairs):
            self._set_cell(row, 0, str(display_no))
            self._set_cell(row, 1, sn)
            self._set_cell(row, 2, pn)
            self._set_cell(row, 3, "待查询", _PENDING_COLOR)
            for column in range(4, len(_COLUMNS)):
                self._set_cell(row, column, "")

    def _mark_retry_rows(self) -> None:
        """重试批次：把对应行状态改「重试中」，其余行原样保留。"""
        for item in self._row_pairs:
            row = self._row_of(item)
            if row is not None:
                self._set_cell(row, 2, "重试中", _PENDING_COLOR)

    def _row_of(self, entry: tuple[str, str, int]) -> int | None:
        """按 (SN, 显示序号) 找物理行号；找不到返回 None。"""
        sn, _pn, display_no = entry
        return self._row_by_display_no(sn, display_no)

    def _display_no_of(self, sn: str, pn: str, fallback: int) -> int:
        """重试行沿用上一次结果的显示序号；找不到就顺延。"""
        for item in self._results.values():
            if item.serial_number == sn and item.product_number == pn:
                return item.display_no
        return fallback

    def _set_cell(self, row: int, column: int, text: str, color: str | None = None) -> None:
        item = QTableWidgetItem(text)
        if color is not None:
            item.setForeground(QColor(color))
        self.table.setItem(row, column, item)

    def _on_result(self, result: object) -> None:
        """逐条结果（主线程）：按 index 找到对应行覆盖，结果按显示序号存档。"""
        if not isinstance(result, warranty_service.WarrantyResult):  # pragma: no cover - 防御
            return
        row = self._row_for_result(result)
        if row is None:  # pragma: no cover - 行映射失败不该发生
            return
        self._results[result.display_no] = result
        cells = warranty_service.result_cells(result)
        self._set_cell(row, 2, cells[1])
        self._set_cell(row, 3, cells[2], self._status_color(result))
        # cells: [序列号, 产品号, 状态, 型号, 描述, 产品线, 截止, 剩余, 备注] → 表格列 4..9
        for column, value in zip((4, 5, 6, 7, 8, 9), cells[3:], strict=False):
            if result.status == "ok":
                self._set_cell(
                    row, column, value, self._status_color(result) if column == 4 else None
                )
            else:
                self._set_cell(row, column, value, _ERROR_COLOR if column == 9 else _MUTED_COLOR)
        if result.status == "ok" and result.entitlement is not None:
            tooltip = warranty_service.describe_entitlement(result.entitlement)
            for column in range(len(_COLUMNS)):
                cell = self.table.item(row, column)
                if cell is not None:
                    cell.setToolTip(tooltip)
        # 行内容落定后，若用户正选中这一行（常见：查询中先点行，结果后到），
        # 选择没变化就不会触发 itemSelectionChanged —— 这里主动刷新明细。
        if {index.row() for index in self.table.selectedIndexes()} == {row}:
            self._on_selection_changed()
        self._refresh_progress()
        self._refresh_retry_buttons()
        self.clear_button.setEnabled(True)

    def _row_for_result(self, result: warranty_service.WarrantyResult) -> int | None:
        """结果 index → 物理行号：本轮第 index 项对应的行。"""
        if 0 <= result.index < len(self._row_pairs):
            _sn, _pn, display_no = self._row_pairs[result.index]
            return self._row_by_display_no(_sn, display_no)
        return None

    def _row_by_display_no(self, sn: str, display_no: int) -> int | None:
        for row in range(self.table.rowCount()):
            number_item = self.table.item(row, 0)
            sn_item = self.table.item(row, 1)
            if (
                sn_item is not None
                and sn_item.text() == sn
                and number_item is not None
                and number_item.text() == str(display_no)
            ):
                return row
        return None

    @staticmethod
    def _status_color(result: warranty_service.WarrantyResult) -> str | None:
        if result.status != "ok" or result.entitlement is None:
            return _ERROR_COLOR if result.status == "error" else _MUTED_COLOR
        state = warranty_service.entitlement_state(result.entitlement)
        return {
            "valid": _VALID_COLOR,
            "expiring": _EXPIRING_COLOR,
            "expired": _EXPIRED_COLOR,
            "multi": _EXPIRING_COLOR,
        }.get(state.kind, _MUTED_COLOR)

    def _refresh_progress(self) -> None:
        done = self._done_count()
        failed = sum(1 for item in self._results.values() if item.status == "error")
        total = len(self._row_pairs)
        self.progress_label.setText(
            f"已查询 {done}/{total}" + (f" · 失败 {failed}" if failed else "")
        )

    def _done_count(self) -> int:
        return len(self._results)

    # -- 明细 -----------------------------------------------------------------
    def _on_selection_changed(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        self.detail_table.setRowCount(0)
        if len(rows) != 1:
            return
        result = self._result_at_row(rows[0])
        if result is None:
            return
        # 明细直接吃服务层 detail_rows：界面与导出内容一致；失败/未查询行也有一行说明
        self._fill_detail_rows(warranty_service.detail_rows(result))

    def _fill_detail_rows(self, rows: list[list[str]]) -> None:
        self.detail_table.setRowCount(len(rows))
        for row, cells in enumerate(rows):
            for column, value in enumerate(cells):
                self.detail_table.setItem(row, column, QTableWidgetItem(value))

    def _result_at_row(self, row: int) -> warranty_service.WarrantyResult | None:
        """物理行号 → 最新结果：读该行第 0 列的显示序号，再查结果档案。"""
        number_item = self.table.item(row, 0)
        if number_item is None:
            return None
        try:
            display_no = int(number_item.text())
        except ValueError:  # pragma: no cover - 行号列恒为数字
            return None
        return self._results.get(display_no)

    # -- 完成 / 失败 ----------------------------------------------------------
    def _on_finished(self, report: object) -> None:
        self._handle = None
        if not isinstance(report, warranty_service.WarrantyReport):  # pragma: no cover - 防御
            self._on_failed("查询返回了意外结果")
            return
        self._report = report
        if report.captcha_hit:
            self._show_captcha_banner()
        tail = "（已停止）" if report.cancelled else ""
        self.summary_label.setText(
            f"共 {len(report.results)} 条：成功 {report.ok_count}，失败 {report.error_count}，"
            f"未查询 {report.skipped_count}{tail}"
        )
        done_text = f"已查询 {self._done_count()}/{len(self._row_pairs)}"
        if report.error_count:
            done_text += f" · 失败 {report.error_count}"
        self.progress_label.setText(done_text)
        self._set_state("finished")

    def _on_failed(self, message: str) -> None:
        self._handle = None
        self._set_state("idle")
        self.error.show_error(f"查询失败：{message}")
        self.progress_label.setText("查询失败")

    def _on_cancelled(self) -> None:
        self._handle = None
        self._set_state("finished")
        self.progress_label.setText("已取消")

    # -- 文件 -----------------------------------------------------------------
    def _choose_import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入序列号文件", "", "文本文件 (*.txt);;CSV 文件 (*.csv);;所有文件 (*.*)"
        )
        if not path:
            return
        text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
        self.serials_input.setPlainText(text)

    def _choose_export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 CSV", "warranty-result.csv", "CSV 文件 (*.csv)"
        )
        if path:
            self.export_csv(path)

    def export_csv(self, path: str | Path) -> int:
        """导出完整内容：概览 → 总览表 → 服务明细段；返回总览行数。"""
        if self._report is None:
            return 0
        rows = self._report.export_rows()
        with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
            csv.writer(handle).writerows(rows)
        return len(self._report.results)

    # -- 测试 / 外部接口 ----------------------------------------------------
    def set_serials(self, text: str) -> None:
        self.serials_input.setPlainText(text)

    def set_delay_ms(self, ms: int) -> None:
        self.delay_spin.setValue(ms)

    def set_third_party(self, checked: bool) -> None:
        self.third_party_check.setChecked(checked)

    def run_blocking(self, *, timeout_ms: int = 5) -> None:
        """测试辅助：等待后台任务结束并处理事件，让信号在本线程落定。"""
        if self._handle is not None:
            self._runner.wait_for_idle(30_000)
        _process_events(timeout_ms)

    def table_rows(self) -> list[list[str]]:
        table = self.table
        return [
            [
                table.item(row, column).text() if table.item(row, column) else ""
                for column in range(table.columnCount())
            ]
            for row in range(table.rowCount())
        ]

    def detail_rows(self) -> list[list[str]]:
        table = self.detail_table
        return [
            [
                table.item(row, column).text() if table.item(row, column) else ""
                for column in range(table.columnCount())
            ]
            for row in range(table.rowCount())
        ]

    def status_text(self) -> str:
        return self.progress_label.text()

    def summary_text(self) -> str:
        return self.summary_label.text()

    def error_text(self) -> str:
        return self.error.text()


class _ResultBridge(QObject):
    """把工作者线程的结果回调转成 Qt 信号——保证 UI 改动只发生在主线程。"""

    result = pyqtSignal(object)

    def emit_result(self, result: warranty_service.WarrantyResult) -> None:
        self.result.emit(result)


def _make_table(columns: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setObjectName("ResultTable")
    table.setHorizontalHeaderLabels(columns)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    return table


def _process_events(timeout_ms: int) -> None:
    import time as _time

    from PyQt6.QtWidgets import QApplication

    deadline = _time.monotonic() + timeout_ms / 1000
    application = QApplication.instance()
    if application is None:  # pragma: no cover - 测试环境总先建 QApplication
        return
    while _time.monotonic() < deadline:
        application.processEvents()
        _time.sleep(0.005)
