from __future__ import annotations

import http.client
import ipaddress
import json
import math
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

from aegis_core.contracts import (
    AgentRole,
    PolicyDecision,
    RiskLevel,
    ToolAuthorization,
    ToolExecutionResult,
)
from aegis_core.mcp.client import McpProtocolError, _sanitize_call_result, mcp_schema_sha256
from aegis_core.plugins.contracts import (
    MCP_PROTOCOL_VERSION,
    McpConnectorManifest,
    McpToolManifest,
    PluginAuthMode,
    PluginPackage,
    exposed_tool_name,
)
from aegis_core.plugins.store import PluginError, PluginStore
from aegis_core.secrets import (
    MacOSPluginSecret,
    SecretNotFoundError,
    contains_likely_secret_material,
)
from aegis_core.skills.contracts import SkillManifest, SkillOrigin
from aegis_core.tools.broker import PolicyContext, PolicyViolation, ToolDefinition

MAX_MCP_RESPONSE_BYTES = 262_144
MCP_TIMEOUT_SECONDS = 12


class PluginArguments(BaseModel):
    model_config = ConfigDict(extra="allow")


class PluginExecutionError(RuntimeError):
    pass


class PluginUnavailableError(PluginExecutionError):
    pass


@dataclass(frozen=True, slots=True)
class PluginToolBinding:
    plugin_id: str
    plugin_name: str
    connector: McpConnectorManifest
    tool: McpToolManifest
    exposed_name: str


def _validate_value(value: Any, schema: dict[str, Any], *, depth: int = 0) -> Any:
    if depth > 4:
        raise PolicyViolation("plugin arguments exceed maximum depth")
    expected = schema["type"]
    if expected == "object":
        if not isinstance(value, dict):
            raise PolicyViolation("plugin argument must be an object")
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        if set(value) - set(properties) or not required.issubset(value):
            raise PolicyViolation("plugin object arguments do not match schema")
        return {
            key: _validate_value(item, properties[key], depth=depth + 1)
            for key, item in value.items()
        }
    if expected == "array":
        if not isinstance(value, list) or len(value) > schema["maxItems"]:
            raise PolicyViolation("plugin array argument is invalid")
        minimum = schema.get("minItems", 0)
        if not isinstance(minimum, int) or len(value) < minimum:
            raise PolicyViolation("plugin array argument is too short")
        return [_validate_value(item, schema["items"], depth=depth + 1) for item in value]
    if expected == "string":
        if not isinstance(value, str) or isinstance(value, bool):
            raise PolicyViolation("plugin string argument is invalid")
        if (
            len(value) > schema["maxLength"]
            or len(value) < schema.get("minLength", 0)
            or any(ord(character) < 32 and character not in {"\n", "\t"} for character in value)
            or contains_likely_secret_material(value)
        ):
            raise PolicyViolation("plugin string argument is unsafe")
    elif expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise PolicyViolation("plugin integer argument is invalid")
    elif expected == "number":
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise PolicyViolation("plugin number argument is invalid")
    elif expected == "boolean":
        if not isinstance(value, bool):
            raise PolicyViolation("plugin boolean argument is invalid")
    if "minimum" in schema and value < schema["minimum"]:
        raise PolicyViolation("plugin numeric argument is below minimum")
    if "maximum" in schema and value > schema["maximum"]:
        raise PolicyViolation("plugin numeric argument is above maximum")
    if "enum" in schema and value not in schema["enum"]:
        raise PolicyViolation("plugin argument is outside enum")
    return value


def _argument_guard(schema: dict[str, Any], binding: PluginToolBinding | None = None):
    def guard(arguments: BaseModel, context: PolicyContext) -> BaseModel:
        del context
        normalized = _validate_value(arguments.model_dump(mode="python"), schema)
        if (
            binding is not None
            and urlparse(binding.connector.endpoint).hostname == "mcp.context7.com"
        ):
            from aegis_core.plugins.context7 import validate_context7_arguments

            validate_context7_arguments(binding.tool.name, normalized)
        return PluginArguments.model_validate(normalized)

    return guard


