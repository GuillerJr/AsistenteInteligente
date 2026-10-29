"""Separate native-owner group proof endpoints, never private-chat discovery."""

from __future__ import annotations

import hashlib
import json

from jarvis_core.management import params
from jarvis_core.protocol import authenticated_client
from jarvis_core.telegram import TelegramError, _chat_id, _route
from jarvis_core.telegram_group_pairing import TelegramGroupPairing


class TelegramGroupManagementAPI:
    def telegram_group_handlers(self):
        return {
            "telegram.group.pairing.review": self.telegram_group_review,
            "telegram.group.pairing.start": self.telegram_group_start,
            "telegram.group.pairing.command": self.telegram_group_command,
            "telegram.group.pairing.status": self.telegram_group_status,
            "telegram.group.pairing.confirm": self.telegram_group_confirm,
            "telegram.group.pairing.cancel": self.telegram_group_cancel,
        }

    def _group_review(self, chat_id):
        _chat_id(chat_id, kind="group")
        channel = self._telegram_channel
        identity = channel.client.identity if channel is not None else None
        if identity is None or identity.username is None or channel.client.closed:
            raise TelegramError("telegram_login_required")
        routes, internal_hash, owner_hash = self._telegram_configuration()
        value = next((row for row in routes if row["chat_id"] == chat_id), None)
        if value is None:
            raise TelegramError("telegram_route_unavailable")
        route = _route(value)
        if route.chat_type == "private":
            raise TelegramError("telegram_group_review_required")
        profile = self.agent_profiles.get(route.agent_id)
        self._telegram_validate_route(route)
        self._assert_workspace_current()
        review = {
            "agent_id": route.agent_id,
            "agent_revision": profile.revision,
            "bot_id": identity.bot_id,
            "username": identity.username,
            "workspace_id": self._workspace_id,
            "max_seconds": 180,
            "consumes_ignored_updates": True,
            "requires_exclusive_bot": True,
            "telegram_retains_start_message": True,
            "chat_id": route.chat_id,
            "chat_type": route.chat_type,
            "allowed_sender_ids": list(route.allowed_sender_ids),
            "message_thread_id": route.message_thread_id,
            "route_revision": route.revision,
            "allow_attachments": route.allow_attachments,
        }
        digest = hashlib.sha256(
            json.dumps(
                ["jarvis_telegram_group_pairing_v1", owner_hash, review], sort_keys=True
            ).encode()
        ).hexdigest()
        return {**review, "review_hash": digest}, internal_hash, route

    async def telegram_group_review(self, value):
        params(value, {"chat_id"}, {"chat_id"})
        return self._group_review(value["chat_id"])[0]

    async def telegram_group_start(self, value):
        fields = {
            "chat_id",
            "expected_route_revision",
            "expected_profile_revision",
            "review_hash",
            "acknowledge_update_consumption",
            "confirm_no_other_consumer",
        }
        params(value, fields, fields)
        self._management_idle()
        self._telegram_require_stopped()
        if (
            value["acknowledge_update_consumption"] is not True
            or value["confirm_no_other_consumer"] is not True
        ):
            raise TelegramError("telegram_pairing_review_required")
        review, internal_hash, route = self._group_review(value["chat_id"])
        if (
            type(value["expected_route_revision"]) is not int
            or type(value["expected_profile_revision"]) is not int
            or route.revision != value["expected_route_revision"]
            or review["agent_revision"] != value["expected_profile_revision"]
            or review["review_hash"] != value["review_hash"]
        ):
            raise TelegramError("telegram_configuration_changed")
        owner = authenticated_client.get()
        self.client_authority.lookup(owner, scope="owner")

        def guard():
            self.client_authority.lookup(owner, scope="owner")
            current = self._group_review(value["chat_id"])[0]
            if current["review_hash"] != review["review_hash"] or self._closing:
                raise TelegramError("telegram_configuration_changed")

        async with self._telegram_mutation():
            if self._telegram_group_pairing is not None:
                await self._telegram_group_pairing.stop()
            pairing = TelegramGroupPairing(
                self._telegram_channel.client,
                self._telegram_journal,
                target_route=route,
                host_guard=guard,
            )
            self._telegram_group_pairing, self._telegram_group_owner = pairing, owner
            self._telegram_group_proof_chat = None
            try:
                await pairing.start(
                    agent_id=route.agent_id,
                    agent_revision=review["agent_revision"],
                    expected_configuration_hash=internal_hash,
                )
                guard()
            except BaseException:
                await pairing.stop()
                self._telegram_group_owner = None
                raise
        return pairing.status()

    async def telegram_group_command(self, value):
        params(value, {"challenge_id"}, {"challenge_id"})
        pairing = self._telegram_group_pairing
        if pairing is None:
            raise TelegramError("telegram_pairing_link_unavailable")
        pairing._guard()
        return {
            "challenge_id": pairing.challenge_id,
            "command": pairing.native_command(value["challenge_id"]),
        }

    async def telegram_group_status(self, value):
        params(value, set())
        return (
            self._telegram_group_pairing.status()
            if self._telegram_group_pairing
            else {
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
                "target_chat_id": None,
                "chat_type": None,
                "allowed_sender_ids": [],
                "message_thread_id": None,
                "route_revision": None,
                "sender_id": None,
            }
        )

    async def telegram_group_confirm(self, value):
        fields = {
            "claim_id",
            "chat_id",
            "expected_route_revision",
            "expected_profile_revision",
            "configuration_hash",
        }
        params(value, fields, fields)
        self._management_idle()
        pairing = self._telegram_group_pairing
        claim = pairing.claim() if pairing is not None else None
        if claim is None:
            raise TelegramError("telegram_pairing_claim_invalid")
        pairing._guard()
        review, _internal, route = self._group_review(value["chat_id"])
        routes, _internal, configuration_hash = self._telegram_configuration()
        raw_route = next(row for row in routes if row["chat_id"] == route.chat_id)
        if (
            type(value["expected_route_revision"]) is not int
            or type(value["expected_profile_revision"]) is not int
            or value["claim_id"] != claim.claim_id
            or claim.chat_id != route.chat_id
            or route != pairing.target_route
            or value["expected_route_revision"] != route.revision
            or value["expected_profile_revision"] != review["agent_revision"]
            or claim.agent_revision != review["agent_revision"]
            or value["configuration_hash"] != configuration_hash
        ):
            raise TelegramError("telegram_configuration_changed")
        key = self._telegram_restored_pairing_key(raw_route)
        async with self._telegram_mutation():
            pairing.consume(
                claim_id=claim.claim_id,
                agent_id=route.agent_id,
                expected_profile_revision=claim.agent_revision,
            )
            await pairing.stop(state="confirmed")
            self.client_authority.lookup(self._telegram_group_owner, scope="owner")
            self._assert_workspace_current()
            if (
                self._group_review(route.chat_id)[0] != review
                or self._telegram_restored_pairing_key(raw_route) != key
            ):
                raise TelegramError("telegram_configuration_changed")
            self._telegram_restored_pairings[route.chat_id] = key
            self._telegram_pairing_proof_owners[route.chat_id] = self._telegram_group_owner
            self._telegram_group_proof_chat = route.chat_id
            self._telegram_group_owner = None
        return self._telegram_snapshot()

    async def telegram_group_cancel(self, value):
        params(value, set())
        async with self._telegram_mutation():
            if self._telegram_group_pairing is not None:
                await self._telegram_group_pairing.stop()
            proof_chat = self._telegram_group_proof_chat
            if proof_chat is not None:
                self._telegram_restored_pairings.pop(proof_chat, None)
                self._telegram_pairing_proof_owners.pop(proof_chat, None)
            self._telegram_group_proof_chat = None
            self._telegram_group_owner = None
        return await self.telegram_group_status({})
