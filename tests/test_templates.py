"""templates 单测：严格未定义变量、文件加载与模板枚举。"""

from __future__ import annotations

import pytest

from netops.core.templates import (
    TemplateError,
    list_templates,
    render,
    render_file,
    template_status,
)


def test_render_inline() -> None:
    assert render("sysname {{ name }}", name="CORE-01") == "sysname CORE-01"


def test_render_loop() -> None:
    text = "{% for v in vlans %}vlan {{ v }}\n{% endfor %}"
    assert render(text, vlans=[10, 20]) == "vlan 10\nvlan 20\n"


def test_render_missing_variable_raises() -> None:
    with pytest.raises(TemplateError):
        render("sysname {{ missing }}")


def test_render_syntax_error_wrapped() -> None:
    with pytest.raises(TemplateError, match="语法错误"):
        render("{% for x in %}")


def test_render_file_ap_register(monkeypatch) -> None:
    output = render_file(
        "h3c/ap_register.j2",
        generated_at="2026-11-03 10:00:00",
        aps=[
            {
                "name": "office-3f-01",
                "model": "WA6320",
                "serial": "210235A1B2C3D4E5F6",
                "service_templates": [10, 11],
            }
        ],
        groups=[{"name": "office-3f", "ap_names": ["office-3f-01"]}],
    )
    assert "wlan ap office-3f-01 model WA6320" in output
    assert "serial-id 210235A1B2C3D4E5F6" in output
    assert "service-template 10" in output
    assert "wlan ap-group office-3f" in output
    assert " ap office-3f-01" in output


def test_render_file_without_groups() -> None:
    output = render_file(
        "h3c/ap_register.j2",
        generated_at="t",
        aps=[{"name": "ap1", "model": "WA6320", "serial": "SN", "service_templates": []}],
        groups=[],
    )
    assert "wlan ap ap1 model WA6320" in output
    assert "ap-group" not in output


def test_render_file_unknown_template() -> None:
    with pytest.raises(TemplateError, match="不存在"):
        render_file("h3c/does-not-exist.j2")


def test_template_status_and_listing() -> None:
    assert template_status("h3c/ap_register.j2")
    assert not template_status("h3c/nope.j2")
    assert "h3c/ap_register.j2" in list_templates()
