from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from aegis_core.contracts import Capability
from aegis_core.mcp.client import MCP_PROTOCOL_VERSION, McpProtocolError, McpServerConfiguration
from aegis_core.mcp.home_assistant import HomeAssistantMcpClient
from aegis_core.mcp.integrations import HomeAssistantEndpoint, IntegrationScope


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/api/mcp",
        "http://127.0.0.1/api/mcp",
        "http://169.254.169.254/api/mcp",
        "http://192.168.1.2/api/services/light/turn_on",
        "http://user:password@192.168.1.2/api/mcp",
        "http://192.168.1.2/api/mcp?secret=x",
        "http://192.168.1.2/api/mcp#fragment",
        "http://192.168.1.2:0/api/mcp",
    ],
)
def test_ha_endpoint_cannot_escape_private_exact_mcp_endpoint(url):
    with pytest.raises(ValidationError):
        HomeAssistantEndpoint(url=url, allow_insecure_lan=True)


def test_ha_plaintext_transport_requires_explicit_consent():
    with pytest.raises(ValidationError):
        HomeAssistantEndpoint(url="http://192.168.1.2:8123/api/mcp")
    HomeAssistantEndpoint(url="https://192.168.1.2:8123/api/mcp")


def configuration():
    return McpServerConfiguration(
        server_id="home-assistant",
        command=Path("/usr/bin/false"),
        capability=Capability.SYSTEM_READ,
        integration=IntegrationScope(provider="home-assistant"),
        home_assistant=HomeAssistantEndpoint(
            url="http://192.168.1.2:8123/api/mcp",
            allow_insecure_lan=True,
        ),
    )


def mock_peer(monkeypatch, handler):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "aegis_core.mcp.home_assistant.MacOSGenericSecret.get", lambda self: "synthetic-test-token"
    )

    def factory(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("aegis_core.mcp.home_assistant.httpx.AsyncClient", factory)


@pytest.mark.parametrize("sse", [False, True])
async def test_ha_initialization_scoped_auth_catalogue_and_read_call(monkeypatch, sse):
    methods = []

    def handler(request):
        message = json.loads(request.content)
        method = message["method"]
        methods.append(method)
        assert request.headers["authorization"] == "Bearer synthetic-test-token"
        assert request.headers["mcp-protocol-version"] == MCP_PROTOCOL_VERSION
        if method != "initialize":
            assert request.headers["mcp-session-id"] == "synthetic-session"
        if method == "notifications/initialized":
            return httpx.Response(202)
        result = (
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "serverInfo": {"name": "HA"},
                "capabilities": {"tools": {}},
            }
            if method == "initialize"
            else {"tools": [{"name": "GetLiveContext", "inputSchema": {"type": "object"}}]}
            if method == "tools/list"
            else {"content": [{"type": "text", "text": "synthetic-light: off"}]}
        )
        envelope = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        headers = {"MCP-Session-Id": "synthetic-session"}
        if sse:
            headers["content-type"] = "text/event-stream"
            return httpx.Response(
                200,
                headers=headers,
                content="event: message\ndata: " + json.dumps(envelope) + "\n\n",
            )
        return httpx.Response(200, headers=headers, json=envelope)

    mock_peer(monkeypatch, handler)
    client = HomeAssistantMcpClient(configuration())
    assert len(await client.start()) == 1
    try:
        assert client.available
        result = await client.call_tool("GetLiveContext", {})
        assert result["content"][0]["text"] == "synthetic-light: off"
        with pytest.raises(ValueError):
            await client.call_tool("HassTurnOn", {})
        assert methods == [
            "initialize",
            "notifications/initialized",
            "tools/list",
            "tools/list",
            "tools/call",
        ]
    finally:
        await client.close()
    assert not client.available


@pytest.mark.parametrize("failure", ["redirect", "session", "id", "oversized", "server_request"])
async def test_ha_protocol_failures_never_follow_or_leak_credentials(monkeypatch, failure):
    requests = []

    def handler(request):
        requests.append(request)
        if failure == "redirect":
            return httpx.Response(307, headers={"Location": "https://attacker.example/api/mcp"})
        if failure == "oversized":
            return httpx.Response(
                200, headers={"content-type": "application/json"}, content=b"x" * 262_145
            )
        headers = {"MCP-Session-Id": "\x7f"} if failure == "session" else {}
        message = {"jsonrpc": "2.0", "id": 2, "result": {}}
        if failure == "server_request":
            message = {"jsonrpc": "2.0", "id": 1, "method": "sampling/createMessage"}
        return httpx.Response(200, headers=headers, json=message)

    mock_peer(monkeypatch, handler)
    client = HomeAssistantMcpClient(configuration())
    with pytest.raises(McpProtocolError) as error:
        await client.start()
    assert len(requests) == 1
    assert "synthetic-test-token" not in str(error.value)
    assert not client.available


async def test_ha_schema_drift_quarantines_before_tool_call(monkeypatch):
    lists = 0
    calls = 0

    def handler(request):
        nonlocal lists, calls
        message = json.loads(request.content)
        if message["method"] == "notifications/initialized":
            return httpx.Response(202)
        if message["method"] == "initialize":
            result = {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "serverInfo": {"name": "HA"},
                "capabilities": {"tools": {}},
            }
        elif message["method"] == "tools/list":
            lists += 1
            result = {
                "tools": [
                    {
                        "name": "GetLiveContext",
                        "inputSchema": {"type": "object"},
                        "description": str(lists),
                    }
                ]
            }
        else:
            calls += 1
            result = {}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})

    mock_peer(monkeypatch, handler)
    client = HomeAssistantMcpClient(configuration())
    await client.start()
    with pytest.raises(McpProtocolError):
        await client.call_tool("GetLiveContext", {})
    assert calls == 0 and client.failure_code == "catalogue_changed"
    assert not client.available
