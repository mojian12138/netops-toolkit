"""ipcalc 内核单测：覆盖边界、错误输入与六类计算场景。"""

from __future__ import annotations

import pytest

from netops.core import ipcalc


def test_parse_mask_accepts_three_notations() -> None:
    assert ipcalc.parse_mask(24) == 24
    assert ipcalc.parse_mask("/24") == 24
    assert ipcalc.parse_mask("255.255.255.0") == 24
    assert ipcalc.parse_mask("255.255.255.252") == 30


def test_parse_mask_rejects_discontinuous_mask() -> None:
    with pytest.raises(ipcalc.CalcError):
        ipcalc.parse_mask("255.0.255.0")


def test_parse_mask_rejects_out_of_range() -> None:
    with pytest.raises(ipcalc.CalcError):
        ipcalc.parse_mask(33)
    with pytest.raises(ipcalc.CalcError):
        ipcalc.parse_mask("")


def test_network_info_class_c() -> None:
    info = ipcalc.network_info("192.168.1.77/24")
    assert info.network == "192.168.1.0"
    assert info.broadcast == "192.168.1.255"
    assert info.first_host == "192.168.1.1"
    assert info.last_host == "192.168.1.254"
    assert info.usable_hosts == 254
    assert info.mask == "255.255.255.0"
    assert info.wildcard == "0.0.0.255"
    assert info.kind == "private"
    assert info.cidr == "192.168.1.0/24"


def test_network_info_accepts_dotted_mask() -> None:
    info = ipcalc.network_info("10.1.2.3", "255.255.0.0")
    assert info.network == "10.1.0.0"
    assert info.usable_hosts == 65534


def test_network_info_rejects_mask_with_cidr() -> None:
    with pytest.raises(ipcalc.CalcError):
        ipcalc.network_info("10.0.0.1/24", "255.255.255.0")


def test_network_info_slash_31_and_32() -> None:
    p2p = ipcalc.network_info("10.0.0.0/31")
    assert (p2p.first_host, p2p.last_host, p2p.usable_hosts) == ("10.0.0.0", "10.0.0.1", 2)
    single = ipcalc.network_info("10.0.0.5/32")
    assert single.usable_hosts == 1
    assert single.total_addresses == 1


def test_network_info_slash_0() -> None:
    info = ipcalc.network_info("8.8.8.8/0")
    assert info.network == "0.0.0.0"
    assert info.broadcast == "255.255.255.255"
    assert info.total_addresses == 2**32
    assert info.usable_hosts == 2**32 - 2


def test_prefix_mask_roundtrip() -> None:
    for prefix in range(33):
        assert ipcalc.parse_mask(ipcalc.prefix_to_mask(prefix)) == prefix


def test_mask_to_binary() -> None:
    assert ipcalc.mask_to_binary("255.255.255.192") == ("11111111.11111111.11111111.11000000")


def test_aggregate_contiguous_blocks() -> None:
    result = ipcalc.aggregate(["10.0.0.0/24", "10.0.1.0/24"])
    assert result.blocks == ("10.0.0.0/23",)
    assert result.wasted_addresses == 0


def test_aggregate_drops_contained_and_duplicates() -> None:
    result = ipcalc.aggregate("10.0.0.0/16\n10.0.1.0/24\n10.0.1.0/24\n")
    assert result.blocks == ("10.0.0.0/16",)


def test_aggregate_rejects_invalid_line() -> None:
    with pytest.raises(ipcalc.CalcError):
        ipcalc.aggregate(["10.0.0.0/24", "not-a-network"])


def test_aggregate_requires_input() -> None:
    with pytest.raises(ipcalc.CalcError):
        ipcalc.aggregate(["# only comment"])


def test_optimize_merges_gap_with_acceptable_waste() -> None:
    result = ipcalc.optimize(["10.0.0.0/24", "10.0.2.0/24"], max_waste_ratio=0.6)
    assert result.blocks == ("10.0.0.0/22",)
    assert result.wasted_addresses == 512


def test_optimize_keeps_blocks_without_waste_budget() -> None:
    result = ipcalc.optimize(["10.0.0.0/24", "10.0.2.0/24"], max_waste_ratio=0.0)
    assert set(result.blocks) == {"10.0.0.0/24", "10.0.2.0/24"}


def test_split_by_count_rounds_up_to_power_of_two() -> None:
    blocks = ipcalc.split_by_count("192.168.0.0/24", 3)
    assert len(blocks) == 4
    assert blocks[0].cidr == "192.168.0.0/26"
    assert blocks[-1].cidr == "192.168.0.192/26"


def test_split_by_hosts() -> None:
    blocks = ipcalc.split_by_hosts("10.0.0.0/24", 50)
    assert len(blocks) == 4
    assert all(block.usable_hosts >= 50 for block in blocks)


def test_split_by_hosts_exceeding_capacity() -> None:
    with pytest.raises(ipcalc.CalcError):
        ipcalc.split_by_hosts("10.0.0.0/24", 300)


