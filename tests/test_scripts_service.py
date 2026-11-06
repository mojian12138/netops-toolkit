import pytest

from netops.services.scripts import (
    InterfaceRangeError,
    ScriptError,
    ScriptService,
    parse_interface_range,
)


def test_discover_and_render_defaults():
    service = ScriptService()
    assert len(service.templates()) == 1
    assert service.templates()[0].template_id == "h3c.comware_v7"
    rendered = service.render("h3c.comware_v7", service.defaults("h3c.comware_v7"))
    assert rendered.content.endswith("\n")
    assert "user-interface vty 0 63" in rendered.content


def test_invalid_required_value_is_rejected():
    service = ScriptService()
    values = service.defaults("h3c.comware_v7")
    values["hostname"] = ""
    assert service.validate("h3c.comware_v7", values)
    with pytest.raises(ScriptError):
        service.render("h3c.comware_v7", values)


def test_redaction_keeps_sensitive_values_out_of_output():
    service = ScriptService()
    values = service.defaults("h3c.comware_v7")
    values["password"] = "VerySecret!"
    values["mgmt_ip"] = "10.10.50.64/24"
    content = service.render_redacted("h3c.comware_v7", values).content
    assert "VerySecret!" not in content
    assert "10.10.50.64" not in content


def test_parse_interface_ranges():
    assert parse_interface_range("1/0/1-24") == "GigabitEthernet1/0/1 to GigabitEthernet1/0/24"
    assert parse_interface_range("GE1/0/1 to GE1/0/4, 1/0/6") == (
        "GigabitEthernet1/0/1 to GigabitEthernet1/0/4, GigabitEthernet1/0/6"
    )
    with pytest.raises(InterfaceRangeError):
        parse_interface_range("GE1/0/1 to GE1/1/4")


def test_single_uplink_and_trunk_range_render():
    service = ScriptService()
    values = service.defaults("h3c.comware_v7")
    values.update(
        uplink_enabled=True,
        uplink_mode="single",
        uplink_interface="Ten-GigabitEthernet 1/0/49",
        business_enabled=True,
        business_range="1/0/1-24",
        business_mode="trunk",
        business_permit_vlans="10,20-22",
    )
    code = service.render("h3c.comware_v7", values).content
    assert "interface Ten-GigabitEthernet1/0/49" in code
    assert "port link-type trunk" in code
    assert "port trunk permit vlan all" in code
    assert "interface range GigabitEthernet1/0/1 to GigabitEthernet1/0/24" in code
    assert "port trunk permit vlan 10 20 21 22" in code


def test_aggregation_members_render_as_complete_interfaces():
    service = ScriptService()
    values = service.defaults("h3c.comware_v7")
    values.update(uplink_enabled=True, uplink_mode="aggregation")
    code = service.render("h3c.comware_v7", values).content
    assert (
        "interface Bridge-Aggregation1\n description UPLINK-LAG\n link-aggregation mode dynamic\nquit\ninterface Ten-GigabitEthernet1/0/49"
        in code
    )
    assert "interface Ten-GigabitEthernet1/0/49" in code
    assert "interface Ten-GigabitEthernet1/0/50" in code
    assert "port trunk permit vlan all" in code
    assert code.index("interface Bridge-Aggregation1\n port link-type trunk") > code.index(
        "port link-aggregation group 1"
    )


def test_vty_31_and_63_and_delimiters():
    service = ScriptService()
    values = service.defaults("h3c.comware_v7")
    values["vty_max"] = 31
    code = service.render("h3c.comware_v7", values).content
    assert "user-interface vty 0 31" in code
    assert "line vty" not in code
    assert "quit" in code
    assert code.rstrip().endswith("save force")
