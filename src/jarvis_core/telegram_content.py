"""Own bounded Telegram address/attachment parsing; no transport or implicit read.

The reviewed route is the policy. Telegram entities are offsets in UTF-16 code
units, not Python character offsets. Metadata and filenames remain external data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from jarvis_core.telegram import TelegramError, _integer, _text

MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class TelegramAttachment:
    file_id: str = field(repr=False)
    unique_id: str = field(repr=False)
    name: str
    mime_type: str | None
    file_size: int | None
    kind: str

    def __post_init__(self):
        for value in (self.file_id, self.unique_id):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= 512
                or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None
            ):
                raise TelegramError("telegram_attachment_invalid")
        _text(self.name, 256)
        if (
            not self.name.strip()
            or any(ord(c) < 32 for c in self.name)
            or any(c in self.name for c in ("/", "\\", "\0"))
            or self.name in {".", ".."}
            or not isinstance(self.kind, str)
            or self.kind not in {"photo", "document", "audio", "voice", "video", "animation"}
        ):
            raise TelegramError("telegram_attachment_invalid")
        if self.mime_type is not None and (
            not isinstance(self.mime_type, str)
            or re.fullmatch(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+", self.mime_type) is None
            or len(self.mime_type) > 128
        ):
            raise TelegramError("telegram_attachment_invalid")
        if self.file_size is not None:
            _integer(self.file_size, minimum=1, maximum=MAX_ATTACHMENT_BYTES)


def _utf16_slice(text, offset, length):
    if type(offset) is not int or type(length) is not int or offset < 0 or length < 1:
        return None
    raw = text.encode("utf-16-le")
    if offset + length > len(raw) // 2:
        return None
    try:
        return raw[offset * 2 : (offset + length) * 2].decode("utf-16-le")
    except UnicodeError:
        return None


def addressed_to_bot(message, text, identity):
    reply = message.get("reply_to_message")
    if isinstance(reply, dict):
        sender = reply.get("from")
        if (
            isinstance(sender, dict)
            and sender.get("is_bot") is True
            and type(sender.get("id")) is int
            and sender["id"] == identity.bot_id
            and not reply.get("sender_chat")
            and (
                not isinstance(reply.get("chat"), dict)
                or reply["chat"].get("id") == message["chat"]["id"]
            )
        ):
            return True
    if identity.username is None:
        return False
    entities = message.get("entities" if "text" in message else "caption_entities", [])
    if not isinstance(entities, list) or len(entities) > 100:
        return False
    target = "@" + identity.username.lower()
    for entity in entities:
        if not isinstance(entity, dict) or entity.get("type") not in {"mention", "bot_command"}:
            continue
        span = _utf16_slice(text, entity.get("offset"), entity.get("length"))
        if span is None:
            continue
        if entity["type"] == "mention" and span.lower() == target:
            return True
        if (
            entity["type"] == "bot_command"
            and span.startswith("/")
            and span.lower().endswith(target)
            and span.count("@") == 1
        ):
            return True
    return False


def attachments_from_message(message):
    kinds = [
        key
        for key in ("photo", "document", "audio", "voice", "video", "animation")
        if key in message
    ]
    # Telegram may expose animation together with its document representation.
    if kinds == ["document", "animation"]:
        kinds = ["animation"]
    if not kinds:
        return ()
    if len(kinds) != 1:
        raise TelegramError("telegram_attachment_invalid")
    kind = kinds[0]
    row = message[kind]
    if kind == "photo":
        if (
            not isinstance(row, list)
            or not 1 <= len(row) <= 16
            or any(not isinstance(v, dict) for v in row)
        ):
            raise TelegramError("telegram_attachment_invalid")
        # Prefer the largest eligible photo. Unknown sizes remain bounded by
        # getFile and the streaming body quota before any bytes enter the model.
        eligible = [
            v
            for v in row
            if v.get("file_size") is None
            or (type(v.get("file_size")) is int and 0 < v["file_size"] <= MAX_ATTACHMENT_BYTES)
        ]
        if not eligible:
            raise TelegramError("telegram_attachment_limit")
        row = eligible[-1]
    if not isinstance(row, dict):
        raise TelegramError("telegram_attachment_invalid")
    defaults = {
        "photo": ("telegram-photo.jpg", "image/jpeg"),
        "voice": ("telegram-voice.ogg", "audio/ogg"),
        "video": ("telegram-video.mp4", "video/mp4"),
        "animation": ("telegram-animation.mp4", "video/mp4"),
        "audio": ("telegram-audio", None),
        "document": ("telegram-document", None),
    }
    default_name, default_mime = defaults[kind]
    if kind in {"voice", "audio", "video", "animation"} and "duration" in row:
        _integer(row["duration"], maximum=300)
    return (
        TelegramAttachment(
            row.get("file_id"),
            row.get("file_unique_id"),
            row.get("file_name", default_name),
            row.get("mime_type", default_mime),
            row.get("file_size"),
            kind,
        ),
    )


def message_content(message, route, identity):
    chat, sender = message.get("chat"), message.get("from")
    if (
        not isinstance(chat, dict)
        or not isinstance(sender, dict)
        or chat.get("type") != route.chat_type
        or sender.get("is_bot") is not False
        or type(sender.get("id")) is not int
        or not 1 <= sender["id"] <= (1 << 52) - 1
        or sender["id"] == identity.bot_id
        or message.get("sender_chat") is not None
    ):
        raise TelegramError("telegram_sender_ignored")
    if route.chat_type == "private":
        if sender["id"] != route.chat_id:
            raise TelegramError("telegram_sender_ignored")
    elif sender["id"] not in route.allowed_sender_ids:
        raise TelegramError("telegram_sender_ignored")
    thread = message.get("message_thread_id")
    if (thread != route.message_thread_id
            or (thread is not None and (type(thread) is not int or not 1 <= thread < (1 << 31)))):
        raise TelegramError("telegram_thread_ignored")
    text = message.get("text", message.get("caption", ""))
    _text(text, 8000, empty=True)
    if route.chat_type != "private" and not addressed_to_bot(message, text, identity):
        raise TelegramError("telegram_mention_required")
    attachments = attachments_from_message(message)
    if attachments and not route.allow_attachments:
        raise TelegramError("telegram_attachment_not_authorized")
    if not text.strip() and not attachments:
        raise TelegramError("telegram_text_unsupported")
    return text or "Archivo adjunto recibido desde la ruta de Telegram autorizada.", attachments
