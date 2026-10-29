"""Owner-reviewed private-chat pairing; no inference or outbound Bot API calls.

Official URI contract: https://core.telegram.org/api/links#bot-links.
Only the native Telegram app may receive the sensitive tg:// URI. Telegram
retains the owner's /start message; Jarvis keeps no command body or nonce.
"""

from __future__ import annotations

import asyncio
import hmac
import math
import os
import secrets
import time
from dataclasses import asdict, dataclass
from uuid import uuid4

from jarvis_core.private_json import PrivateJsonError
from jarvis_core.telegram import TelegramError


@dataclass(frozen=True)
class PairingClaim:
    claim_id: str
    challenge_id: str
    chat_id: int
    agent_id: str
    agent_revision: int
    bot_id: int
    expires_at: float


class TelegramPairing:
    def __init__(self, client, journal, *, ttl=180, clock=time.time, monotonic=time.monotonic):
        if type(ttl) not in {int, float} or not math.isfinite(ttl) or not 0 < ttl <= 180:
            raise TelegramError("telegram_pairing_ttl_invalid")
        self.client, self.journal = client, journal
        self.ttl, self.clock, self.monotonic = ttl, clock, monotonic
        self.state, self.error_code = "idle", None
        self.challenge_id = self.agent_id = self.agent_revision = None
        self.expires_at = self._deadline = self._started_at = None
        self._nonce = self._claim = self._configuration = None
        self._worker = self._expiry = self._lease = None
        self._stop = asyncio.Event()
        self._link_retrieved = False
        self.host_guard = None

    @property
    def busy(self):
        self._check_expiry()
        return self._lease is not None or self.state in {"starting", "listening", "claimed"}

    def _invalidate(self, state):
        self.state, self._nonce, self._claim = state, None, None
        self._stop.set()
        if self._worker is not None and not self._worker.done():
            self._worker.cancel()

    def _check_expiry(self):
        if (
            self.state in {"starting", "listening", "claimed"}
            and self.monotonic() >= self._deadline
        ):
            self._invalidate("expired")

    def status(self):
        self._check_expiry()
        identity = self.client.identity
        return {
            "state": self.state,
            "challenge_id": self.challenge_id,
            "claim_id": self._claim.claim_id if self._claim is not None else None,
            "chat_id": self._claim.chat_id if self._claim is not None else None,
            "agent_id": self.agent_id,
            "agent_revision": self.agent_revision,
            "bot_id": identity.bot_id if identity is not None else None,
            "username": identity.username if identity is not None else None,
            "expires_at": self.expires_at,
            "error": self.error_code,
        }

    async def start(self, *, agent_id, agent_revision, expected_configuration_hash):
        if self.busy:
            raise TelegramError("telegram_pairing_active")
        identity = self.client.identity
        if identity is None or identity.username is None or self.client.closed:
            raise TelegramError("telegram_pairing_username_required")
        if expected_configuration_hash != self.journal.configuration_hash():
            raise TelegramError("telegram_configuration_changed")
        self._lease = self.journal.acquire_runner()
        self.state, self.error_code = "starting", None
        self.challenge_id, self._nonce = uuid4().hex, secrets.token_urlsafe(32)
        self.agent_id, self.agent_revision = agent_id, agent_revision
        self._started_at = math.floor(self.clock())
        self.expires_at, self._deadline = self.clock() + self.ttl, self.monotonic() + self.ttl
        self._configuration = expected_configuration_hash
        self._stop, self._link_retrieved, self._claim = asyncio.Event(), False, None
        try:
            await self.client.ensure_no_webhook()
            self._guard()
            self.state = "listening"
            self._worker = asyncio.create_task(self._run())
            self._expiry = asyncio.create_task(self._expire())
        except BaseException as error:
            target = (
                "expired"
                if self.state == "expired"
                else "cancelled"
                if isinstance(error, asyncio.CancelledError)
                else "failed"
            )
            self.error_code = (
                None
                if target == "cancelled"
                else error.code
                if isinstance(error, TelegramError)
                else "telegram_pairing_start_failed"
            )
            self._invalidate(target)
            os.close(self._lease)
            self._lease = None
            raise
        return self.status()

    def _guard(self):
        self._check_expiry()
        if self._stop.is_set() or self._nonce is None:
            raise TelegramError("telegram_pairing_expired")
        self.journal._assert_runner(self._lease)
        if self._configuration != self.journal.configuration_hash():
            raise TelegramError("telegram_configuration_changed")
        if self.client.identity is None or self.client.identity.bot_id != self.journal.bot_id:
            raise TelegramError("telegram_bot_changed")
        if self.host_guard is not None:
            self.host_guard()

    def native_uri(self, challenge_id):
        self._check_expiry()
        if (
            self.state != "listening"
            or self._nonce is None
            or challenge_id != self.challenge_id
            or self._link_retrieved
        ):
            raise TelegramError("telegram_pairing_link_unavailable")
        self._link_retrieved = True
        return "tg://resolve?domain=" + self.client.identity.username + "&start=" + self._nonce

    def claim(self):
        self._check_expiry()
        return self._claim

    def consume(self, *, claim_id, agent_id, expected_profile_revision):
        self._check_expiry()
        if self.host_guard is not None:
            self.host_guard()
        claim = self._claim
        if (
            self.state != "claimed"
            or claim is None
            or claim.claim_id != claim_id
            or claim.agent_id != agent_id
            or claim.agent_revision != expected_profile_revision
            or self._nonce is None
            or self.client.identity is None
            or claim.bot_id != self.client.identity.bot_id
            or claim.bot_id != self.journal.bot_id
            or self._configuration != self.journal.configuration_hash()
        ):
            raise TelegramError("telegram_pairing_claim_invalid")
        self._invalidate("confirmed")
        return claim

    def _match(self, update):
        message = update.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("chat"), dict):
            return None
        chat, sender = message["chat"], message.get("from")
        if (
            chat.get("type") != "private"
            or type(chat.get("id")) is not int
            or not 1 <= chat["id"] <= (1 << 52) - 1
            or not isinstance(sender, dict)
            or type(sender.get("id")) is not int
            or sender["id"] != chat["id"]
            or sender.get("is_bot") is not False
            or chat["id"] == self.client.identity.bot_id
            or type(message.get("message_id")) is not int
            or not 1 <= message["message_id"] <= (1 << 31) - 1
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
        expected = ("/start " + self._nonce).encode()
        if len(raw) != len(expected) or not hmac.compare_digest(raw, expected):
            return None
        return chat["id"], message["message_id"]

    def _make_claim(self, chat_id):
        return PairingClaim(uuid4().hex, self.challenge_id, chat_id, self.agent_id,
                            self.agent_revision, self.client.identity.bot_id, self.expires_at)

    async def _run(self):
        offset = 0
        try:
            async with asyncio.timeout(max(0, self._deadline - self.monotonic())):
                while not self._stop.is_set():
                    self._guard()
                    updates = await self.client.get_updates(offset, before_dispatch=self._guard)
                    self._guard()
                    for update in updates:
                        self._guard()
                        matched = self._match(update)
                        if matched is not None:
                            chat_id, message_id = matched
                            # Persist the body-free tombstone before any claim
                            # is visible. A failed write cannot propose a route.
                            self.journal.mark_control_message(chat_id, message_id)
                        self.journal.claim(
                            update["update_id"],
                            None,
                            error_code="telegram_pairing_control"
                            if matched
                            else "telegram_pairing_ignored",
                            permit_epoch_change=offset < self.journal.offset,
                        )
                        if matched is not None:
                            self._guard()
                            self._claim = self._make_claim(chat_id)
                            self.state = "claimed"
                            return
                    if updates:
                        offset = updates[-1]["update_id"] + 1
                    else:
                        await asyncio.sleep(0.25)
        except TimeoutError:
            self._invalidate("expired")
        except asyncio.CancelledError:
            if self.state not in {"expired", "cancelled", "confirmed"}:
                self._invalidate("cancelled")
        except (TelegramError, PrivateJsonError) as error:
            self.error_code = error.code
            self._invalidate("expired" if self.state == "expired" else "failed")
        except Exception:
            self.error_code = "telegram_pairing_failed"
            self._invalidate("failed")
        finally:
            if self._lease is not None:
                os.close(self._lease)
                self._lease = None

    async def _expire(self):
        await asyncio.sleep(max(0, self._deadline - self.monotonic()))
        if self.state in {"starting", "listening", "claimed"}:
            self._invalidate("expired")

    async def stop(self, *, state="cancelled"):
        self._invalidate(state)
        tasks = tuple(task for task in (self._worker, self._expiry) if task is not None)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            _done, pending = await asyncio.wait(tasks, timeout=10)
            if pending:
                raise TelegramError("telegram_cleanup_incomplete", effect_status="unknown")
            await asyncio.gather(*tasks, return_exceptions=True)

    def claim_metadata(self):
        claim = self.claim()
        return asdict(claim) if claim is not None else None
