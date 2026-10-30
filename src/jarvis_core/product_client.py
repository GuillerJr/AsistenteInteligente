"""Own local CLI/text client, with explicit admitted Ed25519 identity only.

No credential discovery, model-text execution, public listener, inference
fallback, mutation retry, or tool approval endpoint is provided. Terminal output
is JSON data, including model text, so terminal control sequences stay escaped.
Run with ``python -m jarvis_core.product_client`` or the host's entry point.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import os
import re
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from jarvis_core.client_identity import ClientIdentity
from jarvis_core.protocol import LocalRpcClient, RpcError
from jarvis_core.recovery_fs import Directory

_ID = re.compile(r"[a-f0-9]{32}\Z")
_TERMINAL = frozenset({"completed", "cancelled", "failed", "interrupted"})
_ACTIVE = frozenset({"queued", "running", "waiting_approval", "cancelling", "cleaning"})
_READ = frozenset({"service.local_status", "sessions.list", "sessions.messages", "runs.get",
                   "events.poll", "approvals.list", "product.snapshot", "product.tool.result"})
_WRITE = frozenset({"sessions.create", "runs.start", "runs.cancel", "product.initialize",
                    "product.tool.start", "product.interaction"})


class ProductClientError(ValueError):
    """Public errors retain stable metadata, never raw input or remote errors."""

    def __init__(self, code, *, request_id=None, unknown=False):
        super().__init__(code)
        self.code, self.request_id, self.unknown = code, request_id, unknown

    def receipt(self):
        return {"error": self.code, "request_id": self.request_id,
                "outcome": "unknown" if self.unknown else "rejected", "permits_replay": False}


def _fail(code, **kwargs):
    raise ProductClientError("client_" + code, **kwargs)


def _identity(value):
    if type(value) is not str or not _ID.fullmatch(value):
        _fail("identity_invalid")
    return value


def _text(value, limit, *, empty=False):
    try:
        valid = (type(value) is str and "\0" not in value and len(value.encode()) <= limit
                 and (empty or bool(value.strip())))
    except UnicodeError:
        valid = False
    if not valid:
        _fail("text_invalid")
    return value


def bounded_json(value, *, maximum=262144):
    nodes = 0

    def check(item, depth=0):
        nonlocal nodes
        nodes += 1
        if depth > 20 or nodes > 20000:
            _fail("frame_limit")
        if item is None or type(item) in {str, int, bool}:
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for child in item:
                check(child, depth + 1)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                check(child, depth + 1)
            return
        _fail("frame_invalid")

    check(value)
    try:
        data = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _fail("frame_invalid")
    if len(data) + 1 > maximum:
        _fail("frame_limit")
    return data + b"\n"


def decode_json(data, *, maximum=262144):
    if type(data) is str:
        data = data.encode()
    if type(data) is not bytes or len(data) > maximum:
        _fail("frame_limit")

    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                _fail("frame_invalid")
            result[key] = value
        return result

    def invalid(_value):
        _fail("frame_invalid")

    try:
        value = json.loads(data.decode(), object_pairs_hook=pairs, parse_constant=invalid)
    except (ValueError, UnicodeError, RecursionError):
        _fail("frame_invalid")
    bounded_json(value, maximum=maximum)
    return value


@dataclass(frozen=True)
class ClientSession:
    session_id: str
    temporary: bool = False

    def __post_init__(self):
        _identity(self.session_id)
        if type(self.temporary) is not bool:
            _fail("scope_invalid")

    @property
    def params(self):
        return {"session_id": self.session_id, "temporary": self.temporary}


@dataclass(frozen=True)
class ProductClientLimits:
    timeout: float = 20
    deadline: float = 180
    cleanup_deadline: float = 30
    poll_interval: float = 0.1
    max_frame_bytes: int = 262144
    max_output_bytes: int = 1048576
    max_messages: int = 1000
    max_events: int = 2000
    max_pages: int = 20
    idle_timeout: float = 120
    max_commands: int = 200

    def __post_init__(self):
        for key in ("timeout", "deadline", "cleanup_deadline", "poll_interval", "idle_timeout"):
            value = getattr(self, key)
            if type(value) not in {int, float} or not math.isfinite(value) or not 0 < value <= 1800:
                _fail("limits_invalid")
        for key in ("max_frame_bytes", "max_output_bytes", "max_messages", "max_events",
                    "max_pages", "max_commands"):
            value = getattr(self, key)
            if type(value) is not int or not 1 <= value <= 1048576:
                _fail("limits_invalid")


class ProductClient:
    def __init__(self, request: Callable[..., Awaitable], *, close=None, limits=None,
                 clock=time.monotonic, sleep=asyncio.sleep):
        self.request = request
        self.finish = close
        self.limits = limits or ProductClientLimits()
        self.clock, self.sleep = clock, sleep
        self._unknown = set()

    @classmethod
    async def connect(cls, state, identity_file, *, limits=None):
        limits = limits or ProductClientLimits()
        state, identity_file = Path(state), Path(identity_file)
        if (not state.is_absolute() or not identity_file.is_absolute()
                or ".." in state.parts or ".." in identity_file.parts):
            _fail("explicit_paths_required")
        try:
            identity = ClientIdentity.load(identity_file)
            transport = LocalRpcClient(state, identity=identity, timeout=limits.timeout,
                                       max_frame_bytes=limits.max_frame_bytes)
            await transport.connect()
        except (ValueError, OSError, RpcError, TimeoutError):
            _fail("connection_unavailable")
        return cls(transport.call, close=transport.aclose, limits=limits)

    async def close(self):
        if self.finish is not None:
            await self.finish()

    async def _call(self, method, params, *, request_id=None):
        if method not in _READ | _WRITE:
            _fail("method_unavailable")
        mutation = method in _WRITE
        key = None if request_id is None else (method, request_id)
        if mutation and key is not None and key in self._unknown:
            _fail("mutation_unconfirmed", request_id=request_id, unknown=True)
        bounded_json(params, maximum=self.limits.max_frame_bytes)
        try:
            async with asyncio.timeout(self.limits.timeout):
                result = await self.request(method, params)
            bounded_json(result, maximum=self.limits.max_frame_bytes)
            if type(result) is not dict:
                _fail("response_invalid")
            return result
        except asyncio.CancelledError:
            if mutation and key is not None:
                self._unknown.add(key)
            raise
        except RpcError:
            _fail("rpc_rejected", request_id=request_id)
        except (ValueError, OSError, RuntimeError, TimeoutError):
            if mutation:
                if key is not None:
                    self._unknown.add(key)
                _fail("mutation_unconfirmed", request_id=request_id, unknown=True)
            _fail("read_unavailable")

    async def status(self):
        return await self._call("service.local_status", {})

    async def initialize(self, *, temporary=False, workspace_path=None, workspace_name=None):
        if type(temporary) is not bool:
            _fail("scope_invalid")
        params = {"temporary": temporary}
        if (workspace_path is None) != (workspace_name is None):
            _fail("selection_invalid")
        if workspace_path is not None:
            path = Path(workspace_path)
            if not path.is_absolute() or ".." in path.parts:
                _fail("explicit_paths_required")
            params.update(workspace_path=_text(str(path), 4096),
                          workspace_name=_text(workspace_name, 512))
        return await self._call("product.initialize", params)

    async def sessions(self, *, temporary=False, offset=0):
        if type(temporary) is not bool or type(offset) is not int or not 0 <= offset <= 100000:
            _fail("selection_invalid")
        result = await self._call("sessions.list", {"temporary": temporary, "offset": offset})
        if type(result.get("sessions")) is not list or len(result["sessions"]) > 100:
            _fail("response_invalid")
        for row in result["sessions"]:
            if type(row) is not dict or row.get("temporary") is not temporary:
                _fail("response_invalid")
            _identity(row.get("session_id"))
        return result

    async def create_session(self, title="Nueva conversación", *, temporary=False,
                             workspace_id=None, agent_id="jarvis"):
        _text(title, 512)
        if type(temporary) is not bool:
            _fail("selection_invalid")
        if type(agent_id) is not str or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", agent_id) is None:
            _fail("selection_invalid")
        await self.initialize(temporary=temporary)
        params = {"title": title, "temporary": temporary, "agent_id": agent_id}
        if workspace_id is not None:
            params["workspace_id"] = _identity(workspace_id)
        result = await self._call("sessions.create", params)
        row = result.get("session")
        if type(row) is not dict or row.get("temporary") is not temporary:
            _fail("mutation_unconfirmed", unknown=True)
        return ClientSession(_identity(row.get("session_id")), temporary)

    async def messages(self, scope, *, after=0, complete=False):
        if type(scope) is not ClientSession or type(after) is not int or after < 0:
            _fail("scope_invalid")
        rows, total = [], 0
        for _ in range(self.limits.max_pages):
            value = await self._call("sessions.messages", {**scope.params, "after": after})
            page = value.get("messages")
            if type(page) is not list or len(page) > 50 or type(value.get("has_more")) is not bool:
                _fail("response_invalid")
            for row in page:
                if (type(row) is not dict or row.get("session_id") != scope.session_id
                        or type(row.get("sequence")) is not int or row["sequence"] <= after
                        or type(row.get("role")) is not str
                        or row["role"] not in {"user", "assistant", "system"}):
                    _fail("response_invalid")
                _text(row.get("text"), 65536, empty=True)
                after = row["sequence"]
                total += len(bounded_json(row, maximum=self.limits.max_frame_bytes))
                if total > self.limits.max_output_bytes or len(rows) >= self.limits.max_messages:
                    _fail("history_limit")
                rows.append(row)
            if not complete or not value["has_more"]:
                return {"messages": rows, "next_after": after, "has_more": value["has_more"]}
            if not page:
                _fail("response_invalid")
        _fail("history_limit")

    async def snapshot(self, scope, *, run_id=None):
        if type(scope) is not ClientSession:
            _fail("scope_invalid")
        params = scope.params
        if run_id is not None:
            params["run_id"] = "host" if run_id == "host" else _identity(run_id)
        value = await self._call("product.snapshot", params)
        binding = value.get("scope")
        if (type(binding) is not dict or binding.get("session_id") != scope.session_id
                or binding.get("temporary") is not scope.temporary
                or (run_id is not None and binding.get("run_id") != run_id)):
            _fail("response_invalid")
        return value

    async def approvals(self, scope, run_id):
        if type(scope) is not ClientSession:
            _fail("scope_invalid")
        run_id = _identity(run_id)
        value = await self._call("approvals.list", {**scope.params, "run_id": run_id})
        rows = value.get("approvals")
        if type(rows) is not list or len(rows) > 32:
            _fail("response_invalid")
        output = []
        for row in rows:
            if (type(row) is not dict or row.get("session_id") != scope.session_id
                    or row.get("run_id") != run_id
                    or type(row.get("digest")) is not str
                    or re.fullmatch(r"[a-f0-9]{64}", row["digest"]) is None):
                _fail("response_invalid")
            output.append({"approval_id": _identity(row.get("approval_id")),
                           "tool_name": _text(row.get("tool_name"), 128),
                           "digest": row["digest"], "review_in_app": True})
        return {"approvals": output}

    async def start_prompt(self, scope, message, *, request_id=None,
                           expected_workspace_id=None, expected_security_generation=None):
        if type(scope) is not ClientSession:
            _fail("scope_invalid")
        message, request_id = _text(message, 32000), _identity(request_id or uuid4().hex)
        params = {**scope.params, "message": message, "request_id": request_id}
        if (expected_workspace_id is None) != (expected_security_generation is None):
            _fail("selection_invalid")
        if expected_workspace_id is not None:
            _identity(expected_workspace_id)
            if (type(expected_security_generation) is not int
                    or not 0 <= expected_security_generation <= 2**53 - 1):
                _fail("selection_invalid")
            params.update(expected_workspace_id=expected_workspace_id,
                          expected_security_generation=expected_security_generation)
        value = await self._call("runs.start", params, request_id=request_id)
        if (value.get("run_id") != request_id or type(value.get("status")) is not str
                or value["status"] not in _ACTIVE | _TERMINAL):
            self._unknown.add(("runs.start", request_id))
            _fail("mutation_unconfirmed", request_id=request_id, unknown=True)
        return {"run_id": request_id, "status": value["status"], "permits_replay": False}

    async def get_run(self, scope, run_id):
        if type(scope) is not ClientSession:
            _fail("scope_invalid")
        value = await self._call("runs.get", {**scope.params, "run_id": _identity(run_id)})
        row = value.get("run")
        if (type(row) is not dict or row.get("run_id") != run_id
                or type(row.get("status")) is not str or row["status"] not in _ACTIVE | _TERMINAL
                or type(row.get("outcome")) is not str
                or row["outcome"] not in {"pending", "known", "unknown"}):
            _fail("response_invalid")
        if row.get("answer") is not None:
            _text(row["answer"], self.limits.max_output_bytes, empty=True)
        return row

    async def events(self, scope, run_id, *, after=0):
        if (type(scope) is not ClientSession or type(after) is not int or after < 0):
            _fail("scope_invalid")
        value = await self._call("events.poll", {**scope.params, "run_id": _identity(run_id),
                                                "after": after})
        rows = value.get("events")
        if (type(rows) is not list or len(rows) > 50
                or type(value.get("has_more")) is not bool):
            _fail("response_invalid")
        output = []
        for row in rows:
            if (type(row) is not dict or type(row.get("sequence")) is not int
                    or row["sequence"] <= after or type(row.get("payload")) is not dict):
                _fail("response_invalid")
            after = row["sequence"]
            item = {"sequence": after, "kind": _text(row.get("kind"), 128)}
            if item["kind"] == "answer.delta":
                item["text"] = _text(row["payload"].get("text"), 32000, empty=True)
            output.append(item)
        return {"events": output, "after": after, "has_more": value.get("has_more") is True}

    async def wait_run(self, scope, run_id, *, on_update=None, deadline=None):
        run_id = _identity(run_id)
        duration = self.limits.deadline if deadline is None else deadline
        if (type(duration) not in {int, float} or not math.isfinite(duration)
                or not 0 < duration <= 1800):
            _fail("limits_invalid")
        try:
            async with asyncio.timeout(duration):
                return await self._observe_run(scope, run_id, on_update=on_update,
                                               deadline=duration)
        except TimeoutError:
            _fail("run_deadline", request_id=run_id, unknown=True)
        except ProductClientError as error:
            raise ProductClientError(error.code, request_id=run_id, unknown=True) from None

    async def _observe_run(self, scope, run_id, *, on_update=None, deadline):
        run_id = _identity(run_id)
        end = self.clock() + deadline
        after, count, output = 0, 0, 0
        while self.clock() < end:
            events = await self.events(scope, run_id, after=after)
            after = events["after"]
            for event in events["events"]:
                count += 1
                output += len(bounded_json(event, maximum=self.limits.max_frame_bytes))
                if count > self.limits.max_events or output > self.limits.max_output_bytes:
                    _fail("output_limit", request_id=run_id, unknown=True)
                if on_update is not None:
                    await on_update(event)
            run = await self.get_run(scope, run_id)
            if run["status"] in _TERMINAL:
                if events["has_more"]:
                    continue
                return {**run, "settled": True, "permits_replay": False}
            if run["status"] == "waiting_approval" and on_update is not None:
                await on_update({"kind": "approval.pending", **await self.approvals(scope, run_id)})
            await self.sleep(self.limits.poll_interval)
        _fail("run_deadline", request_id=run_id, unknown=True)

    async def cancel_run(self, scope, run_id):
        run_id = _identity(run_id)
        ack = await self._call("runs.cancel", {**scope.params, "run_id": run_id},
                               request_id=run_id)
        if ack.get("run_id") != run_id or type(ack.get("accepted")) is not bool:
            self._unknown.add(("runs.cancel", run_id))
            _fail("mutation_unconfirmed", request_id=run_id, unknown=True)
        receipt = await self.wait_run(scope, run_id, deadline=self.limits.cleanup_deadline)
        return {**receipt, "cancel_accepted": ack["accepted"],
                "cancellation_confirmed": receipt["status"] == "cancelled"
                    and receipt.get("cleanup_confirmed") is True,
                "effects_known": receipt["outcome"] == "known"}

    async def project_tool(self, scope, tool, arguments, *, request_id=None):
        if tool not in {"project_inspect", "project_log", "project_worktrees", "project_register",
                        "project_operation"} or type(arguments) is not dict:
            _fail("project_action_invalid")
        identity = _identity(request_id or uuid4().hex)
        value = await self._call("product.tool.start", {**scope.params, "tool": tool,
            "arguments": arguments, "request_id": identity}, request_id=identity)
        if value.get("run_id") != identity:
            self._unknown.add(("product.tool.start", identity))
            _fail("mutation_unconfirmed", request_id=identity, unknown=True)
        return {"run_id": identity, "review_in_app": True, "permits_replay": False}


def create_identity(directory):
    """Create only a fresh, explicitly selected 0700 key directory; never admit it."""
    path = Path(directory)
    if not path.is_absolute() or ".." in path.parts or path.name in {"", ".", ".."}:
        _fail("identity_directory_invalid")
    try:
        with Directory(path.parent, private=False) as parent:
            parent.check()
            os.mkdir(path.name, mode=0o700, dir_fd=parent.fd)
            os.fsync(parent.fd)
            with Directory(path) as selected:
                identity = ClientIdentity.generate()
                fd = os.open("identity.key", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=selected.fd)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(identity.private_key.private_bytes_raw())
                    stream.flush()
                    os.fsync(stream.fileno())
                os.fsync(selected.fd)
                selected.check()
            parent.check()
        return identity.public_key
    except (OSError, ValueError):
        _fail("identity_creation_failed")


async def open_stdio(*, frame_limit=262144):
    """Async pipes avoid lingering input threads after an idle/output deadline."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=frame_limit)
    read_protocol = asyncio.StreamReaderProtocol(reader)
    read_transport, _ = await loop.connect_read_pipe(lambda: read_protocol, sys.stdin.buffer)
    protocol = asyncio.streams.FlowControlMixin(loop=loop)
    transport, _ = await loop.connect_write_pipe(lambda: protocol, sys.stdout.buffer)
    writer = asyncio.StreamWriter(transport, protocol, None, loop)
    writer._jarvis_read_transport = read_transport
    return reader, writer


