"""IPv4 计算内核：纯函数、零 Qt 依赖，供六个计算器页面共用。

所有算法以 32 位无符号整数为中间表示，避免字符串比较陷阱；
输入解析对空格、大小写、前导零保持宽容，但对非法掩码严格拒绝。
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

__all__ = [
    "CalcError",
    "NetworkInfo",
    "AggregateBlock",
    "AggregateResult",
    "parse_mask",
    "prefix_to_mask",
    "wildcard_mask",
    "mask_to_binary",
    "mask_to_hex",
    "prefix_for_hosts",
    "hosts_for_prefix",
    "prefix_for_subnets",
    "subnet_count",
    "AddressForms",
    "parse_ipv4",
    "address_forms",
    "network_info",
    "aggregate",
    "optimize",
    "split_by_count",
    "split_by_hosts",
    "relation",
    "RELATION_KINDS",
    "relation_label",
    "AddressSubject",
    "parse_subject",
    "parse_subjects",
    "compare",
    "is_inside",
    "uncovered",
    "contains_matrix",
]

_MAX_PREFIX = 32


class CalcError(ValueError):
    """计算器输入非法。消息面向用户，可直接展示。"""


def _int_to_ip(value: int) -> str:
    if not 0 <= value <= 0xFFFFFFFF:
        raise CalcError(f"地址超出 32 位范围：{value}")
    return str(ipaddress.IPv4Address(value))


def parse_mask(mask: str | int) -> int:
    """把掩码解析为前缀长度。

    接受 ``24`` / ``/24`` / ``255.255.255.0`` 三种写法；
    掩码位必须连续，否则视为非法。
    """
    if isinstance(mask, int):
        prefix = mask
    else:
        text = mask.strip()
        if not text:
            raise CalcError("掩码不能为空")
        if text.startswith("/"):
            text = text[1:].strip()
        if not text:
            raise CalcError("掩码不能为空")
        try:
            prefix = (
                int(text)
                if text.isdigit()
                else int(ipaddress.IPv4Network(f"0.0.0.0/{text}").prefixlen)
            )
        except (ValueError, ipaddress.NetmaskValueError) as exc:
            raise CalcError(f"非法掩码（网络位必须连续）：{mask!r}") from exc
    if not 0 <= prefix <= _MAX_PREFIX:
        raise CalcError(f"前缀长度必须在 0-32 之间：{prefix}")
    return prefix


def prefix_to_mask(prefix: int) -> str:
    if not 0 <= prefix <= _MAX_PREFIX:
        raise CalcError(f"前缀长度必须在 0-32 之间：{prefix}")
    return _int_to_ip(((1 << prefix) - 1) << (32 - prefix))


def wildcard_mask(prefix: int) -> str:
    """ACL 使用的反掩码。"""
    mask = int(ipaddress.IPv4Address(prefix_to_mask(prefix)))
    return _int_to_ip(mask ^ 0xFFFFFFFF)


def mask_to_binary(mask: str | int) -> str:
    prefix = parse_mask(mask)
    value = int(ipaddress.IPv4Address(prefix_to_mask(prefix)))
    return ".".join(f"{(value >> shift) & 0xFF:08b}" for shift in (24, 16, 8, 0))


def mask_to_hex(mask: str | int) -> str:
    """掩码的十六进制形式，如 ``0xFFFFFF00``。"""
    prefix = parse_mask(mask)
    value = int(ipaddress.IPv4Address(prefix_to_mask(prefix)))
    return f"0x{value:08X}"


def prefix_for_hosts(hosts: int) -> int:
    """按每个子网需要的可用主机数，反推最合适的前缀长度。

    /31、/32 按 RFC 3021 处理（无网络/广播地址），因此 1、2 台主机分别对应 /32、/31。
    """
    if hosts < 1:
        raise CalcError("主机数必须 ≥ 1")
    if hosts == 1:
        return 32
    if hosts == 2:
        return 31
    needed = hosts + 2  # 网络地址 + 广播地址
    host_bits = (needed - 1).bit_length()
    if host_bits > 32:
        raise CalcError("主机数超出 IPv4 可容纳范围")
    return 32 - host_bits


def hosts_for_prefix(prefix: int) -> int:
    """给定前缀长度，返回每个子网的可用主机数（与 ``network_info`` 口径一致）。"""
    if not 0 <= prefix <= _MAX_PREFIX:
        raise CalcError(f"前缀长度必须在 0-32 之间：{prefix}")
    if prefix == 32:
        return 1
    if prefix == 31:
        return 2
    return (1 << (32 - prefix)) - 2


def prefix_for_subnets(base_prefix: int, count: int) -> int:
    """在 ``base_prefix`` 网段内划出至少 ``count`` 个子网所需的前缀长度。

    数量不是 2 的幂时向上取整；超出原网段容量时报错。
    """
    if not 0 <= base_prefix <= _MAX_PREFIX:
        raise CalcError(f"前缀长度必须在 0-32 之间：{base_prefix}")
    if count < 1:
        raise CalcError("子网数量必须 ≥ 1")
    extra = (count - 1).bit_length()
    if base_prefix + extra > _MAX_PREFIX:
        raise CalcError(f"/{base_prefix} 最多只能拆出 {1 << (32 - base_prefix)} 个子网")
    return base_prefix + extra


def subnet_count(prefix: int) -> int:
    """给定前缀在 /0 下能划出的子网数量。"""
    if not 0 <= prefix <= _MAX_PREFIX:
        raise CalcError(f"前缀长度必须在 0-32 之间：{prefix}")
    return 1 << prefix


@dataclass(frozen=True, slots=True)
class AddressForms:
    """同一个 32 位地址的各进制形态。"""

    value: int
    dotted: str
    binary: str
    binary_compact: str
    hex: str
    hex_bytes: str
    integer: str
    endian_swap: str
    kind: str


def parse_ipv4(text: str) -> int:
    """宽容解析 IPv4 地址，支持四种写法：

    - 点分十进制：``192.168.1.1``（每段允许前导零，如 ``192.168.001.001``）
    - 二进制：``11000000.10101000.00000001.00000001``（4 段×8 位，可带 ``0b`` 前缀）
    - 十六进制：``0xC0A80101`` / ``0xC0A8.0101`` / ``c0a80101``
    - 十进制整数：``3232235777``

    冒号或连字符写法一律视为 IPv6 / 区间，直接拒绝；其余写法忽略空白。
    """
    raw = "".join(text.lower().split())
    if not raw:
        raise CalcError("IP 地址不能为空")
    if ":" in raw or "-" in raw:
        raise CalcError(f"非法地址：{text!r}（IPv6 / 地址区间暂不支持）")

    if raw.startswith("0b"):
        return _parse_binary(raw[2:], text)
    if raw.startswith("0x") or any(ch in "abcdef" for ch in raw):
        digits = raw[2:] if raw.startswith("0x") else raw
        joined = digits.replace(".", "")
        if not joined or any(ch not in "0123456789abcdef" for ch in joined):
            raise CalcError(f"非法十六进制地址：{text!r}")
        if len(joined) > 8:
            raise CalcError(f"十六进制地址超出 32 位：{text!r}")
        return int(joined, 16)

    if raw.isdigit():
        value = int(raw)
        if value > 0xFFFFFFFF:
            raise CalcError(f"整数超出 32 位范围：{text!r}")
        return value

    parts = raw.split(".")
    if len(parts) == 4 and any(len(part) == 8 for part in parts):
        return _parse_binary(raw, text)
    if len(parts) != 4:
        raise CalcError(f"点分地址必须包含 4 段：{text!r}")

    octets: list[int] = []
    for part in parts:
        if not part.isdigit() or int(part) > 255:
            raise CalcError(f"非法点分地址：{text!r}（每段必须在 0-255 之间）")
        octets.append(int(part))
    return (octets[0] << 24) | (octets[1] << 16) | (octets[2] << 8) | octets[3]


def _parse_binary(digits: str, original: str) -> int:
    """解析二进制写法：4 段×8 位（不足补零），或省略分段的 32 位串。"""
    parts = digits.split(".")
    if len(parts) not in (1, 4) or any(not part for part in parts):
        raise CalcError(f"非法二进制地址：{original!r}（应为 4 段，每段 8 位）")
    if len(parts) == 4:
        if any(len(part) > 8 for part in parts):
            raise CalcError(f"二进制地址每段不得超过 8 位：{original!r}")
        digits = "".join(part.zfill(8) for part in parts)
    if any(ch not in "01" for ch in digits):
        raise CalcError(f"非法二进制地址：{original!r}")
    if len(digits) > 32:
        raise CalcError(f"二进制地址超出 32 位：{original!r}")
    return int(digits, 2)


def address_forms(value: int | str) -> AddressForms:
    """把地址（整数或任意支持的写法）展开为各进制形态。"""
    number = parse_ipv4(value) if isinstance(value, str) else value
    if not 0 <= number <= 0xFFFFFFFF:
        raise CalcError(f"地址超出 32 位范围：{value}")
    swapped = int.from_bytes(number.to_bytes(4, "big"), "little")
    dotted = _int_to_ip(number)
    binary = ".".join(f"{(number >> shift) & 0xFF:08b}" for shift in (24, 16, 8, 0))
    return AddressForms(
        value=number,
        dotted=dotted,
        binary=binary,
        binary_compact=binary.replace(".", ""),
        hex=f"0x{number:08X}",
        hex_bytes=f"{number:08X}",
        integer=str(number),
        endian_swap=_int_to_ip(swapped),
        kind=_network_kind(dotted),
    )


def _split_target(text: str, mask: str | int | None) -> tuple[str, int]:
    """把 ``192.168.1.1/24``、``192.168.1.1 255.255.255.0``、``192.168.10.0 24`` 拆成地址与前缀。"""
    value = text.strip()
    if not value:
        raise CalcError("IP 地址不能为空")
    if "/" in value:
        address, _, inline_mask = value.partition("/")
        if mask is not None:
            raise CalcError("已使用 CIDR 写法时不要再单独填写掩码")
        return address.strip(), parse_mask(inline_mask)
    parts = value.split()
    if len(parts) == 2 and mask is None:
        return parts[0], parse_mask(parts[1])
    if len(parts) > 1:
        raise CalcError(f"无法解析的地址：{value!r}")
    if mask is None:
        raise CalcError("请提供掩码，例如 192.168.1.1/24 或 192.168.1.1 255.255.255.0")
    return value, parse_mask(mask)


def _network_kind(address: str) -> str:
    ip = ipaddress.IPv4Address(address)
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link-local"
    if ip.is_private:
        return "private"
    if ip.is_multicast:
        return "multicast"
    if ip.is_reserved:
        return "reserved"
    return "public"


@dataclass(frozen=True, slots=True)
class NetworkInfo:
    address: str
    prefix: int
    mask: str
    wildcard: str
    network: str
    broadcast: str
    first_host: str | None
    last_host: str | None
    total_addresses: int
    usable_hosts: int
    kind: str

    @property
    def cidr(self) -> str:
        return f"{self.network}/{self.prefix}"

    @property
    def host_range(self) -> str:
        if self.first_host is None or self.last_host is None:
            return "—"
        return f"{self.first_host} - {self.last_host}"


def network_info(text: str, mask: str | int | None = None) -> NetworkInfo:
    address_text, prefix = _split_target(text, mask)
    address = parse_ipv4(address_text)
    host_bits = 32 - prefix
    block_size = 1 << host_bits
    network_int = address & ~(block_size - 1)
    broadcast_int = network_int | (block_size - 1)
    network = _int_to_ip(network_int)
    broadcast = _int_to_ip(broadcast_int)

    if prefix >= 31:
        # RFC 3021：/31 两端都可用，/32 只有一个地址。
        first_host = network
        last_host = broadcast
    else:
        first_host = _int_to_ip(network_int + 1)
        last_host = _int_to_ip(broadcast_int - 1)
    usable = hosts_for_prefix(prefix)

    return NetworkInfo(
        address=_int_to_ip(address),
        prefix=prefix,
        mask=prefix_to_mask(prefix),
        wildcard=wildcard_mask(prefix),
        network=network,
        broadcast=broadcast,
        first_host=first_host,
        last_host=last_host,
        total_addresses=block_size,
        usable_hosts=usable,
        kind=_network_kind(network),
    )


@dataclass(frozen=True, slots=True)
class AggregateBlock:
    """一条聚合结果，及其覆盖的原输入网段。"""

    network: ipaddress.IPv4Network
    sources: tuple[ipaddress.IPv4Network, ...]

    @property
    def cidr(self) -> str:
        return f"{self.network.network_address}/{self.network.prefixlen}"

    @property
    def covered(self) -> int:
        """被原网段真正覆盖的地址数（不含冗余）。"""
        return sum(net.num_addresses for net in self.sources)

    @property
    def wasted(self) -> int:
        return self.network.num_addresses - self.covered


@dataclass(frozen=True, slots=True)
class AggregateResult:
    """聚合结果：``blocks`` 是给配置用的 CIDR 字符串，``details`` 保留溯源信息。"""

    blocks: tuple[str, ...]
    sources: tuple[str, ...]
    duplicates: int = 0
    contained: tuple[str, ...] = ()
    details: tuple[AggregateBlock, ...] = ()

    @property
    def cidr_list(self) -> list[str]:
        return list(self.blocks)

    @property
    def covered_addresses(self) -> int:
        """原输入实际覆盖的地址数（冗余部分不计）。"""
        return sum(block.covered for block in self.details)

    @property
    def wasted_addresses(self) -> int:
        """聚合后被顺带覆盖、但原本不在输入里的地址数。"""
        return sum(block.wasted for block in self.details)

    def block_for(self, cidr: str) -> AggregateBlock | None:
        for block in self.details:
            if block.cidr == cidr:
                return block
        return None

    def render_config(self, hop: str, style: str = "h3c") -> str:
        """按设备风格生成汇总路由配置。"""
        if not hop.strip():
            raise CalcError("下一跳不能为空")
        try:
            hop_ip = str(ipaddress.IPv4Address(hop.strip()))
        except ValueError as exc:
            raise CalcError(f"非法下一跳地址：{hop!r}") from exc
        try:
            command = _ROUTE_TEMPLATES[style]
        except KeyError as exc:
            raise CalcError(f"不支持的设备风格：{style}") from exc
        return "\n".join(
            command.format(
                network=block.network.network_address,
                prefix=block.network.prefixlen,
                hop=hop_ip,
            )
            for block in self.details
        )


_ROUTE_TEMPLATES = {
    "h3c": "ip route-static {network} {prefix} {hop}",
    "huawei": "ip route-static {network} {prefix} {hop}",
    "cisco": "ip route {network} {prefix} {hop}",
}


def _input_tokens(lines: list[str] | str) -> list[str]:
    """把多行/逗号/空格分隔的输入摊平成网段列表，忽略空行与 ``#`` 注释。"""
    source = lines if isinstance(lines, str) else "\n".join(lines)
    tokens: list[str] = []
    for line in source.splitlines():
        line = line.split("#", 1)[0]
        tokens.extend(part for part in line.replace(",", " ").split() if part)
    return tokens


