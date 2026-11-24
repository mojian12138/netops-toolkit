"""H3C 维保查询服务测试：MockTransport 模拟接口，不发起真实请求。"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from netops.services import warranty as ws

SN_OK = "219801A7KQ5259C00001"
SN_MULTI = "210235A1B2C3D4E5F6"


def _entitlement_payload(serial: str = SN_OK) -> dict:
    return {
        "code": 200,
        "data": {
            "type": 0,
            "es": [
                {
                    "esHeader": {
                        "warnings": {"eiaErrorList": [{"errorText": "设备注册信息缺失，仅供参考"}]}
                    },
                    "combinedUnitEntitlement": {
                        "oos": {
                            "serialNumber": serial,
                            "shipToCountry": "CN",
                            "lastEndDate": "2027-09-09",
                            "outOfInsuranceDays": "307",
                            "product": {
                                "productId": "9801A7KQ",
                                "productDesc": "H3C WA7220H-EI",
                                "productLineDesc": "无线产品线",
                            },
                        },
                        "warrantyList": [
                            {
                                "offerList": [
                                    {
                                        "offerCode": "8813A04X",
                                        "offerDesc": "基本维保5x10xNBD发出",
                                        "standAloneOffer": "Factory Warranty",
                                        "appliesTo": {
                                            "startDate": "2026-06-11",
                                            "endDate": "2027-09-09",
                                            "status": "A",
                                        },
                                    }
                                ]
                            }
                        ],
                        "contractList": [
                            {
                                "hpCarePackSerialNumber": "CP123456",
                                "svcAggrementId": "AG999",
                                "offerList": [
                                    {
                                        "offerCode": "9991A11F",
                                        "offerDesc": "金牌服务",
                                        "pkgOffer": {"offerCode": "PKG01"},
                                        "appliesTo": {
                                            "startDate": "2026-06-11",
                                            "endDate": "2027-09-09",
                                            "status": "Z",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                }
            ],
        },
    }


def _multi_payload() -> dict:
    return {
        "code": 200,
        "data": {
            "type": 1,
            "es": [
                {
                    "productList": {
                        "productList": [
                            {
                                "productNumber": "9801A7KQ",
                                "productDesc": "H3C WA7220H-EI",
                                "functionalLocation": "CN-GD",
                            },
                            {
                                "productNumber": "3210A1BC",
                                "productDesc": "H3C S5130S-28P-EI",
                                "functionalLocation": "CN-BJ",
                            },
                        ]
                    }
                }
            ],
        },
    }


class Responder:
    """按 SN 返回预设响应，并记录收到的表单字段。"""

    def __init__(self, mapping: dict[str, object]) -> None:
        self.mapping = mapping
        self.requests: list[dict[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = dict(pair.split("=", 1) for pair in request.content.decode().split("&"))
        self.requests.append(body)
        sn = body.get("serialNumber") or ""
        value = self.mapping.get(sn, "ok")
        if value == "ok":
            return httpx.Response(200, json=_entitlement_payload(sn))
        if value == "multi":
            return httpx.Response(200, json=_multi_payload())
        if isinstance(value, int):  # HTTP 层状态码（如 429）
            return httpx.Response(value, json={"code": value, "message": "need captcha"})
        return httpx.Response(200, json=value)


def _factory(responder: Responder) -> ws.WarrantyQueryService:
    """把 MockTransport 绑进服务构造参数。"""

    def make_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=ws.BASE_URL, transport=httpx.MockTransport(responder))

    return make_client  # type: ignore[return-value]


async def _run(service: ws.WarrantyQueryService) -> ws.WarrantyReport:
    return await service.run_async()


# ---------------------------------------------------------------------------
# parse_entitlement
# ---------------------------------------------------------------------------


def test_parse_entitlement_success() -> None:
    ent = ws.parse_entitlement(_entitlement_payload())
    assert ent.serial_number == SN_OK
    assert ent.product_id == "9801A7KQ"
    assert ent.product_desc == "H3C WA7220H-EI"
    assert ent.product_line == "无线产品线"
    assert ent.ship_to_country == "CN"
    assert ent.last_end_date == "2027-09-09"
    assert ent.warnings == ("设备注册信息缺失，仅供参考",)
    assert len(ent.warranties) == 1
    offer = ent.warranties[0]
    assert offer.offer_code == "8813A04X"
    assert offer.provider == "Factory Warranty"
    assert offer.status_text == "有效"
    assert len(ent.contracts) == 1
    contract = ent.contracts[0]
    assert contract.is_care_pack is True
    assert contract.contract_id == "CP123456"
    assert contract.offers[0].parent_code == "PKG01"
    assert contract.offers[0].status_text == "当前有效"


def test_parse_entitlement_multi_product() -> None:
    ent = ws.parse_entitlement(_multi_payload())
    assert ent.product_id == ""
    assert len(ent.product_candidates) == 2
    assert ent.product_candidates[0].product_number == "9801A7KQ"
    assert ent.product_candidates[1].product_desc == "H3C S5130S-28P-EI"


def test_parse_entitlement_missing_es_raises() -> None:
    with pytest.raises(ws.QueryError):
        ws.parse_entitlement({"code": 200, "data": {"es": []}})


def test_status_text_mapping() -> None:
    assert ws.STATUS_TEXT["A"] == "有效"
    assert ws.STATUS_TEXT["X"] == "已失效"
    assert ws.STATUS_TEXT["Z"] == "当前有效"


# ---------------------------------------------------------------------------
# parse_pairs
# ---------------------------------------------------------------------------


def test_parse_pairs_mixed_lines() -> None:
    text = (
        "# 注释行\n"
        f"{SN_OK}\n"
        f"{SN_MULTI}  9801A7KQ\n"
        "\n"
        f"{SN_OK}\n"  # 完全重复，去重
        f"{SN_OK}　GLOBAL-PN\n"  # 全角空格
    )
    pairs = ws.parse_pairs(text)
    assert pairs == ((SN_OK, ""), (SN_MULTI, "9801A7KQ"), (SN_OK, "GLOBAL-PN"))


def test_parse_pairs_empty_raises() -> None:
    with pytest.raises(ws.WarrantyError, match="至少输入一个"):
        ws.parse_pairs("  \n# 只有注释\n")


def test_parse_pairs_over_limit_raises() -> None:
    text = "\n".join(f"SN{i:03d}" for i in range(201))
    with pytest.raises(ws.WarrantyError, match="上限"):
        ws.parse_pairs(text)


# ---------------------------------------------------------------------------
# entitlement_state
# ---------------------------------------------------------------------------


def _ent_with_end(end: str | None) -> ws.Entitlement:
    return ws.Entitlement(
        serial_number=SN_OK,
        product_id="X",
        product_desc="D",
        product_line="L",
        ship_to_country="CN",
        last_end_date=end,
        out_of_insurance_days=None,
        warnings=(),
        warranties=(),
        contracts=(),
        product_candidates=(),
    )


def test_entitlement_state_valid() -> None:
    state = ws.entitlement_state(_ent_with_end("2027-09-09"), today=date(2026, 11, 6))
    assert state.kind == "valid"
    assert state.label == "有效"


def test_entitlement_state_expiring_boundary() -> None:
    state = ws.entitlement_state(_ent_with_end("2027-02-04"), today=date(2026, 11, 6))
    assert state.kind == "expiring"
    assert "90" in state.days_text


def test_entitlement_state_expired() -> None:
    state = ws.entitlement_state(_ent_with_end("2026-01-01"), today=date(2026, 11, 6))
    assert state.kind == "expired"
    assert state.days is not None and state.days < 0
    # 用户实测反馈：已过保的剩余天数列必须写「已过期 N 天」，不能显示悬空天数
    assert state.days_text == f"已过期 {abs(state.days)} 天"


def test_entitlement_state_day_counts() -> None:
    """剩余/过期天数按日历日精确计算。"""
    state = ws.entitlement_state(_ent_with_end("2026-11-01"), today=date(2026, 11, 6))
    assert state.kind == "expired"
    assert state.days_text == "已过期 5 天"

    state = ws.entitlement_state(_ent_with_end("2026-11-07"), today=date(2026, 11, 6))
    assert state.kind == "expiring"
    assert state.days_text == "1 天"


def test_entitlement_state_unknown_and_multi() -> None:
    assert ws.entitlement_state(_ent_with_end(None)).kind == "unknown"
    assert ws.entitlement_state(_ent_with_end("not-a-date")).kind == "unknown"
    multi = ws.parse_entitlement(_multi_payload())
    assert ws.entitlement_state(multi).kind == "multi"


# ---------------------------------------------------------------------------
# WarrantyQueryService
# ---------------------------------------------------------------------------


def test_service_success_batch() -> None:
    responder = Responder({SN_OK: "ok", "SN2": "ok", "SN3": "ok"})
    seen: list[ws.WarrantyResult] = []
    service = ws.WarrantyQueryService(
        [(SN_OK, ""), ("SN2", ""), ("SN3", "")],
        delay_s=0.0,
        on_result=seen.append,
        client_factory=_factory(responder),
    )
    report = service.run()

    assert report.captcha_hit is False
    assert report.ok_count == 3
    assert [r.index for r in report.results] == [0, 1, 2]
    assert all(r.ok for r in report.results)
    assert report.results[0].entitlement is not None
    assert report.results[0].entitlement.product_id == "9801A7KQ"
    assert len(seen) == 3
    # 表单字段：语言固定 CN，第三方默认 N，_d 是毫秒时间戳
    assert responder.requests[0]["language"] == "CN"
    assert responder.requests[0]["mvs"] == "N"
    assert responder.requests[0]["productNumber"] == ""


def test_service_respects_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(ws.asyncio, "sleep", fake_sleep)
    responder = Responder({SN_OK: "ok", "SN2": "ok"})
    service = ws.WarrantyQueryService(
        [(SN_OK, ""), ("SN2", "")],
        delay_s=0.5,
        sleep=fake_sleep,
        client_factory=_factory(responder),
    )
    report = service.run()
    assert report.ok_count == 2
    # 首条前不等待；50ms 切片累加 = 0.5s
    assert sleeps and sum(sleeps) == pytest.approx(0.5)


def test_service_stops_on_captcha() -> None:
    responder = Responder({SN_OK: "ok", "SN2": 429, "SN3": "ok"})
    service = ws.WarrantyQueryService(
        [(SN_OK, ""), ("SN2", ""), ("SN3", "")],
        delay_s=0.0,
        client_factory=_factory(responder),
    )
    report = service.run()

    assert report.captcha_hit is True
    assert [r.status for r in report.results] == ["ok", "captcha", "skipped"]
    assert report.results[2].error.startswith("触发验证码")
    # 验证码后不再发请求：只收到前两条
    assert len(responder.requests) == 2
    assert report.failed[0].status == "captcha"


def test_service_continues_after_error() -> None:
    responder = Responder({SN_OK: "ok", "SN2": {"code": 400, "message": "e607 Serial invalid"}})
    service = ws.WarrantyQueryService(
        [(SN_OK, ""), ("SN2", ""), ("SN3", "")],
        delay_s=0.0,
        client_factory=_factory(responder),
    )
    report = service.run()

    assert [r.status for r in report.results] == ["ok", "error", "ok"]
    assert "序列号格式有误或不存在" in report.results[1].error


def test_service_multi_product_is_ok_result() -> None:
    responder = Responder({SN_MULTI: "multi"})
    service = ws.WarrantyQueryService(
        [(SN_MULTI, "")], delay_s=0.0, client_factory=_factory(responder)
    )
    report = service.run()
    result = report.results[0]
    assert result.ok
    assert result.entitlement is not None
    assert result.entitlement.product_candidates


def test_service_cancel_midway() -> None:
    responder = Responder({SN_OK: "ok", "SN2": "ok", "SN3": "ok"})
    state = {"checked": 0}

    def should_cancel() -> bool:
        state["checked"] += 1
        return state["checked"] > 2  # 第 2 条之后开始取消

    service = ws.WarrantyQueryService(
        [(SN_OK, ""), ("SN2", ""), ("SN3", "")],
        delay_s=0.0,
        on_result=lambda _r: None,
        should_cancel=should_cancel,
        client_factory=_factory(responder),
    )
    report = service.run()
    assert report.cancelled is True
    statuses = [r.status for r in report.results]
    assert statuses[0] == "ok"
    assert "skipped" in statuses


def test_service_max_queries_cap() -> None:
    responder = Responder({SN_OK: "ok", "SN2": "ok", "SN3": "ok"})
    service = ws.WarrantyQueryService(
        [(SN_OK, ""), ("SN2", ""), ("SN3", "")],
        delay_s=0.0,
        max_queries=2,
        client_factory=_factory(responder),
    )
    report = service.run()
    assert [r.status for r in report.results] == ["ok", "ok", "skipped"]
    assert len(responder.requests) == 2


def test_service_third_party_mvs_y() -> None:
    responder = Responder({SN_OK: "ok"})
    service = ws.WarrantyQueryService(
        [(SN_OK, "")], delay_s=0.0, third_party=True, client_factory=_factory(responder)
    )
    service.run()
    assert responder.requests[0]["mvs"] == "Y"


def test_service_http_level_429_is_captcha() -> None:
    responder = Responder({SN_OK: 429})
    service = ws.WarrantyQueryService(
        [(SN_OK, "")], delay_s=0.0, client_factory=_factory(responder)
    )
    report = service.run()
    assert report.results[0].status == "captcha"
    assert report.captcha_hit is True


def test_service_network_error_keeps_going() -> None:
    def responder(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    def make_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=ws.BASE_URL, transport=httpx.MockTransport(responder))

    service = ws.WarrantyQueryService(
        [(SN_OK, ""), ("SN2", "")], delay_s=0.0, client_factory=make_client
    )
    report = service.run()
    assert all(r.status == "error" for r in report.results)
    assert "网络错误" in report.results[0].error


def test_service_invalid_params_raise() -> None:
    with pytest.raises(ws.WarrantyError):
        ws.WarrantyQueryService([])
    with pytest.raises(ws.WarrantyError, match="间隔"):
        ws.WarrantyQueryService([(SN_OK, "")], delay_s=99.0)


def test_report_csv_rows() -> None:
    responder = Responder({SN_OK: "ok", "SN2": {"code": 400, "message": "e607 x"}})
    service = ws.WarrantyQueryService(
        [(SN_OK, ""), ("SN2", "")], delay_s=0.0, client_factory=_factory(responder)
    )
    report = service.run()
    rows = report.csv_rows()
    assert len(rows) == 3
    assert rows[0][0] == "序列号"
    assert rows[1][2] == "有效"
    assert "e607" in rows[2][-1]


def test_report_detail_rows() -> None:
    """明细行：保修提供方、金牌服务合同号、备注都在，非 ok 行有一行说明。"""
    responder = Responder({SN_OK: "ok", "SN2": {"code": 400, "message": "e607 x"}})
    service = ws.WarrantyQueryService(
        [(SN_OK, ""), ("SN2", "")], delay_s=0.0, client_factory=_factory(responder)
    )
    report = service.run()
    detail = report.csv_detail_rows()

    assert detail[0] == list(ws.DETAIL_HEADER)
    # SN_OK：保修 + 金牌服务 + 备注 = 3 行
    sn1_rows = [row for row in detail if row[0] == SN_OK]
    assert len(sn1_rows) == 3
    assert sn1_rows[0][1] == "保修"
    assert sn1_rows[0][4] == "Factory Warranty"
    assert sn1_rows[0][7] == "有效"
    assert sn1_rows[1][1] == "金牌服务"
    assert sn1_rows[1][2] == "CP123456"
    # SN2：失败行说明
    sn2_rows = [row for row in detail if row[0] == "SN2"]
    assert len(sn2_rows) == 1
    assert sn2_rows[0][1] == "查询失败"
    assert "e607" in sn2_rows[0][8]


def test_report_detail_rows_care_pack_and_warning() -> None:
    payload = _entitlement_payload()
    # 加一条金牌服务合同 + 备注，验证明细行完整
    payload["data"]["es"][0]["combinedUnitEntitlement"]["contractList"].append(
        {
            "hpCarePackSerialNumber": "CP888",
            "offerList": [
                {
                    "offerCode": "9991A11F",
                    "offerDesc": "金牌服务 7x24",
                    "pkgOffer": {"offerCode": "PKG01"},
                    "appliesTo": {
                        "startDate": "2026-06-11",
                        "endDate": "2027-09-09",
                        "status": "Z",
                    },
                }
            ],
        }
    )
    responder = Responder({SN_OK: payload})
    service = ws.WarrantyQueryService(
        [(SN_OK, "")], delay_s=0.0, client_factory=_factory(responder)
    )
    report = service.run()
    detail = report.csv_detail_rows()

    assert len(detail) == 5  # 表头 + 保修 + 原合同(CP123456) + 新金牌服务 + 备注
    care = detail[3]
    assert care[1] == "金牌服务"
    assert care[2] == "CP888"
    assert care[3] == "PKG01/9991A11F"
    assert care[7] == "当前有效"
    assert detail[4][1] == "备注"
    assert "设备注册信息缺失" in detail[4][8]


def test_report_export_rows_structure() -> None:
    """完整导出：概览（标签,值两列）→ 总览 → 明细，各段齐备且不错位。"""
    responder = Responder({SN_OK: "ok", "SN2": {"code": 400, "message": "e607 x"}})
    service = ws.WarrantyQueryService(
        [(SN_OK, ""), ("SN2", "")], delay_s=0.5, client_factory=_factory(responder)
    )
    report = service.run()
    rows = report.export_rows()

    assert rows[0] == ["H3C 维保查询导出"]
    assert rows[1][0] == "查询时间"
    assert rows[2][0] == "查询结果"
    assert "共 2 条" in rows[2][1]
    assert rows[3][0] == "查询间隔" and rows[3][1] == "0.5 秒"
    assert rows[4][0] == "第三方设备" and rows[4][1] == "否"
    # 总览段表头 9 列，数据行同为 9 列（标题与内容列数一致）
    assert any(row == list(_CSV_HEADER) for row in rows)
    assert any(row and row[0] == "── 服务明细 ──" for row in rows)
    assert any(row == list(ws.DETAIL_HEADER) for row in rows)
    assert any("8813A04X" in (row[3] if len(row) > 3 else "") for row in rows)


_CSV_HEADER = [
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


def test_report_csv_rows_column_alignment() -> None:
    """总览表头与数据行列数一致（用户反馈过的错位回归）。"""
    responder = Responder({SN_OK: "ok", "SN2": {"code": 400, "message": "e607 x"}})
    service = ws.WarrantyQueryService(
        [(SN_OK, "9801A7KQ"), ("SN2", "")], delay_s=0.0, client_factory=_factory(responder)
    )
    report = service.run()
    rows = report.csv_rows()
    assert rows[0] == _CSV_HEADER
    for row in rows[1:]:
        assert len(row) == len(_CSV_HEADER)
    # 第二列是产品号（来自输入），第三列才是状态
    assert rows[1][1] == "9801A7KQ"
    assert rows[1][2] == "有效"
    assert rows[2][1] == ""
    assert rows[2][2] == "查询失败"


def test_detail_rows_multi_product() -> None:
    payload = _multi_payload()
    responder = Responder({SN_MULTI: payload})
    service = ws.WarrantyQueryService(
        [(SN_MULTI, "")], delay_s=0.0, client_factory=_factory(responder)
    )
    report = service.run()
    detail = report.csv_detail_rows()
    assert len(detail) == 3  # 表头 + 2 个候选产品
    assert detail[1][1] == "多产品候选"
    assert detail[1][3] == "9801A7KQ"
    assert "CN-GD" in detail[1][8]


async def test_run_async_direct() -> None:
    responder = Responder({SN_OK: "ok"})
    service = ws.WarrantyQueryService(
        [(SN_OK, "")], delay_s=0.0, client_factory=_factory(responder)
    )
    report = await _run(service)
    assert report.ok_count == 1
