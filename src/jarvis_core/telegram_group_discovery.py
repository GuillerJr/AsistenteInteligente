"""Explicit one-use group configuration proof, never automatic routing."""

from __future__ import annotations

import hmac
import math
from dataclasses import dataclass
from uuid import uuid4

from jarvis_core.telegram import TelegramError, _chat_id, _route
from jarvis_core.telegram_pairing import PairingClaim, TelegramPairing


@dataclass(frozen=True)
class GroupDiscoveryClaim(PairingClaim):
    chat_type: str
    message_thread_id: int | None
    sender_id: int


class TelegramGroupDiscovery(TelegramPairing):
    def __init__(self, client, journal, *, review, host_guard, **options):
        super().__init__(client, journal, **options)
        self.review, self.host_guard = dict(review), host_guard
        self._candidate = self._confirmed_claim = None

    @staticmethod
    def idle_status():
        return {
            "state": "idle",
            "challenge_id": None,
            "claim_id": None,
            "owner_chat_id": None,
            "chat_id": None,
            "chat_type": None,
            "message_thread_id": None,
            "sender_id": None,
            "agent_id": None,
            "agent_revision": None,
            "configuration_hash": None,
            "allow_attachments": False,
            "error_code": None,
            "expires_in_seconds": None,
        }

    def status(self):
        self._check_expiry()
        row = self._claim or (self._confirmed_claim if self.state == "confirmed" else None)
        return {
            "state": self.state,
            "challenge_id": self.challenge_id,
            "claim_id": row.claim_id if self.state == "claimed" and row else None,
            "owner_chat_id": self.review["owner_chat_id"],
            "chat_id": row.chat_id if row else None,
            "chat_type": row.chat_type if row else None,
            "message_thread_id": row.message_thread_id if row else None,
            "sender_id": row.sender_id if row else None,
            "agent_id": self.review["agent_id"],
            "agent_revision": self.review["agent_revision"],
            "configuration_hash": self.review["configuration_hash"],
            "allow_attachments": self.review["allow_attachments"],
            "error_code": self.error_code,
            "expires_in_seconds": (
                max(0, min(180, math.ceil(self._deadline - self.monotonic())))
                if self._deadline is not None
                else None
            ),
        }

    def native_uri(self, challenge_id):
        raise TelegramError("telegram_group_command_required")

    def native_command(self, challenge_id):
        self._check_expiry()
        if self.state == "expired":
            raise TelegramError("telegram_pairing_expired")
        if (
            self.state != "listening"
            or self._nonce is None
            or challenge_id != self.challenge_id
            or self._link_retrieved
        ):
            raise TelegramError("telegram_pairing_link_unavailable")
        self._guard()
        self._link_retrieved = True
        return "/jarvis_pair@" + self.review["username"] + " " + self._nonce

    def _match(self, update):
        message = update.get("message")
        if not isinstance(message, dict):
            return None
        chat, sender = message.get("chat"), message.get("from")
        if (
            not isinstance(chat, dict)
            or not isinstance(chat.get("type"), str)
            or chat.get("type") not in {"group", "supergroup"}
            or not isinstance(sender, dict)
            or type(sender.get("id")) is not int
            or sender["id"] != self.review["owner_chat_id"]
            or sender.get("is_bot") is not False
            or message.get("sender_chat") is not None
            or type(message.get("message_id")) is not int
            or not 1 <= message["message_id"] < (1 << 31)
            or type(message.get("date")) is not int
            or not self._started_at <= message["date"] <= self.clock() + 300
        ):
            return None
        try:
            _chat_id(chat.get("id"), kind=chat["type"])
        except TelegramError:
            return None
        thread = message.get("message_thread_id")
        if thread is not None and (
            chat["type"] != "supergroup" or type(thread) is not int or not 1 <= thread < (1 << 31)
        ):
            return None
        text = message.get("text")
        expected = "/jarvis_pair@" + self.review["username"] + " " + self._nonce
        if not isinstance(text, str):
            return None
        try:
            raw = text.encode()
        except UnicodeError:
            return None
        if len(raw) != len(expected.encode()) or not hmac.compare_digest(raw, expected.encode()):
            return None
        self._candidate = chat["type"], thread, sender["id"]
        return chat["id"], message["message_id"]

    def _make_claim(self, chat_id):
        return GroupDiscoveryClaim(
            uuid4().hex,
            self.challenge_id,
            chat_id,
            self.agent_id,
            self.agent_revision,
            self.client.identity.bot_id,
            self.expires_at,
            *self._candidate,
        )

    def validate_claim(self):
        self._check_expiry()
        if self.state != "claimed" or self._claim is None or self._nonce is None:
            raise TelegramError("telegram_pairing_claim_invalid")
        if self._configuration != self.journal.configuration_hash():
            raise TelegramError("telegram_configuration_changed")
        self.host_guard()


