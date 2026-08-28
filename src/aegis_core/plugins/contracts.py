from __future__ import annotations

import hashlib
import json
import math
import re
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aegis_core.contracts import Capability, RiskLevel
from aegis_core.secrets import contains_likely_secret_material
from aegis_core.skills.contracts import SkillDraft

PLUGIN_ID_PATTERN = r"^[a-z][a-z0-9-]{2,31}$"
CONNECTOR_ID_PATTERN = r"^[a-z][a-z0-9-]{2,31}$"
MCP_TOOL_NAME_PATTERN = r"^[a-z][a-z0-9_]{2,31}$"
SEMVER_PATTERN = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
MCP_PROTOCOL_VERSION = "2026-07-28"
MAX_PLUGIN_RESOURCES_BYTES = 65_536

PLUGIN_ALLOWED_CAPABILITIES = frozenset(
    {
        Capability.WEB_READ,
        Capability.MAIL_READ,
        Capability.MAIL_WRITE,
        Capability.CALENDAR_READ,
        Capability.CALENDAR_WRITE,
        Capability.CONTACTS_READ,
        Capability.CONTACTS_WRITE,
        Capability.REMINDERS_READ,
        Capability.REMINDERS_WRITE,
        Capability.APPLICATION_CONTROL,
    }
)
_WRITE_CAPABILITIES = frozenset(
    {
        Capability.MAIL_WRITE,
        Capability.CALENDAR_WRITE,
        Capability.CONTACTS_WRITE,
        Capability.REMINDERS_WRITE,
        Capability.APPLICATION_CONTROL,
    }
)
_UNSAFE_PLUGIN_PROSE = (
    "bypass the broker",
    "ignore previous",
    "ignore system",
    "omite la confirmación",
    "omite la confirmacion",
    "skip confirmation",
)


class PluginAuthMode(StrEnum):
    NONE = "none"
    BEARER = "bearer"


def exposed_tool_name(plugin_id: str, connector_id: str, tool_name: str) -> str:
    value = f"plugin_{plugin_id.replace('-', '_')}__{connector_id.replace('-', '_')}__{tool_name}"
    if len(value) > 63:
        raise ValueError("plugin tool name exceeds runtime limit")
    return value


def _validate_prose(value: str, *, minimum: int, maximum: int) -> str:
    if (
        value != " ".join(value.split())
        or not value.isprintable()
        or not minimum <= len(value) <= maximum
        or contains_likely_secret_material(value)
        or any(pattern in value.casefold() for pattern in _UNSAFE_PLUGIN_PROSE)
    ):
        raise ValueError("plugin prose is unsafe or malformed")
    return value


def validate_plugin_json_schema(schema: dict[str, Any], *, depth: int = 0) -> None:
    if depth > 4 or not isinstance(schema, dict):
        raise ValueError("plugin argument schema is too complex")
    allowed_keys = {
        "type",
        "description",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
    }
    if set(schema) - allowed_keys:
        raise ValueError("plugin argument schema uses unsupported keywords")
    schema_type = schema.get("type")
    if schema_type not in {"object", "array", "string", "integer", "number", "boolean"}:
        raise ValueError("plugin argument schema type is unsupported")
    description = schema.get("description")
    if description is not None:
        if not isinstance(description, str):
            raise ValueError("plugin argument description is invalid")
        _validate_prose(description, minimum=3, maximum=240)
    enum = schema.get("enum")
    if enum is not None:
        if (
            not isinstance(enum, list)
            or not 1 <= len(enum) <= 32
            or len({json.dumps(item, sort_keys=True) for item in enum}) != len(enum)
        ):
            raise ValueError("plugin argument enum is invalid")
    for name in ("minLength", "maxLength", "minItems", "maxItems"):
        if name in schema and (
            isinstance(schema[name], bool) or not isinstance(schema[name], int) or schema[name] < 0
        ):
            raise ValueError("plugin schema bound is invalid")
    for name in ("minimum", "maximum"):
        if name in schema and (
            isinstance(schema[name], bool)
            or not isinstance(schema[name], (int, float))
            or not math.isfinite(schema[name])
        ):
            raise ValueError("plugin numeric bound is invalid")
    if schema_type == "object":
        properties = schema.get("properties")
        required = schema.get("required", [])
        if (
            not isinstance(properties, dict)
            or len(properties) > 24
            or schema.get("additionalProperties") is not False
            or not isinstance(required, list)
            or len(required) != len(set(required))
            or any(name not in properties for name in required)
        ):
            raise ValueError("plugin object schema must be closed and bounded")
        for name, child in properties.items():
            if re.fullmatch(r"^[a-z][a-z0-9_]{0,63}$", name) is None:
                raise ValueError("plugin argument name is invalid")
            validate_plugin_json_schema(child, depth=depth + 1)
    elif schema_type == "array":
        items = schema.get("items")
        maximum = schema.get("maxItems")
        if not isinstance(items, dict) or not isinstance(maximum, int) or not 1 <= maximum <= 50:
            raise ValueError("plugin array schema must have bounded items")
        validate_plugin_json_schema(items, depth=depth + 1)
    elif schema_type == "string":
        maximum = schema.get("maxLength")
        if not isinstance(maximum, int) or not 1 <= maximum <= 8_192:
            raise ValueError("plugin string schema must have a bounded length")


class McpToolManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=MCP_TOOL_NAME_PATTERN)
    description: str = Field(min_length=8, max_length=300)
    input_schema: dict[str, Any]
    capability: Capability
    risk: RiskLevel

    @field_validator("description")
    @classmethod
    def description_must_be_safe(cls, value: str) -> str:
        return _validate_prose(value, minimum=8, maximum=300)

    @field_validator("input_schema")
    @classmethod
    def schema_must_be_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_plugin_json_schema(value)
        if value.get("type") != "object":
            raise ValueError("plugin tool input schema must be an object")
        return value

    @model_validator(mode="after")
    def capability_and_risk_must_be_safe(self) -> McpToolManifest:
        if self.capability not in PLUGIN_ALLOWED_CAPABILITIES:
            raise ValueError("plugin capability is not available")
        if self.risk is RiskLevel.LOW:
            raise ValueError("external plugin tools cannot be low risk")
        if self.capability in _WRITE_CAPABILITIES and self.risk not in {
            RiskLevel.HIGH,
            RiskLevel.CRITICAL,
        }:
            raise ValueError("plugin mutations must be high or critical risk")
        return self


class McpConnectorManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    connector_id: str = Field(pattern=CONNECTOR_ID_PATTERN)
    endpoint: str = Field(min_length=12, max_length=2_048)
    auth: PluginAuthMode = PluginAuthMode.NONE
    tools: tuple[McpToolManifest, ...] = Field(min_length=1, max_length=32)

    @field_validator("endpoint")
    @classmethod
    def endpoint_must_be_public_https_shape(cls, value: str) -> str:
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
            or parsed.fragment
            or parsed.hostname in {"localhost", "localhost.localdomain"}
        ):
            raise ValueError("MCP endpoint must be public HTTPS on port 443")
        return value

    @model_validator(mode="after")
    def tool_names_must_be_unique(self) -> McpConnectorManifest:
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("duplicate MCP tool name")
        return self


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    plugin_id: str = Field(pattern=PLUGIN_ID_PATTERN)
    name: str = Field(min_length=3, max_length=80)
    version: str = Field(pattern=SEMVER_PATTERN)
    publisher: str = Field(min_length=2, max_length=100)
    description: str = Field(min_length=8, max_length=300)
    declared_capabilities: frozenset[Capability] = Field(max_length=16)
    allowed_network_hosts: frozenset[str] = Field(default_factory=frozenset, max_length=16)
    skills: tuple[SkillDraft, ...] = Field(default_factory=tuple, max_length=16)
    skill_resources: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    connectors: tuple[McpConnectorManifest, ...] = Field(default_factory=tuple, max_length=8)

    @field_validator("name", "publisher", "description")
    @classmethod
    def prose_must_be_safe(cls, value: str) -> str:
        return _validate_prose(value, minimum=2, maximum=300)

    @field_validator("allowed_network_hosts")
    @classmethod
    def hosts_must_be_normalized(cls, values: frozenset[str]) -> frozenset[str]:
        for value in values:
            if (
                value != value.casefold()
                or len(value) > 253
                or re.fullmatch(r"[a-z0-9.-]+", value) is None
                or value.startswith(".")
                or value.endswith(".")
            ):
                raise ValueError("plugin network host is invalid")
        return values

    @model_validator(mode="after")
    def boundaries_must_match_contents(self) -> PluginManifest:
        if not self.skills and not self.connectors:
            raise ValueError("plugin must contain a skill or connector")
        if not self.declared_capabilities.issubset(PLUGIN_ALLOWED_CAPABILITIES):
            raise ValueError("plugin declares an unavailable capability")
        connector_ids = [connector.connector_id for connector in self.connectors]
        if len(connector_ids) != len(set(connector_ids)):
            raise ValueError("duplicate connector identifier")
        skill_ids = [skill.skill_id for skill in self.skills]
        if len(skill_ids) != len(set(skill_ids)):
            raise ValueError("duplicate plugin skill identifier")
        if any(skill.resources for skill in self.skills):
            raise ValueError("plugin skill resources must use packaged references")
        if set(self.skill_resources) - set(skill_ids) or any(
            not 1 <= len(paths) <= 4 or len(paths) != len(set(paths))
            for paths in self.skill_resources.values()
        ):
            raise ValueError("plugin skill resources are invalid")
        used_capabilities = {
            tool.capability for connector in self.connectors for tool in connector.tools
        }
        if used_capabilities != set(self.declared_capabilities):
            raise ValueError("plugin capabilities must exactly match connector tools")
        endpoint_hosts = {urlparse(connector.endpoint).hostname for connector in self.connectors}
        if endpoint_hosts != set(self.allowed_network_hosts):
            raise ValueError("plugin network hosts must exactly match connector endpoints")
        exposed = {
            exposed_tool_name(self.plugin_id, connector.connector_id, tool.name)
            for connector in self.connectors
            for tool in connector.tools
        }
        for skill in self.skills:
            plugin_names = {name for name in skill.allowed_tools if name.startswith("plugin_")}
            if not plugin_names.issubset(exposed):
                raise ValueError("plugin skill references an undeclared plugin tool")
        return self


