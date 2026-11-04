"""MAC 地址内核：纯函数、零 Qt 依赖，供 MAC 格式转换页与后续设备备注共用。

解析对分隔符、大小写、``0x`` 前缀保持宽容，但严格要求恰好 6 个字节；
格式化输出十种常见写法，覆盖思科 / H3C 接口、Windows、Linux、Wireshark、
DHCP 绑定表等场景。OUI 只识别极少数知名厂商，其余标为「本地库未收录」——
宁可查不到，也不能张冠李戴。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "MacError",
    "MacForms",
    "parse_mac",
    "format_mac",
    "mac_forms",
    "describe_mac",
    "lookup_oui",
    "OUI_TABLE",
]

#: 内置 OUI 表（前 3 字节 → 厂商）。只收录网络设备与主流网卡厂商，
#: 写测试的虚拟 MAC 一律用 02:xx（本地管理地址），不会误命中。
OUI_TABLE: dict[int, str] = {
    0x00000C: "Cisco",
    0x000142: "Cisco",
    0x0001C7: "Cisco",
    0x00116C: "H3C",
    0x0018BA: "Cisco",
    0x001A1E: "Aruba Networks",
    0x001B0A: "Nortel / 华为早期规划",
    0x001C40: "Huawei",
    0x001EE5: "H3C",
    0x0021A0: "Cisco",
    0x0023AC: "Cisco",
    0x0024C4: "Cisco",
    0x00250F: "Huawei",
    0x0026C6: "H3C",
    0x003A9B: "Cisco",
    0x00E0FC: "Huawei",
    0x0C6803: "Cisco",
    0x0CD996: "Cisco",
    0x1866DA: "Dell",
    0x1C1B0D: "Huawei",
    0x206A8A: "Wallys Communications",
    0x3C5282: "H3C",
    0x3CD9B3: "Hangzhou H3C Technologies",
    0x3CE5A6: "Hangzhou H3C Technologies",
    0x48435A: "Mellanox / NVIDIA",
    0x4C1FCC: "Huawei",
    0x5006AB: "Cisco",
    0x54781A: "Cisco",
    0x581F28: "Huawei",
    0x5C0214: "Huawei",
    0x70B3D5: "IEEE 注册分配块",
    0x70CA9B: "Cisco",
    0x78BAF9: "Cisco",
    0x7C1E52: "Huawei",
    0x980284: "Cisco",
    0xA0D37A: "Huawei",
    0xA86BAD: "Huawei",
    0xBC0F9A: "Cisco",
    0xC0C1C0: "Cisco",
    0xC88E0C: "Huawei",
    0xD46E5C: "Huawei",
    0xD8B1C4: "Aruba Networks",
    0xE05E53: "H3C",
    # 虚拟机 / 容器常用的「本地管理」OUI 前缀
    0x02004C: "虚拟机（VirtualBox / 常见虚拟网卡）",
    0x080027: "云厂商虚拟网卡（Amazon / 阿里云 / 腾讯云等常见前缀）",
    0x525400: "QEMU / KVM 虚拟网卡",
}

_SEPARATORS = {"-", ":", " ", "."}


class MacError(ValueError):
    """MAC 输入非法。消息面向用户，可直接展示。"""


@dataclass(frozen=True, slots=True)
class MacForms:
    """一个 MAC 地址的各种常见写法。"""

    value: int  #: 48 位无符号整数
    cisco: str  #: aabb.ccdd.eeff —— 思科 / H3C 接口
    hyphen: str  #: aa-bb-cc-dd-ee-ff —— Windows ipconfig
    colon: str  #: aa:bb:cc:dd:ee:ff —— Linux / Wireshark
    colon_upper: str  #: AA:BB:CC:DD:EE:FF —— 华为部分输出
    plain: str  #: aabbccddeeff —— DHCP 绑定表
    spaced: str  #: aa bb cc dd ee ff
    huawei: str  #: aabb-ccdd-eeff —— 华为 / H3C display 输出
    windows: str  #: AABB-CCDD-EEFF —— getmac / 注册表
    prefixed: str  #: 0xaabbccddeeff
    oui_bytes: str  #: aabbcc —— OUI 前缀

    @property
    def oui(self) -> int:
        return self.value >> 24

    @property
    def nic(self) -> str:
        """NIC 部分（后 3 字节），厂商自行分配。"""
        return f"{self.value & 0xFFFFFF:06x}"


def parse_mac(text: str) -> int:
    """把任意一种常见写法解析成 48 位整数。

    分隔符只允许 ``-`` / ``:`` / 空格 / ``.`` 四选一，且必须贯穿全文；
    点分写法固定 ``aabb.ccdd.eeff``，其余分隔符每段固定 2 位（段数不匹配时
    统一报长度错误，便于用户一眼看出少写了还是多写了）。
    """
    value = text.strip()
    if value.lower().startswith("0x"):
        value = value[2:].strip()
    if not value:
        raise MacError("MAC 地址不能为空")
    digits = _strip_separators(value, text)
    if any(ch not in "0123456789abcdefABCDEF" for ch in digits):
        raise MacError(f"MAC 含有非法字符：{text!r}（只允许十六进制 0-9 / A-F）")
    if len(digits) != 12:
        raise MacError(f"MAC 长度应为 12 位十六进制，实际 {len(digits)} 位：{text!r}")
    return int(digits, 16)


def _strip_separators(body: str, original: str) -> str:
    separators = {ch for ch in body if not ch.isalnum()}
    if not separators:
        return body
    unknown = separators - _SEPARATORS
    if unknown:
        chars = "、".join(repr(ch) for ch in sorted(unknown))
        raise MacError(f"MAC 含有非法分隔符：{chars}（只允许 - : 空格 . 之一）")
    if len(separators) > 1:
        chars = "、".join(repr(ch) for ch in sorted(separators))
        raise MacError(f"MAC 分隔符混用：{chars}，请统一使用 - : 空格 . 之一")
    separator = separators.pop()
    groups = body.split(separator)
    if any(not group for group in groups):
        raise MacError(f"MAC 分隔位置异常：{original!r}")
    if separator == ".":
        if len(groups) != 3 or any(len(group) != 4 for group in groups):
            raise MacError(f"点分 MAC 应为 aabb.ccdd.eeff 形式：{original!r}")
    elif len({len(group) for group in groups}) == 1:
        # 段长一致（2 位或 4 位）就放行，段数交给长度校验统一报错
        return "".join(groups)
    else:
        raise MacError(f"MAC 每段长度应一致（2 位或 4 位）：{original!r}")
    return "".join(groups)


def format_mac(value: int) -> MacForms:
    """由 48 位整数生成全部写法。"""
    if not 0 <= value <= 0xFFFFFFFFFFFF:
        raise MacError(f"MAC 超出 48 位范围：{value}")
    digits = f"{value:012x}"
    pairs = [digits[index : index + 2] for index in range(0, 12, 2)]
    groups = [digits[index : index + 4] for index in range(0, 12, 4)]
    colon = ":".join(pairs)
    return MacForms(
        value=value,
        cisco=".".join(groups),
        hyphen="-".join(pairs),
        colon=colon,
        colon_upper=colon.upper(),
        plain=digits,
        spaced=" ".join(pairs),
        huawei="-".join(groups),
        windows="-".join(groups).upper(),
        prefixed=f"0x{digits}",
        oui_bytes=digits[:6],
    )


def mac_forms(text: str) -> MacForms:
    """解析 + 生成全部写法的一站式入口。"""
    return format_mac(parse_mac(text))


def lookup_oui(oui: int) -> str | None:
    """按 OUI 查厂商；未收录返回 ``None``。"""
    return OUI_TABLE.get(oui & 0xFFFFFF)


def describe_mac(forms: MacForms) -> list[str]:
    """给出该地址的中文说明，如「组播地址」「本地管理地址」「OUI → 厂商」。"""
    notes: list[str] = []
    first = forms.value >> 40
    if forms.value == 0xFFFFFFFFFFFF:
        notes.append("全网广播地址（FF:FF:FF:FF:FF:FF），不能作为单播源/目的")
        return notes
    if forms.value == 0:
        notes.append("全零地址，通常表示接口未配置 MAC")
        return notes
    if first & 0x01:
        notes.append("组播地址（第 1 字节最低位为 1）")
    if first & 0x02:
        notes.append("本地管理地址，OUI 由设备自行分配，无法据此判断厂商")
    else:
        vendor = lookup_oui(forms.oui)
        if vendor:
            notes.append(f"OUI {forms.oui_bytes} → {vendor}")
        else:
            notes.append(f"OUI {forms.oui_bytes} 本地库未收录")
    return notes
