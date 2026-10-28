"""Own RAM-only OAuth 2.1/PKCE for explicitly selected HTTP MCP resources.

No credentials are discovered, exported, logged, or passed through a relay.
Owner UI opens the reviewed authorization URL; only the exact private loopback
callback accepts its code. Token exchange/rotation is never retried after an
uncertain acknowledgement. Resource/issuer/scope/epoch fences survive awaits.
Sources: modelcontextprotocol.io/specification/2026-07-28/basic/authorization,
RFC 9728/8414/8707/9207/7636/8252 (consulted 2026-10-28).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import math
import re
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from jarvis_core.mcp_http import McpHTTPError, McpHTTPNetwork

_SCOPE = re.compile(r"[\x21\x23-\x5b\x5d-\x7e]{1,256}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9\-._~+/]+=*\Z")


class McpOAuthError(ValueError):
    def __init__(self, code, *, uncertain=False):
        self.code, self.uncertain = code, uncertain
        super().__init__(code)


def _scopes(value):
    if (
        not isinstance(value, (tuple, list))
        or len(value) > 64
        or any(not isinstance(item, str) or not _SCOPE.fullmatch(item) for item in value)
        or len(set(value)) != len(value)
    ):
        raise McpOAuthError("oauth_scopes_invalid")
    return tuple(value)


def _plain(value, maximum=8192):
    return (
        isinstance(value, str)
        and 0 < len(value.encode()) <= maximum
        and all(ord(c) >= 32 and ord(c) != 127 for c in value)
    )


def _challenge(value):
    if value is None:
        return {}
    if not isinstance(value, str) or len(value) > 8192 or not value.startswith("Bearer "):
        raise McpOAuthError("oauth_challenge_invalid")
    position, result = 7, {}
    pattern = re.compile(
        r'([A-Za-z][A-Za-z0-9_]*)=(?:"((?:[^"\\\r\n]|\\["\\])*)"|([!#$%&\'*+.^_`|~0-9A-Za-z-]+))'
    )
    while position < len(value):
        match = pattern.match(value, position)
        if match is None or match[1].lower() in result:
            raise McpOAuthError("oauth_challenge_invalid")
        result[match[1].lower()] = (
            match[2].replace('\\"', '"').replace("\\\\", "\\") if match[2] is not None else match[3]
        )
        position = match.end()
        if position < len(value):
            separator = re.match(r",[ \t]*", value[position:])
            if separator is None:
                raise McpOAuthError("oauth_challenge_invalid")
            position += separator.end()
    return result


@dataclass(frozen=True)
class BearerLease:
    resource: str
    scopes: tuple[str, ...]
    epoch: int
    expires_at: float
    _owner: object = field(repr=False, compare=False)
    _token: str = field(repr=False, compare=False)

    @property
    def authorization_header(self):
        self.assert_current()
        return "Bearer " + self._token

    def assert_current(self):
        self._owner._assert_lease(self)


@dataclass
class _Grant:
    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    client_id: str
    scopes: tuple[str, ...]
    expires_at: float
    issuer: str
    token_endpoint: str


@dataclass
class _Flow:
    id: str
    epoch: int
    issuer: str
    client_id: str
    scopes: tuple[str, ...]
    redirect_uri: str
    authorization_url: str
    verifier: str = field(repr=False)
    state: str = field(repr=False)
    future: asyncio.Future = field(repr=False)
    expires_at: float = 0
    phase: str = "waiting_owner"
    dispatched: bool = False
    callback_server: asyncio.Server | None = None
    callback_tasks: set = field(default_factory=set, repr=False)
    callback_writers: set = field(default_factory=set, repr=False)
    expiry_task: asyncio.Task | None = field(default=None, repr=False)
    token_endpoint: str = ""
    requires_iss: bool = False


class McpOAuthClient:
    def __init__(
        self,
        resource_url,
        network=None,
        *,
        before_effect=None,
        allow_loopback=False,
        test_origins=(),
        clock=time.monotonic,
    ):
        self.network = network or McpHTTPNetwork(
            allow_loopback=allow_loopback, test_origins=test_origins, before_effect=before_effect
        )
        self.resource = self.network.url(resource_url, config=True).canonical
        self.before_effect = before_effect
        self.clock = clock
        self._epoch = 1
        self._resource_metadata = None
        self._metadata = None
        self._issuer = None
        self._required_scopes = ()
        self._grant = None
        self._flow = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._unknown = False
        self._registrations = {}

    def _check(self, epoch=None):
        if self._closed:
            raise McpOAuthError("oauth_closed")
        if epoch is not None and epoch != self._epoch:
            raise McpOAuthError("oauth_revoked")
        if self.before_effect is not None:
            self.before_effect()

    async def _metadata_json(self, url):
        self.network.url(url)
        self._check()
        value = await self.network.json(url, maximum=131072, timeout=20)
        self._check()
        if not isinstance(value, dict):
            raise McpOAuthError("oauth_metadata_invalid")
        return value

    async def discover(self, challenge=None, *, issuer=None):
        self._check()
        hints = _challenge(challenge)
        resource = urlsplit(self.resource)
        metadata_url = hints.get("resource_metadata")
        if metadata_url is None:
            metadata_url = urlunsplit(
                (
                    resource.scheme,
                    resource.netloc,
                    "/.well-known/oauth-protected-resource" + resource.path.rstrip("/"),
                    "",
                    "",
                )
            )
        self.network.url(metadata_url)
        try:
            protected = await self._metadata_json(metadata_url)
        except McpHTTPError as error:
            if error.status != 404 or "resource_metadata" in hints or not resource.path.rstrip("/"):
                raise
            protected = await self._metadata_json(
                urlunsplit(
                    (
                        resource.scheme,
                        resource.netloc,
                        "/.well-known/oauth-protected-resource",
                        "",
                        "",
                    )
                )
            )
        if protected.get("resource") != self.resource:
            raise McpOAuthError("oauth_resource_mismatch")
        servers = protected.get("authorization_servers")
        if (
            not isinstance(servers, list)
            or not 1 <= len(servers) <= 8
            or any(not isinstance(item, str) for item in servers)
            or len(set(servers)) != len(servers)
        ):
            raise McpOAuthError("oauth_issuer_invalid")
        for item in servers:
            self.network.url(item, config=True)
        self._resource_metadata = protected
        # RFC 9728 scopes_supported is a catalog, not a mandatory grant.
        # Only an exact Bearer challenge may demand additional scopes; the
        # owner's configured scope selection remains the authorization ceiling.
        _scopes(protected.get("scopes_supported", ()))
        self._required_scopes = _scopes(hints["scope"].split() if "scope" in hints else ())
        if issuer is None and len(servers) != 1:
            return {
                "state": "choose_issuer",
                "resource": self.resource,
                "issuers": servers,
                "scopes": list(self._required_scopes),
            }
        selected = servers[0] if issuer is None else issuer
        if selected not in servers:
            raise McpOAuthError("oauth_issuer_mismatch")
        if self._grant is not None and selected != self._grant.issuer:
            raise McpOAuthError("oauth_revoke_before_issuer_change")
        if (
            self._flow is not None
            and self._flow.phase in {"waiting_owner", "exchanging"}
            and selected != self._flow.issuer
        ):
            raise McpOAuthError("oauth_flow_busy")
        parsed = urlsplit(selected)
        if parsed.query or parsed.fragment:
            raise McpOAuthError("oauth_issuer_invalid")
        oauth = urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                "/.well-known/oauth-authorization-server" + parsed.path.rstrip("/"),
                "",
                "",
            )
        )
        try:
            metadata = await self._metadata_json(oauth)
        except McpHTTPError as error:
            if error.status != 404:
                raise
            metadata = await self._metadata_json(
                selected.rstrip("/") + "/.well-known/openid-configuration"
            )
        if metadata.get("issuer") != selected:
            raise McpOAuthError("oauth_issuer_mismatch")
        for field_name in ("authorization_endpoint", "token_endpoint"):
            self.network.url(metadata.get(field_name))
        if "S256" not in metadata.get("code_challenge_methods_supported", ()):
            raise McpOAuthError("oauth_pkce_required")
        if (
            "authorization_response_iss_parameter_supported" in metadata
            and type(metadata["authorization_response_iss_parameter_supported"]) is not bool
        ):
            raise McpOAuthError("oauth_metadata_invalid")
        self._metadata, self._issuer = metadata, selected
        return self.metadata()

    def metadata(self):
        value = {
            "state": "ready" if self._metadata else "not_discovered",
            "resource": self.resource,
            "issuer": self._issuer,
            "scopes": list(self._required_scopes),
            "supported_scopes": list(self._resource_metadata.get("scopes_supported", ()))
            if self._resource_metadata
            else [],
        }
        if self._metadata:
            value.update(
                authorization_endpoint=self._metadata["authorization_endpoint"],
                token_endpoint=self._metadata["token_endpoint"],
                cimd_supported=self._metadata.get("client_id_metadata_document_supported") is True,
                registration_supported=isinstance(self._metadata.get("registration_endpoint"), str),
            )
        return value

    async def register_client(self, redirect_uri, *, client_name="Jarvis"):
        """Explicit optional DCR; never invoked by connect/discover/begin."""
        self._check()
        if self._metadata is None or not _plain(client_name, 128):
            raise McpOAuthError("oauth_not_discovered")
        endpoint = self._metadata.get("registration_endpoint")
        self.network.url(endpoint)
        redirect = urlsplit(redirect_uri)
        if redirect.scheme != "http" or redirect.hostname != "127.0.0.1" or not redirect.port:
            raise McpOAuthError("oauth_redirect_invalid")
        dispatched = False

        def dispatch():
            nonlocal dispatched
            self._check()
            dispatched = True

        try:
            value = await self.network.json(
                endpoint,
                method="POST",
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                body=json.dumps(
                    {
                        "client_name": client_name,
                        "redirect_uris": [redirect_uri],
                        "token_endpoint_auth_method": "none",
                        "grant_types": ["authorization_code", "refresh_token"],
                        "response_types": ["code"],
                    },
                    separators=(",", ":"),
                ).encode(),
                maximum=131072,
                accepted_statuses=(201,),
                before_dispatch=dispatch,
            )
        except BaseException as error:
            if dispatched:
                self._unknown = True
            if isinstance(error, asyncio.CancelledError):
                raise
            raise McpOAuthError(
                "oauth_registration_unknown" if dispatched else "oauth_registration_failed",
                uncertain=dispatched,
            ) from None
        client_id = value.get("client_id")
        if not _plain(client_id, 2048) or value.get("client_secret"):
            raise McpOAuthError("oauth_registration_invalid")
        self._registrations[client_id] = self._issuer, redirect_uri
        return {"client_id": client_id, "issuer": self._issuer, "public_client": True}

    async def begin_authorization(
        self, client_id=None, scopes=None, *, registration_mode="pre_registered", lifetime=180
    ):
        async with self._lock:
            self._check()
            if self._metadata is None or (
                registration_mode != "dynamic" and not _plain(client_id, 2048)
            ):
                raise McpOAuthError("oauth_not_discovered")
            if self._unknown or (
                self._flow is not None and self._flow.phase in {"waiting_owner", "exchanging"}
            ):
                raise McpOAuthError("oauth_flow_busy")
            if registration_mode not in {"pre_registered", "cimd", "dynamic"}:
                raise McpOAuthError("oauth_registration_invalid")
            if registration_mode == "cimd":
                if self._metadata.get("client_id_metadata_document_supported") is not True:
                    raise McpOAuthError("oauth_cimd_unavailable")
                if not self.network.url(client_id, config=True).secure:
                    raise McpOAuthError("oauth_client_id_invalid")
            approved = self._required_scopes if scopes is None else _scopes(scopes)
            if not set(self._required_scopes) <= set(approved):
                raise McpOAuthError("oauth_insufficient_scope")
            if (
                type(lifetime) not in {int, float}
                or not math.isfinite(lifetime)
                or not 30 <= lifetime <= 600
            ):
                raise McpOAuthError("oauth_flow_lifetime_invalid")
            verifier, state = secrets.token_urlsafe(64), secrets.token_urlsafe(32)
            # RFC 8252 allows a varying loopback port; pre-registered redirect
            # paths must stay fixed. PKCE/state supply per-flow anti-forgery.
            callback_path = "/jarvis-oauth/callback"
            future = asyncio.get_running_loop().create_future()

            flow = None

            async def callback(reader, writer):
                await self._callback(flow, reader, writer)

            server = await asyncio.start_server(callback, "127.0.0.1", 0, limit=8192)
            port = server.sockets[0].getsockname()[1]
            redirect_uri = f"http://127.0.0.1:{port}{callback_path}"
            flow = _Flow(
                uuid4().hex,
                self._epoch,
                self._issuer,
                client_id or "",
                approved,
                redirect_uri,
                "",
                verifier,
                state,
                future,
                self.clock() + lifetime,
                callback_server=server,
                token_endpoint=self._metadata["token_endpoint"],
                requires_iss=self._metadata.get("authorization_response_iss_parameter_supported")
                is True,
            )
            self._flow = flow
            flow.expiry_task = asyncio.create_task(self._expire(flow, lifetime))
            if registration_mode == "dynamic":
                try:
                    registered = await self.register_client(redirect_uri)
                    self._check(flow.epoch)
                    client_id = flow.client_id = registered["client_id"]
                except BaseException:
                    flow.phase = "unknown" if self._unknown else "failed"
                    await asyncio.shield(self._close_callback(flow))
                    raise
            query = {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "resource": self.resource,
                "code_challenge_method": "S256",
                "state": state,
                "code_challenge": base64.urlsafe_b64encode(
                    hashlib.sha256(verifier.encode()).digest()
                )
                .decode()
                .rstrip("="),
            }
            if approved:
                query["scope"] = " ".join(approved)
            authorization_url = self._metadata["authorization_endpoint"]
            authorization_url += ("&" if urlsplit(authorization_url).query else "?") + urlencode(
                query
            )
            flow.authorization_url = authorization_url
            return {
                "flow_id": flow.id,
                "authorization_url": authorization_url,
                "issuer": flow.issuer,
                "resource": self.resource,
                "scopes": list(approved),
                "expires_in_seconds": int(lifetime),
                "status": flow.phase,
            }

    async def _expire(self, flow, lifetime):
        await asyncio.sleep(lifetime)
        if flow.phase == "waiting_owner":
            flow.phase = "expired"
            flow.verifier, flow.state = "", ""
            self._clear_code(flow)
            # Expiration closes only the owned callback; no token action occurs.
            try:
                await self._close_callback(flow)
            except McpOAuthError:
                flow.phase = "cleanup_pending"

    @staticmethod
    def _clear_code(flow):
        if not flow.future.done():
            flow.future.cancel()
        elif not flow.future.cancelled():
            with contextlib.suppress(Exception):
                flow.future.result()
        # Dropping the completed Future releases any authorization code it held.
        flow.future = asyncio.get_running_loop().create_future()
        flow.future.cancel()

    async def _callback(self, flow, reader, writer):
        task = asyncio.current_task()
        if flow is not None:
            flow.callback_tasks.add(task)
            flow.callback_writers.add(writer)
        status, text = 400, "La respuesta no corresponde a esta autorización."
        try:
            async with asyncio.timeout(5):
                raw = await reader.readuntil(b"\r\n\r\n")
                if len(raw) > 8192 or flow is None or flow.phase != "waiting_owner":
                    raise McpOAuthError("oauth_callback_invalid")
                lines = raw[:-4].decode("ascii").split("\r\n")
                method, target, protocol = lines[0].split(" ")
                if method != "GET" or protocol != "HTTP/1.1":
                    raise McpOAuthError("oauth_callback_invalid")
                headers = {}
                for row in lines[1:]:
                    key, value = row.split(":", 1)
                    if key.lower() in headers:
                        raise McpOAuthError("oauth_callback_invalid")
                    headers[key.lower()] = value.strip()
                redirect = urlsplit(flow.redirect_uri)
                parts = urlsplit(target)
                if (
                    parts.scheme
                    or parts.netloc
                    or parts.path != redirect.path
                    or headers.get("host") != redirect.netloc
                    or "origin" in headers
                    or headers.get("content-length", "0") != "0"
                    or "transfer-encoding" in headers
                ):
                    raise McpOAuthError("oauth_callback_invalid")
                pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
                if len(pairs) > 6 or len(dict(pairs)) != len(pairs):
                    raise McpOAuthError("oauth_callback_invalid")
                values = dict(pairs)
                if set(values) - {
                    "code",
                    "state",
                    "iss",
                    "error",
                    "error_description",
                    "error_uri",
                }:
                    raise McpOAuthError("oauth_callback_invalid")
                self._check(flow.epoch)
                if self.clock() >= flow.expires_at or not hmac.compare_digest(
                    values.get("state", ""), flow.state
                ):
                    raise McpOAuthError("oauth_state_invalid")
                requires_iss = flow.requires_iss
                if (requires_iss and "iss" not in values) or (
                    "iss" in values and values["iss"] != flow.issuer
                ):
                    raise McpOAuthError("oauth_issuer_mismatch")
                if "error" in values:
                    if not flow.future.done():
                        flow.future.set_exception(McpOAuthError("oauth_owner_denied"))
                    status, text = 200, "La autorización fue rechazada. Puedes volver a Jarvis."
                elif not _plain(values.get("code"), 4096) or flow.future.done():
                    raise McpOAuthError("oauth_callback_invalid")
                else:
                    flow.future.set_result(values["code"])
                    status, text = 200, "Respuesta recibida. Puedes volver a Jarvis."
        except (
            ValueError,
            UnicodeError,
            OSError,
            TimeoutError,
            McpOAuthError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            pass
        finally:
            body = text.encode()
            try:
                writer.write(
                    f"HTTP/1.1 {status} {'OK' if status == 200 else 'Bad Request'}\r\n".encode()
                    + b"Content-Type: text/plain; charset=utf-8\r\nCache-Control: no-store\r\n"
                    + b"Content-Security-Policy: default-src 'none'; frame-ancestors 'none'\r\n"
                    + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                    + body
                )
                await asyncio.wait_for(writer.drain(), 1)
            except (OSError, TimeoutError):
                pass
            finally:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 1)
                except (OSError, TimeoutError):
                    pass  # Retain the exact writer until a later close proves release.
                else:
                    if flow is not None:
                        flow.callback_writers.discard(writer)
                if flow is not None:
                    flow.callback_tasks.discard(task)

    async def _close_callback(self, flow):
        timer = flow.expiry_task
        if timer is not None and timer is not asyncio.current_task() and not timer.done():
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)
        if flow.callback_server is not None:
            flow.callback_server.close()
            await flow.callback_server.wait_closed()
            flow.callback_server = None
        tasks = tuple(task for task in flow.callback_tasks if task is not asyncio.current_task())
        for writer in tuple(flow.callback_writers):
            writer.close()
        for task in tasks:
            task.cancel()
        if tasks:
            _done, pending = await asyncio.wait(tasks, timeout=2)
            if pending:
                raise McpOAuthError("oauth_cleanup_pending")
        for writer in tuple(flow.callback_writers):
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except (TimeoutError, OSError):
                raise McpOAuthError("oauth_cleanup_pending") from None
            flow.callback_writers.discard(writer)

    def _read_token(
        self, value, *, client_id, approved, issuer, token_endpoint, previous_refresh=None
    ):
        if (
            not isinstance(value, Mapping)
            or value.get("token_type", "").lower() != "bearer"
            or not _plain(value.get("access_token"))
            or not _TOKEN.fullmatch(value["access_token"])
            or type(value.get("expires_in")) not in {int, float}
            or not math.isfinite(value["expires_in"])
            or not 1 <= value["expires_in"] <= 2_592_000
        ):
            raise McpOAuthError("oauth_token_response_invalid")
        scopes = _scopes(
            value["scope"].split() if isinstance(value.get("scope"), str) else approved
        )
        if not set(approved) <= set(scopes) or not set(scopes) <= set(approved):
            raise McpOAuthError("oauth_scope_mismatch")
        refresh = value.get("refresh_token", previous_refresh)
        if refresh is not None and not _plain(refresh):
            raise McpOAuthError("oauth_token_response_invalid")
        return _Grant(
            value["access_token"],
            refresh,
            client_id,
            scopes,
            self.clock() + float(value["expires_in"]),
            issuer,
            token_endpoint,
        )

    async def finish_authorization(self, flow_id):
        async with self._lock:
            self._check()
            flow = self._flow
            if flow is None or flow.id != flow_id:
                raise McpOAuthError("oauth_flow_not_found")
            if flow.phase == "complete":
                return self.status()
            if flow.phase != "waiting_owner":
                raise McpOAuthError("oauth_flow_not_replayable", uncertain=flow.dispatched)
            if self.clock() >= flow.expires_at:
                flow.phase = "expired"
                await self._close_callback(flow)
                raise McpOAuthError("oauth_flow_expired")
            if not flow.future.done():
                return {"flow_id": flow.id, "status": "waiting_owner"}
            flow.phase = "exchanging"
            try:
                code = flow.future.result()
                self._check(flow.epoch)

                def dispatch():
                    self._check(flow.epoch)
                    flow.dispatched = True

                body = urlencode(
                    {
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": flow.redirect_uri,
                        "client_id": flow.client_id,
                        "code_verifier": flow.verifier,
                        "resource": self.resource,
                    }
                ).encode()
                value = await self.network.json(
                    flow.token_endpoint,
                    method="POST",
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "application/json",
                    },
                    body=body,
                    before_dispatch=dispatch,
                    maximum=131072,
                    timeout=20,
                )
                self._check(flow.epoch)
                self._grant = self._read_token(
                    value,
                    client_id=flow.client_id,
                    approved=flow.scopes,
                    issuer=flow.issuer,
                    token_endpoint=flow.token_endpoint,
                )
                self._epoch += 1
                flow.phase = "complete"
                return self.status()
            except BaseException as error:
                self._unknown = flow.dispatched
                flow.phase = "unknown" if flow.dispatched else "failed"
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise McpOAuthError(
                    "oauth_exchange_unknown" if flow.dispatched else "oauth_exchange_failed",
                    uncertain=flow.dispatched,
                ) from None
            finally:
                flow.verifier, flow.state = "", ""
                self._clear_code(flow)
                await asyncio.shield(self._close_callback(flow))

    def credential_lease(self, resource, *, required_scopes=()):
        self._check()
        if resource != self.resource or self._grant is None:
            raise McpOAuthError("oauth_authorization_required")
        if self.clock() >= self._grant.expires_at:
            raise McpOAuthError("oauth_refresh_required")
        if not set(_scopes(required_scopes)) <= set(self._grant.scopes):
            raise McpOAuthError("oauth_insufficient_scope")
        return BearerLease(
            self.resource,
            self._grant.scopes,
            self._epoch,
            self._grant.expires_at,
            self,
            self._grant.access_token,
        )

    def _assert_lease(self, lease):
        self._check(lease.epoch)
        if (
            lease.resource != self.resource
            or self._grant is None
            or self._unknown
            or self.clock() >= lease.expires_at
            or not hmac.compare_digest(lease._token, self._grant.access_token)
        ):
            raise McpOAuthError("oauth_authorization_required")

    async def refresh(self):
        async with self._lock:
            self._check()
            grant, epoch = self._grant, self._epoch
            if grant is None or grant.refresh_token is None or self._unknown:
                raise McpOAuthError("oauth_authorization_required")
            dispatched = False

            def dispatch():
                nonlocal dispatched
                self._check(epoch)
                dispatched = True

            try:
                value = await self.network.json(
                    grant.token_endpoint,
                    method="POST",
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "application/json",
                    },
                    body=urlencode(
                        {
                            "grant_type": "refresh_token",
                            "refresh_token": grant.refresh_token,
                            "client_id": grant.client_id,
                            "resource": self.resource,
                            "scope": " ".join(grant.scopes),
                        }
                    ).encode(),
                    before_dispatch=dispatch,
                    maximum=131072,
                    timeout=20,
                )
                self._check(epoch)
                refreshed = self._read_token(
                    value,
                    client_id=grant.client_id,
                    approved=grant.scopes,
                    issuer=grant.issuer,
                    token_endpoint=grant.token_endpoint,
                    previous_refresh=grant.refresh_token,
                )
                if refreshed.refresh_token == grant.refresh_token:
                    raise McpOAuthError("oauth_refresh_rotation_required")
                self._epoch += 1
                self._grant = refreshed
                return self.status()
            except BaseException:
                if dispatched:
                    self._unknown, self._grant = True, None
                    self._epoch += 1
                raise McpOAuthError(
                    "oauth_refresh_unknown" if dispatched else "oauth_refresh_failed",
                    uncertain=dispatched,
                ) from None

    async def revoke(self):
        # Local revocation precedes every await and invalidates captured leases.
        self._epoch += 1
        self._grant, self._unknown = None, False
        if self._flow is not None:
            self._flow.phase = "revoked"
            self._flow.verifier, self._flow.state = "", ""
            self._clear_code(self._flow)
            await self._close_callback(self._flow)
        return {"revoked": True, "local_only": True, "tokens_persisted": False}

    def status(self):
        grant = self._grant
        return {
            "connected": bool(grant and not self._unknown and self.clock() < grant.expires_at),
            "resource": self.resource,
            "issuer": self._issuer,
            "tokens_persisted": False,
            "scopes": list(grant.scopes if grant else ()),
            "uncertain": self._unknown,
            "flow_id": self._flow.id if self._flow else None,
            "flow_status": self._flow.phase if self._flow else None,
            "expires_in_seconds": max(0, int(grant.expires_at - self.clock())) if grant else 0,
        }

    @property
    def blockers(self):
        return self.network.blockers + (
            ("mcp_oauth_callback_active",)
            if self._flow
            and (
                self._flow.callback_server is not None
                or self._flow.callback_tasks
                or self._flow.callback_writers
            )
            else ()
        )

    async def aclose(self):
        self._closed = True
        await self.revoke()
        await self.network.aclose()
        return {"closed": True, "cleanup_confirmed": True, "tokens_persisted": False}