class TelegramGroupDiscoveryAPI:
    def telegram_discovery_handlers(self):
        return {
            "telegram.group.discover.review": self.telegram_discovery_review,
            "telegram.group.discover.start": self.telegram_discovery_start,
            "telegram.group.discover.status": self.telegram_discovery_status,
            "telegram.group.discover.command": self.telegram_discovery_command,
            "telegram.group.discover.confirm": self.telegram_discovery_confirm,
            "telegram.group.discover.cancel": self.telegram_discovery_cancel,
        }

    def _telegram_discovery_review(self, owner_chat_id, agent_id, allow_attachments, owner):
        import hashlib
        import json

        self.client_authority.lookup(owner, scope="owner")
        _chat_id(owner_chat_id, kind="private")
        if type(allow_attachments) is not bool:
            raise TelegramError("telegram_discovery_review_invalid")
        channel = self._telegram_channel
        identity = channel.client.identity if channel is not None else None
        if identity is None or channel.client.closed or identity.username is None:
            raise TelegramError("telegram_login_required")
        routes, internal, configuration = self._telegram_configuration()
        route = next((row for row in routes if row["chat_id"] == owner_chat_id), None)
        if route is None or route.get("chat_type", "private") != "private" or not route["enabled"]:
            raise TelegramError("telegram_discovery_fresh_private_pairing_required")
        self._telegram_validate_route(_route(route))
        if (
            self._telegram_restored_pairings.get(owner_chat_id)
            != self._telegram_restored_pairing_key(route)
            or self._telegram_pairing_proof_owners.get(owner_chat_id) != owner
        ):
            raise TelegramError("telegram_discovery_fresh_private_pairing_required")
        profile = self.agent_profiles.get(agent_id)
        self._assert_workspace_current()
        row = {
            "owner_chat_id": owner_chat_id,
            "agent_id": profile.agent_id,
            "agent_revision": profile.revision,
            "bot_id": identity.bot_id,
            "username": identity.username,
            "workspace_id": self._workspace_id,
            "profile_id": self.profile_lease.scope.profile_id,
            "configuration_hash": configuration,
            "allow_attachments": allow_attachments,
            "max_seconds": 180,
            "consumes_ignored_updates": True,
            "requires_exclusive_bot": True,
            "telegram_retains_start_message": True,
        }
        row["review_hash"] = hashlib.sha256(
            json.dumps(["jarvis_telegram_group_discovery_v1", row], sort_keys=True).encode()
        ).hexdigest()
        return row, internal

    async def telegram_discovery_review(self, value):
        from jarvis_core.management import params
        from jarvis_core.protocol import authenticated_client

        fields = {"owner_chat_id", "agent_id", "allow_attachments"}
        params(value, fields, fields)
        self._management_idle()
        self._telegram_require_stopped()
        return self._telegram_discovery_review(**value, owner=authenticated_client.get())[0]

    async def telegram_discovery_start(self, value):
        from jarvis_core.management import params
        from jarvis_core.protocol import authenticated_client

        fields = {
            "owner_chat_id",
            "agent_id",
            "allow_attachments",
            "expected_profile_revision",
            "review_hash",
            "acknowledge_update_consumption",
            "confirm_no_other_consumer",
        }
        params(value, fields, fields)
        self._management_idle()
        self._telegram_require_stopped()
        owner = authenticated_client.get()
        review, internal = self._telegram_discovery_review(
            value["owner_chat_id"], value["agent_id"], value["allow_attachments"], owner
        )
        if (
            value["acknowledge_update_consumption"] is not True
            or value["confirm_no_other_consumer"] is not True
            or type(value["expected_profile_revision"]) is not int
            or value["expected_profile_revision"] != review["agent_revision"]
            or value["review_hash"] != review["review_hash"]
        ):
            raise TelegramError("telegram_discovery_review_invalid")

        def guard():
            current = self._telegram_discovery_review(
                value["owner_chat_id"], value["agent_id"], value["allow_attachments"], owner
            )[0]
            if current != review or self._closing:
                raise TelegramError("telegram_configuration_changed")

        async with self._telegram_mutation():
            if self._telegram_discovery is not None:
                await self._telegram_discovery.stop()
            pairing = TelegramGroupDiscovery(
                self._telegram_channel.client,
                self._telegram_journal,
                review=review,
                host_guard=guard,
            )
            self._telegram_discovery, self._telegram_discovery_owner = pairing, owner
            try:
                await pairing.start(
                    agent_id=review["agent_id"],
                    agent_revision=review["agent_revision"],
                    expected_configuration_hash=internal,
                )
                guard()
            except BaseException:
                await pairing.stop()
                if pairing.challenge_id is None:
                    self._telegram_discovery = None
                    self._telegram_discovery_owner = None
                raise
        return pairing.status()

    async def telegram_discovery_status(self, value):
        from jarvis_core.management import params

        params(value, set())
        return (
            self._telegram_discovery.status()
            if self._telegram_discovery
            else (TelegramGroupDiscovery.idle_status())
        )

    async def telegram_discovery_command(self, value):
        from jarvis_core.management import params

        params(value, {"challenge_id"}, {"challenge_id"})
        if self._telegram_discovery is None:
            raise TelegramError("telegram_pairing_link_unavailable")
        return {
            "challenge_id": self._telegram_discovery.challenge_id,
            "command": self._telegram_discovery.native_command(value["challenge_id"]),
        }

    async def telegram_discovery_confirm(self, value):
        from jarvis_core.management import params

        fields = {"claim_id", "expected_profile_revision", "configuration_hash"}
        params(value, fields, fields)
        self._management_idle()
        pairing = self._telegram_discovery
        claim = pairing.claim() if pairing is not None else None
        if claim is None:
            raise TelegramError("telegram_pairing_claim_invalid")
        pairing.validate_claim()
        owner = self._telegram_discovery_owner
        review, _internal = self._telegram_discovery_review(
            pairing.review["owner_chat_id"],
            pairing.review["agent_id"],
            pairing.review["allow_attachments"],
            owner,
        )
        if (
            review != pairing.review
            or value["claim_id"] != claim.claim_id
            or type(value["expected_profile_revision"]) is not int
            or value["expected_profile_revision"] != claim.agent_revision
            or value["configuration_hash"] != review["configuration_hash"]
        ):
            raise TelegramError("telegram_configuration_changed")
        if any(row.chat_id == claim.chat_id for row in self._telegram_journal.routes()):
            raise TelegramError("telegram_route_revision_conflict")
        if len(self._telegram_journal.routes()) >= 32:
            raise TelegramError("telegram_route_limit")
        async with self._telegram_mutation():
            pairing.consume(
                claim_id=claim.claim_id,
                agent_id=claim.agent_id,
                expected_profile_revision=claim.agent_revision,
            )
            await pairing.stop(state="confirmed")
            self.client_authority.lookup(owner, scope="owner")
            if (
                self._telegram_discovery_review(
                    review["owner_chat_id"], review["agent_id"], review["allow_attachments"], owner
                )[0]
                != review
            ):
                raise TelegramError("telegram_configuration_changed")
            try:
                session = self.store.create_session(
                    "Telegram · " + self.agent_profiles.get(claim.agent_id).name,
                    agent_id=claim.agent_id,
                )
                self._telegram_journal.set_route(
                    claim.chat_id,
                    claim.agent_id,
                    session.ref.session_id,
                    chat_type=claim.chat_type,
                    allowed_sender_ids=[claim.sender_id],
                    message_thread_id=claim.message_thread_id,
                    allow_attachments=review["allow_attachments"],
                )
                route = next(
                    row
                    for row in self._telegram_configuration()[0]
                    if row["chat_id"] == claim.chat_id
                )
                self._telegram_restored_pairings[claim.chat_id] = (
                    self._telegram_restored_pairing_key(route)
                )
                self._telegram_pairing_proof_owners[claim.chat_id] = owner
                pairing._confirmed_claim = claim
                self._telegram_discovery_owner = None
            except BaseException:
                pairing._invalidate("failed")
                pairing.error_code = "telegram_discovery_publication_unconfirmed"
                raise
        return self._telegram_snapshot()

    async def telegram_discovery_cancel(self, value):
        from jarvis_core.management import params

        params(value, set())
        async with self._telegram_mutation():
            if self._telegram_discovery is not None:
                await self._telegram_discovery.stop()
            self._telegram_discovery_owner = None
        return await self.telegram_discovery_status({})
