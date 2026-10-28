"""Own loopback HTTP peers; not an external MCP service conformance claim."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from jarvis_core.mcp import (
    HTTP_COMPAT_VERSION,
    LEGACY_VERSION,
    MODERN_VERSION,
    McpCancelled,
    McpError,
)
from jarvis_core.mcp_http import (
    McpHttpClient,
    McpHTTPError,
    McpHTTPNetwork,
    header_value,
    read_headers,
)
from jarvis_core.web import WebError


class HTTPFixture:
    def __init__(self, handler):
        self.handler, self.requests = handler, []
        self.connections = {}
        self.writers = set()
        self.server, self.base = None, None

    async def __aenter__(self):
        self.server = await asyncio.start_server(self._accept, "127.0.0.1", 0)
        self.base = "http://127.0.0.1:" + str(self.server.sockets[0].getsockname()[1])
        return self

    async def __aexit__(self, *errors):
        self.server.close()
        await self.server.wait_closed()
        tasks = tuple(self.connections)
        for writer in tuple(self.writers):
            writer.close()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # A callback can finish/cancel while awaiting its writer finalizer. Its
        # done callback retires the task, never the independent socket owner.
        for writer in tuple(self.writers):
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), 1)
            self.writers.discard(writer)
        assert not self.connections and not self.writers

    def _accept(self, reader, writer):
        task = asyncio.create_task(self._handle(reader, writer))
        self.connections[task] = writer
        self.writers.add(writer)
        task.add_done_callback(lambda done: self.connections.pop(done, None))

    async def _handle(self, reader, writer):
        task = asyncio.current_task()
        try:
            async with asyncio.timeout(5):
                raw = await reader.readuntil(b"\r\n\r\n")
                lines = raw[:-4].decode().split("\r\n")
                method, target, _ = lines[0].split()
                headers = dict(
                    (key.lower(), value.strip())
                    for key, value in (line.split(":", 1) for line in lines[1:])
                )
                body = await reader.readexactly(int(headers.get("content-length", "0")))
                request = {"method": method, "target": target, "headers": headers, "body": body}
                self.requests.append(request)
                response = await self.handler(request, reader, writer)
                if response is not None:
                    status, fields, payload = response
                    if isinstance(payload, dict):
                        payload = json.dumps(payload, ensure_ascii=False).encode()
                    fields = {
                        "Content-Type": "application/json",
                        "Content-Length": str(len(payload)),
                        "Connection": "close",
                        **fields,
                    }
                    writer.write(
                        f"HTTP/1.1 {status} Result\r\n".encode()
                        + "".join(f"{key}: {value}\r\n" for key, value in fields.items()).encode()
                        + b"\r\n"
                        + payload
                    )
                    await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            self.writers.discard(writer)
            self.connections.pop(task, None)


def reply(message, result):
    return {"jsonrpc": "2.0", "id": message["id"], "result": result}


def catalog():
    return [
        {
            "name": "echo",
            "description": "Reporte externo sintético",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
        }
    ]


async def parse_response_headers(lines):
    reader = asyncio.StreamReader()
    reader.feed_data(b"HTTP/1.1 200 OK\r\n" + b"\r\n".join(lines) + b"\r\n\r\n")
    reader.feed_eof()
    return await read_headers(reader)


async def test_repeated_set_cookie_is_validated_then_discarded_instead_of_folded():
    status, headers = await parse_response_headers(
        [
            b"Content-Type: application/json",
            b"Set-Cookie: synthetic_a=ONE; Expires=Wed, 28 Oct 2026 19:00:00 GMT; Secure",
            b"set-cookie: synthetic_b=TWO; HttpOnly",
            b"SET-COOKIE: synthetic_c=THREE; SameSite=Strict",
            b"MCP-Session-Id: reviewed-session",
            b"Content-Length: 2",
        ]
    )
    assert status == 200
    assert headers == {
        "content-type": "application/json",
        "mcp-session-id": "reviewed-session",
        "content-length": "2",
    }
    assert "synthetic_" not in repr(headers)


@pytest.mark.parametrize(
    "name,first,second",
    [
        ("Content-Length", "2", "2"),
        ("Content-Length", "2", "3"),
        ("Transfer-Encoding", "chunked", "chunked"),
        ("Content-Type", "application/json", "text/event-stream"),
        ("Content-Encoding", "identity", "gzip"),
        ("Mcp-Session-Id", "one", "two"),
        ("MCP-Protocol-Version", "2025-06-18", "2026-07-28"),
        ("Location", "https://example.com/a", "https://example.com/b"),
        ("WWW-Authenticate", 'Bearer realm="one"', 'Bearer realm="two"'),
    ],
)
async def test_cookie_compatibility_preserves_other_header_rejection_rules(name, first, second):
    with pytest.raises(McpHTTPError, match="http_duplicate_header"):
        await parse_response_headers(
            [
                b"Set-Cookie: ignored=synthetic",
                (name + ": " + first).encode(),
                (name.lower() + ": " + second).encode(),
            ]
        )


@pytest.mark.parametrize(
    "line",
    [
        b"Set-Cookie : ignored=synthetic",
        b"\tSet-Cookie: ignored=synthetic",
        b"Set-Cookie: ignored=synthetic\x00",
    ],
)
async def test_ignored_cookie_does_not_bypass_header_syntax_validation(line):
    with pytest.raises(WebError, match="web_headers_invalid"):
        await parse_response_headers([line])


async def test_discarded_cookie_lines_still_count_towards_header_limits_and_framing():
    with pytest.raises(McpHTTPError, match="http_response_invalid"):
        await parse_response_headers([b"Set-Cookie: ignored=synthetic"] * 101)
    with pytest.raises(McpHTTPError, match="http_header_limit"):
        await parse_response_headers([b"Set-Cookie: ignored=" + b"x" * 32768])
    with pytest.raises(McpHTTPError, match="http_response_invalid"):
        await parse_response_headers(
            [
                b"Set-Cookie: ignored=synthetic",
                b"Content-Length: 2",
                b"Transfer-Encoding: chunked",
            ]
        )


async def test_three_response_cookies_are_not_reused_by_mcp_transport(tmp_path):
    async def handler(request, _reader, writer):
        assert "cookie" not in request["headers"]
        message = json.loads(request["body"])
        if message["method"] == "notifications/initialized":
            return 202, {}, b""
        result = (
            {
                "protocolVersion": HTTP_COMPAT_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "independent-cookie-peer", "version": "1"},
            }
            if message["method"] == "initialize"
            else {"tools": catalog()}
        )
        payload = json.dumps(reply(message, result)).encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b"Set-Cookie: own_a=ONE; HttpOnly\r\nset-cookie: own_b=TWO; Secure\r\n"
            b"SET-COOKIE: own_c=THREE; Expires=Wed, 28 Oct 2026 19:00:00 GMT\r\n"
            + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
            + payload
        )
        await writer.drain()
        return None

    async with HTTPFixture(handler) as remote:
        client = McpHttpClient(
            remote.base + "/mcp",
            tmp_path.resolve() / "cookie-free-client",
            protocol_version=HTTP_COMPAT_VERSION,
            test_origins=(remote.base,),
        )
        try:
            await client.start()
            assert len(await client.list_tools()) == 1
            assert len(remote.requests) == 3
            assert all("cookie" not in request["headers"] for request in remote.requests)
        finally:
            assert (await client.aclose())["local_cleanup_confirmed"]


@pytest.mark.parametrize("protocol", [HTTP_COMPAT_VERSION, LEGACY_VERSION, MODERN_VERSION])
async def test_http_json_and_sse_reply_resource_prompt_and_exact_effect_guard(tmp_path, protocol):
    effects, committed = [], []

    async def handler(request, reader, writer):
        if request["method"] == "DELETE":
            return 204, {}, b""
        message = json.loads(request["body"])
        method, params = message["method"], message.get("params", {})
        if protocol == MODERN_VERSION:
            assert request["headers"]["mcp-method"] == method
            assert params["_meta"]["io.modelcontextprotocol/protocolVersion"] == protocol
        else:
            assert request["headers"]["mcp-protocol-version"] == protocol
            assert "mcp-method" not in request["headers"] and "_meta" not in params
        if method == "notifications/initialized":
            return 202, {}, b""
        result = (
            {
                "protocolVersion": protocol,
                "serverInfo": {"name": "own", "version": "1"},
                "supportedVersions": [MODERN_VERSION],
                "ttlMs": 0,
                "cacheScope": "private",
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            }
            if method in {"initialize", "server/discover"}
            else {"tools": catalog()}
            if method == "tools/list"
            else {"resources": [{"uri": "fixture://item", "name": "item"}]}
            if method == "resources/list"
            else {"contents": [{"uri": "fixture://item", "text": "DATO EXTERNO"}]}
            if method == "resources/read"
            else {"prompts": [{"name": "own_prompt"}]}
            if method == "prompts/list"
            else {
                "messages": [{"role": "user", "content": {"type": "text", "text": "no autoridad"}}]
            }
            if method == "prompts/get"
            else {"content": [{"type": "text", "text": params["arguments"]["text"]}]}
        )
        if method == "tools/call":
            effects.append(params["arguments"])
            assert committed == ["once"]
            payload = json.dumps(reply(message, result)).encode()
            return (
                200,
                {"Content-Type": "text/event-stream"},
                b": keep-alive\n\nevent: message\ndata: " + payload + b"\n\n",
            )
        return (
            200,
            ({"Mcp-Session-Id": "own_session"} if method == "initialize" else {}),
            reply(message, result),
        )

    async with HTTPFixture(handler) as fixture:
        client = McpHttpClient(
            fixture.base + "/mcp",
            tmp_path.resolve() / "client",
            protocol_version=protocol,
            test_origins=(fixture.base,),
            authorize=lambda *_args: asyncio.sleep(0, result=True),
        )
        try:
            await client.start()
            assert client.connected and len(await client.list_tools()) == 1
            await client.list_resources()
            assert (await client.read_resource("fixture://item"))["contents"][0][
                "text"
            ] == "DATO EXTERNO"
            await client.list_prompts()
            assert (await client.get_prompt("own_prompt"))["messages"]
            value = await client.call_tool(
                "echo", {"text": "MARCADOR"}, dispatch_guard=lambda: committed.append("once")
            )
            assert value.verified is False and value.effect_status == "reported"
            assert effects == [{"text": "MARCADOR"}]
        finally:
            closed = await client.aclose()
            assert closed["local_cleanup_confirmed"] and not client.connected


@pytest.mark.parametrize("protocol", [HTTP_COMPAT_VERSION, LEGACY_VERSION])
@pytest.mark.parametrize("length", [128, 129, 220, 8191])
async def test_visible_ascii_opaque_session_is_reused_without_truncation(
    tmp_path, protocol, length
):
    # A public MCP peer returned a 220-byte opaque identifier. The spec permits
    # every visible ASCII character, rather than only token/UUID alphabets.
    alphabet = "opaque:/+={}[]!~"
    session = (alphabet * ((length // len(alphabet)) + 1))[:length]

    async def handler(request, _reader, _writer):
        if request["method"] == "DELETE":
            assert request["headers"]["mcp-session-id"] == session
            return 204, {}, b""
        message = json.loads(request["body"])
        method = message["method"]
        if method == "initialize":
            assert "mcp-session-id" not in request["headers"]
            return (
                200,
                {"Mcp-Session-Id": session},
                reply(
                    message,
                    {
                        "protocolVersion": protocol,
                        "serverInfo": {"name": "own", "version": "1"},
                        "capabilities": {"tools": {}},
                    },
                ),
            )
        assert request["headers"]["mcp-session-id"] == session
        if method == "notifications/initialized":
            return 202, {}, b""
        assert method == "tools/list"
        return 200, {}, reply(message, {"tools": catalog()})

    async with HTTPFixture(handler) as fixture:
        client = McpHttpClient(
            fixture.base + "/mcp",
            tmp_path.resolve() / "client",
            protocol_version=protocol,
            test_origins=(fixture.base,),
        )
        try:
            await client.start()
            assert len(await client.list_tools()) == 1
        finally:
            closed = await client.aclose()
        assert closed["local_cleanup_confirmed"]
        assert closed["remote_session_termination"] == "confirmed"
        assert len(fixture.requests) == 4
        assert [request["method"] for request in fixture.requests] == [
            "POST", "POST", "POST", "DELETE"
        ]


@pytest.mark.parametrize(
    "session",
    ["", "with space", "with\ttab", "control\x1f", "delete\x7f", "nonascii\u00e9", "x" * 8192],
    ids=["empty", "space", "tab", "control", "delete", "non-ascii", "http-field-limit"],
)
async def test_invalid_or_oversized_session_is_not_reused_or_retried(tmp_path, session):
    async def handler(request, _reader, _writer):
        message = json.loads(request["body"])
        return (
            200,
            {"Mcp-Session-Id": session},
            reply(
                message,
                {
                    "protocolVersion": HTTP_COMPAT_VERSION,
                    "serverInfo": {"name": "own", "version": "1"},
                    "capabilities": {"tools": {}},
                },
            ),
        )

    async with HTTPFixture(handler) as fixture:
        client = McpHttpClient(
            fixture.base + "/mcp",
            tmp_path.resolve() / "client",
            protocol_version=HTTP_COMPAT_VERSION,
            test_origins=(fixture.base,),
        )
        with pytest.raises(McpError):
            await client.start()
        closed = await client.aclose()
        assert closed["local_cleanup_confirmed"]
        assert not client.connected and client._session_id is None
        assert len(fixture.requests) == 1
        request = fixture.requests[0]
        assert json.loads(request["body"])["method"] == "initialize"
        assert "mcp-session-id" not in request["headers"]


@pytest.mark.parametrize("delete_status", [204, 503])
@pytest.mark.parametrize("protocol", [HTTP_COMPAT_VERSION, LEGACY_VERSION])
async def test_2025_get_stream_session_delete_and_remote_unknown_do_not_replay(
    tmp_path, delete_status, protocol
):
    async def handler(request, reader, writer):
        if request["method"] == "GET":
            assert request["headers"]["mcp-session-id"] == "session"
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n: ready\n\n")
            await writer.drain()
            await reader.read()
            return None
        if request["method"] == "DELETE":
            return delete_status, {}, b""
        message = json.loads(request["body"])
        if message["method"] == "notifications/initialized":
            return 202, {}, b""
        return (
            200,
            {"Mcp-Session-Id": "session"},
            reply(
                message,
                {
                    "protocolVersion": protocol,
                    "serverInfo": {"name": "own", "version": "1"},
                    "capabilities": {"tools": {"listChanged": True}},
                },
            ),
        )

    async with HTTPFixture(handler) as fixture:
        client = McpHttpClient(
            fixture.base + "/mcp",
            tmp_path.resolve() / "client",
            protocol_version=protocol,
            test_origins=(fixture.base,),
        )
        await client.start()
        first = await client.aclose()
        second = await client.aclose()
        assert first == second and first["local_cleanup_confirmed"]
        assert first["remote_session_termination"] == (
            "confirmed" if delete_status == 204 else "unknown"
        )
        assert [r["method"] for r in fixture.requests].count("DELETE") == 1
        assert [r["method"] for r in fixture.requests].count("GET") == 1


@pytest.mark.parametrize("cancel", [False, True])
async def test_effect_lost_ack_or_cancel_is_unknown_and_not_retried(tmp_path, cancel):
    entered = asyncio.Event()
    effects = []

    async def handler(request, reader, writer):
        if request["method"] == "DELETE":
            return 204, {}, b""
        message = json.loads(request["body"])
        if message["method"] == "notifications/initialized":
            return 202, {}, b""
        if message["method"] == "initialize":
            return (
                200,
                {},
                reply(
                    message,
                    {
                        "protocolVersion": LEGACY_VERSION,
                        "serverInfo": {"name": "own", "version": "1"},
                        "capabilities": {"tools": {}},
                    },
                ),
            )
        if message["method"] == "tools/list":
            return 200, {}, reply(message, {"tools": catalog()})
        effects.append(True)
        entered.set()
        if cancel:
            await reader.read()
        return None

    async with HTTPFixture(handler) as fixture:
        client = McpHttpClient(
            fixture.base + "/mcp",
            tmp_path.resolve() / "client",
            test_origins=(fixture.base,),
            authorize=lambda *_a: asyncio.sleep(0, result=True),
        )
        try:
            await client.start()
            await client.list_tools()
            task = asyncio.create_task(client.call_tool("echo", {"text": "once"}))
            await entered.wait()
            if cancel:
                task.cancel()
            with pytest.raises((McpError, McpCancelled)) as outcome:
                await task
            assert outcome.value.effect_status == "unknown" and effects == [True]
            assert not client._pending
        finally:
            assert (await client.aclose())["local_cleanup_confirmed"]


async def test_url_policy_and_mirrored_header_encoding_are_explicit(tmp_path):
    network = McpHTTPNetwork()
    for value in (
        "http://127.0.0.1:8080/mcp",
        "https://127.0.0.1/mcp",
        "https://user:secret@example.com/mcp",
        "https://example.com/mcp#fragment",
    ):
        with pytest.raises(McpHTTPError):
            network.url(value, config=True)
    assert header_value("plain") == "plain"
    assert header_value("España").startswith("=?base64?")
    assert header_value("=?base64?literal?=").startswith("=?base64?")
    assert not Path(tmp_path / "client").exists()
    await network.aclose()


async def test_explicit_legacy_sse_2024_uses_only_advertised_same_origin_post(tmp_path):
    stream = {}
    effects = []

    async def handler(request, reader, writer):
        if request["method"] == "GET":
            stream["writer"] = writer
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n"
                b"event: endpoint\ndata: /messages?session=synthetic\n\n"
            )
            await writer.drain()
            await reader.read()
            return None
        assert request["target"] == "/messages?session=synthetic"
        message = json.loads(request["body"])
        if "id" in message:
            method = message["method"]
            result = (
                {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "own", "version": "1"},
                    "capabilities": {"tools": {}},
                }
                if method == "initialize"
                else {"tools": catalog()}
                if method == "tools/list"
                else {"content": [{"type": "text", "text": "external"}]}
            )
            if method == "tools/call":
                effects.append(True)
            stream["writer"].write(
                b"event: message\ndata: " + json.dumps(reply(message, result)).encode() + b"\n\n"
            )
            await stream["writer"].drain()
        return 202, {}, b""

    async with HTTPFixture(handler) as fixture:
        client = McpHttpClient(
            fixture.base + "/sse",
            tmp_path.resolve() / "client",
            transport="sse",
            protocol_version="2024-11-05",
            test_origins=(fixture.base,),
            authorize=lambda *_a: asyncio.sleep(0, result=True),
        )
        try:
            await client.start()
            await client.list_tools()
            assert (await client.call_tool("echo", {"text": "once"})).verified is False
            assert effects == [True]
        finally:
            assert (await client.aclose())["local_cleanup_confirmed"]


async def test_http_final_guard_after_connector_await_prevents_first_byte(tmp_path):
    connected, release = asyncio.Event(), asyncio.Event()
    revoked = False

    async def handler(request, reader, writer):
        raise AssertionError("No request may reach the peer after revocation")

    async with HTTPFixture(handler) as fixture:

        async def connect(address, url):
            streams = await asyncio.open_connection(address, url.port)
            connected.set()
            await release.wait()
            return streams

        def guard():
            if revoked:
                raise McpHTTPError("revoked_before_dispatch")

        network = McpHTTPNetwork(
            test_origins=(fixture.base,), connector=connect, before_effect=guard
        )
        request = asyncio.create_task(network.request(fixture.base + "/mcp"))
        await asyncio.wait_for(connected.wait(), 2)
        revoked = True
        release.set()
        with pytest.raises(McpHTTPError, match="revoked_before_dispatch"):
            await request
        assert not fixture.requests
        assert (await network.aclose())["cleanup_confirmed"] and not network.blockers


async def test_modern_subscription_ack_and_change_invalidates_exact_catalog(tmp_path):
    opened, send_change, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def handler(request, reader, writer):
        message = json.loads(request["body"])
        if message["method"] == "server/discover":
            return (
                200,
                {},
                reply(
                    message,
                    {
                        "supportedVersions": [MODERN_VERSION],
                        "ttlMs": 0,
                        "cacheScope": "private",
                        "serverInfo": {"name": "own", "version": "1"},
                        "capabilities": {"tools": {"listChanged": True}},
                    },
                ),
            )
        if message["method"] == "subscriptions/listen":
            assert message["params"]["notifications"] == {"toolsListChanged": True}
            assert request["headers"]["mcp-method"] == "subscriptions/listen"
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n"
            )

            def event(method, params):
                return (
                    b"event: message\ndata: "
                    + json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "method": method,
                            "params": {
                                **params,
                                "_meta": {"io.modelcontextprotocol/subscriptionId": message["id"]},
                            },
                        }
                    ).encode()
                    + b"\n\n"
                )

            writer.write(
                event(
                    "notifications/subscriptions/acknowledged",
                    {"notifications": {"toolsListChanged": True}},
                )
            )
            await writer.drain()
            opened.set()
            await send_change.wait()
            writer.write(event("notifications/tools/list_changed", {}))
            await writer.drain()
            finish.set()
            await reader.read()
            return None
        if message["method"] == "tools/list":
            return 200, {}, reply(message, {"tools": catalog()})
        raise AssertionError(message["method"])

    async with HTTPFixture(handler) as fixture:
        client = McpHttpClient(
            fixture.base + "/mcp",
            tmp_path.resolve() / "modern",
            protocol_version=MODERN_VERSION,
            test_origins=(fixture.base,),
        )
        try:
            await client.start()
            await opened.wait()
            assert len(await client.list_tools()) == 1
            initial = client._catalog_generations["tools"]
            send_change.set()
            await finish.wait()
            for _ in range(100):
                if client._catalog_generations["tools"] != initial:
                    break
                await asyncio.sleep(0.001)
            assert not client._tools and client._catalog_generations["tools"] == initial + 1
            assert client.notification_methods == [
                "notifications/subscriptions/acknowledged",
                "notifications/tools/list_changed",
            ]
        finally:
            await client.aclose()