def _parse_networks(lines: list[str] | str) -> list[ipaddress.IPv4Network]:
    """解析网段列表，兼容 ``10.0.0.0/24``、``10.0.0.0 255.255.255.0``、``10.0.0.0 24``。"""
    tokens = _input_tokens(lines)
    networks: list[ipaddress.IPv4Network] = []
    index = 0
    while index < len(tokens):
        text = tokens[index]
        index += 1
        if index < len(tokens) and _looks_like_mask(tokens[index]):
            text = f"{text}/{tokens[index]}"
            index += 1
        if _is_address_range(text):
            raise CalcError(f"地址区间暂不支持：{text!r}（请改用 CIDR，如 10.0.0.0/24）")
        try:
            networks.append(ipaddress.IPv4Network(text, strict=False))
        except ValueError as exc:
            raise CalcError(f"非法网段：{text!r}（请使用 10.0.0.0/24 形式）") from exc
    if not networks:
        raise CalcError("请至少输入一个网段")
    return networks


def _is_address_range(text: str) -> bool:
    """``10.0.0.1-10.0.0.9`` 这类区间写法：两端都是合法 IPv4 才算。"""
    if text.count("-") != 1:
        return False
    start, _, end = text.partition("-")
    try:
        ipaddress.IPv4Address(start.strip())
        ipaddress.IPv4Address(end.strip())
    except ValueError:
        return False
    return True


