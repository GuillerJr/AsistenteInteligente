"""Own independent HTTP wire peers for loopback relay authority and custody."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit

import pytest

from jarvis_core.mcp import LEGACY_VERSION, MODERN_VERSION
from jarvis_core.mcp_http import McpHttpClient, McpHTTPNetwork
from jarvis_core.mcp_server import McpRelayError, McpRelayServer


async def wire(relay, message, *, protocol=MODERN_VERSION, extra=None, token=None):
    endpoint = urlsplit(relay.endpoint)
    body = json.dumps(message, ensure_ascii=False).encode()
    fields = {
        "Host": endpoint.netloc,
        "Authorization": "Bearer " + (token or relay.pairing_credential()["bearer_token"]),
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
        "MCP-Protocol-Version": protocol,
    }
    if protocol == MODERN_VERSION:
        fields["Mcp-Method"] = message["method"]
        if message["method"] in {"tools/call", "resources/read", "prompts/get"}:
            fields["Mcp-Name"] = message["params"].get("name", message["params"].get("uri"))
    fields.update(extra or {})
    reader, writer = await asyncio.open_connection("127.0.0.1", endpoint.port)
    writer.write(
        b"POST /mcp HTTP/1.1\r\n"
        + "".join(f"{key}: {value}\r\n" for key, value in fields.items()).encode()
        + b"\r\n"
        + body
    )
    await writer.drain()
    try:
        raw = await reader.read()
        head, body = raw.split(b"\r\n\r\n", 1)
        return int(head.split()[1]), json.loads(body) if body else None
    finally:
        writer.close()
        await writer.wait_closed()


def message(identity, method, params=None):
    return {
        "jsonrpc": "2.0",
        "id": identity,
        "method": method,
        "params": {
            **(params or {}),
            "_meta": {"io.modelcontextprotocol/protocolVersion": MODERN_VERSION},
        },
    }


@pytest.mark.parametrize("protocol", [LEGACY_VERSION, MODERN_VERSION])
async def test_relay_explicit_eras_tools_resources_and_prompt_roundtrip(tmp_path, protocol):
    called = []

    async def dispatch(method, params, context):
        context.assert_current()
        called.append(method)
        return (
            {
                "tools": [
                    {
                        "name": "own_echo",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"],
                            "additionalProperties": False,
                        },
                    }
                ]
            }
            if method == "tools/list"
            else {"content": [{"type": "text", "text": params["arguments"]["text"]}]}
            if method == "tools/call"
            else {"resources": [{"uri": "fixture://note", "name": "note"}]}
            if method == "resources/list"
            else {"contents": [{"uri": "fixture://note", "text": "fixture-only"}]}
            if method == "resources/read"
            else {"prompts": [{"name": "own_prompt"}]}
            if method == "prompts/list"
            else {"messages": [{"role": "user", "content": {"type": "text", "text": "data"}}]}
        )

    relay = McpRelayServer(dispatch, capabilities={"tools": {}, "resources": {}, "prompts": {}})
    await relay.start()
    secret = relay.pairing_credential()

    class Lease:
        resource = relay.endpoint
        authorization_header = "Bearer " + secret["bearer_token"]

        def assert_current(self):
            assert relay.status()["running"]

    async def authorize(*args):
        return True

    client = McpHttpClient(
        relay.endpoint,
        tmp_path,
        protocol_version=protocol,
        allow_loopback=True,
        authorize=authorize,
        credentials=lambda _: Lease(),
    )
    try:
        await client.start()
        await client.list_tools()
        assert (await client.call_tool("own_echo", {"text": "own marker"})).payload["content"][0][
            "text"
        ] == "own marker"
        await client.list_resources()
        assert (await client.read_resource("fixture://note"))["contents"][0][
            "text"
        ] == "fixture-only"
        await client.list_prompts()
        assert (await client.get_prompt("own_prompt"))["messages"]
        assert called.count("tools/call") == 1
        assert "bearer_token" not in json.dumps(relay.status())
    finally:
        assert (await client.aclose())["local_cleanup_confirmed"]
        assert (await relay.aclose())["cleanup_confirmed"]
        assert not relay.blockers


async def test_relay_independent_wire_replay_window_conflict_and_header_origin_auth():
    effects = []

    async def dispatch(method, params, context):
        context.assert_current()
        effects.append(dict(params))
        return {"content": [{"type": "text", "text": "done"}]}

    relay = McpRelayServer(dispatch)
    await relay.start()
    request = message("same", "tools/call", {"name": "own", "arguments": {"n": 1}})
    try:
        assert (await wire(relay, request))[0] == 200
        assert (await wire(relay, request))[1]["result"]
        assert len(effects) == 1
        changed = message("same", "tools/call", {"name": "own", "arguments": {"n": 2}})
        assert (await wire(relay, changed))[1]["error"]["code"] == -32012
        assert (await wire(relay, request, token="not-authorized"))[0] == 401
        assert (await wire(relay, request, extra={"Origin": "https://unapproved.example"}))[
            0
        ] == 403
        assert (await wire(relay, request, extra={"Host": "attacker.example"}))[0] == 403
        status, result = await wire(relay, request, extra={"Mcp-Name": "wrong"})
        assert status == 400 and result["error"]["code"] == -32020
        assert len(effects) == 1
    finally:
        await relay.aclose()


async def test_relay_disconnect_retains_host_callback_until_actual_cleanup():
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    async def dispatch(method, params, context):
        calls.append(method)
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            while not release.is_set():
                try:
                    await asyncio.shield(release.wait())
                except asyncio.CancelledError:
                    pass

    relay = McpRelayServer(dispatch)
    await relay.start()
    task = asyncio.create_task(wire(relay, message(9, "tools/call", {"name": "held"})))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.wait_for(cleaning.wait(), 2)
        closing = asyncio.create_task(relay.aclose())
        await asyncio.sleep(0.02)
        assert not closing.done() and relay.blockers
        release.set()
        assert (await asyncio.wait_for(closing, 2))["cleanup_confirmed"]
        assert calls == ["tools/call"] and not relay.blockers
        assert relay.status()["uncertain_requests"] == 1
    finally:
        release.set()
        await relay.aclose()


async def test_relay_modern_subscription_ack_exact_filter_and_revoke():
    async def dispatch(*args):
        raise AssertionError("Subscriptions do not execute the host tool dispatcher")

    relay = McpRelayServer(
        dispatch, capabilities={"tools": {"listChanged": True}, "resources": {"listChanged": True}}
    )
    await relay.start()
    network = McpHTTPNetwork(allow_loopback=True)

    class Lease:
        resource = relay.endpoint
        authorization_header = "Bearer " + relay.pairing_credential()["bearer_token"]

        def assert_current(self):
            pass

    try:
        body = message(12, "subscriptions/listen", {"notifications": {"toolsListChanged": True}})
        stream = await network.open(
            relay.endpoint,
            method="POST",
            credential=Lease(),
            headers={
                "Content-Type": "application/json",
                "MCP-Protocol-Version": MODERN_VERSION,
                "Mcp-Method": "subscriptions/listen",
            },
            body=json.dumps(body).encode(),
        )
        events = stream.events()
        first = json.loads((await anext(events))[1])
        assert first["method"] == "notifications/subscriptions/acknowledged"
        assert first["params"]["notifications"] == {"toolsListChanged": True}
        await relay.notify("notifications/resources/list_changed")
        await relay.notify("notifications/tools/list_changed")
        second = json.loads((await anext(events))[1])
        assert second["method"] == "notifications/tools/list_changed"
        assert second["params"]["_meta"]["io.modelcontextprotocol/subscriptionId"] == 12
        await relay.revoke()
        with pytest.raises(McpRelayError, match="revoked"):
            relay._assert_local(1)
    finally:
        await network.aclose()
        await relay.aclose()


async def test_header_annotation_catalog_is_bound_to_principal_and_epoch():
    from jarvis_core.mcp_server import RelayPrincipal

    called = []

    async def authenticate(token):
        if token not in {"first", "second"}:
            raise McpRelayError("mcp_relay_unauthorized")
        return RelayPrincipal(token, relay.endpoint, frozenset({"tools"}), 1, lambda: None)

    async def dispatch(method, params, context):
        if method == "tools/list":
            return {
                "tools": [
                    {
                        "name": "own",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "label": {
                                    "type": "string",
                                    "x-mcp-header": context.principal.principal_id,
                                }
                            },
                        },
                    }
                ]
            }
        called.append(context.principal.principal_id)
        return {"content": []}

    relay = McpRelayServer(dispatch, authenticate=authenticate)
    await relay.start()
    try:
        for principal in ("first", "second"):
            assert (await wire(relay, message(1, "tools/list"), token=principal))[0] == 200
        request = message(2, "tools/call", {"name": "own", "arguments": {"label": "safe"}})
        assert (await wire(relay, request, token="first", extra={"Mcp-Param-first": "safe"}))[
            0
        ] == 200
        assert (await wire(relay, request, token="second", extra={"Mcp-Param-first": "safe"}))[
            0
        ] == 400
        assert called == ["first"]
    finally:
        await relay.aclose()


@pytest.mark.parametrize("already_closing", [False, True])
async def test_rejected_accept_is_owned_before_first_step_until_writer_close_ack(already_closing):
    entered, release = asyncio.Event(), asyncio.Event()

    class Writer:
        closed = False

        def close(self):
            self.closed = True

        async def wait_closed(self):
            entered.set()
            await release.wait()

    async def dispatch(*args):
        raise AssertionError("A rejected accept cannot dispatch work")

    relay = McpRelayServer(dispatch, max_connections=1)
    writer = Writer()
    relay._closing = already_closing
    # Simulate either a closing listener or its sole admitted slot already used.
    if not already_closing:
        occupied = asyncio.create_task(asyncio.sleep(60))
        relay._connections[occupied] = Writer()
    relay._accept(None, writer)
    accepted = next(task for task, owned in relay._connections.items() if owned is writer)
    assert not accepted.done() and relay.blockers
    # Cancellation can precede the callback coroutine's first instruction;
    # the independent registry must still retain the exact socket.
    accepted.cancel()
    await asyncio.gather(accepted, return_exceptions=True)
    assert relay._connections[accepted] is writer
    closing = asyncio.create_task(relay.aclose())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.sleep(0.01)
        assert not closing.done() and relay.blockers
        release.set()
        assert (await asyncio.wait_for(closing, 2))["cleanup_confirmed"]
        assert writer.closed and not relay._connections and not relay.blockers
    finally:
        release.set()
        await asyncio.gather(closing, return_exceptions=True)
