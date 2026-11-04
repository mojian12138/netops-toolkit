"""Schema-driven network script templates and rendering service."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator

from netops.core.templates import render

PACKAGE = "netops.resources.templates.scripts"


class ScriptError(ValueError):
    """用户输入或模板渲染失败。"""


@dataclass(frozen=True, slots=True)
class ScriptTemplate:
    template_id: str
    title: str
    vendor: str
    category: str
    description: str
    filename: str
    schema: dict[str, Any]
    source: str

    @property
    def fields(self) -> list[dict[str, Any]]:
        properties = self.schema.get("properties", {})
        required = set(self.schema.get("required", []))
        result = []
        for name, spec in properties.items():
            item = dict(spec)
            item["name"] = name
            item["required"] = name in required
            result.append(item)
        return result


@dataclass(frozen=True, slots=True)
class RenderedScript:
    template: ScriptTemplate
    values: dict[str, Any]
    content: str


class ScriptRegistry:
    def __init__(self, templates: list[ScriptTemplate]) -> None:
        self._templates = {item.template_id: item for item in templates}

    @classmethod
    def discover(cls) -> ScriptRegistry:
        root = resources.files(PACKAGE)
        entries: list[ScriptTemplate] = []
        for meta_path in sorted(_walk_files(root, "meta.json"), key=lambda p: str(p)):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            schema_path = meta_path.parent.joinpath("schema.json")
            template_path = meta_path.parent.joinpath(meta["template"])
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            entries.append(
                ScriptTemplate(
                    template_id=meta["id"],
                    title=meta["title"],
                    vendor=meta["vendor"],
                    category=meta["category"],
                    description=meta.get("description", ""),
                    filename=meta["filename"],
                    schema=schema,
                    source=template_path.read_text(encoding="utf-8"),
                )
            )
        if not entries:
            raise ScriptError("未发现脚本模板")
        if len({item.template_id for item in entries}) != len(entries):
            raise ScriptError("脚本模板 ID 重复")
        return cls(sorted(entries, key=lambda x: (x.vendor, x.category, x.title, x.template_id)))

    def all(self) -> tuple[ScriptTemplate, ...]:
        return tuple(self._templates.values())

    def get(self, template_id: str) -> ScriptTemplate:
        try:
            return self._templates[template_id]
        except KeyError as exc:
            raise ScriptError(f"脚本模板不存在：{template_id}") from exc


class ScriptService:
    def __init__(self, registry: ScriptRegistry | None = None) -> None:
        self.registry = registry or ScriptRegistry.discover()

    def templates(self) -> tuple[ScriptTemplate, ...]:
        return self.registry.all()

    def defaults(self, template_id: str) -> dict[str, Any]:
        template = self.registry.get(template_id)
        return {
            name: spec["default"]
            for name, spec in template.schema.get("properties", {}).items()
            if "default" in spec
        }

    def validate(self, template_id: str, values: Mapping[str, Any]) -> list[str]:
        template = self.registry.get(template_id)
        errors = sorted(
            Draft202012Validator(template.schema).iter_errors(dict(values)),
            key=lambda e: list(e.path),
        )
        return [self._message(error) for error in errors]

    def render(self, template_id: str, values: Mapping[str, Any]) -> RenderedScript:
        template = self.registry.get(template_id)
        errors = self.validate(template_id, values)
        if errors:
            raise ScriptError("；".join(errors))
        try:
            content = render(template.source, **dict(values)).rstrip() + "\n"
        except Exception as exc:  # noqa: BLE001
            raise ScriptError(f"脚本渲染失败：{exc}") from exc
        return RenderedScript(template, dict(values), content)

    @staticmethod
    def _message(error: Any) -> str:
        path = "、".join(str(part) for part in error.path)
        prefix = f"字段「{path}」" if path else "输入"
        if error.validator == "required":
            missing = error.message.split("'", 2)[1]
            return f"缺少必填字段：{missing}"
        if error.validator == "pattern":
            return f"{prefix}格式不正确"
        if error.validator == "enum":
            return f"{prefix}取值不在允许范围内"
        return f"{prefix}{error.message.removeprefix(prefix)}"


def _walk_files(base: Any, filename: str) -> list[Any]:
    found: list[Any] = []
    if not base.is_dir():
        return found
    for entry in base.iterdir():
        if entry.is_dir():
            found.extend(_walk_files(entry, filename))
        elif entry.name == filename:
            found.append(entry)
    return found


_DEFAULT_SERVICE: ScriptService | None = None


def default_service() -> ScriptService:
    global _DEFAULT_SERVICE
    if _DEFAULT_SERVICE is None:
        _DEFAULT_SERVICE = ScriptService()
    return _DEFAULT_SERVICE
