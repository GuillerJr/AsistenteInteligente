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
from jarvis_core.protocol import RpcError
from jarvis_core.state import SessionRef, StateError
from jarvis_core.telegram import (
    IncomingMessage,
    TelegramChannel,
    TelegramClient,
    TelegramDispatchResult,
    TelegramError,
    TelegramJournal,
    TelegramRoute,
)
from jarvis_core.telegram_pairing import TelegramPairing


class TelegramManagementAPI:
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
        self._telegram_pairing = None
        self._telegram_pairing_factory = pairing_factory

    def telegram_handlers(self):
        return {
            "telegram.status": self.telegram_status,
            "telegram.connect": self.telegram_connect,
            "telegram.disconnect": self.telegram_disconnect,
            "telegram.routes.create": self.telegram_create_route,
            "telegram.routes.remove": self.telegram_remove_route,
            "telegram.start": self.telegram_start,
            "telegram.stop": self.telegram_stop,
            "telegram.history": self.telegram_history,
            "telegram.pairing.review": self.telegram_pairing_review,
            "telegram.pairing.start": self.telegram_pairing_start,
            "telegram.pairing.open": self.telegram_pairing_open,
            "telegram.pairing.status": self.telegram_pairing_status,
            "telegram.pairing.confirm": self.telegram_pairing_confirm,
            "telegram.pairing.cancel": self.telegram_pairing_cancel,
        }

    def telegram_uses_agent(self, agent_id):
        return any(row.agent_id == agent_id for row in self._telegram_journal.routes()) or (
            self._telegram_pairing is not None
            and self._telegram_pairing.busy
            and self._telegram_pairing.agent_id == agent_id
        )

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
        }

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
            channel = TelegramChannel(client, self._telegram_journal, self._dispatch_telegram)
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
        params(value, {"chat_id", "agent_id"}, {"chat_id", "agent_id"})
        self._management_idle()
        self._telegram_require_stopped()
        chat_id = value["chat_id"]
        if type(chat_id) is not int or not 1 <= chat_id <= (1 << 52) - 1:
            raise TelegramError("telegram_identity_invalid")
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
            self._telegram_journal.set_route(chat_id, profile.agent_id, session.ref.session_id)
        return self._telegram_snapshot()

    async def telegram_remove_route(self, value):
        params(value, {"chat_id", "expected_revision"}, {"chat_id", "expected_revision"})
        self._management_idle()
        self._telegram_require_stopped()
        async with self._telegram_mutation():
            self._telegram_journal.remove_route(**value)
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
            self._assert_workspace_current()
            for route in routes:
                self._telegram_validate_route(TelegramRoute(**route))
            self._telegram_workspace_id = self._workspace_id
            try:
                channel.start(expected_configuration_hash=internal_hash)
            except BaseException:
                self._telegram_workspace_id = None
                raise
            self._telegram_last_error = None
        return self._telegram_snapshot()

    async def telegram_stop(self, value):
        params(value, set())
        async with self._telegram_mutation():
            if self._telegram_pairing is not None:
                await self._telegram_pairing.stop()
            if self._telegram_channel is not None:
                await self._telegram_channel.stop()
            self._telegram_workspace_id = None
        return self._telegram_snapshot()

    async def telegram_disconnect(self, value):
        params(value, set())
        async with self._telegram_mutation():
            await self.close_telegram()
        return self._telegram_snapshot()

    async def telegram_history(self, value):
        params(value, set())
        return {"events": list(self._telegram_journal.history()), "limit": 50, "capacity": 2048}

    async def close_telegram(self):
        """Host shutdown; an incomplete callback retains ownership and its lease."""
        if self._telegram_pairing is not None:
            await self._telegram_pairing.stop()
        channel = self._telegram_channel
        if channel is not None:
            await channel.aclose()
        self._telegram_channel = None
        self._telegram_workspace_id = None

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
        async with self._telegram_mutation():
            if self._telegram_pairing is not None:
                await self._telegram_pairing.stop()
            pairing = self._telegram_pairing_factory(
                self._telegram_channel.client, self._telegram_journal
            )
            self._telegram_pairing = pairing
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
        async with self._telegram_mutation():
            claim = pairing.consume(**value)
            await pairing.stop(state="confirmed")
            session = self.store.create_session(
                "Telegram · " + profile.name, agent_id=profile.agent_id
            )
            self._telegram_journal.set_route(
                claim.chat_id, profile.agent_id, session.ref.session_id
            )
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
        ):
            raise TelegramError("telegram_route_unavailable")
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
        try:
            ref = self._telegram_validate_incoming(incoming)
            response = await self.start_run(
                {"session_id": ref.session_id, "message": incoming.text, "request_id": run_id}
            )
            accepted = True
            if response.get("run_id") != run_id:
                raise TelegramError("telegram_dispatch_unconfirmed", effect_status="unknown")
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
            self._telegram_validate_incoming(incoming)
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
            return TelegramDispatchResult(
                run_id if accepted else None,
                "interrupted" if accepted else "failed",
                "unknown" if accepted else "known",
                error_code="telegram_dispatch_unconfirmed"
                if accepted
                else "telegram_route_unavailable",
            )
