"""Owner-only conversation UX and delivery recovery for the Telegram adapter."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from aegis_core.contracts import DocumentInput, ImageInput, InputModality, UserRequest
from aegis_core.job_contracts import JobError, JobStatus
from aegis_core.memory.crypto import RowAuthenticationError
from aegis_core.memory.errors import MemoryNotFoundError, MemoryStoreError
from aegis_core.memory.task_checkpoint import ResultReference
from aegis_core.privacy import redact_secrets_for_remote
from aegis_core.providers.errors import ProviderError
from aegis_core.providers.nvidia import NvidiaNimError
from aegis_core.secrets import contains_likely_secret_material
from aegis_core.telegram.client import TOKEN_PATTERN, TelegramAPIError, text_chunks
from aegis_core.telegram.journal import (
    Delivery,
    InterruptedRun,
    MessageReference,
    TransportState,
)
from aegis_core.telegram.media import MediaError, decode, voice_file
from aegis_core.telegram.presentation import failure_message, speech_content

logger = logging.getLogger(__name__)


def bounded(text: str, limit: int) -> str:
    return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


@dataclass(frozen=True)
class Incoming:
    text: str
    message_id: int
    reference: MessageReference | None = None
    documents: tuple[DocumentInput, ...] = ()
    image: ImageInput | None = None
    image_name: str | None = None
    update_id: int | None = None


class TelegramExperience:
    """A mixin: lifecycle and broker authority remain owned by TelegramService."""

    def _init_experience(self, journal, transcriber, synthesizer, batch_seconds) -> None:
        self._journal = journal
        self._journal_error = False
        try:
            self._transport = journal.load() if journal else TransportState()
        except (OSError, ValueError, KeyError, TypeError, RowAuthenticationError):
            self._transport = TransportState()
            self._journal_error = True
            self._error = "telegram_journal_invalid"
        self._transcribe, self._synthesize = transcriber, synthesizer
        self._batch_seconds = batch_seconds
        self._incoming: list[Incoming] = []
        self._batch_task = None
        self._media_task = None
        self._media_admission_gate = None
        self._media_update_id = None
        self._audio_task = None
        self._audio_progress_id = None
        self._audio_nonce = None
        self._progress_id = None
        self._progress_phase = ""
        self._progress_at = 0.0
        self._control = None
        self._active_text = ""
        # A message that arrived while a job ran waits for the owner's choice: queue it
        # for after the job, or correct the running request (Codex's queue and steer).
        self._busy_choice: tuple[str, Incoming] | None = None
        self._after_active: list[Incoming] = []
        self._approved_tool = None
        self._delivery_at = 0.0
        if self._journal_error:
            return
        try:
            self._ensure_transport_binding()
            # Historical resources without provenance must not regain controls.
            if any(r.resources and r.resource_mode is None for r in self._transport.references):
                self._transport_update(
                    references=tuple(
                        r.model_copy(update={"resources": ()}) if r.resource_mode is None else r
                        for r in self._transport.references
                    )
                )
        except (OSError, ValueError):
            return  # Only this channel is blocked; IPC and the other services still start.

    def _ensure_transport_binding(self) -> None:
        if self._journal_error:
            raise ValueError("Telegram journal requires explicit recovery")
        if (self._transport.bot_id, self._transport.owner_id) != (
            self._config.bot_id,
            self._config.owner_id,
        ):
            self._commit_transport(
                TransportState(
                    bot_id=self._config.bot_id,
                    owner_id=self._config.owner_id,
                    acknowledged_offset=self._config.offset,
                )
            )

    def _commit_transport(self, value: TransportState) -> None:
        if self._journal_error:
            raise ValueError("Telegram journal requires explicit recovery")
        if self._journal:
            try:
                self._journal.save(value)
            except (OSError, ValueError, KeyError, TypeError, RowAuthenticationError):
                self._journal_error = True
                self._error = "telegram_journal_invalid"
                raise ValueError("Telegram journal requires explicit recovery") from None
        self._transport = value

    def _transport_update(self, **changes) -> None:
        self._commit_transport(
            TransportState.model_validate(
                {
                    **self._transport.model_dump(),
                    **changes,
                }
            )
        )

    async def _reset_experience(self, *, preserve_inbox_ids=()) -> None:
        for task in (self._batch_task, self._media_task, self._audio_task):
            if task and task is not asyncio.current_task():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self._batch_task = self._media_task = self._audio_task = None
        self._audio_progress_id = self._audio_nonce = None
        self._incoming.clear()
        self._media_update_id = None
        self._control = self._progress_id = None
        self._progress_phase = ""
        self._busy_choice = None
        self._after_active = [item for item in self._after_active
                              if item.update_id in preserve_inbox_ids]
        if not self._closed and not self._journal_error:
            self._transport_update(
                interrupted=None,
                inbox=tuple(e for e in self._transport.inbox if e.update_id in preserve_inbox_ids),
            )

    @property
    def _busy(self) -> bool:
        return bool(self._active or self._incoming or self._media_task)

    async def _reference_for_message(self, message: dict) -> MessageReference | None:
        quote = message.get("reply_to_message")
        if not isinstance(quote, dict):
            return None
        identifier = quote.get("message_id")
        if type(identifier) is not int:
            raise ValueError("invalid message reference")
        # Only our locally recorded messages count, never forwarded/spoofed quote text.
        reference = next(
            (
                r
                for r in reversed(self._transport.references)
                if identifier in r.message_ids
                and self._clock() - r.created < 172800
                and r.conversation_id == self._config.conversation_id
            ),
            None,
        )
        if reference is None:
            raise ValueError("reference unavailable")
        await self._conversations.ensure_exists(reference.conversation_id)
        return reference

    async def _accept_message(self, message: dict, text: str) -> None:
        self._ensure_transport_binding()
        if len(self._transport.deliveries) >= 3:
            await self._send(
                "Tengo respuestas pendientes de entregar. Usa /estado antes de continuar."
            )
            return
        try:
            reference = await self._reference_for_message(message)
        except (ValueError, MemoryNotFoundError):
            await self._send(
                "No tengo disponible ese mensaje en el chat activo. "
                "Indica el correo o archivo concreto que quieres usar."
            )
            return
        prepared_text = message.get("_prepared_text")
        item = Incoming(
            prepared_text if isinstance(prepared_text, str) else text,
            message.get("message_id", 0),
            reference,
            update_id=message.get("_inbox_update_id"),
        )
        if self._active:
            # Ask instead of guessing from fixed phrasings whether this corrects the
            # running request or is a new one for afterwards.
            nonce = secrets.token_urlsafe(12)
            self._busy_choice = (nonce, item)
            await self._send(
                "Sigo con tu solicitud anterior. ¿Qué hago con este mensaje?",
                buttons=[[
                    {"text": "Ponerlo en cola", "callback_data": f"Q:{nonce}"},
                    {"text": "Corregir la actual", "callback_data": f"K:{nonce}"},
                ]],
            )
            return
        if self._media_task:
            if not any(key in message for key in ("voice", "document", "photo")):
                await self._queue_incoming(item)
                return
            await self._send("Estoy preparando tu archivo o audio. Usa /cancelar para detenerlo.")
            return
        if "voice" in message and isinstance(prepared_text, str):
            # This trusted journal value was already transcribed and checked. It
            # can be longer than Telegram's external 4 KiB text limit, up to 8 KiB.
            await self._queue_incoming(item)
            return
        if any(key in message for key in ("voice", "document", "photo")):
            if self._batch_task:
                self._batch_task.cancel()
                self._batch_task = None
            self._media_task = asyncio.create_task(self._prepare_media(message, item, self._epoch))
            self._media_update_id = item.update_id
        else:
            await self._queue_incoming(item)

    async def _prepare_media(self, message: dict, item: Incoming, epoch: int) -> None:
        try:
            gate = self._media_admission_gate
            if gate is not None:
                # Recovered text followers must join the attachment's batch before
                # decoding can fail or submit it. Reading the next encrypted chat
                # entry may otherwise yield to this task in the middle of admission.
                await gate.wait()
            if epoch != self._epoch or self._closed or self._journal_error:
                return
            if "voice" in message and self._transcribe is None:
                raise MediaError("unconfigured")
            self._control = secrets.token_urlsafe(18)
            self._progress_id = await self._send(
                "Transcribiendo tu nota de voz…"
                if "voice" in message
                else "Preparando tu archivo…",
                buttons=[[{"text": "Cancelar", "callback_data": f"c:{self._control}"}]],
            )
            if "voice" in message:
                value = message["voice"]
                if (
                    not isinstance(value, dict)
                    or type(value.get("duration")) is not int
                    or not 0 < value["duration"] <= 120
                    or self._transcribe is None
                ):
                    raise MediaError("invalid")
                data = await self._client.download(value.get("file_id"), limit=8_000_000)
                await self._cosmetic(
                    "sendChatAction", {"chat_id": self._config.owner_id, "action": "typing"}
                )
                transcript = await self._transcribe(data)
                if TOKEN_PATTERN.search(transcript) or contains_likely_secret_material(transcript):
                    raise MediaError("secret")
                item = replace(item, text=transcript)
                # Linking identifies the channel owner, never a biometric voice verification.
            else:
                photo = message.get("photo")
                if isinstance(photo, list) and photo:
                    value = photo[-1]
                    name, kind = "foto-telegram.jpg", "image"
                else:
                    value = message.get("document")
                    if not isinstance(value, dict):
                        raise MediaError("invalid")
                    name = value.get("file_name")
                    if (
                        not isinstance(name, str)
                        or not 1 <= len(name) <= 255
                        or any(c in name for c in ("/", "\\", "\x00", "\n", "\r"))
                    ):
                        raise MediaError("invalid")
                    kind = (
                        "image"
                        if name.rsplit(".", 1)[-1].lower() in {"png", "jpg", "jpeg", "webp"}
                        else "document"
                    )
                if not isinstance(value, dict):
                    raise MediaError("invalid")
                data = await self._client.download(value.get("file_id"))
                decoded = await decode(data, kind=kind, name=name)
                if kind == "image":
                    item = replace(
                        item,
                        image=ImageInput.model_validate(decoded),
                        image_name=name,
                        text=item.text or "Describe la imagen que adjunté.",
                    )
                else:
                    document = DocumentInput.model_validate(decoded)
                    if contains_likely_secret_material(document.content):
                        raise MediaError("secret")
                    item = replace(
                        item,
                        documents=(document,),
                        text=item.text or "Resume el documento que adjunté.",
                    )
            async with self._lock:
                if epoch != self._epoch or self._closed:
                    return
                if self._blocked():
                    await self._begin_transport_run(
                        UserRequest(text=item.text or "[Archivo o audio]"),
                        submission_started=False,
                    )
                    self._transport_update(
                        interrupted=self._transport.interrupted.model_copy(
                            update={"input_recovery_allowed": False}
                        )
                    )
                    return
                self._media_task = None
                if self._progress_id:
                    await self._cosmetic(
                        "editMessageText",
                        {
                            "chat_id": self._config.owner_id,
                            "message_id": self._progress_id,
                            "text": "Contenido preparado.",
                            "reply_markup": {"inline_keyboard": []},
                        },
                    )
                if "voice" in message:
                    await self._send("Entendí: " + item.text)
                if not await self._queue_incoming(item) and self._incoming:
                    await self._reject_media_batch(item)
        except (
            TelegramAPIError,
            MediaError,
            NvidiaNimError,
            ProviderError,
            ValueError,
            OSError,
            JobError,
            MemoryStoreError,
        ):
            async with self._lock:
                if epoch == self._epoch and not self._closed:
                    self._media_task = None
                    if not self._journal_error:
                        if (
                            self._transport.interrupted
                            and self._transport.interrupted.submission_started
                        ):
                            await self._notify(
                                "La solicitud ya llegó al servicio, pero no pude presentar "
                                "su estado. Comprueba el resultado antes de enviarla de nuevo."
                            )
                        else:
                            await self._reject_media_batch(
                                item, voice_unconfigured=(
                                    "voice" in message and self._transcribe is None
                                ),
                            )
        finally:
            if self._media_task is asyncio.current_task():
                self._media_task = None
            if self._media_update_id == item.update_id:
                self._media_update_id = None
                if (
                    item.update_id is not None
                    and not any(incoming.update_id == item.update_id for incoming in self._incoming)
                    and not self._journal_error
                    and not self._closed
                    and not self._blocked()
                ):
                    self._consume_inbox((item.update_id,))

    async def _reject_media_batch(
        self, item: Incoming, *, voice_unconfigured: bool = False,
    ) -> None:
        """Close a failed attachment batch only after its rejection is durable."""
        pending = tuple(self._incoming)
        retained = [incoming.text for incoming in pending]
        if item.text and not any(incoming.update_id == item.update_id for incoming in pending):
            retained.insert(0, item.text)
        text = (
            "La transcripción de notas de voz aún no está configurada en Jarvis. "
            "Envía el contenido por escrito para continuar."
            if voice_unconfigured else
            "No pude completar la solicitud con ese archivo o audio. "
            "Puedes enviar una imagen, texto o PDF con texto (máximo 10 MB). "
            "Las notas de voz requieren un proveedor de audio configurado."
        )
        if retained:
            combined = "\n".join(retained)
            text += (
                "\n\nNo ejecuté los mensajes agrupados porque el adjunto no está disponible "
                "para esa solicitud. Envíalos de nuevo con el archivo o como una solicitud "
                "independiente.\n\n"
            )
            if len(combined.encode("utf-8")) <= 8192:
                text += "Textos conservados:\n" + combined
            else:
                text += (
                    "La agrupación también supera el límite de texto. Esta es solo una "
                    "copia parcial; divide y reenvía tus mensajes completos:\n"
                    + bounded(combined, 8192)
                )
        identifiers = tuple(
            identifier
            for identifier in (item.update_id, *(i.update_id for i in pending))
            if identifier is not None
        )
        progress_id = self._progress_id
        await self._enqueue_result(text, inbox_ids=identifiers)
        self._incoming.clear()
        if self._batch_task:
            self._batch_task.cancel()
            self._batch_task = None
        self._control = self._progress_id = None
        self._progress_phase = ""
        if progress_id:
            await self._cosmetic(
                "editMessageText",
                {
                    "chat_id": self._config.owner_id,
                    "message_id": progress_id,
                    "text": "Contenido no preparado. La solicitud no se ejecutó.",
                    "reply_markup": {"inline_keyboard": []},
                },
            )

    async def _release_after_active(self) -> None:
        """Send the messages queued during the previous job, now that it ended."""
        if self._active or self._media_task or not self._after_active:
            return
        queued, self._after_active = self._after_active, []
        for item in queued:
            if not await self._queue_incoming(item):
                break
        if self._incoming and not self._active:
            await self._submit_incoming()

    async def _queue_incoming(self, item: Incoming) -> bool:
        if len(item.text.encode("utf-8")) > 8192:
            await self._send(
                "La instrucción es demasiado larga. Envíala en solicitudes más breves."
            )
            return False
        if len(self._incoming) >= 4:
            await self._send(
                "Agrupé cuatro mensajes. Espera a que los procese antes de añadir otro."
            )
            return False
        if self._incoming and self._incoming[-1].reference != item.reference:
            await self._send(
                "Los mensajes citan objetivos distintos. Envíalos en solicitudes separadas."
            )
            return False
        if item.update_id is not None:
            self._transport_update(
                inbox=tuple(
                    entry.model_copy(update={"prepared_text": item.text})
                    if entry.update_id == item.update_id
                    else entry
                    for entry in self._transport.inbox
                )
            )
        self._incoming.append(item)
        if self._transport.interrupted is None:
            if self._config.conversation_id is None:
                record = await self._conversations.create(title="Telegram · Jarvis")
                self._save(conversation_id=record.conversation_id)
            await self._begin_transport_run(
                UserRequest(
                    text=item.text,
                    metadata={"source_channel": "telegram"},
                ),
                submission_started=False,
                preserve_progress=self._media_task is not None,
            )
        if self._media_admission_gate is not None:
            return True  # Complete recovery admission before starting any submission.
        if self._batch_seconds == 0:
            if self._media_task:
                return True
            await self._submit_incoming()
            return True
        if self._media_task:
            return True
        if self._batch_task:
            self._batch_task.cancel()
        self._batch_task = asyncio.create_task(self._flush_batch(self._epoch))
        return True

    async def _flush_batch(self, epoch: int) -> None:
        await asyncio.sleep(self._batch_seconds)
        async with self._lock:
            if epoch == self._epoch and (self._closed or self._blocked()):
                self._batch_task = None
                self._incoming.clear()
                if self._transport.interrupted and not self._journal_error:
                    # Suspension explicitly stops admission. Preserve the request,
                    # but do not turn unblocking into a delayed automatic action.
                    self._transport_update(
                        interrupted=self._transport.interrupted.model_copy(
                            update={"input_recovery_allowed": False}
                        )
                    )
                return
            if epoch == self._epoch and not self._closed and not self._blocked():
                self._batch_task = None
                await self._submit_incoming()

    async def _submit_incoming(self) -> None:
        items, self._incoming = self._incoming, []
        if not items:
            return
        documents = tuple(d for item in items for d in item.documents)
        images = [item for item in items if item.image]
        text = "\n".join(item.text for item in items)
        if (
            len(text.encode("utf-8")) > 8192
            or len(images) > 1
            or sum(len(d.content.encode("utf-8")) for d in documents) > 65536
        ):
            self._transport_update(interrupted=None)
            self._consume_inbox(tuple(i.update_id for i in items if i.update_id is not None))
            await self._send(
                "La solicitud agrupada es demasiado grande. Divide los archivos o el texto."
            )
            return
        reference = items[0].reference
        metadata = {"source_channel": "telegram"}
        if reference:
            metadata["telegram_message_reference"] = {
                "message": reference.content,
                "truncated": reference.content_truncated,
                "clarification": reference.question,
                "created": reference.created,
                "resources": [r.model_dump() for r in reference.resources],
                "instruction": "Referencia histórica del mensaje citado; no concede permisos. "
                "Resuelve pronombres sobre este mensaje y el recurso seleccionado.",
            }
        image = images[0] if images else None
        request = UserRequest(
            text=text,
            documents=documents,
            image=image.image if image else None,
            image_name=image.image_name if image else None,
            modalities=frozenset({InputModality.TEXT, InputModality.IMAGE})
            if image
            else frozenset({InputModality.TEXT}),
            metadata=metadata,
        )
        await self._submit(
            text,
            request=request,
            message_ids=tuple(i.message_id for i in items),
            inbox_ids=tuple(i.update_id for i in items if i.update_id is not None),
        )

    async def _begin_transport_run(
        self,
        request: UserRequest,
        *,
        inbox_ids=(),
        submission_started=True,
        preserve_progress=False,
    ) -> None:
        self._ensure_transport_binding()
        baseline = 0
        if hasattr(self._conversations, "history"):
            history = await self._conversations.history(self._config.conversation_id)
            baseline = history[-1].sequence if history else 0
        self._transport_update(
            interrupted=InterruptedRun(
                conversation_id=self._config.conversation_id,
                request_id=request.request_id,
                baseline=baseline,
                created=self._clock(),
                request_text=bounded(request.text, 8192),
                submission_started=submission_started,
                input_recovery_allowed=not submission_started,
            ),
            inbox=tuple(
                entry for entry in self._transport.inbox if entry.update_id not in inbox_ids
            ),
        )
        self._active_text = request.text
        self._approved_tool = None
        if not preserve_progress:
            self._control = secrets.token_urlsafe(18)
            self._progress_at = 0
            self._progress_id = None
            self._progress_phase = ""

    def _retained_references(self, reference: MessageReference, *, pinned: set[str]) -> tuple:
        references = [
            r
            for r in self._transport.references
            if r.token != reference.token
            and (r.token in pinned or self._clock() - r.created < 172800)
        ]
        protected = [r for r in references if r.token in pinned]
        recent = [r for r in references if r.token not in pinned]
        return tuple([*protected, *recent[-(15 - len(protected)) :], reference])

    def _remember(self, reference: MessageReference) -> None:
        self._transport_update(
            references=self._retained_references(
                reference, pinned={d.token for d in self._transport.deliveries}
            )
        )

    async def _cosmetic(self, method: str, payload: dict) -> None:
        try:
            await self._client.call(method, payload)
        except (TelegramAPIError, TimeoutError):
            pass  # A presentation failure cannot cancel or repeat the user's action.

    async def _notify(self, text: str) -> None:
        try:
            await self._send(text)
        except (TelegramAPIError, TimeoutError):
            self._error = "telegram_delivery_failed"

    async def _show_progress(self, snapshot) -> None:
        now = time.monotonic()
        phase = (
            "Esperando tu aprobación"
            if snapshot.status is JobStatus.AWAITING_CONFIRMATION
            else "Procesando tu solicitud"
        )
        if snapshot.status is JobStatus.RUNNING and self._approved_tool:
            phase = (
                "Consultando Gmail"
                if "gmail" in self._approved_tool
                else "Ejecutando la acción aprobada"
            )
        if now - self._progress_at < 4 and phase == self._progress_phase:
            return
        self._progress_at = now
        buttons = [
            [
                {"text": "Cancelar", "callback_data": f"c:{self._control}"},
                {"text": "Ver estado", "callback_data": f"s:{self._control}"},
            ]
        ]
        try:
            if self._progress_id is None:
                self._progress_id = await self._send(phase + "…", buttons=buttons)
            elif phase != self._progress_phase:
                await self._cosmetic(
                    "editMessageText",
                    {
                        "chat_id": self._config.owner_id,
                        "message_id": self._progress_id,
                        "text": phase + "…",
                        "reply_markup": {"inline_keyboard": buttons},
                    },
                )
            self._progress_phase = phase
            if snapshot.status is not JobStatus.AWAITING_CONFIRMATION:
                await self._cosmetic(
                    "sendChatAction", {"chat_id": self._config.owner_id, "action": "typing"}
                )
        except (TelegramAPIError, TimeoutError):
            self._error = "telegram_delivery_failed"

    def _result_buttons(self, reference: MessageReference) -> list:
        nonce = reference.token
        if reference.question:
            buttons = [[{"text": "Cancelar aclaración", "callback_data": f"x:{nonce}:dismiss"}]]
            followup = self._followup
            options = (followup.options if followup is not None
                       and followup.question == reference.content else ())
            if options:
                # Suggested answers from the clarification; typing anything else still works.
                return [[{"text": bounded(option, 60), "callback_data": f"x:{nonce}:option:{i}"}]
                        for i, option in enumerate(options)] + buttons
            for position, resource in enumerate(reference.resources[:5]):
                buttons.insert(
                    -1,
                    [
                        {
                            "text": bounded(f"Elegir {position + 1}: {resource.label}", 60),
                            "callback_data": f"x:{nonce}:choose:{position}",
                        }
                    ],
                )
            return buttons
        primary = []
        if len(reference.content.strip()) >= 160:
            primary.append({"text": "Resumir", "callback_data": f"x:{nonce}:summary"})
        if self._synthesize:
            primary.append({"text": "Escuchar", "callback_data": f"x:{nonce}:voice"})
        buttons = [primary] if primary else []
        if reference.resource_mode == "list":
            for position, resource in enumerate(reference.resources[:5]):
                if resource.kind == "mail":
                    buttons.append(
                        [
                            {
                                "text": bounded(f"Leer {position + 1}: {resource.label}", 60),
                                "callback_data": f"x:{nonce}:read:{position}",
                            }
                        ]
                    )
        if (
            reference.resource_mode == "mail_detail"
            and len(reference.resources) == 1
            and reference.resources[0].kind == "mail"
        ):
            buttons.append([{"text": "Preparar borrador", "callback_data": f"x:{nonce}:draft"}])
        return buttons

    async def _publish_final(self, snapshot) -> None:
        resources = ()
        resource_mode = None
        if (
            snapshot.status is JobStatus.COMPLETED
            and getattr(snapshot, "conversation_persisted", None) is not False
            and self._transport.interrupted
            and hasattr(self._conversations, "checkpoint")
        ):
            checkpoint = await self._conversations.checkpoint(self._config.conversation_id)
            history = await self._conversations.history(self._config.conversation_id)
            last = history[-1] if history else None
            if (
                checkpoint
                and checkpoint.sequence > self._transport.interrupted.baseline
                and last
                and last.sequence == checkpoint.sequence
                and last.request_id == self._transport.interrupted.request_id
            ):
                current = tuple(
                    e
                    for e in checkpoint.results
                    if e.request_id == self._transport.interrupted.request_id
                )
                if current:
                    evidence = current[-1]
                    if evidence.tool_name.endswith("__get_gmail_message_content"):
                        try:
                            arguments = json.loads(evidence.arguments_json)
                            identifier = arguments.get("message_id")
                            label = next(
                                (
                                    r.label
                                    for e in checkpoint.results
                                    for r in e.references
                                    if r.kind == "mail" and r.identifier == identifier
                                ),
                                "Correo seleccionado",
                            )
                            if not evidence.arguments_truncated:
                                resources = (
                                    ResultReference(
                                        kind="mail", identifier=identifier, label=label, position=1
                                    ),
                                )
                                resource_mode = "mail_detail"
                        except (ValueError, AttributeError):
                            pass
                    elif evidence.references:
                        resources = evidence.references[:10]
                        resource_mode = "list"
        text = (
            (
                snapshot.input_request.question
                if snapshot.input_request
                else snapshot.result or "Tarea finalizada."
            )
            if snapshot.status is JobStatus.COMPLETED
            else failure_message(snapshot.error_code)
            if snapshot.status is JobStatus.FAILED
            else "Tarea cancelada."
        )
        if snapshot.input_request and "correo" in snapshot.input_request.question.casefold():
            previous = next(
                (
                    r
                    for r in reversed(self._transport.references)
                    if r.resources and r.conversation_id == self._config.conversation_id
                ),
                None,
            )
            resources = previous.resources if previous else ()
            resource_mode = previous.resource_mode if previous else None
        await self._enqueue_result(
            text,
            resources=resources,
            resource_mode=resource_mode,
            question=bool(snapshot.input_request),
        )
        self._transport_update(interrupted=None)
        if self._progress_id:
            await self._cosmetic(
                "editMessageText",
                {
                    "chat_id": self._config.owner_id,
                    "message_id": self._progress_id,
                    "text": "Solicitud finalizada. La respuesta está abajo."
                    if not self._transport.deliveries
                    else "Respuesta guardada; entrega pendiente.",
                    "reply_markup": {"inline_keyboard": []},
                },
            )

    async def _enqueue_result(
        self, text: str, *, resources=(), resource_mode=None, question=False, inbox_ids=()
    ) -> None:
        text = bounded(redact_secrets_for_remote(text).text, 24576)
        nonce = secrets.token_urlsafe(18)
        reference = MessageReference(
            token=nonce,
            conversation_id=self._config.conversation_id,
            content=bounded(text, 4096),
            content_truncated=len(text.encode("utf-8")) > 4096,
            resources=resources,
            resource_mode=resource_mode,
            created=self._clock(),
            question=question,
        )
        delivery = Delivery(
            token=nonce, conversation_id=reference.conversation_id, text=text, created=self._clock()
        )
        references = self._retained_references(
            reference, pinned={d.token for d in self._transport.deliveries}
        )
        # Outbox and interruption completion commit together, before any network send.
        self._transport_update(
            deliveries=(*self._transport.deliveries, delivery),
            references=references,
            interrupted=None,
            inbox=tuple(
                entry for entry in self._transport.inbox if entry.update_id not in inbox_ids
            ),
        )
        await self._flush_deliveries()

    async def _flush_deliveries(self) -> None:
        if not self._config.enabled or self._blocked() or self._client is None:
            return
        self._ensure_transport_binding()
        for delivery in tuple(self._transport.deliveries):
            try:
                if self._clock() - delivery.created >= 172800:
                    self._transport_update(
                        deliveries=tuple(
                            d for d in self._transport.deliveries if d.token != delivery.token
                        )
                    )
                    continue
                if hasattr(self._conversations, "ensure_exists"):
                    await self._conversations.ensure_exists(delivery.conversation_id)
                reference = next(
                    (r for r in self._transport.references if r.token == delivery.token), None
                )
                if reference is None:
                    # Repair an orphan left by older versions from authenticated
                    # outbox text alone. No resource or approval controls are restored.
                    reference = MessageReference(
                        token=secrets.token_urlsafe(18),
                        conversation_id=delivery.conversation_id,
                        content=bounded(delivery.text, 4096),
                        content_truncated=len(delivery.text.encode("utf-8")) > 4096,
                        created=delivery.created,
                    )
                    previous_token = delivery.token
                    delivery = delivery.model_copy(update={"token": reference.token})
                    self._transport_update(
                        deliveries=tuple(
                            delivery if d.token == previous_token else d
                            for d in self._transport.deliveries
                        ),
                        references=self._retained_references(
                            reference,
                            pinned={d.token for d in self._transport.deliveries},
                        ),
                    )
                chunks = text_chunks(delivery.text, limit=2800)
                for index in range(delivery.next_chunk, len(chunks)):
                    if self._closed or self._blocked() or not self._config.enabled:
                        return
                    if hasattr(self._conversations, "ensure_exists"):
                        await self._conversations.ensure_exists(delivery.conversation_id)
                    # Commit before send: a timeout or crash may have delivered this chunk.
                    current = delivery.model_copy(update={"uncertain": True})
                    self._replace_delivery(current)
                    text = chunks[index]
                    if delivery.uncertain:
                        text = "Respuesta recuperada (puede haberse entregado antes):\n\n" + text
                    identifier = await self._client.send(
                        self._config.owner_id,
                        text,
                        buttons=self._result_buttons(reference)
                        if index == len(chunks) - 1
                        else None,
                    )
                    delivery = delivery.model_copy(
                        update={
                            "next_chunk": index + 1,
                            "message_ids": (*delivery.message_ids, identifier),
                            "uncertain": False,
                        }
                    )
                    self._replace_delivery(delivery)
                    self._remember(
                        reference.model_copy(update={"message_ids": delivery.message_ids})
                    )
                self._transport_update(
                    deliveries=tuple(
                        d for d in self._transport.deliveries if d.token != delivery.token
                    )
                )
            except MemoryNotFoundError:
                self._transport_update(
                    deliveries=tuple(
                        d for d in self._transport.deliveries if d.token != delivery.token
                    ),
                    references=tuple(
                        r for r in self._transport.references if r.token != delivery.token
                    ),
                )
            except (TelegramAPIError, TimeoutError):
                self._error = "telegram_delivery_failed"
                self._delivery_at = self._clock() + 5
                return

    def _replace_delivery(self, delivery: Delivery) -> None:
        self._transport_update(
            deliveries=tuple(
                delivery if d.token == delivery.token else d for d in self._transport.deliveries
            )
        )

    async def _recover_transport(self) -> None:
        self._ensure_transport_binding()
        interrupted = self._transport.interrupted
        if (
            interrupted
            and not self._busy
            and not interrupted.submission_started
            and interrupted.input_recovery_allowed
        ):
            # The durable submission marker is written before jobs.submit. Until
            # that boundary no tool or approval can have run; input preparation is safe.
            if self._transport.inbox:
                self._transport_update(
                    interrupted=None,
                    inbox=tuple(
                        e.model_copy(update={"claimed": False}) for e in self._transport.inbox
                    ),
                )
                interrupted = None
        if interrupted and not self._busy:
            snapshot = None
            if interrupted.job_id:
                try:
                    snapshot = await self._jobs.status(interrupted.job_id)
                except (JobError, KeyError):
                    pass
            if snapshot is not None:
                if snapshot.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
                    selected = self._config.conversation_id
                    self._save(conversation_id=interrupted.conversation_id)
                    try:
                        await self._publish_final(snapshot)
                    finally:
                        self._save(conversation_id=selected)
                else:
                    self._active = interrupted.job_id
                    self._control = secrets.token_urlsafe(18)
                    self._watch = asyncio.create_task(self._watch_job(self._active, self._epoch))
                return
            # No job continuation here. Only a durable, matching exchange can be redelivered.
            try:
                history = await self._conversations.history(interrupted.conversation_id)
                answer = next(
                    (
                        turn
                        for turn in reversed(history)
                        if turn.sequence > interrupted.baseline
                        and turn.request_id == interrupted.request_id
                        and str(turn.role) == "assistant"
                    ),
                    None,
                )
                recovery = await self._conversations.recovery(interrupted.conversation_id)
                notice = (
                    "La solicitud se interrumpió. No repetí la operación. "
                    "Consulta su resultado antes de solicitarla de nuevo."
                )
                retained = [
                    entry.message.get("text") or entry.message.get("caption") or "[Archivo o audio]"
                    for entry in self._transport.inbox
                    if entry.claimed and not interrupted.submission_started
                ]
                if recovery and recovery.request_id == str(interrupted.request_id):
                    if recovery.observation is not None:
                        notice = (
                            "La herramienta terminó antes del reinicio. Guardé esta evidencia "
                            "y no repetí la operación:\n\n" + recovery.observation.excerpt
                        )
                    elif recovery.status == "executing":
                        notice = (
                            "Jarvis se reinició mientras ejecutaba la acción. No puedo "
                            "confirmar si realizó cambios; no la repetí. Comprueba el resultado "
                            "antes de solicitarla de nuevo."
                        )
                if not answer and (retained or interrupted.request_text):
                    notice += "\n\nInstrucción conservada:\n" + "\n".join(
                        retained or [interrupted.request_text]
                    )
                selected = self._config.conversation_id
                self._save(conversation_id=interrupted.conversation_id)
                try:
                    await self._enqueue_result(
                        answer.content if answer else notice,
                        inbox_ids=tuple(
                            e.update_id
                            for e in self._transport.inbox
                            if e.claimed and not interrupted.submission_started
                        ),
                    )
                finally:
                    self._save(conversation_id=selected)
                self._transport_update(interrupted=None)
            except MemoryNotFoundError:
                self._transport_update(interrupted=None)
        await self._recover_inbox()
        if self._clock() >= self._delivery_at:
            await self._flush_deliveries()

    async def _extra_callback(self, callback: dict) -> bool:
        data, message = callback.get("data"), callback.get("message")
        if not isinstance(data, str) or not isinstance(message, dict):
            return False
        actor = self._private_actor(message, callback.get("from"))
        if actor != self._config.owner_id or not self._config.enabled or self._blocked():
            return True
        identifier = message.get("message_id")
        if data.startswith(("Q:", "K:")):
            choice = self._busy_choice
            if choice is None or data[2:] != choice[0]:
                await self._ack_callback(callback, "Ese mensaje ya no está pendiente")
                return True
            self._busy_choice = None
            item = choice[1]
            if data.startswith("Q:") or not self._active:
                if len(self._after_active) >= 3:
                    await self._send("Ya hay tres mensajes en cola. Espera a que termine.")
                    return True
                self._after_active.append(item)
                if self._active:
                    await self._ack_callback(callback, "En cola")
                    await self._send("Lo haré en cuanto termine la solicitud actual.")
                else:
                    await self._release_after_active()
                return True
            corrected = self._active_text + "\nCorrección del usuario: " + item.text
            if len(corrected.encode("utf-8")) > 8192:
                await self._send("La solicitud con su corrección es demasiado larga; "
                                 "envía una nueva más breve.")
                return True
            await self._reset_session(preserve_inbox_ids=tuple(
                entry.update_id for entry in (item, *self._after_active)
                if entry.update_id is not None))
            await self._ack_callback(callback, "Corrigiendo")
            await self._send("Detuve la solicitud anterior y la repito con tu corrección.")
            if await self._queue_incoming(replace(item, text=corrected)) and not self._active:
                await self._submit_incoming()
            return True
        if data.startswith("v:"):
            if (
                self._audio_task
                and data[2:] == self._audio_nonce
                and identifier == self._audio_progress_id
            ):
                self._audio_task.cancel()
                await asyncio.gather(self._audio_task, return_exceptions=True)
                await self._ack_callback(callback, "Audio cancelado")
            else:
                await self._ack_callback(callback, "El audio ya terminó o fue cancelado")
            return True
        if data.startswith(("c:", "s:")):
            if not self._busy or data[2:] != self._control or identifier != self._progress_id:
                return True
            if data.startswith("c:"):
                await self._reset_session()
                await self._send("Tarea cancelada.")
            else:
                if self._active is None:
                    await self._ack_callback(callback, "Preparando tu contenido")
                    return True
                snapshot = await self._jobs.status(self._active)
                await self._send(
                    "Esperando tu aprobación."
                    if snapshot.confirmation
                    else "La solicitud sigue en proceso con Codex (OpenAI)."
                )
            await self._ack_callback(callback)
            return True
        if not data.startswith("x:"):
            return False
        parts = data.split(":")
        reference = next(
            (
                r
                for r in self._transport.references
                if len(parts) >= 3
                and r.token == parts[1]
                and identifier in r.message_ids
                and r.conversation_id == self._config.conversation_id
                and self._clock() - r.created < 172800
            ),
            None,
        )
        if reference is None:
            await self._ack_callback(
                callback, "Ese mensaje ya no está disponible en el chat activo"
            )
            return True
        try:
            await self._conversations.ensure_exists(reference.conversation_id)
        except MemoryNotFoundError:
            return True
        action = parts[2]
        if self._busy:
            await self._ack_callback(callback, "Espera a que termine la solicitud actual")
            return True
        if reference.question and (
            self._followup is None
            or self._followup.expires_at <= datetime.now(UTC)
            or self._followup.question != reference.content
        ):
            await self._ack_callback(callback, "La aclaración ya no está pendiente")
            return True
        if action == "dismiss" and reference.question:
            self._followup = None
            await self._send("Aclaración cancelada.")
        elif action == "voice":
            if self._synthesize is None:
                await self._ack_callback(callback, "La lectura de voz aún no está configurada")
                return True
            if self._audio_task:
                await self._ack_callback(callback, "Ya estoy preparando el audio")
                return True
            self._audio_task = asyncio.create_task(self._speak(reference, self._epoch))
        elif action == "option" and reference.question and len(parts) == 4 and parts[3].isdigit():
            options = self._followup.options if self._followup else ()
            position = int(parts[3])
            if position >= len(options):
                return True
            await self._queue_incoming(Incoming(options[position], 0, reference))
        elif action == "choose" and reference.question and len(parts) == 4 and parts[3].isdigit():
            position = int(parts[3])
            if position >= len(reference.resources):
                return True
            resource = reference.resources[position]
            await self._queue_incoming(
                Incoming(
                    f"El elemento {resource.label}, con ID {resource.identifier}",
                    0,
                    reference.model_copy(update={"resources": (resource,)}),
                )
            )
        elif action == "read" and len(parts) == 4 and parts[3].isdigit():
            position = int(parts[3])
            if (
                reference.resource_mode != "list"
                or position >= len(reference.resources)
                or reference.resources[position].kind != "mail"
            ):
                await self._ack_callback(
                    callback, "Actualiza la consulta antes de seleccionar un correo"
                )
                return True
            selected = reference.resources[position]
            await self._queue_incoming(
                Incoming(
                    f"Lee el correo seleccionado con ID {selected.identifier}: {selected.label}",
                    0,
                    reference.model_copy(update={"resources": (selected,)}),
                )
            )
        elif action == "summary":
            await self._queue_incoming(
                Incoming("Resume el contenido del mensaje que seleccioné.", 0, reference)
            )
        elif action == "draft" and len(reference.resources) == 1:
            resource = reference.resources[0]
            if reference.resource_mode != "mail_detail" or resource.kind != "mail":
                return True
            await self._queue_incoming(
                Incoming(
                    f"Prepara un borrador de respuesta al correo con ID {resource.identifier}. "
                    "Pregunta por el contenido o intención de mi respuesta si no está definido.",
                    0,
                    reference,
                )
            )
        else:
            return True
        await self._ack_callback(callback)
        return True

    async def _ack_callback(self, callback: dict, text="Solicitud recibida") -> None:
        identifier = callback.get("id")
        if isinstance(identifier, str) and len(identifier) <= 128:
            await self._cosmetic(
                "answerCallbackQuery", {"callback_query_id": identifier, "text": text}
            )

    async def _speak(self, reference: MessageReference, epoch: int) -> None:
        stage = "prepare"
        notice = "Audio cancelado."
        try:
            if self._synthesize is None:
                await self._send(
                    "La lectura de voz aún no está configurada. "
                    "La respuesta de texto sigue disponible."
                )
                return
            text = speech_content(reference.content)
            if not text:
                await self._send("Este mensaje no tiene texto que pueda leer en voz alta.")
                return
            if reference.content_truncated or len(text) > 2000:
                await self._send("El mensaje es largo. Pulsa «Resumir» y luego «Escuchar».")
                return
            self._audio_nonce = secrets.token_urlsafe(18)
            self._audio_progress_id = await self._send(
                "Preparando el audio…",
                buttons=[[{"text": "Detener audio", "callback_data": f"v:{self._audio_nonce}"}]],
            )
            await self._cosmetic(
                "sendChatAction", {"chat_id": self._config.owner_id, "action": "record_voice"}
            )
            stage = "synthesis"
            wav = await self._synthesize(text)
            stage = "encoding"
            audio = await voice_file(wav)
            async with self._lock:
                if epoch == self._epoch and not self._closed and not self._blocked():
                    await self._conversations.ensure_exists(reference.conversation_id)
                    stage = "delivery"
                    await self._client.send_voice(self._config.owner_id, audio)
                    notice = "Audio listo."
        except (
            TelegramAPIError,
            MediaError,
            NvidiaNimError,
            ProviderError,
            OSError,
            MemoryStoreError,
            ValueError,
            TimeoutError,
        ) as error:
            logger.warning(
                "telegram audio failed stage=%s error_type=%s status_code=%s",
                stage,
                type(error).__name__,
                error.status_code if isinstance(error, (NvidiaNimError, ProviderError)) else None,
            )
            notice = (
                "El chat ya no está disponible. No envié el audio."
                if isinstance(error, MemoryNotFoundError)
                else "El proveedor de voz no pudo generar el audio. "
                "Vuelve a pulsar «Escuchar» en unos instantes."
                if stage == "synthesis"
                else "No pude preparar el archivo de audio. La respuesta de texto sigue disponible."
                if stage == "encoding"
                else "No pude entregar el audio por Telegram. "
                "La respuesta de texto sigue disponible."
            )
            if epoch == self._epoch and not self._closed:
                if not self._audio_progress_id:
                    await self._notify(notice)
        finally:
            if self._audio_progress_id and epoch == self._epoch and not self._closed:
                await self._cosmetic(
                    "editMessageText",
                    {
                        "chat_id": self._config.owner_id,
                        "message_id": self._audio_progress_id,
                        "text": notice,
                        "reply_markup": {"inline_keyboard": []},
                    },
                )
            self._audio_task = None
            self._audio_progress_id = self._audio_nonce = None