def _looks_like_mask(token: str) -> bool:
    """判断独立 token 是否是需要与前一个地址拼起来的掩码。"""
    if token.isdigit():
        return True
    if token.count(".") != 3:
        return False
    try:
        ipaddress.IPv4Network(f"0.0.0.0/{token}")
    except ValueError:
        return False
    return True


def _normalize(lines: list[str] | str) -> tuple[list[ipaddress.IPv4Network], int, tuple[str, ...]]:
    """去重并按前缀从长到短排序，剔除被其它网段完全包含的项。

    返回 (保留的网段, 重复条数, 被剔除的网段)。
    """
    networks = _parse_networks(lines)
    unique = sorted(set(networks), key=lambda net: (int(net.network_address), net.prefixlen))
    duplicates = len(networks) - len(unique)
    kept: list[ipaddress.IPv4Network] = []
    for net in unique:
        if any(net.subnet_of(other) and net != other for other in kept):
            continue
        kept.append(net)
    removed = tuple(str(net) for net in unique if net not in kept)
    return kept, duplicates, removed


def _blocks(
    merged: list[ipaddress.IPv4Network], kept: list[ipaddress.IPv4Network]
) -> tuple[AggregateBlock, ...]:
    """为每条聚合块记录它覆盖了哪些原输入网段。"""
    return tuple(
        AggregateBlock(
            network=net,
            sources=tuple(src for src in kept if src.subnet_of(net)),
        )
        for net in merged
    )