class _PinnedPostConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, address: str) -> None:
        super().__init__(
            hostname, port=443, timeout=MCP_TIMEOUT_SECONDS, context=ssl.create_default_context()
        )
        self._address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self._address, 443), self.timeout)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def _public_addresses(hostname: str) -> tuple[str, ...]:
    try:
        values = tuple(
            sorted(
                {item[4][0] for item in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)}
            )
        )
    except OSError as error:
        raise PluginExecutionError("plugin endpoint could not be resolved") from error
    if not values:
        raise PluginExecutionError("plugin endpoint has no address")
    for value in values:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as error:
            raise PluginExecutionError("plugin endpoint returned an invalid address") from error
        if not address.is_global:
            raise PluginExecutionError("plugin endpoint resolved outside public Internet")
    return values


def _decode_mcp_response(body: bytes, content_type: str, request_id: str) -> dict[str, Any]:
    try:
        if content_type.startswith("text/event-stream"):
            events = body.decode("utf-8").replace("\r\n", "\n").split("\n\n")
            messages = []
            for event in events:
                data = "\n".join(
                    line[5:].lstrip(" ") for line in event.splitlines() if line.startswith("data:")
                )
                if data:
                    messages.append(json.loads(data))
            if len(messages) != 1:
                raise PluginExecutionError("MCP streaming interaction is unsupported")
            payload = messages[0]
        elif content_type.startswith("application/json"):
            payload = json.loads(body)
        else:
            raise PluginExecutionError("MCP response content type is unsupported")
    except (UnicodeError, ValueError, TypeError, RecursionError) as error:
        raise PluginExecutionError("MCP response is malformed") from error
    if (
        not isinstance(payload, dict)
        or payload.get("jsonrpc") != "2.0"
        or payload.get("id") != request_id
        or "method" in payload
        or (("result" in payload) == ("error" in payload))
    ):
        raise PluginExecutionError("MCP response does not match request")
    if payload.get("error") is not None:
        raise PluginExecutionError("MCP server returned an error")
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("resultType", "complete") != "complete":
        raise PluginExecutionError("MCP result requires unsupported interaction")
    return result