def close_stdio(writer):
    reader_transport = getattr(writer, "_jarvis_read_transport", None)
    if reader_transport is not None:
        reader_transport.close()
    writer.close()


async def write_frame(writer, value, *, limits):
    data = bounded_json(value, maximum=limits.max_frame_bytes)
    writer.write(data)
    await asyncio.wait_for(writer.drain(), limits.timeout)


async def run_tui(client, reader, writer, *, scope=None, temporary=False):
    """Finite newline commands; prompt starts once, get/cancel observe that run."""
    help_text = {"ayuda": "status | sessions | new título | select ID | messages | "
        "projects | project ID | inbox | prompt texto | get RUN | cancel RUN | quit"}
    written = len(bounded_json(help_text, maximum=client.limits.max_frame_bytes))
    if written > client.limits.max_output_bytes:
        _fail("output_limit")
    await write_frame(writer, help_text, limits=client.limits)
    for _ in range(client.limits.max_commands):
        try:
            line = await asyncio.wait_for(reader.readline(), client.limits.idle_timeout)
        except (TimeoutError, ValueError):
            return
        if not line:
            return
        if len(line) > client.limits.max_frame_bytes or not line.endswith(b"\n"):
            _fail("frame_limit")
        try:
            command, _, argument = line[:-1].decode().partition(" ")
            if command in {"quit", "salir"}:
                return
            if command == "status":
                value = await client.status()
            elif command == "sessions":
                value = await client.sessions(temporary=temporary)
            elif command == "new":
                scope = await client.create_session(argument or "Nueva conversación",
                                                     temporary=temporary)
                value = scope.params
            elif command == "select":
                candidate = ClientSession(argument.strip(), temporary)
                await client.messages(candidate)
                scope, value = candidate, candidate.params
            elif scope is None:
                _fail("select_session")
            elif command == "messages":
                value = await client.messages(scope)
            elif command in {"projects", "project", "inbox"}:
                snapshot = await client.snapshot(scope)
                if command == "inbox":
                    value = {"inbox": snapshot.get("inbox")}
                elif command == "projects":
                    value = {"projects": snapshot.get("projects", [])}
                else:
                    value = {"project": next((row for row in snapshot.get("projects", [])
                        if row.get("project_id") == argument.strip()), None)}
            elif command == "prompt":
                value = await client.start_prompt(scope, argument)
            elif command == "get":
                value = await client.get_run(scope, argument.strip())
            elif command == "cancel":
                value = await client.cancel_run(scope, argument.strip())
            else:
                _fail("command_unavailable")
        except (ProductClientError, UnicodeError) as error:
            value = error.receipt() if isinstance(error, ProductClientError) else {
                "error": "client_input_invalid", "permits_replay": False}
        data = bounded_json(value, maximum=client.limits.max_frame_bytes)
        written += len(data)
        if written > client.limits.max_output_bytes:
            _fail("output_limit")
        await write_frame(writer, value, limits=client.limits)