def _result(
    kept: list[ipaddress.IPv4Network],
    merged: list[ipaddress.IPv4Network],
    *,
    duplicates: int,
    contained: tuple[str, ...],
) -> AggregateResult:
    merged = sorted(merged, key=lambda net: int(net.network_address))
    details = _blocks(merged, kept)
    return AggregateResult(
        blocks=tuple(block.cidr for block in details),
        sources=tuple(str(net) for net in kept),
        duplicates=duplicates,
        contained=contained,
        details=details,
    )


def aggregate(lines: list[str] | str) -> AggregateResult:
    """精准聚合：只合并能完全覆盖的相邻块，不产生多余地址。"""
    kept, duplicates, contained = _normalize(lines)
    collapsed = list(ipaddress.collapse_addresses(kept))
    return _result(kept, collapsed, duplicates=duplicates, contained=contained)


def _common_supernet(a: ipaddress.IPv4Network, b: ipaddress.IPv4Network) -> ipaddress.IPv4Network:
    prefix = min(a.prefixlen, b.prefixlen)
    block = (int(a.network_address), prefix)
    candidate = ipaddress.IPv4Network(block, strict=False)
    while not (b.network_address in candidate and b.broadcast_address in candidate):
        prefix -= 1
        if prefix < 0:
            return ipaddress.IPv4Network("0.0.0.0/0")
        candidate = ipaddress.IPv4Network((int(a.network_address), prefix), strict=False)
    return candidate


