"""Owner-bound MCP OAuth and loopback relay control, with RAM-only credentials.

The host admits each RPC once. Network configuration, issuer/PKCE and export
reviews never approve a tool: relay calls enter the existing Runtime dispatcher.
No token, authorization code, state or verifier is put in durable event/state data.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import time
from dataclasses import dataclass
from functools import wraps
from urllib.parse import urlsplit
from uuid import uuid4

from .client_identity import ClientIdentityError, profile_binding
from .host_profile import HostProfileError
from .mcp import McpError
from .mcp_http import McpHTTPNetwork
from .mcp_oauth import McpOAuthClient, McpOAuthError
from .mcp_registry import McpRegistryError, ServerSpec
from .mcp_server import McpRelayError, McpRelayServer
from .protocol import RpcError, authenticated_client

MCP_NETWORK_READ_RPCS = frozenset(
    {
        "mcp.oauth.status",
        "mcp.oauth.discover",
        "mcp.relay.status",
        "mcp.relay.review",
        "mcp.relay.token",
    }
)
_BASE = {"session_id", "temporary"}


def _params(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise RpcError(-32602, "Los parámetros de la conexión MCP no son válidos.")


def _digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _endpoint(callback):
    @wraps(callback)
    async def invoke(self, value):
        try:
            return await callback(self, value)
        except RpcError:
            raise
        except ClientIdentityError:
            raise RpcError(
                -32003, "Se requiere el cliente dueño que revisó esta conexión."
            ) from None
        except (
            McpOAuthError,
            McpRelayError,
            McpError,
            McpRegistryError,
            HostProfileError,
        ) as error:
            code = getattr(error, "code", str(error))
            if not re.fullmatch(r"[a-z_]{1,96}", code):
                code = "mcp_network_unavailable"
            raise RpcError(
                -32011,
                f"La conexión requiere revisión: {code}. No se reenvía automáticamente.",
            ) from None
        except (OSError, ValueError, TypeError, UnicodeError):
            raise RpcError(-32602, "No se pudo validar esta conexión MCP.") from None

    return invoke


@dataclass
class _OAuth:
    spec: ServerSpec
    owner: str
    guard: object
    client: McpOAuthClient
    lock: asyncio.Lock


class MCPNetworkManagementAPI:
    def mcp_network_handlers(self):
        return {
            "mcp.oauth.discover": self.mcp_oauth_discover,
            "mcp.oauth.begin": self.mcp_oauth_begin,
            "mcp.oauth.finish": self.mcp_oauth_finish,
            "mcp.oauth.refresh": self.mcp_oauth_refresh,
            "mcp.oauth.revoke": self.mcp_oauth_revoke,
            "mcp.oauth.status": self.mcp_oauth_status,
            "mcp.relay.status": self.mcp_relay_status,
            "mcp.relay.review": self.mcp_relay_review,
            "mcp.relay.decide": self.mcp_relay_decide,
            "mcp.relay.token": self.mcp_relay_token,
        }

    def _network_state(self):
        if not hasattr(self, "_mcp_network_state"):
            self._mcp_network_state = {
                "oauth": {},
                "reviews": {},
                "relay": None,
                "relay_owner": None,
                "exports": (),
                "tasks": set(),
                "closing": False,
                "transition": False,
                "requires_review": False,
                "relay_binding": None,
                "catalog_notice": None,
                "catalog_notice_delivery": None,
                "relay_identity_guard": None,
                "relay_mcp_bindings": (),
            }
        return self._mcp_network_state

    def _network_guard(self, *, ref=None, spec=None):
        owner = authenticated_client.get()
        if not isinstance(owner, str) or not re.fullmatch(r"[a-f0-9]{64}", owner):
            raise ClientIdentityError("client_not_admitted")
        self.client_authority.lookup(owner, scope="owner")
        self.profile_lease.assert_current()
        physical, profile = profile_binding(self.directory), self.profile_lease.scope.profile_id
        workspace = self._workspace_id
        agent = self.agent_profiles.get(self.store.get_session(ref).agent_id) if ref else None

        def guard():
            self.client_authority.lookup(owner, scope="owner")
            self.profile_lease.assert_current()
            if (
                self._closing
                or self._network_state()["closing"]
                or profile_binding(self.directory) != physical
                or self.profile_lease.scope.profile_id != profile
                or self._workspace_id != workspace
            ):
                raise McpRelayError("mcp_network_context_changed")
            self._assert_workspace_current()
            if agent is not None and (
                self.store.get_session(ref).agent_id != agent.agent_id
                or self.agent_profiles.get(agent.agent_id).fingerprint != agent.fingerprint
                or ref in self._retiring_sessions
            ):
                raise McpRelayError("mcp_network_context_changed")
            if spec is not None and self._network_spec(spec.server_id) != spec:
                raise McpRelayError("mcp_network_configuration_changed")

        guard()
        return owner, guard

    def _network_spec(self, identity):
        if not isinstance(identity, str):
            raise McpRegistryError("mcp_server_id_invalid")
        raw = next(
            (spec for spec in self.config.server_specs if spec["server_id"] == identity), None
        )
        if raw is None:
            raise McpRegistryError("mcp_server_not_configured")
        spec = ServerSpec(**raw)
        if spec.transport == "stdio" or spec.oauth is None:
            raise McpOAuthError("oauth_not_configured")
        return spec

    def _network_oauth(self, identity, *, create=False):
        state = self._network_state()
        spec = self._network_spec(identity)
        owner, guard = self._network_guard(spec=spec)
        entry = state["oauth"].get(identity)
        if entry is not None:
            if entry.owner != owner:
                raise ClientIdentityError("client_not_admitted")
            if entry.spec != spec:
                raise McpOAuthError("oauth_configuration_changed")
            entry.guard()
            return entry
        if not create:
            return None
        if len(state["oauth"]) >= 32:
            raise McpOAuthError("oauth_capacity")
        # The owner's loopback grant names exact endpoint/resource origins.
        # Remote metadata cannot turn it into access to every local port.
        origins = (
            tuple(
                dict.fromkeys(
                    f"{parts.scheme}://{parts.netloc}"
                    for parts in (urlsplit(spec.endpoint), urlsplit(spec.oauth["resource"]))
                    if parts.scheme == "http"
                )
            )
            if spec.allow_loopback
            else ()
        )
        network = McpHTTPNetwork(test_origins=origins, before_effect=guard)
        entry = _OAuth(
            spec,
            owner,
            guard,
            McpOAuthClient(spec.oauth["resource"], network, before_effect=guard),
            asyncio.Lock(),
        )
        state["oauth"][identity] = entry
        return entry

    @_endpoint
    async def mcp_oauth_discover(self, value):
        _params(value, {"server_id", "issuer"}, {"server_id"})
        entry = self._network_oauth(value["server_id"], create=True)
        async with entry.lock:
            metadata = await entry.client.discover(issuer=value.get("issuer"))
            entry.guard()
            return {"metadata": metadata, "status": entry.client.status()}

    @_endpoint
    async def mcp_oauth_begin(self, value):
        _params(value, {"server_id", "client_id", "scopes", "registration_mode"}, {"server_id"})
        entry = self._network_oauth(value["server_id"], create=True)
        client_id = value.get("client_id", entry.spec.oauth["client_id"])
        scopes = list(value.get("scopes", entry.spec.oauth["scopes"]))
        # The configured public client/audience is part of the native review.
        # Broadening it requires editing/reviewing the server configuration first.
        if client_id != entry.spec.oauth["client_id"] or scopes != list(entry.spec.oauth["scopes"]):
            raise McpOAuthError("oauth_configuration_changed")
        async with entry.lock:
            if hasattr(self, "_mcp_authorization_changed"):
                await self._mcp_authorization_changed(entry.spec.server_id)
            flow = await entry.client.begin_authorization(
                client_id,
                scopes,
                registration_mode=value.get("registration_mode", "pre_registered"),
            )
            entry.guard()
            return {"flow": flow}

    @_endpoint
    async def mcp_oauth_finish(self, value):
        _params(value, {"server_id", "flow_id"}, {"server_id", "flow_id"})
        entry = self._network_oauth(value["server_id"])
        if entry is None:
            raise McpOAuthError("oauth_flow_not_found")
        async with entry.lock:
            result = await entry.client.finish_authorization(value["flow_id"])
            entry.guard()
            return {
                "status": "connected" if result.get("connected") else "authorization_pending",
                "authorization": result,
            }

    @_endpoint
    async def mcp_oauth_refresh(self, value):
        """Explicit owner refresh, never a hidden retry of an MCP operation."""
        _params(value, {"server_id"}, {"server_id"})
        entry = self._network_oauth(value["server_id"])
        if entry is None:
            raise McpOAuthError("oauth_authorization_required")
        async with entry.lock:
            if hasattr(self, "_mcp_authorization_changed"):
                await self._mcp_authorization_changed(entry.spec.server_id)
            result = await entry.client.refresh()
            entry.guard()
            return {
                "status": "connected" if result.get("connected") else "failed",
                "authorization": result,
            }

    @_endpoint
    async def mcp_oauth_revoke(self, value):
        _params(value, {"server_id"}, {"server_id"})
        entry = self._network_oauth(value["server_id"])
        if entry is None:
            if hasattr(self, "_mcp_authorization_changed"):
                await self._mcp_authorization_changed(value["server_id"])
            return {"revoked": True, "local_only": True, "tokens_persisted": False}
        try:
            result = await entry.client.revoke()
        finally:
            # Revocation invalidates grants synchronously before its first
            # await. Views must close even if callback cleanup is still pending.
            if hasattr(self, "_mcp_authorization_changed"):
                await self._mcp_authorization_changed(entry.spec.server_id)
        return result

    @_endpoint
    async def mcp_oauth_status(self, value):
        _params(value, {"server_id"}, {"server_id"})
        entry = self._network_oauth(value["server_id"])
        return {
            "status": entry.client.status()
            if entry
            else {
                "connected": False,
                "tokens_persisted": False,
                "uncertain": False,
                "flow_id": None,
                "flow_status": None,
                "expires_in_seconds": 0,
            }
        }

    def mcp_network_credentials(self, spec):
        state = getattr(self, "_mcp_network_state", None)
        entry = state["oauth"].get(spec.server_id) if state else None
        if entry is None or entry.spec != spec:
            raise McpOAuthError("oauth_authorization_required")
        entry.guard()
        return entry.client.credential_lease(
            entry.client.resource, required_scopes=spec.oauth["scopes"]
        )

    def _network_exports(self, names):
        if (
            not isinstance(names, list)
            or len(names) > 128
            or len(set(names)) != len(names)
            or any(not isinstance(name, str) for name in names)
        ):
            raise McpRelayError("mcp_relay_exports_invalid")
        available = {spec["name"]: spec for spec in self.catalog.specs()}
        if set(names) - available.keys():
            raise McpRelayError("mcp_relay_exports_changed")
        return _digest([available[name] for name in sorted(names)])

    def _network_relay_public(self):
        state = self._network_state()
        self.mcp_network_catalog_changed()
        relay = state["relay"]
        raw = relay.status() if relay is not None else {}
        return {
            "running": raw.get("running", False),
            "endpoint": raw.get("endpoint"),
            "export_count": len(state["exports"]),
            "remote_clients": raw.get("active_connections", 0),
            "token_available": raw.get("running", False)
            and not state["requires_review"]
            and not state["transition"],
            "requires_review": state["requires_review"],
            "uncertain_requests": raw.get("uncertain_requests", 0),
            "request_retention_window": raw.get("request_retention_window", 128),
        }

    @_endpoint
    async def mcp_relay_status(self, value):
        _params(value, set())
        self._network_guard()
        return {"relay": self._network_relay_public()}

    @_endpoint
    async def mcp_relay_review(self, value):
        _params(value, _BASE | {"action", "exports", "port"}, {"session_id", "action", "exports"})
        if value["action"] not in {"start", "stop", "revoke"} or value.get("port", 0) != 0:
            raise McpRelayError("mcp_relay_review_invalid")
        ref = self._ref(value)
        owner, guard = self._network_guard(ref=ref)
        if value["action"] == "start":
            self._management_idle()
        elif getattr(self, "_maintenance", False):
            raise McpRelayError("mcp_relay_control_busy")
        state = self._network_state()
        if value["action"] == "start":
            exports = value["exports"]
            exports_digest = self._network_exports(exports)
        else:
            # Stale/removed exports cannot prevent the owner closing an exact
            # listener. Only a fresh start binds the current tool catalog.
            exports = list(state["exports"])
            exports_digest = _digest(exports)
        if state["transition"]:
            raise McpRelayError("mcp_relay_busy")
        state["reviews"] = {
            key: item
            for key, item in state["reviews"].items()
            if item["expires"] > time.monotonic()
        }
        if len(state["reviews"]) >= 16:
            raise McpRelayError("mcp_relay_review_capacity")
        relay = state["relay"]
        if value["action"] == "start":
            if relay is not None:
                raise McpRelayError("mcp_relay_already_started")
        elif relay is None or state["relay_owner"] != owner:
            raise McpRelayError("mcp_relay_not_ready")
        identity = uuid4().hex
        public = {
            "review_id": identity,
            "action": value["action"],
            "address": "127.0.0.1",
            "port": 0,
            "exports": sorted(exports),
            "exports_digest": exports_digest,
            "expires_ns": time.time_ns() + 120_000_000_000,
            "session_id": ref.session_id,
            "temporary": ref.temporary,
            "authentication_required": True,
            "operation_approval_required": True,
        }
        public["digest"] = _digest(public)
        state["reviews"][identity] = {
            "public": public,
            "guard": guard,
            "owner": owner,
            "ref": ref,
            "catalog": self.catalog,
            "scope": self.profile_lease.scope,
            "relay": relay,
            "expires": time.monotonic() + 120,
        }
        return {"review": public}

    @_endpoint
    async def mcp_relay_decide(self, value):
        _params(value, {"review_id", "digest", "allow"}, {"review_id", "digest", "allow"})
        owner, _guard = self._network_guard()
        if type(value["allow"]) is not bool:
            raise McpRelayError("mcp_relay_decision_invalid")
        state = self._network_state()
        ticket = state["reviews"].get(value["review_id"])
        if (
            ticket is None
            or ticket["owner"] != owner
            or ticket["expires"] <= time.monotonic()
            or ticket["public"]["digest"] != value["digest"]
            or state["transition"]
            or ticket["relay"] is not state["relay"]
        ):
            raise McpRelayError("mcp_relay_review_expired")
        self.profile_lease.assert_current(ticket["scope"])
        ticket["guard"]()
        public = ticket["public"]
        if public["action"] == "start" and (
            self.catalog is not ticket["catalog"]
            or self._network_exports(public["exports"]) != public["exports_digest"]
        ):
            raise McpRelayError("mcp_relay_exports_changed")
        state["reviews"].pop(value["review_id"])
        if not value["allow"]:
            return {"denied": True, "relay": self._network_relay_public()}
        if public["action"] == "start":
            self._management_idle()
        elif getattr(self, "_maintenance", False):
            raise McpRelayError("mcp_relay_control_busy")
        state["transition"] = True
        try:
            if public["action"] == "start":
                epoch = self._network_catalog_epoch()
                state["requires_review"] = False
                state["catalog_notice_delivery"] = None
                state["relay_binding"] = (epoch, public["exports_digest"])
                state["relay_identity_guard"] = ticket["guard"]
                registry = getattr(self, "mcp", None)
                state["relay_mcp_bindings"] = tuple(
                    binding
                    for name in public["exports"]
                    if (binding := getattr(registry, "_bindings", {}).get(name)) is not None
                )

                def guard():
                    ticket["guard"]()
                    if (
                        state["requires_review"]
                        or not self._network_mcp_bindings_current()
                        or self._network_catalog_epoch() != epoch
                        or self._network_exports(public["exports"]) != public["exports_digest"]
                    ):
                        raise McpRelayError("mcp_relay_exports_changed")

                async def dispatch(method, params, context):
                    guard()
                    token = authenticated_client.set(owner)
                    try:
                        return await self.mcp_product.relay_dispatch(
                            method,
                            params,
                            context,
                            ref=ticket["ref"],
                            exports=tuple(public["exports"]),
                            assert_current=guard,
                        )
                    finally:
                        authenticated_client.reset(token)

                relay = McpRelayServer(
                    dispatch,
                    before_effect=ticket["guard"],
                    dispatch_guard=guard,
                    capabilities={"tools": {"listChanged": True}},
                )
                state["relay"], state["relay_owner"], state["exports"] = (
                    relay,
                    owner,
                    tuple(public["exports"]),
                )
                await relay.start()
                guard()
            elif public["action"] == "stop":
                await state["relay"].aclose()
                notice = state["catalog_notice"]
                if notice is not None:
                    with contextlib.suppress(McpRelayError):
                        await asyncio.shield(notice)
                    if notice.done():
                        state["tasks"].discard(notice)
                state["catalog_notice"] = None
                state["relay"], state["relay_owner"], state["exports"] = None, None, ()
                state["requires_review"], state["relay_binding"] = False, None
                state["relay_identity_guard"], state["relay_mcp_bindings"] = None, ()
            else:
                await state["relay"].revoke(close_views=True)
            return {
                "relay": self._network_relay_public(),
                "applied": True,
                "receipt": {
                    "status": "known",
                    "review_digest": value["digest"],
                    "tool_authorizations_granted": False,
                },
            }
        finally:
            state["transition"] = False

    @_endpoint
    async def mcp_relay_token(self, value):
        _params(value, set())
        owner, _guard = self._network_guard()
        state = self._network_state()
        if state["relay"] is None or state["relay_owner"] != owner:
            raise McpRelayError("mcp_relay_not_ready")
        if state["requires_review"] or state["transition"]:
            raise McpRelayError("mcp_relay_requires_review")
        return {
            "token": state["relay"].pairing_credential()["bearer_token"],
            "persisted": False,
            "owner_only": True,
        }

    def _network_catalog_epoch(self):
        return getattr(self, "_catalog_fingerprint", None) or _digest(self.catalog.specs())

    def _network_mcp_bindings_current(self):
        state = self._network_state()
        registry = getattr(self, "mcp", None)
        return all(
            registry is not None and registry._binding_current(binding)
            for binding in state["relay_mcp_bindings"]
        )

    def mcp_network_catalog_changed(self):
        """Synchronous effect fence; delivery/cleanup is owned until settled.

        Rebuilding an identical catalog does not invalidate an exported view.
        Real changes fence immediately, before a task can observe/dispatch work.
        No peer work is automatically reissued when a view is invalidated.
        """
        state = getattr(self, "_mcp_network_state", None)
        if state is None or state["relay"] is None or state["closing"]:
            return
        try:
            if state["relay_identity_guard"] is not None:
                state["relay_identity_guard"]()
            binding = (self._network_catalog_epoch(), self._network_exports(list(state["exports"])))
            if not self._network_mcp_bindings_current():
                binding = None
        except Exception:
            # A revoked/deleted context is a fence, never a reason to expose
            # private exception text or keep advertising an unusable channel.
            binding = None
        if binding == state["relay_binding"] or state["requires_review"]:
            return
        state["requires_review"] = True
        relay = state["relay"]

        async def publish_and_close():
            try:
                state["catalog_notice_delivery"] = await relay.notify(
                    "notifications/tools/list_changed", wait_flushed=True, timeout=1
                )
            finally:
                # Closing owned views cancels their Runtime requests and waits
                # actual local cleanup. The listener remains for owner controls.
                await relay.revoke(close_views=True)

        task = asyncio.create_task(publish_and_close())
        state["catalog_notice"] = task
        state["tasks"].add(task)
        task.add_done_callback(
            lambda done: (
                state["tasks"].discard(done)
                if not done.cancelled() and done.exception() is None
                else None
            )
        )

    @property
    def mcp_network_blockers(self):
        state = getattr(self, "_mcp_network_state", None)
        if state is None:
            return ()
        blockers = []
        for entry in state["oauth"].values():
            blockers.extend(entry.client.blockers)
        if state["relay"] is not None:
            if state["relay"].status()["running"]:
                blockers.append("mcp_relay_listener_active")
            blockers.extend(state["relay"].blockers)
        if state["transition"] or state["tasks"]:
            blockers.append("mcp_network_cleanup_pending")
        return tuple(sorted(set(blockers)))

    def mcp_network_client_revoked(self, owner):
        state = getattr(self, "_mcp_network_state", None)
        if state is None:
            return
        resources = [entry.client for entry in state["oauth"].values() if entry.owner == owner]
        if state["relay"] is not None and state["relay_owner"] == owner:
            resources.append(state["relay"])
        for resource in resources:
            # Captured guards consult client_authority synchronously. Closing
            # releases owned sockets and invalidates all RAM credentials as well.
            task = asyncio.create_task(resource.aclose())
            state["tasks"].add(task)
            task.add_done_callback(
                lambda done: (
                    state["tasks"].discard(done)
                    if not done.cancelled() and done.exception() is None
                    else None
                )
            )

    async def close_mcp_network_server(self, server_id):
        """Trusted host removal hook: close before deleting its public config."""
        state = getattr(self, "_mcp_network_state", None)
        entry = state["oauth"].get(server_id) if state else None
        if entry is not None:
            await entry.client.aclose()
            if entry.client.blockers:
                raise McpOAuthError("oauth_cleanup_pending")
            state["oauth"].pop(server_id)
        return {"closed": True, "local_cleanup_confirmed": True}

    async def close_mcp_network(self):
        state = getattr(self, "_mcp_network_state", None)
        if state is None:
            return {"closed": True, "local_cleanup_confirmed": True}
        state["closing"] = True
        for entry in state["oauth"].values():
            await entry.client.aclose()
        if state["relay"] is not None:
            await state["relay"].aclose()
        tasks = tuple(state["tasks"])
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            for task in tasks:
                with contextlib.suppress(BaseException):
                    task.result()
            state["tasks"].clear()
        state["reviews"].clear()
        return {
            "closed": True,
            "local_cleanup_confirmed": True,
            "remote_effects_may_continue": True,
        }
