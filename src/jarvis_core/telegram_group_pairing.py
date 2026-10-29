"""Fresh proof for an exact reviewed group; private discovery stays unchanged.

The owner manually posts a one-use command. We never invite a bot, change group
settings, send a command, infer a route or reuse a private chat's proof.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from uuid import uuid4

from jarvis_core.telegram import TelegramError, TelegramRoute
from jarvis_core.telegram_pairing import PairingClaim, TelegramPairing


@dataclass(frozen=True)
class GroupPairingClaim(PairingClaim):
    sender_id: int


class TelegramGroupPairing(TelegramPairing):
    def __init__(self, client, journal, *, target_route, host_guard, **options):
        if (
            not isinstance(target_route, TelegramRoute)
            or target_route.chat_type == "private"
            or not callable(host_guard)
        ):
            raise TelegramError("telegram_group_review_required")
        super().__init__(client, journal, **options)
        self.target_route, self.host_guard = target_route, host_guard
        self._matched_sender = None

    def _guard(self):
        super()._guard()
        current = next(
            (row for row in self.journal.routes() if row.chat_id == self.target_route.chat_id), None
        )
        if current != self.target_route:
            raise TelegramError("telegram_configuration_changed")
        self.host_guard()

    def status(self):
        status = super().status()
        route = self.target_route
        status.update(
            target_chat_id=route.chat_id,
            chat_type=route.chat_type,
            allowed_sender_ids=list(route.allowed_sender_ids),
            message_thread_id=route.message_thread_id,
            route_revision=route.revision,
            sender_id=self._claim.sender_id if self._claim is not None else None,
        )
        return status

    def validate_claim(self):
        # A matched reader has already closed its exclusive polling lease.
        # A claimed proof is still RAM-only and must revalidate all bindings,
        # but cannot assert ownership of the now-retired runner descriptor.
        self._check_expiry()
        if self.state != "claimed" or self._claim is None or self._nonce is None:
            raise TelegramError("telegram_pairing_claim_invalid")
        if self._configuration != self.journal.configuration_hash():
            raise TelegramError("telegram_configuration_changed")
        if (self.client.identity is None or self.client.closed
                or self.client.identity.bot_id != self.journal.bot_id):
            raise TelegramError("telegram_bot_changed")
        self.host_guard()
        current = next((row for row in self.journal.routes()
                        if row.chat_id == self.target_route.chat_id), None)
        if current != self.target_route:
            raise TelegramError("telegram_configuration_changed")

    def native_uri(self, challenge_id):
        raise TelegramError("telegram_group_command_required")

    def native_command(self, challenge_id):
        self._check_expiry()
        if (
            self.state != "listening"
            or self._nonce is None
            or challenge_id != self.challenge_id
            or self._link_retrieved
        ):
            raise TelegramError("telegram_pairing_link_unavailable")
        self._link_retrieved = True
        return "/jarvis_pair@" + self.client.identity.username + " " + self._nonce

    def _match(self, update):
        message = update.get("message")
        if not isinstance(message, dict):
            return None
        chat, sender, route = message.get("chat"), message.get("from"), self.target_route
        if (
            not isinstance(chat, dict)
            or not isinstance(sender, dict)
            or type(chat.get("id")) is not int
            or chat["id"] != route.chat_id
            or chat.get("type") != route.chat_type
            or type(sender.get("id")) is not int
            or sender["id"] not in route.allowed_sender_ids
            or sender.get("is_bot") is not False
            or message.get("sender_chat") is not None
            or (message.get("message_thread_id") is not None
                and type(message["message_thread_id"]) is not int)
            or message.get("message_thread_id") != route.message_thread_id
            or type(message.get("message_id")) is not int
            or not 1 <= message["message_id"] < (1 << 31)
            or type(message.get("date")) is not int
            or not self._started_at <= message["date"] <= self.clock() + 300
        ):
            return None
        text = message.get("text")
        if not isinstance(text, str):
            return None
        try:
            raw = text.encode()
        except UnicodeError:
            return None
        expected = ("/jarvis_pair@" + self.client.identity.username + " " + self._nonce).encode()
        if len(raw) != len(expected) or not hmac.compare_digest(raw, expected):
            return None
        self._matched_sender = sender["id"]
        return chat["id"], message["message_id"]

    def _make_claim(self, chat_id):
        return GroupPairingClaim(
            uuid4().hex,
            self.challenge_id,
            chat_id,
            self.agent_id,
            self.agent_revision,
            self.client.identity.bot_id,
            self.expires_at,
            self._matched_sender,
        )
