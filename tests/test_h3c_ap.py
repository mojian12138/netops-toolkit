"""H3C AP 注册配置生成单测：纯字符串处理，不碰设备。"""

from __future__ import annotations

from datetime import datetime

import pytest

from netops.services.h3c_ap import (
    MAX_APS,
    ApError,
    ApGroup,
    build_batch,
    parse_ap,
    parse_ap_groups,
    parse_csv,
    parse_service_templates,
    render_config,
    serial_warning,
)

_STAMP = datetime(2026, 11, 4, 19, 30, 0)


def _ap(name: str = "office-3f-01", serial: str = "210235A1B2C3D4E5F6", **kwargs):
    return parse_ap(name=name, serial=serial, model=kwargs.pop("model", "WA6320"), **kwargs)


# ---------------------------------------------------------------------------
# 服务模板解析
# ---------------------------------------------------------------------------


def test_parse_service_templates_accepts_separators() -> None:
    assert parse_service_templates("10 11") == (10, 11)
    assert parse_service_templates("10,11,12") == (10, 11, 12)
    assert parse_service_templates("10;11；12") == (10, 11, 12)
    assert parse_service_templates([10, 11]) == (10, 11)
    assert parse_service_templates("") == ()
    assert parse_service_templates(None) == ()


def test_parse_service_templates_dedups_preserving_order() -> None:
    assert parse_service_templates("11 10 11") == (11, 10)


def test_parse_service_templates_rejects_bad_values() -> None:
    with pytest.raises(ApError, match="必须是数字"):
        parse_service_templates("abc")
    with pytest.raises(ApError, match="1-4094"):
        parse_service_templates("0")
    with pytest.raises(ApError, match="1-4094"):
        parse_service_templates("9999")
    with pytest.raises(ApError, match="最多"):
        parse_service_templates("1 2 3 4 5 6 7 8 9")


# ---------------------------------------------------------------------------
# 单条校验
# ---------------------------------------------------------------------------


def test_parse_ap_normalizes_and_validates() -> None:
    ap = _ap(name="  office-3f-01  ", serial="  210235A1B2C3D4E5F6 ", service_templates="10 11")
    assert ap.name == "office-3f-01"
    assert ap.serial == "210235A1B2C3D4E5F6"
    assert ap.service_templates == (10, 11)
    assert ap.radio_count == 2


def test_parse_ap_accepts_real_world_models() -> None:
    for model in ("WA6320", "WA6320-HI", "WTU430", "WA2620E-AGN", "WA4320H"):
        assert _ap(model=model).model == model


def test_parse_ap_rejects_bad_name() -> None:
    with pytest.raises(ApError, match="不能为空"):
        _ap(name="   ")
    with pytest.raises(ApError, match="不合法"):
        _ap(name="-leading")
    with pytest.raises(ApError, match="不合法"):
        _ap(name="has space")
    with pytest.raises(ApError, match="不合法"):
        _ap(name="a" * 64)


def test_serial_length_is_advisory() -> None:
    assert "20 位" in serial_warning("210235A1B2C3D4E5F6")
    assert serial_warning("210235A1B2C3D4E5F6AB") == ""

    with pytest.raises(ApError, match="序列号不能为空"):
        _ap(serial="  ")
    with pytest.raises(ApError, match="序列号不合法"):
        _ap(serial="abc")
    with pytest.raises(ApError, match="型号不能为空"):
        _ap(model="")
    with pytest.raises(ApError, match="型号不合法"):
        _ap(model="6320")


def test_parse_ap_group_name_error_mentions_group() -> None:
    with pytest.raises(ApError, match="AP 组名不合法"):
        _ap(ap_group="bad group")


# ---------------------------------------------------------------------------
# 批次校验
# ---------------------------------------------------------------------------


def test_build_batch_rejects_empty_and_duplicates() -> None:
    with pytest.raises(ApError, match="至少需要一台 AP"):
        build_batch([])
    with pytest.raises(ApError, match="名称重复"):
        build_batch([_ap("a1", "210235A1B2C3D4E5F6"), _ap("a1", "210235A1B2C3D4E5F7")])
    with pytest.raises(ApError, match="序列号重复"):
        build_batch([_ap("a1", "210235A1B2C3D4E5F6"), _ap("a2", "210235A1B2C3D4E5F6")])


def test_build_batch_rejects_oversize() -> None:
    aps = [_ap(f"ap{index}", f"210235A1B2C3D4E{index:04d}") for index in range(MAX_APS + 1)]
    with pytest.raises(ApError, match="最多"):
        build_batch(aps)


def test_build_batch_rejects_unknown_group_member() -> None:
    with pytest.raises(ApError, match="引用了不存在的 AP"):
        build_batch([_ap("a1")], groups=[ApGroup(name="g1", ap_names=("a2",))])


def test_build_batch_rejects_ap_in_two_groups() -> None:
    with pytest.raises(ApError, match="同时出现在组"):
        build_batch(
            [_ap("a1")],
            groups=[
                ApGroup(name="g1", ap_names=("a1",)),
                ApGroup(name="g2", ap_names=("a1",)),
            ],
        )


def test_build_batch_merges_group_declared_on_ap() -> None:
    batch = build_batch([_ap("a1", ap_group="g1"), _ap("a2", "210235A1B2C3D4E5F7", ap_group="g1")])
    assert batch.group_count == 1
    assert batch.groups[0].name == "g1"
    assert batch.groups[0].ap_names == ("a1", "a2")


def test_build_batch_rejects_group_conflict() -> None:
    with pytest.raises(ApError, match="冲突"):
        build_batch(
            [_ap("a1", ap_group="g1")],
            groups=[ApGroup(name="g2", ap_names=("a1",))],
        )


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------