class PluginPackage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest: PluginManifest
    resources: dict[str, str] = Field(default_factory=dict)
    checksum_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def package_must_be_canonical_and_intact(self) -> PluginPackage:
        total = 0
        for path, content in self.resources.items():
            if (
                re.fullmatch(r"(?:references|templates)/[a-zA-Z0-9._/-]{1,160}", path) is None
                or ".." in path.split("/")
                or not content
                or contains_likely_secret_material(content)
            ):
                raise ValueError("plugin resource is unsafe")
            total += len(content.encode("utf-8"))
        if total > MAX_PLUGIN_RESOURCES_BYTES:
            raise ValueError("plugin resources exceed size limit")
        referenced = {path for paths in self.manifest.skill_resources.values() for path in paths}
        if referenced != set(self.resources):
            raise ValueError("plugin resources must be referenced exactly once or more")
        if self.checksum_sha256 != self.calculated_checksum():
            raise ValueError("plugin package checksum does not match")
        return self

    def calculated_checksum(self) -> str:
        return calculate_package_checksum(self.manifest, self.resources)

    @classmethod
    def create(
        cls,
        manifest: PluginManifest,
        resources: dict[str, str] | None = None,
    ) -> PluginPackage:
        resolved_resources = resources or {}
        return cls(
            manifest=manifest,
            resources=resolved_resources,
            checksum_sha256=calculate_package_checksum(manifest, resolved_resources),
        )


def calculate_package_checksum(
    manifest: PluginManifest,
    resources: dict[str, str],
) -> str:
    canonical = json.dumps(
        _canonical_value(
            {
                "manifest": manifest.model_dump(mode="python"),
                "resources": resources,
            }
        ),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, frozenset):
        return sorted((_canonical_value(item) for item in value), key=lambda item: str(item))
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    return value
