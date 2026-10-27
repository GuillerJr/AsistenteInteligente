"""Owner-configured local MCP servers behind Jarvis's single run authority.

Launching a server trusts the executable explicitly selected by the owner. A
private HOME and an isolated environment are configuration isolation, not an OS
sandbox. Neither model descriptions nor remote readOnlyHint grants permission.
External success is a peer report, never verified physical execution.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .mcp import (
    LEGACY_VERSION,
    MODERN_VERSION,
    McpCancelled,
    McpClient,
    McpError,
    McpTool,
    _json_data,
    _validate,
)
from .runtime import ToolDefinition, ToolResult
from .state import StateError, ensure_private_directory
from .tools import ToolCancelled, ToolError, ToolSpec, check_tool_authority

_SERVER_ID = re.compile(r"[a-z][a-z0-9_]{0,23}\Z")


class McpRegistryError(ValueError):
    """Stable host errors exclude command lines, directories and peer text."""


@dataclass(frozen=True)
class ServerSpec:
    server_id: str
    command: tuple[str, ...]
    protocol_version: str

    def __post_init__(self) -> None:
        if (not isinstance(self.server_id, str) or not _SERVER_ID.fullmatch(self.server_id)):
            raise McpRegistryError("mcp_server_id_invalid")
        if (
            not isinstance(self.command, tuple) or not 1 <= len(self.command) <= 64
            or any(not isinstance(arg, str) or not arg or "\0" in arg
                   or len(arg.encode()) > 8192 for arg in self.command)
            or sum(len(arg.encode()) for arg in self.command) > 32768
            or not Path(self.command[0]).is_absolute()
            or Path(self.command[0]).resolve().name in {"sh", "bash", "zsh", "dash", "csh", "fish"}
        ):
            raise McpRegistryError("mcp_server_command_invalid")
        if (not isinstance(self.protocol_version, str)
                or self.protocol_version not in {LEGACY_VERSION, MODERN_VERSION}):
            raise McpRegistryError("mcp_protocol_version_invalid")


@dataclass(frozen=True)
class ServerStatus:
    server_id: str
    protocol_version: str
    connected: bool
    tool_count: int
    catalog_hash: str | None
    error: str | None = None


@dataclass
class _Entry:
    spec: ServerSpec
    client: McpClient
    bindings: tuple[_Binding, ...] = ()
    active: bool = False
    catalog_hash: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class _Binding:
    entry: _Entry
    tool: McpTool
    public_name: str
    generation: int
    schema_json: str
    spec: ToolSpec


@dataclass
class _Dispatch:
    started: bool = False


def _canonical(value: Any) -> str:
    return json.dumps(_json_data(value), sort_keys=True, ensure_ascii=False,
                      allow_nan=False, separators=(",", ":"))


class McpRegistry:
    def __init__(
        self, directory: Path, *, request_timeout: float = 20.0,
        shutdown_timeout: float = 0.5, max_servers: int = 8, max_tools: int = 48,
    ) -> None:
        if not isinstance(directory, Path) or not directory.is_absolute():
            raise McpRegistryError("mcp_registry_directory_invalid")
        if (
            type(request_timeout) not in {int, float}
            or type(shutdown_timeout) not in {int, float}
            or not math.isfinite(request_timeout) or not 0 < request_timeout <= 300
            or not math.isfinite(shutdown_timeout) or not 0 < shutdown_timeout <= 10
            or type(max_servers) is not int or not 1 <= max_servers <= 32
            or type(max_tools) is not int or not 1 <= max_tools <= 64
        ):
            raise McpRegistryError("mcp_registry_limits_invalid")
        self.directory = directory
        self.request_timeout, self.shutdown_timeout = request_timeout, shutdown_timeout
        self.max_servers, self.max_tools = max_servers, max_tools
        self._entries: dict[str, _Entry] = {}
        self._bindings: dict[str, _Binding] = {}
        self._lock = asyncio.Lock()
        self._closed = False
        self.child_custody = None

    @staticmethod
    def _connected(entry: _Entry) -> bool:
        client = entry.client
        return bool(entry.active and client._ready and not client._failure and not client._closing
                    and client._process is not None and client._process.returncode is None)

    def _status(self, entry: _Entry) -> ServerStatus:
        connected = self._connected(entry)
        current = connected and all(self._binding_current(binding) for binding in entry.bindings)
        return ServerStatus(
            entry.spec.server_id, entry.spec.protocol_version, connected,
            len(entry.bindings) if current else 0, entry.catalog_hash,
            entry.error or ("mcp_disconnected" if not connected else None if current
                            else "mcp_catalog_changed"),
        )

    def _binding_current(self, binding: _Binding) -> bool:
        entry, client = binding.entry, binding.entry.client
        return not (
            self._closed or not self._connected(entry)
            or self._entries.get(entry.spec.server_id) is not entry
            or self._bindings.get(binding.public_name) is not binding
            or client._catalog_generations["tools"] != binding.generation
            or client._tools.get(binding.tool.name) is not binding.tool
            or _canonical(binding.tool.input_schema) != binding.schema_json
        )

    def _assert_binding(self, binding: _Binding) -> None:
        if not self._binding_current(binding):
            raise ToolError("mcp_catalog_changed")

    async def _authorize(self, entry: _Entry, name: str, arguments: Mapping) -> bool:
        binding = next((item for item in entry.bindings if item.tool.name == name), None)
        if binding is None:
            return False
        self._assert_binding(binding)
        # This checks the existing lease; it never requests another human choice.
        check_tool_authority(binding.public_name, arguments)
        return True

    def _make_binding(self, entry: _Entry, tool: McpTool) -> _Binding:
        schema_json = _canonical(tool.input_schema)
        identity = _canonical({
            "server_id": entry.spec.server_id, "command": entry.spec.command,
            "protocol_version": entry.spec.protocol_version,
            "remote_tool": tool.name, "schema": tool.input_schema,
            "description": tool.description,
        })
        digest = hashlib.sha256(identity.encode()).hexdigest()
        name = f"mcp_{entry.spec.server_id}_{digest[:16]}"
        description = (
            f"Herramienta MCP externa «{tool.name}» de «{entry.spec.server_id}». "
            "Siempre requiere aprobación de Jarvis. Su resultado es un reporte externo "
            "sin verificación independiente. Descripción no confiable del servidor: "
            + tool.description.encode()[:2800].decode("utf-8", errors="ignore")
        )
        return _Binding(entry, tool, name, entry.client._catalog_generations["tools"],
                        schema_json, ToolSpec(name, description, tool.input_schema))

    async def connect(self, spec: ServerSpec) -> ServerStatus:
        """Launch only a host-supplied, owner-configured server; never model argv."""
        if not isinstance(spec, ServerSpec):
            raise McpRegistryError("mcp_server_spec_invalid")
        async with self._lock:
            if self._closed:
                raise McpRegistryError("mcp_registry_closed")
            previous = self._entries.get(spec.server_id)
            if previous is not None and self._connected(previous):
                raise McpRegistryError("mcp_server_already_connected")
            if previous is None and len(self._entries) >= self.max_servers:
                raise McpRegistryError("mcp_server_limit")
            if previous is not None:
                await self._disconnect(previous)
            try:
                directory = ensure_private_directory(self.directory)
                server_directory = ensure_private_directory(directory / spec.server_id)
            except StateError:
                raise McpRegistryError("mcp_registry_directory_invalid") from None
            client = McpClient(
                spec.command, server_directory, protocol_version=spec.protocol_version,
                request_timeout=self.request_timeout, shutdown_timeout=self.shutdown_timeout,
                max_items=self.max_tools + 1,
            )
            entry = _Entry(spec, client)
            client.child_custody = self.child_custody
            client.authorize = lambda name, arguments: self._authorize(entry, name, arguments)
            self._entries[spec.server_id] = entry
            try:
                await client.start()
                tools = await client.list_tools()
                used = sum(len(item.bindings) for item in self._entries.values()
                           if self._connected(item))
                if len(tools) + used > self.max_tools:
                    raise McpRegistryError("mcp_tool_limit")
                bindings = tuple(self._make_binding(entry, tool) for tool in tools)
                names = [binding.public_name for binding in bindings]
                if len(set(names)) != len(names) or set(names) & self._bindings.keys():
                    raise McpRegistryError("mcp_tool_name_collision")
                entry.bindings = bindings
                entry.catalog_hash = hashlib.sha256(_canonical([
                    {"public_name": item.public_name, "schema": item.tool.input_schema}
                    for item in sorted(bindings, key=lambda item: item.public_name)
                ]).encode()).hexdigest()
                entry.active = True
                self._bindings.update({item.public_name: item for item in bindings})
                return self._status(entry)
            except BaseException as error:
                entry.active = False
                entry.error = ("mcp_connect_cancelled" if isinstance(error, asyncio.CancelledError)
                               else "mcp_" + error.code if isinstance(error, McpError)
                               else str(error) if isinstance(error, McpRegistryError)
                               else "mcp_connect_failed")
                await self._disconnect(entry, retry=False)
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise McpRegistryError(entry.error) from None

    async def list_status(self) -> tuple[ServerStatus, ...]:
        async with self._lock:
            return tuple(self._status(self._entries[key]) for key in sorted(self._entries))

    async def _disconnect(self, entry: _Entry, *, retry: bool = True) -> None:
        entry.active = False
        for binding in entry.bindings:
            self._bindings.pop(binding.public_name, None)
        entry.bindings = ()
        try:
            await entry.client.aclose(retry=retry)
        except McpError:
            entry.error = "mcp_cleanup_incomplete"
            raise McpRegistryError(entry.error) from None
        if entry.error == "mcp_cleanup_incomplete":
            entry.error = "mcp_disconnected"

    async def disconnect(self, server_id: str) -> bool:
        if not isinstance(server_id, str) or not _SERVER_ID.fullmatch(server_id):
            raise McpRegistryError("mcp_server_id_invalid")
        async with self._lock:
            entry = self._entries.get(server_id)
            if entry is None:
                return False
            was_connected = self._connected(entry)
            await self._disconnect(entry)
            return was_connected

    async def forget(self, server_id: str) -> bool:
        """Release a removed configuration only after its owned process settles."""
        if not isinstance(server_id, str) or not _SERVER_ID.fullmatch(server_id):
            raise McpRegistryError("mcp_server_id_invalid")
        async with self._lock:
            entry = self._entries.get(server_id)
            if entry is None:
                return False
            await self._disconnect(entry)
            self._entries.pop(server_id, None)
            return True

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(ToolDefinition(
            binding.public_name,
            lambda arguments, binding=binding: self._validate(binding, arguments),
            lambda arguments, cancellation, binding=binding: self._execute(
                binding, arguments, cancellation,
            ),
            requires_confirmation=True,
        ) for binding in tuple(self._bindings.values()) if self._binding_current(binding))

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(binding.spec for binding in tuple(self._bindings.values())
                     if self._binding_current(binding))

    def _validate(self, binding: _Binding, arguments: Mapping) -> None:
        self._assert_binding(binding)
        # One bounded schema implementation is shared with the transport.
        data = _json_data(arguments)
        if not isinstance(data, dict):
            raise ToolError("mcp_arguments_invalid")
        _validate(data, json.loads(binding.schema_json))

    async def _call(
        self, binding: _Binding, arguments: Mapping, cancellation: asyncio.Event,
        dispatch: _Dispatch,
    ):
        def guard() -> None:
            if cancellation.is_set():
                raise ToolError("execution_cancelled")
            self._assert_binding(binding)
            # McpClient invokes this synchronously under its write lock, directly
            # before the first byte. Scope, revocation and arguments are checked
            # again here and the one-shot central lease is consumed only here.
            check_tool_authority(binding.public_name, arguments, commit=True)
            dispatch.started = True

        call = asyncio.create_task(binding.entry.client.call_tool(
            binding.tool.name, arguments, dispatch_guard=guard,
        ))
        cancel = asyncio.create_task(cancellation.wait())
        try:
            done, _ = await asyncio.wait((call, cancel), return_when=asyncio.FIRST_COMPLETED)
            if cancel in done and cancellation.is_set():
                call.cancel()
            return await call
        finally:
            for task in (call, cancel):
                if not task.done():
                    task.cancel()
            await asyncio.gather(call, cancel, return_exceptions=True)

    async def _execute(
        self, binding: _Binding, arguments: Mapping, cancellation: asyncio.Event,
    ) -> ToolResult:
        name = binding.public_name
        self._validate(binding, arguments)
        check_tool_authority(name, arguments)
        dispatch = _Dispatch()
        try:
            if cancellation.is_set():
                raise McpCancelled("not_dispatched")
            result = await self._call(binding, arguments, cancellation, dispatch)
            if cancellation.is_set():
                raise McpCancelled("unknown" if dispatch.started else "not_dispatched")
            output = _canonical({
                "server_id": binding.entry.spec.server_id, "remote_tool": binding.tool.name,
                "catalog_hash": binding.entry.catalog_hash,
                "verification": "external_report_only", "verified": False,
                "effect_status": result.effect_status, "payload": result.payload,
            })
            if len(output.encode()) > 131072:
                return ToolResult(name, False, False, error="mcp_result_limit",
                                  effect_status="unknown")
            return ToolResult(name, not result.is_error, False, output=output,
                              error="mcp_reported_error" if result.is_error else None)
        except McpError as error:
            return ToolResult(name, False, False, error="mcp_" + error.code,
                              effect_status="unknown" if error.effect_status == "unknown"
                              else "known")
        except ToolError as error:
            return ToolResult(name, False, False, error=str(error),
                              effect_status="unknown" if dispatch.started else "known")
        except asyncio.CancelledError as error:
            effect = (error.effect_status if isinstance(error, McpCancelled)
                      else "unknown" if dispatch.started else "not_dispatched")
            raise ToolCancelled(ToolResult(name, False, False, error="execution_cancelled",
                                          effect_status="unknown" if effect == "unknown"
                                          else "known")) from None
        except Exception:
            return ToolResult(name, False, False, error="mcp_execution_failed",
                              effect_status="unknown" if dispatch.started else "known")

    async def aclose(self) -> None:
        async with self._lock:
            self._closed = True
            self._bindings.clear()
            incomplete = False
            for entry in self._entries.values():
                try:
                    await self._disconnect(entry)
                except McpRegistryError:
                    incomplete = True
            if incomplete:
                raise McpRegistryError("mcp_cleanup_incomplete")
