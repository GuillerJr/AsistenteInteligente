from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import os
import re
import stat
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aegis_core.contracts import (
    AgentRole,
    Capability,
    PolicyDecision,
    RiskLevel,
    ToolAuthorization,
    ToolExecutionResult,
)
from aegis_core.tools.audit import AuditSink, NullAuditSink
from aegis_core.tools.broker import PolicyContext, PolicyViolation, ToolDefinition

MCP_PROTOCOL_VERSION = "2025-11-25"
MAX_MCP_CONFIG_BYTES = 65_536
MAX_MCP_SERVERS = 8
MAX_MCP_TOOLS_PER_SERVER = 64
MAX_MCP_TOOL_PAGES = 8
MAX_MCP_MESSAGE_BYTES = 262_144
MAX_MCP_OUTPUT_BYTES = 60_000
MAX_MCP_CONTENT_BLOCKS = 32
MAX_MCP_SCHEMA_NODES = 128
MAX_MCP_SCHEMA_DEPTH = 6
MAX_MCP_PENDING_REQUESTS = 16
MAX_MCP_ARGUMENT_BYTES = 65_536
_MCP_TOOL_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_SERVER_ID = re.compile(r"^[a-z][a-z0-9-]{2,31}$")
_SAFE_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SENSITIVE_ENVIRONMENT_TERMS = frozenset(
    {"CREDENTIAL", "KEY", "PASSWORD", "SECRET", "TOKEN"}
)
_WRITE_CAPABILITIES = frozenset(
    {
        Capability.SYSTEM_CONTROL,
        Capability.MAIL_WRITE,
        Capability.CALENDAR_WRITE,
        Capability.CONTACTS_WRITE,
        Capability.REMINDERS_WRITE,
        Capability.APPLICATION_CONTROL,
        Capability.PROCESS_EXECUTION,
        Capability.SYSTEM_ADMIN,
    }
)
_MCP_CAPABILITIES = frozenset(
    {
        Capability.SYSTEM_READ,
        Capability.SYSTEM_CONTROL,
        Capability.FILESYSTEM_READ,
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
        Capability.NETWORK_DISCOVERY,
        Capability.PROCESS_EXECUTION,
        Capability.SCREEN_CAPTURE,
    }
)


class McpConfigurationError(ValueError):
    """Raised when local MCP configuration cannot be trusted."""


class McpProtocolError(RuntimeError):
    """Raised when a local MCP peer violates its negotiated protocol."""


class McpExecutionError(RuntimeError):
    """Raised when a validated MCP tool call cannot complete safely."""


class McpArguments(BaseModel):
    model_config = ConfigDict(extra="allow")


class McpServerConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    server_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,31}$")
    command: Path
    arguments: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    working_directory: Path | None = None
    environment: dict[str, str] = Field(default_factory=dict, max_length=16)
    capability: Capability
    risk: RiskLevel = RiskLevel.HIGH
    allowed_roles: frozenset[AgentRole] = Field(
        default_factory=lambda: frozenset({AgentRole.PLANNER}),
        min_length=1,
        max_length=3,
    )
    start_timeout_seconds: float = Field(default=8.0, ge=1.0, le=30.0)
    call_timeout_seconds: float = Field(default=15.0, ge=1.0, le=60.0)
    enabled: bool = True

    @field_validator("command")
    @classmethod
    def command_must_be_absolute(cls, value: Path) -> Path:
        if not value.is_absolute() or "\0" in str(value):
            raise ValueError("MCP command must be an absolute path")
        return value

    @field_validator("arguments")
    @classmethod
    def arguments_must_be_bounded(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if not value or len(value.encode("utf-8")) > 2_048 or "\0" in value:
                raise ValueError("MCP process argument is invalid")
        return values

    @field_validator("environment")
    @classmethod
    def environment_must_not_contain_secrets(cls, values: dict[str, str]) -> dict[str, str]:
        for name, value in values.items():
            if (
                _SAFE_ENVIRONMENT_NAME.fullmatch(name) is None
                or any(term in name for term in _SENSITIVE_ENVIRONMENT_TERMS)
                or not value
                or len(value.encode("utf-8")) > 4_096
                or "\0" in value
            ):
                raise ValueError("MCP environment entry is invalid")
        return values

    @model_validator(mode="after")
    def capability_and_risk_must_be_safe(self) -> McpServerConfiguration:
        if self.capability not in _MCP_CAPABILITIES or self.risk is RiskLevel.LOW:
            raise ValueError("MCP capability or risk is not allowed")
        if self.capability in _WRITE_CAPABILITIES and self.risk not in {
            RiskLevel.HIGH,
            RiskLevel.CRITICAL,
        }:
            raise ValueError("mutating MCP servers must be high or critical risk")
        if AgentRole.SYNTHESIZER in self.allowed_roles or AgentRole.ROUTER in self.allowed_roles:
            raise ValueError("MCP tools cannot be exposed to router or synthesizer roles")
        return self


class McpHostConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    servers: tuple[McpServerConfiguration, ...] = Field(
        default_factory=tuple,
        max_length=MAX_MCP_SERVERS,
    )

    @model_validator(mode="after")
    def server_ids_must_be_unique(self) -> McpHostConfiguration:
        identifiers = [server.server_id for server in self.servers]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("duplicate MCP server id")
        return self


def load_mcp_configuration(path: Path) -> McpHostConfiguration:
    if not path.exists():
        return McpHostConfiguration()
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise McpConfigurationError("MCP configuration is unavailable") from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
            or not 0 < metadata.st_size <= MAX_MCP_CONFIG_BYTES
        ):
            raise McpConfigurationError("MCP configuration permissions are unsafe")
        raw = os.read(descriptor, MAX_MCP_CONFIG_BYTES + 1)
    finally:
        os.close(descriptor)
    try:
        return McpHostConfiguration.model_validate_json(raw)
    except (UnicodeError, ValueError) as error:
        raise McpConfigurationError("MCP configuration is invalid") from error


def _validate_executable(configuration: McpServerConfiguration) -> None:
    try:
        metadata = configuration.command.lstat()
    except OSError as error:
        raise McpConfigurationError("MCP executable is unavailable") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid not in {0, os.getuid()}
        or metadata.st_mode & 0o022
        or not os.access(configuration.command, os.X_OK)
    ):
        raise McpConfigurationError("MCP executable is not private or executable")
    directory = configuration.working_directory
    if directory is not None:
        try:
            directory_metadata = directory.lstat()
        except OSError as error:
            raise McpConfigurationError("MCP working directory is unavailable") from error
        if (
            directory.is_symlink()
            or not directory.is_absolute()
            or not stat.S_ISDIR(directory_metadata.st_mode)
            or directory_metadata.st_uid not in {0, os.getuid()}
            or directory_metadata.st_mode & 0o022
        ):
            raise McpConfigurationError("MCP working directory is unsafe")


