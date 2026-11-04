"""MAC 内核单测：全部格式互转、非法输入与 OUI 说明。"""

from __future__ import annotations

import pytest

from netops.core import mac

SAMPLE = 0x00116C0A1B2C


@pytest.mark.parametrize(
    "text",
    [
        "00:11:6c:0a:1b:2c",
        "00-11-6C-0A-1B-2C",
        "0011.6c0a.1b2c",
        "0011-6c0a-1b2c",
        "00116C0A1B2C",
        "0x00116c0a1b2c",
        "0X00116C0A1B2C",
        "00 11 6c 0a 1b 2c",
        "  00:11:6C:0A:1B:2C  ",
    ],
)
def test_parse_mac_accepts_common_notations(text: str) -> None:
    assert mac.parse_mac(text) == SAMPLE


def test_parse_mac_rejects_empty() -> None:
    with pytest.raises(mac.MacError, match="不能为空"):
        mac.parse_mac("   ")
    with pytest.raises(mac.MacError, match="不能为空"):
        mac.parse_mac("0x")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("00:11:6c:0a:1b", "长度应为 12 位"),
        ("00:11:6c:0a:1b:2c:3d", "长度应为 12 位"),
        ("00:11:6c:0a:1b:2", "每段长度应一致"),
        ("00:11:6c:0a:1b:zz", "非法字符"),
        ("00:11:6c:0a:1b:2c:3d:4e", "长度应为 12 位"),
        ("00:11:6c:0a:1:2c", "每段长度应一致"),
        ("0011.6c0a.1b2", "点分 MAC"),
        ("0011.6c0a.1b2c.3d", "点分 MAC"),
        ("00:11-6c:0a:1b:2c", "分隔符混用"),
        ("00:11:6c:0a:1b:2c/", "非法分隔符"),
        ("00::11:6c:0a:1b:2c", "分隔位置异常"),
    ],
)
def test_parse_mac_rejects_invalid(text: str, message: str) -> None:
    with pytest.raises(mac.MacError, match=message):
        mac.parse_mac(text)


def test_format_mac_all_forms() -> None:
    forms = mac.format_mac(SAMPLE)
    assert forms.cisco == "0011.6c0a.1b2c"
    assert forms.hyphen == "00-11-6c-0a-1b-2c"
    assert forms.colon == "00:11:6c:0a:1b:2c"
    assert forms.colon_upper == "00:11:6C:0A:1B:2C"
    assert forms.plain == "00116c0a1b2c"
    assert forms.spaced == "00 11 6c 0a 1b 2c"
    assert forms.huawei == "0011-6c0a-1b2c"
    assert forms.windows == "0011-6C0A-1B2C"
    assert forms.prefixed == "0x00116c0a1b2c"
    assert forms.oui_bytes == "00116c"
    assert forms.oui == 0x00116C
    assert forms.nic == "0a1b2c"


def test_format_mac_round_trips_every_form() -> None:
    forms = mac.format_mac(SAMPLE)
    for text in (
        forms.cisco,
        forms.hyphen,
        forms.colon,
        forms.colon_upper,
        forms.plain,
        forms.spaced,
        forms.huawei,
        forms.windows,
        forms.prefixed,
    ):
        assert mac.parse_mac(text) == SAMPLE


def test_format_mac_bounds() -> None:
    assert mac.format_mac(0).colon == "00:00:00:00:00:00"
    assert mac.format_mac(0xFFFFFFFFFFFF).colon == "ff:ff:ff:ff:ff:ff"
    with pytest.raises(mac.MacError, match="48 位"):
        mac.format_mac(0x1_0000_0000_0000)
    with pytest.raises(mac.MacError, match="48 位"):
        mac.format_mac(-1)


def test_mac_forms_is_parse_plus_format() -> None:
    assert mac.mac_forms("0011.6c0a.1b2c") == mac.format_mac(SAMPLE)


def test_lookup_oui_known_and_unknown() -> None:
    assert mac.lookup_oui(0x00116C) == "H3C"
    assert mac.lookup_oui(0x001C40) == "Huawei"
    assert mac.lookup_oui(0x02004C) is not None
    assert mac.lookup_oui(0x02ABCD) is None


def test_describe_mac_vendor() -> None:
    notes = mac.describe_mac(mac.format_mac(SAMPLE))
    assert notes == ["OUI 00116c → H3C"]


def test_describe_mac_unknown_vendor() -> None:
    notes = mac.describe_mac(mac.format_mac(0x02ABCD000001))
    # 02 是本地管理位，无论 OUI 是否命中都不应据此报厂商
    assert notes == ["本地管理地址，OUI 由设备自行分配，无法据此判断厂商"]


def test_describe_mac_unknown_global_oui() -> None:
    notes = mac.describe_mac(mac.format_mac(0x00ABCD0D0E0F))
    assert notes == ["OUI 00abcd 本地库未收录"]


def test_describe_mac_multicast_and_broadcast() -> None:
    multicast = mac.describe_mac(mac.format_mac(0x01005E000001))
    assert any("组播地址" in note for note in multicast)
    broadcast = mac.describe_mac(mac.format_mac(0xFFFFFFFFFFFF))
    assert broadcast == ["全网广播地址（FF:FF:FF:FF:FF:FF），不能作为单播源/目的"]
    zero = mac.describe_mac(mac.format_mac(0))
    assert zero == ["全零地址，通常表示接口未配置 MAC"]
