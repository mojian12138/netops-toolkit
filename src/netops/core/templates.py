"""Jinja2 模板环境。

厂商脚本全部走模板，新增脚本 = 新增 ``.j2`` 文件 + 一个参数 schema，
不在 Python 里拼字符串。``StrictUndefined`` 保证缺变量时立刻报错，
避免生成半成品配置。
"""

from __future__ import annotations

from importlib import resources

from jinja2 import Environment, StrictUndefined, TemplateSyntaxError

__all__ = [
    "TemplateError",
    "TEMPLATES_PACKAGE",
    "render",
    "render_file",
    "list_templates",
    "template_status",
]

TEMPLATES_PACKAGE = "netops.resources.templates"

_ENV = Environment(
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
    autoescape=False,
)


class TemplateError(RuntimeError):
    """模板缺失或渲染失败；消息面向用户。"""


def render(text: str, **context: object) -> str:
    """渲染一段内联模板（测试与小片段使用）。"""
    try:
        return _ENV.from_string(text).render(**context)
    except TemplateSyntaxError as exc:
        raise TemplateError(f"模板语法错误：{exc}") from exc
    except Exception as exc:  # noqa: BLE001 - StrictUndefined 抛 UndefinedError
        raise TemplateError(f"模板渲染失败：{exc}") from exc


def render_file(name: str, **context: object) -> str:
    """渲染 ``resources/templates`` 下的模板，``name`` 形如 ``h3c/ap_register.j2``。"""
    try:
        source = resources.files(TEMPLATES_PACKAGE).joinpath(name)
    except (ModuleNotFoundError, TypeError) as exc:
        raise TemplateError(f"模板资源不可用：{name}") from exc
    if not source.is_file():
        raise TemplateError(f"模板不存在：{name}")
    return render(source.read_text(encoding="utf-8"), **context)


def list_templates(subdir: str = "") -> list[str]:
    """列出可用模板（相对路径），供 UI 模板树使用。"""
    return _walk_templates(resources.files(TEMPLATES_PACKAGE), subdir.strip("/"))


def _walk_templates(base, prefix: str) -> list[str]:
    if not base.is_dir():
        return []
    names: list[str] = []
    for entry in sorted(base.iterdir(), key=lambda item: item.name):
        if entry.is_dir():
            names.extend(_walk_templates(entry, f"{prefix}/{entry.name}".strip("/")))
        elif entry.name.endswith(".j2"):
            names.append(f"{prefix}/{entry.name}".strip("/"))
    return names


def template_status(name: str) -> bool:
    """模板是否存在，供测试与页面预检。"""
    try:
        return resources.files(TEMPLATES_PACKAGE).joinpath(name).is_file()
    except (ModuleNotFoundError, TypeError):
        return False
