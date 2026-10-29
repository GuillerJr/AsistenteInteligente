"""Explicit native-owner Telegram setup and same-authority inbound dispatch.

No RPC injects a transport or chooses an existing conversation. A reviewed route
owns a new durable Jarvis session. Only the owner starts polling; messages from
Telegram remain text and cannot decide native approval cards.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from uuid import uuid4

from jarvis_core.host_profile import profile_background
from jarvis_core.management import params
from jarvis_core.protocol import RpcError, authenticated_client
from jarvis_core.state import SessionRef, StateError
from jarvis_core.telegram import (
    _UUID,
    IncomingMessage,
    TelegramChannel,
    TelegramClient,
    TelegramDispatchResult,
    TelegramError,
    TelegramJournal,
    TelegramRoute,
    _chat_id,
    _route,
)
from jarvis_core.telegram_group_management import TelegramGroupManagementAPI
from jarvis_core.telegram_pairing import TelegramPairing


class TelegramManagementAPI(TelegramGroupManagementAPI):
    def initialize_telegram(
        self, *, client_factory=TelegramClient, pairing_factory=TelegramPairing
    ):
        """Host-only initialization; never connects or reads credentials."""
        if getattr(self, "_telegram_channel", None) is not None:
            raise TelegramError("telegram_already_initialized")
        if not callable(client_factory):
            raise TelegramError("telegram_client_factory_invalid")
        self._telegram_client_factory = client_factory
        from jarvis_core.host_profile import BoundProfileResource

        self._telegram_journal = BoundProfileResource(
            TelegramJournal(self.directory / "telegram"), self.profile_lease, self.profile_gate
        )
        self._telegram_channel = None
        self._telegram_mutating = False
        self._telegram_workspace_id = None
        self._telegram_last_error = None
        self._telegram_owner = None
        self._telegram_pairing = None
        self._telegram_pairing_owner = None
        self._telegram_pairing_proof_chat = None
        self._telegram_pairing_factory = pairing_factory
        self._telegram_cancelled_runs = set()
        self._telegram_group_pairing = None
        self._telegram_group_owner = None
        self._telegram_group_proof_chat = None
        self._telegram_run_context = {}
        # A restored route is historical configuration. A new local review
        # cannot prove a live chat: each process/connection needs a fresh claim.
        self._telegram_restored_pairings = {}
        self._telegram_pairing_proof_owners = {}

    def _telegram_client_revoked(self, client_id):
        """Withdraw admission synchronously; retain every cleanup owner/lease."""
        if self._telegram_owner == client_id:
            channel = self._telegram_channel
            if channel is not None:
                channel._stop.set()
                channel._send_tickets.clear()
                if channel._worker is not None and not channel._worker.done():
                    channel._worker.cancel()
        for attribute, owner in (("_telegram_pairing", self._telegram_pairing_owner),
                                 ("_telegram_group_pairing", self._telegram_group_owner)):
            pairing = getattr(self, attribute)
            if owner == client_id and pairing is not None:
                pairing._invalidate("cancelled")
        for chat_id, owner in tuple(self._telegram_pairing_proof_owners.items()):
            if owner == client_id:
                self._telegram_restored_pairings.pop(chat_id, None)
                self._telegram_pairing_proof_owners.pop(chat_id, None)

    def telegram_handlers(self):
        return {
            **self.telegram_group_handlers(),
            "telegram.status": self.telegram_status,
            "telegram.connect": self.telegram_connect,
            "telegram.disconnect": self.telegram_disconnect,
            "telegram.routes.create": self.telegram_create_route,
            "telegram.routes.update": self.telegram_update_route,
            "telegram.routes.remove": self.telegram_remove_route,
            "telegram.start": self.telegram_start,
            "telegram.stop": self.telegram_stop,
            "telegram.history": self.telegram_history,
            "telegram.events.cancel": self.telegram_cancel_event,
            "telegram.destination": self.telegram_destination,
            "telegram.pairing.review": self.telegram_pairing_review,
            "telegram.pairing.start": self.telegram_pairing_start,
            "telegram.pairing.open": self.telegram_pairing_open,
            "telegram.pairing.status": self.telegram_pairing_status,
            "telegram.pairing.confirm": self.telegram_pairing_confirm,
            "telegram.pairing.cancel": self.telegram_pairing_cancel,
            "telegram.restore.pairing.confirm": self.telegram_restore_pairing_confirm,
        }

    def telegram_uses_agent(self, agent_id):
        return any(row.agent_id == agent_id for row in self._telegram_journal.routes()) or (
            self._telegram_pairing is not None
            and self._telegram_pairing.busy
            and self._telegram_pairing.agent_id == agent_id
        ) or (self._telegram_group_pairing is not None and self._telegram_group_pairing.busy
              and self._telegram_group_pairing.agent_id == agent_id)

    def telegram_uses_session(self, ref):
        return not ref.temporary and any(
            row.session_id == ref.session_id for row in self._telegram_journal.routes()
        )

    def telegram_blocks_workspace_change(self):
        channel = self._telegram_channel
        return (
            self._telegram_mutating
            or (channel is not None and channel._lease is not None)
            or (self._telegram_pairing is not None and self._telegram_pairing.busy)
            or (self._telegram_group_pairing is not None and self._telegram_group_pairing.busy)
        )

    def _telegram_configuration(self):
        # Review rows and their hash must come from one transactional snapshot.
        value = self._telegram_journal.document.read()
        internal_hash = hashlib.sha256(
            json.dumps(
                [value["bot_id"], value["routes"]], sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        digest = hashlib.sha256(
            json.dumps([internal_hash, self._workspace_id], separators=(",", ":")).encode()
        ).hexdigest()
        rows = [dict(row) for _, row in sorted(value["routes"].items())]
        return rows, internal_hash, digest

    def _telegram_snapshot(self):
        routes, _internal_hash, digest = self._telegram_configuration()
        channel = self._telegram_channel
        identity = channel.client.identity if channel is not None else None
        return {
            "connected": identity is not None and not channel.client.closed,
            "running": channel.running if channel is not None else False,
            "bot_id": identity.bot_id if identity is not None else None,
            "username": identity.username if identity is not None else None,
            "routes": routes,
            "configuration_hash": digest,
            "workspace_id": self._workspace_id,
            "pinned_workspace_id": self._telegram_workspace_id,
            "error": (channel.error_code if channel is not None else None)
            or self._telegram_last_error,
            "restored_pairing_required_chat_ids": self._telegram_missing_restored_pairings(routes),
        }

    def _telegram_restored_pairing_key(self, route):
        identity = (self._telegram_channel.client.identity
                    if self._telegram_channel is not None else None)
        agent = self.agent_profiles.get(route["agent_id"])
        return (identity.bot_id if identity else None, route["chat_id"], route["agent_id"],
                agent.revision, route["session_id"], route["revision"],
                self._workspace_id, self.workspace_tools.identity,
                self._telegram_configuration()[2])

    def _telegram_missing_restored_pairings(self, routes=None):
        if routes is None:
            routes = self._telegram_configuration()[0]
        missing = []
        for route in routes:
            if not route["enabled"]:
                continue
            if (self.profile_lease.admission is None
                    and route.get("chat_type", "private") == "private"):
                continue
            try:
                key = self._telegram_restored_pairing_key(route)
            except (StateError, ValueError):
                missing.append(route["chat_id"])
            else:
                if (key[0] is None
                        or self._telegram_restored_pairings.get(route["chat_id"]) != key):
                    missing.append(route["chat_id"])
        return missing

    def _telegram_require_fresh_pairings(self):
        if self._telegram_missing_restored_pairings():
            raise TelegramError("telegram_restore_fresh_pairing_required")

    def _telegram_runtime_guard(self):
        self.client_authority.lookup(self._telegram_owner, scope="owner")
        self._telegram_require_fresh_pairings()

    @asynccontextmanager
    async def _telegram_mutation(self):
        if self._closing or self._maintenance or self._telegram_mutating:
            raise RpcError(-32011, "Espera a que termine el cambio de configuración actual.")
        self._telegram_mutating, self._maintenance = True, True
        try:
            yield
        finally:
            self._telegram_mutating, self._maintenance = False, False

    def _telegram_require_stopped(self):
        if self.telegram_blocks_workspace_change():
            raise TelegramError("telegram_stop_required")

    async def telegram_status(self, value):
        params(value, set())
        return self._telegram_snapshot()

    async def telegram_connect(self, value):
        params(value, {"token"}, {"token"})
        self._management_idle()
        self._telegram_require_stopped()
        if self._telegram_channel is not None:
            raise TelegramError("telegram_disconnect_required")
        async with self._telegram_mutation():
            client = self._telegram_client_factory(value["token"])
            channel = TelegramChannel(
                client, self._telegram_journal, self._dispatch_telegram,
                configuration_guard=self._telegram_runtime_guard)
            self._telegram_channel = channel
            try:
                await channel.connect()
                self._telegram_last_error = None
            except BaseException as error:
                await client.aclose()
                self._telegram_channel = None
                self._telegram_last_error = (
                    error.code if isinstance(error, TelegramError) else "telegram_connect_failed"
                )
                raise
        return self._telegram_snapshot()

    async def telegram_create_route(self, value):
        policy = {"chat_type", "allowed_sender_ids", "message_thread_id", "allow_attachments"}
        params(value, {"chat_id", "agent_id"} | policy, {"chat_id", "agent_id"})
        self._management_idle()
        self._telegram_require_stopped()
        chat_id = value["chat_id"]
        _chat_id(chat_id, kind=value.get("chat_type", "private"))
        route_policy = {"chat_type": value.get("chat_type", "private"),
                        "allowed_sender_ids": value.get("allowed_sender_ids", []),
                        "message_thread_id": value.get("message_thread_id"),
                        "allow_attachments": value.get("allow_attachments", False)}
        # Validate the complete policy before creating even an empty session.
        _route({"chat_id": chat_id, "agent_id": value["agent_id"], "session_id": "a" * 32,
                "revision": 1, "enabled": True, **route_policy})
        profile = self.agent_profiles.get(value["agent_id"])
        routes = self._telegram_journal.routes()
        if any(row.chat_id == chat_id for row in routes):
            raise TelegramError("telegram_route_revision_conflict")
        if len(routes) >= 32:
            raise TelegramError("telegram_route_limit")
        async with self._telegram_mutation():
            # A failed/uncertain route write may leave only this empty, own
            # session. Never delete another conversation to roll back config.
            session = self.store.create_session(
                "Telegram · " + profile.name, agent_id=profile.agent_id
            )
            self._telegram_journal.set_route(chat_id, profile.agent_id, session.ref.session_id,
                                            **route_policy)
        return self._telegram_snapshot()

    async def telegram_update_route(self, value):
        policy = {"chat_type", "allowed_sender_ids", "message_thread_id",
                  "allow_attachments", "enabled"}
        params(value, {"chat_id", "expected_revision"} | policy,
               {"chat_id", "expected_revision"})
        self._management_idle()
        self._telegram_require_stopped()
        current = next((row for row in self._telegram_journal.routes()
                        if row.chat_id == value["chat_id"]), None)
        if current is None:
            raise TelegramError("telegram_route_revision_conflict")
        options = {key: value.get(key, getattr(current, key)) for key in policy}
        async with self._telegram_mutation():
            self._telegram_journal.set_route(current.chat_id, current.agent_id, current.session_id,
                expected_revision=value["expected_revision"], **options)
            self._telegram_restored_pairings.pop(current.chat_id, None)
            self._telegram_pairing_proof_owners.pop(current.chat_id, None)
        return self._telegram_snapshot()

    async def telegram_remove_route(self, value):
        params(value, {"chat_id", "expected_revision"}, {"chat_id", "expected_revision"})
        self._management_idle()
        self._telegram_require_stopped()
        async with self._telegram_mutation():
            self._telegram_journal.remove_route(**value)
            self._telegram_restored_pairings.pop(value["chat_id"], None)
            self._telegram_pairing_proof_owners.pop(value["chat_id"], None)
        return self._telegram_snapshot()

    async def telegram_start(self, value):
        params(value, {"configuration_hash"}, {"configuration_hash"})
        self._management_idle()
        self._telegram_require_stopped()
        channel = self._telegram_channel
        if channel is None or channel.client.identity is None:
            raise TelegramError("telegram_login_required")
        async with self._telegram_mutation():
            routes, internal_hash, configuration_hash = self._telegram_configuration()
            if value["configuration_hash"] != configuration_hash:
                raise TelegramError("telegram_configuration_changed")
            if self._telegram_missing_restored_pairings(routes):
                raise TelegramError("telegram_restore_fresh_pairing_required")
            self._assert_workspace_current()
            for route in routes:
                self._telegram_validate_route(TelegramRoute(**route))
            owner = authenticated_client.get()
            self.client_authority.lookup(owner, scope="owner")
            self._telegram_workspace_id = self._workspace_id
            self._telegram_owner = owner
            try:
                channel.start(expected_configuration_hash=internal_hash)
            except BaseException:
                self._telegram_workspace_id = None
                self._telegram_owner = None
                raise
            self._telegram_last_error = None
        return self._telegram_snapshot()

    async def telegram_stop(self, value):
        params(value, set())
        async with self._telegram_mutation():
            if self._telegram_pairing is not None:
                await self._telegram_pairing.stop()
                self._telegram_pairing_owner = None
            if self._telegram_group_pairing is not None:
                await self._telegram_group_pairing.stop()
                self._telegram_group_owner = None
            if self._telegram_channel is not None:
                await self._telegram_channel.stop()
            self._telegram_workspace_id = None
            self._telegram_owner = None
        return self._telegram_snapshot()

    async def telegram_disconnect(self, value):
        params(value, set())
        async with self._telegram_mutation():
            await self.close_telegram()
        return self._telegram_snapshot()

    async def telegram_history(self, value):
        params(value, set())
        return {"events": list(self._telegram_journal.history()), "limit": 50, "capacity": 2048}

    async def telegram_destination(self, value):
        params(value, {"session_id", "run_id"}, {"session_id", "run_id"})
        if (not isinstance(value["run_id"], str) or not _UUID.fullmatch(value["run_id"])
                or not isinstance(value["session_id"], str)
                or not _UUID.fullmatch(value["session_id"])):
            raise TelegramError("telegram_run_identity_invalid")
        context = self._telegram_run_context.get(value["run_id"])
        if context is None or context["ref"].session_id != value["session_id"]:
            raise TelegramError("telegram_route_unavailable")
        context["guard"]()
        incoming = context["incoming"]
        return {"run_id": value["run_id"], "session_id": value["session_id"],
                "chat_id": incoming.chat_id, "chat_type": incoming.chat_type,
                "sender_id": incoming.sender_id, "message_id": incoming.message_id,
                "message_thread_id": incoming.message_thread_id,
                "route_revision": incoming.route_revision,
                "maximum_attachment_bytes": 5 * 1024 * 1024}

    async def telegram_cancel_event(self, value):
        fields = {"epoch", "update_id", "expected_run_id", "expected_route_revision"}
        params(value, fields, fields)
        if (type(value["epoch"]) is not int or type(value["update_id"]) is not int
                or type(value["expected_route_revision"]) is not int):
            raise TelegramError("telegram_event_not_active")
        identity = value["epoch"], value["update_id"]
        row = next((row for row in self._telegram_journal.history(limit=100)
                    if (row["epoch"], row["update_id"]) == identity), None)
        if (row is None or row["status"] != "dispatching" or row["run_id"] is None
                or row["run_id"] != value["expected_run_id"]
                or row["route_revision"] != value["expected_route_revision"]):
            raise TelegramError("telegram_event_not_active")
        ref = SessionRef(row["session_id"])
        async with self._telegram_mutation():
            self._telegram_cancelled_runs.add(row["run_id"])
            await self._telegram_cancel_owned_run(row["run_id"], ref)
        return {"run_id": row["run_id"], "cancel_requested": True,
                "automatic_replay": False}

    async def close_telegram(self):
        """Host shutdown; an incomplete callback retains ownership and its lease."""
        if self._telegram_pairing is not None:
            await self._telegram_pairing.stop()
        if self._telegram_group_pairing is not None:
            await self._telegram_group_pairing.stop()
        channel = self._telegram_channel
        if channel is not None:
            await channel.aclose()
        self._telegram_channel = None
        self._telegram_workspace_id = None
        self._telegram_owner = None
        self._telegram_group_owner = None
        self._telegram_pairing_owner = None
        self._telegram_pairing_proof_chat = None
        self._telegram_group_proof_chat = None
        self._telegram_restored_pairings.clear()
        self._telegram_pairing_proof_owners.clear()

    def _telegram_pairing_review(self, agent_id):
        channel = self._telegram_channel
        if channel is None or channel.client.identity is None or channel.client.closed:
            raise TelegramError("telegram_login_required")
        identity = channel.client.identity
        if identity.username is None:
            raise TelegramError("telegram_pairing_username_required")
        profile = self.agent_profiles.get(agent_id)
        _routes, internal_hash, owner_hash = self._telegram_configuration()
        value = {
            "agent_id": profile.agent_id,
            "agent_revision": profile.revision,
            "bot_id": identity.bot_id,
            "username": identity.username,
            "workspace_id": self._workspace_id,
            "max_seconds": 180,
            "consumes_ignored_updates": True,
            "requires_exclusive_bot": True,
            "telegram_retains_start_message": True,
        }
        review_hash = hashlib.sha256(
            json.dumps(
                ["jarvis_telegram_pairing_v1", owner_hash, value],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return {**value, "review_hash": review_hash}, internal_hash

    async def telegram_pairing_review(self, value):
        params(value, {"agent_id"}, {"agent_id"})
        return self._telegram_pairing_review(value["agent_id"])[0]

    async def telegram_pairing_start(self, value):
        params(
            value,
            {
                "agent_id",
                "expected_profile_revision",
                "review_hash",
                "acknowledge_update_consumption",
                "confirm_no_other_consumer",
            },
            {
                "agent_id",
                "expected_profile_revision",
                "review_hash",
                "acknowledge_update_consumption",
                "confirm_no_other_consumer",
            },
        )
        self._management_idle()
        self._telegram_require_stopped()
        if (
            value["acknowledge_update_consumption"] is not True
            or value["confirm_no_other_consumer"] is not True
        ):
            raise TelegramError("telegram_pairing_review_required")
        if type(value["expected_profile_revision"]) is not int:
            raise TelegramError("telegram_pairing_claim_invalid")
        review, internal_hash = self._telegram_pairing_review(value["agent_id"])
        if (
            review["review_hash"] != value["review_hash"]
            or review["agent_revision"] != value["expected_profile_revision"]
        ):
            raise TelegramError("telegram_configuration_changed")
        self._assert_workspace_current()
        owner = authenticated_client.get()
        self.client_authority.lookup(owner, scope="owner")
        async with self._telegram_mutation():
            if self._telegram_pairing is not None:
                await self._telegram_pairing.stop()
            pairing = self._telegram_pairing_factory(
                self._telegram_channel.client, self._telegram_journal
            )
            self._telegram_pairing = pairing
            self._telegram_pairing_owner = owner
            self._telegram_pairing_proof_chat = None
            pairing.host_guard = lambda: self.client_authority.lookup(owner, scope="owner")
            await pairing.start(
                agent_id=review["agent_id"],
                agent_revision=review["agent_revision"],
                expected_configuration_hash=internal_hash,
            )
            try:
                current = self._telegram_pairing_review(value["agent_id"])[0]
                self._assert_workspace_current()
                if current["review_hash"] != review["review_hash"]:
                    raise TelegramError("telegram_configuration_changed")
            except BaseException:
                await pairing.stop()
                raise
        return pairing.status()

    async def telegram_pairing_open(self, value):
        params(value, {"challenge_id"}, {"challenge_id"})
        pairing = self._telegram_pairing
        if pairing is None:
            raise TelegramError("telegram_pairing_link_unavailable")
        return {
            "challenge_id": pairing.challenge_id,
            "telegram_uri": pairing.native_uri(value["challenge_id"]),
        }

    async def telegram_pairing_status(self, value):
        params(value, set())
        if self._telegram_pairing is not None:
            return self._telegram_pairing.status()
        return {
            "state": "idle",
            "challenge_id": None,
            "claim_id": None,
            "chat_id": None,
            "agent_id": None,
            "agent_revision": None,
            "bot_id": None,
            "username": None,
            "expires_at": None,
            "error": None,
        }

    async def telegram_pairing_cancel(self, value):
        params(value, set())
        async with self._telegram_mutation():
            if self._telegram_pairing is not None:
                await self._telegram_pairing.stop()
            proof_chat = self._telegram_pairing_proof_chat
            if proof_chat is not None:
                self._telegram_restored_pairings.pop(proof_chat, None)
                self._telegram_pairing_proof_owners.pop(proof_chat, None)
            self._telegram_pairing_proof_chat = None
            self._telegram_pairing_owner = None
        return await self.telegram_pairing_status({})

    async def telegram_pairing_confirm(self, value):
        params(
            value,
            {"claim_id", "agent_id", "expected_profile_revision"},
            {"claim_id", "agent_id", "expected_profile_revision"},
        )
        self._management_idle()
        pairing = self._telegram_pairing
        if pairing is None or pairing.claim() is None:
            raise TelegramError("telegram_pairing_claim_invalid")
        profile = self.agent_profiles.get(value["agent_id"])
        if (
            type(value["expected_profile_revision"]) is not int
            or profile.revision != value["expected_profile_revision"]
        ):
            raise TelegramError("telegram_configuration_changed")
        claim = pairing.claim()
        if any(row.chat_id == claim.chat_id for row in self._telegram_journal.routes()):
            raise TelegramError("telegram_route_revision_conflict")
        if len(self._telegram_journal.routes()) >= 32:
            raise TelegramError("telegram_route_limit")
        channel = self._telegram_channel
        _routes, _hash, configuration_hash = self._telegram_configuration()
        async with self._telegram_mutation():
            claim = pairing.consume(**value)
            await pairing.stop(state="confirmed")
            self.client_authority.lookup(self._telegram_pairing_owner, scope="owner")
            self._assert_workspace_current()
            if (self.agent_profiles.get(profile.agent_id).revision != claim.agent_revision
                    or self._telegram_configuration()[2] != configuration_hash
                    or self._telegram_channel is not channel or channel.client.closed
                    or channel.client.identity is None
                    or channel.client.identity.bot_id != claim.bot_id):
                raise TelegramError("telegram_configuration_changed")
            session = self.store.create_session(
                "Telegram · " + profile.name, agent_id=profile.agent_id
            )
            self._telegram_journal.set_route(
                claim.chat_id, profile.agent_id, session.ref.session_id
            )
            if self.profile_lease.admission is not None:
                routes = self._telegram_configuration()[0]
                route = next(row for row in routes if row["chat_id"] == claim.chat_id)
                self._telegram_restored_pairings[claim.chat_id] = (
                    self._telegram_restored_pairing_key(route))
                self._telegram_pairing_proof_owners[claim.chat_id] = self._telegram_pairing_owner
                self._telegram_pairing_proof_chat = claim.chat_id
        return self._telegram_snapshot()

    async def telegram_restore_pairing_confirm(self, value):
        """Consume a fresh private-chat claim for one exact historical route.

        This is the existing native pairing decision, with no route/session
        write and no reader start. RAM evidence cannot survive reconnect or be
        imported by backup/restore. A failed/unknown confirmation is not retried.
        """
        fields = {"claim_id", "agent_id", "expected_profile_revision", "chat_id",
                  "expected_route_revision", "configuration_hash"}
        params(value, fields, fields)
        self._management_idle()
        self.profile_lease.check_bootstrap_ready()
        if self.profile_lease.admission is None:
            raise TelegramError("telegram_restore_profile_required")
        pairing = self._telegram_pairing
        claim = pairing.claim() if pairing is not None else None
        channel = self._telegram_channel
        identity = channel.client.identity if channel is not None else None
        if claim is None or identity is None or channel.client.closed:
            raise TelegramError("telegram_pairing_claim_invalid")
        profile = self.agent_profiles.get(value["agent_id"])
        routes, _internal_hash, configuration_hash = self._telegram_configuration()
        route = next((row for row in routes if row["chat_id"] == value["chat_id"]), None)
        if (type(value["chat_id"]) is not int or type(value["expected_route_revision"]) is not int
                or type(value["expected_profile_revision"]) is not int
                or value["configuration_hash"] != configuration_hash
                or route is None or not route["enabled"]
                or route["revision"] != value["expected_route_revision"]
                or route["chat_id"] != claim.chat_id or claim.bot_id != identity.bot_id
                or route["agent_id"] != profile.agent_id or claim.agent_id != profile.agent_id
                or profile.revision != value["expected_profile_revision"]
                or claim.agent_revision != profile.revision):
            raise TelegramError("telegram_configuration_changed")
        self._assert_workspace_current()
        self._telegram_validate_route(TelegramRoute(**route))
        key = self._telegram_restored_pairing_key(route)
        async with self._telegram_mutation():
            pairing.consume(claim_id=value["claim_id"], agent_id=value["agent_id"],
                            expected_profile_revision=value["expected_profile_revision"])
            await pairing.stop(state="confirmed")
            self.client_authority.lookup(self._telegram_pairing_owner, scope="owner")
            # Closing the fresh pairing reader can await. Revalidate every
            # pinned identity, configuration and destination before retaining it.
            current_routes, _hash, current_hash = self._telegram_configuration()
            current_route = next((row for row in current_routes
                                  if row["chat_id"] == value["chat_id"]), None)
            self._assert_workspace_current()
            if (current_hash != configuration_hash or current_route != route
                    or self._telegram_channel is not channel or channel.client.closed
                    or self._telegram_restored_pairing_key(route) != key):
                raise TelegramError("telegram_configuration_changed")
            self._telegram_validate_route(TelegramRoute(**route))
            self._telegram_restored_pairings[route["chat_id"]] = key
            self._telegram_pairing_proof_owners[route["chat_id"]] = self._telegram_pairing_owner
            self._telegram_pairing_proof_chat = route["chat_id"]
        return self._telegram_snapshot()

    def _telegram_validate_route(self, route):
        self.agent_profiles.get(route.agent_id)
        record = self.store.get_session(SessionRef(route.session_id))
        if record.archived or record.agent_id != route.agent_id:
            raise TelegramError("telegram_route_unavailable")
        return record.ref

    def _telegram_validate_incoming(self, incoming: IncomingMessage):
        if (
            self._closing
            or self._telegram_workspace_id is None
            or self._telegram_workspace_id != self._workspace_id
            or self.config.workspace != self._configured_workspace
        ):
            raise TelegramError("telegram_workspace_unavailable")
        self._assert_workspace_current()
        route = next(
            (row for row in self._telegram_journal.routes() if row.chat_id == incoming.chat_id),
            None,
        )
        if (
            route is None
            or not route.enabled
            or route.agent_id != incoming.agent_id
            or route.session_id != incoming.session_id
            or route.revision != incoming.route_revision
            or route.chat_type != incoming.chat_type
            or route.message_thread_id != incoming.message_thread_id
            or (route.chat_type == "private" and incoming.sender_id != incoming.chat_id)
            or (route.chat_type != "private" and incoming.sender_id not in route.allowed_sender_ids)
            or (incoming.attachments and not route.allow_attachments)
        ):
            raise TelegramError("telegram_route_unavailable")
        if self.profile_lease.admission is not None or route.chat_type != "private":
            routes = self._telegram_configuration()[0]
            if incoming.chat_id in self._telegram_missing_restored_pairings(routes):
                raise TelegramError("telegram_restore_fresh_pairing_required")
        return self._telegram_validate_route(route)

    async def _telegram_cancel_owned_run(self, run_id, ref):
        handle = self._handles.get(run_id)
        if handle is None:
            return
        handle.cancel()

        async def settle():
            await handle.await_settled()
            await self._await_run_receipt(ref, handle)

        cleanup = asyncio.create_task(settle())
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
        cleanup.result()

    @profile_background("telegram.dispatch")
    async def _dispatch_telegram(self, incoming: IncomingMessage):
        run_id, ref, accepted = uuid4().hex, None, False
        ingest_started = ingest_completed = False
        channel = self._telegram_channel

        def guard():
            if (channel is None or channel is not self._telegram_channel or not channel.running
                    or run_id in self._telegram_cancelled_runs):
                raise TelegramError("telegram_route_unavailable")
            channel._guard_configuration()
            self._telegram_validate_incoming(incoming)

        try:
            ref = self._telegram_validate_incoming(incoming)
            guard()
            self._automation_run_guards[run_id] = guard
            self._telegram_run_context[run_id] = {
                "incoming": incoming, "channel": channel, "ref": ref, "guard": guard}
            request = {"session_id": ref.session_id, "message": incoming.text, "request_id": run_id}
            if incoming.attachments:
                route = next(row for row in self._telegram_journal.routes()
                             if row.chat_id == incoming.chat_id)
                if not route.allow_attachments:
                    raise TelegramError("telegram_attachment_not_authorized")
                downloaded = []
                for attachment in incoming.attachments:
                    guard()
                    payload = await channel.client.download_attachment(attachment,
                                                                        before_dispatch=guard)
                    downloaded.append((attachment, payload))
                guard()
                ingest = getattr(self, "_telegram_ingest_attachments", None)
                if not callable(ingest):
                    raise TelegramError("telegram_attachment_handler_unavailable")
                ingest_started = True
                imported = await ingest(incoming, tuple(downloaded), session_ref=ref,
                                        run_id=run_id, guard=guard)
                if (not isinstance(imported, dict) or set(imported) != {"message", "attachment_ids"}
                        or not isinstance(imported["message"], str)
                        or not isinstance(imported["attachment_ids"], list)):
                    raise TelegramError("telegram_attachment_handler_unavailable")
                ingest_completed = True
                guard()
                request.update(imported)
            response = await self.start_run(request)
            accepted = True
            if response.get("run_id") != run_id:
                raise TelegramError("telegram_dispatch_unconfirmed", effect_status="unknown")
            self._telegram_journal.attach_run(incoming, run_id)
            handle = self._handles.get(run_id)
            receipt = (
                await self._await_run_receipt(ref, handle)
                if handle is not None
                else self._run_value(ref, run_id)
            )
            if (
                receipt is None
                or receipt.get("run_id") != run_id
                or receipt.get("status") not in {"completed", "failed", "cancelled", "interrupted"}
                or receipt.get("outcome") not in {"known", "unknown"}
            ):
                await self._telegram_cancel_owned_run(run_id, ref)
                return TelegramDispatchResult(
                    run_id, "interrupted", "unknown", error_code="telegram_receipt_unavailable"
                )
            guard()
            return TelegramDispatchResult(
                run_id,
                receipt["status"],
                receipt["outcome"],
                answer=receipt.get("answer")
                if receipt["status"] == "completed" and receipt["outcome"] == "known"
                else None,
                error_code=receipt.get("error"),
            )
        except BaseException as error:
            if ref is not None:
                accepted = accepted or run_id in self._handles
                await self._telegram_cancel_owned_run(run_id, ref)
                if not accepted:
                    try:
                        self.store.get_run(ref, run_id)
                        accepted = True
                    except StateError:
                        pass
            if isinstance(error, asyncio.CancelledError):
                raise
            if run_id in self._telegram_cancelled_runs:
                return TelegramDispatchResult(run_id if accepted else None, "cancelled", "unknown",
                                             error_code="telegram_cancelled_no_replay")
            if not accepted and ingest_started:
                unknown = (
                    not ingest_completed and getattr(error, "effect_status", "unknown") == "unknown"
                )
                return TelegramDispatchResult(None, "interrupted" if unknown else "failed",
                    "unknown" if unknown else "known",
                    error_code="telegram_attachment_import_unconfirmed" if unknown
                    else "telegram_attachment_imported_without_run" if ingest_completed
                    else "telegram_attachment_import_failed")
            return TelegramDispatchResult(
                run_id if accepted else None,
                "interrupted" if accepted else "failed",
                "unknown" if accepted else "known",
                error_code="telegram_dispatch_unconfirmed"
                if accepted
                else "telegram_route_unavailable",
            )
        finally:
            if run_id not in self._handles:
                self._automation_run_guards.pop(run_id, None)
                self._telegram_cancelled_runs.discard(run_id)
                self._telegram_run_context.pop(run_id, None)
