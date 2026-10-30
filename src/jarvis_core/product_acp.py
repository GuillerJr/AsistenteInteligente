"""Bounded local ACP v1 stdio adapter over Jarvis's signed local client.

Selected subset, not a universal ACP compliance claim. Reviewed primary docs:
https://agentclientprotocol.com/protocol/v1/schema
https://agentclientprotocol.com/protocol/v1/transports
https://agentclientprotocol.com/protocol/v1/session-setup
https://agentclientprotocol.com/protocol/v1/prompt-turn

No remote listener, client filesystem/terminal calls, ACP-supplied MCP launch,
extra permission authority or approvals.decide exists. Native approvals remain
in Jarvis. Resource links are preserved as JSON prompt data, never dereferenced.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from jarvis_core.product_client import (
    ClientSession,
    ProductClient,
    ProductClientError,
    ProductClientLimits,
    _identity,
    _text,
    bounded_json,
    close_stdio,
    decode_json,
    open_stdio,
    write_frame,
)


class ACPError(ValueError):
    def __init__(self, code, message, *, data=None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


def _params(value, allowed, required=()):
    if (type(value) is not dict or set(value) - set(allowed) - {"_meta"}
            or set(required) - set(value)):
        raise ACPError(-32602, "Los parámetros ACP no son válidos.")
    return value


def _id(value):
    try:
        return ((type(value) is int and abs(value) <= 2**53 - 1)
                or (type(value) is str and 1 <= len(value.encode()) <= 128))
    except UnicodeError:
        return False


def _content(blocks):
    if type(blocks) is not list or not 1 <= len(blocks) <= 64:
        raise ACPError(-32602, "El prompt requiere bloques de contenido acotados.")
    parts = []
    for block in blocks:
        if type(block) is not dict:
            raise ACPError(-32602, "El bloque de contenido no es válido.")
        if block.get("type") == "text":
            _params(block, {"type", "text", "annotations"}, {"type", "text"})
            parts.append(_text(block["text"], 32000, empty=True))
        elif block.get("type") == "resource_link":
            _params(block, {"type", "uri", "name", "mimeType", "title", "description", "size",
                            "annotations"}, {"type", "uri", "name"})
            _text(block["uri"], 4096)
            _text(block["name"], 512)
            for key in ("mimeType", "title", "description"):
                if block.get(key) is not None:
                    _text(block[key], 2048, empty=True)
            if block.get("size") is not None and (type(block["size"]) is not int
                                                   or not 0 <= block["size"] < 2**63):
                raise ACPError(-32602, "El tamaño del recurso no es válido.")
            # Preserve the complete selected reference as data. No file/network
            # read or instruction/harness rewriting is performed by this adapter.
            parts.append(bounded_json(block, maximum=32000).decode().rstrip("\n"))
        else:
            raise ACPError(-32602, "Este adaptador admite texto y referencias de recursos.")
    return _text("".join(parts), 32000)


@dataclass
class _Turn:
    scope: ClientSession
    request_id: str
    cancelled: bool = False
    cancel_started: bool = False
    dispatched: bool = False


class ACPBridge:
    def __init__(self, client: ProductClient, emit, *, workspace, workspace_id,
                 temporary=False):
        self.client, self.emit = client, emit
        self.workspace = Path(workspace)
        if not self.workspace.is_absolute() or ".." in self.workspace.parts:
            raise ACPError(-32602, "Elige una carpeta absoluta y admitida para ACP.")
        self.workspace_id = _identity(workspace_id)
        if type(temporary) is not bool:
            raise ACPError(-32602, "El modo temporal no es válido.")
        self.temporary = temporary
        self.initialized = False
        self.sessions = {}
        self.generations = {}
        self.active = {}
        self.used_runs = set()
        self.client_capabilities = {}
        self._cancel_tasks = set()

    async def _update(self, session_id, update):
        await self.emit({"jsonrpc": "2.0", "method": "session/update",
                         "params": {"sessionId": session_id, "update": update}})

    def _workspace_params(self, params, *, load=False):
        _params(params, {"cwd", "mcpServers", "sessionId"} if load else {"cwd", "mcpServers"},
                {"cwd", "mcpServers", "sessionId"} if load else {"cwd", "mcpServers"})
        if type(params["cwd"]) is not str or params["cwd"] != str(self.workspace):
            raise ACPError(-32602, "La carpeta ACP debe coincidir con la selección explícita.")
        if params["mcpServers"] != []:
            raise ACPError(-32602, "Configura los servidores MCP desde Jarvis; ACP no los inicia.")

    def _session(self, session_id):
        if type(session_id) is not str or session_id not in self.sessions:
            raise ACPError(-32002, "La sesión no está cargada en esta conexión ACP.")
        return self.sessions[session_id]

    def _generation(self, snapshot):
        binding = snapshot.get("binding")
        if (type(binding) is not dict or binding.get("workspace_id") != self.workspace_id
                or snapshot["scope"].get("workspace_id") != self.workspace_id
                or type(binding.get("security_generation")) is not int
                or not 0 <= binding["security_generation"] <= 2**53 - 1):
            raise ACPError(-32602, "La sesión pertenece a otra carpeta o su vínculo cambió.")
        return binding["security_generation"]

    async def initialize(self, params):
        _params(params, {"protocolVersion", "clientCapabilities", "clientInfo"},
                {"protocolVersion"})
        if self.initialized:
            raise ACPError(-32600, "La conexión ACP ya está inicializada.")
        version = params["protocolVersion"]
        if type(version) is not int or not 0 <= version <= 65535:
            raise ACPError(-32602, "La versión ACP no es válida.")
        capabilities = params.get("clientCapabilities", {})
        if type(capabilities) is not dict:
            raise ACPError(-32602, "Las capacidades del cliente no son válidas.")
        configured = await self.client.initialize(temporary=self.temporary)
        workspaces = configured.get("workspaces")
        if type(workspaces) is not list or len(workspaces) > 64 or not any(
                type(row) is dict and row.get("workspace_id") == self.workspace_id
                and row.get("path") == str(self.workspace) for row in workspaces):
            raise ACPError(-32000, "Admite esta identidad y carpeta en Jarvis antes de usar ACP.")
        self.client_capabilities = capabilities
        self.initialized = True
        return {"protocolVersion": 1, "agentInfo": {"name": "jarvis-local", "title": "Jarvis",
            "version": "0.1.0"}, "agentCapabilities": {"loadSession": True,
                "promptCapabilities": {"image": False, "audio": False, "embeddedContext": False},
                "mcpCapabilities": {"http": False, "sse": False}}, "authMethods": [],
            "_meta": {"jarvis": {"profile": "local-v1-subset", "permissions": "native_app_only",
                "mcpConfiguration": "native_app_only", "resourceLinks": "json_data_no_fetch",
                "temporary": self.temporary, "permitsReplay": False}}}

    async def new_session(self, params):
        self._workspace_params(params)
        if len(self.sessions) >= 32:
            raise ACPError(-32004, "Se alcanzó el límite de sesiones ACP de esta conexión.")
        scope = await self.client.create_session("Conversación ACP", temporary=self.temporary,
                                                 workspace_id=self.workspace_id)
        snapshot = await self.client.snapshot(scope, run_id="host")
        self.generations[scope.session_id] = self._generation(snapshot)
        self.sessions[scope.session_id] = scope
        return {"sessionId": scope.session_id}

    async def load_session(self, params):
        self._workspace_params(params, load=True)
        if len(self.sessions) >= 32 and params["sessionId"] not in self.sessions:
            raise ACPError(-32004, "Se alcanzó el límite de sesiones ACP de esta conexión.")
        scope = ClientSession(_identity(params["sessionId"]), self.temporary)
        if scope.session_id in self.active:
            raise ACPError(-32011, "Espera al cierre del turno antes de cargar esta sesión.")
        snapshot = await self.client.snapshot(scope, run_id="host")
        generation = self._generation(snapshot)
        # Read all pages before emitting anything. Oversized history is rejected,
        # never presented as a complete replay after silently truncating it.
        history = await self.client.messages(scope, complete=True)
        if self._generation(await self.client.snapshot(scope, run_id="host")) != generation:
            raise ACPError(-32602, "El vínculo de la sesión cambió durante la carga.")
        self.generations[scope.session_id] = generation
        self.sessions[scope.session_id] = scope
        for message in history["messages"]:
            if message["role"] in {"user", "assistant"}:
                await self._update(scope.session_id, {
                    "sessionUpdate": "user_message_chunk" if message["role"] == "user"
                        else "agent_message_chunk",
                    "content": {"type": "text", "text": message["text"]}})
        return {}

    async def prompt(self, params):
        _params(params, {"sessionId", "prompt"}, {"sessionId", "prompt"})
        scope = self._session(params["sessionId"])
        if scope.session_id in self.active:
            raise ACPError(-32011, "Esta sesión todavía tiene un turno pendiente de cierre.")
        message = _content(params["prompt"])
        meta = params.get("_meta") or {}
        if type(meta) is not dict or type(meta.get("jarvis", {})) is not dict:
            raise ACPError(-32602, "Los metadatos ACP no son válidos.")
        request_id = _identity(meta.get("jarvis", {}).get("request_id") or uuid4().hex)
        if request_id in self.used_runs:
            raise ACPError(-32602, "La identidad de este turno ya se utilizó; no se reenviará.")
        self.used_runs.add(request_id)
        turn = _Turn(scope, request_id)
        self.active[scope.session_id] = turn
        chunks, approvals = [], set()

        async def updated(event):
            if event.get("kind") == "answer.delta":
                chunks.append(event["text"])
                await self._update(scope.session_id, {"sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": event["text"]}})
            elif event.get("kind") == "approval.pending":
                for row in event["approvals"]:
                    if row["approval_id"] in approvals:
                        continue
                    approvals.add(row["approval_id"])
                    await self._update(scope.session_id, {"sessionUpdate": "tool_call",
                        "toolCallId": row["approval_id"],
                        "title": "Revisar permiso: " + row["tool_name"],
                        "kind": "other", "status": "pending", "_meta": {"jarvis": {
                            "reviewInApp": True, "digest": row["digest"]}}})

        try:
            current = self._generation(await self.client.snapshot(scope, run_id="host"))
            if current != self.generations.get(scope.session_id):
                raise ACPError(-32602, "El vínculo de la sesión cambió. Cárgala de nuevo.")
            if turn.cancelled:
                return {"stopReason": "cancelled", "_meta": {"jarvis": {
                    "request_id": request_id, "outcome": "known", "runStarted": False,
                    "cleanupConfirmed": True, "permitsReplay": False}}}
            turn.dispatched = True
            await self.client.start_prompt(scope, message, request_id=request_id,
                expected_workspace_id=self.workspace_id, expected_security_generation=current)
            receipt = await self.client.wait_run(scope, request_id, on_update=updated)
            if (receipt.get("cleanup_confirmed") is True
                    or (receipt["status"] == "completed" and receipt["outcome"] == "known")):
                for approval_id in approvals:
                    await self._update(scope.session_id, {"sessionUpdate": "tool_call_update",
                        "toolCallId": approval_id,
                        "status": "completed" if receipt["status"] == "completed" else "failed",
                        "_meta": {"jarvis": {"nativePermissionReviewOnly": True,
                            "outcome": receipt["outcome"]}}})
            if turn.cancelled or receipt["status"] == "cancelled":
                if receipt.get("cleanup_confirmed") is not True:
                    raise ACPError(-32010, "El cierre quedó sin confirmar; no se repetirá.",
                        data={"request_id": request_id, "outcome": "unknown",
                              "permitsReplay": False})
                return {"stopReason": "cancelled", "_meta": {"jarvis": {
                    "request_id": request_id, "outcome": receipt["outcome"],
                    "cleanupConfirmed": True, "permitsReplay": False}}}
            if receipt["status"] != "completed" or receipt["outcome"] != "known":
                raise ACPError(-32010, "El turno terminó sin un resultado confirmado.",
                    data={"request_id": request_id, "outcome": receipt["outcome"],
                          "permitsReplay": False})
            answer = receipt.get("answer") or ""
            streamed = "".join(chunks)
            if not answer.startswith(streamed):
                raise ACPError(-32010, "La salida final requiere revisión en Jarvis.",
                               data={"request_id": request_id, "permitsReplay": False})
            tail = answer[len(streamed):]
            if tail:
                await self._update(scope.session_id, {"sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": tail}})
            return {"stopReason": "end_turn", "_meta": {"jarvis": {
                "request_id": request_id, "outcome": "known", "permitsReplay": False}}}
        except ProductClientError as error:
            if error.code == "client_rpc_rejected" and not error.unknown:
                turn.dispatched = False  # A definitive start rejection created no run.
            raise
        finally:
            # Unknown receipt keeps the session blocked in this connection. A
            # separate explicit load/connection is needed to inspect core state.
            if (not turn.dispatched or ("receipt" in locals()
                    and (receipt.get("cleanup_confirmed") is True
                    or (receipt["status"] == "completed" and receipt["outcome"] == "known")))):
                self.active.pop(scope.session_id, None)

    async def _cancel(self, turn):
        if turn.cancel_started:
            return
        turn.cancel_started = True
        turn.cancelled = True
        if not turn.dispatched:
            return
        try:
            await self.client.cancel_run(turn.scope, turn.request_id)
        except ProductClientError:
            # An ACK or failed transport is never a cleanup receipt.
            return

    async def cancel(self, params):
        _params(params, {"sessionId"}, {"sessionId"})
        scope = self._session(params["sessionId"])
        turn = self.active.get(scope.session_id)
        if turn is not None and not turn.cancel_started:
            task = asyncio.create_task(self._cancel(turn))
            self._cancel_tasks.add(task)
            task.add_done_callback(self._cancel_tasks.discard)
        return None

    async def dispatch(self, message):
        if (type(message) is not dict or message.get("jsonrpc") != "2.0"
                or type(message.get("method")) is not str
                or set(message) - {"jsonrpc", "id", "method", "params"}
                or ("id" in message and not _id(message["id"]))):
            raise ACPError(-32600, "El mensaje JSON-RPC no es válido.")
        method, params = message["method"], message.get("params", {})
        if method == "initialize":
            return await self.initialize(params)
        if not self.initialized:
            raise ACPError(-32000, "Inicializa la conexión ACP antes de usar sesiones.")
        if method == "session/cancel":
            if "id" in message:
                raise ACPError(-32600, "session/cancel requiere una notificación sin id.")
            return await self.cancel(params)
        if "id" not in message:
            return None
        methods = {"session/new": self.new_session, "session/load": self.load_session,
                   "session/prompt": self.prompt}
        if method not in methods:
            raise ACPError(-32601, "El método ACP no está disponible en este adaptador local.")
        return await methods[method](params)

    async def close(self):
        for turn in tuple(self.active.values()):
            if not turn.cancel_started:
                task = asyncio.create_task(self._cancel(turn))
                self._cancel_tasks.add(task)
                task.add_done_callback(self._cancel_tasks.discard)
        if self._cancel_tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*tuple(self._cancel_tasks),
                    return_exceptions=True), self.client.limits.cleanup_deadline + 1)
            except TimeoutError:
                pass


async def serve(reader, writer, bridge: ACPBridge):
    """Newline-delimited JSON-RPC with concurrent prompt/cancel and bounded I/O."""
    limits = bridge.client.limits
    tasks, seen = set(), set()
    lock = asyncio.Lock()
    written = 0

    async def emit(value):
        nonlocal written
        frame = bounded_json(value, maximum=limits.max_frame_bytes)
        async with lock:
            written += len(frame)
            if written > limits.max_output_bytes:
                raise ProductClientError("client_output_limit", unknown=True)
            await write_frame(writer, value, limits=limits)

    bridge.emit = emit

    async def respond(message):
        identity = message.get("id") if type(message) is dict else None
        if not _id(identity):
            identity = None
        try:
            result = await bridge.dispatch(message)
            if (type(message) is dict and "id" in message
                    and message.get("method") != "session/cancel"):
                await emit({"jsonrpc": "2.0", "id": identity, "result": result})
        except (ACPError, ProductClientError) as error:
            if type(message) is dict and "id" not in message:
                return
            if isinstance(error, ACPError):
                value = {"code": error.code, "message": error.message}
                if error.data is not None:
                    value["data"] = error.data
            else:
                value = {"code": -32010, "message": "La operación requiere revisión en Jarvis.",
                         "data": error.receipt()}
            await emit({"jsonrpc": "2.0", "id": identity, "error": value})

    try:
        for _ in range(limits.max_commands):
            try:
                frame = await asyncio.wait_for(reader.readline(), limits.idle_timeout)
            except (TimeoutError, ValueError):
                break
            if not frame:
                break
            if len(frame) > limits.max_frame_bytes or not frame.endswith(b"\n"):
                await emit({"jsonrpc": "2.0", "id": None,
                    "error": {"code": -32700, "message": "El frame supera el límite ACP."}})
                break
            try:
                message = decode_json(frame, maximum=limits.max_frame_bytes)
            except ProductClientError:
                await emit({"jsonrpc": "2.0", "id": None,
                    "error": {"code": -32700, "message": "El frame ACP no es JSON válido."}})
                continue
            if type(message) is dict and "id" in message and _id(message["id"]):
                identity = (type(message["id"]).__name__, message["id"])
                if identity in seen:
                    await emit({"jsonrpc": "2.0", "id": message["id"], "error": {
                        "code": -32600, "message": "La identidad JSON-RPC ya se utilizó."}})
                    continue
                seen.add(identity)
            if len(tasks) >= 8 and not (type(message) is dict
                                        and message.get("method") == "session/cancel"):
                if type(message) is dict and "id" in message:
                    await emit({"jsonrpc": "2.0", "id": message["id"], "error": {
                        "code": -32004, "message": "Hay demasiadas solicitudes ACP pendientes."}})
                continue
            # Initialization and cancellation are handled before reading the next
            # line; prompts run independently so a cancel notification can arrive.
            if (type(message) is dict and type(message.get("method")) is str
                    and message["method"] in {"initialize", "session/cancel"}):
                await respond(message)
            else:
                task = asyncio.create_task(respond(message))
                tasks.add(task)
                def finished(done):
                    tasks.discard(done)
                    if not done.cancelled():
                        done.exception()  # Retrieve failures without logging peer input.
                task.add_done_callback(finished)
                await asyncio.sleep(0)
    finally:
        await bridge.close()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def parser():
    result = argparse.ArgumentParser(prog="jarvis-acp", description="Adaptador ACP local de Jarvis")
    result.add_argument("--state", type=Path, required=True)
    result.add_argument("--identity-file", type=Path, required=True)
    result.add_argument("--workspace", type=Path, required=True)
    result.add_argument("--workspace-id", required=True)
    result.add_argument("--temporary", action="store_true")
    result.add_argument("--timeout", type=float, default=20)
    result.add_argument("--deadline", type=float, default=180)
    return result


async def run(args, *, client_factory=ProductClient.connect):
    limits = ProductClientLimits(timeout=args.timeout, deadline=args.deadline)
    client = await client_factory(args.state, args.identity_file, limits=limits)
    try:
        reader, writer = await open_stdio(frame_limit=limits.max_frame_bytes)
        try:
            bridge = ACPBridge(client, None, workspace=args.workspace,
                               workspace_id=args.workspace_id, temporary=args.temporary)
            await serve(reader, writer, bridge)
        finally:
            close_stdio(writer)
    finally:
        await client.close()


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        asyncio.run(run(args))
        return 0
    except (ACPError, ProductClientError, OSError, KeyboardInterrupt):
        # Stdout is reserved for JSON-RPC. No raw input, credential, remote error
        # or selected history is copied to stderr on an adapter failure.
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