def optimize(lines: list[str] | str, max_waste_ratio: float = 0.25) -> AggregateResult:
    """优化聚合：允许包含少量多余地址，换取更少的路由条目。

    ``max_waste_ratio`` 是合并后整块允许的冗余比例
    （冗余 = 整块地址数 - 原网段已覆盖地址数），0 即退化为精准聚合。
    """
    if not 0 <= max_waste_ratio < 1:
        raise CalcError("冗余比例必须在 [0, 1) 之间")
    kept, duplicates, contained = _normalize(lines)
    blocks = list(ipaddress.collapse_addresses(kept))

    def waste_of(merged: ipaddress.IPv4Network) -> int:
        covered = sum(src.num_addresses for src in kept if src.subnet_of(merged))
        return merged.num_addresses - covered

    while True:
        best: tuple[int, int, int] | None = None  # (waste, i, j)
        for i, a in enumerate(blocks):
            for j in range(i + 1, len(blocks)):
                b = blocks[j]
                if a.overlaps(b):
                    continue
                merged = _common_supernet(a, b)
                if merged in (a, b):
                    continue
                waste = waste_of(merged)
                if waste <= max_waste_ratio * merged.num_addresses and (
                    best is None or waste < best[0]
                ):
                    best = (waste, i, j)
        if best is None:
            break
        _, i, j = best
        merged = _common_supernet(blocks[i], blocks[j])
        blocks = [blk for k, blk in enumerate(blocks) if k not in (i, j)]
        blocks = list(ipaddress.collapse_addresses([*blocks, merged]))

    return _result(kept, blocks, duplicates=duplicates, contained=contained)


