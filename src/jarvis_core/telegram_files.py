"""Bounded Telegram attachment transports to its one verified API host.

There are no arbitrary URLs, local Bot API paths, redirects, proxies or
credential-bearing result strings. The caller revalidates its reviewed route
before both getFile and this independent GET request.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import ssl

from jarvis_core.telegram import _TOKEN, TelegramCancelled, TelegramError, TelegramHTTPTransport
from jarvis_core.telegram_content import MAX_ATTACHMENT_BYTES


def validate_file_path(value):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 512
        or value.startswith("/")
        or len(value.split("/")) > 8
        or any(
            part in {"", ".", ".."} or re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", part) is None
            for part in value.split("/")
        )
    ):
        raise TelegramError("telegram_attachment_path_invalid")
    return value


class TelegramFileTransport:
    def __init__(self, *, connector=None):
        self._connector = connector or asyncio.open_connection

    async def __call__(self, path, token, timeout, before_dispatch):
        validate_file_path(path)
        if (
            not isinstance(token, str)
            or not _TOKEN.fullmatch(token)
            or not callable(before_dispatch)
        ):
            raise TelegramError("telegram_attachment_not_authorized")
        writer = None
        try:
            async with asyncio.timeout(timeout):
                reader, writer = await self._connector(
                    "api.telegram.org",
                    443,
                    ssl=ssl.create_default_context(),
                    server_hostname="api.telegram.org",
                    ssl_shutdown_timeout=1,
                )
                before_dispatch()
                # The sensitive path exists only in the write buffer; never
                # return it, record it, or incorporate remote exception text.
                writer.write(
                    (
                        f"GET /file/bot{token}/{path} HTTP/1.1\r\n"
                        "Host: api.telegram.org\r\nConnection: close\r\n"
                        "Accept: application/octet-stream\r\nAccept-Encoding: identity\r\n\r\n"
                    ).encode("ascii")
                )
                await writer.drain()
                return await TelegramHTTPTransport()._read_response(
                    reader, maximum_bytes=MAX_ATTACHMENT_BYTES
                )
        except TelegramError:
            raise
        except asyncio.CancelledError:
            raise TelegramCancelled("known") from None
        except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError):
            raise TelegramError("telegram_attachment_transport_failed") from None
        finally:
            if writer is not None:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 1)
                except asyncio.CancelledError:
                    TelegramHTTPTransport._abort(writer)
                    raise TelegramCancelled("known") from None
                except (OSError, TimeoutError):
                    TelegramHTTPTransport._abort(writer)


class TelegramDocumentTransport:
    """One multipart effect, with unknown outcome after any lost write ACK."""

    def __init__(self, *, connector=None):
        self._connector = connector or asyncio.open_connection

    async def __call__(
        self, incoming, payload, name, mime, caption, token, timeout, before_dispatch
    ):
        if (
            not isinstance(token, str)
            or not _TOKEN.fullmatch(token)
            or not callable(before_dispatch)
        ):
            raise TelegramError("telegram_attachment_not_authorized")
        boundary = "jarvis_" + secrets.token_hex(24)
        if boundary.encode() in payload:
            raise TelegramError("telegram_attachment_invalid")
        fields = {
            "chat_id": str(incoming.chat_id),
            "reply_parameters": json.dumps(
                {"message_id": incoming.message_id, "allow_sending_without_reply": False},
                separators=(",", ":"),
            ),
            "disable_content_type_detection": "true",
            "allow_paid_broadcast": "false",
        }
        if incoming.message_thread_id is not None:
            fields["message_thread_id"] = str(incoming.message_thread_id)
        if caption:
            fields["caption"] = caption
        pieces = []
        for key, value in fields.items():
            pieces.append(
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n'
                    + value
                    + "\r\n"
                ).encode()
            )
        pieces.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="document"; '
                f'filename="{name}"\r\nContent-Type: {mime}\r\n\r\n'
            ).encode()
        )
        pieces.extend((payload, f"\r\n--{boundary}--\r\n".encode()))
        body = b"".join(pieces)
        writer, dispatched = None, False
        try:
            async with asyncio.timeout(timeout):
                reader, writer = await self._connector(
                    "api.telegram.org",
                    443,
                    ssl=ssl.create_default_context(),
                    server_hostname="api.telegram.org",
                    ssl_shutdown_timeout=1,
                )
                before_dispatch()
                dispatched = True
                writer.write(
                    (
                        f"POST /bot{token}/sendDocument HTTP/1.1\r\n"
                        "Host: api.telegram.org\r\nConnection: close\r\n"
                        "Accept: application/json\r\n"
                        f"Content-Type: multipart/form-data; boundary={boundary}\r\n"
                        f"Content-Length: {len(body)}\r\nAccept-Encoding: identity\r\n\r\n"
                    ).encode()
                    + body
                )
                await writer.drain()
                return await TelegramHTTPTransport()._read_response(reader)
        except TelegramError as error:
            if dispatched:
                raise TelegramError(error.code, effect_status="unknown") from None
            raise
        except asyncio.CancelledError:
            raise TelegramCancelled("unknown" if dispatched else "known") from None
        except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError):
            raise TelegramError(
                "telegram_attachment_delivery_unconfirmed",
                effect_status="unknown" if dispatched else "known",
            ) from None
        finally:
            if writer is not None:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 1)
                except asyncio.CancelledError:
                    TelegramHTTPTransport._abort(writer)
                    raise TelegramCancelled("unknown" if dispatched else "known") from None
                except (OSError, TimeoutError):
                    TelegramHTTPTransport._abort(writer)
