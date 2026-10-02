from __future__ import annotations

import asyncio
import secrets
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aegis_core.async_tasks import run_blocking_owned
from aegis_core.contracts import UserRequest
from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult
from aegis_core.job_contracts import TERMINAL_STATUSES, JobError, JobStatus
from aegis_core.memory.errors import MemoryStoreError
from aegis_core.secrets import SecretNotFoundError, contains_likely_secret_material
from aegis_core.telegram.client import (
    TOKEN_PATTERN,
    TelegramAPIError,
    TelegramClient,
    validate_token,
)
from aegis_core.telegram.configuration import TelegramConfiguration, TelegramConfigurationStore

HELP = (
    "Soy Jarvis. Escríbeme lo que necesitas; usaré tu configuración de NVIDIA y tus herramientas. "
    "Las acciones que requieran aprobación mostrarán botones para aprobar una vez o rechazar.\n\n"
    "/nuevo — nueva conversación\n/cancelar — cancelar la tarea\n/estado — estado del servicio\n"
    "/ayuda — esta ayuda\n\n"
    "Por ahora recibo texto. Tu Mac debe estar encendido, conectado y con Jarvis activo."
)


class ConfirmPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = Field(strict=True)


class ConfigurePayload(ConfirmPayload):
    token: str = Field(min_length=37, max_length=121, repr=False)


class PairApprovalPayload(ConfirmPayload):
    candidate_id: int = Field(gt=0, le=2**52, strict=True)


class EnabledPayload(ConfirmPayload):
    enabled: bool = Field(strict=True)


@dataclass(frozen=True)
class Pairing:
    nonce: str
    expires: float
    attempts: int = 0


@dataclass(frozen=True)
class Decision:
    nonce: str
    job_id: UUID
    digest: str
    message_id: int
    expires: datetime


