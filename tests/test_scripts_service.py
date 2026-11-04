from netops.services.scripts import ScriptError, ScriptService


def test_discover_and_render_defaults():
    service = ScriptService()
    assert len(service.templates()) >= 4
    for item in service.templates():
        result = service.render(item.template_id, service.defaults(item.template_id))
        assert result.content.strip()


def test_invalid_required_value_is_rejected():
    service = ScriptService()
    values = service.defaults("h3c.opening")
    values["hostname"] = ""
    assert service.validate("h3c.opening", values)
    try:
        service.render("h3c.opening", values)
    except ScriptError:
        pass
    else:
        raise AssertionError("invalid values must not render")


def test_render_is_deterministic():
    service = ScriptService()
    values = service.defaults("huawei.opening")
    assert (
        service.render("huawei.opening", values).content
        == service.render("huawei.opening", values).content
    )