def split_by_count(text: str, count: int) -> list[NetworkInfo]:
    """按目标子网数量拆分；数量不是 2 的幂时向上取整到 2 的幂。"""
    if count < 1:
        raise CalcError("子网数量必须 ≥ 1")
    info = network_info(text)
    extra = 0
    while (1 << extra) < count:
        extra += 1
    return _split_extra(info, extra)


def split_by_hosts(text: str, hosts: int) -> list[NetworkInfo]:
    """按每段可用主机数拆分，向上取整到 2 的幂（预留网络/广播地址）。"""
    if hosts < 1:
        raise CalcError("每段主机数必须 ≥ 1")
    info = network_info(text)
    needed = hosts + 2
    host_bits = 1
    while (1 << host_bits) < needed:
        host_bits += 1
    extra = (32 - info.prefix) - host_bits
    if extra < 0:
        raise CalcError("每段主机数超过该网段容量")
    return _split_extra(info, extra)


def _split_extra(info: NetworkInfo, extra_bits: int) -> list[NetworkInfo]:
    if extra_bits == 0:
        return [info]
    new_prefix = info.prefix + extra_bits
    base = int(ipaddress.IPv4Address(info.network))
    step = 1 << (32 - new_prefix)
    return [
        network_info(f"{_int_to_ip(base + index * step)}/{new_prefix}")
        for index in range(1 << extra_bits)
    ]


def relation(target: str, candidate: str) -> str:
    """判断 candidate 与 target 的关系。

    返回 ``contained``（candidate 在 target 内）、``contains``（candidate 包含 target）、
    ``overlap``（部分重叠）、``disjoint``（不相交）。
    """
    target_net = ipaddress.IPv4Network(target.strip(), strict=False)
    candidate_net = ipaddress.IPv4Network(candidate.strip(), strict=False)
    if candidate_net.subnet_of(target_net):
        return "contained"
    if target_net.subnet_of(candidate_net):
        return "contains"
    if target_net.overlaps(candidate_net):
        return "overlap"
    return "disjoint"


#: ``relation`` / ``compare`` 的四种结论，按「越像包含关系越靠前」排列。
RELATION_KINDS = ("contained", "contains", "overlap", "disjoint")

_RELATION_LABELS = {
    "contained": "完全包含",
    "contains": "包含目标",
    "overlap": "部分重叠",
    "disjoint": "互不相交",
}


def relation_label(kind: str) -> str:
    return _RELATION_LABELS.get(kind, kind)


