"""Schema-driven network script templates and rendering service."""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator

from netops.core.templates import render

PACKAGE = "netops.resources.templates.scripts"


class ScriptError(ValueError):
    """用户输入或模板渲染失败。"""


class InterfaceRangeError(ValueError):
    """接口或端口范围格式不合法。"""

    """接口或端口范围格式不合法。"""


def parse_interface_range(text: str, *, default_type: str = "GigabitEthernet") -> str:
    """Normalize common shorthand/comma range syntax to Comware interface-range grammar."""
    raw = text.strip()
    if not raw:
        raise InterfaceRangeError("端口范围不能为空")
    allowed = {
        "GE": "GigabitEthernet",
        "GI": "GigabitEthernet",
        "GIGE": "GigabitEthernet",
        "GIGABITETHERNET": "GigabitEthernet",
        "TE": "Ten-GigabitEthernet",
        "XGE": "Ten-GigabitEthernet",
        "TEN-GIGABITETHERNET": "Ten-GigabitEthernet",
    }
    interface_pattern = re.compile(
        r"(?:(GigabitEthernet|Ten-GigabitEthernet|GE|Gi|XGE|Te)\s*)?(\d+/\d+/\d+)", re.IGNORECASE
    )
    parts = [part.strip() for part in re.split(r"[,;，；]+", raw) if part.strip()]
    normalized: list[str] = []
    for part in parts:
        match = re.fullmatch(r"(.+?)\s+(?:to|-)\s+(.+)", part, re.IGNORECASE)
        if match:
            start, end = match.groups()
        else:
            shorthand = re.fullmatch(
                r"(?:(GigabitEthernet|Ten-GigabitEthernet|GE|Gi|XGE|Te)\s*)?(\d+/\d+/\d+)-(\d+)",
                part,
                re.IGNORECASE,
            )
            if shorthand:
                kind, start_port, end_port = shorthand.groups()
                start = f"{kind or default_type} {start_port}"
                slot = start_port.rsplit("/", 1)[0]
                end = f"{kind or default_type} {slot}/{end_port}"
            else:
                start = end = part
        start_match, end_match = (
            interface_pattern.fullmatch(start.strip()),
            interface_pattern.fullmatch(end.strip()),
        )
        if not start_match or not end_match:
            raise InterfaceRangeError(
                f"无法识别端口范围：{part}。示例：1/0/1-24 或 GE1/0/1 to GE1/0/24"
            )
        start_kind = allowed[start_match.group(1).upper()] if start_match.group(1) else default_type
        end_kind = allowed[end_match.group(1).upper()] if end_match.group(1) else default_type
        start_id, end_id = start_match.group(2), end_match.group(2)
        start_parts, end_parts = start_id.split("/"), end_id.split("/")
        if start_kind != end_kind or start_parts[:2] != end_parts[:2]:
            raise InterfaceRangeError(f"范围起止接口必须属于同一类型和槽位：{part}")
        start_port, end_port = int(start_parts[-1]), int(end_parts[-1])
        if start_port > end_port:
            raise InterfaceRangeError(f"端口范围不能倒序：{part}")
        if start_id == end_id:
            normalized.append(f"{start_kind}{start_id}")
        else:
            normalized.append(f"{start_kind}{start_id} to {start_kind}{end_id}")
    return ", ".join(dict.fromkeys(normalized))


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
        return tuple(item for item in self.registry.all() if item.template_id == "h3c.comware_v7")

    def defaults(self, template_id: str) -> dict[str, Any]:
        template = self.registry.get(template_id)
        return {
            name: spec["default"]
            for name, spec in template.schema.get("properties", {}).items()
            if "default" in spec
        }

    def sensitive_fields(self, template_id: str) -> tuple[str, ...]:
        template = self.registry.get(template_id)
        return tuple(
            name
            for name, spec in template.schema.get("properties", {}).items()
            if spec.get("sensitive", False)
        )

    def redacted_values(self, template_id: str, values: Mapping[str, Any]) -> dict[str, Any]:
        template = self.registry.get(template_id)
        redacted = dict(values)
        for name in self.sensitive_fields(template_id):
            if name in redacted:
                spec = template.schema.get("properties", {}).get(name, {})
                redacted[name] = spec.get("redact", "<REDACTED>")
        return redacted

    def _normalize_values(self, template_id: str, values: Mapping[str, Any]) -> dict[str, Any]:
        template = self.registry.get(template_id)
        normalized = dict(values)
        for name, spec in template.schema.get("properties", {}).items():
            value = normalized.get(name)
            if spec.get("type") == "integer" and isinstance(value, str) and value.strip():
                with suppress(ValueError):
                    normalized[name] = int(value.strip())
            elif spec.get("type") == "array" and isinstance(value, str):
                normalized[name] = [item for item in value.replace(",", " ").split() if item]
            if spec.get("format") == "ipv4-cidr" and isinstance(value, str):
                try:
                    if "/" in value:
                        address, mask = value.strip().split("/", 1)
                        try:
                            network = ipaddress.IPv4Network(f"0.0.0.0/{mask}")
                            interface = ipaddress.ip_interface(f"{address}/{network.prefixlen}")
                        except ValueError:
                            network = ipaddress.IPv4Network(f"{address}/{mask}")
                            interface = ipaddress.ip_interface(f"{address}/{network.prefixlen}")
                    else:
                        interface = ipaddress.ip_interface(value.strip())
                except ValueError:
                    normalized[name] = value
                    normalized[f"{name}__invalid"] = True
                else:
                    normalized[name] = f"{interface.ip}/{interface.network.prefixlen}"
            elif spec.get("format") == "ipv4" and isinstance(value, str) and value:
                try:
                    normalized[name] = str(ipaddress.IPv4Address(value.strip()))
                except ValueError:
                    normalized[name] = value
                    normalized[f"{name}__invalid"] = True
        for name in ("business_range", "uplink_interface"):
            if isinstance(normalized.get(name), str):
                try:
                    normalized[name] = parse_interface_range(normalized[name])
                except InterfaceRangeError as exc:
                    normalized[f"{name}__invalid"] = str(exc)
            else:
                normalized[f"{name}__validated"] = True
        members_text = normalized.get("aggregation_members")
        if isinstance(members_text, str):
            try:
                members = [
                    item.strip() for item in re.split(r"[,;，；]+", members_text) if item.strip()
                ]
                normalized["aggregation_members_render"] = ", ".join(
                    parse_interface_range(item) for item in members
                )
                normalized["aggregation_members__validated"] = True
            except InterfaceRangeError as exc:
                normalized["aggregation_members__invalid"] = str(exc)
        permit = normalized.get("business_permit_vlans")
        if isinstance(permit, str):
            vlan_ids: list[int] = []
            try:
                for token in re.split(r"[,;，；\s]+", permit.strip()):
                    if not token:
                        continue
                    if "-" in token:
                        start, end = (int(piece) for piece in token.split("-", 1))
                        if start > end:
                            raise ValueError
                        vlan_ids.extend(range(start, end + 1))
                    else:
                        vlan_ids.append(int(token))
                if not vlan_ids or any(not 1 <= vlan <= 4094 for vlan in vlan_ids):
                    raise ValueError
                normalized["business_permit_vlans_render"] = " ".join(
                    str(vlan) for vlan in sorted(set(vlan_ids))
                )
                normalized["business_permit_vlans__validated"] = True
            except ValueError:
                normalized["business_permit_vlans__invalid"] = (
                    "Trunk 允许 VLAN 请输入 1-4094 的编号或范围"
                )
        aggregation_enabled = (
            normalized.get("uplink_enabled") and normalized.get("uplink_mode") == "aggregation"
        )
        if (
            aggregation_enabled
            and len(normalized.get("aggregation_members_render", "").split(", ")) < 2
        ):
            normalized["aggregation_members__invalid"] = "链路聚合至少需要两个成员接口"
        if normalized.get("uplink_enabled") and normalized.get("uplink_mode") == "single":
            normalized["uplink_interface"] = normalized.get("uplink_interface", "")
        return normalized

    def validate(self, template_id: str, values: Mapping[str, Any]) -> list[str]:
        template = self.registry.get(template_id)
        normalized = self._normalize_values(template_id, values)
        errors = sorted(
            Draft202012Validator(template.schema).iter_errors(normalized),
            key=lambda e: list(e.path),
        )
        for name in ("business_range", "uplink_interface", "aggregation_members"):
            if normalized.get(f"{name}__validated"):
                errors = [error for error in errors if list(error.path) != [name]]
            if normalized.get(f"{name}__invalid"):
                detail = normalized[f"{name}__invalid"]
                errors.append(
                    type(
                        "ValidationError",
                        (),
                        {"path": (name,), "validator": "pattern", "message": detail},
                    )()
                )
        if normalized.get("business_permit_vlans__validated"):
            errors = [error for error in errors if list(error.path) != ["business_permit_vlans"]]
        if normalized.get("business_permit_vlans__invalid"):
            errors.append(
                type(
                    "ValidationError",
                    (),
                    {
                        "path": ("business_permit_vlans",),
                        "validator": "pattern",
                        "message": normalized["business_permit_vlans__invalid"],
                    },
                )()
            )
        for name in template.schema.get("properties", {}):
            invalid = normalized.get(f"{name}__invalid")
            if invalid:
                detail = invalid if isinstance(invalid, str) else "格式不正确"
                errors.append(
                    type(
                        "ValidationError",
                        (),
                        {"path": (name,), "validator": "pattern", "message": detail},
                    )()
                )
        return [self._message(error) for error in errors]

    def render_redacted(self, template_id: str, values: Mapping[str, Any]) -> RenderedScript:
        template = self.registry.get(template_id)
        redacted = self.redacted_values(template_id, values)
        normalized = self._normalize_values(template_id, redacted)
        normalized.update(redacted)
        try:
            content = render(template.source, **normalized).rstrip() + "\n"
        except Exception as exc:  # noqa: BLE001
            raise ScriptError(f"脚本渲染失败：{exc}") from exc
        return RenderedScript(template, redacted, content)

    def render(self, template_id: str, values: Mapping[str, Any]) -> RenderedScript:
        template = self.registry.get(template_id)
        normalized = self._normalize_values(template_id, values)
        errors = self.validate(template_id, values)
        if errors:
            raise ScriptError("；".join(errors))
        try:
            content = render(template.source, **normalized).rstrip() + "\n"
        except Exception as exc:  # noqa: BLE001
            raise ScriptError(f"脚本渲染失败：{exc}") from exc
        return RenderedScript(template, normalized, content)

    def force_render(
        self, template_id: str, values: Mapping[str, Any]
    ) -> tuple[RenderedScript, list[str]]:
        """Render the provided values even when invalid, and return validation messages."""
        template = self.registry.get(template_id)
        errors = self.validate(template_id, values)
        normalized = self._normalize_values(template_id, values)
        if "aggregation_members__invalid" in normalized:
            normalized["aggregation_members_render"] = str(values.get("aggregation_members", ""))
        if "business_permit_vlans__invalid" in normalized:
            normalized["business_permit_vlans_render"] = str(
                values.get("business_permit_vlans", "")
            )
        for name in template.schema.get("properties", {}):
            normalized.pop(f"{name}__invalid", None)
            normalized.pop(f"{name}__validated", None)
        content = render(template.source, **normalized).rstrip() + "\n"
        if errors:
            marked = "\n".join(f"# !! INVALID: {message}" for message in errors)
            content = f"{marked}\n{content}"
        return RenderedScript(template, normalized, content), errors

    @staticmethod
    def _message(error: Any) -> str:
        path = "、".join(str(part) for part in error.path)
        prefix = f"字段「{path}」" if path else "输入"
        if error.validator == "required":
            missing = error.message.split("'", 2)[1]
            return f"缺少必填字段：{missing}"
        if error.validator == "enum" and path == "vty_max":
            return "VTY 末号请选择 31 或 63"
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
