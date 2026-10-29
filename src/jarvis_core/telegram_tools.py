"""Approved attachment replies bound to one live Telegram message and run.

There is no destination argument, arbitrary file path, upload retry or inherited
permission. Telegram's response is an external API acknowledgment, not an
independently verified delivery to the human recipient.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Mapping

from jarvis_core.media_documents import MediaError
from jarvis_core.runtime import ToolDefinition, ToolResult
from jarvis_core.telegram import TelegramError
from jarvis_core.telegram_content import MAX_ATTACHMENT_BYTES
from jarvis_core.tools import (
    ToolCancelled,
    ToolError,
    ToolSpec,
    check_tool_authority,
    current_tool_operation,
)

NAME = "telegram_send_attachment"


class TelegramReplyTools:
    def __init__(self, service):
        self.service = service

    @staticmethod
    def validate(arguments):
        if (
            not isinstance(arguments, Mapping)
            or set(arguments) - {"attachment_id", "sha256", "caption", "delivery"}
            or {"attachment_id", "sha256"} - set(arguments)
            or not isinstance(arguments["attachment_id"], str)
            or not re.fullmatch(r"[a-f0-9]{32}", arguments["attachment_id"])
            or not isinstance(arguments["sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", arguments["sha256"])
        ):
            raise ToolError("telegram_attachment_arguments_invalid")
        if (
            not isinstance(arguments.get("delivery", "document"), str)
            or arguments.get("delivery", "document") not in {"document", "voice"}
        ):
            raise ToolError("telegram_attachment_delivery_invalid")
        caption = arguments.get("caption", "")
        try:
            if (
                not isinstance(caption, str)
                or "\0" in caption
                or len(caption.encode()) > 4096
                or len(caption.encode("utf-16-le")) // 2 > 1024
            ):
                raise ValueError
        except (ValueError, UnicodeError):
            raise ToolError("telegram_attachment_caption_invalid") from None

    def definitions(self):
        return (ToolDefinition(NAME, self.validate, self.execute, requires_confirmation=True),)

    def specs(self):
        return (
            ToolSpec(
                NAME,
                "Envía un adjunto de esta conversación al mensaje de Telegram "
                "que originó la ejecución. No elige otro destino. Requiere aprobación nueva "
                "del dueño y el SHA-256 exacto; hasta 5 MiB. El recibo de Telegram es externo, "
                "sin verificación independiente. delivery=document envía un archivo y es "
                "el modo predeterminado; delivery=voice envía sólo Ogg OPUS como nota de voz, "
                "hasta 120 segundos, sin grabar ni modificar el audio.",
                {
                    "type": "object",
                    "properties": {
                        "attachment_id": {"type": "string", "pattern": "^[a-f0-9]{32}$"},
                        "sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                        "caption": {"type": "string", "maxLength": 1024},
                        "delivery": {"type": "string", "enum": ["document", "voice"]},
                    },
                    "required": ["attachment_id", "sha256"],
                    "additionalProperties": False,
                },
            ),
        )

    async def execute(self, arguments, cancellation):
        self.validate(arguments)
        check_tool_authority(NAME, arguments)
        operation = current_tool_operation()
        context = self.service._telegram_run_context.get(operation.run_id)
        if context is None or context["ref"].session_id != operation.session_id:
            return ToolResult(NAME, False, False, error="telegram_route_unavailable")
        channel, incoming, ref = context["channel"], context["incoming"], context["ref"]
        dispatched = False

        def guard():
            if cancellation.is_set():
                raise asyncio.CancelledError
            context["guard"]()
            self.service._assert_run_client(operation.run_id)

        try:
            guard()
            store = self.service._media_store()
            scope = self.service._media_scope(ref)
            source = store.read(
                scope,
                arguments["attachment_id"],
                arguments["sha256"],
                guard=guard,
                cancellation=cancellation,
            )
            payload, manifest = source.payload, source.manifest
            if not 1 <= len(payload) <= MAX_ATTACHMENT_BYTES:
                return ToolResult(NAME, False, False, error="telegram_attachment_limit")
            delivery = arguments.get("delivery", "document")
            if delivery == "voice" and (manifest.kind != "audio" or manifest.mime != "audio/ogg"):
                return ToolResult(NAME, False, False, error="telegram_voice_format_invalid")

            def before_dispatch():
                nonlocal dispatched
                guard()
                fresh = store.read(
                    scope,
                    arguments["attachment_id"],
                    arguments["sha256"],
                    guard=guard,
                    cancellation=cancellation,
                )
                if (
                    fresh.manifest != manifest
                    or hashlib.sha256(fresh.payload).hexdigest() != arguments["sha256"]
                ):
                    raise TelegramError("telegram_attachment_identity_changed")
                check_tool_authority(NAME, arguments, commit=True)
                dispatched = True

            send = (
                channel.client.send_voice if delivery == "voice" else channel.client.send_document
            )
            preparation = {"preparation_guard": guard} if delivery == "voice" else {}
            sent = await send(
                incoming,
                payload,
                manifest.name,
                manifest.mime,
                arguments.get("caption", ""),
                before_dispatch=before_dispatch,
                **preparation,
            )
            value = {
                "delivery": "telegram_api_ack",
                "independently_verified": False,
                "destination": "current_telegram_message",
                "message_id": sent,
                "attachment_id": manifest.attachment_id,
                "sha256": manifest.sha256,
                "byte_length": manifest.byte_length,
                "delivery_mode": delivery,
            }
            return ToolResult(NAME, True, False, output=json.dumps(value, sort_keys=True))
        except asyncio.CancelledError as error:
            unknown = dispatched or getattr(error, "effect_status", "known") == "unknown"
            if unknown:
                self._pause_uncertain(channel)
            raise ToolCancelled(
                ToolResult(
                    NAME,
                    False,
                    False,
                    error="telegram_attachment_cancelled",
                    effect_status="unknown" if unknown else "known",
                )
            ) from None
        except (TelegramError, MediaError, ToolError) as error:
            unknown = getattr(error, "effect_status", "known") == "unknown"
            if unknown:
                self._pause_uncertain(channel)
            code = str(error)
            if re.fullmatch(r"[a-z][a-z0-9_]{0,127}", code) is None:
                code = "telegram_attachment_delivery_unconfirmed"
            return ToolResult(
                NAME, False, False, error=code, effect_status="unknown" if unknown else "known"
            )
        except Exception:
            if dispatched:
                self._pause_uncertain(channel)
            return ToolResult(
                NAME,
                False,
                False,
                error="telegram_attachment_delivery_unconfirmed",
                effect_status="unknown" if dispatched else "known",
            )

    @staticmethod
    def _pause_uncertain(channel):
        channel.error_code = "telegram_attachment_delivery_unconfirmed"
        channel._stop.set()
        channel._send_tickets.clear()