@dataclass(frozen=True, slots=True)
class AddressSubject:
    """``compare`` 与包含矩阵使用的最小地址单元：一个地址或一个网段。"""

    text: str  #: 原文（地址写法保留原样）
    network: ipaddress.IPv4Network  #: 规范化后的网段，裸地址按 /32 处理
    is_host: bool  #: 输入是单个地址（而非网段）

    @property
    def cidr(self) -> str:
        return str(self.network)

    @property
    def start(self) -> int:
        return int(self.network.network_address)


def parse_subject(text: str) -> AddressSubject:
    """解析单个 IP 或网段。

    裸地址支持 ``parse_ipv4`` 的全部写法（点分 / 二进制 / 十六进制 / 整数），
    统一按 /32 单主机处理——不做 A/B/C 类推断，避免「10.0.0.1 落在 10.0.0.0/16 内」
    这类问题被默认掩码带偏。
    """
    value = text.strip()
    if not value:
        raise CalcError("地址不能为空")
    if "/" in value:
        try:
            net = ipaddress.IPv4Network(value, strict=False)
        except ValueError as exc:
            raise CalcError(f"非法网段：{value!r}（请使用 10.0.0.0/24 形式）") from exc
        return AddressSubject(text=value, network=net, is_host=False)
    address = ipaddress.IPv4Address(parse_ipv4(value))
    net = ipaddress.IPv4Network(f"{address}/32", strict=False)
    return AddressSubject(text=value, network=net, is_host=True)


def parse_subjects(lines: list[str] | str) -> list[AddressSubject]:
    """逐行解析待测列表，保留原始顺序；空行与 ``#`` 注释跳过。"""
    source = lines if isinstance(lines, str) else "\n".join(lines)
    subjects: list[AddressSubject] = []
    for line in source.splitlines():
        value = line.split("#", 1)[0].strip()
        if value:
            subjects.append(parse_subject(value))
    if not subjects:
        raise CalcError("请至少输入一个 IP 或网段")
    return subjects


def compare(targets: list[AddressSubject], subject: AddressSubject) -> tuple[str, ...]:
    """待测对象与每个目标网段的关系，每个元素都是 ``RELATION_KINDS`` 之一。"""
    return tuple(relation(str(target.network), str(subject.network)) for target in targets)


def is_inside(targets: list[AddressSubject], subject: AddressSubject) -> bool:
    """待测对象是否完整落在**任意一个**目标网段内。"""
    return any(subject.network.subnet_of(t.network) for t in targets)


def uncovered(
    targets: list[AddressSubject], subject: AddressSubject
) -> list[ipaddress.IPv4Network]:
    """待测网段中**不在**任何目标网段里的部分；整个网段都被覆盖时返回空列表。

    覆盖计算只看目标网段的并集，与 ``is_inside`` 的「单个网段包含」语义不同：
    ``10.0.0.0/24`` 虽然不被 ``10.0.0.0/25`` 单独包含，但其中没有地址漏出目标。
    """
    remaining = [subject.network]
    for target in targets:
        net = target.network
        next_remaining: list[ipaddress.IPv4Network] = []
        for piece in remaining:
            if not piece.overlaps(net):
                next_remaining.append(piece)
            elif not piece.subnet_of(net):
                next_remaining.extend(piece.address_exclude(net))
        remaining = next_remaining
        if not remaining:
            break
    return sorted(remaining, key=lambda net: int(net.network_address))


def contains_matrix(
    targets: list[AddressSubject], subjects: list[AddressSubject]
) -> list[tuple[str, ...]]:
    """包含矩阵：第 ``i`` 行对应 ``subjects[i]``，每格放该对象与 ``targets[j]`` 的关系。

    单元格：``✓`` 待测对象在该目标内；``✗+`` 待测对象包含目标；``✗-`` 部分重叠；
    ``·`` 不相交。用简短符号是为了多列目标时表格能一屏放下。
    """
    marks = {"contained": "✓", "contains": "✗+", "overlap": "✗-", "disjoint": "·"}
    return [tuple(marks[kind] for kind in compare(targets, subject)) for subject in subjects]