def test_split_by_count_single() -> None:
    blocks = ipcalc.split_by_count("10.0.0.0/24", 1)
    assert [block.cidr for block in blocks] == ["10.0.0.0/24"]


def test_relation_all_four_outcomes() -> None:
    assert ipcalc.relation("10.0.0.0/16", "10.0.1.0/24") == "contained"
    assert ipcalc.relation("10.0.0.0/16", "10.0.0.0/8") == "contains"
    assert ipcalc.relation("10.0.0.0/25", "10.0.0.128/25") == "disjoint"
    assert ipcalc.relation("10.0.0.0/16", "10.0.0.0/16") == "contained"


def test_mask_binary_and_wildcard() -> None:
    assert ipcalc.wildcard_mask(24) == "0.0.0.255"
    assert ipcalc.wildcard_mask(0) == "255.255.255.255"


def test_mask_to_binary_and_hex() -> None:
    assert ipcalc.mask_to_binary(24) == "11111111.11111111.11111111.00000000"
    assert ipcalc.mask_to_binary("255.255.255.252") == ("11111111.11111111.11111111.11111100")
    assert ipcalc.mask_to_hex(24) == "0xFFFFFF00"
    assert ipcalc.mask_to_hex(0) == "0x00000000"
    assert ipcalc.mask_to_hex(32) == "0xFFFFFFFF"


def test_prefix_for_hosts_table() -> None:
    assert ipcalc.prefix_for_hosts(1) == 32
    assert ipcalc.prefix_for_hosts(2) == 31
    assert ipcalc.prefix_for_hosts(3) == 29
    assert ipcalc.prefix_for_hosts(14) == 28
    assert ipcalc.prefix_for_hosts(15) == 27
    assert ipcalc.prefix_for_hosts(30) == 27
    assert ipcalc.prefix_for_hosts(254) == 24
    assert ipcalc.prefix_for_hosts(500) == 23


def test_prefix_for_hosts_rejects_zero() -> None:
    with pytest.raises(ipcalc.CalcError):
        ipcalc.prefix_for_hosts(0)


def test_hosts_for_prefix_matches_network_info() -> None:
    for prefix in (0, 8, 16, 24, 28, 30, 31, 32):
        info = ipcalc.network_info(f"10.0.0.0/{prefix}")
        assert info.usable_hosts == ipcalc.hosts_for_prefix(prefix)


def test_prefix_for_subnets_rounds_up() -> None:
    assert ipcalc.prefix_for_subnets(24, 1) == 24
    assert ipcalc.prefix_for_subnets(24, 2) == 25
    assert ipcalc.prefix_for_subnets(24, 3) == 26
    assert ipcalc.prefix_for_subnets(24, 5) == 27
    assert ipcalc.prefix_for_subnets(16, 256) == 24


def test_prefix_for_subnets_rejects_overflow() -> None:
    with pytest.raises(ipcalc.CalcError):
        ipcalc.prefix_for_subnets(30, 8)
    with pytest.raises(ipcalc.CalcError):
        ipcalc.prefix_for_subnets(24, 0)


def test_subnet_count() -> None:
    assert ipcalc.subnet_count(0) == 1
    assert ipcalc.subnet_count(24) == 16777216


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("  192.168.001.001 ", 0xC0A80101),
        ("11000000.10101000.00000001.00000001", 0xC0A80101),
        ("0b11000000.10101000.00000001.00000001", 0xC0A80101),
        ("0xC0A80101", 0xC0A80101),
        ("c0a80101", 0xC0A80101),
        ("0x1", 1),
        ("0xC0A8.0101", 0xC0A80101),
        ("ff.ff.ff.ff", 0xFFFFFFFF),
        ("3232235777", 0xC0A80101),
    ],
)
def test_parse_ipv4_accepts_many_notations(text: str, expected: int) -> None:
    assert ipcalc.parse_ipv4(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "192.168.1",
        "192.168.1.1.1",
        "256.1.1.1",
        "1.2.3.x",
        "4294967296",
        "0x1FFFFFFFF",
        "C0:A8:01:01",
        "0b12",
        "10.0.0.0-10.0.0.1",
    ],
)
def test_parse_ipv4_rejects_invalid(text: str) -> None:
    with pytest.raises(ipcalc.CalcError):
        ipcalc.parse_ipv4(text)


def test_address_forms_round_trip() -> None:
    forms = ipcalc.address_forms("192.168.1.1")
    assert forms.value == 0xC0A80101
    assert forms.dotted == "192.168.1.1"
    assert forms.binary == "11000000.10101000.00000001.00000001"
    assert forms.hex == "0xC0A80101"
    assert forms.hex_bytes == "C0A80101"
    assert forms.integer == "3232235777"
    assert forms.endian_swap == "1.1.168.192"
    for text in (forms.dotted, forms.binary, forms.hex, forms.integer):
        assert ipcalc.parse_ipv4(text) == forms.value