class TelegramService:
    """Outbound-only, explicitly paired owner adapter for the existing job broker."""

    def __init__(
        self,
        store: TelegramConfigurationStore,
        keychain,
        jobs,
        conversations,
        *,
        blocked: Callable[[], bool],
        client_factory=TelegramClient,
        clock=time.time,
        pair_clock=time.monotonic,
    ) -> None:
        self._store, self._keychain = store, keychain
        self._jobs, self._conversations = jobs, conversations
        self._blocked, self._factory, self._clock = blocked, client_factory, clock
        self._pair_clock = pair_clock
        self._error: str | None = None
        try:
            self._config = store.load()
        except (OSError, ValueError):
            self._config = TelegramConfiguration()
            self._error = "telegram_configuration_invalid"
        self._client = None
        self._lock = asyncio.Lock()
        self._pair: Pairing | None = None
        self._candidate: dict | None = None
        self._active: UUID | None = None
        self._watch: asyncio.Task | None = None
        self._decision: Decision | None = None
        self._followup = None
        self._epoch = 0
        self._closed = False

    def _save(self, **changes) -> None:
        candidate = TelegramConfiguration.model_validate(
            {**self._config.model_dump(), **changes},
        )
        self._store.save(candidate)
        self._config = candidate

    def _expire_pair(self) -> None:
        if self._pair is not None and self._pair.expires <= self._pair_clock():
            self._pair, self._candidate = None, None

    def status(self) -> dict:
        self._expire_pair()
        state = "disabled"
        if self._pair is not None:
            state = "awaiting_confirmation" if self._candidate else "awaiting_pair"
        elif self._config.enabled:
            state = (
                "suspended"
                if self._blocked()
                else "connected"
                if self._client and not self._error
                else "connecting"
            )
        return {
            "state": state,
            "enabled": self._config.enabled,
            "configured": self._config.bot_id is not None,
            "bot_username": self._config.bot_username,
            "owner_id": self._config.owner_id,
            "owner_name": self._config.owner_name,
            "candidate": self._candidate,
            "error": self._error,
            "pair_url": (
                f"https://t.me/{self._config.bot_username}?start={self._pair.nonce}"
                if self._pair
                else None
            ),
        }

    def handlers(self) -> dict:
        return {
            method: self._handle
            for method in (
                "telegram.status",
                "telegram.configure",
                "telegram.pair.begin",
                "telegram.pair.approve",
                "telegram.set_enabled",
                "telegram.unlink",
            )
        }

    async def _handle(self, request: IpcRequest) -> IpcHandlerResult:
        try:
            async with self._lock:
                payload = request.payload
                if request.method == "telegram.status":
                    if payload:
                        raise ValueError("unexpected payload")
                elif request.method == "telegram.configure":
                    value = ConfigurePayload.model_validate(payload)
                    if value.confirm is not True:
                        raise ValueError("confirmation required")
                    await self._configure(value.token)
                elif request.method == "telegram.pair.begin":
                    if payload or self._config.bot_id is None:
                        raise ValueError("configure the bot first")
                    await self._reset_session()
                    self._save(enabled=False)
                    self._pair = Pairing(secrets.token_urlsafe(24), self._pair_clock() + 300)
                elif request.method == "telegram.pair.approve":
                    value = PairApprovalPayload.model_validate(payload)
                    self._expire_pair()
                    if (
                        not value.confirm
                        or not self._pair
                        or not self._candidate
                        or self._candidate["id"] != value.candidate_id
                    ):
                        raise ValueError("pair candidate changed")
                    await self._reset_session()
                    self._save(
                        owner_id=value.candidate_id,
                        owner_name=self._candidate["name"],
                        conversation_id=None,
                        enabled=True,
                    )
                    self._pair, self._candidate = None, None
                elif request.method == "telegram.set_enabled":
                    value = EnabledPayload.model_validate(payload)
                    if not value.confirm or (value.enabled and self._config.owner_id is None):
                        raise ValueError("owner confirmation required")
                    await self._reset_session()
                    self._pair, self._candidate = None, None
                    self._save(enabled=value.enabled)
                elif request.method == "telegram.unlink":
                    value = ConfirmPayload.model_validate(payload)
                    if not value.confirm:
                        raise ValueError("confirmation required")
                    await self._reset_session()
                    self._save(enabled=False, owner_id=None, owner_name=None, conversation_id=None)
                    self._pair, self._candidate = None, None
                else:
                    raise ValueError("unknown method")
                return IpcHandlerResult(ok=True, payload=self.status())
        except TelegramAPIError as error:
            self._error = error.code
            return IpcHandlerResult(ok=False, error_code=error.code)
        except (ValueError, ValidationError):
            return IpcHandlerResult(ok=False, error_code="telegram_invalid_configuration")
        except (OSError, SecretNotFoundError, subprocess.SubprocessError):
            return IpcHandlerResult(ok=False, error_code="telegram_credential_unavailable")

    async def _configure(self, token: str) -> None:
        validate_token(token)
        client = self._factory(token)
        try:
            bot = await client.call("getMe")
            webhook = await client.call("getWebhookInfo")
            if not isinstance(webhook, dict) or webhook.get("url"):
                raise TelegramAPIError("telegram_webhook_configured")
            if not isinstance(bot, dict) or bot.get("is_bot") is not True:
                raise TelegramAPIError("telegram_response_invalid")
            config = TelegramConfiguration(bot_id=bot.get("id"), bot_username=bot.get("username"))
            # First stop the old binding, even if writing the new credential fails.
            await self._reset_session()
            self._save(enabled=False, owner_id=None, owner_name=None, conversation_id=None)
            await run_blocking_owned(self._keychain.set, token)
            self._store.save(config)
            self._config = config
            self._pair, self._candidate = None, None
            if self._client:
                await self._client.close()
            self._client = client
            self._error = None
        except BaseException:
            await client.close()
            raise

    async def _reset_session(self) -> None:
        self._epoch += 1
        if self._watch:
            self._watch.cancel()
            await asyncio.gather(self._watch, return_exceptions=True)
            self._watch = None
        if self._active is not None:
            try:
                await self._jobs.cancel(self._active)
            except JobError:
                pass
        self._active, self._decision, self._followup = None, None, None

    async def run(self) -> None:
        while not self._closed:
            self._expire_pair()
            if (not self._config.enabled and self._pair is None) or self._blocked():
                await asyncio.sleep(1)
                continue
            epoch = self._epoch
            try:
                if self._client is None:
                    token = await run_blocking_owned(self._keychain.get)
                    client = self._factory(token)
                    try:
                        bot = await client.call("getMe")
                        if (
                            not isinstance(bot, dict)
                            or bot.get("id") != self._config.bot_id
                            or bot.get("is_bot") is not True
                        ):
                            raise TelegramAPIError("telegram_credential_invalid", 60)
                    except BaseException:
                        await client.close()
                        raise
                    self._client = client
                updates = await self._client.call(
                    "getUpdates",
                    {
                        "offset": self._config.offset,
                        "timeout": 25,
                        "limit": 25,
                        "allowed_updates": ["message", "callback_query"],
                    },
                )
                if not isinstance(updates, list):
                    raise TelegramAPIError("telegram_response_invalid")
                async with self._lock:
                    if epoch != self._epoch:
                        continue
                    self._error = None
                    for update in updates[:25]:
                        await self.process_update(update)
                if not updates:
                    await asyncio.sleep(0.1)
            except TelegramAPIError as error:
                self._error = error.code
                await asyncio.sleep(error.retry_after)
            except (
                OSError,
                ValueError,
                SecretNotFoundError,
                JobError,
                MemoryStoreError,
                subprocess.SubprocessError,
            ):
                self._error = "telegram_service_unavailable"
                await asyncio.sleep(5)

    @staticmethod
    def _private_actor(message: dict, actor: dict | None = None) -> int | None:
        chat = message.get("chat")
        actor = actor if actor is not None else message.get("from")
        if not isinstance(chat, dict) or not isinstance(actor, dict):
            return None
        identifier = actor.get("id")
        if (
            type(identifier) is int
            and 0 < identifier <= 2**52
            and actor.get("is_bot") is False
            and chat.get("type") == "private"
            and type(chat.get("id")) is int
            and chat["id"] == identifier
        ):
            return identifier
        return None

    async def process_update(self, update) -> None:
        if not isinstance(update, dict) or type(update.get("update_id")) is not int:
            return
        identifier = update["update_id"]
        if not self._config.offset <= identifier < 2**53:
            return
        # Durable acknowledgement BEFORE any submission/approval. Restart never replays actions.
        self._save(offset=identifier + 1)
        self._expire_pair()
        if self._blocked() or self._closed:
            return
        if isinstance(update.get("callback_query"), dict):
            await self._callback(update["callback_query"])
            return
        message = update.get("message")
        if not isinstance(message, dict):
            return
        actor = self._private_actor(message)
        timestamp = message.get("date")
        if (
            actor is None
            or type(timestamp) is not int
            or not -30 <= self._clock() - timestamp <= 300
        ):
            return
        text = message.get("text")
        if self._pair is not None and self._candidate is None:
            if isinstance(text, str) and secrets.compare_digest(
                text.encode("utf-8"),
                f"/start {self._pair.nonce}".encode(),
            ):
                name = message["from"].get("first_name")
                self._candidate = {
                    "id": actor,
                    "name": name[:100] if isinstance(name, str) else "Cuenta de Telegram",
                }
            elif isinstance(text, str) and text.startswith("/start "):
                attempts = self._pair.attempts + 1
                self._pair = (
                    Pairing(self._pair.nonce, self._pair.expires, attempts)
                    if attempts < 20
                    else None
                )
            return
        if not self._config.enabled or actor != self._config.owner_id:
            return
        if not isinstance(text, str):
            await self._send(
                "Por ahora puedo recibir mensajes de texto. Escribe tu instrucción aquí."
            )
            return
        text = text.strip()
        if (
            not text
            or len(text.encode("utf-8")) > 4096
            or TOKEN_PATTERN.search(text)
            or contains_likely_secret_material(text)
        ):
            await self._send(
                "No puedo procesar ese mensaje. Usa texto breve y no compartas claves ni tokens."
            )
            return
        command = text.split()[0].split("@")[0].lower()
        if command in {"/start", "/ayuda", "/help"}:
            await self._send(HELP)
        elif command == "/estado":
            await self._send(
                "Jarvis está conectado en tu Mac."
                + (" Estoy trabajando en una tarea." if self._active else " Estoy listo.")
            )
        elif command in {"/cancelar", "/nuevo"}:
            await self._reset_session()
            if command == "/nuevo":
                self._save(conversation_id=None)
            await self._send(
                "Nueva conversación lista." if command == "/nuevo" else "Tarea cancelada."
            )
        elif self._active:
            await self._send(
                "Estoy trabajando en tu solicitud anterior. "
                "Puedes usar /cancelar antes de enviar otra."
            )
        elif command.startswith("/"):
            await self._send(HELP)
        else:
            await self._submit(text)

    async def _send(self, text: str, *, buttons=None) -> int:
        if (
            not self._config.enabled
            or self._config.owner_id is None
            or self._blocked()
            or self._client is None
        ):
            return 0
        return await asyncio.wait_for(
            self._client.send(self._config.owner_id, text, buttons=buttons),
            timeout=20,
        )

    async def _submit(self, text: str) -> None:
        if self._config.conversation_id is None:
            record = await self._conversations.create(title="Telegram · Jarvis")
            self._save(conversation_id=record.conversation_id)
        followup = self._followup
        self._followup = None
        if followup and followup.expires_at > datetime.now(UTC):
            text = f"Solicitud original: {followup.request_text}\nRespuesta a la aclaración: {text}"
            if len(text.encode("utf-8")) > 8192:
                await self._send(
                    "La aclaración es demasiado larga. Envía una solicitud nueva más breve."
                )
                return
        snapshot = await self._jobs.submit(
            UserRequest(text=text, metadata={"source_channel": "telegram"}),
            conversation_id=self._config.conversation_id,
        )
        self._active = snapshot.job_id
        epoch = self._epoch
        self._watch = asyncio.create_task(
            self._watch_job(snapshot.job_id, epoch), name="telegram-job"
        )

    async def _watch_job(self, job_id: UUID, epoch: int) -> None:
        version = 0
        try:
            while not self._closed:
                snapshot = await self._jobs.wait_for_change(
                    job_id,
                    after_stream_version=version,
                    timeout_seconds=10,
                )
                version = snapshot.stream_version
                async with self._lock:
                    if epoch != self._epoch or job_id != self._active:
                        return
                    if self._blocked():
                        await self._jobs.cancel(job_id)
                        self._active, self._decision = None, None
                        return
                    confirmation = snapshot.confirmation
                    if snapshot.status is JobStatus.AWAITING_CONFIRMATION and confirmation:
                        if (
                            self._decision is None
                            or self._decision.digest != confirmation.call_digest
                        ):
                            nonce = secrets.token_urlsafe(18)
                            preview = (
                                "\n".join(
                                    f"{field.label}: {field.value}"
                                    for field in confirmation.preview.fields
                                )
                                if confirmation.preview
                                else f"Herramienta: {confirmation.tool_name}"
                            )
                            message_id = await self._send(
                                f"Decisión de Jarvis\n{confirmation.summary}\n\n{preview}\n\n"
                                "Revisa esta acción. La aprobación sirve una sola vez.",
                                buttons=[
                                    [
                                        {"text": "Aprobar una vez", "callback_data": f"a:{nonce}"},
                                        {"text": "Rechazar", "callback_data": f"r:{nonce}"},
                                    ]
                                ],
                            )
                            if message_id:
                                self._decision = Decision(
                                    nonce,
                                    job_id,
                                    confirmation.call_digest,
                                    message_id,
                                    confirmation.expires_at,
                                )
                    elif snapshot.status in TERMINAL_STATUSES:
                        self._decision = None
                        if snapshot.status is JobStatus.COMPLETED:
                            self._followup = snapshot.input_request
                            await self._send(
                                snapshot.input_request.question
                                if snapshot.input_request
                                else snapshot.result or "Tarea finalizada."
                            )
                        elif snapshot.status is JobStatus.FAILED:
                            await self._send(
                                "No pude completar la tarea. "
                                "Revisa Jarvis en tu Mac y vuelve a intentarlo."
                            )
                        else:
                            await self._send("Tarea cancelada.")
                        self._active = None
                        return
                if snapshot.status is JobStatus.AWAITING_CONFIRMATION:
                    await asyncio.sleep(1)
        except (TelegramAPIError, JobError, OSError, ValueError):
            async with self._lock:
                if epoch == self._epoch and self._active == job_id:
                    self._error = "telegram_delivery_failed"
                    try:
                        await self._jobs.cancel(job_id)
                    except JobError:
                        pass
                    self._active, self._decision = None, None

    async def _callback(self, callback: dict) -> None:
        message = callback.get("message")
        if not isinstance(message, dict):
            return
        actor = self._private_actor(message, callback.get("from"))
        decision = self._decision
        data = callback.get("data")
        if (
            not self._config.enabled
            or actor != self._config.owner_id
            or decision is None
            or type(message.get("message_id")) is not int
            or message["message_id"] != decision.message_id
            or decision.job_id != self._active
            or decision.expires <= datetime.now(UTC)
            or not isinstance(data, str)
            or data not in (f"a:{decision.nonce}", f"r:{decision.nonce}")
        ):
            return
        snapshot = await self._jobs.status(decision.job_id)
        if (
            snapshot.status is not JobStatus.AWAITING_CONFIRMATION
            or not snapshot.confirmation
            or snapshot.confirmation.call_digest != decision.digest
        ):
            return
        self._decision = None
        if data.startswith("a:"):
            await self._jobs.approve_from_telegram(decision.job_id, decision.digest, owner_id=actor)
        else:
            await self._jobs.cancel(decision.job_id)
        # These cosmetic API calls happen only AFTER the one-time decision is consumed.
        callback_id = callback.get("id")
        if isinstance(callback_id, str) and len(callback_id) <= 128:
            await self._client.call(
                "answerCallbackQuery",
                {"callback_query_id": callback_id, "text": "Decisión registrada"},
            )
        await self._client.call(
            "editMessageReplyMarkup",
            {
                "chat_id": actor,
                "message_id": decision.message_id,
                "reply_markup": {"inline_keyboard": []},
            },
        )

    async def close(self) -> None:
        self._closed = True
        async with self._lock:
            await self._reset_session()
            if self._client:
                await self._client.close()
                self._client = None
