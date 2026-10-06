from __future__ import annotations

import html
import json
import logging
import re
from urllib.parse import quote

import httpx

TOKEN_PATTERN = re.compile(r"[0-9]{5,20}:[A-Za-z0-9_-]{30,100}")


class TelegramAPIError(RuntimeError):
    def __init__(self, code: str, retry_after: int = 5) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after = min(max(retry_after, 1), 60)


class TelegramTokenLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = TOKEN_PATTERN.sub("[telegram-token]", record.getMessage())
        record.args = ()
        return True


def validate_token(token: str) -> None:
    if not isinstance(token, str) or TOKEN_PATTERN.fullmatch(token) is None:
        raise ValueError("Invalid Telegram credential")


def text_chunks(text: str, limit: int = 3_000) -> list[str]:
    # Telegram entities count UTF-16 code units. Keep astral emoji within the ceiling.
    chunks, current, units = [], [], 0
    for character in text[:32_768]:
        length = 2 if ord(character) > 0xFFFF else 1
        if units + length > limit:
            chunks.append("".join(current))
            current, units = [], 0
        current.append(character)
        units += length
    if current:
        chunks.append("".join(current))
    return chunks or ["No recibí una respuesta completa."]


def telegram_html(text: str) -> str:
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", escaped)
    return re.sub(r"(?m)^#{1,6} (.+)$", r"<b>\1</b>", escaped)


