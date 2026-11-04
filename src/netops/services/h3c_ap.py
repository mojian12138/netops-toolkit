"""H3C AP 注册配置生成：数据模型 + 校验 + 模板渲染，零 Qt 依赖。

为什么单独一层而不是页面里直接拼字符串：

- 配置是**要粘进生产设备**的，名称 / SN / 型号错了会导致 AP 起不来，
  校验规则必须能被单测覆盖，而不是埋在 QLineEdit 的槽函数里；
- CSV 批量导入要「任一行非法则整表不生成」，这套规则和单条表单校验共用；
- ``resources/templates/h3c/ap_register.j2`` 才是唯一的输出来源，
  Python 侧只负责把用户输入变成模板变量，改格式不用动代码。

**型号与 SN 的校验取「宽进严出」**：H3C 型号族（WA / WX / WT 等）与序列号格式
在不同版本上不完全一致，这里只做明显错误的拦截（空、含空白、含非法字符），
不做型号白名单——白名单会把新型号挡在门外，比放行更糟。
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from netops.core.templates import TemplateError, render_file

__all__ = [
    "MAX_APS",
    "MAX_SERVICE_TEMPLATES",
    "TEMPLATE_NAME",
    "ApError",
    "ApSpec",
    "ApGroup",
    "ApBatch",
    "parse_service_templates",
    "parse_ap",
    "parse_ap_groups",
    "build_batch",
    "render_config",
    "parse_csv",
    "CSV_HEADER",
]

#: 单次最多生成多少台 AP 的配置——再多就不该靠粘贴了。
MAX_APS = 200
#: 每台 AP 最多绑几个服务模板（radio 数量上限，H3C AP 一般 2–3 个）。
MAX_SERVICE_TEMPLATES = 8

TEMPLATE_NAME = "h3c/ap_register.j2"

#: AP 名：字母数字开头，允许 ``- _ .``，长 1–63（Comware 名称上限）。
_AP_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
#: 型号：H3C 形如 ``WA6320`` / ``WA6320-HI`` / ``WTU430`` / ``WA2620E-AGN``。
_MODEL = re.compile(r"^[A-Za-z]{2,6}[0-9][A-Za-z0-9-]{0,30}$")
#: 序列号：厂商实际都是字母数字，短横线偶见，长度放宽到 8–40。
_SERIAL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{7,39}$")
#: 服务模板号：1–4094 的整数（Comware 的 wlan service-template 编号范围）。
_TEMPLATE_ID = re.compile(r"^\d{1,4}$")

CSV_HEADER = ["name", "serial", "model", "service_templates", "ap_group"]
#: CSV 必填列；后两列可以整列缺失（老模板只填三列也能导）。
_CSV_REQUIRED = ["name", "serial", "model"]


class ApError(ValueError):
    """AP 参数非法。消息面向用户，可直接展示；``line`` 用于 CSV 定位。"""

    def __init__(self, message: str, *, line: int | None = None) -> None:
        super().__init__(message)
        self.line = line

    def __str__(self) -> str:
        text = super().__str__()
        return f"第 {self.line} 行：{text}" if self.line is not None else text


@dataclass(frozen=True, slots=True)
class ApSpec:
    """一台 AP 的注册参数。"""

    name: str
    serial: str
    model: str
    service_templates: tuple[int, ...] = ()
    ap_group: str = ""

    @property
    def radio_count(self) -> int:
        return len(self.service_templates)


@dataclass(frozen=True, slots=True)
class ApGroup:
    """一个 AP 组及其成员（成员按加入顺序）。"""

    name: str
    ap_names: tuple[str, ...]


@dataclass(slots=True)
class ApBatch:
    """一次生成所需的全部输入。"""

    aps: tuple[ApSpec, ...] = ()
    groups: tuple[ApGroup, ...] = ()
    generated_at: datetime = field(default_factory=datetime.now)

    @property
    def ap_count(self) -> int:
        return len(self.aps)

    @property
    def group_count(self) -> int:
        return len(self.groups)


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def parse_service_templates(text: str | Sequence[int] | None) -> tuple[int, ...]:
    """把 ``"10 11"`` / ``"10,11"`` / ``[10, 11]`` 解析成模板号元组。

    去重、保序；同一台 AP 的两个 radio 绑同一个模板没有意义，但也不报错。
    """
    if text is None:
        return ()
    if isinstance(text, str):
        tokens = [token for token in re.split(r"[\s,;，；]+", text.strip()) if token]
    else:
        tokens = [str(item).strip() for item in text if str(item).strip()]
    if not tokens:
        return ()
    if len(tokens) > MAX_SERVICE_TEMPLATES:
        raise ApError(f"服务模板最多 {MAX_SERVICE_TEMPLATES} 个，当前 {len(tokens)} 个")

    result: list[int] = []
    for token in tokens:
        if not _TEMPLATE_ID.match(token):
            raise ApError(f"服务模板号必须是数字：{token}")
        value = int(token)
        if not 1 <= value <= 4094:
            raise ApError(f"服务模板号必须在 1-4094 之间：{value}")
        if value not in result:
            result.append(value)
    return tuple(result)


def _check_ap_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise ApError("AP 名称不能为空")
    if not _AP_NAME.match(name):
        raise ApError(
            f"AP 名称不合法：{name}（字母或数字开头，只能含字母、数字、点、连字符、下划线，≤63 字符）"
        )
    return name


def _check_serial(value: str) -> str:
    serial = value.strip()
    if not serial:
        raise ApError("序列号不能为空")
    if not _SERIAL.match(serial):
        raise ApError(f"序列号不合法：{serial}（8-40 位字母、数字或连字符）")
    return serial


def _check_model(value: str) -> str:
    model = value.strip()
    if not model:
        raise ApError("AP 型号不能为空")
    if not _MODEL.match(model):
        raise ApError(f"AP 型号不合法：{model}（例如 WA6320、WA6320-HI、WTU430）")
    return model


def parse_ap(
    *,
    name: str,
    serial: str,
    model: str,
    service_templates: str | Sequence[int] | None = None,
    ap_group: str = "",
) -> ApSpec:
    """校验并构造一台 AP。"""
    group = ap_group.strip()
    if group:
        # AP 组名和 AP 名共用一套命名规则，Comware 侧限制一致。
        try:
            group = _check_ap_name(group)
        except ApError as exc:
            raise ApError(str(exc).replace("AP 名称", "AP 组名")) from None
    return ApSpec(
        name=_check_ap_name(name),
        serial=_check_serial(serial),
        model=_check_model(model),
        service_templates=parse_service_templates(service_templates),
        ap_group=group,
    )


def parse_ap_groups(text: str) -> tuple[ApGroup, ...]:
    """解析「AP 组 → 成员」映射。

    ``text`` 每行一个组：``组名: ap1, ap2``（冒号、全角冒号、空格都行）。
    这里只做语法拆分，成员是否真实存在由 ``build_batch`` 统一校验。
    """
    groups: list[ApGroup] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        # 冒号（半角/全角）分隔组名与成员；没有冒号就是「只声明组名」
        parts = re.split(r"[：:]", line, maxsplit=1)
        group_name = parts[0].strip()
        members = parts[1] if len(parts) > 1 else ""
        if not group_name:
            raise ApError(f"AP 组名为空：{line}")
        if not _AP_NAME.match(group_name):
            raise ApError(f"AP 组名不合法：{group_name}")
        if group_name in seen:
            raise ApError(f"AP 组重复定义：{group_name}")
        seen.add(group_name)
        ap_names = tuple(token for token in re.split(r"[\s,;，；]+", members.strip()) if token)
        groups.append(ApGroup(name=group_name, ap_names=ap_names))
    return tuple(groups)


def build_batch(
    aps: Iterable[ApSpec],
    *,
    groups: Iterable[ApGroup] | None = None,
    generated_at: datetime | None = None,
) -> ApBatch:
    """把 AP 列表和组映射合成一个完整批次，并做交叉校验。

    - AP 数量、名称、序列号都不能重复（序列号重复 = 同一台设备注册两次，AC 会报冲突）；
    - AP 组里的成员必须在本批 AP 中出现；
    - 同一台 AP 被写进多个组直接报错，而不是让用户猜最终生效的是哪个。
    """
    specs = tuple(aps)
    if not specs:
        raise ApError("至少需要一台 AP")
    if len(specs) > MAX_APS:
        raise ApError(f"单次最多 {MAX_APS} 台 AP，当前 {len(specs)} 台")

    names: set[str] = set()
    serials: set[str] = set()
    for spec in specs:
        if spec.name in names:
            raise ApError(f"AP 名称重复：{spec.name}")
        names.add(spec.name)
        if spec.serial in serials:
            raise ApError(f"序列号重复：{spec.serial}")
        serials.add(spec.serial)

    group_list = tuple(groups or ())
    assigned: dict[str, str] = {}
    for group in group_list:
        for ap_name in group.ap_names:
            if ap_name not in names:
                raise ApError(f"AP 组 {group.name} 引用了不存在的 AP：{ap_name}")
            previous = assigned.get(ap_name)
            if previous is not None and previous != group.name:
                raise ApError(f"AP {ap_name} 同时出现在组 {previous} 和 {group.name} 中")
            assigned[ap_name] = group.name

    # AP 自己写的 ap_group 与组映射冲突时也要报错
    for spec in specs:
        if not spec.ap_group:
            continue
        mapped = assigned.get(spec.name)
        if mapped is not None and mapped != spec.ap_group:
            raise ApError(f"AP {spec.name} 标注的组 {spec.ap_group} 与组映射中的 {mapped} 冲突")
        assigned.setdefault(spec.name, spec.ap_group)

    # 把 AP 自己的 ap_group 补成组映射，保证模板只处理 groups 一处
    merged: dict[str, list[str]] = {}
    for group in group_list:
        merged.setdefault(group.name, [])
        merged[group.name].extend(group.ap_names)
    for spec in specs:
        if spec.ap_group:
            merged.setdefault(spec.ap_group, [])
    for group_name, members in merged.items():
        for spec in specs:
            if spec.ap_group == group_name and spec.name not in members:
                members.append(spec.name)

    final_groups = tuple(
        ApGroup(name=name, ap_names=tuple(dict.fromkeys(members)))
        for name, members in merged.items()
        if members
    )
    return ApBatch(
        aps=specs,
        groups=final_groups,
        generated_at=generated_at or datetime.now(),
    )


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------


def render_config(batch: ApBatch, *, template: str = TEMPLATE_NAME) -> str:
    """渲染成可直接粘贴到 AC 的配置文本。"""
    if not batch.aps:
        raise ApError("没有可生成的 AP")
    try:
        return render_file(
            template,
            generated_at=batch.generated_at.strftime("%Y-%m-%d %H:%M:%S"),
            aps=batch.aps,
            groups=batch.groups,
        )
    except TemplateError as exc:
        raise ApError(f"配置生成失败：{exc}") from exc


# ---------------------------------------------------------------------------
# CSV 批量导入
# ---------------------------------------------------------------------------


def parse_csv(text: str, *, limit: int = MAX_APS) -> tuple[ApSpec, ...]:
    """解析 CSV 批量导入，任一行非法立刻抛 ``ApError`` 并带**原始行号**。

    表头必须含 ``name,serial,model``；``service_templates`` 与 ``ap_group``
    可选（列在就有用，不在就当空）。允许 ``#`` 注释行与空行。
    整表不合法就不返回任何结果，页面据此拒绝生成——避免「一半 AP 生效一半没生效」
    这种最难查的状态。

    行号按用户粘贴的原文计算：注释行和空行会被跳过，但仍占行号，
    所以报错能直接定位到原文第几行。
    """
    #: 保留 (原文行号, 内容)，供 csv.reader 逐行解析后映射回原文行号。
    kept: list[tuple[int, str]] = [
        (number, line)
        for number, line in enumerate(text.splitlines(), start=1)
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not kept:
        raise ApError("CSV 内容为空")

    # csv.reader 的下标是「过滤后的第 N 行」，这里映射回原文行号。
    line_numbers = [number for number, _ in kept]
    reader = csv.reader(io.StringIO("\n".join(line for _, line in kept)))
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        raise ApError("CSV 内容为空")

    def source_line(position: int) -> int:
        """过滤后第 ``position`` 行（0 基）对应的原文行号。"""
        if not line_numbers:
            return position + 1
        if position < len(line_numbers):
            return line_numbers[position]
        return line_numbers[-1]

    header = [cell.strip().lower() for cell in rows[0]]
    missing = [name for name in _CSV_REQUIRED if name not in header]
    if missing:
        raise ApError(
            f"CSV 表头缺少列：{'、'.join(missing)}"
            f"（至少需要 {','.join(_CSV_REQUIRED)}，可选 {','.join(CSV_HEADER[3:])}）"
        )
    index = {name: header.index(name) for name in CSV_HEADER if name in header}

    specs: list[ApSpec] = []
    for offset, row in enumerate(rows[1:], start=1):
        line_no = source_line(offset)

        # 默认参数绑定当前行：直接在闭包里引用 row 会踩 B023（延迟绑定）
        def cell(column: str, _row: list[str] = row) -> str:
            position = index.get(column)
            if position is None or position >= len(_row):
                return ""
            return _row[position].strip()

        try:
            specs.append(
                parse_ap(
                    name=cell("name"),
                    serial=cell("serial"),
                    model=cell("model"),
                    service_templates=cell("service_templates"),
                    ap_group=cell("ap_group"),
                )
            )
        except ApError as exc:
            raise ApError(str(exc), line=line_no) from None
        if len(specs) > limit:
            raise ApError(f"单次最多 {limit} 台 AP，请拆分批次", line=line_no)
    return tuple(specs)
