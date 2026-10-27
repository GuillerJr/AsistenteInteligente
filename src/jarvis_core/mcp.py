"""Own bounded stdio MCP transport; remote text and results remain untrusted data.

Supported wire modes are explicit: legacy 2025-11-25 (initialize/initialized),
and modern 2026-07-28 (server/discover and per-request metadata). No era fallback,
automatic reconnect/retry, sampling, elicitation, subscriptions, tasks or HTTP.
Modern input_required results are surfaced, never answered on the user's behalf.

Tool schemas use a deliberately bounded validation subset, documented in
_SCHEMA_KEYS; unsupported validation keywords/references fail closed. This is
not a complete JSON Schema implementation or complete MCP conformance claim.
The private environment prevents accidental configuration inheritance; it is
not an OS sandbox for the explicitly selected executable.

Protocol sources (consulted 2026-10-24):
https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle
https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio
https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning
https://modelcontextprotocol.io/specification/2026-07-28/schema
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import re
import shutil
import signal
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from .state import StateError, ensure_private_directory

LEGACY_VERSION = "2025-11-25"
MODERN_VERSION = "2026-07-28"
Authorize = Callable[[str, Mapping[str, Any]], Awaitable[bool]]
EffectStatus = Literal["not_dispatched", "unknown", "reported"]
_NAME = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")
_SCHEMA_KEYS = {
    "$schema", "type", "properties", "required", "additionalProperties", "items",
    "enum", "const", "minLength", "maxLength", "minimum", "maximum", "minItems",
    "maxItems", "description", "title", "default", "examples", "deprecated",
}
_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}


class McpError(Exception):
    """Sanitized local error; never expose arbitrary peer errors or stderr."""

    def __init__(
        self, code: str, *, effect_status: EffectStatus = "not_dispatched",
        rpc_code: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.effect_status = effect_status
        self.rpc_code = rpc_code


class McpCancelled(asyncio.CancelledError):
    def __init__(self, effect_status: EffectStatus) -> None:
        super().__init__("mcp_cancelled")
        self.effect_status = effect_status


class McpInputRequired(McpError):
    """An external interaction is pending; host must decide how to present it."""

    def __init__(self, payload: Mapping[str, Any], effect_status: EffectStatus) -> None:
        super().__init__("input_required", effect_status=effect_status)
        self.payload = _freeze(payload)


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    # Remote readOnlyHint/destructiveHint never grant authority.
    requires_confirmation: Literal[True] = field(default=True, init=False)


@dataclass(frozen=True)
class McpCallResult:
    payload: Mapping[str, Any]
    is_error: bool
    effect_status: Literal["reported"] = field(default="reported", init=False)
    verified: Literal[False] = field(default=False, init=False)


@dataclass
class _Pending:
    future: asyncio.Future[dict[str, Any]]
    effect: bool
    dispatched: bool = False

    @property
    def effect_status(self) -> EffectStatus:
        return "unknown" if self.effect and self.dispatched else "not_dispatched"


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _json_data(value: Any, depth: int = 0, budget: list[int] | None = None) -> Any:
    budget = [20_000] if budget is None else budget
    budget[0] -= 1
    if depth > 32 or budget[0] < 0:
        raise McpError("data_limit")
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise McpError("invalid_json_data")
        return {key: _json_data(item, depth + 1, budget) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_data(item, depth + 1, budget) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise McpError("invalid_json_data")


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _schema(schema: Any, depth: int = 0) -> None:
    if type(schema) is bool:
        return
    if not isinstance(schema, dict) or depth > 16:
        raise McpError("invalid_tool_schema")
    if set(schema) - _SCHEMA_KEYS:
        raise McpError("unsupported_tool_schema")
    if "$schema" in schema and schema["$schema"] not in {
        "https://json-schema.org/draft/2020-12/schema",
        "http://json-schema.org/draft-07/schema#",
    }:
        raise McpError("unsupported_schema_dialect")
    if "type" in schema and (
        not isinstance(schema["type"], str) or schema["type"] not in _TYPES
    ):
        raise McpError("invalid_tool_schema")
    for key in ("description", "title"):
        if key in schema and not isinstance(schema[key], str):
            raise McpError("invalid_tool_schema")
    if "properties" in schema:
        if not isinstance(schema["properties"], dict):
            raise McpError("invalid_tool_schema")
        for child in schema["properties"].values():
            _schema(child, depth + 1)
    if "required" in schema:
        required = schema["required"]
        if (
            not isinstance(required, list)
            or any(not isinstance(item, str) for item in required)
            or len(required) != len(set(required))
        ):
            raise McpError("invalid_tool_schema")
    for key in ("items", "additionalProperties"):
        if key in schema:
            _schema(schema[key], depth + 1)
    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        if key in schema and (type(schema[key]) is not int or schema[key] < 0):
            raise McpError("invalid_tool_schema")
    for key in ("minimum", "maximum"):
        if key in schema and (type(schema[key]) not in {int, float}):
            raise McpError("invalid_tool_schema")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise McpError("invalid_tool_schema")


def _validate(value: Any, schema: Mapping[str, Any] | bool) -> None:
    if schema is False:
        raise McpError("invalid_tool_arguments")
    if schema is True:
        return
    kind = schema.get("type")
    valid = {
        "object": isinstance(value, dict), "array": isinstance(value, list),
        "string": isinstance(value, str), "number": type(value) in {int, float},
        "integer": type(value) is int, "boolean": type(value) is bool, "null": value is None,
    }
    if kind and not valid[kind]:
        raise McpError("invalid_tool_arguments")
    if "enum" in schema and not any(_same_json(value, item) for item in schema["enum"]):
        raise McpError("invalid_tool_arguments")
    if "const" in schema and not _same_json(value, schema["const"]):
        raise McpError("invalid_tool_arguments")
    if isinstance(value, dict):
        if set(schema.get("required", ())) - value.keys():
            raise McpError("invalid_tool_arguments")
        props = schema.get("properties", {})
        for key, item in value.items():
            _validate(item, props.get(key, schema.get("additionalProperties", True)))
    if isinstance(value, list) and "items" in schema:
        for item in value:
            _validate(item, schema["items"])
    for keys, measured in (
        (("minLength", "maxLength"), len(value) if isinstance(value, str) else None),
        (("minItems", "maxItems"), len(value) if isinstance(value, list) else None),
        (("minimum", "maximum"), value if type(value) in {int, float} else None),
    ):
        low, high = keys
        if measured is not None and (
            (low in schema and measured < schema[low])
            or (high in schema and measured > schema[high])
        ):
            raise McpError("invalid_tool_arguments")


def _same_json(left: Any, right: Any) -> bool:
    if type(left) in {int, float} and type(right) in {int, float}:
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _same_json(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_json(a, b) for a, b in zip(left, right, strict=True)
        )
    return left == right


class McpClient:
    """Launch only an explicitly selected executable, never a shell command.

    Each process gets a fresh private HOME/CWD below directory. Only explicitly
    supplied PATH/LANG/LC_ALL/TZ values may augment the minimal environment;
    owner secrets/configuration are never inherited. Tool authorization is a
    fresh async callback on every exact argument snapshot, default deny.

    The host must bind that callback to the central session/run/operation
    authority. This transport has no session identity or durable grants, and a
    boolean callback alone cannot certify revocation of state hidden inside the
    same callable. Production integration needs a versioned/revalidatable grant
    immediately before the effect; this disconnected client does not claim it.
    """

    def __init__(
        self, command: tuple[str, ...], directory: Path, env: dict[str, str] | None = None,
        *, authorize: Authorize | None = None, protocol_version: str = LEGACY_VERSION,
        request_timeout: float = 20.0, shutdown_timeout: float = 0.5,
        max_line_bytes: int = 1_048_576, max_pages: int = 16, max_items: int = 1_000,
    ) -> None:
        if (
            not isinstance(command, tuple) or not command
            or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in command)
            or not Path(command[0]).is_absolute()
        ):
            raise ValueError("mcp_requires_absolute_argv")
        if Path(command[0]).resolve().name in {"sh", "bash", "zsh", "dash", "csh", "fish"}:
            raise ValueError("mcp_shell_not_allowed")
        if not isinstance(directory, Path) or not directory.is_absolute():
            raise ValueError("mcp_requires_explicit_directory")
        if protocol_version not in {LEGACY_VERSION, MODERN_VERSION}:
            raise ValueError("unsupported_mcp_version")
        if (
            type(request_timeout) not in {int, float}
            or type(shutdown_timeout) not in {int, float}
            or any(type(limit) is not int for limit in (max_line_bytes, max_pages, max_items))
            or not 0 < request_timeout <= 300 or not 0 < shutdown_timeout <= 10
            or not 256 <= max_line_bytes <= 16_777_216 or not 1 <= max_pages <= 100
            or not 1 <= max_items <= 10_000
        ):
            raise ValueError("invalid_mcp_limits")
        supplied = {} if env is None else dict(env)
        if set(supplied) - {"PATH", "LANG", "LC_ALL", "TZ"} or any(
            not isinstance(value, str) or "\0" in value for value in supplied.values()
        ):
            raise ValueError("mcp_environment_not_allowed")
        self.command, self.directory = command, directory
        self.authorize, self.protocol_version = authorize, protocol_version
        self.request_timeout, self.shutdown_timeout = request_timeout, shutdown_timeout
        self.max_line_bytes, self.max_pages, self.max_items = max_line_bytes, max_pages, max_items
        self._environment = supplied
        self.child_custody = None
        self._process: asyncio.subprocess.Process | None = None
        self._spawn: asyncio.Task[asyncio.subprocess.Process] | None = None
        self._reader: asyncio.Task[None] | None = None
        self._stderr: asyncio.Task[None] | None = None
        self._closing: asyncio.Task[None] | None = None
        self._home: Path | None = None
        self._pending: dict[int, _Pending] = {}
        self._write_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._counter = 0
        self._ready = False
        self._failure: str | None = None
        self._tools: dict[str, McpTool] = {}
        self._catalog_generations = {"tools": 0, "resources": 0, "prompts": 0}
        self._resources: set[str] = set()
        self._prompts: set[str] = set()
        self.capabilities: Mapping[str, Any] = MappingProxyType({})
        self.notification_methods: list[str] = []

    async def start(self) -> McpClient:
        async with self._start_lock:
            if self._ready:
                return self
            if self._process is not None or self._closing is not None:
                raise McpError("client_closed")
            try:
                ensure_private_directory(self.directory)
            except StateError:
                raise McpError("invalid_private_directory") from None
            self._home = Path(tempfile.mkdtemp(prefix="mcp-", dir=self.directory))
            for name in ("tmp", "config", "cache"):
                (self._home / name).mkdir(mode=0o700)
            environment = {
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C.UTF-8",
                **self._environment, "HOME": str(self._home), "TMPDIR": str(self._home / "tmp"),
                "XDG_CONFIG_HOME": str(self._home / "config"),
                "XDG_CACHE_HOME": str(self._home / "cache"),
                "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
            }
            try:
                self._spawn = asyncio.create_task(asyncio.create_subprocess_exec(
                    *self.command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, cwd=self._home, env=environment,
                    limit=self.max_line_bytes + 1, start_new_session=True,
                    pass_fds=self.child_custody() if self.child_custody else (),
                ))
                self._process = await asyncio.shield(self._spawn)
                if self._closing is not None:
                    raise McpError("client_closed")
                self._reader = asyncio.create_task(self._read_loop())
                self._stderr = asyncio.create_task(self._drain_stderr())
                if self.protocol_version == MODERN_VERSION:
                    result = await self._request("server/discover", {}, ready=False)
                    versions = result.get("supportedVersions")
                    if (
                        not isinstance(versions, list)
                        or any(not isinstance(version, str) for version in versions)
                        or self.protocol_version not in versions
                    ):
                        raise McpError("unsupported_mcp_version")
                    if (
                        type(result.get("ttlMs")) not in {int, float}
                        or result["ttlMs"] < 0
                        or result.get("cacheScope") not in {"public", "private"}
                    ):
                        raise McpError("invalid_discovery")
                else:
                    result = await self._request("initialize", {
                        "protocolVersion": self.protocol_version, "capabilities": {},
                        "clientInfo": {"name": "Jarvis", "version": "0.1.0a1"},
                    }, ready=False)
                    if result.get("protocolVersion") != self.protocol_version:
                        raise McpError("unsupported_mcp_version")
                    info = result.get("serverInfo")
                    if not isinstance(info, dict) or not all(
                        isinstance(info.get(key), str) for key in ("name", "version")
                    ):
                        raise McpError("invalid_server_info")
                capabilities = result.get("capabilities")
                if not isinstance(capabilities, dict) or any(
                    not isinstance(value, dict) for value in capabilities.values()
                ):
                    raise McpError("invalid_capabilities")
                self.capabilities = _freeze(capabilities)
                if self.protocol_version == LEGACY_VERSION:
                    await self._write({"jsonrpc": "2.0", "method": "notifications/initialized"})
                self._ready = True
                return self
            except OSError:
                await self.aclose(retry=False)
                raise McpError("server_start_failed") from None
            except BaseException:
                await self.aclose(retry=False)
                raise

    async def _write(
        self, message: Mapping[str, Any], pending: _Pending | None = None,
        before_dispatch: Callable[[], None] | None = None,
    ) -> None:
        data = json.dumps(_json_data(message), ensure_ascii=True, allow_nan=False).encode() + b"\n"
        if len(data) > self.max_line_bytes:
            raise McpError("message_limit")
        async with self._write_lock:
            if self._process is None or self._process.stdin is None or self._failure:
                raise McpError(self._failure or "client_closed")
            if before_dispatch is not None:
                before_dispatch()
            if pending:
                pending.dispatched = True
            self._process.stdin.write(data)
            await self._process.stdin.drain()

    async def _request(
        self, method: str, params: dict[str, Any], *, effect: bool = False, ready: bool = True,
        before_dispatch: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if ready and not self._ready:
            raise McpError("client_not_ready")
        if self._closing or self._failure:
            raise McpError(self._failure or "client_closed")
        if len(self._pending) >= 32:
            raise McpError("request_capacity")
        params = _json_data(params)
        if self.protocol_version == MODERN_VERSION:
            params["_meta"] = {
                "io.modelcontextprotocol/protocolVersion": self.protocol_version,
                "io.modelcontextprotocol/clientCapabilities": {},
                "io.modelcontextprotocol/clientInfo": {"name": "Jarvis", "version": "0.1.0a1"},
            }
        self._counter += 1
        request_id = self._counter
        pending = _Pending(asyncio.get_running_loop().create_future(), effect)
        self._pending[request_id] = pending
        try:
            async with asyncio.timeout(self.request_timeout):
                await self._write({
                    "jsonrpc": "2.0", "id": request_id, "method": method, "params": params,
                }, pending, before_dispatch)
                result = await asyncio.shield(pending.future)
            if "error" in result:
                raise McpError(
                    "remote_error", rpc_code=result["error"]["code"],
                    effect_status=pending.effect_status,
                )
            payload = result["result"]
            result_type = payload.get(
                "resultType", None if self.protocol_version == MODERN_VERSION else "complete",
            )
            if result_type == "input_required":
                raise McpInputRequired(payload, pending.effect_status)
            if result_type != "complete":
                raise McpError("unsupported_result_type", effect_status=pending.effect_status)
            return payload
        except (TimeoutError, asyncio.CancelledError) as error:
            if pending.dispatched:
                with contextlib.suppress(Exception):
                    async with asyncio.timeout(0.2):
                        await self._write({"jsonrpc": "2.0", "method": "notifications/cancelled",
                                           "params": {"requestId": request_id}})
            await self.aclose(retry=False)
            if isinstance(error, asyncio.CancelledError):
                raise McpCancelled(pending.effect_status) from None
            raise McpError("request_timeout", effect_status=pending.effect_status) from None
        except (OSError, ConnectionError, McpError) as error:
            if isinstance(error, McpError) and error.code in {
                "remote_error", "input_required", "unsupported_result_type", "message_limit",
                "approval_revoked", "catalog_changed",
            }:
                if self._failure is not None or self._closing is not None:
                    await self.aclose(retry=False)
                if pending.effect_status == "unknown" and error.effect_status == "not_dispatched":
                    raise McpError(
                        error.code, effect_status="unknown", rpc_code=error.rpc_code,
                    ) from None
                raise
            await self.aclose(retry=False)
            code = error.code if isinstance(error, McpError) else "connection_lost"
            raise McpError(code, effect_status=pending.effect_status) from None
        finally:
            self._pending.pop(request_id, None)
            if not pending.future.done():
                pending.future.cancel()
            elif not pending.future.cancelled():
                pending.future.exception()  # Also consume an error arriving during cancellation.

    async def _read_loop(self) -> None:
        assert self._process and self._process.stdout
        try:
            while True:
                try:
                    line = await self._process.stdout.readline()
                except ValueError:
                    raise McpError("message_limit") from None
                if not line:
                    raise McpError("connection_lost")
                if len(line) > self.max_line_bytes or not line.endswith(b"\n"):
                    raise McpError("message_limit")
                try:
                    message = json.loads(line, object_pairs_hook=_object_pairs)
                    message = _json_data(message)
                except (ValueError, UnicodeError, RecursionError):
                    raise McpError("invalid_json_message") from None
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    raise McpError("invalid_rpc_message")
                if "method" in message:
                    if "result" in message or "error" in message:
                        raise McpError("invalid_rpc_message")
                    method = message["method"]
                    if not isinstance(method, str) or len(method) > 256:
                        raise McpError("invalid_rpc_message")
                    if "id" in message:
                        if self.protocol_version == MODERN_VERSION:
                            # Modern stdio forbids server requests/client responses.
                            raise McpError("unexpected_server_request")
                        request_id = message["id"]
                        if type(request_id) not in {str, int}:
                            raise McpError("invalid_rpc_message")
                        response = {"jsonrpc": "2.0", "id": request_id}
                        if method == "ping":
                            response["result"] = {}
                        else:
                            response["error"] = {"code": -32601, "message": "Method not supported"}
                        async with asyncio.timeout(self.request_timeout):
                            await self._write(response)
                    else:
                        if len(self.notification_methods) >= 1_000:
                            raise McpError("notification_limit")
                        self.notification_methods.append(method)
                        for feature in self._catalog_generations:
                            if method == f"notifications/{feature}/list_changed":
                                self._catalog_generations[feature] += 1
                        if method == "notifications/tools/list_changed":
                            self._tools.clear()
                        elif method == "notifications/resources/list_changed":
                            self._resources.clear()
                        elif method == "notifications/prompts/list_changed":
                            self._prompts.clear()
                    continue
                request_id = message.get("id")
                if type(request_id) is not int or request_id not in self._pending:
                    raise McpError("uncorrelated_response")
                pending = self._pending[request_id]
                if pending.future.done() or ("result" in message) == ("error" in message):
                    raise McpError("invalid_rpc_message")
                if "result" in message and not isinstance(message["result"], dict):
                    raise McpError("invalid_rpc_message")
                if "error" in message and (
                    not isinstance(message["error"], dict)
                    or type(message["error"].get("code")) is not int
                    or not isinstance(message["error"].get("message"), str)
                ):
                    raise McpError("invalid_rpc_message")
                pending.future.set_result(message)
        except asyncio.CancelledError:
            return
        except (McpError, OSError, TimeoutError) as error:
            self._failure = error.code if isinstance(error, McpError) else "connection_lost"
            for pending in self._pending.values():
                if not pending.future.done():
                    pending.future.set_exception(McpError(self._failure))
            if self._closing is None:
                self._begin_close()

    async def _drain_stderr(self) -> None:
        assert self._process and self._process.stderr
        while await self._process.stderr.read(16_384):
            pass  # Drain without retaining arbitrary messages, credentials or owner data.

    def _capability(self, name: str) -> None:
        if name not in self.capabilities:
            raise McpError("capability_unavailable")

    async def _list(self, feature: str, key: str) -> list[dict[str, Any]]:
        self._capability(feature)
        cursor: str | None = None
        seen: set[str] = set()
        entries: list[dict[str, Any]] = []
        total_bytes = 0
        for _ in range(self.max_pages):
            page = await self._request(
                f"{feature}/list", {} if cursor is None else {"cursor": cursor},
            )
            items = page.get(key)
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise McpError("invalid_catalog")
            entries.extend(items)
            total_bytes += len(json.dumps(page))
            if len(entries) > self.max_items or total_bytes > self.max_line_bytes * 2:
                raise McpError("catalog_limit")
            cursor = page.get("nextCursor")
            if cursor is None:
                return entries
            if not isinstance(cursor, str) or not cursor or len(cursor) > 1_024 or cursor in seen:
                raise McpError("invalid_pagination")
            seen.add(cursor)
        raise McpError("pagination_limit")

    async def list_tools(self) -> tuple[McpTool, ...]:
        self._tools.clear()
        self._catalog_generations["tools"] += 1
        generation = self._catalog_generations["tools"]
        entries = await self._list("tools", "tools")
        tools: dict[str, McpTool] = {}
        for item in entries:
            name, schema = item.get("name"), item.get("inputSchema")
            if not isinstance(name, str) or not _NAME.fullmatch(name) or name in tools:
                raise McpError("invalid_tool_name")
            if not isinstance(schema, dict) or schema.get("type") != "object":
                raise McpError("invalid_tool_schema")
            _schema(schema)
            description = item.get("description", "")
            if not isinstance(description, str):
                raise McpError("invalid_tool_description")
            tools[name] = McpTool(name, description, _freeze(schema))
        if generation != self._catalog_generations["tools"]:
            raise McpError("catalog_changed")
        self._tools = tools
        return tuple(tools.values())

    async def call_tool(
        self, tool: str, arguments: Mapping[str, Any], *,
        dispatch_guard: Callable[[], None] | None = None,
    ) -> McpCallResult:
        definition = self._tools.get(tool)
        if definition is None:
            raise McpError("tool_not_listed")
        snapshot = _json_data(arguments)
        if not isinstance(snapshot, dict):
            raise McpError("invalid_tool_arguments")
        _validate(snapshot, _json_data(definition.input_schema))
        if self.authorize is None:
            raise McpError("approval_required")
        authority = self.authorize
        try:
            async with asyncio.timeout(self.request_timeout):
                granted = await authority(tool, _freeze(snapshot))
        except TimeoutError:
            raise McpError("approval_timeout") from None
        if granted is not True:
            raise McpError("approval_denied")
        if self._tools.get(tool) is not definition:
            raise McpError("catalog_changed")

        def before_dispatch() -> None:
            if self.authorize is not authority:
                raise McpError("approval_revoked")
            if self._tools.get(tool) is not definition:
                raise McpError("catalog_changed")
            if dispatch_guard is not None:
                dispatch_guard()

        result = await self._request(
            "tools/call", {"name": tool, "arguments": snapshot}, effect=True,
            before_dispatch=before_dispatch,
        )
        if (
            not isinstance(result.get("content"), list)
            or type(result.get("isError", False)) is not bool
        ):
            raise McpError("invalid_tool_result", effect_status="unknown")
        if any(not isinstance(item, dict) or not isinstance(item.get("type"), str)
               for item in result["content"]):
            raise McpError("invalid_tool_result", effect_status="unknown")
        return McpCallResult(_freeze(result), result.get("isError", False))

    async def list_resources(self) -> tuple[Mapping[str, Any], ...]:
        self._resources.clear()
        self._catalog_generations["resources"] += 1
        generation = self._catalog_generations["resources"]
        entries = await self._list("resources", "resources")
        uris = [item.get("uri") for item in entries]
        if any(not isinstance(uri, str) or not uri or len(uri) > 8_192 for uri in uris):
            raise McpError("invalid_resource")
        if len(set(uris)) != len(uris):
            raise McpError("duplicate_resource")
        if generation != self._catalog_generations["resources"]:
            raise McpError("catalog_changed")
        self._resources = set(uris)
        return tuple(_freeze(item) for item in entries)

    async def read_resource(self, uri: str) -> Mapping[str, Any]:
        if uri not in self._resources:
            raise McpError("resource_not_listed")
        result = await self._request("resources/read", {"uri": uri})
        if not isinstance(result.get("contents"), list):
            raise McpError("invalid_resource_result")
        return _freeze(result)

    async def list_prompts(self) -> tuple[Mapping[str, Any], ...]:
        self._prompts.clear()
        self._catalog_generations["prompts"] += 1
        generation = self._catalog_generations["prompts"]
        entries = await self._list("prompts", "prompts")
        names = [item.get("name") for item in entries]
        if any(not isinstance(name, str) or not _NAME.fullmatch(name) for name in names):
            raise McpError("invalid_prompt")
        if len(set(names)) != len(names):
            raise McpError("duplicate_prompt")
        if generation != self._catalog_generations["prompts"]:
            raise McpError("catalog_changed")
        self._prompts = set(names)
        return tuple(_freeze(item) for item in entries)

    async def get_prompt(
        self, name: str, arguments: Mapping[str, str] | None = None,
    ) -> Mapping[str, Any]:
        if name not in self._prompts:
            raise McpError("prompt_not_listed")
        params = _json_data({} if arguments is None else arguments)
        if not isinstance(params, dict) or any(
            not isinstance(value, str) for value in params.values()
        ):
            raise McpError("invalid_prompt_arguments")
        result = await self._request("prompts/get", {"name": name, "arguments": params})
        if not isinstance(result.get("messages"), list):
            raise McpError("invalid_prompt_result")
        return _freeze(result)

    def _begin_close(self) -> None:
        self._closing = asyncio.create_task(self._close())
        # Reader-initiated cleanup may fail before a host awaits aclose. Consume
        # diagnostics without changing what awaiting this task will raise.
        self._closing.add_done_callback(
            lambda task: None if task.cancelled() else task.exception()
        )

    async def aclose(self, *, retry: bool = True) -> None:
        if self._closing is None:
            self._begin_close()
        elif retry and self._closing.done() and not self._closing.cancelled():
            failure = self._closing.exception()
            if isinstance(failure, McpError) and failure.code == "cleanup_incomplete":
                # A new explicit close retries cleanup only. _closing never
                # becomes None, so start/calls cannot reconnect or replay.
                self._begin_close()
        await asyncio.shield(self._closing)

    @staticmethod
    def _signal_group(process, sig) -> None:
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        except OSError:
            raise McpError("cleanup_incomplete", effect_status="unknown") from None

    @staticmethod
    def _group_absent(process) -> bool:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            raise McpError("cleanup_incomplete", effect_status="unknown") from None
        return False

    async def _settle_group(self, process) -> None:
        # Reaping only the direct child does not prove its inherited process
        # group is empty. ESRCH on the selected group is the sole absence proof.
        if self._group_absent(process):
            return
        for sig in (signal.SIGTERM, signal.SIGKILL):
            self._signal_group(process, sig)
            deadline = asyncio.get_running_loop().time() + self.shutdown_timeout
            while not self._group_absent(process):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.01, remaining))
            else:
                return
        raise McpError("cleanup_incomplete", effect_status="unknown")

    async def _close(self) -> None:
        self._ready = False
        self._tools.clear()
        self._resources.clear()
        self._prompts.clear()
        for pending in tuple(self._pending.values()):
            if not pending.future.done():
                pending.future.set_exception(McpError("client_closed"))
        process = self._process
        if process is None and self._spawn is not None:
            # A close/cancel may arrive while the operating system is still spawning.
            # Own that task until it yields the process; never discard a live child.
            with contextlib.suppress(OSError):
                process = await asyncio.shield(self._spawn)
                self._process = process
        if process is not None:
            if process.stdin:
                process.stdin.close()
            reaped = False
            for sig in (None, signal.SIGTERM, signal.SIGKILL):
                if sig is not None:
                    self._signal_group(process, sig)
                try:
                    async with asyncio.timeout(self.shutdown_timeout):
                        await process.wait()
                    reaped = True
                    break
                except TimeoutError:
                    continue
            if not reaped:
                raise McpError("cleanup_incomplete", effect_status="unknown")
            await self._settle_group(process)
        readers = tuple(task for task in (self._reader, self._stderr) if task is not None)
        for task in readers:
            if not task.done():
                task.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        if self._home is not None:
            try:
                shutil.rmtree(self._home)
            except FileNotFoundError:
                pass
            except OSError:
                raise McpError("cleanup_incomplete", effect_status="unknown") from None