def parser():
    result = argparse.ArgumentParser(prog="jarvis", description="Cliente local explícito de Jarvis")
    result.add_argument("--state", type=Path)
    result.add_argument("--identity-file", type=Path)
    result.add_argument("--temporary", action="store_true")
    result.add_argument("--timeout", type=float, default=20)
    result.add_argument("--deadline", type=float, default=180)
    commands = result.add_subparsers(dest="command", required=True)
    identity = commands.add_parser("identity-create")
    identity.add_argument("--directory", type=Path, required=True)
    commands.add_parser("status")
    initialize = commands.add_parser("initialize")
    initialize.add_argument("--workspace-path", type=Path)
    initialize.add_argument("--workspace-name")
    sessions = commands.add_parser("sessions")
    sessions.add_argument("action", choices=["list", "create", "messages"])
    sessions.add_argument("--session-id")
    sessions.add_argument("--title", default="Nueva conversación")
    sessions.add_argument("--workspace-id")
    sessions.add_argument("--after", type=int, default=0)
    project = commands.add_parser("project")
    project.add_argument("action", choices=["list", "read", "inspect", "propose"])
    project.add_argument("--session-id", required=True)
    project.add_argument("--project-id")
    project.add_argument("--kind", choices=["worktree.create", "worktree.archive",
                                           "worktree.restore", "file.restore"])
    project.add_argument("--arguments", default="{}")
    project.add_argument("--request-id")
    inbox = commands.add_parser("inbox")
    inbox.add_argument("--session-id", required=True)
    prompt = commands.add_parser("prompt")
    prompt.add_argument("--session-id", required=True)
    prompt.add_argument("--message", required=True)
    prompt.add_argument("--request-id")
    prompt.add_argument("--no-wait", action="store_true")
    control = commands.add_parser("control")
    control.add_argument("action", choices=["get", "cancel", "approvals", "snapshot"])
    control.add_argument("--session-id", required=True)
    control.add_argument("--run-id")
    tui = commands.add_parser("tui")
    tui.add_argument("--session-id")
    return result


