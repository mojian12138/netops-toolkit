"""H3C 维保查询页面测试（h3c.warranty）：假服务注入，不发起真实网络请求。"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from PyQt6.QtWidgets import QApplication

from netops.services import warranty as ws
from netops.ui.pages.warranty_page import WarrantyPage
from netops.ui.theme import apply_theme

SN_OK = "219801A7KQ5259C00001"
SN_BAD = "210235A1B2C3D4E5F6"
SN_MULTI = "219801A7KQ5259C00002"


def _entitlement(serial: str = SN_OK, *, end: str = "2027-09-09") -> ws.Entitlement:
    return ws.Entitlement(
        serial_number=serial,
        product_id="9801A7KQ",
        product_desc="H3C WA7220H-EI",
        product_line="无线产品线",
        ship_to_country="CN",
        last_end_date=end,
        out_of_insurance_days="307",
        warnings=(),
        warranties=(
            ws.Offer(
                offer_code="8813A04X",
                description="基本维保5x10xNBD发出",
                provider="Factory Warranty",
                parent_code="",
                start_date="2026-06-11",
                end_date=end,
                status="A",
            ),
        ),
        contracts=(),
        product_candidates=(),
    )


def _ok(index: int, serial: str) -> ws.WarrantyResult:
    return ws.WarrantyResult(
        index=index,
        serial_number=serial,
        product_number="",
        status="ok",
        entitlement=_entitlement(serial),
    )


def _error(
    index: int, serial: str, message: str = "序列号格式有误或不存在（e607）"
) -> ws.WarrantyResult:
    return ws.WarrantyResult(
        index=index, serial_number=serial, product_number="", status="error", error=message
    )


def _captcha(index: int, serial: str) -> ws.WarrantyResult:
    return ws.WarrantyResult(
        index=index,
        serial_number=serial,
        product_number="",
        status="captcha",
        error="服务端要求滑块验证码（need captcha）",
    )


def _skipped(index: int, serial: str) -> ws.WarrantyResult:
    return ws.WarrantyResult(
        index=index,
        serial_number=serial,
        product_number="",
        status="skipped",
        error="触发验证码，未查询（请在浏览器验证后重试）",
    )


def _report(
    results: list[ws.WarrantyResult], *, captcha_hit: bool = False, cancelled: bool = False
) -> ws.WarrantyReport:
    now = datetime.now()
    return ws.WarrantyReport(
        results=tuple(results),
        started_at=now,
        finished_at=now,
        duration_s=0.01,
        delay_s=0.5,
        third_party=False,
        cancelled=cancelled,
        captcha_hit=captcha_hit,
    )


class FakeService:
    """脚本化假服务：按预设结果序列逐条回调，并记录构造参数。"""

    calls: list[dict] = []
    script: list[ws.WarrantyResult] = []

    def __init__(
        self,
        pairs,
        *,
        delay_s=0.5,
        third_party=False,
        on_result=None,
        should_cancel=None,
        **_kwargs,
    ) -> None:
        FakeService.calls.append(
            {"pairs": tuple(pairs), "delay_s": delay_s, "third_party": third_party}
        )
        self.pairs = list(pairs)
        self._on_result = on_result

    async def run_async(self) -> ws.WarrantyReport:
        results: list[ws.WarrantyResult] = []
        for index, (sn, _pn) in enumerate(self.pairs):
            # 脚本按 (序号, SN) 匹配；找不到就用同 SN 的最后一条
            result = None
            for candidate in FakeService.script:
                if candidate.serial_number == sn and candidate.index == index:
                    result = candidate
            if result is None:
                for candidate in reversed(FakeService.script):
                    if candidate.serial_number == sn:
                        result = candidate
                        break
            if result is None:
                result = _ok(index, sn)
            results.append(result)
            if self._on_result is not None:
                self._on_result(result)
        return _report(results, captcha_hit=any(r.status == "captcha" for r in results))


@pytest.fixture(scope="module")
def app() -> QApplication:
    application = QApplication.instance() or QApplication(["pytest"])
    apply_theme(application)
    return application


@pytest.fixture()
def page(app) -> WarrantyPage:
    widget = WarrantyPage()
    widget.service_factory = FakeService
    FakeService.calls = []
    FakeService.script = []
    yield widget
    widget.shutdown()
    widget.deleteLater()


def _run(page: WarrantyPage, serials: str, script: list[ws.WarrantyResult]) -> None:
    FakeService.script = script
    page.set_serials(serials)
    assert page.start()
    page.run_blocking()


def test_defaults_are_idle(page: WarrantyPage) -> None:
    assert page.state() == "idle"
    assert page.start_button.isEnabled()
    assert not page.stop_button.isEnabled()
    assert not page.retry_button.isEnabled()
    assert not page.export_csv_button.isEnabled()
    assert page.delay_spin.value() == 500
    assert page.delay_spin.suffix() == " ms"
    assert page.captcha_banner_visible() is False
    assert page.table_rows() == []
    assert page.status_text() == "等待开始"


def test_start_requires_serials(page: WarrantyPage) -> None:
    page.set_serials("  \n# 只有注释\n")
    assert page.start() is False
    assert "至少输入一个序列号" in page.error_text()
    assert page.state() == "idle"


def test_over_limit_shows_error(page: WarrantyPage) -> None:
    page.set_serials("\n".join(f"SN{i:03d}" for i in range(201)))
    assert page.start() is False
    assert "上限" in page.error_text()


def test_single_query_success(page: WarrantyPage) -> None:
    _run(page, SN_OK, [_ok(0, SN_OK)])

    assert page.state() == "finished"
    rows = page.table_rows()
    assert len(rows) == 1
    assert rows[0][0] == "1"
    assert rows[0][1] == SN_OK
    assert rows[0][2] == ""  # 产品号（输入未带）
    assert rows[0][3] == "有效"
    assert rows[0][4] == "9801A7KQ"
    assert rows[0][7] == "2027-09-09"
    assert "天" in rows[0][8]  # 剩余天数列（具体数值随“今天”浮动）
    assert page.export_csv_button.isEnabled()
    assert "成功 1" in page.summary_text()
    assert not page.captcha_banner_visible()


def test_batch_rows_update_per_item(page: WarrantyPage) -> None:
    script = [_ok(0, "SN1"), _ok(1, "SN2"), _error(2, "SN3")]
    _run(page, "SN1\nSN2\nSN3", script)

    rows = page.table_rows()
    assert len(rows) == 3
    assert [row[0] for row in rows] == ["1", "2", "3"]
    assert rows[0][3] == "有效"
    assert rows[1][3] == "有效"
    assert rows[2][3] == "查询失败"
    assert "e607" in rows[2][9]
    assert "已查询 3/3" in page.status_text()
    assert "失败 1" in page.status_text()


def test_product_number_column_filled_from_input(page: WarrantyPage) -> None:
    """输入行带 PN 时，产品号列要回显。"""
    page.set_serials(f"{SN_MULTI} 9801A7KQ")
    FakeService.script = [
        ws.WarrantyResult(
            index=0,
            serial_number=SN_MULTI,
            product_number="9801A7KQ",
            status="ok",
            entitlement=_entitlement(SN_MULTI),
        )
    ]
    assert page.start()
    page.run_blocking()

    rows = page.table_rows()
    assert rows[0][2] == "9801A7KQ"


def test_expiring_and_expired_highlight(page: WarrantyPage) -> None:
    # 日期一律相对今天推导，避免测试随时间自然过期（曾经的硬编码炸弹）
    today = date.today()
    soon = _entitlement("SN1", end=(today + timedelta(days=30)).isoformat())  # 临期
    past = _entitlement("SN2", end=(today - timedelta(days=30)).isoformat())  # 已过保
    script = [
        ws.WarrantyResult(
            index=0, serial_number="SN1", product_number="", status="ok", entitlement=soon
        ),
        ws.WarrantyResult(
            index=1, serial_number="SN2", product_number="", status="ok", entitlement=past
        ),
    ]
    _run(page, "SN1\nSN2", script)

    rows = page.table_rows()
    assert rows[0][3] == "临期"
    assert rows[1][3] == "已过保"
    from PyQt6.QtGui import QColor  # noqa: PLC0415 - 测试内导入即可

    from netops.ui.pages.warranty_page import _EXPIRED_COLOR, _EXPIRING_COLOR  # noqa: PLC0415

    assert (
        rows[0]
        and page.table.item(0, 3).foreground().color().name() == QColor(_EXPIRING_COLOR).name()
    )
    assert page.table.item(1, 3).foreground().color().name() == QColor(_EXPIRED_COLOR).name()


def test_retry_failed_only_requeries_failures(page: WarrantyPage) -> None:
    script = [_ok(0, "SN1"), _error(1, "SN2"), _error(2, "SN3")]
    _run(page, "SN1\nSN2\nSN3", script)
    FakeService.calls.clear()
    FakeService.script = []

    assert page.retry_button.isEnabled()
    assert page.retry_failed() is True
    page.run_blocking()

    # 只重查两条失败的，成功行不动
    assert len(FakeService.calls) == 1
    assert FakeService.calls[0]["pairs"] == (("SN2", ""), ("SN3", ""))
    rows = page.table_rows()
    assert len(rows) == 3
    assert [row[1] for row in rows] == ["SN1", "SN2", "SN3"]
    assert [row[0] for row in rows] == ["1", "2", "3"]
    assert rows[1][3] == "有效"  # 重试成功（默认 _ok）
    assert rows[2][3] == "有效"


def test_retry_with_no_failures_shows_hint(page: WarrantyPage) -> None:
    _run(page, SN_OK, [_ok(0, SN_OK)])
    assert page.retry_failed() is False
    assert "没有需要重试的行" in page.error_text()


def test_captcha_banner_and_retry_flow(page: WarrantyPage) -> None:
    script = [
        _ok(0, "SN1"),
        _captcha(1, "SN2"),
        _skipped(2, "SN3"),
    ]
    _run(page, "SN1\nSN2\nSN3", script)

    assert page.captcha_banner_visible() is True
    assert page.open_page_button.isEnabled()
    assert page.retry_banner_button.isEnabled()
    rows = page.table_rows()
    assert rows[1][3] == "触发验证码"
    assert rows[2][3] == "未查询"
    assert "验证码" in rows[1][9]

    opened: list[str] = []
    page.open_page_button.clicked.disconnect()
    page.open_page_button.clicked.connect(lambda: opened.append(ws.PAGE_URL))
    page.open_page_button.click()
    assert opened == [ws.PAGE_URL]

    # 重试全部失败项（captcha + skipped），重试成功后横幅消失
    FakeService.calls.clear()
    FakeService.script = []
    assert page.retry_failed() is True
    page.run_blocking()
    assert FakeService.calls[0]["pairs"] == (("SN2", ""), ("SN3", ""))
    assert page.captcha_banner_visible() is False
    rows = page.table_rows()
    assert len(rows) == 3
    assert rows[1][3] == "有效"
    assert rows[2][3] == "有效"


def test_official_site_button_always_available(page: WarrantyPage) -> None:
    """输入卡上的「打开验证网站」按钮随时可点，不依赖查询状态。"""
    assert page.official_site_button.isEnabled()

    opened: list[str] = []
    page.official_site_button.clicked.disconnect()
    page.official_site_button.clicked.connect(lambda: opened.append(ws.PAGE_URL))
    page.official_site_button.click()
    assert opened == [ws.PAGE_URL]


def test_stop_is_noop_when_idle(page: WarrantyPage) -> None:
    page.stop()
    assert page.state() == "idle"


def test_clear_results_resets_table_and_export(page: WarrantyPage) -> None:
    _run(page, SN_OK, [_ok(0, SN_OK)])
    assert page.table_rows()
    assert page.clear_button.isEnabled()

    page.clear_results()
    assert page.table_rows() == []
    assert not page.export_csv_button.isEnabled()
    assert not page.clear_button.isEnabled()
    assert page.status_text() == "等待开始"


def test_export_csv(page: WarrantyPage, tmp_path) -> None:
    script = [_ok(0, SN_OK), _error(1, SN_BAD)]
    _run(page, f"{SN_OK}\n{SN_BAD}", script)

    target = tmp_path / "result.csv"
    assert page.export_csv(target) == 2
    content = target.read_text(encoding="utf-8-sig")
    # 概览（标签,值 两列）+ 总览段 + 明细段
    assert content.startswith("H3C 维保查询导出")
    assert "查询时间" in content
    assert "查询结果,共 2 条" in content
    assert "查询间隔,0.5 秒" in content
    assert "序列号,产品号,状态" in content
    assert SN_OK in content
    assert "e607" in content
    assert "服务明细" in content
    # 明细段包含保修行与失败行说明
    assert "8813A04X" in content
    assert "Factory Warranty" in content
    assert content.count("序列号,来源,合同号,服务项,提供方,开始日期,结束日期,状态,描述") == 1


def test_delay_and_third_party_passed_to_service(page: WarrantyPage) -> None:
    page.set_delay_ms(800)
    page.set_third_party(True)
    _run(page, SN_OK, [_ok(0, SN_OK)])

    assert FakeService.calls[0]["delay_s"] == pytest.approx(0.8)
    assert FakeService.calls[0]["third_party"] is True


def test_detail_table_shows_warranty_on_selection(page: WarrantyPage) -> None:
    _run(page, SN_OK, [_ok(0, SN_OK)])

    page.table.selectRow(0)
    rows = page.detail_rows()
    assert len(rows) == 1
    assert rows[0][0] == SN_OK  # 序列号列
    assert rows[0][1] == "保修"
    assert rows[0][3] == "8813A04X"
    assert rows[0][4] == "Factory Warranty"  # 提供方
    assert rows[0][7] == "有效"


def test_multi_product_detail_hint(page: WarrantyPage) -> None:
    multi = ws.Entitlement(
        serial_number=SN_MULTI,
        product_id="",
        product_desc="",
        product_line="",
        ship_to_country="",
        last_end_date=None,
        out_of_insurance_days=None,
        warnings=(),
        warranties=(),
        contracts=(),
        product_candidates=(ws.ProductBrief("9801A7KQ", "H3C WA7220H-EI", "CN-GD"),),
    )
    result = ws.WarrantyResult(
        index=0, serial_number=SN_MULTI, product_number="", status="ok", entitlement=multi
    )
    _run(page, SN_MULTI, [result])

    page.table.selectRow(0)
    rows = page.detail_rows()
    assert rows[0][1] == "多产品候选"
    assert rows[0][3] == "9801A7KQ"
    assert "请带 PN 重查" in rows[0][7]
    assert "CN-GD" in rows[0][8]


def test_detail_shows_error_row_note(page: WarrantyPage) -> None:
    """失败行选中时明细区给一行说明，不留白。"""
    script = [_ok(0, SN_OK), _error(1, SN_BAD)]
    _run(page, f"{SN_OK}\n{SN_BAD}", script)

    page.table.selectRow(1)
    rows = page.detail_rows()
    assert len(rows) == 1
    assert rows[0][0] == SN_BAD
    assert rows[0][1] == "查询失败"
    assert "e607" in rows[0][8]


def test_detail_refreshes_when_result_arrives_after_selection(page: WarrantyPage) -> None:
    """先点行后出结果：结果落到当前选中行时明细要自动刷新（用户实测反馈）。"""
    _run(page, SN_OK, [])
    # 模拟"行已选中、结果未回"：清掉结果后重建待查询行并选中
    page.clear_results()
    page._row_pairs = [(SN_OK, "", 1)]  # noqa: SLF001
    page.table.setRowCount(1)
    page._set_cell(0, 0, "1")  # noqa: SLF001
    page._set_cell(0, 1, SN_OK)  # noqa: SLF001
    page._set_cell(0, 3, "待查询")  # noqa: SLF001
    page.table.selectRow(0)
    assert page.detail_table.rowCount() == 0  # 结果未到，明细为空

    page._on_result(_ok(0, SN_OK))  # noqa: SLF001 - 结果落定要主动刷明细
    rows = page.detail_rows()
    assert len(rows) == 1
    assert rows[0][1] == "保修"
    assert rows[0][4] == "Factory Warranty"


def test_multi_contract_care_pack_detail(page: WarrantyPage) -> None:
    ent = _entitlement(SN_OK)
    care = ws.Contract(
        contract_id="CP888",
        is_care_pack=True,
        offers=(
            ws.Offer(
                offer_code="9991A11F",
                description="金牌服务 7x24",
                provider="H3C Care Pack",
                parent_code="PKG01",
                start_date="2026-06-11",
                end_date="2027-09-09",
                status="Z",
            ),
        ),
    )
    rich = ws.Entitlement(
        serial_number=SN_OK,
        product_id="9801A7KQ",
        product_desc="H3C WA7220H-EI",
        product_line="无线产品线",
        ship_to_country="CN",
        last_end_date="2027-09-09",
        out_of_insurance_days="307",
        warnings=("设备注册信息缺失",),
        warranties=ent.warranties,
        contracts=(care,),
        product_candidates=(),
    )
    result = ws.WarrantyResult(
        index=0, serial_number=SN_OK, product_number="", status="ok", entitlement=rich
    )
    _run(page, SN_OK, [result])

    page.table.selectRow(0)
    rows = page.detail_rows()
    assert len(rows) == 3  # 保修 1 + 金牌服务 1 + 备注 1
    assert rows[1][1] == "金牌服务"
    assert rows[1][2] == "CP888"  # 合同号
    assert rows[1][3] == "PKG01/9991A11F"  # 父服务项/服务项
    assert rows[1][7] == "当前有效"
    assert rows[2][1] == "备注"
    assert "设备注册信息缺失" in rows[2][8]
