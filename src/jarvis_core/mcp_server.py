"""Own authenticated loopback MCP server/relay; never a token passthrough.

The host supplies one dispatcher behind Jarvis's existing Runtime authority.
Neither this HTTP listener nor MCP annotations approve a tool. Local bearer
credentials authorize a principal/audience, not the requested operation. Reply
loss retains a bounded request receipt and never automatically repeats effects.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import re
import secrets
from collections import OrderedDict
from dataclasses import dataclass, field
from types import MappingProxyType

from jarvis_core.mcp import LEGACY_VERSION, MODERN_VERSION, _json_data, _object_pairs
from jarvis_core.mcp_http import decode_header


class McpRelayError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class RelayPrincipal:
    principal_id: str
    resource: str
    scopes: frozenset[str]
    epoch: int
    assert_current: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class RelayContext:
    principal: RelayPrincipal
    request_id: str | int
    protocol_version: str
    cancellation: asyncio.Event = field(repr=False, compare=False)
    assert_current: object = field(repr=False, compare=False)


@dataclass
class _Record:
    digest: str
    method: str
    result: dict | None = None
    unresolved: bool = False


@dataclass(eq=False)
class _Stream:
    principal_id: str
    session_id: str | None
    queue: asyncio.Queue
    filters: dict
    request_id: str | int | None


@dataclass
class _Notice:
    message: dict
    flushed: asyncio.Future


class McpRelayServer:
    def __init__(
        self,
        dispatch,
        *,
        authenticate=None,
        allowed_origins=(),
        before_effect=None,
        dispatch_guard=None,
        capabilities=None,
        server_name="Jarvis",
        max_bytes=1_048_576,
        request_timeout=180,
        max_connections=16,
        max_records=128,
    ):
        if (
            not callable(dispatch)
            or (authenticate is not None and not callable(authenticate))
            or type(max_bytes) is not int
            or not 1024 <= max_bytes <= 4_194_304
            or type(max_connections) is not int
            or not 1 <= max_connections <= 64
            or type(max_records) is not int
            or not 8 <= max_records <= 1024
            or type(request_timeout) not in {int, float}
            or not 1 <= request_timeout <= 900
        ):
            raise McpRelayError("mcp_relay_configuration_invalid")
        self.dispatch, self.authenticate = dispatch, authenticate
        self.allowed_origins = frozenset(allowed_origins)
        self.before_effect = before_effect
        self.dispatch_guard = dispatch_guard
        self.capabilities = _json_data(
            capabilities
            if capabilities is not None
            else {
                "tools": {"listChanged": True},
                "resources": {"listChanged": True, "subscribe": True},
                "prompts": {"listChanged": True},
            }
        )
        self.server_name, self.max_bytes, self.request_timeout = (
            server_name,
            max_bytes,
            request_timeout,
        )
        self.max_connections, self.max_records = max_connections, max_records
        self._server = None
        self._connections = {}
        self._workers = {}
        self._tool_headers = {}
        self._records = OrderedDict()
        self._sessions = {}
        self._streams = set()
        self._token = secrets.token_urlsafe(48)
        self._epoch = 1
        self._closing = False
        self.endpoint = None

    async def start(self, *, host="127.0.0.1", port=0):
        if host != "127.0.0.1" or type(port) is not int or not 0 <= port <= 65535:
            raise McpRelayError("mcp_relay_loopback_required")
        if self._server is not None or self._closing:
            raise McpRelayError("mcp_relay_already_started")
        self._server = await asyncio.start_server(self._accept, host, port, limit=32768 + 1024)
        actual = self._server.sockets[0].getsockname()[1]
        self.endpoint = f"http://127.0.0.1:{actual}/mcp"
        return self.status()

    def pairing_credential(self):
        """Owner-only control must keep this secret outside model/status/export."""
        if self.endpoint is None or self._closing:
            raise McpRelayError("mcp_relay_not_ready")
        return {
            "endpoint": self.endpoint,
            "bearer_token": self._token,
            "audience": self.endpoint,
            "scope": "jarvis_owner_bridge",
        }

    def _assert_local(self, epoch):
        if self._closing or self._epoch != epoch:
            raise McpRelayError("mcp_relay_revoked")
        if self.before_effect is not None:
            self.before_effect()

    async def _principal(self, token):
        if not isinstance(token, str) or len(token) > 8192:
            raise McpRelayError("mcp_relay_unauthorized")
        if self.authenticate is not None:
            principal = await self.authenticate(token)
            if not isinstance(principal, RelayPrincipal) or principal.resource != self.endpoint:
                raise McpRelayError("mcp_relay_unauthorized")
            principal.assert_current()
            return principal
        if not hmac.compare_digest(token, self._token):
            raise McpRelayError("mcp_relay_unauthorized")
        epoch = self._epoch
        return RelayPrincipal(
            hashlib.sha256(token.encode()).hexdigest(),
            self.endpoint,
            frozenset({"tools", "resources", "prompts", "metadata"}),
            epoch,
            lambda: self._assert_local(epoch),
        )

    async def _send(self, writer, status, body=b"", *, fields=None):
        if isinstance(body, dict):
            body = json.dumps(_json_data(body), separators=(",", ":"), ensure_ascii=False).encode()
        if len(body) > self.max_bytes:
            raise McpRelayError("mcp_relay_response_limit")
        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
            "Connection": "close",
            "Content-Length": str(len(body)),
            **(fields or {}),
        }
        writer.write(
            f"HTTP/1.1 {status} Result\r\n".encode()
            + "".join(f"{key}: {value}\r\n" for key, value in headers.items()).encode()
            + b"\r\n"
            + body
        )
        await writer.drain()

    @staticmethod
    def _error(identity, code, text):
        return {"jsonrpc": "2.0", "id": identity, "error": {"code": code, "message": text}}

    async def _read_request(self, reader):
        head = await reader.readuntil(b"\r\n\r\n")
        if len(head) > 32768:
            raise McpRelayError("mcp_relay_request_limit")
        lines = head[:-4].decode("ascii").split("\r\n")
        if len(lines) > 65:
            raise McpRelayError("mcp_relay_request_limit")
        method, target, version = lines[0].split(" ")
        if version != "HTTP/1.1" or target != "/mcp" or method not in {"POST", "GET", "DELETE"}:
            raise McpRelayError("mcp_relay_request_invalid")
        headers = {}
        for line in lines[1:]:
            key, value = line.split(":", 1)
            if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key) or key.lower() in headers:
                raise McpRelayError("mcp_relay_headers_invalid")
            if any(ord(c) < 32 or ord(c) > 126 for c in value):
                raise McpRelayError("mcp_relay_headers_invalid")
            headers[key.lower()] = value.strip()
        if headers.get("host") != self.endpoint.removeprefix("http://").removesuffix("/mcp"):
            raise McpRelayError("mcp_relay_host_invalid")
        origin = headers.get("origin")
        if origin is not None and origin not in self.allowed_origins:
            raise McpRelayError("mcp_relay_origin_invalid")
        auth = headers.get("authorization", "")
        if not auth.startswith("Bearer ") or "transfer-encoding" in headers:
            raise McpRelayError("mcp_relay_unauthorized")
        principal = await self._principal(auth[7:])
        principal.assert_current()
        length = headers.get("content-length", "0")
        if not re.fullmatch(r"[0-9]{1,10}", length) or int(length) > self.max_bytes:
            raise McpRelayError("mcp_relay_request_limit")
        if method != "POST" and int(length):
            raise McpRelayError("mcp_relay_request_invalid")
        raw = await reader.readexactly(int(length))
        principal.assert_current()
        return method, headers, raw, principal

    def _session(self, headers, principal, *, required):
        identity = headers.get("mcp-session-id")
        session = self._sessions.get(identity)
        if identity is None and not required:
            return None
        if session is None or session[0] != principal.principal_id or session[1] != principal.epoch:
            raise McpRelayError("mcp_relay_session_invalid")
        return identity

    def _routing(self, headers, message, protocol, principal):
        method, params = message.get("method"), message.get("params", {})
        if not isinstance(method, str) or not isinstance(params, dict):
            raise McpRelayError("mcp_relay_rpc_invalid")
        if protocol == MODERN_VERSION:
            if (
                headers.get("mcp-method") != method
                or not isinstance(params.get("_meta"), dict)
                or params.get("_meta", {}).get("io.modelcontextprotocol/protocolVersion")
                != protocol
            ):
                raise McpRelayError("mcp_relay_header_mismatch")
            if method in {"tools/call", "prompts/get", "resources/read"}:
                source = params.get("uri") if method == "resources/read" else params.get("name")
                if decode_header(headers.get("mcp-name", "")) != source:
                    raise McpRelayError("mcp_relay_header_mismatch")
            if method == "tools/call":
                expected = {}
                arguments = params.get("arguments", {})
                for header, path, kind in self._tool_headers.get(
                    (principal.principal_id, principal.epoch), {}
                ).get(params.get("name"), ()):
                    value = arguments
                    for key in path:
                        if not isinstance(value, dict) or key not in value:
                            break
                        value = value[key]
                    else:
                        if kind == "boolean" and type(value) is bool:
                            value = str(value).lower()
                        elif kind == "integer" and type(value) is int and abs(value) < 2**53:
                            value = str(value)
                        elif kind != "string" or not isinstance(value, str):
                            raise McpRelayError("mcp_relay_header_mismatch")
                        expected["mcp-param-" + header.lower()] = value
                actual = {
                    key: decode_header(value)
                    for key, value in headers.items()
                    if key.startswith("mcp-param-")
                }
                if actual != expected:
                    raise McpRelayError("mcp_relay_header_mismatch")
        return method, params

    def _subscription_filter(self, params, principal):
        requested = params.get("notifications", {})
        names = {
            "toolsListChanged": "tools",
            "promptsListChanged": "prompts",
            "resourcesListChanged": "resources",
            "resourceSubscriptions": "resources",
        }
        if not isinstance(requested, dict) or set(requested) - names.keys():
            raise McpRelayError("mcp_relay_subscription_invalid")
        honored = {}
        for key, value in requested.items():
            feature = names[key]
            if key == "resourceSubscriptions":
                if (
                    not isinstance(value, list)
                    or len(value) > 128
                    or any(not isinstance(uri, str) or len(uri.encode()) > 8192 for uri in value)
                ):
                    raise McpRelayError("mcp_relay_subscription_invalid")
                if feature in principal.scopes and self.capabilities.get(feature, {}).get(
                    "subscribe"
                ):
                    honored[key] = list(dict.fromkeys(value))
            elif type(value) is not bool:
                raise McpRelayError("mcp_relay_subscription_invalid")
            elif (
                value
                and feature in principal.scopes
                and self.capabilities.get(feature, {}).get("listChanged")
            ):
                honored[key] = True
        return honored

    async def _stream(
        self, reader, writer, principal, session_id=None, request_id=None, filters=None
    ):
        queue = asyncio.Queue(maxsize=64)
        row = _Stream(principal.principal_id, session_id, queue, filters or {}, request_id)
        self._streams.add(row)
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
            b"Cache-Control: no-store\r\nX-Accel-Buffering: no\r\nConnection: close\r\n\r\n"
        )
        await writer.drain()
        if request_id is not None:
            queue.put_nowait(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/subscriptions/acknowledged",
                    "params": {
                        "_meta": {"io.modelcontextprotocol/subscriptionId": request_id},
                        "notifications": row.filters,
                    },
                }
            )
        try:
            while not self._closing:
                principal.assert_current()
                item = asyncio.create_task(queue.get())
                disconnected = asyncio.create_task(reader.read(1))
                try:
                    ready, _ = await asyncio.wait(
                        (item, disconnected), timeout=15, return_when=asyncio.FIRST_COMPLETED
                    )
                    if disconnected in ready:
                        return
                    if item in ready:
                        message = item.result()
                        if message is None:
                            return
                        notice = message if isinstance(message, _Notice) else None
                        message = notice.message if notice is not None else message
                        raw = json.dumps(
                            message, ensure_ascii=False, separators=(",", ":")
                        ).encode()
                        if len(raw) > self.max_bytes:
                            raise McpRelayError("mcp_relay_response_limit")
                        writer.write(b"event: message\ndata: " + raw + b"\n\n")
                    else:
                        writer.write(b": keep-alive\n\n")
                    await writer.drain()
                    if item in ready and notice is not None and not notice.flushed.done():
                        notice.flushed.set_result(True)
                finally:
                    if item.done() and not item.cancelled():
                        queued = item.result()
                        if isinstance(queued, _Notice) and not queued.flushed.done():
                            queued.flushed.set_result(False)
                    item.cancel()
                    disconnected.cancel()
                    await asyncio.gather(item, disconnected, return_exceptions=True)
        finally:
            self._streams.discard(row)
            while not queue.empty():
                queued = queue.get_nowait()
                if isinstance(queued, _Notice) and not queued.flushed.done():
                    queued.flushed.set_result(False)

    async def notify(self, method, params=None, *, wait_flushed=False, timeout=1):
        names = {
            "notifications/tools/list_changed": "toolsListChanged",
            "notifications/prompts/list_changed": "promptsListChanged",
            "notifications/resources/list_changed": "resourcesListChanged",
            "notifications/resources/updated": "resourceSubscriptions",
        }
        if method not in names:
            raise McpRelayError("mcp_relay_notification_invalid")
        params = _json_data(params or {})
        if not isinstance(params, dict):
            raise McpRelayError("mcp_relay_notification_invalid")
        acknowledgements = []
        for row in tuple(self._streams):
            key = names[method]
            if row.request_id is not None and not row.filters.get(key):
                continue
            if (
                row.request_id is not None
                and key == "resourceSubscriptions"
                and params.get("uri") not in row.filters[key]
            ):
                continue
            selected = dict(params)
            if row.request_id is not None:
                selected["_meta"] = {
                    **selected.get("_meta", {}),
                    "io.modelcontextprotocol/subscriptionId": row.request_id,
                }
            message = {"jsonrpc": "2.0", "method": method, "params": selected}
            if len(json.dumps(message).encode()) > self.max_bytes:
                raise McpRelayError("mcp_relay_response_limit")
            if wait_flushed:
                acknowledgement = asyncio.get_running_loop().create_future()
                acknowledgements.append(acknowledgement)
                message = _Notice(message, acknowledgement)
            try:
                row.queue.put_nowait(message)
            except asyncio.QueueFull:
                raise McpRelayError("mcp_relay_stream_backpressure") from None

        if not wait_flushed:
            return None
        if acknowledgements:
            await asyncio.wait(acknowledgements, timeout=timeout)
        return {
            "receivers": len(acknowledgements),
            "flushed": sum(f.done() and f.result() is True for f in acknowledgements),
            "pending": sum(not f.done() for f in acknowledgements),
        }

    def _cache_headers(self, result, principal):
        key = principal.principal_id, principal.epoch
        if key not in self._tool_headers and len(self._tool_headers) >= 32:
            raise McpRelayError("mcp_relay_schema_capacity")
        schemas = self._tool_headers.setdefault(key, {})
        for tool in result.get("tools", ()):
            if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                raise McpRelayError("mcp_relay_result_invalid")
            paths = []

            def walk(node, path=(), static=True, paths=paths):
                if not isinstance(node, dict):
                    return
                if "x-mcp-header" in node:
                    header, kind = node["x-mcp-header"], node.get("type")
                    if (
                        not static
                        or not isinstance(header, str)
                        or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}", header)
                        or kind not in {"string", "integer", "boolean"}
                        or header.lower() in {row[0].lower() for row in paths}
                    ):
                        raise McpRelayError("mcp_relay_result_invalid")
                    paths.append((header, path, kind))
                for key, value in node.items():
                    if key == "properties" and isinstance(value, dict):
                        for name, child in value.items():
                            walk(child, (*path, name), static)
                    elif isinstance(value, dict):
                        walk(value, path, False)
                    elif isinstance(value, list):
                        for child in value:
                            walk(child, path, False)

            walk(tool.get("inputSchema"))
            schemas[tool["name"]] = paths

    async def _dispatch(self, message, protocol, principal, reader, session_id):
        identity = message.get("id")
        if type(identity) not in {str, int} or (isinstance(identity, str) and len(identity) > 128):
            raise McpRelayError("mcp_relay_rpc_invalid")
        method, params = message["method"], message.get("params", {})
        feature = method.split("/", 1)[0]
        if feature in {"tools", "resources", "prompts"} and feature not in principal.scopes:
            return self._error(identity, -32003, "Este cliente no tiene ese ámbito.")
        cancellation = asyncio.Event()
        local_epoch = self._epoch

        def guard():
            self._assert_local(local_epoch)
            principal.assert_current()
            if self.dispatch_guard is not None:
                self.dispatch_guard()
            if cancellation.is_set():
                raise McpRelayError("mcp_relay_cancelled")

        context = RelayContext(principal, identity, protocol, cancellation, guard)
        guard()
        digest = hashlib.sha256(
            json.dumps(message, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        key = principal.principal_id, principal.epoch, session_id, type(identity).__name__, identity
        previous = self._records.get(key)
        if previous is not None:
            if previous.digest != digest:
                return self._error(identity, -32012, "La identidad ya corresponde a otra petición.")
            if previous.result is not None:
                return previous.result
            return self._error(identity, -32011, "La petición anterior sigue pendiente o incierta.")
        while len(self._records) >= self.max_records:
            removable = next(
                (
                    key
                    for key, row in self._records.items()
                    if row.result is not None and not row.unresolved
                ),
                None,
            )
            if removable is None:
                return self._error(identity, -32011, "El registro de peticiones está lleno.")
            self._records.pop(removable)
        record = _Record(digest, method)
        self._records[key] = record
        guard()
        worker = asyncio.create_task(self.dispatch(method, MappingProxyType(params), context))
        self._workers[worker] = record
        worker.add_done_callback(lambda done: self._workers.pop(done, None))
        disconnected = asyncio.create_task(reader.read(1))
        try:
            ready, _ = await asyncio.wait(
                (worker, disconnected),
                timeout=self.request_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if worker not in ready:
                cancellation.set()
                worker.cancel()
                record.unresolved = True
                # Never acknowledge cleanup while the host callback remains live.
                await asyncio.shield(worker)
                return self._error(identity, -32002, "La petición fue cancelada; no se reenvía.")
            result = _json_data(worker.result())
            if not isinstance(result, dict):
                raise McpRelayError("mcp_relay_result_invalid")
            if method == "tools/list":
                self._cache_headers(result, principal)
            response = {"jsonrpc": "2.0", "id": identity, "result": result}
            if len(json.dumps(response).encode()) > self.max_bytes:
                raise McpRelayError("mcp_relay_response_limit")
            record.result = response
            return response
        except BaseException:
            cancellation.set()
            if not worker.done():
                worker.cancel()
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        continue
                    except BaseException:
                        break
            record.unresolved = True
            raise
        finally:
            disconnected.cancel()
            await asyncio.gather(disconnected, return_exceptions=True)

    def _accept(self, reader, writer):
        # Register before the coroutine's first instruction. Shutdown can then
        # account for a rejected/cancelled accept even if its body never starts.
        rejected = len(self._connections) >= self.max_connections or self._closing
        task = asyncio.create_task(self._connection(reader, writer, rejected=rejected))
        self._connections[task] = writer

    async def _connection(self, reader, writer, *, rejected=False):
        task = asyncio.current_task()
        self._connections.setdefault(task, writer)
        try:
            if rejected or self._closing:
                return
            async with asyncio.timeout(10):
                method, headers, raw, principal = await self._read_request(reader)
            protocol = headers.get("mcp-protocol-version")
            if protocol is None and method == "POST":
                initial = json.loads(raw, object_pairs_hook=_object_pairs)
                if (
                    isinstance(initial, dict)
                    and initial.get("method") == "initialize"
                    and isinstance(initial.get("params"), dict)
                    and initial["params"].get("protocolVersion") == LEGACY_VERSION
                ):
                    protocol = LEGACY_VERSION
            if protocol not in {LEGACY_VERSION, MODERN_VERSION}:
                await self._send(writer, 400)
                return
            if method == "GET":
                if protocol == MODERN_VERSION:
                    await self._send(writer, 405, fields={"Allow": "POST"})
                else:
                    session = self._session(headers, principal, required=True)
                    await self._stream(reader, writer, principal, session)
                return
            if method == "DELETE":
                session = self._session(headers, principal, required=True)
                self._sessions.pop(session)
                for row in tuple(self._streams):
                    if row.session_id == session:
                        row.queue.put_nowait(None)
                await self._send(writer, 204)
                return
            if headers.get("content-type", "").split(";", 1)[0] != "application/json":
                await self._send(writer, 415)
                return
            message = _json_data(json.loads(raw, object_pairs_hook=_object_pairs))
            if (
                not isinstance(message, dict)
                or message.get("jsonrpc") != "2.0"
                or "result" in message
                or "error" in message
            ):
                raise McpRelayError("mcp_relay_rpc_invalid")
            rpc_method, params = self._routing(headers, message, protocol, principal)
            if rpc_method == "notifications/initialized" and protocol == LEGACY_VERSION:
                self._session(headers, principal, required=True)
                await self._send(writer, 202)
                return
            if "id" not in message:
                raise McpRelayError("mcp_relay_rpc_invalid")
            response_headers = {}
            if rpc_method in {"initialize", "server/discover"}:
                if rpc_method == "initialize" and protocol == LEGACY_VERSION:
                    if params.get("protocolVersion") != protocol or len(self._sessions) >= 64:
                        raise McpRelayError("mcp_relay_protocol_invalid")
                    session = secrets.token_urlsafe(32)
                    self._sessions[session] = principal.principal_id, principal.epoch
                    response_headers["Mcp-Session-Id"] = session
                    result = {
                        "protocolVersion": protocol,
                        "serverInfo": {"name": self.server_name, "version": "0.1.0a1"},
                        "capabilities": self.capabilities,
                    }
                elif rpc_method == "server/discover" and protocol == MODERN_VERSION:
                    result = {
                        "supportedVersions": [MODERN_VERSION],
                        "ttlMs": 0,
                        "cacheScope": "private",
                        "capabilities": self.capabilities,
                        "serverInfo": {"name": self.server_name, "version": "0.1.0a1"},
                    }
                else:
                    raise McpRelayError("mcp_relay_protocol_invalid")
                response = {"jsonrpc": "2.0", "id": message["id"], "result": result}
            elif rpc_method == "subscriptions/listen" and protocol == MODERN_VERSION:
                await self._stream(
                    reader,
                    writer,
                    principal,
                    request_id=message["id"],
                    filters=self._subscription_filter(params, principal),
                )
                return
            elif rpc_method == "ping":
                response = {"jsonrpc": "2.0", "id": message["id"], "result": {}}
            else:
                session = self._session(headers, principal, required=protocol == LEGACY_VERSION)
                response = await self._dispatch(message, protocol, principal, reader, session)
            await self._send(writer, 200, response, fields=response_headers)
        except asyncio.CancelledError:
            raise
        except McpRelayError as error:
            status = (
                401
                if error.code == "mcp_relay_unauthorized"
                else 403
                if error.code in {"mcp_relay_host_invalid", "mcp_relay_origin_invalid"}
                else 400
            )
            code = -32020 if error.code == "mcp_relay_header_mismatch" else -32602
            with contextlib.suppress(Exception):
                await self._send(
                    writer, status, self._error(None, code, "No se aceptó la petición MCP.")
                )
        except (
            ValueError,
            OSError,
            UnicodeError,
            TimeoutError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            with contextlib.suppress(Exception):
                await self._send(
                    writer, 403, self._error(None, -32003, "No se aceptó la petición MCP.")
                )
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except (TimeoutError, OSError):
                # Keep the exact writer/task as an unresolved owner.
                return
            self._connections.pop(task, None)

    def status(self):
        return {
            "running": self._server is not None and not self._closing,
            "endpoint": self.endpoint,
            "loopback_only": True,
            "authentication_required": True,
            "active_connections": len(self._connections),
            "sessions": len(self._sessions),
            "retained_requests": len(self._records),
            "request_retention_window": self.max_records,
            "uncertain_requests": sum(row.unresolved for row in self._records.values()),
        }

    async def revoke(self, *, close_views=False):
        self._epoch += 1
        self._token = secrets.token_urlsafe(48)
        self._sessions.clear()
        self._tool_headers.clear()
        for row in tuple(self._streams):
            with contextlib.suppress(asyncio.QueueFull):
                row.queue.put_nowait(None)
        if close_views:
            await self._close_views()
        return {
            "revoked": True,
            "epoch": self._epoch,
            "local_cleanup_confirmed": not self._connections and not self._workers,
        }

    async def _close_views(self):
        current = asyncio.current_task()
        tasks = tuple(
            task for task in set(self._connections) | set(self._workers) if task is not current
        )
        for task in tasks:
            writer = self._connections.get(task)
            if writer is not None:
                writer.close()
            task.cancel()
        if tasks:
            _done, pending = await asyncio.wait(tasks, timeout=5)
            if pending:
                raise McpRelayError("mcp_relay_cleanup_pending")
            await asyncio.gather(*tasks, return_exceptions=True)
        for task, writer in tuple(self._connections.items()):
            if task is current:
                continue
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except (TimeoutError, OSError):
                raise McpRelayError("mcp_relay_cleanup_pending") from None
            self._connections.pop(task, None)

    @property
    def blockers(self):
        return ("mcp_relay_active",) if self._connections or self._workers else ()

    async def aclose(self):
        self._closing = True
        await self.revoke()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        tasks = tuple(set(self._connections) | set(self._workers))
        for task in tasks:
            task.cancel()
        if tasks:
            _done, pending = await asyncio.wait(tasks, timeout=5)
            if pending:
                raise McpRelayError("mcp_relay_cleanup_pending")
            await asyncio.gather(*tasks, return_exceptions=True)
        for task, writer in tuple(self._connections.items()):
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except (TimeoutError, OSError):
                raise McpRelayError("mcp_relay_cleanup_pending") from None
            self._connections.pop(task)
        return {
            "closed": True,
            "cleanup_confirmed": True,
            "uncertain_requests_preserved": sum(row.unresolved for row in self._records.values()),
        }