async def execute_cli(client, args):
    if args.command == "status":
        return await client.status()
    if args.command == "initialize":
        return await client.initialize(temporary=args.temporary,
            workspace_path=args.workspace_path, workspace_name=args.workspace_name)
    if args.command == "sessions":
        if args.action == "list":
            return await client.sessions(temporary=args.temporary)
        if args.action == "create":
            return (await client.create_session(args.title, temporary=args.temporary,
                    workspace_id=args.workspace_id)).params
        return await client.messages(ClientSession(args.session_id, args.temporary),
                                     after=args.after)
    scope = ClientSession(args.session_id, args.temporary)
    if args.command == "prompt":
        started = await client.start_prompt(scope, args.message, request_id=args.request_id)
        args.request_id = started["run_id"]
        return started if args.no_wait else await client.wait_run(scope, started["run_id"])
    if args.command == "control":
        if args.action == "snapshot":
            return await client.snapshot(scope, run_id=args.run_id)
        if args.action == "cancel":
            return await client.cancel_run(scope, args.run_id)
        if args.action == "approvals":
            return await client.approvals(scope, args.run_id)
        return await client.get_run(scope, args.run_id)
    snapshot = await client.snapshot(scope)
    if args.command == "inbox":
        return {"inbox": snapshot.get("inbox")}
    if args.command == "project":
        rows = snapshot.get("projects", [])
        if args.action == "list":
            return {"projects": rows}
        project = next((row for row in rows if row.get("project_id") == args.project_id), None)
        if project is None:
            _fail("project_not_admitted")
        if args.action == "read":
            return {"project": project}
        if args.action == "inspect":
            return await client.project_tool(scope, "project_inspect",
                {"project_id": args.project_id}, request_id=args.request_id)
        if args.kind is None:
            _fail("project_action_invalid")
        return await client.project_tool(scope, "project_operation",
            {"project_id": args.project_id, "kind": args.kind,
             "arguments": decode_json(args.arguments)}, request_id=args.request_id)
    _fail("command_unavailable")