class TelegramClient:
    def __init__(self, token: str, *, transport=None) -> None:
        validate_token(token)
        for name in (
            "httpx",
            "httpcore",
            "httpcore.connection",
            "httpcore.http11",
            "httpcore.http2",
            "httpcore.proxy",
        ):
            logger = logging.getLogger(name)
            if not any(isinstance(item, TelegramTokenLogFilter) for item in logger.filters):
                logger.addFilter(TelegramTokenLogFilter())
        self._base = f"https://api.telegram.org/bot{token}/"
        self._http = httpx.AsyncClient(
            timeout=35,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def call(self, method: str, payload: dict | None = None):
        if method not in {
            "getMe",
            "getWebhookInfo",
            "getUpdates",
            "sendMessage",
            "answerCallbackQuery",
            "editMessageReplyMarkup",
            "editMessageText",
            "sendChatAction",
            "getFile",
        }:
            raise ValueError("Unsupported Telegram API method")
        try:
            async with self._http.stream(
                "POST",
                self._base + method,
                json=payload or {},
                timeout=35 if method == "getUpdates" else 10,
            ) as response:
                data = bytearray()
                async for part in response.aiter_bytes():
                    data.extend(part)
                    if len(data) > 1_048_576:
                        raise TelegramAPIError("telegram_response_invalid")
                if response.status_code == 401:
                    raise TelegramAPIError("telegram_credential_invalid", 60)
                if response.status_code == 409:
                    raise TelegramAPIError("telegram_poll_conflict", 30)
                value = json.loads(data)
                if not isinstance(value, dict):
                    raise TelegramAPIError("telegram_response_invalid")
                if response.status_code == 429:
                    parameters = value.get("parameters")
                    delay = parameters.get("retry_after", 5) if isinstance(parameters, dict) else 5
                    raise TelegramAPIError(
                        "telegram_rate_limited", delay if type(delay) is int else 5
                    )
                if (
                    response.status_code != 200
                    or not isinstance(value, dict)
                    or value.get("ok") is not True
                ):
                    raise TelegramAPIError("telegram_request_failed")
                return value["result"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise TelegramAPIError("telegram_unavailable") from None

    async def send(self, chat_id: int, text: str, *, buttons: list | None = None) -> int:
        chunks = text_chunks(text)
        message_id = 0
        for index, chunk in enumerate(chunks):
            payload = {
                "chat_id": chat_id,
                "text": telegram_html(chunk),
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            }
            if buttons is not None and index == len(chunks) - 1:
                payload["reply_markup"] = {"inline_keyboard": buttons}
            message = await self.call("sendMessage", payload)
            if not isinstance(message, dict):
                raise TelegramAPIError("telegram_response_invalid")
            message_id = message.get("message_id")
            if type(message_id) is not int or message_id <= 0:
                raise TelegramAPIError("telegram_response_invalid")
        return message_id

    async def close(self) -> None:
        await self._http.aclose()

    async def download(self, file_id: str, *, limit: int = 10_000_000) -> bytes:
        if not isinstance(file_id, str) or not 1 <= len(file_id) <= 512:
            raise TelegramAPIError("telegram_file_invalid")
        value = await self.call("getFile", {"file_id": file_id})
        if not isinstance(value, dict):
            raise TelegramAPIError("telegram_file_invalid")
        path, size = value.get("file_path"), value.get("file_size")
        if (
            not isinstance(path, str)
            or len(path) > 512
            or not re.fullmatch(r"[A-Za-z0-9_./-]+", path)
            or path.startswith("/")
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or (size is not None and (type(size) is not int or not 0 < size <= limit))
        ):
            raise TelegramAPIError("telegram_file_invalid")
        url = self._base.replace("api.telegram.org/bot", "api.telegram.org/file/bot")
        try:
            async with self._http.stream(
                "GET", url + quote(path, safe="/"), timeout=25
            ) as response:
                if response.status_code != 200:
                    raise TelegramAPIError("telegram_download_failed")
                data = bytearray()
                async for part in response.aiter_bytes():
                    data.extend(part)
                    if len(data) > limit:
                        raise TelegramAPIError("telegram_file_invalid")
                if not data or (size is not None and len(data) != size):
                    raise TelegramAPIError("telegram_file_invalid")
                return bytes(data)
        except httpx.HTTPError:
            raise TelegramAPIError("telegram_download_failed") from None

    async def send_voice(self, chat_id: int, audio: bytes) -> int:
        if not 1 <= len(audio) <= 8_388_608:
            raise ValueError("voice exceeds transport limit")
        try:
            response = await self._http.post(
                self._base + "sendVoice",
                data={"chat_id": str(chat_id)},
                files={"voice": ("jarvis.m4a", audio, "audio/mp4")},
                timeout=25,
            )
            if response.status_code != 200 or len(response.content) > 65536:
                raise TelegramAPIError("telegram_voice_delivery_failed")
            value = response.json()
            identifier = value.get("result", {}).get("message_id")
            if value.get("ok") is not True or type(identifier) is not int or identifier <= 0:
                raise TelegramAPIError("telegram_response_invalid")
            return identifier
        except (httpx.HTTPError, ValueError, AttributeError):
            raise TelegramAPIError("telegram_voice_delivery_failed") from None

    async def send_generated_media(self, chat_id: int, artifact, data: bytes) -> int:
        from aegis_core.generated_media import MAX_MEDIA_BYTES, GeneratedMedia

        artifact = GeneratedMedia.model_validate(artifact)
        if not 12 <= len(data) <= MAX_MEDIA_BYTES or len(data) != artifact.byte_count:
            raise ValueError("invalid generated media upload")
        try:
            # Documents preserve the original JPEG/MP4 and its provenance watermark.
            # Cosmos VP9-in-MP4 is not universally supported by inline video players.
            async with self._http.stream("POST", self._base + "sendDocument",
                    data={"chat_id": str(chat_id), "caption": "Generado con NVIDIA Cosmos3 Nano"},
                    files={"document": (artifact.file_name, data, artifact.media_type)},
                    timeout=60) as response:
                body = bytearray()
                async for part in response.aiter_bytes():
                    body.extend(part)
                    if len(body) > 65536:
                        raise TelegramAPIError("telegram_response_invalid")
                value = json.loads(body)
                if response.status_code == 429:
                    raise TelegramAPIError("telegram_rate_limited")
                identifier = value.get("result", {}).get("message_id")
                if (response.status_code != 200 or value.get("ok") is not True
                        or type(identifier) is not int or identifier <= 0):
                    raise TelegramAPIError("telegram_media_delivery_failed")
                return identifier
        except (httpx.HTTPError, ValueError, AttributeError):
            raise TelegramAPIError("telegram_media_delivery_failed") from None
