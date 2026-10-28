"""Own OAuth issuer/callback/PKCE fixtures; no account, owner token or public call."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from test_mcp_http import HTTPFixture

from jarvis_core.mcp_http import McpHTTPNetwork
from jarvis_core.mcp_oauth import McpOAuthClient, McpOAuthError


class Issuer:
    def __init__(self):
        self.base = None
        self.exchange = []
        self.rotation = 0
        self.drop_exchange = False
        self.pkce = True

    async def handle(self, request, reader, writer):
        if request["target"] == "/.well-known/oauth-protected-resource/mcp":
            return (
                200,
                {},
                {
                    "resource": self.base + "/mcp",
                    "authorization_servers": [self.base + "/issuer"],
                    "scopes_supported": ["files:read", "files:write"],
                },
            )
        if request["target"] == "/.well-known/oauth-authorization-server/issuer":
            return (
                200,
                {},
                {
                    "issuer": self.base + "/issuer",
                    "authorization_endpoint": self.base + "/authorize",
                    "token_endpoint": self.base + "/token",
                    "registration_endpoint": self.base + "/register",
                    "code_challenge_methods_supported": ["S256"] if self.pkce else [],
                    "authorization_response_iss_parameter_supported": True,
                    "client_id_metadata_document_supported": True,
                    "token_endpoint_auth_methods_supported": ["none"],
                },
            )
        if request["target"] == "/register":
            return 201, {}, {"client_id": "registered-own-client"}
        if request["target"] == "/token":
            payload = {key: value[0] for key, value in parse_qs(request["body"].decode()).items()}
            assert payload["resource"] == self.base + "/mcp"
            self.exchange.append(payload)
            if self.drop_exchange:
                return None
            self.rotation += 1
            return (
                200,
                {},
                {
                    "token_type": "Bearer",
                    "access_token": f"synthetic-access-{self.rotation}",
                    "refresh_token": f"synthetic-refresh-{self.rotation}",
                    "expires_in": 3600,
                    "scope": "files:read",
                },
            )
        return 404, {}, b""


@pytest.fixture
async def issuer():
    value = Issuer()
    async with HTTPFixture(value.handle) as http:
        value.base = http.base
        oauth = McpOAuthClient(http.base + "/mcp", McpHTTPNetwork(test_origins=(http.base,)))
        try:
            yield value, oauth
        finally:
            assert (await oauth.aclose())["cleanup_confirmed"]
            assert not oauth.blockers


async def callback(flow, *, state=None, issuer=None):
    query = parse_qs(urlsplit(flow["authorization_url"]).query)
    redirect = urlsplit(query["redirect_uri"][0])
    parameters = {
        "code": "synthetic-code",
        "state": query["state"][0] if state is None else state,
        "iss": flow["issuer"] if issuer is None else issuer,
    }
    reader, writer = await asyncio.open_connection("127.0.0.1", redirect.port)
    try:
        target = redirect.path + "?" + urlencode(parameters)
        writer.write(
            (
                f"GET {target} HTTP/1.1\r\nHost: {redirect.netloc}\r\nConnection: close\r\n\r\n"
            ).encode()
        )
        await writer.drain()
        return await reader.read()
    finally:
        writer.close()
        await writer.wait_closed()


async def test_real_loopback_pkce_resource_issuer_rotation_and_local_revocation(issuer):
    peer, oauth = issuer
    discovery = await oauth.discover()
    assert discovery["issuer"] == peer.base + "/issuer"
    flow = await oauth.begin_authorization("registered-client", ["files:read"])
    query = parse_qs(urlsplit(flow["authorization_url"]).query)
    assert query["resource"] == [peer.base + "/mcp"] and query["code_challenge_method"] == ["S256"]
    assert urlsplit(query["redirect_uri"][0]).path == "/jarvis-oauth/callback"
    assert b"200 OK" in await callback(flow)
    assert (await oauth.finish_authorization(flow["flow_id"]))["connected"] is True
    payload = peer.exchange[0]
    encoded = (
        base64.urlsafe_b64encode(hashlib.sha256(payload["code_verifier"].encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert encoded == query["code_challenge"][0]
    lease = oauth.credential_lease(peer.base + "/mcp", required_scopes=("files:read",))
    assert lease.authorization_header.startswith("Bearer synthetic-access-")
    assert "synthetic-access" not in repr(lease) + json.dumps(oauth.status())
    with pytest.raises(McpOAuthError, match="authorization_required"):
        oauth.credential_lease(peer.base + "/another-resource")
    await oauth.refresh()
    with pytest.raises(McpOAuthError):
        lease.assert_current()
    current = oauth.credential_lease(peer.base + "/mcp")
    await oauth.revoke()
    with pytest.raises(McpOAuthError):
        current.assert_current()
    assert oauth.status()["connected"] is False


@pytest.mark.parametrize("invalid", ["state", "issuer"])
async def test_callback_mixup_or_state_rejected_before_any_token_post(issuer, invalid):
    peer, oauth = issuer
    await oauth.discover()
    flow = await oauth.begin_authorization("client", ["files:read"])
    response = await callback(
        flow,
        **({"state": "not-the-state"} if invalid == "state" else {"issuer": peer.base + "/evil"}),
    )
    assert b"400 Bad Request" in response and not peer.exchange
    assert (await oauth.finish_authorization(flow["flow_id"]))["status"] == "waiting_owner"
    assert b"200 OK" in await callback(flow)
    assert (await oauth.finish_authorization(flow["flow_id"]))["connected"]


async def test_pkce_required_and_lost_exchange_ack_is_never_retried(issuer):
    peer, oauth = issuer
    peer.pkce = False
    with pytest.raises(McpOAuthError, match="oauth_pkce_required"):
        await oauth.discover()
    peer.pkce = True
    await oauth.discover()
    flow = await oauth.begin_authorization("client", ["files:read"])
    await callback(flow)
    peer.drop_exchange = True
    with pytest.raises(McpOAuthError, match="oauth_exchange_unknown"):
        await oauth.finish_authorization(flow["flow_id"])
    with pytest.raises(McpOAuthError, match="oauth_flow_not_replayable"):
        await oauth.finish_authorization(flow["flow_id"])
    assert len(peer.exchange) == 1 and not oauth.status()["connected"]
    assert oauth.status()["uncertain"] is True


async def test_explicit_dynamic_registration_uses_callback_that_is_actually_bound(issuer):
    _peer, oauth = issuer
    await oauth.discover()
    flow = await oauth.begin_authorization(scopes=["files:read"], registration_mode="dynamic")
    assert parse_qs(urlsplit(flow["authorization_url"]).query)["client_id"] == [
        "registered-own-client"
    ]
    assert b"200 OK" in await callback(flow)
    assert (await oauth.finish_authorization(flow["flow_id"]))["connected"]


async def test_abandoned_flow_closes_callback_and_releases_partial_request(issuer):
    _peer, oauth = issuer
    await oauth.discover()
    flow = await oauth.begin_authorization("registered-client", ["files:read"])
    redirect = urlsplit(parse_qs(urlsplit(flow["authorization_url"]).query)["redirect_uri"][0])
    _reader, writer = await asyncio.open_connection("127.0.0.1", redirect.port)
    writer.write(b"GET /jarvis-oauth/callback HTTP/1.1\r\n")
    await writer.drain()
    await asyncio.sleep(0)
    try:
        await oauth.revoke()
        assert not oauth._flow.callback_tasks and not oauth._flow.callback_writers
        assert oauth._flow.callback_server is None
        assert oauth._flow.future.cancelled()
        assert oauth.status()["connected"] is False
        with pytest.raises(OSError):
            await asyncio.open_connection("127.0.0.1", redirect.port)
    finally:
        writer.close()
        await writer.wait_closed()


async def test_callback_expiration_has_no_token_effect(issuer):
    peer, oauth = issuer
    await oauth.discover()
    flow = await oauth.begin_authorization("registered-client", ["files:read"])
    timer = oauth._flow.expiry_task
    timer.cancel()
    await asyncio.gather(timer, return_exceptions=True)
    await oauth._expire(oauth._flow, 0.001)
    assert oauth.status()["flow_status"] == "expired"
    assert not peer.exchange and not oauth.blockers
    with pytest.raises(McpOAuthError, match="not_replayable"):
        await oauth.finish_authorization(flow["flow_id"])
