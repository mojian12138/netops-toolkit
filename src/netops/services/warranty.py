"""H3C 维保查询服务（es.h3c.com entitlement 接口）。

数据来源：``tests/h3c/es.h3c.com.har`` 抓包分析（接口无需登录）。
本模块零 Qt 依赖，UI 侧通过 ``on_result`` 回调（工作者线程）+ 信号桥接收逐条结果。

限流策略：**严格串行** + 固定间隔（默认 500ms）。连续查询约 20 次会触发官方
滑块验证码（body code=429）；触发即熔断——本批剩余条目记 ``skipped``，
不重试、不绕过，由 UI 引导用户到浏览器完成一次验证后重试失败项。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx

__all__ = [
    "BASE_URL",
    "CAPTCHA_HINT_QUERIES",
    "CaptchaRequiredError",
    "DEFAULT_DELAY_S",
    "DEFAULT_TIMEOUT_S",
    "DETAIL_HEADER",
    "Entitlement",
    "EXPIRING_DAYS",
    "MAX_DELAY_S",
    "MAX_PAIRS",
    "Offer",
    "PAGE_URL",
    "ProductBrief",
    "QUERY_PATH",
    "QueryError",
    "STATUS_TEXT",
    "WarrantyError",
    "WarrantyQueryService",
    "WarrantyReport",
    "WarrantyResult",
    "WarrantyState",
    "describe_entitlement",
    "detail_rows",
    "entitlement_state",
    "parse_entitlement",
    "parse_pairs",
    "result_cells",
]

_log = logging.getLogger(__name__)

BASE_URL = "https://es.h3c.com"
QUERY_PATH = "/entitlement/query"
PAGE_URL = f"{BASE_URL}/entitlement/?locale=zh_CN"

DEFAULT_DELAY_S = 0.5
MAX_DELAY_S = 10.0
DEFAULT_TIMEOUT_S = 30.0
#: UI 输入硬上限——防止误粘贴整表打爆官方配额。
MAX_PAIRS = 200
#: 官方接口约连续 20 次查询触发滑块验证码（HAR 实测），用于 UI 提示。
CAPTCHA_HINT_QUERIES = 20
#: 临期阈值：剩余天数 ≤ 90 天视为即将过保。
EXPIRING_DAYS = 90

#: appliesTo.status → 中文说明（与官网 entitlement-cn.js transStatus 一致）。
STATUS_TEXT: dict[str, str] = {
    "A": "有效",
    "B": "未生效",
    "C": "已取消",
    "E": "永久",
    "F": "尚未生效",
    "I": "非正式",
    "X": "已失效",
    "Y": "未生效",
    "Z": "当前有效",
}

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


class WarrantyError(ValueError):
    """用户输入或参数非法。消息面向用户，可直接展示。"""


class CaptchaRequiredError(WarrantyError):
    """服务端返回 429，需要滑块验证码。不可绕过，只能在浏览器完成验证。"""


class QueryError(WarrantyError):
    """查询失败（参数错误 / 序列号不存在 / 服务端异常）。"""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"code={code} message={message}")
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Offer:
    """一条维保/合同服务项。"""

    offer_code: str
    description: str
    provider: str  # standAloneOffer，如 Factory Warranty
    parent_code: str  # pkgOffer.offerCode，可为空
    start_date: str
    end_date: str
    status: str

    @property
    def status_text(self) -> str:
        return STATUS_TEXT.get(self.status, self.status)


@dataclass(frozen=True, slots=True)
class Contract:
    """一张服务合同（含 Care Pack 金牌服务）。"""

    contract_id: str
    is_care_pack: bool
    offers: tuple[Offer, ...]


@dataclass(frozen=True, slots=True)
class ProductBrief:
    """type=1（一个 SN 对应多个产品）时的候选产品。"""

    product_number: str
    product_desc: str
    functional_location: str


@dataclass(frozen=True, slots=True)
class Entitlement:
    """单条序列号的完整维保查询结果。"""

    serial_number: str
    product_id: str
    product_desc: str
    product_line: str
    ship_to_country: str
    last_end_date: str | None
    out_of_insurance_days: str | None
    warnings: tuple[str, ...]
    warranties: tuple[Offer, ...]
    contracts: tuple[Contract, ...]
    product_candidates: tuple[ProductBrief, ...]  # 仅 type=1 时非空


#: 单条查询状态：ok 成功 / error 查询失败 / captcha 触发验证码 / skipped 未查询。
RESULT_STATUSES = ("ok", "error", "captcha", "skipped")


@dataclass(frozen=True, slots=True)
class WarrantyResult:
    """一条序列号的查询结果。"""

    index: int  # 本批次内 0 基序号
    serial_number: str
    product_number: str
    status: str  # RESULT_STATUSES 之一
    entitlement: Entitlement | None = None  # 仅 status == "ok"
    error: str = ""  # 用户可读错误
    elapsed_ms: float = 0.0
    #: UI 展示序号（1 基，跨重试稳定）；服务层按 index 推导，UI 也可自行指定。
    display_no: int = 0  # 0 表示未指定，UI 侧用 index+1 兜底

    def __post_init__(self) -> None:
        if self.display_no <= 0:
            object.__setattr__(self, "display_no", self.index + 1)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def retryable(self) -> bool:
        """captcha / error / skipped 都允许重试（e607 重试必然再失败，但无害）。"""
        return self.status in {"error", "captcha", "skipped"}

    @property
    def status_text(self) -> str:
        if self.status == "ok":
            return "查询成功"
        if self.status == "captcha":
            return "触发验证码"
        if self.status == "skipped":
            return "未查询"
        return "查询失败"


@dataclass(frozen=True, slots=True)
class WarrantyReport:
    """一轮批量查询的完整结果。"""

    results: tuple[WarrantyResult, ...]
    started_at: datetime
    finished_at: datetime
    duration_s: float
    delay_s: float
    third_party: bool
    cancelled: bool
    captcha_hit: bool

    @property
    def ok_count(self) -> int:
        return sum(1 for item in self.results if item.status == "ok")

    @property
    def error_count(self) -> int:
        return sum(1 for item in self.results if item.status == "error")

    @property
    def captcha_count(self) -> int:
        return sum(1 for item in self.results if item.status == "captcha")

    @property
    def skipped_count(self) -> int:
        return sum(1 for item in self.results if item.status == "skipped")

    @property
    def failed(self) -> tuple[WarrantyResult, ...]:
        """全部可重试条目（保持原顺序），供「重试失败项」使用。"""
        return tuple(item for item in self.results if item.retryable)

    def csv_rows(self) -> list[list[str]]:
        """表头 + 每条序列号一行，与结果表格列一致（总览段）。"""
        rows = [list(_CSV_HEADER)]
        for item in self.results:
            rows.append(result_cells(item))
        return rows

    def csv_detail_rows(self) -> list[list[str]]:
        """导出用明细段：每条维保/合同服务项一行（含表头）。"""
        rows = [list(DETAIL_HEADER)]
        for item in self.results:
            rows.extend(detail_rows(item))
        return rows

    def export_rows(self) -> list[list[str]]:
        """完整导出内容：查询条件概览 + 总览段 + 明细段。

        概览行是「标签, 值」两列结构，避免多值挤一行导致标题与内容错位。
        """
        summary = (
            f"共 {len(self.results)} 条：成功 {self.ok_count}，失败 {self.error_count}，"
            f"未查询 {self.skipped_count}"
            + ("（已停止）" if self.cancelled else "")
            + ("（触发验证码）" if self.captcha_hit else "")
        )
        rows: list[list[str]] = [
            ["H3C 维保查询导出"],
            ["查询时间", f"{self.finished_at:%Y-%m-%d %H:%M:%S}"],
            ["查询结果", summary],
            ["查询间隔", f"{self.delay_s:.1f} 秒"],
            ["第三方设备", "是" if self.third_party else "否"],
            [],
        ]
        rows.extend(self.csv_rows())
        rows.append([])
        rows.append(["── 服务明细 ──"])
        rows.extend(self.csv_detail_rows())
        return rows


# ---------------------------------------------------------------------------
# 输入解析
# ---------------------------------------------------------------------------


def parse_pairs(text: str, *, limit: int = MAX_PAIRS) -> tuple[tuple[str, str], ...]:
    """把多行输入解析成 (SN, PN) 序列。

    每行 ``SN`` 或 ``SN PN``，``#`` 开头为注释，空行跳过，全角空格容错；
    完全相同的 (SN, PN) 去重。超过 ``limit`` 条或一条都没有时抛 ``WarrantyError``。
    """
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].replace("　", " ").strip()
        if not line:
            continue
        parts = line.split()
        sn, pn = parts[0], parts[1] if len(parts) > 1 else ""
        key = (sn, pn)
        if key in seen:
            continue
        seen.add(key)
        pairs.append(key)
        if len(pairs) > limit:
            raise WarrantyError(f"序列号 {limit + 1} 条起，超过单次上限 {limit} 条，请分批查询")
    if not pairs:
        raise WarrantyError("请至少输入一个序列号，格式：每行一个 SN，也可写「SN 产品号」")
    return tuple(pairs)


# ---------------------------------------------------------------------------
# 响应解析
# ---------------------------------------------------------------------------


def _s(value: Any) -> str:
    """None → 空串，其余转 str。"""
    return "" if value is None else str(value)


def _parse_offer(offer: dict[str, Any]) -> Offer:
    applies: dict[str, Any] = offer.get("appliesTo") or {}
    pkg: dict[str, Any] = offer.get("pkgOffer") or {}
    return Offer(
        offer_code=_s(offer.get("offerCode")),
        description=_s(offer.get("offerDesc") or offer.get("offerLongDesc")),
        provider=_s(offer.get("standAloneOffer")),
        parent_code=_s(pkg.get("offerCode")),
        start_date=_s(applies.get("startDate")),
        end_date=_s(applies.get("endDate")),
        status=_s(applies.get("status")),
    )


def parse_entitlement(payload: dict[str, Any]) -> Entitlement:
    """解析 /entitlement/query 的 code=200 响应。"""
    data: dict[str, Any] = payload.get("data") or {}
    es_list: list[dict[str, Any]] = data.get("es") or []
    if not es_list:
        raise QueryError(200, "响应中无 es 数据")

    # type=1：该 SN 命中多个产品，需要提供 PN 细化查询
    if data.get("type") == 1:
        pl = (es_list[0].get("productList") or {}).get("productList") or []
        candidates = tuple(
            ProductBrief(
                product_number=_s(p.get("productNumber")),
                product_desc=_s(p.get("productDesc")),
                functional_location=_s(p.get("functionalLocation")),
            )
            for p in pl
        )
        return Entitlement(
            serial_number="",
            product_id="",
            product_desc="",
            product_line="",
            ship_to_country="",
            last_end_date=None,
            out_of_insurance_days=None,
            warnings=(),
            warranties=(),
            contracts=(),
            product_candidates=candidates,
        )

    combined: dict[str, Any] = es_list[0].get("combinedUnitEntitlement") or {}
    oos: dict[str, Any] = combined.get("oos") or {}
    product: dict[str, Any] = oos.get("product") or {}

    es_header = es_list[0].get("esHeader") or {}
    eia_list = (es_header.get("warnings") or {}).get("eiaErrorList") or []
    warnings = tuple(_s(w.get("errorText")) for w in eia_list if w.get("errorText"))

    warranties = tuple(
        _parse_offer(offer)
        for group in combined.get("warrantyList") or []
        for offer in group.get("offerList") or []
    )

    contracts: list[Contract] = []
    for item in combined.get("contractList") or []:
        care_pack = bool(item.get("hpCarePackSerialNumber"))
        contracts.append(
            Contract(
                contract_id=_s(item.get("hpCarePackSerialNumber") or item.get("svcAggrementId")),
                is_care_pack=care_pack,
                offers=tuple(_parse_offer(o) for o in item.get("offerList") or []),
            )
        )

    return Entitlement(
        serial_number=_s(oos.get("serialNumber")),
        product_id=_s(product.get("productId")),
        product_desc=_s(product.get("productDesc")),
        product_line=_s(product.get("productLineDesc")),
        ship_to_country=_s(oos.get("shipToCountry")),
        last_end_date=oos.get("lastEndDate"),
        out_of_insurance_days=oos.get("outOfInsuranceDays"),
        warnings=warnings,
        warranties=warranties,
        contracts=tuple(contracts),
        product_candidates=(),
    )


# ---------------------------------------------------------------------------
# 派生状态（表格着色与列文本的单一事实来源）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WarrantyState:
    """一条结果的展示状态：kind 决定颜色，label/days_text 决定单元格文本。"""

    kind: str  # "valid" / "expiring" / "expired" / "unknown" / "multi"
    days: int | None  # 距到期天数，已过保为负
    label: str
    days_text: str


def _parse_end_date(value: str) -> date | None:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def entitlement_state(ent: Entitlement, *, today: date | None = None) -> WarrantyState:
    """由 ``last_end_date`` 推导临期/过保状态；解析失败一律 unknown，不臆造。

    剩余天数按日历日计算；已过保显示「已过期 N 天」，有效/临期显示「N 天」。
    """
    if ent.product_candidates:
        return WarrantyState("multi", None, "多产品", "已过期")
    end_text = (ent.last_end_date or "").strip()
    if not end_text:
        return WarrantyState("unknown", None, "未知", "未知")
    end_day = _parse_end_date(end_text)
    if end_day is None:
        return WarrantyState("unknown", None, "未知", "未知")
    today = today or date.today()
    days = (end_day - today).days
    if days < 0:
        return WarrantyState("expired", days, "已过保", f"已过期 {-days} 天")
    if days <= EXPIRING_DAYS:
        return WarrantyState("expiring", days, "临期", f"{days} 天")
    return WarrantyState("valid", days, "有效", f"{days} 天")


def result_cells(result: WarrantyResult) -> list[str]:
    """结果表格一行（与 CSV 总览行共用，列与 ``_CSV_HEADER`` 一致，共 9 列）。

    列序：序列号 / 产品号 / 状态 / 产品型号 / 产品描述 / 产品线 / 维保截止 / 剩余天数 / 备注 / 错误。
    """
    ent = result.entitlement
    if result.status == "ok" and ent is not None:
        state = entitlement_state(ent)
        warning = "；".join(ent.warnings)
        return [
            ent.serial_number or result.serial_number,
            result.product_number,
            state.label,
            ent.product_id,
            ent.product_desc,
            ent.product_line,
            ent.last_end_date or "未知",
            state.days_text,
            warning
            or ("该序列号对应多个产品，请在行尾补产品号后重试" if ent.product_candidates else ""),
        ]
    return [
        result.serial_number,
        result.product_number,
        result.status_text,
        "",
        "",
        "",
        "",
        "",
        result.error,
    ]


#: 明细表 / 明细 CSV 共用的列头。
DETAIL_HEADER = [
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


def detail_rows(result: WarrantyResult) -> list[list[str]]:
    """一条结果的服务明细行（保修 / 服务合同 / 多产品候选 / 备注）。

    与 ``DETAIL_HEADER`` 列序一致；服务层与 UI 明细表、CSV 明细段共用，
    保证「界面看到什么，导出就是什么」。
    """
    ent = result.entitlement
    if result.status != "ok" or ent is None:
        return [[result.serial_number, result.status_text, "", "", "", "", "", "", result.error]]

    rows: list[list[str]] = []
    if ent.product_candidates:
        # type=1：没有维保数据，列出候选产品本身就是明细
        for item in ent.product_candidates:
            rows.append(
                [
                    result.serial_number,
                    "多产品候选",
                    "",
                    item.product_number,
                    "",
                    "",
                    "",
                    "请带 PN 重查",
                    item.product_desc,
                ]
            )
            if item.functional_location:
                rows[-1][8] = f"{rows[-1][8]}（位置 {item.functional_location}）"
        for warning in ent.warnings:
            rows.append([result.serial_number, "备注", "", "", "", "", "", "", warning])
        return rows

    for offer in ent.warranties:
        rows.append(
            [
                result.serial_number,
                "保修",
                "",
                offer.offer_code,
                offer.provider,
                offer.start_date,
                offer.end_date,
                offer.status_text,
                offer.description,
            ]
        )
    for contract in ent.contracts:
        source = "金牌服务" if contract.is_care_pack else "服务合同"
        for offer in contract.offers:
            code = (
                f"{offer.parent_code}/{offer.offer_code}" if offer.parent_code else offer.offer_code
            )
            rows.append(
                [
                    result.serial_number,
                    source,
                    contract.contract_id,
                    code,
                    offer.provider,
                    offer.start_date,
                    offer.end_date,
                    offer.status_text,
                    offer.description,
                ]
            )
    for warning in ent.warnings:
        rows.append([result.serial_number, "备注", "", "", "", "", "", "", warning])
    return rows


def describe_entitlement(ent: Entitlement) -> str:
    """明细 tooltip：保修 / 服务合同 / 备注，仿 CLI render 的紧凑版。"""
    lines: list[str] = []
    if ent.product_candidates:
        lines.append(
            f"该序列号对应 {len(ent.product_candidates)} 个产品，请在输入中带产品号精确查询："
        )
        for item in ent.product_candidates:
            lines.append(
                f"· PN={item.product_number}  {item.product_desc}  位置={item.functional_location}"
            )
        return "\n".join(lines)

    lines.append(f"销售地：{ent.ship_to_country or '未知'}")
    if ent.warranties:
        lines.append("【保修】")
        for offer in ent.warranties:
            provider = f"（{offer.provider}）" if offer.provider else ""
            lines.append(
                f"· {offer.offer_code} {offer.description}  "
                f"{offer.start_date or '?'} ~ {offer.end_date or '?'}  [{offer.status_text}]{provider}"
            )
    for contract in ent.contracts:
        kind = "金牌服务 (Care Pack)" if contract.is_care_pack else "服务合同"
        lines.append(f"【{kind}】合同号 {contract.contract_id}")
        for offer in contract.offers:
            parent = f"{offer.parent_code}/" if offer.parent_code else ""
            lines.append(
                f"· {parent}{offer.offer_code} {offer.description}  "
                f"{offer.start_date or '?'} ~ {offer.end_date or '?'}  [{offer.status_text}]"
            )
    for warning in ent.warnings:
        lines.append(f"【备注】{warning}")
    return "\n".join(lines)


def _friendly_message(exc: Exception) -> str:
    """把异常转成用户可读文案（e607 是官方「序列号不存在」的错误码）。"""
    if isinstance(exc, QueryError):
        if "e607" in exc.message:
            return "序列号格式有误或不存在（e607）"
        return exc.message or f"接口返回 code={exc.code}"
    if isinstance(exc, httpx.HTTPError):
        return f"网络错误：{exc}"
    return f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# HTTP 客户端
# ---------------------------------------------------------------------------


def build_client(
    timeout_s: float = DEFAULT_TIMEOUT_S,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """构造与官方页面 XHR 一致请求头的异步客户端。

    ``transport`` 仅供测试注入 ``httpx.MockTransport``；生产恒为 ``None``。
    """
    return httpx.AsyncClient(
        base_url=BASE_URL,
        timeout=timeout_s,
        headers={
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": BASE_URL,
            "Referer": PAGE_URL,
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"
            ),
        },
        transport=transport,
    )


async def query_entitlement(
    client: httpx.AsyncClient,
    serial_number: str,
    product_number: str = "",
    *,
    third_party: bool = False,
) -> Entitlement:
    """查询一条序列号；429 抛 ``CaptchaRequiredError``，其他非 200 抛 ``QueryError``。"""
    response = await client.post(
        QUERY_PATH,
        data={
            "serialNumber": serial_number.strip(),
            "productNumber": product_number.strip(),
            "language": "CN",
            "_d": str(int(time.time() * 1000)),
            "mvs": "Y" if third_party else "N",
        },
    )
    # HTTP 层 429 也按验证码处理（实测走 body code=429，这里防御兼容）
    if response.status_code == 429:
        raise CaptchaRequiredError("服务端要求滑块验证码（need captcha）")
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    code = payload.get("code")
    if code == 429:
        raise CaptchaRequiredError("服务端要求滑块验证码（need captcha）")
    if code != 200:
        # 400 e607 = 序列号格式/不存在；500 = 服务端异常
        raise QueryError(int(code), _s(payload.get("message")))
    return parse_entitlement(payload)


# ---------------------------------------------------------------------------
# 批量会话
# ---------------------------------------------------------------------------

ResultCallback = Callable[[WarrantyResult], None]
AsyncSleep = Callable[[float], Awaitable[None]]

#: 间隔睡眠切片：停止请求最迟约一个切片后生效。
_SLEEP_SLICE_S = 0.05


class WarrantyQueryService:
    """一轮维保批量查询：串行请求 + 固定间隔，验证码即熔断。

    ``run()`` 是阻塞调用（内部 ``asyncio.run``），交给 ``TaskRunner.submit_async``
    时直接用 ``run_async``。``on_result`` 在工作者线程被调用，UI 侧必须经
    pyqtSignal 桥接回主线程。``client_factory`` 供测试注入 MockTransport；
    AsyncClient 与事件循环绑定，必须在协程内部创建。
    """

    def __init__(
        self,
        pairs: Sequence[tuple[str, str]],
        *,
        delay_s: float = DEFAULT_DELAY_S,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        third_party: bool = False,
        max_queries: int = 0,  # 0 = 不限制
        on_result: ResultCallback | None = None,
        should_cancel: Callable[[], bool] | None = None,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        sleep: AsyncSleep | None = None,
        wall_clock: Callable[[], datetime] = datetime.now,
        display_nos: Sequence[int] | None = None,  # 与 pairs 对齐的 UI 展示序号
    ) -> None:
        cleaned = tuple(
            (sn.strip(), pn.strip()) for sn, pn in ((p[0], p[1]) for p in pairs) if sn or pn
        )
        if not cleaned:
            raise WarrantyError("请至少输入一个序列号")
        if len(cleaned) > MAX_PAIRS:
            raise WarrantyError(
                f"序列号 {len(cleaned)} 条，超过单次上限 {MAX_PAIRS} 条，请分批查询"
            )
        if not 0 <= delay_s <= MAX_DELAY_S:
            raise WarrantyError(f"查询间隔必须在 0-{MAX_DELAY_S:.0f} 秒之间")
        if not 1 <= timeout_s <= 120:
            raise WarrantyError("单次请求超时必须在 1-120 秒之间")
        if display_nos is not None and len(display_nos) != len(cleaned):
            raise WarrantyError("展示序号数量与序列号数量不一致")

        self.pairs = cleaned
        self.delay_s = delay_s
        self.timeout_s = timeout_s
        self.third_party = third_party
        self.max_queries = max(0, max_queries)
        #: 每条的 UI 展示序号；未提供时用 index+1。
        self.display_nos: tuple[int, ...] = (
            tuple(display_nos) if display_nos is not None else tuple(range(1, len(cleaned) + 1))
        )
        self._on_result = on_result
        self._should_cancel = should_cancel or (lambda: False)
        self._client_factory = client_factory
        self._sleep = sleep or asyncio.sleep
        self._wall_clock = wall_clock

    # -- 对外 ---------------------------------------------------------------

    def run(self) -> WarrantyReport:
        """阻塞执行整轮查询；``should_cancel`` 生效时返回已采集的部分。"""
        return asyncio.run(self.run_async())

    async def run_async(self) -> WarrantyReport:
        started_at = self._wall_clock()
        t0 = time.monotonic()
        cancelled = False
        captcha_hit = False
        results: list[WarrantyResult] = []

        async with self._build_client() as client:
            for index, (sn, pn) in enumerate(self.pairs):
                if index > 0:
                    await self._paced_sleep()
                if self._should_cancel():
                    results.extend(self._mark_remaining(index, len(self.pairs), "已停止，未查询"))
                    cancelled = True
                    break
                if self.max_queries and index >= self.max_queries:
                    results.extend(
                        self._mark_remaining(
                            index, len(self.pairs), f"超出单次上限 {self.max_queries} 条，未查询"
                        )
                    )
                    break

                result = await self._query(client, index, sn, pn)
                results.append(result)
                self._emit(result)
                if result.status == "captcha":
                    # 验证码即熔断：不重试、不绕过；剩余条目等浏览器验证后重试。
                    captcha_hit = True
                    results.extend(
                        self._mark_remaining(
                            index + 1,
                            len(self.pairs),
                            "触发验证码，未查询（请在浏览器验证后重试）",
                        )
                    )
                    break

        return WarrantyReport(
            results=tuple(results),
            started_at=started_at,
            finished_at=self._wall_clock(),
            duration_s=max(time.monotonic() - t0, 0.0),
            delay_s=self.delay_s,
            third_party=self.third_party,
            cancelled=cancelled,
            captcha_hit=captcha_hit,
        )

    # -- 内部 ---------------------------------------------------------------

    def _build_client(self) -> httpx.AsyncClient:
        if self._client_factory is not None:
            return self._client_factory()
        return build_client(self.timeout_s)

    async def _paced_sleep(self) -> None:
        """间隔睡眠按切片切分，停止请求能在一个切片内生效。"""
        remaining = self.delay_s
        while remaining > 0:
            if self._should_cancel():
                return
            step = min(_SLEEP_SLICE_S, remaining)
            await self._sleep(step)
            remaining -= step

    async def _query(
        self, client: httpx.AsyncClient, index: int, sn: str, pn: str
    ) -> WarrantyResult:
        start = time.monotonic()
        try:
            entitlement = await query_entitlement(client, sn, pn, third_party=self.third_party)
        except CaptchaRequiredError as exc:
            return self._result(index, sn, pn, "captcha", error=str(exc), start=start)
        except WarrantyError as exc:
            return self._result(index, sn, pn, "error", error=_friendly_message(exc), start=start)
        except Exception as exc:  # noqa: BLE001 - 单条异常不能中断整批
            _log.debug("查询 %s 异常", sn, exc_info=True)
            return self._result(index, sn, pn, "error", error=_friendly_message(exc), start=start)
        return self._result(index, sn, pn, "ok", entitlement=entitlement, start=start)

    def _result(
        self,
        index: int,
        sn: str,
        pn: str,
        status: str,
        *,
        entitlement: Entitlement | None = None,
        error: str = "",
        start: float,
    ) -> WarrantyResult:
        return WarrantyResult(
            index=index,
            serial_number=sn,
            product_number=pn,
            status=status,
            entitlement=entitlement,
            error=error,
            elapsed_ms=(time.monotonic() - start) * 1000,
            display_no=self.display_nos[index],
        )

    def _mark_remaining(self, start_index: int, total: int, message: str) -> list[WarrantyResult]:
        """把 ``[start_index, total)`` 的条目记为 skipped 并逐条回调。"""
        skipped: list[WarrantyResult] = []
        for index in range(start_index, total):
            sn, pn = self.pairs[index]
            item = WarrantyResult(
                index=index,
                serial_number=sn,
                product_number=pn,
                status="skipped",
                error=message,
                display_no=self.display_nos[index],
            )
            skipped.append(item)
            self._emit(item)
        return skipped

    def _emit(self, result: WarrantyResult) -> None:
        if self._on_result is None:
            return
        try:
            self._on_result(result)
        except Exception:  # noqa: BLE001 - 展示层回调异常不影响查询
            _log.debug("on_result 回调异常", exc_info=True)
