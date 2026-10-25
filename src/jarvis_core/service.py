"""Local Jarvis coordinator and explicit development service entry point.

This connects actual IPC, SQLite, execution lifecycle and the official Codex
client. It is the first vertical baseline slice, not the complete OpenClaw parity
target. No legacy owner state, tools, credentials or installed app are imported.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import signal
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from pathlib import Path

from jarvis_core.bridge import CodexBridge
from jarvis_core.codex import CodexClient, CodexError
from jarvis_core.config import ConfigError, ProfileConfig
from jarvis_core.host_profile import (
    READ_RPCS,
    BoundProfileResource,
    HostProfileError,
    ProfileLease,
    ServiceProfileGate,
    profile_background,
    profile_callback,
    service_blockers,
    service_fingerprint,
)
from jarvis_core.maintenance import MaintenanceCoordinator
from jarvis_core.management import ManagementAPI
from jarvis_core.mcp_registry import McpRegistry, McpRegistryError, ServerSpec
from jarvis_core.memory import Citation, MemoryError, MemoryLimits, MemoryScope, MemoryStore
from jarvis_core.memory_tools import MemoryTools
from jarvis_core.private_json import PrivateJsonError
from jarvis_core.processes import ProcessTools
from jarvis_core.profiles import ProfileError, ProfilesStore
from jarvis_core.protocol import LocalRpcServer, RpcError, TransportSecurityError
from jarvis_core.runtime import AgentRuntime, RunContext, RunHandle, ToolResult
from jarvis_core.scheduler import Scheduler, SchedulerError, SchedulerLimits, ScheduleStore
from jarvis_core.skills import SkillError, SkillsStore
from jarvis_core.state import (
    SessionRecord,
    SessionRef,
    StateError,
    StateLimits,
    StateStore,
    ensure_private_directory,
)
from jarvis_core.telegram import TelegramError
from jarvis_core.telegram_management import TelegramManagementAPI
from jarvis_core.tools import ApprovalAuthority, ToolCatalog, ToolError
from jarvis_core.web import WebFetchTools
from jarvis_core.workspace import WorkspaceError, WorkspaceTools

_BASE = {"session_id", "temporary"}
_TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


def _params(params: dict, allowed: set[str], required: set[str] | None = None) -> None:
    if not isinstance(params, dict) or set(params) - allowed or (required or set()) - set(params):
        raise RpcError(-32602, "Los parámetros de la solicitud no son válidos.")


def _session(record: SessionRecord) -> dict:
    return {
        "session_id": record.ref.session_id,
        "temporary": record.ref.temporary,
        "title": record.title,
        "archived": record.archived,
        "agent_id": record.agent_id,
        "parent_session_id": record.parent_session_id,
    }


class _ProviderAdapter:
    def __init__(self, service: JarvisService) -> None:
        self.service = service
        self.bridge = CodexBridge(
            service.provider,
            prepare=service._prepare_turn,
            on_event=service._event,
            on_thread=service._bind_thread,
            on_turn_closed=service._provider_turn_closed,
        )

    async def next_step(self, context: RunContext):
        service = self.service
        service._active_ref.set(service._refs[context.run_id])
        try:
            return await self.bridge.next_step(context)
        except (
            CodexError,
            ConfigError,
            WorkspaceError,
            ProfileError,
            SkillError,
            PrivateJsonError,
        ) as error:
            service._errors[context.run_id] = str(error)
            raise

    async def cleanup(self, context: RunContext) -> None:
        try:
            await self.bridge.cleanup(context)
        finally:
            self.service._active_ref.set(None)


class _ApprovalGate:
    def __init__(self, service: JarvisService) -> None:
        self.service = service

    async def request(self, operation, cancellation):
        with self.service.profile_gate.admit("run.approval_wait", mutating=True, closing=True):
            return await self._request(operation, cancellation)

    async def _request(self, operation, cancellation):
        ref = self.service._refs[operation.run_id]
        self.service.store.set_run_status(ref, operation.run_id, "waiting_approval")
        try:
            return await self.service.authority.request(operation, cancellation)
        finally:
            record = self.service.store.get_run(ref, operation.run_id)
            if record.status == "waiting_approval" and not cancellation.is_set():
                self.service.store.set_run_status(ref, operation.run_id, "running")


class JarvisService(TelegramManagementAPI, ManagementAPI):
    def __init__(self, directory: Path, provider: CodexClient, *,
                 profile_lease: ProfileLease | None = None) -> None:
        self.profile_lease = profile_lease or ProfileLease.acquire(directory)
        self.profile_lease.assert_current()
        if self.profile_lease.root != directory:
            raise HostProfileError("profile_directory_mismatch")
        try:
            self.profile_lease.check_bootstrap_ready()
            self._initialize(directory, provider)
        except BaseException:
            for name in ("schedule_store", "memory", "store"):
                resource = getattr(self, name, None)
                if resource is not None:
                    resource.close()
            self.profile_lease.close()
            raise

    def _initialize(self, directory, provider):
        self.directory = directory
        self.maintenance_coordinator = None
        self._run_writers = {}
        self.profile_gate = ServiceProfileGate(
            self.profile_lease, blockers=lambda: service_blockers(self),
            fingerprint=lambda: service_fingerprint(self),
        )
        self.profile_gate.service = self
        self.store = StateStore(
            directory / "jarvis.sqlite3", limits=StateLimits(max_json_bytes=512000)
        )
        self.provider = provider
        self._handles: dict[str, RunHandle] = {}
        self._refs: dict[str, SessionRef] = {}
        self._errors: dict[str, str] = {}
        self._observers: set[asyncio.Task] = set()
        self._settlers: dict[str, asyncio.Task] = {}
        self._closing = False
        self._maintenance = False
        self._close_task: asyncio.Task | None = None
        self._retiring_sessions: set[SessionRef] = set()
        self._run_fingerprints: dict[str, str] = {}
        self.process_tools = None
        self._active_ref: ContextVar[SessionRef | None] = ContextVar(
            "jarvis_run_scope", default=None
        )
        self.config = ProfileConfig(directory)
        self.agent_profiles = ProfilesStore(directory / "profiles")
        self.skills = SkillsStore(directory / "profiles")
        self.schedule_store = ScheduleStore(
            directory / "schedules.sqlite3",
            limits=SchedulerLimits(max_schedules=32, max_prompt_bytes=8000),
        )
        self.scheduler = Scheduler(self.schedule_store, self._dispatch_schedule, close_timeout=10)
        self.authority = ApprovalAuthority()
        self.mcp = McpRegistry(directory / "mcp")
        self.memory = MemoryStore(
            directory / "memory.sqlite3", limits=MemoryLimits(max_document_bytes=16000)
        )
        for name in ("store", "config", "agent_profiles", "skills", "schedule_store", "memory"):
            setattr(self, name, BoundProfileResource(
                getattr(self, name), self.profile_lease, self.profile_gate,
            ))
        self.scheduler.store = self.schedule_store
        self.web_tools = WebFetchTools()
        self._volatile_bindings: dict[str, str] = {}
        self._temporary_memory_scopes: dict[str, set[MemoryScope]] = {}
        self._configured_workspace = self.config.workspace
        self._workspace_path = (
            Path(self._configured_workspace)
            if self._configured_workspace
            else (ensure_private_directory(directory / "workspace"))
        )
        self._configure_tools()
        self.initialize_telegram()
        self.maintenance_coordinator = MaintenanceCoordinator(self, self.profile_gate)
        self.server = LocalRpcServer(directory, self.handlers(), max_frame_bytes=1024 * 1024)

    def _configure_tools(self) -> None:
        self.workspace_tools = None
        self._workspace_error = None
        try:
            candidate = WorkspaceTools(self._workspace_path)
            identity = candidate.identity
            if (
                self._configured_workspace
                and self.config.workspace_identity is not None
                and self.config.workspace_identity != identity
            ):
                raise WorkspaceError("workspace_identity_changed")
            self.workspace_tools = candidate
        except (WorkspaceError, OSError) as error:
            self._workspace_error = (
                str(error) if isinstance(error, WorkspaceError) else ("workspace_unavailable")
            )
            identity = self.config.workspace_identity or (0, 0)
        self._workspace_id = hashlib.sha256(
            json.dumps(
                [
                    str(self._workspace_path),
                    *identity,
                ]
            ).encode()
        ).hexdigest()
        memory_tools = MemoryTools(self.memory, self._active_memory_scope)
        definitions = list(memory_tools.definitions())
        specs = list(memory_tools.specs())
        definitions.extend(self.web_tools.definitions())
        specs.extend(self.web_tools.specs())
        if self.workspace_tools:
            definitions.extend(self.workspace_tools.definitions())
            specs.extend(self.workspace_tools.specs())
            if self.process_tools is None:
                self.process_tools = ProcessTools(
                    self.workspace_tools, lambda: self._active_process_scope()
                )
            definitions.extend(self.process_tools.definitions())
            specs.extend(self.process_tools.specs())
        definitions.extend(self.mcp.definitions())
        specs.extend(self.mcp.specs())
        self._mcp_signature = tuple(spec.name for spec in self.mcp.specs())
        self.catalog = ToolCatalog(definitions, specs, authority=self.authority)
        self._catalog_fingerprint = hashlib.sha256(
            json.dumps(
                [self._workspace_id, self.catalog.specs()],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        adapter = _ProviderAdapter(self)
        self._adapter = adapter
        self.runtime = AgentRuntime(
            adapter,
            self.catalog.definitions(),
            approval_gate=_ApprovalGate(self),
            max_steps=32,
            cleanup=adapter.cleanup,
            execution_scope=self._execution_scope,
            on_tool_result=self._tool_result,
        )

    def _memory_scope(self, ref: SessionRef) -> MemoryScope:
        scope = MemoryScope(
            self.store.get_session(ref).agent_id,
            self._workspace_id,
            ref.session_id if ref.temporary else None,
            ref.temporary,
        )
        if ref.temporary:
            self._temporary_memory_scopes.setdefault(ref.session_id, set()).add(scope)
        return scope

    def _provider_turn_closed(self, context: RunContext) -> None:
        handle = self._handles.get(context.run_id)
        if handle is not None:
            self._errors[context.run_id] = "provider_turn_closed"
            handle.cancel()

    @contextmanager
    def _execution_scope(self, operation, grant):
        with self.profile_gate.admit("run.effect", mutating=True, closing=True):
            self._adapter.bridge.assert_can_execute(operation.run_id)
            with self.authority.execution_scope(operation, grant):
                yield

    def _active_memory_scope(self) -> MemoryScope:
        ref = self._active_ref.get()
        if ref is None:
            raise ValueError("memory_run_scope_required")
        return self._memory_scope(ref)

    def _active_process_scope(self) -> str:
        ref = self._active_ref.get()
        if ref is None:
            raise ValueError("process_run_scope_required")
        self.store.get_session(ref)
        return ref.session_id

    @profile_callback("run.event")
    def _event(self, context: RunContext, kind: str, payload: dict) -> None:
        self.store.append_event(self._refs[context.run_id], context.run_id, kind, payload)

    @staticmethod
    def _receipt(result: ToolResult) -> dict:
        raw = result.output.encode()
        return {
            **asdict(result),
            "output": raw[:512].decode("utf-8", errors="ignore"),
            "output_sha256": hashlib.sha256(raw).hexdigest(),
            "output_bytes": len(raw),
            "output_truncated": len(raw) > 512,
        }

    def _tool_result(self, context: RunContext, result: ToolResult) -> None:
        self._event(context, "tool.completed", {"receipt": self._receipt(result)})

    @profile_callback("run.prepare")
    def _prepare_turn(self, context: RunContext) -> dict:
        ref = self._refs[context.run_id]
        if self.config.workspace != self._configured_workspace:
            raise ConfigError("workspace_configuration_changed_restart")
        self._assert_workspace_current()
        self.store.set_run_status(ref, context.run_id, "running")
        session = self.store.get_session(ref)
        profile = self.agent_profiles.get(session.agent_id)
        skills = self.skills.context(session.agent_id)
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    self._catalog_fingerprint,
                    profile.fingerprint,
                    skills.fingerprint,
                ]
            ).encode()
        ).hexdigest()
        self._run_fingerprints[context.run_id] = fingerprint
        matches = (
            self._volatile_bindings.get(ref.session_id) == fingerprint
            if ref.temporary
            else self.config.binding_matches(
                ref.session_id,
                fingerprint,
            )
        )
        thread_id = session.provider_thread_id if matches else None
        history = []
        if thread_id is None:
            # Only our own session, with bounded recent context. Never thread/list
            # or external owner history; failed user requests are not replayed.
            rows = self.store.list_recent_messages(ref, limit=99 if skills.text else 100)
            budget = 16000
            for row in reversed(rows):
                if row.run_id == context.run_id:
                    continue
                if row.run_id and self.store.get_run(ref, row.run_id).status != "completed":
                    continue
                if row.role not in {"user", "assistant"}:
                    continue
                raw = row.text.encode()
                if len(raw) > budget:
                    break
                budget -= len(raw)
                history.append(
                    {
                        "type": "message",
                        "role": row.role,
                        "content": [
                            {
                                "type": "input_text" if row.role == "user" else "output_text",
                                "text": row.text,
                            }
                        ],
                    }
                )
            history.reverse()
            if history:
                self._event(
                    context, "context.restored", {"messages": len(history), "budget_bytes": 16000}
                )
        return {
            "thread_id": thread_id,
            "temporary": ref.temporary,
            "tool_specs": self.catalog.specs(),
            "history": history,
            "instructions": profile.instructions,
            "context_data": skills.text,
        }

    def _assert_workspace_current(self) -> None:
        current = WorkspaceTools(self._workspace_path)
        if self.workspace_tools is None or current.identity != self.workspace_tools.identity:
            raise WorkspaceError("workspace_identity_changed")

    @profile_callback("run.bind")
    def _bind_thread(self, context: RunContext, thread_id: str) -> None:
        ref = self._refs[context.run_id]
        self.store.set_provider_thread(ref, thread_id)
        if ref.temporary:
            self._volatile_bindings[ref.session_id] = self._run_fingerprints[context.run_id]
        else:
            self.config.bind(ref.session_id, self._run_fingerprints[context.run_id])

    def handlers(self) -> dict[str, Callable[[dict], Awaitable[dict]]]:
        methods = {
            "service.status": self.status,
            "provider.login": self.login,
            "provider.logout": self.logout,
            "sessions.create": self.create_session,
            "sessions.list": self.list_sessions,
            "sessions.update": self.update_session,
            "sessions.delete": self.delete_session,
            "sessions.messages": self.messages,
            "runs.start": self.start_run,
            "runs.get": self.get_run,
            "runs.cancel": self.cancel_run,
            "events.poll": self.events,
            "runs.list": self.list_runs,
            "tools.list": self.list_tools,
            "workspace.get": self.get_workspace,
            "workspace.set": self.set_workspace,
            "approvals.list": self.list_approvals,
            "approvals.decide": self.decide_approval,
            "approvals.revoke": self.revoke_approvals,
            "memory.list": self.list_memory,
            "memory.search": self.search_memory,
            "memory.resolve": self.resolve_memory,
            "mcp.list": self.list_mcp,
            "mcp.add": self.add_mcp,
            "mcp.connect": self.connect_mcp,
            "mcp.disconnect": self.disconnect_mcp,
            "mcp.remove": self.remove_mcp,
        }
        methods.update(self.management_handlers())
        methods.update(self.telegram_handlers())

        def safe(name, callback):
            async def invoke(params):
                try:
                    with self.profile_gate.admit(name, mutating=name not in READ_RPCS):
                        return await callback(params)
                except HostProfileError as error:
                    raise RpcError(-32037, error.code) from None
                except StateError as error:
                    raise RpcError(-32010, error.code) from None
                except CodexError as error:
                    raise RpcError(-32020, str(error)) from None
                except TelegramError as error:
                    raise RpcError(-32036, error.code) from None
                except (ProfileError, SkillError, PrivateJsonError, SchedulerError) as error:
                    raise RpcError(-32035, str(error)) from None
                except (ConfigError, WorkspaceError, ToolError, McpRegistryError) as error:
                    message = (
                        "La configuración pudo cambiar, pero no se confirmó su guardado. "
                        "Reinicia Jarvis antes de ejecutar herramientas."
                        if "uncertain" in str(error)
                        else "No se pudo aplicar la configuración o decisión de herramienta."
                    )
                    raise RpcError(-32030, message) from None
                except MemoryError as error:
                    message = (
                        "La cita ya no coincide con la memoria actual. Actualiza la búsqueda."
                        if "citation" in error.code or "generation" in error.code
                        else "No se pudo consultar la memoria local de esta carpeta."
                    )
                    raise RpcError(-32031, message) from None

            return invoke

        handlers = {name: safe(name, callback) for name, callback in methods.items()}
        # These fixed native controls own their admission and background jobs.
        # Status/decision/cancel must remain available while a cut excludes every
        # pre-existing handler. They cannot expose a model tool or touch stores.
        if self.maintenance_coordinator is not None:
            handlers.update(self.maintenance_coordinator.handlers())
        return handlers

    def _ref(self, params: dict) -> SessionRef:
        # Mode is validated against a real session. No client metadata is trusted
        # as a replacement for that lookup or for a durable provider binding.
        ref = SessionRef(params.get("session_id"), params.get("temporary", False))
        self.store.get_session(ref)
        return ref

    def _busy(self, ref: SessionRef) -> bool:
        return ref in self._retiring_sessions or any(value == ref for value in self._refs.values())

    async def status(self, params: dict) -> dict:
        _params(params, set())
        provider = await self.provider.account_status()
        return {
            "ready": not self._closing,
            "provider": provider,
            "capabilities": {
                "chat": True,
                "sessions": True,
                "temporary_sessions": True,
                "streaming": True,
                "cancellation": True,
                "tools": True,
                "memory": True,
                "memory_mode": "local_lexical",
                "mcp": True,
                "agents": True,
                "skills": True,
                "schedules": True,
                "telegram": True,
                "web_fetch": True,
                "processes": self.process_tools.status()
                if self.process_tools
                else {"available": False},
                "baseline_complete": False,
            },
        }

    async def list_tools(self, params: dict) -> dict:
        _params(params, set())
        if not self._handles and not self._maintenance:
            self._refresh_mcp_catalog()
        definitions = {tool.name: tool for tool in self.catalog.definitions()}
        return {
            "tools": [
                {
                    "name": spec["name"],
                    "description": spec["description"],
                    "input_schema": spec["inputSchema"],
                    "requires_confirmation": definitions[spec["name"]].requires_confirmation,
                }
                for spec in self.catalog.specs()
            ]
        }

    def _workspace_value(self) -> dict:
        return {
            "path": str(self._workspace_path),
            "available": self.workspace_tools is not None,
            "private_default": self._configured_workspace is None,
            "error": (
                "La carpeta no está disponible o cambió de identidad. Selecciónala de nuevo."
                if self._workspace_error
                else None
            ),
        }

    async def get_workspace(self, params: dict) -> dict:
        _params(params, set())
        return {"workspace": self._workspace_value()}

    async def set_workspace(self, params: dict) -> dict:
        _params(params, {"path"}, {"path"})
        if (
            self._handles
            or self._maintenance
            or self._closing
            or self._retiring_sessions
            or (self.process_tools and self.process_tools.has_running)
            or self.telegram_blocks_workspace_change()
        ):
            raise RpcError(
                -32011, "Espera a que terminen las ejecuciones antes de cambiar de carpeta."
            )
        value = params["path"]
        if not isinstance(value, str) or len(value.encode()) > 4096:
            raise RpcError(-32602, "La ruta de la carpeta no es válida.")
        path = Path(value)
        if (
            path == self.directory
            or self.directory.is_relative_to(path)
            or (path.is_relative_to(self.directory) and path != self.directory / "workspace")
        ):
            raise RpcError(
                -32602, "El perfil privado de Jarvis no puede usarse como carpeta de trabajo."
            )
        candidate = WorkspaceTools(path)
        try:
            self.config.set_workspace(path, candidate.identity)
        except ConfigError as error:
            if "uncertain" in str(error):
                self._closing = True
            raise
        self._maintenance = True
        try:
            if self.process_tools:
                await self.process_tools.aclose()
                self.process_tools = None
            self._workspace_path = path
            self._configured_workspace = str(path)
            self._volatile_bindings.clear()
            self._configure_tools()
        except BaseException:
            self._closing = True
            raise
        finally:
            self._maintenance = False
        return {"workspace": self._workspace_value()}

    def _refresh_mcp_catalog(self) -> None:
        # list_changed and process exit invalidate bindings synchronously. Never
        # replace the executor beneath an active run; its dispatch guard fails
        # closed until the next idle run can acquire the current catalog.
        if tuple(spec.name for spec in self.mcp.specs()) != self._mcp_signature:
            self._configure_tools()

    @asynccontextmanager
    async def _mcp_maintenance(self):
        # No await before setting the flag: no run or second mutation can slip
        # between the active-run check and the start of process maintenance.
        if self._closing or self._maintenance or self._handles:
            raise RpcError(-32011, "Espera a que termine la ejecución o conexión actual.")
        self._maintenance = True
        try:
            yield
        finally:
            self._configure_tools()
            self._maintenance = False

    async def list_mcp(self, params: dict) -> dict:
        _params(params, set())
        statuses = {item.server_id: asdict(item) for item in await self.mcp.list_status()}
        return {
            "servers": [
                {
                    "connected": False,
                    "tool_count": 0,
                    "catalog_hash": None,
                    "error": None,
                    **statuses.get(spec["server_id"], {}),
                    **spec,
                    "configured": True,
                }
                for spec in self.config.server_specs
            ],
            "supported_protocols": ["2025-11-25", "2026-07-28"],
        }

    @staticmethod
    def _server_spec(params: dict) -> ServerSpec:
        if not isinstance(params.get("command"), list):
            raise RpcError(-32602, "El comando MCP debe ser una lista de programa y argumentos.")
        return ServerSpec(params["server_id"], tuple(params["command"]), params["protocol_version"])

    def _write_configuration(self, callback) -> None:
        try:
            callback()
        except ConfigError as error:
            if "uncertain" in str(error):
                self._closing = True
            raise

    async def add_mcp(self, params: dict) -> dict:
        _params(
            params,
            {"server_id", "command", "protocol_version"},
            {"server_id", "command", "protocol_version"},
        )
        spec = self._server_spec(params)
        async with self._mcp_maintenance():
            if any(item["server_id"] == spec.server_id for item in self.config.server_specs):
                raise RpcError(-32602, "Ese identificador MCP ya está configurado.")
            self._write_configuration(
                lambda: self.config.set_server(spec.server_id, spec.command, spec.protocol_version)
            )
        return {"configured": True}

    async def connect_mcp(self, params: dict) -> dict:
        _params(
            params,
            {"server_id", "command", "protocol_version"},
            {"server_id", "command", "protocol_version"},
        )
        expected = self._server_spec(params)
        async with self._mcp_maintenance():
            configured = next(
                (
                    item
                    for item in self.config.server_specs
                    if item["server_id"] == expected.server_id
                ),
                None,
            )
            if configured is None or ServerSpec(**configured) != expected:
                raise RpcError(-32030, "La configuración MCP cambió. Revísala antes de conectar.")
            # The native owner confirmation reviewed this exact argv and version.
            # One deadline includes initialize and every tools/list page, below
            # the native 30 s budget; cancellation tears down the owned process.
            async with asyncio.timeout(24):
                status = await self.mcp.connect(expected)
        return {"server": {**asdict(status), **configured, "configured": True}}

    async def disconnect_mcp(self, params: dict) -> dict:
        _params(params, {"server_id"}, {"server_id"})
        async with self._mcp_maintenance():
            disconnected = await self.mcp.disconnect(params["server_id"])
        return {"disconnected": disconnected}

    async def remove_mcp(self, params: dict) -> dict:
        _params(params, {"server_id"}, {"server_id"})
        async with self._mcp_maintenance():
            configured = any(
                item["server_id"] == params["server_id"] for item in self.config.server_specs
            )
            await self.mcp.forget(params["server_id"])
            self._write_configuration(lambda: self.config.remove_server(params["server_id"]))
        return {"removed": configured}

    async def list_approvals(self, params: dict) -> dict:
        _params(params, _BASE | {"run_id"}, {"session_id"})
        ref = self._ref(params)
        entries = self.authority.list_pending(ref.session_id)
        return {
            "approvals": [
                {
                    "approval_id": item.approval_id,
                    "session_id": item.session_id,
                    "run_id": item.run_id,
                    "tool_name": item.tool_name,
                    "arguments": json.loads(item.arguments_json),
                    "arguments_json": item.arguments_json,
                    "digest": item.digest,
                    "expires_at": item.expires_at_ns / 1_000_000_000,
                }
                for item in entries
                if self._refs.get(item.run_id) == ref
                and ("run_id" not in params or params["run_id"] == item.run_id)
            ]
        }

    async def decide_approval(self, params: dict) -> dict:
        _params(
            params,
            _BASE | {"approval_id", "digest", "allow"},
            {"session_id", "approval_id", "digest", "allow"},
        )
        ref = self._ref(params)
        if type(params["allow"]) is not bool:
            raise RpcError(-32602, "La decisión debe ser permitir o rechazar.")
        item = next(
            (
                item
                for item in self.authority.list_pending(ref.session_id)
                if item.approval_id == params["approval_id"]
                and item.digest == params["digest"]
                and self._refs.get(item.run_id) == ref
            ),
            None,
        )
        accepted = self.authority.decide(item.approval_id, params["allow"]) if item else False
        return {"accepted": accepted}

    async def revoke_approvals(self, params: dict) -> dict:
        _params(params, _BASE, {"session_id"})
        ref = self._ref(params)
        count = self.authority.revoke(ref.session_id)
        return {"revoked": True, "count": count}

    async def list_memory(self, params: dict) -> dict:
        _params(params, _BASE | {"offset"}, {"session_id"})
        records = self.memory.list_documents(
            self._memory_scope(self._ref(params)), limit=50, offset=params.get("offset", 0)
        )
        return {
            "documents": [asdict(record) for record in records],
            "next_offset": params.get("offset", 0) + len(records),
            "has_more": len(records) == 50,
        }

    async def search_memory(self, params: dict) -> dict:
        _params(params, _BASE | {"query", "offset", "generation"}, {"session_id", "query"})
        result = self.memory.search(
            self._memory_scope(self._ref(params)),
            params["query"],
            limit=10,
            offset=params.get("offset", 0),
            expected_generation=params.get("generation"),
        )
        return {
            "matches": [asdict(hit) for hit in result.hits],
            "mode": result.mode,
            "generation": result.generation,
            "next_offset": result.next_offset,
            "has_more": result.has_more,
        }

    async def resolve_memory(self, params: dict) -> dict:
        _params(params, _BASE | {"citation"}, {"session_id", "citation"})
        if not isinstance(params["citation"], dict):
            raise RpcError(-32602, "La cita no es válida.")
        try:
            citation = Citation(**params["citation"])
        except TypeError:
            raise RpcError(-32602, "La cita no es válida.") from None
        text = self.memory.resolve_citation(self._memory_scope(self._ref(params)), citation)
        return {"excerpt": text, "verified": True, "external_source_verified": False}

    async def list_runs(self, params: dict) -> dict:
        _params(params, _BASE | {"offset"}, {"session_id"})
        records = self.store.list_runs(self._ref(params), limit=100, offset=params.get("offset", 0))
        return {
            "runs": [
                {"run_id": run.run_id, "status": run.status, "outcome": run.outcome}
                for run in records
            ],
            "next_offset": params.get("offset", 0) + len(records),
            "has_more": len(records) == 100,
        }

    async def login(self, params: dict) -> dict:
        _params(params, set())
        return await self.provider.login()

    async def logout(self, params: dict) -> dict:
        _params(params, set())
        if self._handles:
            raise RpcError(-32011, "Cancela las ejecuciones antes de cerrar la conexión.")
        return await self.provider.logout()

    async def create_session(self, params: dict) -> dict:
        _params(params, {"title", "temporary", "agent_id"})
        self._title(params.get("title", "Nueva conversación"))
        profile = self.agent_profiles.get(params.get("agent_id", "jarvis"))
        record = self.store.create_session(
            params.get("title", "Nueva conversación"),
            temporary=params.get("temporary", False),
            agent_id=profile.agent_id,
        )
        return {"session": _session(record)}

    async def list_sessions(self, params: dict) -> dict:
        _params(params, {"include_archived", "temporary", "offset"})
        archived = params.get("include_archived", False)
        temporary = params.get("temporary")
        if type(archived) is not bool or (temporary is not None and type(temporary) is not bool):
            raise RpcError(-32602, "La selección de sesiones no es válida.")
        offset = params.get("offset", 0)
        modes = (False, True) if temporary is None else (temporary,)
        records = [
            _session(record)
            for mode in modes
            for record in self.store.list_sessions(
                temporary=mode,
                include_archived=archived,
                limit=100,
                offset=offset,
            )
        ]
        return {
            "sessions": records,
            "next_offset": offset + 100,
            "has_more": any(
                len([r for r in records if r["temporary"] == mode]) == 100 for mode in (False, True)
            ),
        }

    @staticmethod
    def _title(title: str) -> None:
        if not isinstance(title, str) or not title.strip() or len(title.encode()) > 512:
            raise RpcError(-32602, "El título debe tener entre 1 y 512 bytes.")

    async def update_session(self, params: dict) -> dict:
        _params(params, _BASE | {"title", "archived"}, {"session_id"})
        ref = self._ref(params)
        if "title" in params:
            self._title(params["title"])
        if self._busy(ref):
            raise RpcError(-32011, "Espera a que termine la ejecución de esta conversación.")
        record = self.store.update_session(
            ref, title=params.get("title"), archived=params.get("archived")
        )
        return {"session": _session(record)}

    async def delete_session(self, params: dict) -> dict:
        _params(params, _BASE, {"session_id"})
        ref = self._ref(params)
        if self._busy(ref):
            raise RpcError(-32011, "Cancela y espera al cierre de la ejecución antes de borrar.")
        if self.telegram_uses_session(ref):
            raise RpcError(-32036, "Quita la ruta de Telegram antes de borrar esta conversación.")
        self.authority.revoke(ref.session_id)
        self._retiring_sessions.add(ref)
        try:
            if self.process_tools:
                await self.process_tools.close_scope(ref.session_id)
        finally:
            self._retiring_sessions.discard(ref)
        if ref.temporary:
            self._memory_scope(ref)
            for scope in self._temporary_memory_scopes.get(ref.session_id, ()):
                self.memory.clear_scope(scope)
            self._temporary_memory_scopes.pop(ref.session_id, None)
            self._volatile_bindings.pop(ref.session_id, None)
        else:
            self.config.forget(ref.session_id)
        self.store.delete_session(ref)
        return {"deleted": True}

    async def messages(self, params: dict) -> dict:
        _params(params, _BASE | {"after", "runs_offset"}, {"session_id"})
        ref = self._ref(params)
        selected = self.store.list_messages(ref, after=params.get("after", 0), limit=50)
        records = []
        size = 0
        for record in selected:
            size += len(json.dumps(asdict(record)).encode())
            if size > 256000:
                break
            records.append(record)
        runs = self.store.list_runs(ref, limit=100, offset=params.get("runs_offset", 0))
        return {
            "messages": [asdict(record) for record in records],
            "next_after": records[-1].sequence if records else params.get("after", 0),
            "has_more": len(records) == 50 or len(records) < len(selected),
            "runs": [
                {"run_id": run.run_id, "status": run.status, "outcome": run.outcome} for run in runs
            ],
            "runs_next_offset": params.get("runs_offset", 0) + len(runs),
            "runs_has_more": len(runs) == 100,
        }

    async def start_run(self, params: dict) -> dict:
        _params(params, _BASE | {"message", "request_id"}, {"session_id", "message"})
        if self._closing:
            raise RpcError(-32011, "Jarvis está cerrando el servicio.")
        if self._maintenance:
            raise RpcError(-32011, "Espera a que termine la conexión MCP actual.")
        if tuple(spec.name for spec in self.mcp.specs()) != self._mcp_signature:
            if self._handles:
                raise RpcError(
                    -32011, "El catálogo cambió. Espera a que termine la ejecución actual."
                )
            self._refresh_mcp_catalog()
        ref = self._ref(params)
        message = params["message"]
        if not isinstance(message, str) or not message.strip() or len(message.encode()) > 32000:
            raise RpcError(-32602, "El mensaje debe tener entre 1 y 32000 bytes.")
        request_id = params.get("request_id")
        if request_id is not None:
            if request_id in self._refs and self._refs[request_id] != ref:
                raise RpcError(-32012, "La identidad de solicitud ya está en uso.")
            try:
                previous = self.store.get_run(ref, request_id)
            except StateError as error:
                if error.code != "run_not_found":
                    raise
            else:
                original = next(
                    (
                        m
                        for m in self.store.get_run_messages(ref, request_id)
                        if m.run_id == request_id and m.role == "user"
                    ),
                    None,
                )
                if original is None or original.text != message:
                    raise RpcError(-32012, "La solicitud ya corresponde a otro mensaje.")
                return {"run_id": previous.run_id, "status": previous.status}
        if self._busy(ref):
            raise RpcError(-32011, "Espera a que termine la ejecución de esta conversación.")
        writer = self.profile_gate.hold("run.owner", mutating=True, closing=True)
        try:
            record = self.store.create_run(ref, run_id=request_id, user_text=message)
        except BaseException:
            writer.close()
            raise
        self._run_writers[record.run_id] = writer
        self._refs[record.run_id] = ref
        handle = self.runtime.start(ref.session_id, message, run_id=record.run_id)
        self._handles[handle.run_id] = handle
        observer = asyncio.create_task(self._settle(ref, handle))
        self._observers.add(observer)
        self._settlers[handle.run_id] = observer
        observer.add_done_callback(self._observers.discard)
        observer.add_done_callback(lambda _task: self._settlers.pop(handle.run_id, None))
        return {"run_id": handle.run_id, "status": record.status}

    async def _await_run_receipt(self, ref: SessionRef, handle: RunHandle) -> dict | None:
        """Wait for this run's durable settlement, not only the model result.

        Channel and schedule delivery cannot claim success from a volatile
        RuntimeResult if writing its authoritative receipt failed. Shield the
        observer so cancelling a delivery never cancels state persistence.
        """
        await handle.await_settled()
        observer = self._settlers.get(handle.run_id)
        try:
            if observer is not None:
                await asyncio.shield(observer)
            record = self.store.get_run(ref, handle.run_id)
            if record.status not in _TERMINAL or record.receipt_json is None:
                return None
            return self._run_value(ref, handle.run_id)
        except (StateError, ValueError):
            return None

    @profile_background("run.settlement")
    async def _settle(self, ref: SessionRef, handle: RunHandle) -> None:
        result = await handle.await_settled()
        try:
            error = self._errors.pop(handle.run_id, result.error)
            receipt = {
                "answer": result.answer,
                "answer_source": result.answer_source,
                "error": error,
                "action_receipts": [self._receipt(item) for item in result.action_receipts],
            }
            self.store.finish_run(
                ref,
                result.run_id,
                status=result.status,
                receipt=receipt,
                assistant_text=result.answer,
                clear_provider_thread=result.status != "completed",
                outcome="known" if result.status == "completed" else "unknown",
            )
        except StateError:
            # Do not forge a successful receipt if persistence failed. The active
            # record remains recoverable as interrupted on the next process start.
            self._closing = True
        finally:
            self._handles.pop(handle.run_id, None)
            self._refs.pop(handle.run_id, None)
            self._run_fingerprints.pop(handle.run_id, None)
            writer = self._run_writers.pop(handle.run_id, None)
            if writer is not None:
                writer.close()

    def _run_value(self, ref: SessionRef, run_id: str) -> dict:
        record = self.store.get_run(ref, run_id)
        receipt = json.loads(record.receipt_json) if record.receipt_json else {}
        return {
            "run_id": record.run_id,
            "status": record.status,
            "outcome": record.outcome,
            "answer": receipt.get("answer"),
            "error": receipt.get("error"),
            "answer_source": receipt.get("answer_source"),
            "action_receipts": receipt.get("action_receipts", []),
        }

    async def get_run(self, params: dict) -> dict:
        _params(params, _BASE | {"run_id"}, {"session_id", "run_id"})
        return {"run": self._run_value(self._ref(params), params["run_id"])}

    async def cancel_run(self, params: dict) -> dict:
        _params(params, _BASE | {"run_id"}, {"session_id", "run_id"})
        ref = self._ref(params)
        record = self.store.get_run(ref, params["run_id"])
        handle = self._handles.get(record.run_id)
        accepted = handle.cancel().accepted if handle else False
        if accepted:
            self.store.set_run_status(ref, record.run_id, "cancelling")
        return {"run_id": record.run_id, "accepted": accepted}

    async def events(self, params: dict) -> dict:
        _params(params, _BASE | {"run_id", "after"}, {"session_id", "run_id"})
        ref = self._ref(params)
        records = self.store.list_events(
            ref, params["run_id"], after=params.get("after", 0), limit=50
        )
        return {
            "events": [
                {
                    "sequence": record.sequence,
                    "kind": record.kind,
                    "payload": json.loads(record.payload_json),
                }
                for record in records
            ],
            "has_more": len(records) == 50,
        }

    async def start(self) -> None:
        self.authority.bind_host_scope(self.profile_lease.scope)
        self.profile_gate.on_generation = self.authority.bind_host_scope
        with self.profile_gate.admit("service.start"):
            await self.server.start()

    async def aclose(self) -> None:
        if (
            self._close_task is not None
            and self._close_task.done()
            and not self._close_task.cancelled()
            and isinstance(
                self._close_task.exception(),
                (McpRegistryError, CodexError, TelegramError, SchedulerError, TimeoutError),
            )
            and str(self._close_task.exception()) in {
                "mcp_cleanup_incomplete", "codex_cleanup_incomplete",
                "telegram_cleanup_incomplete", "service_cleanup_incomplete",
                "scheduler_cleanup_incomplete", "maintenance_cleanup_incomplete",
            }
        ):
            # An explicit new close may finish resources retained after a
            # incomplete child/channel shutdown. It never reopens IPC or inference.
            self._close_task = None
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        self._closing = True
        self.profile_gate.begin_close()
        if self.maintenance_coordinator is not None:
            await self.maintenance_coordinator.aclose()
        await self.profile_gate.wait_for_cut()
        # Do not bind a new host scope after the authority has closed.
        self.profile_gate.on_generation = None
        with self.profile_gate.admit("service.shutdown", mutating=True, closing=True):
            await self._close_resources()
        self.profile_lease.close()

    async def _close_resources(self) -> None:
        resource_errors = []
        try:
            await self.server.aclose()
        except Exception:
            # A pending authenticated handler may still own the stores. Keep
            # them live until an explicit close can confirm transport cleanup.
            resource_errors.append(TimeoutError("service_cleanup_incomplete"))
        try:
            await self.close_telegram()
        except Exception as error:
            resource_errors.append(error)
        scheduler_error = None
        try:
            await self.scheduler.aclose()
        except SchedulerError as error:
            scheduler_error = error
        for handle in tuple(self._handles.values()):
            handle.cancel()
        await self.authority.close()
        provider_close_attempted = False
        observers = tuple(self._observers)
        if observers:
            _, pending = await asyncio.wait(observers, timeout=25)
            if pending:
                # Stop the owned provider to release its pending turn, but never
                # close SQLite under a callback that has not actually settled.
                try:
                    provider_close_attempted = True
                    await self.provider.aclose()
                except Exception as error:
                    resource_errors.append(error)
                _, pending = await asyncio.wait(pending, timeout=3)
                if pending:
                    resource_errors.append(TimeoutError("service_cleanup_incomplete"))
        if scheduler_error:
            try:
                await self.scheduler.aclose()
            except Exception as error:
                resource_errors.append(error)
        # Close every independent owner even when one provider cannot settle.
        # Retain the stores while any child, channel or callback is unconfirmed.
        for closer in (
            self.process_tools.aclose if self.process_tools else None,
            self.web_tools.aclose,
            self.provider.aclose if not provider_close_attempted else None,
            self.mcp.aclose,
        ):
            if closer is not None:
                try:
                    await closer()
                except Exception as error:
                    resource_errors.append(error)
        if resource_errors:
            raise resource_errors[0]
        self.schedule_store.close()
        self.memory.close()
        self.store.close()


async def _serve(args: argparse.Namespace) -> None:
    lease = ProfileLease.acquire(args.state)
    try:
        provider = CodexClient(args.codex, args.provider_state or args.state / "provider")
        service = JarvisService(args.state, provider, profile_lease=lease)
    except BaseException:
        lease.close()
        raise
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopped.set)
    try:
        await service.start()
        await stopped.wait()
    finally:
        await service.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Servicio local de la base Jarvis")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--codex", type=Path, required=True)
    parser.add_argument(
        "--provider-state",
        type=Path,
        help="Conexión oficial explícita de este Jarvis; no busca ni importa cuentas.",
    )
    args = parser.parse_args()
    try:
        asyncio.run(_serve(args))
    except (
        StateError,
        CodexError,
        OSError,
        TransportSecurityError,
        HostProfileError,
        ProfileError,
        SkillError,
        PrivateJsonError,
        SchedulerError,
        McpRegistryError,
        TelegramError,
    ):
        parser.exit(1, "No se pudo iniciar o cerrar el servicio local de Jarvis.\n")


if __name__ == "__main__":
    main()