async def run_cli(args, *, client_factory=ProductClient.connect):
    if args.state is None or args.identity_file is None:
        _fail("explicit_paths_required")
    limits = ProductClientLimits(timeout=args.timeout, deadline=args.deadline)
    client = await client_factory(args.state, args.identity_file, limits=limits)
    try:
        if args.command == "tui":
            reader, writer = await open_stdio(frame_limit=limits.max_frame_bytes)
            try:
                scope = ClientSession(args.session_id, args.temporary) if args.session_id else None
                await run_tui(client, reader, writer, scope=scope, temporary=args.temporary)
            finally:
                close_stdio(writer)
            return None
        return await execute_cli(client, args)
    finally:
        await client.close()


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "identity-create":
            print(create_identity(args.directory))
            return 0
        value = asyncio.run(run_cli(args))
        if value is not None:
            sys.stdout.buffer.write(bounded_json(value, maximum=1048576))
        if type(value) is dict and (value.get("outcome") == "unknown"
                                    or value.get("cancellation_confirmed") is False):
            return 2
        return 0
    except (ProductClientError, KeyboardInterrupt, OSError) as error:
        value = error.receipt() if isinstance(error, ProductClientError) else {
            "error": "client_interrupted" if isinstance(error, KeyboardInterrupt)
                else "client_io_unavailable",
            "outcome": "unknown", "permits_replay": False,
            "request_id": getattr(args, "request_id", None)}
        with contextlib.suppress(OSError, ValueError):
            sys.stdout.buffer.write(bounded_json(value))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