class PluginMcpClient:
    def bind_availability_guard(self, guard: Callable[[str], bool]) -> None:
        previous = getattr(self, "_availability_guard", None)
        self._availability_guard = (
            guard
            if previous is None
            else lambda identifier: previous(identifier) and guard(identifier)
        )

    def _check_available(self, plugin_id: str) -> None:
        guard = getattr(self, "_availability_guard", None)
        if guard is not None and guard(plugin_id) is not True:
            raise PluginUnavailableError("plugin was revoked before request submission")

    def call(self, binding: PluginToolBinding, arguments: dict[str, Any]) -> dict[str, Any]:
        self._check_available(binding.plugin_id)
        if urlparse(binding.connector.endpoint).hostname == "mcp.context7.com":
            from aegis_core.plugins.context7 import validate_context7_arguments

            validate_context7_arguments(binding.tool.name, arguments)
        if any(tool.remote_input_schema_sha256 for tool in binding.connector.tools):
            self.verify_catalogue(binding.plugin_id, binding.connector)
        self._check_available(binding.plugin_id)
        result = self.request(
            binding.plugin_id,
            binding.connector,
            "tools/call",
            {
                "name": binding.tool.name,
                "arguments": arguments,
            },
        )
        try:
            return _sanitize_call_result(
                {
                    key: value
                    for key, value in result.items()
                    if key not in {"resultType", "ttlMs", "cacheScope"}
                }
            )
        except McpProtocolError as error:
            raise PluginExecutionError("MCP tool result is invalid") from error

    def verify_catalogue(self, plugin_id: str, connector: McpConnectorManifest) -> None:
        tools = self.discover_tools(plugin_id, connector)
        catalogue = {tool["name"]: tool for tool in tools}
        for approved in connector.tools:
            pin = approved.remote_input_schema_sha256
            if pin is None:
                continue
            remote = catalogue.get(approved.name)
            if remote is None or not isinstance(remote.get("inputSchema"), dict):
                raise PluginExecutionError("MCP approved tool is missing")
            annotations = remote.get("annotations", {})
            if (
                not isinstance(annotations, dict)
                or annotations.get("readOnlyHint", True) is not True
            ):
                raise PluginExecutionError("MCP tool contradicts read-only approval")
            try:
                actual = mcp_schema_sha256(remote["inputSchema"])
            except McpProtocolError as error:
                raise PluginExecutionError("MCP remote schema is invalid") from error
            if actual != pin:
                raise PluginExecutionError("MCP remote schema changed; review required")

    def discover_tools(self, plugin_id: str, connector: McpConnectorManifest) -> tuple[dict, ...]:
        # This integration admits a single bounded catalogue, not autonomous pagination.
        result = self.request(plugin_id, connector, "tools/list", {})
        tools = result.get("tools")
        if not isinstance(tools, list) or len(tools) > 64 or result.get("nextCursor"):
            raise PluginExecutionError("MCP catalogue is invalid or exceeds the single-page limit")
        names = set()
        for tool in tools:
            if (
                not isinstance(tool, dict)
                or not isinstance(tool.get("name"), str)
                or tool["name"] in names
            ):
                raise PluginExecutionError("MCP catalogue has malformed or duplicate tools")
            names.add(tool["name"])
        return tuple(tools)

    def request(
        self,
        plugin_id: str,
        connector: McpConnectorManifest,
        method: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        self._check_available(plugin_id)
        if method not in {"tools/list", "tools/call"}:
            raise PluginExecutionError("MCP method is not permitted")
        parsed = urlparse(connector.endpoint)
        hostname = parsed.hostname
        if hostname is None:
            raise PluginExecutionError("plugin endpoint is invalid")
        addresses = _public_addresses(hostname)
        request_id = "jarvis-1"
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": {
                    **parameters,
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION,
                        "io.modelcontextprotocol/clientCapabilities": {},
                        "io.modelcontextprotocol/clientInfo": {
                            "name": "Jarvis",
                            "version": "0.1.0",
                        },
                    },
                },
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {
            "Accept": "application/json, text/event-stream",
            "Accept-Encoding": "identity",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
            "Mcp-Method": method,
            "User-Agent": "Jarvis/0.1 MCP",
        }
        if method == "tools/call":
            headers["Mcp-Name"] = parameters["name"]
        if connector.auth is PluginAuthMode.BEARER:
            try:
                credential = MacOSPluginSecret(plugin_id, connector.connector_id).get()
            except SecretNotFoundError as error:
                raise PluginExecutionError("plugin credential is unavailable") from error
            headers["Authorization"] = f"Bearer {credential}"
        target = parsed.path or "/"
        if parsed.query:
            target += f"?{parsed.query}"
        connection = _PinnedPostConnection(hostname, addresses[0])
        try:
            # Connecting can block while a separate owner process revokes a pack.
            # Recheck after TLS, immediately before starting the bounded POST.
            self._check_available(plugin_id)
            connection.connect()
            self._check_available(plugin_id)
            connection.request("POST", target, body=payload, headers=headers)
            response = connection.getresponse()
            if response.getheader("Content-Encoding", "identity").casefold() != "identity":
                raise PluginExecutionError("MCP compressed responses are unsupported")
            body = response.read(MAX_MCP_RESPONSE_BYTES + 1)
            if response.status != 200 or len(body) > MAX_MCP_RESPONSE_BYTES:
                raise PluginExecutionError("MCP request failed")
            return _decode_mcp_response(
                body,
                response.getheader("Content-Type", "").casefold(),
                request_id,
            )
        except (OSError, http.client.HTTPException) as error:
            raise PluginExecutionError("MCP request failed") from error
        finally:
            connection.close()