def test_render_single_ap() -> None:
    batch = build_batch([_ap()], generated_at=_STAMP)
    text = render_config(batch)
    assert "# 生成时间：2026-11-04 19:30:00" in text
    assert "wlan ap office-3f-01 model WA6320" in text
    assert " serial-id 210235A1B2C3D4E5F6" in text
    assert "wlan ap-group" not in text  # 没有组就不该出现组配置


def test_render_ap_with_service_templates() -> None:
    batch = build_batch([_ap(service_templates="10 11")], generated_at=_STAMP)
    text = render_config(batch)
    assert " radio 1" in text
    assert "  service-template 10" in text
    assert "  service-template 11" in text
    assert text.count(" radio 1") == 1
    assert " radio 2" not in text


def test_render_ap_without_service_templates_has_no_radio_lines() -> None:
    batch = build_batch([_ap()], generated_at=_STAMP)
    text = render_config(batch)
    assert " radio" not in text


def test_render_multiple_aps_and_group() -> None:
    batch = build_batch(
        [
            _ap("office-3f-01", "210235A1B2C3D4E5F6", ap_group="office-3f"),
            _ap("office-3f-02", "210235A1B2C3D4E5F7", ap_group="office-3f"),
        ],
        generated_at=_STAMP,
    )
    text = render_config(batch)
    assert text.count("wlan ap office-3f-01 model") == 1
    assert text.count("wlan ap office-3f-02 model") == 1
    assert "wlan ap-group office-3f" in text
    assert " ap office-3f-01" in text
    assert " ap office-3f-02" in text


def test_render_is_stable_for_same_input() -> None:
    def build():
        return render_config(build_batch([_ap(service_templates="10")], generated_at=_STAMP))

    assert build() == build()


def test_render_rejects_empty_batch() -> None:
    from netops.services.h3c_ap import ApBatch

    with pytest.raises(ApError, match="没有可生成的 AP"):
        render_config(ApBatch())


# ---------------------------------------------------------------------------
# AP 组文本解析
# ---------------------------------------------------------------------------


def test_parse_ap_groups_supports_both_colons_and_comments() -> None:
    text = """
    # 三楼的组
    office-3f: a1, a2
    office-4f：a3 a4
    lab
    """
    groups = parse_ap_groups(text)
    assert [g.name for g in groups] == ["office-3f", "office-4f", "lab"]
    assert groups[0].ap_names == ("a1", "a2")
    assert groups[1].ap_names == ("a3", "a4")
    assert groups[2].ap_names == ()


def test_parse_ap_groups_rejects_duplicates_and_bad_names() -> None:
    with pytest.raises(ApError, match="重复定义"):
        parse_ap_groups("g1: a1\ng1: a2")
    with pytest.raises(ApError, match="不合法"):
        parse_ap_groups("bad group: a1")


# ---------------------------------------------------------------------------
# CSV 批量导入
# ---------------------------------------------------------------------------


def test_parse_csv_happy_path() -> None:
    text = (
        "name,serial,model,service_templates,ap_group\n"
        "a1,210235A1B2C3D4E5F6,WA6320,10 11,g1\n"
        "a2,210235A1B2C3D4E5F7,WTU430,,\n"
    )
    specs = parse_csv(text)
    assert len(specs) == 2
    assert specs[0].service_templates == (10, 11)
    assert specs[0].ap_group == "g1"
    assert specs[1].service_templates == ()
    assert specs[1].ap_group == ""


def test_parse_csv_skips_comments_and_blank_lines() -> None:
    text = "# 三楼 AP\nname,serial,model\n\na1,210235A1B2C3D4E5F6,WA6320\n# 下面这台先不导\n"
    assert len(parse_csv(text)) == 1


def test_parse_csv_rejects_missing_header_columns() -> None:
    with pytest.raises(ApError, match="表头缺少列"):
        parse_csv("name,serial\na1,210235A1B2C3D4E5F6\n")


def test_parse_csv_rejects_empty() -> None:
    with pytest.raises(ApError, match="内容为空"):
        parse_csv("   \n# 只有注释\n")


def test_parse_csv_reports_line_number() -> None:
    text = "name,serial,model\na1,210235A1B2C3D4E5F6,WA6320\na2,short,WA6320\n"
    with pytest.raises(ApError) as info:
        parse_csv(text)
    assert "第 3 行" in str(info.value)
    assert "序列号不合法" in str(info.value)


def test_parse_csv_rejects_wrong_column_count() -> None:
    # 少写一列 → service_templates 落到 ap_group 上，仍然能解析出合法值，
    # 这是 CSV 的固有限制；但整行缺列导致必填为空时必须报错
    with pytest.raises(ApError) as info:
        parse_csv("name,serial,model\n,210235A1B2C3D4E5F6,WA6320\n")
    assert "第 2 行" in str(info.value)
    assert "名称不能为空" in str(info.value)


def test_csv_roundtrip_through_build_and_render() -> None:
    text = (
        "name,serial,model,service_templates,ap_group\n"
        "office-3f-01,210235A1B2C3D4E5F6,WA6320,10 11,office-3f\n"
        "office-3f-02,210235A1B2C3D4E5F7,WA6320,10 11,office-3f\n"
    )
    batch = build_batch(parse_csv(text), generated_at=_STAMP)
    rendered = render_config(batch)
    assert "wlan ap-group office-3f" in rendered
    # 只数组里的成员行（以 " ap " 开头），别把 "wlan ap ..." 也数进去
    members = [line for line in rendered.splitlines() if line.startswith(" ap office-3f-0")]
    assert members == [" ap office-3f-01", " ap office-3f-02"]
