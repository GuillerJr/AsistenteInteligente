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
from aegis_core.memory.errors import MemoryNotFoundError, MemoryStoreError
from aegis_core.secrets import SecretNotFoundError, contains_likely_secret_material
from aegis_core.telegram.client import (
    TOKEN_PATTERN,
    TelegramAPIError,
    TelegramClient,
    validate_token,
)
from aegis_core.telegram.configuration import TelegramConfiguration, TelegramConfigurationStore
from aegis_core.telegram.experience import TelegramExperience
from aegis_core.telegram.journal import InboxEntry, MessageReference, TransportState

HELP = (
    "Soy Jarvis. Escríbeme lo que necesitas; usaré tu configuración de NVIDIA y tus herramientas. "
    "Recupero automáticamente lo relevante de tus chats del Mac y tu memoria compartida. "
    "Las acciones que requieran aprobación mostrarán botones para aprobar una vez o rechazar.\n\n"
    "/nuevo — nueva conversación\n/cancelar — cancelar la tarea\n/estado — estado del servicio\n"
    "/chats — ver conversaciones del Mac\n/continuar ID — retomar un chat\n"
    "/ayuda — esta ayuda\n\n"
    "Recibo texto, notas de voz, imágenes y archivos de texto o PDF. "
    "Puedes responder a un mensaje concreto y usar sus botones. "
    "Tu Mac debe estar encendido, conectado y con Jarvis activo."
)


class ConfirmPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = Field(strict=True)


class ConfigurePayload(ConfirmPayload):
    token: str = Field(min_length=37, max_length=121, repr=False)


class PairApprovalPayload(ConfirmPayload):
    candidate_id: int = Field(gt=0, le=2**52, strict=True)


class ConversationSelectionPayload(ConfirmPayload):
    conversation_id: UUID


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


