"""Own bounded MCP HTTP/SSE client; no environment proxies, cookies or replay.

Wire eras/transports are owner-selected. Streamable HTTP 2025 retains explicit
sessions/GET events; 2026 is stateless POST with mirrored routing headers. Legacy
HTTP+SSE is explicit, never an automatic fallback. Remote output stays untrusted.
Primary contracts: modelcontextprotocol.io/specification/{2025-11-25,2026-07-28}/
basic/transports and /2026-07-28/basic/transports/streamable-http (2026-10-28).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import ipaddress
import json
import math
import re
import socket
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urljoin, urlsplit

from jarvis_core.mcp import (
    LEGACY_VERSION,
    MODERN_VERSION,
    McpCancelled,
    McpClient,
    McpError,
    McpInputRequired,
    McpTool,
    _freeze,
    _json_data,
    _object_pairs,
    _schema,
)
from jarvis_core.web import (
    WebError,
    _chunk_size,
    _field,
    connect_pinned,
    parse_url,
    public_ip,
    resolve_public,
)

SSE_VERSION = "2024-11-05"
_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
_METHOD = re.compile(r"[a-zA-Z][a-zA-Z0-9_./-]{0,127}\Z")
_SESSION = re.compile(r"[\x21-\x7e]{1,128}\Z")


class McpHTTPError(McpError):
    def __init__(self, code, *, effect_status="not_dispatched", status=None, challenge=None):
        super().__init__(code, effect_status=effect_status)
        self.status, self.challenge = status, challenge


@dataclass(frozen=True)
class HTTPURL:
    canonical: str
    hostname: str
    port: int
    target: str
    host_header: str
    secure: bool

    @property
    def origin(self):
        return ("https" if self.secure else "http", self.hostname, self.port)


def endpoint_url(value, *, allow_loopback=False, test_origins=(), config=False):
    """Only explicit public HTTPS or numeric loopback granted by the host."""
    try:
        parts = urlsplit(value)
        if config and (parts.query or parts.fragment):
            raise ValueError
        if parts.scheme == "https":
            parsed = parse_url(value)
            return HTTPURL(
                parsed.canonical, parsed.hostname, 443, parsed.target, parsed.host_header, True
            )
        if (
            parts.scheme != "http"
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
            or not parts.port
            or not parts.hostname
            or any(c.isspace() or ord(c) < 32 for c in value)
            or "\\" in value
        ):
            raise ValueError
        address = ipaddress.ip_address(parts.hostname)
        origin = f"http://{parts.netloc}"
        if not address.is_loopback or (not allow_loopback and origin not in test_origins):
            raise ValueError
        if parts.hostname not in {"127.0.0.1", "::1"}:
            raise ValueError
        target = parts.path or "/"
        if parts.query:
            target += "?" + parts.query
        if not value.isascii() or len(value.encode()) > 8192:
            raise ValueError
        return HTTPURL(origin + target, parts.hostname, parts.port, target, parts.netloc, False)
    except (ValueError, TypeError, UnicodeError, WebError):
        raise McpHTTPError("http_endpoint_invalid") from None


def header_value(value):
    if not isinstance(value, str):
        raise McpHTTPError("http_header_value_invalid")
    safe = (
        value == value.strip()
        and all(32 <= ord(c) < 127 for c in value)
        and not (value.startswith("=?base64?") and value.endswith("?="))
    )
    return value if safe else "=?base64?" + base64.b64encode(value.encode()).decode() + "?="


def decode_header(value):
    if value.startswith("=?base64?") and value.endswith("?="):
        try:
            return base64.b64decode(value[9:-2], validate=True).decode()
        except (ValueError, UnicodeError):
            raise McpHTTPError("http_header_value_invalid") from None
    if any(ord(c) < 32 or ord(c) >= 127 for c in value) or value != value.strip():
        raise McpHTTPError("http_header_value_invalid")
    return value


async def read_headers(reader):
    for _ in range(4):
        block = await reader.readuntil(b"\r\n\r\n")
        if len(block) > 32768:
            raise McpHTTPError("http_header_limit")
        lines = block[:-4].split(b"\r\n")
        match = re.fullmatch(rb"HTTP/1\.[01] ([1-5][0-9]{2})(?: [\x20-\x7e]*)?", lines[0])
        if match is None or len(lines) > 101:
            raise McpHTTPError("http_response_invalid")
        fields = {}
        for line in lines[1:]:
            key, value = _field(line)
            if key in fields:
                raise McpHTTPError("http_duplicate_header")
            fields[key] = value
        code = int(match[1])
        if code == 101 or ("content-length" in fields and "transfer-encoding" in fields):
            raise McpHTTPError("http_response_invalid")
        if fields.get("transfer-encoding", "chunked").lower() != "chunked":
            raise McpHTTPError("http_transfer_unsupported")
        if fields.get("content-encoding", "identity").lower() != "identity":
            raise McpHTTPError("http_encoding_unsupported")
        if "content-length" in fields and not re.fullmatch(
            r"[0-9]{1,10}", fields["content-length"]
        ):
            raise McpHTTPError("http_response_invalid")
        if code >= 200:
            return code, fields
    raise McpHTTPError("http_response_invalid")


@dataclass
class HTTPExchange:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter = field(repr=False)
    status: int
    headers: Mapping
    maximum: int
    shutdown_timeout: float
    dispatched: bool = False
    closed: bool = False
    consumed: int = 0

    async def chunks(self):
        remaining = (
            int(self.headers["content-length"]) if "content-length" in self.headers else None
        )
        if remaining is not None and remaining > self.maximum:
            raise McpHTTPError("http_body_limit")
        if self.headers.get("transfer-encoding", "").lower() == "chunked":
            for _ in range(32768):
                count = _chunk_size(await self.reader.readuntil(b"\r\n"))
                if count == 0:
                    for _ in range(33):
                        trailer = await self.reader.readuntil(b"\r\n")
                        if trailer == b"\r\n":
                            return
                        # Do not interpret or accept auth/routing/framing trailers.
                        raise McpHTTPError("http_trailer_unsupported")
                    raise McpHTTPError("http_trailer_limit")
                if self.consumed + count > self.maximum:
                    raise McpHTTPError("http_body_limit")
                data = await self.reader.readexactly(count + 2)
                if not data.endswith(b"\r\n"):
                    raise McpHTTPError("http_chunk_invalid")
                self.consumed += count
                yield data[:-2]
            raise McpHTTPError("http_chunk_limit")
        while remaining is None or remaining > 0:
            size = min(
                65536, (remaining if remaining is not None else self.maximum - self.consumed + 1)
            )
            data = await self.reader.read(size)
            if not data:
                if remaining:
                    raise McpHTTPError("http_body_incomplete")
                return
            self.consumed += len(data)
            if self.consumed > self.maximum:
                raise McpHTTPError("http_body_limit")
            if remaining is not None:
                remaining -= len(data)
            yield data

    async def body(self):
        data = bytearray()
        async for chunk in self.chunks():
            data.extend(chunk)
        return bytes(data)

    async def events(self):
        pending = bytearray()
        data, event = [], "message"
        async for chunk in self.chunks():
            pending.extend(chunk)
            while b"\n" in pending:
                line, _, rest = pending.partition(b"\n")
                pending = bytearray(rest)
                if len(line) > self.maximum:
                    raise McpHTTPError("sse_event_limit")
                line = line.rstrip(b"\r")
                if not line:
                    if data:
                        yield event, b"\n".join(data).decode("utf-8")
                    data, event = [], "message"
                elif line.startswith(b"data:"):
                    data.append(line[5:].removeprefix(b" "))
                elif line.startswith(b"event:"):
                    event = line[6:].strip().decode("ascii")
                elif line.startswith(b":") or line.startswith((b"id:", b"retry:")):
                    # Events IDs never trigger automatic effect replay/reconnect.
                    continue
                if sum(len(item) for item in data) + len(pending) > self.maximum:
                    raise McpHTTPError("sse_event_limit")
        if pending or data:
            raise McpHTTPError("sse_stream_incomplete")

    async def close(self):
        if not self.closed:
            self.writer.close()
            try:
                await asyncio.wait_for(self.writer.wait_closed(), self.shutdown_timeout)
            except (TimeoutError, OSError):
                raise McpHTTPError("http_cleanup_pending") from None
            self.closed = True


class McpHTTPNetwork:
    def __init__(
        self,
        *,
        allow_loopback=False,
        test_origins=(),
        resolver=resolve_public,
        connector=None,
        before_effect=None,
        shutdown_timeout=0.5,
    ):
        self.allow_loopback = bool(allow_loopback)
        self.test_origins = tuple(test_origins)
        for value in self.test_origins:
            endpoint_url(value, allow_loopback=True, config=True)
        self.resolver, self.connector = resolver, connector
        self.before_effect = before_effect
        self.shutdown_timeout = shutdown_timeout
        self._exchanges = []
        self._connecting = set()
        self._closed = False

    def url(self, value, *, config=False):
        return endpoint_url(
            value, allow_loopback=self.allow_loopback, test_origins=self.test_origins, config=config
        )

    async def open(
        self,
        value,
        *,
        method="GET",
        headers=None,
        body=b"",
        before_dispatch=None,
        credential=None,
        maximum=1_048_576,
    ):
        if self._closed:
            raise McpHTTPError("http_closed")
        url = self.url(value)
        if (
            method not in {"GET", "POST", "DELETE"}
            or not isinstance(body, bytes)
            or len(body) > maximum
        ):
            raise McpHTTPError("http_request_invalid")
        fields = {"Host": url.host_header, "Connection": "close", "Accept-Encoding": "identity"}
        for key, item in (headers or {}).items():
            if (
                not isinstance(key, str)
                or not _TOKEN.fullmatch(key)
                or not isinstance(item, str)
                or any(ord(c) < 32 or ord(c) > 126 for c in item)
                or key.lower()
                in {
                    "host",
                    "connection",
                    "content-length",
                    "transfer-encoding",
                    "cookie",
                    "proxy-authorization",
                    "authorization",
                }
            ):
                raise McpHTTPError("http_request_headers_invalid")
            fields[key] = item
        fields["Content-Length"] = str(len(body))
        if credential is not None:
            credential.assert_current()
            resource = self.url(credential.resource)
            if resource.origin != url.origin:
                raise McpHTTPError("http_credential_audience_mismatch")
            fields["Authorization"] = credential.authorization_header
        head = (
            method
            + " "
            + url.target
            + " HTTP/1.1\r\n"
            + "".join(f"{key}: {item}\r\n" for key, item in fields.items())
            + "\r\n"
        ).encode()
        if len(head) > 32768:
            raise McpHTTPError("http_header_limit")
        writer = None
        exchange = None
        try:
            if self.before_effect is not None:
                self.before_effect()
            if url.secure:
                addresses = tuple(
                    dict.fromkeys(public_ip(item) for item in await self.resolver(url.hostname))
                )
                if not addresses:
                    raise McpHTTPError("http_address_missing")
                address = addresses[0]
            else:
                address = url.hostname
            connection = asyncio.create_task(self._connect(address, url, maximum))
            self._connecting.add(connection)
            connection.add_done_callback(self._connecting.discard)
            try:
                exchange = await asyncio.shield(connection)
            except BaseException:
                connection.cancel()
                await asyncio.wait((connection,), timeout=self.shutdown_timeout)
                # The task remains owned until it has actually settled, and any
                # late socket is registered/closed by _connect, never orphaned.
                if connection.done():
                    with contextlib.suppress(BaseException):
                        connection.result()
                raise
            reader, writer = exchange.reader, exchange.writer
            # Every await above precedes final authority/credential validation.
            if self._closed:
                raise McpHTTPError("http_closed")
            if self.before_effect is not None:
                self.before_effect()
            if credential is not None:
                credential.assert_current()
            if before_dispatch is not None:
                before_dispatch()
            writer.write(head + body)
            await writer.drain()
            status, response_fields = await read_headers(reader)
            exchange.status, exchange.headers = status, MappingProxyType(response_fields)
            exchange.dispatched = True
            return exchange
        except BaseException:
            if writer is not None:
                retained = exchange or HTTPExchange(
                    asyncio.StreamReader(), writer, 0, {}, maximum, self.shutdown_timeout
                )
                if exchange is None:
                    self._exchanges.append(retained)
                with contextlib.suppress(Exception):
                    await asyncio.shield(retained.close())
                self.release(retained)
            raise

    async def _connect(self, address, url, maximum):
        if self.connector is not None:
            reader, writer = await self.connector(address, url)
        elif url.secure:
            reader, writer = await connect_pinned(address, url.hostname)
        else:
            reader, writer = await asyncio.open_connection(
                address,
                url.port,
                family=socket.AF_INET6 if ":" in address else socket.AF_INET,
                flags=socket.AI_NUMERICHOST,
                limit=32768 + 1024,
            )
        exchange = HTTPExchange(reader, writer, 0, {}, maximum, self.shutdown_timeout)
        self._exchanges.append(exchange)
        if self._closed or asyncio.current_task().cancelling():
            await exchange.close()
            self.release(exchange)
            raise asyncio.CancelledError
        return exchange

    async def request(
        self,
        value,
        *,
        method="GET",
        headers=None,
        body=b"",
        before_dispatch=None,
        credential=None,
        maximum=1_048_576,
        timeout=20,
    ):
        exchange = None
        try:
            async with asyncio.timeout(timeout):
                exchange = await self.open(
                    value,
                    method=method,
                    headers=headers,
                    body=body,
                    before_dispatch=before_dispatch,
                    credential=credential,
                    maximum=maximum,
                )
                data = await exchange.body()
                return exchange.status, exchange.headers, data
        finally:
            if exchange is not None:
                await asyncio.shield(exchange.close())
                self.release(exchange)

    def release(self, exchange):
        if exchange.closed and exchange in self._exchanges:
            self._exchanges.remove(exchange)

    async def json(self, value, *, accepted_statuses=(200,), **options):
        status, headers, raw = await self.request(value, **options)
        if (
            status not in accepted_statuses
            or headers.get("content-type", "").split(";", 1)[0].strip() != "application/json"
        ):
            raise McpHTTPError("http_metadata_invalid", status=status)
        try:
            return _json_data(json.loads(raw, object_pairs_hook=_object_pairs))
        except (ValueError, UnicodeError, RecursionError):
            raise McpHTTPError("http_json_invalid") from None

    @property
    def blockers(self):
        return (
            ("mcp_http_cleanup_pending",)
            if self._connecting or any(not item.closed for item in self._exchanges)
            else ()
        )

    async def aclose(self):
        self._closed = True
        connecting = tuple(self._connecting)
        for task in connecting:
            task.cancel()
        if connecting:
            _done, pending = await asyncio.wait(connecting, timeout=self.shutdown_timeout)
            if pending:
                raise McpHTTPError("http_cleanup_pending")
        for exchange in tuple(self._exchanges):
            if not exchange.closed:
                await exchange.close()
        self._exchanges.clear()
        return {"closed": True, "cleanup_confirmed": True, "remote_effects_may_continue": True}


class McpHttpClient(McpClient):
    def __init__(
        self,
        endpoint,
        directory,
        *,
        transport="streamable_http",
        protocol_version=LEGACY_VERSION,
        authorize=None,
        credentials=None,
        before_effect=None,
        request_timeout=20,
        shutdown_timeout=0.5,
        max_line_bytes=1_048_576,
        max_pages=16,
        max_items=1000,
        allow_loopback=False,
        test_origins=(),
        network=None,
    ):
        if transport not in {"streamable_http", "sse"} or protocol_version not in {
            LEGACY_VERSION,
            MODERN_VERSION,
            SSE_VERSION,
        }:
            raise McpError("unsupported_mcp_version")
        if protocol_version == SSE_VERSION and transport != "sse":
            raise McpError("unsupported_mcp_version")
        super().__init__(
            (str(Path(directory) / "http-transport"),),
            directory,
            authorize=authorize,
            protocol_version=(
                LEGACY_VERSION if protocol_version == SSE_VERSION else protocol_version
            ),
            request_timeout=request_timeout,
            shutdown_timeout=shutdown_timeout,
            max_line_bytes=max_line_bytes,
            max_pages=max_pages,
            max_items=max_items,
        )
        self.protocol_version = protocol_version
        self.network = network or McpHTTPNetwork(
            allow_loopback=allow_loopback,
            test_origins=test_origins,
            before_effect=before_effect,
            shutdown_timeout=shutdown_timeout,
        )
        self.endpoint = self.network.url(endpoint, config=True).canonical
        self.transport = transport
        self.credentials = credentials
        self._session_id = None
        self._post_endpoint = self.endpoint
        self._events_task = None
        self._event_exchange = None
        self._subscription_id = None
        self._active = set()
        self._http_closing = False
        self._header_paths = {}
        self._http_close_task = None
        self._session_delete_attempted = False
        self.close_receipt = None

    @property
    def connected(self):
        return self._ready and not self._failure and not self._http_closing

    def _credential(self):
        return self.credentials(self.endpoint) if self.credentials is not None else None

    def _headers(self, method, params):
        fields = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.protocol_version != SSE_VERSION:
            fields["MCP-Protocol-Version"] = self.protocol_version
        if self._session_id is not None:
            fields["Mcp-Session-Id"] = self._session_id
        if self.protocol_version == MODERN_VERSION:
            fields["Mcp-Method"] = method
            if method in {"tools/call", "prompts/get", "resources/read"}:
                fields["Mcp-Name"] = header_value(
                    params.get("uri") if method == "resources/read" else params.get("name")
                )
            if method == "tools/call":
                args = params.get("arguments", {})
                for name, path, kind in self._header_paths.get(params.get("name"), ()):
                    value = args
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
                            raise McpError("invalid_tool_arguments")
                        fields["Mcp-Param-" + name] = header_value(value)
        return fields

    async def start(self):
        async with self._start_lock:
            if self.connected:
                return self
            if self._http_closing or self._failure:
                raise McpError("client_closed")
            try:
                if self.transport == "sse":
                    await self._open_events(legacy=True)
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
                        or not math.isfinite(result["ttlMs"])
                        or result["ttlMs"] < 0
                        or result.get("cacheScope") not in {"public", "private"}
                    ):
                        raise McpError("invalid_discovery")
                else:
                    result = await self._request(
                        "initialize",
                        {
                            "protocolVersion": self.protocol_version,
                            "capabilities": getattr(self, "client_capabilities", {}),
                            "clientInfo": {"name": "Jarvis", "version": "0.1.0a1"},
                        },
                        ready=False,
                    )
                    if result.get("protocolVersion") != self.protocol_version:
                        raise McpError("unsupported_mcp_version")
                info = result.get("serverInfo")
                if not isinstance(info, dict) or not all(
                    isinstance(info.get(key), str) for key in ("name", "version")
                ):
                    raise McpError("invalid_server_info")
                caps = result.get("capabilities")
                if not isinstance(caps, dict) or any(
                    not isinstance(v, dict) for v in caps.values()
                ):
                    raise McpError("invalid_capabilities")
                self.capabilities = _freeze(caps)
                if self.protocol_version != MODERN_VERSION:
                    await self._notification("notifications/initialized", {})
                self._ready = True
                if self.transport == "streamable_http" and self.protocol_version == MODERN_VERSION:
                    await self._open_subscription()
                if (
                    self.transport == "streamable_http"
                    and self.protocol_version == LEGACY_VERSION
                    and any(v.get("listChanged") or v.get("subscribe") for v in caps.values())
                ):
                    await self._open_events(legacy=False)
                return self
            except BaseException:
                await self.aclose(retry=False)
                raise

    def _notification(self, method, params):
        return self._post_notification(method, params)

    async def _post_notification(self, method, params):
        body = json.dumps(
            {"jsonrpc": "2.0", "method": method, "params": params},
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        status, _, raw = await self.network.request(
            self._post_endpoint,
            method="POST",
            headers=self._headers(method, params),
            body=body,
            credential=self._credential(),
            maximum=self.max_line_bytes,
            timeout=self.request_timeout,
        )
        if status != 202 or raw:
            raise McpHTTPError("http_notification_rejected", status=status)

    async def _open_events(self, *, legacy):
        async with asyncio.timeout(self.request_timeout):
            exchange = await self.network.open(
                self.endpoint,
                headers={
                    "Accept": "text/event-stream",
                    **({"Mcp-Session-Id": self._session_id} if self._session_id else {}),
                    **({"MCP-Protocol-Version": self.protocol_version} if not legacy else {}),
                },
                credential=self._credential(),
                maximum=self.max_line_bytes * 8,
            )
        if not legacy and exchange.status == 405:
            await exchange.close()
            self.network.release(exchange)
            return
        if (
            exchange.status != 200
            or exchange.headers.get("content-type", "").split(";")[0] != "text/event-stream"
        ):
            await exchange.close()
            raise McpHTTPError("sse_connection_failed")
        self._event_exchange = exchange
        endpoint_ready = asyncio.Event()
        self._events_task = asyncio.create_task(self._listen(exchange, endpoint_ready, legacy))
        if legacy:
            async with asyncio.timeout(self.request_timeout):
                await endpoint_ready.wait()
            if self._failure:
                raise McpError(self._failure)

    async def _open_subscription(self):
        filters = {
            feature + "ListChanged": True
            for feature in ("tools", "resources", "prompts")
            if self.capabilities.get(feature, {}).get("listChanged") is True
        }
        if not filters:
            return
        self._counter += 1
        identity = self._counter
        self._subscription_id = identity
        params = {
            "notifications": filters,
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
                "io.modelcontextprotocol/clientInfo": {"name": "Jarvis", "version": "0.1.0a1"},
                "io.modelcontextprotocol/clientCapabilities": _json_data(
                    getattr(self, "client_capabilities", {})
                ),
            },
        }
        body = json.dumps(
            {"jsonrpc": "2.0", "id": identity, "method": "subscriptions/listen", "params": params},
            separators=(",", ":"),
        ).encode()
        async with asyncio.timeout(self.request_timeout):
            exchange = await self.network.open(
                self.endpoint,
                method="POST",
                headers=self._headers("subscriptions/listen", params),
                body=body,
                credential=self._credential(),
                maximum=self.max_line_bytes * 8,
            )
            self._event_exchange = exchange
            if (
                exchange.status != 200
                or exchange.headers.get("content-type", "").split(";")[0] != "text/event-stream"
            ):
                raise McpHTTPError("sse_subscription_failed")
            events = exchange.events()
            event, data = await anext(events)
            message = _json_data(json.loads(data, object_pairs_hook=_object_pairs))
            if not isinstance(message, dict) or not isinstance(message.get("params"), dict):
                raise McpHTTPError("sse_subscription_ack_invalid")
            metadata = message["params"].get("_meta", {})
            acknowledged = message["params"].get("notifications", {})
            if (
                event != "message"
                or message.get("jsonrpc") != "2.0"
                or "id" in message
                or message.get("method") != "notifications/subscriptions/acknowledged"
                or not isinstance(metadata, dict)
                or metadata.get("io.modelcontextprotocol/subscriptionId") != identity
                or not isinstance(acknowledged, dict)
                or set(acknowledged) - filters.keys()
                or any(value is not True for value in acknowledged.values())
            ):
                raise McpHTTPError("sse_subscription_ack_invalid")
        self.notification_methods.append(message["method"])
        self._events_task = asyncio.create_task(
            self._listen_subscription(exchange, events, identity, acknowledged)
        )

    async def _listen_subscription(self, exchange, events, identity, acknowledged):
        try:
            async for event, data in events:
                message = _json_data(json.loads(data, object_pairs_hook=_object_pairs))
                if not isinstance(message, dict):
                    raise McpError("invalid_rpc_message")
                holder = message.get("params", message.get("result", {}))
                if (
                    event != "message"
                    or not isinstance(holder, dict)
                    or not isinstance(holder.get("_meta"), dict)
                    or holder["_meta"].get("io.modelcontextprotocol/subscriptionId") != identity
                ):
                    raise McpError("uncorrelated_subscription")
                if "id" in message:
                    if (
                        message["id"] != identity
                        or "method" in message
                        or message.get("result", {}).get("resultType") != "complete"
                    ):
                        raise McpError("invalid_rpc_message")
                    if not self._http_closing:
                        self._failure = "sse_subscription_ended"
                    return
                allowed = {
                    "notifications/" + feature + "/list_changed"
                    for feature in ("tools", "resources", "prompts")
                    if acknowledged.get(feature + "ListChanged") is True
                }
                if message.get("method") not in allowed:
                    raise McpError("unrequested_subscription_notification")
                self._accept_message(message)
            if not self._http_closing:
                raise McpError("sse_connection_lost")
        except asyncio.CancelledError:
            raise
        except (
            McpError,
            WebError,
            ValueError,
            OSError,
            UnicodeError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            self._failure = "sse_connection_lost"
        finally:
            await exchange.close()
            self.network.release(exchange)

    def _accept_message(self, message):
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise McpError("invalid_rpc_message")
        if "method" in message:
            if "id" in message or "result" in message or "error" in message:
                raise McpError("unexpected_server_request")
            method = message["method"]
            if not isinstance(method, str) or len(self.notification_methods) >= 1000:
                raise McpError("notification_limit")
            self.notification_methods.append(method)
            for feature in self._catalog_generations:
                if method == f"notifications/{feature}/list_changed":
                    self._catalog_generations[feature] += 1
                    getattr(self, "_" + feature).clear()
            return None
        request_id = message.get("id")
        if type(request_id) is not int or request_id not in self._pending:
            raise McpError("uncorrelated_response")
        pending = self._pending[request_id]
        if pending.future.done() or ("result" in message) == ("error" in message):
            raise McpError("invalid_rpc_message")
        pending.future.set_result(message)
        return request_id

    async def _listen(self, exchange, endpoint_ready, legacy):
        try:
            async for event, data in exchange.events():
                if event == "endpoint" and legacy:
                    if endpoint_ready.is_set():
                        raise McpError("sse_endpoint_changed")
                    target = self.network.url(urljoin(self.endpoint, data))
                    if target.origin != self.network.url(self.endpoint).origin:
                        raise McpError("sse_cross_origin_endpoint")
                    self._post_endpoint = target.canonical
                    endpoint_ready.set()
                elif event == "message":
                    self._accept_message(
                        _json_data(json.loads(data, object_pairs_hook=_object_pairs))
                    )
                else:
                    raise McpError("sse_event_invalid")
            if not self._http_closing:
                raise McpError("sse_connection_lost")
        except asyncio.CancelledError:
            raise
        except (McpError, WebError, ValueError, OSError, UnicodeError):
            self._failure = "sse_connection_lost"
            endpoint_ready.set()
            for pending in self._pending.values():
                if not pending.future.done():
                    pending.future.set_exception(McpError(self._failure))
        finally:
            await exchange.close()
            self.network.release(exchange)

    async def _request(self, method, params, *, effect=False, ready=True, before_dispatch=None):
        if ready and not self.connected:
            raise McpError(self._failure or "client_not_ready")
        if self._http_closing or self._failure or not _METHOD.fullmatch(method):
            raise McpError(self._failure or "client_closed")
        if len(self._pending) >= 32:
            raise McpError("request_capacity")
        params = _json_data(params)
        if self.protocol_version == MODERN_VERSION:
            params["_meta"] = {
                "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
                "io.modelcontextprotocol/clientInfo": {"name": "Jarvis", "version": "0.1.0a1"},
                "io.modelcontextprotocol/clientCapabilities": _json_data(
                    getattr(self, "client_capabilities", {})
                ),
            }
        self._counter += 1
        identity = self._counter
        from jarvis_core.mcp import _Pending

        pending = _Pending(asyncio.get_running_loop().create_future(), effect)
        self._pending[identity] = pending
        owner = asyncio.current_task()
        self._active.add(owner)
        exchange = None
        try:
            async with asyncio.timeout(self.request_timeout):

                def dispatch():
                    if not self._http_closing and not self._failure:
                        if before_dispatch is not None:
                            before_dispatch()
                        pending.dispatched = True
                    else:
                        raise McpError("client_closed")

                body = json.dumps(
                    {"jsonrpc": "2.0", "id": identity, "method": method, "params": params},
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
                exchange = await self.network.open(
                    self._post_endpoint,
                    method="POST",
                    body=body,
                    headers=self._headers(method, params),
                    before_dispatch=dispatch,
                    credential=self._credential(),
                    maximum=self.max_line_bytes,
                )
                if self.protocol_version == LEGACY_VERSION and method == "initialize":
                    session = exchange.headers.get("mcp-session-id")
                    if session is not None and not _SESSION.fullmatch(session):
                        raise McpError("invalid_session_id")
                    self._session_id = session
                if exchange.status == 401:
                    raise McpHTTPError(
                        "http_authorization_required",
                        status=401,
                        challenge=exchange.headers.get("www-authenticate"),
                        effect_status=pending.effect_status,
                    )
                if 300 <= exchange.status < 400:
                    raise McpHTTPError(
                        "http_redirect_rejected", effect_status=pending.effect_status
                    )
                if self.transport == "sse":
                    if exchange.status not in {200, 202}:
                        raise McpHTTPError(
                            "http_request_rejected", effect_status=pending.effect_status
                        )
                    await exchange.body()
                    message = await pending.future
                else:
                    if exchange.status != 200:
                        raise McpHTTPError(
                            "http_request_rejected",
                            status=exchange.status,
                            effect_status=pending.effect_status,
                        )
                    content = exchange.headers.get("content-type", "").split(";", 1)[0].strip()
                    if content == "application/json":
                        message = _json_data(
                            json.loads(await exchange.body(), object_pairs_hook=_object_pairs)
                        )
                        self._accept_message(message)
                    elif content == "text/event-stream":
                        async for event, data in exchange.events():
                            if event != "message":
                                raise McpError("sse_event_invalid")
                            self._accept_message(
                                _json_data(json.loads(data, object_pairs_hook=_object_pairs))
                            )
                            if pending.future.done():
                                break
                    else:
                        raise McpError("http_content_type_invalid")
                    if not pending.future.done():
                        raise McpError("http_response_missing")
                    message = pending.future.result()
                if "error" in message:
                    error = message["error"]
                    if not isinstance(error, dict) or type(error.get("code")) is not int:
                        raise McpError("invalid_rpc_message", effect_status=pending.effect_status)
                    raise McpError(
                        "rpc_error", effect_status=pending.effect_status, rpc_code=error["code"]
                    )
                result = message.get("result")
                if not isinstance(result, dict):
                    raise McpError("invalid_rpc_message", effect_status=pending.effect_status)
                if result.get("resultType") == "input_required":
                    raise McpInputRequired(result, pending.effect_status)
                return result
        except asyncio.CancelledError:
            raise McpCancelled(pending.effect_status) from None
        except (
            TimeoutError,
            OSError,
            ValueError,
            WebError,
            UnicodeError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            RecursionError,
        ) as error:
            self._failure = "http_transport_lost"
            raise McpHTTPError(self._failure, effect_status=pending.effect_status) from error
        finally:
            self._pending.pop(identity, None)
            self._active.discard(owner)
            if exchange is not None:
                try:
                    await asyncio.shield(exchange.close())
                except McpHTTPError:
                    raise McpHTTPError(
                        "http_cleanup_pending", effect_status=pending.effect_status
                    ) from None
                self.network.release(exchange)

    async def list_tools(self):
        if self.protocol_version != MODERN_VERSION:
            return await super().list_tools()
        self._tools.clear()
        self._catalog_generations["tools"] += 1
        generation = self._catalog_generations["tools"]
        entries = await self._list("tools", "tools")
        tools, all_paths = {}, {}
        for item in entries:
            name, schema = item.get("name"), item.get("inputSchema")
            if (
                not isinstance(name, str)
                or not 1 <= len(name.encode()) <= 128
                or any(ord(c) < 32 or ord(c) == 127 for c in name)
                or name in tools
                or not isinstance(schema, dict)
            ):
                raise McpError("invalid_tool_schema")
            paths = []
            cleaned = json.loads(json.dumps(schema))

            def walk(node, path=(), static=True, paths=paths):
                if not isinstance(node, dict):
                    return
                header = node.pop("x-mcp-header", None)
                if header is not None:
                    if (
                        not static
                        or not isinstance(header, str)
                        or not _TOKEN.fullmatch(header)
                        or len(header) > 128
                        or node.get("type") not in {"string", "boolean", "integer"}
                        or header.lower() in {p[0].lower() for p in paths}
                    ):
                        raise McpError("invalid_header_annotation")
                    paths.append((header, path, node["type"]))
                for key, value in node.items():
                    if key == "properties" and isinstance(value, dict):
                        for child, shape in value.items():
                            walk(shape, (*path, child), static)
                    elif isinstance(value, dict):
                        walk(value, path, False)
                    elif isinstance(value, list):
                        for child in value:
                            walk(child, path, False)

            try:
                walk(cleaned)
            except McpError:
                continue  # Modern spec excludes malformed annotated tools.
            _schema(cleaned)
            if cleaned.get("type") != "object":
                raise McpError("invalid_tool_schema")
            description = item.get("description", "")
            if not isinstance(description, str):
                raise McpError("invalid_tool_description")
            metadata = _json_data(item.get("_meta", {}))
            if not isinstance(metadata, dict) or len(json.dumps(metadata).encode()) > 32768:
                raise McpError("invalid_tool_metadata")
            tools[name] = McpTool(name, description, _freeze(cleaned), _freeze(metadata))
            all_paths[name] = paths
        if generation != self._catalog_generations["tools"]:
            raise McpError("catalog_changed")
        self._tools, self._header_paths = tools, all_paths
        return tuple(tools.values())

    async def aclose(self, *, retry=True):
        if (
            self._http_close_task is not None
            and self._http_close_task.done()
            and not self._http_close_task.cancelled()
            and self._http_close_task.exception() is not None
            and retry
        ):
            self._http_close_task = None
        if self._http_close_task is None:
            self._http_close_task = asyncio.create_task(self._close_http())
        return await asyncio.shield(self._http_close_task)

    async def _close_http(self):
        self._http_closing = True
        self._ready = False
        current = asyncio.current_task()
        targets = tuple(task for task in self._active if task is not current)
        for task in targets:
            task.cancel()
        if self._events_task is not None:
            self._events_task.cancel()
            targets += (self._events_task,)
        if targets:
            await asyncio.gather(*targets, return_exceptions=True)
        remote = "not_applicable"
        if self._session_id is not None and not self._session_delete_attempted:
            session = self._session_id
            self._session_delete_attempted = True
            try:
                status, _, _ = await self.network.request(
                    self.endpoint,
                    method="DELETE",
                    headers={
                        "Mcp-Session-Id": session,
                        "MCP-Protocol-Version": self.protocol_version,
                    },
                    credential=self._credential(),
                    timeout=self.shutdown_timeout,
                )
                if status not in {200, 202, 204, 404, 405}:
                    raise McpError("http_session_cleanup_pending")
                remote = "confirmed" if status != 405 else "unknown"
            except Exception:
                remote = "unknown"
            if remote == "confirmed":
                self._session_id = None
        elif self._session_id is not None:
            remote = "unknown"
        await self.network.aclose()
        self._failure = self._failure or "client_closed"
        self.close_receipt = {
            "closed": True,
            "cleanup_confirmed": True,
            "local_cleanup_confirmed": True,
            "remote_session_termination": remote,
            "remote_effects_may_continue": True,
        }
        return self.close_receipt