class PluginRuntime:
    def __init__(
        self,
        packages: tuple[PluginPackage, ...],
        *,
        client: PluginMcpClient | None = None,
        store: PluginStore | None = None,
    ) -> None:
        self._packages = packages
        self._client = client or PluginMcpClient()
        self._store = store
        self._revoked: set[str] = set()
        self._checksums = {p.manifest.plugin_id: p.checksum_sha256 for p in packages}
        if isinstance(self._client, PluginMcpClient):
            self._client.bind_availability_guard(self.is_active)
        self._bindings: dict[str, PluginToolBinding] = {}
        for package in packages:
            manifest = package.manifest
            for connector in manifest.connectors:
                for tool in connector.tools:
                    name = exposed_tool_name(manifest.plugin_id, connector.connector_id, tool.name)
                    if name in self._bindings:
                        raise PluginError("duplicate exposed plugin tool")
                    self._bindings[name] = PluginToolBinding(
                        plugin_id=manifest.plugin_id,
                        plugin_name=manifest.name,
                        connector=connector,
                        tool=tool,
                        exposed_name=name,
                    )

    def skill_manifests(self) -> tuple[SkillManifest, ...]:
        manifests = []
        for package in self._packages:
            for draft in package.manifest.skills:
                resources = tuple(
                    package.resources[path]
                    for path in package.manifest.skill_resources.get(draft.skill_id, ())
                )
                manifests.append(
                    SkillManifest(
                        **draft.model_dump(mode="python", exclude={"resources"}),
                        resources=resources,
                        origin=SkillOrigin.PLUGIN,
                        remote_safe=False,
                        source_version=package.manifest.version,
                    )
                )
        return tuple(manifests)

    def tool_definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(
            ToolDefinition(
                name=binding.exposed_name,
                description=binding.tool.description,
                arguments_model=PluginArguments,
                parameters_schema=binding.tool.input_schema,
                capability=binding.tool.capability,
                risk=binding.tool.risk,
                allowed_roles=frozenset({AgentRole.PLANNER}),
                requires_confirmation=binding.tool.risk in {RiskLevel.HIGH, RiskLevel.CRITICAL},
                argument_guard=_argument_guard(binding.tool.input_schema, binding),
                provider_label=binding.plugin_name,
                external_destination=urlparse(binding.connector.endpoint).hostname,
            )
            for binding in self._bindings.values()
        )

    def handlers(self) -> dict[str, Any]:
        return {name: self._handler(binding) for name, binding in self._bindings.items()}

    @property
    def packages(self) -> tuple[PluginPackage, ...]:
        return self._packages

    def revoke(self, plugin_id: str) -> None:
        self._revoked.add(plugin_id)

    def is_active(self, plugin_id: str) -> bool:
        if plugin_id in self._revoked or plugin_id not in self._checksums:
            return False
        if self._store is None:
            return True
        try:
            record = self._store.get(plugin_id)
            valid = bool(
                record
                and record.enabled
                and record.package.checksum_sha256 == self._checksums[plugin_id]
            )
        except (OSError, ValueError):
            valid = False
        if not valid:
            self.revoke(plugin_id)
        return valid

    def status_payload(self) -> dict[str, Any]:
        return {
            "protocol_version": MCP_PROTOCOL_VERSION,
            "plugins": [
                {
                    "plugin_id": package.manifest.plugin_id,
                    "version": package.manifest.version,
                    "skills": len(package.manifest.skills),
                    "tools": sum(len(connector.tools) for connector in package.manifest.connectors),
                }
                for package in self._packages
            ],
            "tool_count": len(self._bindings),
        }

    def _handler(self, binding: PluginToolBinding):
        def handle(
            authorization: ToolAuthorization,
            context: PolicyContext,
        ) -> ToolExecutionResult:
            del context
            if not self.is_active(binding.plugin_id):
                return self._error(authorization, "plugin_disabled_restart_required")
            if (
                authorization.decision is not PolicyDecision.ALLOW
                or authorization.tool_name != binding.exposed_name
            ):
                return self._error(authorization, "authorization_not_allowed")
            if (
                binding.tool.risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}
                and authorization.reason_code != "confirmation_consumed"
            ):
                return self._error(authorization, "confirmation_not_consumed")
            try:
                arguments = _validate_value(
                    authorization.normalized_arguments,
                    binding.tool.input_schema,
                )
                if urlparse(binding.connector.endpoint).hostname == "mcp.context7.com":
                    from aegis_core.plugins.context7 import validate_context7_arguments

                    validate_context7_arguments(binding.tool.name, arguments)
                result = self._client.call(binding, arguments)
                output = json.dumps(
                    result,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                if len(output.encode("utf-8")) > 65_536:
                    raise PluginExecutionError("MCP result exceeds output limit")
            except PluginUnavailableError:
                return self._error(authorization, "plugin_disabled_restart_required")
            except (PluginExecutionError, PolicyViolation, ValueError, TypeError):
                return self._error(authorization, "plugin_execution_failed")
            return ToolExecutionResult(
                call_id=authorization.call_id,
                tool_name=authorization.tool_name,
                success=result.get("isError") is not True,
                output=output,
                error_code="mcp_tool_error" if result.get("isError") is True else None,
                metadata={
                    "destination": urlparse(binding.connector.endpoint).hostname or "",
                    "plugin_id": binding.plugin_id,
                    "source": "mcp_2026_07_28",
                },
            )

        return handle

    @staticmethod
    def _error(authorization: ToolAuthorization, code: str) -> ToolExecutionResult:
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=False,
            error_code=code,
        )