def _closed_schema(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        schema = json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise McpProtocolError("MCP tool schema is not JSON") from error
    count = 0

    def close(node: Any, depth: int) -> Any:
        nonlocal count
        count += 1
        if count > MAX_MCP_SCHEMA_NODES or depth > MAX_MCP_SCHEMA_DEPTH:
            raise McpProtocolError("MCP tool schema exceeds complexity limit")
        if not isinstance(node, dict):
            return node
        if any(
            key in node
            for key in (
                "$defs",
                "$dynamicRef",
                "$ref",
                "allOf",
                "anyOf",
                "contains",
                "dependentSchemas",
                "else",
                "if",
                "not",
                "oneOf",
                "patternProperties",
                "prefixItems",
                "then",
                "unevaluatedProperties",
            )
        ):
            raise McpProtocolError("MCP tool schema uses unsupported indirection")
        normalized = dict(node)
        schema_type = normalized.get("type")
        if isinstance(schema_type, list) or schema_type is None:
            raise McpProtocolError("MCP tool schema requires an explicit single type")
        if schema_type == "object":
            properties = normalized.get("properties", {})
            if not isinstance(properties, dict) or len(properties) > 64:
                raise McpProtocolError("MCP object schema is unbounded")
            additional_properties = normalized.get("additionalProperties")
            if additional_properties is not None and additional_properties is not False:
                raise McpProtocolError("MCP object schema permits unknown properties")
            normalized["properties"] = {
                key: close(item, depth + 1) for key, item in properties.items()
            }
            normalized["additionalProperties"] = False
        elif schema_type == "array":
            if not isinstance(normalized.get("items"), dict):
                raise McpProtocolError("MCP array schema has no item contract")
            maximum = normalized.get("maxItems", 50)
            if isinstance(maximum, bool) or not isinstance(maximum, int) or not 0 <= maximum <= 50:
                raise McpProtocolError("MCP array schema is unbounded")
            normalized["maxItems"] = maximum
            normalized["items"] = close(normalized["items"], depth + 1)
        elif schema_type == "string":
            maximum = normalized.get("maxLength", 8_192)
            if (
                isinstance(maximum, bool)
                or not isinstance(maximum, int)
                or not 0 <= maximum <= 8_192
            ):
                raise McpProtocolError("MCP string schema is unbounded")
            normalized["maxLength"] = maximum
        elif schema_type not in {"boolean", "integer", "number", "null"}:
            raise McpProtocolError("MCP tool schema type is unsupported")
        return normalized

    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise McpProtocolError("MCP tool input schema must be an object")
    closed = close(schema, 0)
    try:
        Draft202012Validator.check_schema(closed)
    except SchemaError as error:
        raise McpProtocolError("MCP tool input schema is invalid") from error
    return closed


def _argument_guard(schema: dict[str, Any]):
    validator = Draft202012Validator(schema)

    def guard(arguments: BaseModel, context: PolicyContext) -> BaseModel:
        del context
        normalized = arguments.model_dump(mode="python")
        try:
            encoded = json.dumps(normalized, allow_nan=False, separators=(",", ":")).encode()
            if len(encoded) > MAX_MCP_ARGUMENT_BYTES:
                raise PolicyViolation("MCP tool arguments exceed their size limit")
            validator.validate(normalized)
        except (JsonSchemaValidationError, TypeError, ValueError) as error:
            raise PolicyViolation("MCP tool arguments violate their schema") from error
        return McpArguments.model_validate(normalized)

    return guard


@dataclass(frozen=True, slots=True)
class McpToolBinding:
    server_id: str
    remote_name: str
    exposed_name: str
    input_schema: dict[str, Any]
    capability: Capability
    risk: RiskLevel
    allowed_roles: frozenset[AgentRole]


def _exposed_tool_name(server_id: str, remote_name: str, occupied: set[str]) -> str:
    safe_remote = re.sub(r"[^a-z0-9_]", "_", remote_name.casefold()).strip("_") or "tool"
    base = f"mcp_{server_id.replace('-', '_')}__{safe_remote}"
    if not base[0].isalpha():
        base = f"mcp_{base}"
    if len(base) > 54:
        digest = hashlib.sha256(remote_name.encode()).hexdigest()[:8]
        base = f"{base[:54]}_{digest}"
    candidate = base
    suffix = 1
    while candidate in occupied:
        suffix += 1
        candidate = f"{base[:59]}_{suffix}"
    if re.fullmatch(r"^[a-z][a-z0-9_]{2,63}$", candidate) is None:
        raise McpProtocolError("MCP exposed tool name is invalid")
    occupied.add(candidate)
    return candidate


class StdioMcpClient:
    def __init__(
        self,
        configuration: McpServerConfiguration,
        *,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self.configuration = configuration
        self._audit = audit_sink or NullAuditSink()
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_request_id = 1
        self._closed = False
        self._initialized = False

    async def start(self) -> tuple[dict[str, Any], ...]:
        if self._process is not None or self._closed:
            raise McpProtocolError("MCP client lifecycle is invalid")
        _validate_executable(self.configuration)
        environment = {
            "HOME": str(Path.home()),
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin",
            "TMPDIR": os.getenv("TMPDIR", "/private/tmp"),
            **self.configuration.environment,
        }
        try:
            self._process = await asyncio.create_subprocess_exec(
                str(self.configuration.command),
                *self.configuration.arguments,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=self.configuration.working_directory,
                env=environment,
                limit=MAX_MCP_MESSAGE_BYTES + 1,
            )
            if self._process.stdin is None or self._process.stdout is None:
                raise McpProtocolError("MCP stdio transport is unavailable")
            self._reader_task = asyncio.create_task(
                self._read_loop(),
                name=f"mcp-reader-{self.configuration.server_id}",
            )
            async with asyncio.timeout(self.configuration.start_timeout_seconds):
                initialized = await self._request(
                    "initialize",
                    {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "Jarvis", "version": "0.1.0"},
                    },
                )
                self._validate_initialize(initialized)
                await self._notify("notifications/initialized", {})
                tools = await self._list_tools()
            self._initialized = True
            self._audit.record_system_event(
                uuid4(),
                event_type="mcp_server_started",
                component="mcp_host",
                data={
                    "server_id": self.configuration.server_id,
                    "protocol_version": MCP_PROTOCOL_VERSION,
                    "tool_count": len(tools),
                    "contains_user_content": False,
                },
            )
            return tools
        except (OSError, TimeoutError, McpProtocolError):
            await self.close()
            raise

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if not self._initialized or self._closed:
            raise McpExecutionError("MCP server is unavailable")
        try:
            async with asyncio.timeout(self.configuration.call_timeout_seconds):
                result = await self._request(
                    "tools/call",
                    {"name": name, "arguments": dict(arguments)},
                )
            return _sanitize_call_result(result)
        except (OSError, TimeoutError, McpProtocolError) as error:
            await self.close()
            raise McpExecutionError("MCP tool call failed") from error

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._fail_pending(McpProtocolError("MCP client closed"))
        process = self._process
        if process is not None and process.returncode is None:
            if process.stdin is not None:
                process.stdin.close()
                with suppress(BrokenPipeError, ConnectionResetError):
                    await process.stdin.wait_closed()
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=1.0)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        task = self._reader_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._reader_task = None
        self._process = None

    async def _request(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        if self._closed or len(self._pending) >= MAX_MCP_PENDING_REQUESTS:
            raise McpProtocolError("MCP request capacity is unavailable")
        request_id = self._next_request_id
        self._next_request_id += 1
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params)}
            )
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def _notify(self, method: str, params: Mapping[str, Any]) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": dict(params)})

    async def _send(self, payload: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or self._closed:
            raise McpProtocolError("MCP stdio writer is unavailable")
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8") + b"\n"
        except (TypeError, ValueError) as error:
            raise McpProtocolError("MCP request is not JSON") from error
        if len(encoded) > MAX_MCP_MESSAGE_BYTES:
            raise McpProtocolError("MCP request exceeds its size limit")
        async with self._write_lock:
            try:
                process.stdin.write(encoded)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as error:
                raise McpProtocolError("MCP stdio transport closed") from error

    async def _read_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        failure: McpProtocolError | None = None
        try:
            while not self._closed:
                raw = await process.stdout.readline()
                if not raw:
                    failure = McpProtocolError("MCP server closed stdout")
                    break
                if len(raw) > MAX_MCP_MESSAGE_BYTES or not raw.endswith(b"\n"):
                    failure = McpProtocolError("MCP response framing is invalid")
                    break
                try:
                    message = json.loads(raw)
                except (UnicodeError, json.JSONDecodeError):
                    failure = McpProtocolError("MCP response is malformed")
                    break
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    failure = McpProtocolError("MCP response is not JSON-RPC 2.0")
                    break
                if "id" not in message:
                    method = message.get("method")
                    if not isinstance(method, str) or not method.startswith("notifications/"):
                        failure = McpProtocolError("MCP server request is unsupported")
                        break
                    continue
                request_id = message.get("id")
                if isinstance(request_id, bool) or not isinstance(request_id, int):
                    failure = McpProtocolError("MCP response id is invalid")
                    break
                pending = self._pending.get(request_id)
                if pending is None or pending.done():
                    failure = McpProtocolError("MCP response id is unexpected")
                    break
                if "error" in message:
                    error = message.get("error")
                    if not isinstance(error, dict) or not isinstance(error.get("code"), int):
                        failure = McpProtocolError("MCP JSON-RPC error is malformed")
                        break
                    pending.set_exception(McpProtocolError("MCP server returned an error"))
                    continue
                result = message.get("result")
                if not isinstance(result, dict):
                    failure = McpProtocolError("MCP result is malformed")
                    break
                pending.set_result(result)
        except (OSError, ValueError, asyncio.LimitOverrunError) as error:
            failure = McpProtocolError("MCP stdio read failed")
            failure.__cause__ = error
        finally:
            if failure is not None:
                self._fail_pending(failure)
                if process.returncode is None:
                    with suppress(ProcessLookupError):
                        process.kill()

    def _fail_pending(self, error: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)

    @staticmethod
    def _validate_initialize(result: Mapping[str, Any]) -> None:
        if (
            result.get("protocolVersion") != MCP_PROTOCOL_VERSION
            or not isinstance(result.get("capabilities"), dict)
            or not isinstance(result.get("serverInfo"), dict)
            or not isinstance(result["serverInfo"].get("name"), str)
        ):
            raise McpProtocolError("MCP initialization response is incompatible")
        tools_capability = result["capabilities"].get("tools")
        if not isinstance(tools_capability, dict):
            raise McpProtocolError("MCP tools capability is malformed")

    async def _list_tools(self) -> tuple[dict[str, Any], ...]:
        discovered: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(MAX_MCP_TOOL_PAGES):
            result = await self._request(
                "tools/list",
                {"cursor": cursor} if cursor is not None else {},
            )
            tools = result.get("tools")
            if not isinstance(tools, list):
                raise McpProtocolError("MCP tools/list result is malformed")
            for tool in tools:
                if not isinstance(tool, dict):
                    raise McpProtocolError("MCP tool definition is malformed")
                discovered.append(tool)
                if len(discovered) > MAX_MCP_TOOLS_PER_SERVER:
                    raise McpProtocolError("MCP server exposes too many tools")
            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                return tuple(discovered)
            if not isinstance(next_cursor, str) or not 1 <= len(next_cursor) <= 1_024:
                raise McpProtocolError("MCP pagination cursor is invalid")
            cursor = next_cursor
        raise McpProtocolError("MCP tools/list pagination exceeds its limit")


def _sanitize_call_result(result: Mapping[str, Any]) -> dict[str, Any]:
    allowed_top_level = {"content", "structuredContent", "isError", "_meta"}
    if set(result) - allowed_top_level:
        raise McpProtocolError("MCP tool result has unknown fields")
    content = result.get("content", [])
    if not isinstance(content, list) or len(content) > MAX_MCP_CONTENT_BLOCKS:
        raise McpProtocolError("MCP tool content is invalid")
    sanitized: list[dict[str, Any]] = []
    for block in content:
        sanitized.append(_sanitize_content_block(block))
    output: dict[str, Any] = {
        "content": sanitized,
        "isError": result.get("isError") is True,
    }
    if "structuredContent" in result:
        structured = result["structuredContent"]
        if not isinstance(structured, dict):
            raise McpProtocolError("MCP structured content is invalid")
        output["structuredContent"] = structured
    try:
        encoded = json.dumps(
            output,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise McpProtocolError("MCP tool result is not JSON") from error
    if len(encoded) > MAX_MCP_OUTPUT_BYTES:
        raise McpProtocolError("MCP tool result exceeds its size limit")
    return output


def _sanitize_content_block(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        raise McpProtocolError("MCP content block is invalid")
    block_type = value["type"]
    if block_type == "text":
        text = value.get("text")
        if not isinstance(text, str) or len(text.encode("utf-8")) > 32_768:
            raise McpProtocolError("MCP text content is invalid")
        return {"type": "text", "text": text}
    if block_type in {"image", "audio"}:
        data = value.get("data")
        mime_type = value.get("mimeType")
        if (
            not isinstance(data, str)
            or not isinstance(mime_type, str)
            or re.fullmatch(r"^[a-z0-9.+-]+/[a-z0-9.+-]+$", mime_type) is None
            or len(data) > 49_152
        ):
            raise McpProtocolError("MCP binary content is invalid")
        try:
            decoded = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError) as error:
            raise McpProtocolError("MCP binary content is not base64") from error
        if not decoded or len(decoded) > 36_864 or base64.b64encode(decoded).decode() != data:
            raise McpProtocolError("MCP binary content is out of range")
        return {"type": block_type, "data": data, "mimeType": mime_type}
    if block_type == "resource_link":
        uri = value.get("uri")
        name = value.get("name")
        if (
            not isinstance(uri, str)
            or not 1 <= len(uri) <= 2_048
            or not isinstance(name, str)
            or not 1 <= len(name) <= 256
        ):
            raise McpProtocolError("MCP resource link is invalid")
        return {"type": "resource_link", "uri": uri, "name": name}
    if block_type == "resource":
        resource = value.get("resource")
        if not isinstance(resource, dict) or not isinstance(resource.get("uri"), str):
            raise McpProtocolError("MCP embedded resource is invalid")
        body_key = "text" if "text" in resource else "blob" if "blob" in resource else None
        body = resource.get(body_key) if body_key is not None else None
        if body_key is None or not isinstance(body, str) or len(body.encode("utf-8")) > 32_768:
            raise McpProtocolError("MCP embedded resource body is invalid")
        sanitized_resource = {"uri": resource["uri"], body_key: body}
        if isinstance(resource.get("mimeType"), str):
            sanitized_resource["mimeType"] = resource["mimeType"]
        return {"type": "resource", "resource": sanitized_resource}
    raise McpProtocolError("MCP content type is unsupported")


AsyncToolHandler = Callable[
    [ToolAuthorization, PolicyContext],
    Awaitable[ToolExecutionResult],
]


class McpHost:
    def __init__(
        self,
        configuration: McpHostConfiguration,
        *,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._configuration = configuration
        self._audit = audit_sink or NullAuditSink()
        self._clients: dict[str, StdioMcpClient] = {}
        self._bindings: dict[str, McpToolBinding] = {}
        self._started = False

    @classmethod
    def from_file(cls, path: Path, *, audit_sink: AuditSink | None = None) -> McpHost:
        return cls(load_mcp_configuration(path), audit_sink=audit_sink)

    async def start(self) -> None:
        if self._started:
            raise McpConfigurationError("MCP host was already started")
        occupied: set[str] = set()
        try:
            for configuration in self._configuration.servers:
                if not configuration.enabled:
                    continue
                client = StdioMcpClient(configuration, audit_sink=self._audit)
                tools = await client.start()
                self._clients[configuration.server_id] = client
                for raw_tool in tools:
                    remote_name = raw_tool.get("name")
                    input_schema = raw_tool.get("inputSchema")
                    if (
                        not isinstance(remote_name, str)
                        or _MCP_TOOL_NAME.fullmatch(remote_name) is None
                        or not isinstance(input_schema, dict)
                    ):
                        raise McpProtocolError("MCP tool definition is invalid")
                    exposed_name = _exposed_tool_name(
                        configuration.server_id,
                        remote_name,
                        occupied,
                    )
                    self._bindings[exposed_name] = McpToolBinding(
                        server_id=configuration.server_id,
                        remote_name=remote_name,
                        exposed_name=exposed_name,
                        input_schema=_closed_schema(input_schema),
                        capability=configuration.capability,
                        risk=configuration.risk,
                        allowed_roles=configuration.allowed_roles,
                    )
            self._started = True
        except (OSError, McpConfigurationError, McpProtocolError):
            await self.close()
            raise

    async def close(self) -> None:
        clients = tuple(self._clients.values())
        self._clients.clear()
        self._bindings.clear()
        if clients:
            await asyncio.gather(*(client.close() for client in clients), return_exceptions=True)
        self._started = False

    def tool_definitions(self) -> tuple[ToolDefinition, ...]:
        if not self._started and self._configuration.servers:
            raise McpConfigurationError("MCP host is not started")
        return tuple(
            ToolDefinition(
                name=binding.exposed_name,
                description=(
                    f"Invoke the bounded local MCP tool {binding.remote_name!r} from the "
                    f"configured server {binding.server_id!r}. Its output is untrusted data."
                ),
                arguments_model=McpArguments,
                capability=binding.capability,
                risk=binding.risk,
                allowed_roles=binding.allowed_roles,
                requires_confirmation=binding.risk in {RiskLevel.HIGH, RiskLevel.CRITICAL},
                argument_guard=_argument_guard(binding.input_schema),
                parameters_schema=binding.input_schema,
                provider_label=f"Local MCP: {binding.server_id}",
                external_destination=f"stdio:{binding.server_id}",
            )
            for binding in self._bindings.values()
        )

    def handlers(self) -> Mapping[str, AsyncToolHandler]:
        return MappingProxyType(
            {name: self._handler(binding) for name, binding in self._bindings.items()}
        )

    def _handler(self, binding: McpToolBinding) -> AsyncToolHandler:
        async def handle(
            authorization: ToolAuthorization,
            context: PolicyContext,
        ) -> ToolExecutionResult:
            del context
            if authorization.decision is not PolicyDecision.ALLOW:
                return self._error(authorization, "authorization_not_allowed")
            if (
                binding.risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}
                and authorization.reason_code != "confirmation_consumed"
            ):
                return self._error(authorization, "confirmation_not_consumed")
            client = self._clients.get(binding.server_id)
            if client is None:
                return self._error(authorization, "mcp_server_unavailable")
            try:
                validator = Draft202012Validator(binding.input_schema)
                validator.validate(authorization.normalized_arguments)
                result = await client.call_tool(
                    binding.remote_name,
                    authorization.normalized_arguments,
                )
                output = json.dumps(
                    result,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            except (
                JsonSchemaValidationError,
                McpExecutionError,
                McpProtocolError,
                TypeError,
                ValueError,
            ):
                self._audit.record_system_event(
                    uuid4(),
                    event_type="mcp_tool_failed",
                    component="mcp_host",
                    call_id=authorization.call_id,
                    data={
                        "server_id": binding.server_id,
                        "tool": binding.remote_name,
                        "contains_user_content": False,
                    },
                )
                return self._error(authorization, "mcp_execution_failed")
            is_error = result.get("isError") is True
            self._audit.record_system_event(
                uuid4(),
                event_type="mcp_tool_relayed",
                component="mcp_host",
                call_id=authorization.call_id,
                data={
                    "server_id": binding.server_id,
                    "tool": binding.remote_name,
                    "success": not is_error,
                    "output_bytes": len(output.encode("utf-8")),
                    "contains_user_content": False,
                },
            )
            return ToolExecutionResult(
                call_id=authorization.call_id,
                tool_name=authorization.tool_name,
                success=not is_error,
                output=output,
                error_code="mcp_tool_error" if is_error else None,
                metadata={
                    "server_id": binding.server_id,
                    "source": "mcp_2025_11_25_stdio",
                    "verified": True,
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
