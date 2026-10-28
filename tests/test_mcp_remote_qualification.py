"""Independent HTTP/OAuth peer -> private RPC -> actual Runtime authority.

These tests qualify synthetic loopback integration, not a third-party service,
native WebKit rendering or access to an owner's account. Run only under the
repository's supervised CI entrypoint.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import pytest
from test_mcp_http import HTTPFixture
from test_mcp_product import approve, drained
from test_mcp_relay_product import pending
from test_service import connection as connection
from test_service import session, settled

from jarvis_core.config import ConfigError, ProfileConfig
from jarvis_core.mcp import HTTP_COMPAT_VERSION, LEGACY_VERSION, MODERN_VERSION, McpClient, McpError
from jarvis_core.mcp_http import McpHttpClient
from jarvis_core.mcp_registry import McpRegistryError, ServerSpec
from jarvis_core.protocol import RpcError

PEER_SOURCE = Path(__file__).parents[1] / "script" / "mcp_qualification_peer.py"
SPEC = importlib.util.spec_from_file_location("jarvis_qualification_peer", PEER_SOURCE)
PEER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PEER)


@pytest.fixture
async def peer(tmp_path):
    directory = tmp_path / "independent-wire"
    directory.mkdir(mode=0o700)
    async with PEER.QualificationPeer(directory) as value:
        yield value
    assert not value.tasks and not value.writers and not value.streams
    assert not value.access_tokens and not value.refresh_tokens


def calls(peer, method):
    return [row for row in peer.records if row.get("method") == method]


async def get(url):
    parsed = urlsplit(url)
    assert parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
    reader, writer = await asyncio.open_connection(parsed.hostname, parsed.port)
    target = parsed.path + ("?" + parsed.query if parsed.query else "")
    try:
        writer.write(
            (
                f"GET {target} HTTP/1.1\r\nHost: {parsed.netloc}\r\nConnection: close\r\n\r\n"
            ).encode()
        )
        await writer.drain()
        async with asyncio.timeout(5):
            response = await reader.read()
        raw, body = response.split(b"\r\n\r\n", 1)
        lines = raw.decode().split("\r\n")
        headers = dict(
            (key.lower(), value.strip()) for key, value in (row.split(":", 1) for row in lines[1:])
        )
        return int(lines[0].split()[1]), headers, body
    finally:
        writer.close()
        await writer.wait_closed()


async def authorize_synthetic_account(client, peer):
    discovered = await client.call("mcp.oauth.discover", {"server_id": "remote"})
    assert discovered["metadata"]["resource"] == peer.base + "/protected/mcp"
    flow = (await client.call("mcp.oauth.begin", {"server_id": "remote"}))["flow"]
    code, _headers, page = await get(flow["authorization_url"])
    assert code == 200
    # The independent AS renders a review page, then performs the real loopback
    # callback and verifier exchange. No direct injection into OAuth internals.
    approval = re.search(rb'href="(/approve\?ticket=[a-f0-9]+)"', page)
    assert approval is not None
    code, headers, _body = await get(urljoin(peer.base, approval[1].decode()))
    assert code == 302
    assert urlsplit(headers["location"]).path == "/jarvis-oauth/callback"
    assert (await get(headers["location"]))[0] == 200
    result = await client.call(
        "mcp.oauth.finish", {"server_id": "remote", "flow_id": flow["flow_id"]}
    )
    assert result["status"] == "connected"
    assert len([row for row in peer.records if row["event"] == "oauth_token"]) == 1


def spec(peer, route, protocol, transport="streamable_http", *, oauth=False):
    value = {
        "server_id": "remote",
        "command": [],
        "protocol_version": protocol,
        "transport": transport,
        "endpoint": peer.base + route,
        "allow_loopback": True,
    }
    if oauth:
        value["oauth"] = {
            "resource": peer.base + route,
            "client_id": "jarvis-qualification",
            "scopes": ["qualification:read"],
        }
    return value


async def complete(service, client, ref, run_id):
    result = await settled(client, ref, run_id)
    await drained(service)
    return result


async def app_document(service, client, ref, catalog):
    source = await client.call(
        "mcp.apps.open",
        {
            **ref,
            "server_id": "remote",
            "resource_uri": PEER.APP_URI,
            "expected_catalog_hash": catalog["catalog_hash"],
            "request_id": uuid4().hex,
        },
    )
    await approve(client, ref, source["run_id"])
    assert (await complete(service, client, ref, source["run_id"]))["status"] == "completed"
    document = await client.call("mcp.apps.document", {**ref, "run_id": source["run_id"]})
    assert document["settled"] and PEER.HTML_MARKER in document["app"]["html"]
    return document["app"]


@pytest.mark.parametrize(
    "route,protocol,transport",
    [
        ("/mcp18", "2025-06-18", "streamable_http"),
        ("/mcp25", "2025-11-25", "streamable_http"),
        ("/mcp26", "2026-07-28", "streamable_http"),
        ("/sse", "2024-11-05", "sse"),
    ],
)
async def test_remote_catalog_content_denial_exact_approval_and_no_provider_turn(
    connection,
    peer,
    route,
    protocol,
    transport,
):
    service, client, provider, _root = connection
    configuration = spec(peer, route, protocol, transport)
    await client.call("mcp.add", configuration)
    assert (await client.call("mcp.connect", configuration))["server"]["connected"]
    catalog = await client.call("mcp.catalog", {"server_id": "remote"})
    assert catalog["resources"] and catalog["prompts"] and catalog["resource_templates"]
    assert not calls(peer, "resources/read") and not calls(peer, "prompts/get")
    ref = await session(client)
    request = {
        **ref,
        "server_id": "remote",
        "kind": "resource",
        "uri": PEER.RESOURCE,
        "expected_catalog_hash": catalog["catalog_hash"],
        "request_id": uuid4().hex,
    }
    rejected = await client.call("mcp.content.request", request)
    await approve(client, ref, rejected["run_id"], allow=False)
    denied = await complete(service, client, ref, rejected["run_id"])
    assert denied["status"] == "failed" and denied["error"] == "approval_denied"
    assert denied["outcome"] == "known" and denied["action_receipts"] == []
    assert not calls(peer, "resources/read")
    view = await client.call("mcp.content.result", {**ref, "run_id": rejected["run_id"]})
    assert view["settled"] and view["error"] == "approval_denied" and view["payload"] is None

    for kind, extra, method in [
        ("resource", {"uri": PEER.RESOURCE}, "resources/read"),
        (
            "template",
            {"template_uri": PEER.TEMPLATE, "arguments": {"key": "área 42"}},
            "resources/read",
        ),
        ("prompt", {"name": "explain", "arguments": {"topic": "FRASE SINTÉTICA"}}, "prompts/get"),
    ]:
        current = {
            **ref,
            "server_id": "remote",
            "kind": kind,
            **extra,
            "expected_catalog_hash": catalog["catalog_hash"],
            "request_id": uuid4().hex,
        }
        count = len(calls(peer, method))
        started = await client.call("mcp.content.request", current)
        # A native content request only prepares a run; it cannot read ahead.
        ticket = await pending(client, ref)
        assert ticket["run_id"] == started["run_id"] and len(calls(peer, method)) == count
        await approve(client, ref, started["run_id"])
        receipt = await complete(service, client, ref, started["run_id"])
        assert receipt["status"] == "completed"
        assert len(receipt["action_receipts"]) == 1
        assert receipt["action_receipts"][0]["verified"] is False
        assert PEER.PRIVATE_MARKER not in json.dumps(receipt)
        report = await client.call("mcp.content.result", {**ref, "run_id": started["run_id"]})
        assert report["settled"] and report["provenance"] == "external_unverified"
        assert PEER.PRIVATE_MARKER not in json.dumps(report)
        assert len(calls(peer, method)) == count + 1
        dispatched = calls(peer, method)[-1]
        if kind == "prompt":
            assert dispatched["arguments_sha256"] == PEER.digest({"topic": "FRASE SINTÉTICA"})
        else:
            uri = (
                PEER.RESOURCE
                if kind == "resource"
                else "jarvis://qualification/item/%C3%A1rea%2042"
            )
            assert dispatched["uri_sha256"] == PEER.digest(uri)
        assert (await client.call("mcp.content.request", current))["run_id"] == started["run_id"]
        assert len(calls(peer, method)) == count + 1
    assert not provider.calls
    assert (await client.call("mcp.disconnect", {"server_id": "remote"}))["disconnected"]
    transport_client = service.mcp._entries["remote"].client
    assert transport_client.close_receipt["local_cleanup_confirmed"]
    assert not transport_client.network.blockers


@pytest.mark.parametrize("change", ["refresh", "revoke"])
async def test_full_oauth_rotation_retires_app_and_pending_approval_without_dispatch(
    connection,
    peer,
    change,
):
    service, client, provider, _root = connection
    configuration = spec(peer, "/protected/mcp", "2026-07-28", oauth=True)
    await client.call("mcp.add", configuration)
    with pytest.raises(RpcError):
        await client.call("mcp.connect", configuration)
    assert not calls(peer, "server/discover")
    await authorize_synthetic_account(client, peer)
    secrets = set(peer.access_tokens) | set(peer.refresh_tokens)
    assert (await client.call("mcp.connect", configuration))["server"]["connected"]
    catalog = await client.call("mcp.catalog", {"server_id": "remote"})
    ref = await session(client)
    app = await app_document(service, client, ref, catalog)
    request = {
        "app_instance_id": app["app_instance_id"],
        "message": {
            "jsonrpc": "2.0",
            "id": "before-change",
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"text": "NO DESPACHAR 4242"}},
        },
    }
    operation = await client.call("mcp.apps.request", request)
    ticket = await pending(client, ref)
    assert ticket["run_id"] == operation["run_id"] and not calls(peer, "tools/call")
    result = await client.call("mcp.oauth." + change, {"server_id": "remote"})
    if change == "refresh":
        assert result["status"] == "connected"
    else:
        assert result["revoked"]
    receipt = await complete(service, client, ref, operation["run_id"])
    assert receipt["status"] in {"cancelled", "failed"}
    assert receipt["outcome"] == "unknown"
    assert not calls(peer, "tools/call")
    assert not (
        await client.call(
            "approvals.decide",
            {
                **ref,
                "approval_id": ticket["approval_id"],
                "digest": ticket["digest"],
                "allow": True,
            },
        )
    )["accepted"]
    with pytest.raises(RpcError):
        await client.call("mcp.apps.request", request)
    assert (await client.call("mcp.apps.close", {"app_instance_id": app["app_instance_id"]}))[
        "closed"
    ]

    if change == "refresh":
        assert (await client.call("mcp.connect", configuration))["server"]["connected"]
        catalog = await client.call("mcp.catalog", {"server_id": "remote"})
        # The cancelled source chat retains uncertainty and must not silently
        # resume. A fresh chat is an explicit new context, with no inherited app.
        ref = await session(client)
        reopened = await app_document(service, client, ref, catalog)
        request["app_instance_id"] = reopened["app_instance_id"]
        request["message"]["id"] = "fresh-authorization"
        operation = await client.call("mcp.apps.request", request)
        await approve(client, ref, operation["run_id"])
        receipt = await complete(service, client, ref, operation["run_id"])
        assert receipt["status"] == "completed" and len(calls(peer, "tools/call")) == 1
        assert not receipt["action_receipts"][0]["verified"]
        assert PEER.PRIVATE_MARKER not in json.dumps(receipt)
        assert (await client.call("mcp.apps.request", request))["settled"]
        assert len(calls(peer, "tools/call")) == 1
        await client.call("mcp.apps.close", {"app_instance_id": reopened["app_instance_id"]})
        await client.call("mcp.disconnect", {"server_id": "remote"})
    else:
        before = len(peer.records)
        with pytest.raises(RpcError):
            await client.call("mcp.connect", configuration)
        assert len(peer.records) == before
    durable = "\n".join(service.store._disk.iterdump())
    journal = (peer.directory / "wire.jsonl").read_text()
    secrets.update(peer.access_tokens)
    secrets.update(peer.refresh_tokens)
    assert all(secret not in durable + journal for secret in secrets)
    assert PEER.HTML_MARKER not in durable and PEER.PRIVATE_MARKER not in durable
    assert not provider.calls


def test_june_2025_http_choice_survives_configuration_reload_without_network(tmp_path):
    profile = ProfileConfig(tmp_path.resolve() / "version-choice")
    profile.set_server(
        "external",
        (),
        HTTP_COMPAT_VERSION,
        transport="streamable_http",
        endpoint="https://example.com/mcp",
    )
    persisted = ProfileConfig(profile.directory).server_specs
    assert len(persisted) == 1
    assert persisted[0]["protocol_version"] == HTTP_COMPAT_VERSION
    assert persisted[0]["transport"] == "streamable_http"
    chosen = ServerSpec(**persisted[0])
    assert chosen.protocol_version == HTTP_COMPAT_VERSION


@pytest.mark.parametrize("transport", ["stdio", "sse"])
def test_june_2025_is_not_silently_admitted_on_other_transports(tmp_path, transport):
    command = (sys.executable,) if transport == "stdio" else ()
    with pytest.raises(McpRegistryError, match="mcp_protocol_version_invalid"):
        ServerSpec(
            "external",
            command,
            HTTP_COMPAT_VERSION,
            transport=transport,
            endpoint=None if transport == "stdio" else "https://example.com/mcp",
        )
    with pytest.raises(McpError, match="unsupported_mcp_version"):
        if transport == "stdio":
            McpClient(command, tmp_path.resolve() / "client", protocol_version=HTTP_COMPAT_VERSION)
        else:
            McpHttpClient(
                "https://example.com/mcp",
                tmp_path.resolve() / "client",
                transport=transport,
                protocol_version=HTTP_COMPAT_VERSION,
            )
    profile = ProfileConfig(tmp_path.resolve() / "unaccepted-transport")
    with pytest.raises(ConfigError):
        profile.set_server(
            "external",
            command,
            HTTP_COMPAT_VERSION,
            transport=transport,
            endpoint=None if transport == "stdio" else "https://example.com/mcp",
        )
    assert not profile.server_specs


@pytest.mark.parametrize("selected", [LEGACY_VERSION, MODERN_VERSION])
async def test_june_2025_peer_cannot_silently_change_another_explicit_wire_choice(
    tmp_path, selected
):
    methods = []

    async def handler(request, _reader, _writer):
        message = json.loads(request["body"])
        methods.append(message["method"])
        return (
            200,
            {},
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {
                    "protocolVersion": HTTP_COMPAT_VERSION,
                    "supportedVersions": [HTTP_COMPAT_VERSION],
                    "ttlMs": 0,
                    "cacheScope": "private",
                    "capabilities": {},
                    "serverInfo": {"name": "Independent June peer", "version": "1"},
                },
            },
        )

    async with HTTPFixture(handler) as remote:
        client = McpHttpClient(
            remote.base + "/mcp",
            tmp_path.resolve() / "explicit-client",
            protocol_version=selected,
            test_origins=(remote.base,),
        )
        try:
            with pytest.raises(McpError, match="unsupported_mcp_version"):
                await client.start()
            assert methods == ["server/discover" if selected == MODERN_VERSION else "initialize"]
            assert not client.connected
        finally:
            assert (await client.aclose())["local_cleanup_confirmed"]
