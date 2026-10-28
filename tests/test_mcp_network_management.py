"""Signed-host control boundary without provider accounts or owner data."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_mcp_http import HTTPFixture
from test_mcp_oauth import Issuer, callback

from jarvis_core.client_identity import ClientIdentityError
from jarvis_core.host_profile import ProfileLease, ServiceProfileGate
from jarvis_core.mcp_network_management import MCP_NETWORK_READ_RPCS, MCPNetworkManagementAPI
from jarvis_core.mcp_oauth import McpOAuthError
from jarvis_core.protocol import RpcError, authenticated_client

OWNER, OTHER = "a" * 64, "b" * 64


class Authority:
    def __init__(self):
        self.revoked = set()

    def lookup(self, owner, *, scope):
        if owner not in {OWNER, OTHER} or owner in self.revoked or scope != "owner":
            raise ClientIdentityError("client_not_admitted")
        return {"scope": scope}


class Host(MCPNetworkManagementAPI):
    def __init__(self, directory):
        self.directory = directory
        self.profile_lease = ProfileLease.acquire(directory)
        self.profile_gate = ServiceProfileGate(self.profile_lease, blockers=lambda: ())
        self.client_authority = Authority()
        self._workspace_id = "f" * 64
        self._closing, self._retiring_sessions = False, set()
        self.agent_profiles = SimpleNamespace(
            get=lambda _: SimpleNamespace(agent_id="jarvis", fingerprint="c" * 64)
        )
        self.store = SimpleNamespace(get_session=lambda _: SimpleNamespace(agent_id="jarvis"))
        self.catalog = SimpleNamespace(
            specs=lambda: ({"name": "own_echo", "inputSchema": {"type": "object"}},)
        )
        self.config = SimpleNamespace(server_specs=[])
        self.dispatched = []
        self.mcp_product = SimpleNamespace(relay_dispatch=self.dispatch)

    async def dispatch(self, method, params, context, *, ref, exports, assert_current):
        assert_current()
        context.assert_current()
        assert authenticated_client.get() == OWNER
        self.dispatched.append((method, exports))
        return {"tools": []}

    def _assert_workspace_current(self):
        pass

    def _management_idle(self):
        pass

    def _ref(self, value):
        return SimpleNamespace(
            session_id=value["session_id"], temporary=value.get("temporary", False)
        )

    async def call(self, method, params):
        with self.profile_gate.admit(method, mutating=method not in MCP_NETWORK_READ_RPCS):
            return await self.mcp_network_handlers()[method](params)


@pytest.fixture
async def host(tmp_path):
    directory = tmp_path.resolve() / "own-synthetic-profile"
    directory.mkdir(mode=0o700)
    value = Host(directory)
    token = authenticated_client.set(OWNER)
    try:
        yield value
    finally:
        await value.close_mcp_network()
        authenticated_client.reset(token)
        value.profile_lease.close()


async def test_oauth_owner_configuration_binding_and_removed_credentials(host):
    peer = Issuer()
    async with HTTPFixture(peer.handle) as http:
        peer.base = http.base
        host.config.server_specs = [
            {
                "server_id": "own",
                "command": (),
                "protocol_version": "2026-07-28",
                "transport": "streamable_http",
                "endpoint": peer.base + "/mcp",
                "allow_loopback": True,
                "oauth": {
                    "resource": peer.base + "/mcp",
                    "client_id": "own-client",
                    "scopes": ["files:read"],
                },
            }
        ]
        assert (await host.call("mcp.oauth.status", {"server_id": "own"}))["status"][
            "connected"
        ] is False
        assert not host._network_state()["oauth"]
        changes = []

        async def authorization_changed(server_id):
            entry = host._network_state()["oauth"][server_id]
            changes.append(
                {
                    "server": server_id,
                    "connected": entry.client.status()["connected"],
                    "token_exchanges": peer.rotation,
                }
            )

        host._mcp_authorization_changed = authorization_changed
        discovered = await host.call("mcp.oauth.discover", {"server_id": "own"})
        assert discovered["metadata"]["resource"] == peer.base + "/mcp"
        flow = (await host.call("mcp.oauth.begin", {"server_id": "own"}))["flow"]
        assert b"200 OK" in await callback(flow)
        assert (
            await host.call("mcp.oauth.finish", {"server_id": "own", "flow_id": flow["flow_id"]})
        )["status"] == "connected"
        spec = host._network_spec("own")
        lease = host.mcp_network_credentials(spec)
        lease.assert_current()
        refreshed = await host.call("mcp.oauth.refresh", {"server_id": "own"})
        assert refreshed["status"] == "connected" and peer.rotation == 2
        with pytest.raises(McpOAuthError):
            lease.assert_current()
        lease = host.mcp_network_credentials(spec)
        foreign = authenticated_client.set(OTHER)
        try:
            with pytest.raises(RpcError) as error:
                await host.call("mcp.oauth.status", {"server_id": "own"})
            assert error.value.code == -32003
        finally:
            authenticated_client.reset(foreign)
        assert (await host.call("mcp.oauth.revoke", {"server_id": "own"}))["revoked"]
        assert changes == [
            {"server": "own", "connected": False, "token_exchanges": 0},
            {"server": "own", "connected": True, "token_exchanges": 1},
            {"server": "own", "connected": False, "token_exchanges": 2},
        ]
        assert host._network_state()["oauth"]["own"].client.status()["connected"] is False
        await host.close_mcp_network_server("own")
        with pytest.raises(McpOAuthError, match=r"revoked|closed"):
            lease.assert_current()
        assert not host._network_state()["oauth"]


async def test_relay_review_binds_catalog_and_explicit_owner_no_tool_grant(host):
    value = {
        "session_id": "1" * 32,
        "temporary": True,
        "action": "start",
        "exports": ["own_echo"],
        "port": 0,
    }
    # Test host session refs are hashable, like real SessionRef.
    from jarvis_core.state import SessionRef

    host._ref = lambda params: SessionRef(params["session_id"], params.get("temporary", False))
    review = (await host.call("mcp.relay.review", value))["review"]
    assert not host.mcp_network_blockers
    stale_catalog = host.catalog
    host.catalog = SimpleNamespace(specs=stale_catalog.specs)
    with pytest.raises(RpcError):
        await host.call(
            "mcp.relay.decide",
            {"review_id": review["review_id"], "digest": review["digest"], "allow": True},
        )
    assert host._network_state()["relay"] is None
    review = (await host.call("mcp.relay.review", value))["review"]
    result = await host.call(
        "mcp.relay.decide",
        {"review_id": review["review_id"], "digest": review["digest"], "allow": True},
    )
    assert result["relay"]["running"] and not result["receipt"]["tool_authorizations_granted"]
    assert "mcp_relay_listener_active" in host.mcp_network_blockers
    assert not host.dispatched
    assert (await host.call("mcp.relay.token", {}))["owner_only"]
    stop = (await host.call("mcp.relay.review", {**value, "action": "stop"}))["review"]
    result = await host.call(
        "mcp.relay.decide",
        {"review_id": stop["review_id"], "digest": stop["digest"], "allow": True},
    )
    assert not result["relay"]["running"] and not host.mcp_network_blockers