class TelegramService(TelegramExperience):
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
        journal=None,
        transcriber=None,
        synthesizer=None,
        batch_seconds: float = 1.2,
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
        self._init_experience(journal, transcriber, synthesizer, batch_seconds)

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
        if self._journal_error:
            state = "blocked"
        elif self._blocked() and (self._pair is not None or self._config.enabled):
            state = "suspended"
        elif self._pair is not None:
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
            "error": "telegram_journal_invalid" if self._journal_error else self._error,
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
                "telegram.conversation.select",
                "telegram.journal.reset",
            )
        }

    async def _handle(self, request: IpcRequest) -> IpcHandlerResult:
        try:
            async with self._lock:
                payload = request.payload
                if request.method == "telegram.status":
                    if payload:
                        raise ValueError("unexpected payload")
                elif request.method == "telegram.journal.reset":
                    value = ConfirmPayload.model_validate(payload)
                    if not value.confirm or not self._journal_error or self._journal is None:
                        raise ValueError("explicit journal recovery required")
                    state = TransportState(
                        bot_id=self._config.bot_id,
                        owner_id=self._config.owner_id,
                        acknowledged_offset=self._config.offset,
                    )
                    # Persist the barrier before replacing damaged transport state.
                    # A crash after quarantine must never restore an enabled old inbox.
                    self._save(enabled=False, discard_backlog=True)
                    self._journal.reset(state)
                    self._journal_error = False
                    self._transport = state
                    await self._reset_session()
                    # No old pending message or callback may become a new action after recovery.
                    self._pair, self._candidate = None, None
                    self._error = None
                elif self._journal_error:
                    return IpcHandlerResult(ok=False, error_code="telegram_journal_invalid")
                elif request.method == "telegram.conversation.select":
                    value = ConversationSelectionPayload.model_validate(payload)
                    if not value.confirm:
                        raise ValueError("confirmation required")
                    if not self._config.enabled or self._config.owner_id is None:
                        return IpcHandlerResult(ok=False, error_code="telegram_owner_not_linked")
                    await self._select_conversation(value.conversation_id)
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
                    if not value.enabled:
                        self._commit_transport(
                            TransportState(
                                bot_id=self._config.bot_id, owner_id=self._config.owner_id
                            )
                        )
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
            return IpcHandlerResult(
                ok=False,
                error_code="telegram_journal_invalid"
                if self._journal_error
                else "telegram_invalid_configuration",
            )
        except MemoryNotFoundError:
            return IpcHandlerResult(ok=False, error_code="telegram_conversation_not_found")
        except MemoryStoreError:
            return IpcHandlerResult(ok=False, error_code="telegram_conversation_unavailable")
        except JobError:
            return IpcHandlerResult(ok=False, error_code="telegram_conversation_busy")
        except (OSError, SecretNotFoundError, subprocess.SubprocessError):
            return IpcHandlerResult(
                ok=False,
                error_code="telegram_journal_invalid"
                if self._journal_error
                else "telegram_credential_unavailable",
            )

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

    async def _reset_session(self, *, preserve_inbox_ids=()) -> None:
        self._epoch += 1
        await self._reset_experience(preserve_inbox_ids=preserve_inbox_ids)
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
            if (
                self._journal_error
                or (not self._config.enabled and self._pair is None)
                or self._blocked()
            ):
                await asyncio.sleep(1)
                continue
            epoch = self._epoch
            try:
                if self._client is None:
                    expected_bot = self._config.bot_id
                    token = await run_blocking_owned(self._keychain.get)
                    client = self._factory(token)
                    try:
                        bot = await client.call("getMe")
                        if (
                            not isinstance(bot, dict)
                            or bot.get("id") != expected_bot
                            or bot.get("is_bot") is not True
                        ):
                            raise TelegramAPIError("telegram_credential_invalid", 60)
                    except BaseException:
                        await client.close()
                        raise
                    accepted = False
                    try:
                        async with self._lock:
                            accepted = (
                                epoch == self._epoch and not self._closed and self._client is None
                            )
                            if accepted:
                                self._client = client
                    finally:
                        if not accepted:
                            await client.close()
                    if not accepted:
                        continue
                async with self._lock:
                    if epoch == self._epoch and self._config.discard_backlog:
                        await self._discard_backlog()
                    if epoch == self._epoch and self._config.enabled:
                        await self._recover_transport()
                updates = await self._client.call(
                    "getUpdates",
                    {
                        "offset": max(self._config.offset, self._transport.acknowledged_offset),
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
                    if not self._journal_error:
                        self._error = None
                    for update in updates[:25]:
                        await self.process_update(update)
                if not updates:
                    await asyncio.sleep(0.1)
            except TelegramAPIError as error:
                if epoch != self._epoch:
                    continue
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
                if epoch != self._epoch:
                    continue
                if not self._journal_error:
                    self._error = "telegram_service_unavailable"
                await asyncio.sleep(5)

    async def _discard_backlog(self) -> None:
        updates = await self._client.call(
            "getUpdates",
            {
                "offset": -1,
                "timeout": 0,
                "limit": 1,
                "allowed_updates": ["message", "callback_query"],
            },
        )
        if not isinstance(updates, list) or len(updates) > 1:
            raise TelegramAPIError("telegram_response_invalid")
        offset = self._config.offset
        if updates:
            identifier = updates[0].get("update_id") if isinstance(updates[0], dict) else None
            if type(identifier) is not int or not 0 <= identifier < 2**53:
                raise TelegramAPIError("telegram_response_invalid")
            offset = max(offset, identifier + 1)
        self._transport_update(acknowledged_offset=offset)
        self._save(offset=offset, discard_backlog=False)

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
        if self._journal_error or self._config.discard_backlog or self._blocked() or self._closed:
            return  # Do not acknowledge an instruction while this channel cannot admit it.
        self._ensure_transport_binding()
        if not isinstance(update, dict) or type(update.get("update_id")) is not int:
            return
        identifier = update["update_id"]
        offset = max(self._config.offset, self._transport.acknowledged_offset)
        if not offset <= identifier < 2**53:
            return
        message = update.get("message")
        entry = None
        if isinstance(message, dict) and self._config.enabled and self._pair is None:
            actor = self._private_actor(message)
            text = message.get("text")
            if not isinstance(text, str) and any(
                k in message for k in ("voice", "photo", "document")
            ):
                text = message.get("caption", "")
            if (
                actor == self._config.owner_id
                and type(message.get("date")) is int
                and isinstance(text, str)
                and not text.strip().startswith("/")
                and len(text.encode("utf-8")) <= 4096
                and not TOKEN_PATTERN.search(text)
                and not contains_likely_secret_material(text)
            ):
                if len(self._transport.inbox) >= 4:
                    raise TelegramAPIError("telegram_inbox_full", 5)
                if self._config.conversation_id is None:
                    record = await self._conversations.create(title="Telegram · Jarvis")
                    self._save(conversation_id=record.conversation_id)
                entry = InboxEntry(
                    update_id=identifier,
                    message=self._inbox_message(message, text, actor),
                    conversation_id=self._config.conversation_id,
                    received_while_active=self._active is not None,
                )
        # The same encrypted commit retains the input and advances its acknowledgement.
        # Callbacks/commands are acknowledged without being put in any replayable inbox.
        self._transport_update(
            acknowledged_offset=identifier + 1,
            inbox=(*self._transport.inbox, entry) if entry else self._transport.inbox,
        )
        self._save(offset=identifier + 1)
        if entry:
            await self._process_inbox_entry(entry)
        else:
            await self._process_acknowledged_update(update)

    @staticmethod
    def _inbox_message(message: dict, text: str, actor: int) -> dict:
        # Persist only the validated owner, media metadata and locally resolved quote ID.
        value = {
            "date": message["date"],
            "message_id": message.get("message_id", 0),
            "from": {"id": actor, "is_bot": False},
            "chat": {"id": actor, "type": "private"},
            "text": text,
        }
        quote = message.get("reply_to_message")
        if isinstance(quote, dict) and type(quote.get("message_id")) is int:
            value["reply_to_message"] = {"message_id": quote["message_id"]}
        for key in ("voice", "document", "photo"):
            media = message.get(key)
            if key == "photo" and isinstance(media, list) and media:
                media = media[-1]
            if isinstance(media, dict):
                safe = {
                    name: item
                    for name, item in media.items()
                    if name in {"file_id", "duration", "file_size", "file_name", "mime_type"}
                    and isinstance(item, (str, int))
                }
                value[key] = [safe] if key == "photo" else safe
        return value

    def _consume_inbox(self, identifiers: tuple[int, ...]) -> None:
        remaining = tuple(e for e in self._transport.inbox if e.update_id not in identifiers)
        if remaining != self._transport.inbox:
            self._transport_update(inbox=remaining)

    async def _process_inbox_entry(self, entry: InboxEntry) -> None:
        if self._blocked() or self._closed or self._journal_error:
            return
        if entry.claimed:
            return
        if hasattr(self._conversations, "ensure_exists"):
            try:
                await self._conversations.ensure_exists(entry.conversation_id)
            except MemoryNotFoundError:
                # Deleting a chat revokes its queued inputs; never retarget them.
                self._consume_inbox((entry.update_id,))
                await self._send(
                    "No tengo disponible el chat original de esa solicitud: fue eliminado. "
                    "No la ejecuté ni la trasladé a otra conversación."
                )
                return
        claimed = entry.model_copy(update={"claimed": True})
        self._transport_update(
            inbox=tuple(
                claimed if e.update_id == entry.update_id else e for e in self._transport.inbox
            )
        )
        if entry.conversation_id != self._config.conversation_id:
            self._save(conversation_id=entry.conversation_id)
        message = {**entry.message, "_inbox_update_id": entry.update_id}
        if entry.prepared_text is not None:
            message["_prepared_text"] = entry.prepared_text
        await self._process_acknowledged_update({"message": message})
        if self._media_update_id != entry.update_id and not any(
            i.update_id == entry.update_id for i in self._incoming
        ):
            self._consume_inbox((entry.update_id,))

    async def _recover_inbox(self) -> None:
        if self._busy or self._blocked() or self._closed or self._journal_error:
            return
        if len(self._transport.deliveries) >= 3:
            return  # Keep the input until durable replies have room; never discard it.
        busy_entries = tuple(e for e in self._transport.inbox if e.received_while_active)
        if busy_entries:
            selected = self._config.conversation_id
            try:
                await self._conversations.ensure_exists(busy_entries[0].conversation_id)
                self._save(conversation_id=busy_entries[0].conversation_id)
                await self._enqueue_result(
                    "Recibí otra solicitud mientras trabajaba en la anterior. "
                    "No la ejecuté durante el reinicio. Puedes enviarla de nuevo cuando "
                    "hayas revisado el resultado anterior.\n\nInstrucción conservada:\n"
                    + "\n".join(e.message.get("text") or "[Archivo o audio]" for e in busy_entries),
                    inbox_ids=tuple(e.update_id for e in busy_entries),
                )
            except MemoryNotFoundError:
                self._consume_inbox(tuple(e.update_id for e in busy_entries))
            finally:
                self._save(conversation_id=selected)
        claimed = tuple(e for e in self._transport.inbox if e.claimed)
        if claimed:
            # A submitted request atomically removes its inbox entries and stores
            # InterruptedRun first. Remaining claimed entries never reached submission.
            self._transport_update(
                inbox=tuple(e.model_copy(update={"claimed": False}) for e in self._transport.inbox)
            )
        gate = asyncio.Event()
        self._media_admission_gate = gate
        try:
            for entry in tuple(self._transport.inbox):
                media = any(key in entry.message for key in ("voice", "document", "photo"))
                if (
                    not entry.claimed
                    and not self._active
                    and (self._media_task is None or not media)
                ):
                    # Text that followed a queued attachment belongs to its batch,
                    # including after a restart. Never execute it alone if decoding fails.
                    await self._process_inbox_entry(entry)
        except BaseException:
            # Admission never crossed submission. Roll back only transient state;
            # source entries and their marker remain encrypted for a later retry.
            # Clear the media ID first so task cancellation cannot consume a source.
            self._media_update_id = None
            if self._media_task:
                self._media_task.cancel()
                await asyncio.gather(self._media_task, return_exceptions=True)
                self._media_task = None
            self._incoming.clear()
            raise
        finally:
            gate.set()
            if self._media_admission_gate is gate:
                self._media_admission_gate = None
        if self._incoming and self._media_task is None and not self._active:
            if self._batch_seconds == 0:
                await self._submit_incoming()
            else:
                self._batch_task = asyncio.create_task(self._flush_batch(self._epoch))

    async def _process_acknowledged_update(self, update) -> None:
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
        if actor is None or type(timestamp) is not int:
            return
        if not -30 <= self._clock() - timestamp <= 300:
            if self._config.enabled and actor == self._config.owner_id and self._pair is None:
                await self._send(
                    "Recibí una solicitud de cuando Jarvis no estaba disponible. "
                    "Tiene más de cinco minutos o una fecha no válida; no la ejecuté. "
                    "Envíala de nuevo si todavía quieres realizarla."
                )
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
        has_media = any(key in message for key in ("voice", "document", "photo"))
        if not isinstance(text, str) and has_media:
            text = message.get("caption", "")
        if not isinstance(text, str):
            await self._send(
                "Por ahora puedo recibir mensajes de texto. Escribe tu instrucción aquí."
            )
            return
        text = text.strip()
        if (
            (not text and not has_media)
            or len(text.encode("utf-8")) > 4096
            or TOKEN_PATTERN.search(text)
            or contains_likely_secret_material(text)
        ):
            await self._send(
                "No puedo procesar ese mensaje. Usa texto breve y no compartas claves ni tokens."
            )
            return
        command = text.split()[0].split("@")[0].lower() if text else ""
        if command in {"/start", "/ayuda", "/help"}:
            await self._send(HELP)
        elif command == "/estado":
            await self._send(
                "Jarvis está conectado en tu Mac."
                + (" Estoy trabajando en una tarea." if self._busy else " Estoy listo.")
                + (
                    f" Hay {len(self._transport.deliveries)} respuesta(s) guardada(s) por entregar."
                    if self._transport.deliveries
                    else ""
                )
            )
        elif command in {"/cancelar", "/nuevo"}:
            await self._reset_session()
            if command == "/nuevo":
                self._save(conversation_id=None)
            await self._send(
                "Nueva conversación lista. Tu memoria compartida sigue disponible."
                if command == "/nuevo"
                else "Tarea cancelada."
            )
        elif self._busy and command.startswith("/"):
            await self._send("Hay una solicitud en curso. Usa /estado o /cancelar.")
        elif command == "/chats":
            records = await self._conversations.recent()
            await self._send(
                "Conversaciones disponibles:\n\n"
                + "\n\n".join(
                    f"{record.title or 'Conversación'}\n/continuar {record.conversation_id}"
                    for record in records
                )
                if records
                else "Todavía no tienes conversaciones guardadas."
            )
        elif command == "/continuar":
            try:
                identifier = UUID(text.split(maxsplit=1)[1].strip())
                await self._select_conversation(identifier)
                await self._send(
                    "Conversación seleccionada. Tu siguiente mensaje continuará ese chat."
                )
            except (ValueError, IndexError, MemoryNotFoundError):
                await self._send("No encontré ese chat. Usa /chats y copia su comando /continuar.")
        elif command.startswith("/"):
            await self._send(HELP)
        else:
            await self._accept_message(message, text)

    async def _send(self, text: str, *, buttons=None) -> int:
        if (
            not self._config.enabled
            or self._config.owner_id is None
            or self._blocked()
            or self._client is None
            or self._journal_error
        ):
            return 0
        return await asyncio.wait_for(
            self._client.send(self._config.owner_id, text, buttons=buttons),
            timeout=20,
        )

    async def _select_conversation(self, conversation_id: UUID) -> None:
        if not self._config.enabled or self._config.owner_id is None or self._blocked():
            raise ValueError("linked owner required")
        if self._busy:
            raise JobError("active Telegram job")
        await self._conversations.ensure_exists(conversation_id)
        # Selection does not submit, approve or send any content to the bot.
        self._save(conversation_id=conversation_id)
        self._followup = None

    async def _submit(self, text: str, *, request=None, message_ids=(), inbox_ids=()) -> None:
        if self._config.conversation_id is None:
            record = await self._conversations.create(title="Telegram · Jarvis")
            self._save(conversation_id=record.conversation_id)
        followup = self._followup
        self._followup = None
        quoted = request.metadata.get("telegram_message_reference") if request else None
        if (
            followup
            and followup.expires_at > datetime.now(UTC)
            and (not quoted or quoted.get("clarification") is True)
        ):
            text = f"Solicitud original: {followup.request_text}\nRespuesta a la aclaración: {text}"
            if len(text.encode("utf-8")) > 8192:
                await self._send(
                    "La aclaración es demasiado larga. Envía una solicitud nueva más breve."
                )
                return
        request = (
            request.model_copy(update={"text": text})
            if request
            else UserRequest(text=text, metadata={"source_channel": "telegram"})
        )
        await self._begin_transport_run(request, inbox_ids=inbox_ids)
        if self._blocked() or self._closed:
            raise JobError("Telegram runtime unavailable")
        snapshot = await self._jobs.submit(
            request,
            conversation_id=self._config.conversation_id,
        )
        self._active = snapshot.job_id
        self._transport_update(
            interrupted=self._transport.interrupted.model_copy(update={"job_id": snapshot.job_id})
        )
        self._remember(
            MessageReference(
                token=secrets.token_urlsafe(18),
                conversation_id=self._config.conversation_id,
                content=text.encode("utf-8")[:4096].decode("utf-8", errors="ignore"),
                message_ids=tuple(i for i in message_ids if type(i) is int and i > 0),
                created=self._clock(),
            )
        )
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
                    timeout_seconds=4,
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
                    if snapshot.status not in TERMINAL_STATUSES:
                        await self._show_progress(snapshot)
                    if snapshot.status is JobStatus.AWAITING_CONFIRMATION and confirmation:
                        if (
                            self._decision is None
                            or self._decision.digest != confirmation.call_digest
                        ) and self._clock() >= self._delivery_at:
                            nonce = secrets.token_urlsafe(18)
                            preview = (
                                "\n".join(
                                    f"{field.label}: {field.value}"
                                    for field in confirmation.preview.fields
                                )
                                if confirmation.preview
                                else f"Herramienta: {confirmation.tool_name}"
                            )
                            try:
                                message_id = await self._send(
                                    f"Decisión de Jarvis\n{confirmation.summary}\n\n{preview}\n\n"
                                    "Revisa esta acción. La aprobación sirve una sola vez.",
                                    buttons=[
                                        [
                                            {
                                                "text": "Aprobar una vez",
                                                "callback_data": f"a:{nonce}",
                                            },
                                            {"text": "Rechazar", "callback_data": f"r:{nonce}"},
                                        ]
                                    ],
                                )
                            except (TelegramAPIError, TimeoutError):
                                message_id = 0
                                self._error = "telegram_delivery_failed"
                                self._delivery_at = self._clock() + 5
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
                        await self._publish_final(snapshot)
                        self._active = None
                        self._control = None
                        return
                if snapshot.status is JobStatus.AWAITING_CONFIRMATION:
                    await asyncio.sleep(1)
        except (TelegramAPIError, JobError, MemoryStoreError, OSError, ValueError, TimeoutError):
            async with self._lock:
                if epoch == self._epoch and self._active == job_id:
                    self._error = "telegram_delivery_failed"
                    self._active, self._decision = None, None

    async def _callback(self, callback: dict) -> None:
        if await self._extra_callback(callback):
            return
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
        if self._blocked() or self._closed:
            return
        if data.startswith("a:"):
            await self._jobs.approve_from_telegram(decision.job_id, decision.digest, owner_id=actor)
            self._approved_tool = snapshot.confirmation.tool_name
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