def test_address_forms_handles_bounds() -> None:
    assert ipcalc.address_forms(0).dotted == "0.0.0.0"
    assert ipcalc.address_forms(0xFFFFFFFF).hex == "0xFFFFFFFF"
    with pytest.raises(ipcalc.CalcError):
        ipcalc.address_forms(0x1_0000_0000)


def test_network_info_is_tolerant_to_leading_zeros() -> None:
    info = ipcalc.network_info("192.168.001.077/24")
    assert info.address == "192.168.1.77"
    assert info.cidr == "192.168.1.0/24"


# -- 包含检测（B6） ---------------------------------------------------------


def test_parse_subject_keeps_host_and_network_apart() -> None:
    host = ipcalc.parse_subject("10.1.2.3")
    assert host.is_host
    assert host.cidr == "10.1.2.3/32"
    network = ipcalc.parse_subject("10.1.2.0/24")
    assert not network.is_host
    assert network.cidr == "10.1.2.0/24"


def test_parse_subject_host_is_single_address_not_class_default() -> None:
    targets = [ipcalc.parse_subject("10.0.0.0/16")]
    assert ipcalc.compare(targets, ipcalc.parse_subject("10.0.0.1")) == ("contained",)
    assert ipcalc.compare(targets, ipcalc.parse_subject("10.1.0.1")) == ("disjoint",)


def test_parse_subject_accepts_hex_and_integer_hosts() -> None:
    assert ipcalc.parse_subject("0xC0A80101").cidr == "192.168.1.1/32"
    assert ipcalc.parse_subject("3232235777").cidr == "192.168.1.1/32"


def test_parse_subject_rejects_bad_input() -> None:
    with pytest.raises(ipcalc.CalcError):
        ipcalc.parse_subject("10.0.0.0/33")
    with pytest.raises(ipcalc.CalcError):
        ipcalc.parse_subject("   ")


def test_parse_subjects_skips_blank_and_comment_lines() -> None:
    subjects = ipcalc.parse_subjects("10.0.0.1\n\n# 办公网\n192.168.0.0/24  # 尾部注释\n")
    assert [subject.text for subject in subjects] == ["10.0.0.1", "192.168.0.0/24"]


def test_parse_subjects_requires_at_least_one() -> None:
    with pytest.raises(ipcalc.CalcError):
        ipcalc.parse_subjects("\n\n# 只有注释\n")


def test_compare_returns_one_kind_per_target() -> None:
    targets = [ipcalc.parse_subject("10.0.0.0/16"), ipcalc.parse_subject("192.168.0.0/24")]
    assert ipcalc.compare(targets, ipcalc.parse_subject("10.0.1.0/24")) == (
        "contained",
        "disjoint",
    )
    assert ipcalc.compare(targets, ipcalc.parse_subject("192.168.0.0/16")) == (
        "disjoint",
        "contains",
    )


def test_is_inside_requires_a_single_target_to_cover() -> None:
    targets = [ipcalc.parse_subject("10.0.0.0/25"), ipcalc.parse_subject("10.0.0.128/25")]
    half = ipcalc.parse_subject("10.0.0.0/24")
    assert ipcalc.is_inside(targets, ipcalc.parse_subject("10.0.0.1"))
    assert not ipcalc.is_inside(targets, half)


def test_uncovered_returns_nothing_when_union_covers() -> None:
    targets = [ipcalc.parse_subject("10.0.0.0/25"), ipcalc.parse_subject("10.0.0.128/25")]
    assert ipcalc.uncovered(targets, ipcalc.parse_subject("10.0.0.0/24")) == []


def test_uncovered_lists_remaining_pieces_in_order() -> None:
    targets = [ipcalc.parse_subject("10.0.0.0/24")]
    holes = ipcalc.uncovered(targets, ipcalc.parse_subject("10.0.0.0/16"))
    assert [str(net) for net in holes] == [
        "10.0.1.0/24",
        "10.0.2.0/23",
        "10.0.4.0/22",
        "10.0.8.0/21",
        "10.0.16.0/20",
        "10.0.32.0/19",
        "10.0.64.0/18",
        "10.0.128.0/17",
    ]


def test_uncovered_of_disjoint_subject_is_itself() -> None:
    targets = [ipcalc.parse_subject("10.0.0.0/8")]
    assert [
        str(net) for net in ipcalc.uncovered(targets, ipcalc.parse_subject("192.168.0.0/24"))
    ] == ["192.168.0.0/24"]


def test_contains_matrix_marks_each_cell() -> None:
    targets = [ipcalc.parse_subject("10.0.0.0/16"), ipcalc.parse_subject("192.168.0.0/24")]
    subjects = [
        ipcalc.parse_subject("10.0.1.0/24"),
        ipcalc.parse_subject("192.168.0.0/16"),
        ipcalc.parse_subject("172.16.0.0/12"),
    ]
    assert ipcalc.contains_matrix(targets, subjects) == [
        ("✓", "·"),
        ("·", "✗+"),
        ("·", "·"),
    ]


def test_relation_label_covers_known_kinds() -> None:
    assert ipcalc.relation_label("contained") == "完全包含"
    assert ipcalc.relation_label("disjoint") == "互不相交"
