"""Own private-chat Telegram adapter, no unsolicited messages or hidden retries.

Contract reviewed against https://core.telegram.org/bots/api on 2026-10-24.
Tokens and message bodies stay in RAM here. The private journal stores IDs,
cursor, routing revisions and delivery outcomes, never messages or credentials.
Only an owner-reviewed runner can dispatch fresh private-chat text. Its host
callback must use Jarvis Runtime/Authority and cancel/settle its run on cancellation.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import math
import os
import re
import ssl
import stat
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from jarvis_core.private_json import PrivateJsonDocument, PrivateJsonError

if TYPE_CHECKING:
    from jarvis_core.telegram_content import TelegramAttachment

_TOKEN = re.compile(r"([1-9][0-9]{0,18}):[A-Za-z0-9_-]{20,128}")
_UUID = re.compile(r"[a-f0-9]{32}")
_AGENT = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_HASH = re.compile(r"[a-f0-9]{64}")
_ACTIVE = {"claimed", "dispatching", "send_pending"}
_FINAL = {"completed", "ignored", "failed", "interrupted", "cancelled"}
_METHODS = {"getMe", "getWebhookInfo", "getUpdates", "sendMessage", "getFile"}


class TelegramError(ValueError):
    def __init__(self, code: str, *, effect_status="known", retry_after: int | None = None):
        if not isinstance(code, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,127}", code) is None:
            code = "telegram_error"
        super().__init__(code)
        self.code, self.effect_status, self.retry_after = code, effect_status, retry_after


class TelegramCancelled(asyncio.CancelledError):
    def __init__(self, effect_status="unknown"):
        super().__init__("telegram_cancelled")
        self.effect_status = effect_status


def _integer(value, *, minimum=0, maximum=(1 << 52) - 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise TelegramError("telegram_identity_invalid")
    return value


def _chat_id(value, *, kind="any"):
    if not isinstance(kind, str) or kind not in {"any", "private", "group", "supergroup"}:
        raise TelegramError("telegram_identity_invalid")
    _integer(value, minimum=-((1 << 52) - 1))
    if (value == 0 or (kind == "private" and value < 0)
            or (kind in {"group", "supergroup"} and value > 0)):
        raise TelegramError("telegram_identity_invalid")
    return value


def _text(value, maximum, *, empty=False):
    try:
        if (
            not isinstance(value, str)
            or "\0" in value
            or len(value.encode()) > maximum
            or (not empty and not value.strip())
        ):
            raise TelegramError("telegram_text_invalid")
    except UnicodeError:
        raise TelegramError("telegram_text_invalid") from None
    return value


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise TelegramError("telegram_response_invalid")
        value[key] = item
    return value


@dataclass(frozen=True)
class BotIdentity:
    bot_id: int
    username: str | None = None

    def __post_init__(self):
        _integer(self.bot_id, minimum=1)
        if self.username is not None and (
            not isinstance(self.username, str)
            or re.fullmatch(r"[A-Za-z0-9_]{1,64}", self.username) is None
        ):
            raise TelegramError("telegram_bot_identity_invalid")


@dataclass(frozen=True)
class TelegramRoute:
    chat_id: int
    agent_id: str
    session_id: str
    revision: int
    enabled: bool = True
    chat_type: str = "private"
    allowed_sender_ids: tuple[int, ...] = ()
    message_thread_id: int | None = None
    allow_attachments: bool = False


@dataclass(frozen=True)
class IncomingMessage:
    update_id: int
    chat_id: int
    message_id: int
    agent_id: str
    session_id: str
    text: str = field(repr=False)
    route_revision: int = 1
    chat_type: str = "private"
    sender_id: int | None = None
    message_thread_id: int | None = None
    attachments: tuple[TelegramAttachment, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class TelegramDispatchResult:
    run_id: str | None
    status: str
    outcome: str
    answer: str | None = field(default=None, repr=False)
    error_code: str | None = None

    def __post_init__(self):
        if self.run_id is not None and (
            not isinstance(self.run_id, str) or _UUID.fullmatch(self.run_id) is None
        ):
            raise TelegramError("telegram_run_identity_invalid")
        if (
            not isinstance(self.status, str)
            or self.status not in {"completed", "failed", "cancelled", "interrupted"}
            or not isinstance(self.outcome, str)
            or self.outcome not in {"known", "unknown"}
            or (self.status == "completed" and (self.outcome != "known" or self.run_id is None))
        ):
            raise TelegramError("telegram_dispatch_result_invalid")
        if self.answer is not None:
            _text(self.answer, 32000, empty=True)
        if self.error_code is not None and (
            not isinstance(self.error_code, str)
            or re.fullmatch(r"[a-z][a-z0-9_]{0,127}", self.error_code) is None
        ):
            raise TelegramError("telegram_dispatch_result_invalid")


@dataclass(frozen=True)
class TelegramHTTPResponse:
    status: int
    body: bytes = field(repr=False)


class TelegramHTTPTransport:
    """Direct verified TLS to the fixed API host, with a test-only connector hook."""

    def __init__(self, *, connector=None, max_response_bytes=262144):
        if type(max_response_bytes) is not int or not 1024 <= max_response_bytes <= 1048576:
            raise TelegramError("telegram_transport_limit_invalid")
        self._connector = connector or asyncio.open_connection
        self.max_response_bytes = max_response_bytes

    async def __call__(self, method, api_method, parameters, token, timeout, before_dispatch):
        if (
            method not in {"GET", "POST"}
            or api_method not in _METHODS
            or not _TOKEN.fullmatch(token)
        ):
            raise TelegramError("telegram_request_invalid")
        query = urlencode(
            {
                key: json.dumps(value, separators=(",", ":"))
                if isinstance(value, (list, dict, bool))
                else str(value)
                for key, value in parameters.items()
            }
        )
        body = (
            json.dumps(parameters, ensure_ascii=False, allow_nan=False).encode()
            if method == "POST"
            else b""
        )
        if len(body) > 65536 or len(query) > 65536:
            raise TelegramError("telegram_request_limit")
        path = "/bot" + token + "/" + api_method
        if method == "GET" and query:
            path += "?" + query
        header = (
            f"{method} {path} HTTP/1.1\r\nHost: api.telegram.org\r\n"
            "Connection: close\r\nAccept: application/json\r\nAccept-Encoding: identity\r\n"
        )
        if method == "POST":
            header += f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
        wire = header.encode("ascii") + b"\r\n" + body
        writer, dispatched = None, False
        try:
            async with asyncio.timeout(timeout):
                context = ssl.create_default_context()
                reader, writer = await self._connector(
                    "api.telegram.org",
                    443,
                    ssl=context,
                    server_hostname="api.telegram.org",
                    ssl_shutdown_timeout=1,
                )
                if before_dispatch is not None:
                    before_dispatch()
                dispatched = True
                writer.write(wire)
                await writer.drain()
                response = await self._read_response(reader)
                return response
        except TelegramError as error:
            if method == "POST" and dispatched and error.effect_status == "known":
                raise TelegramError(error.code, effect_status="unknown") from None
            raise
        except asyncio.CancelledError:
            raise TelegramCancelled(
                "unknown" if method == "POST" and dispatched else "known"
            ) from None
        except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError):
            raise TelegramError(
                "telegram_transport_failed",
                effect_status="unknown" if method == "POST" and dispatched else "known",
            ) from None
        finally:
            if writer is not None:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 1)
                except asyncio.CancelledError:
                    self._abort(writer)
                    raise TelegramCancelled(
                        "unknown" if method == "POST" and dispatched else "known"
                    ) from None
                except (OSError, TimeoutError):
                    self._abort(writer)

    @staticmethod
    def _abort(writer):
        transport = getattr(writer, "transport", None)
        if transport is not None:
            try:
                transport.abort()
            except OSError:
                pass

    async def _read_response(self, reader, *, maximum_bytes=None):
        maximum = self.max_response_bytes if maximum_bytes is None else maximum_bytes
        if type(maximum) is not int or not 1 <= maximum <= 5 * 1024 * 1024:
            raise TelegramError("telegram_transport_limit_invalid")
        line = await reader.readline()
        if (
            len(line) > 8192
            or re.fullmatch(rb"HTTP/1\.[01] [1-5][0-9]{2}[^\r\n]*\r\n", line) is None
        ):
            raise TelegramError("telegram_http_invalid")
        status = int(line.split(b" ", 2)[1])
        headers, total = {}, len(line)
        for _ in range(64):
            line = await reader.readline()
            total += len(line)
            if total > 16384 or not line.endswith(b"\r\n"):
                raise TelegramError("telegram_http_limit")
            if line == b"\r\n":
                break
            name, colon, value = line[:-2].partition(b":")
            name = name.lower()
            if not colon or name in headers:
                raise TelegramError("telegram_http_invalid")
            headers[name] = value.strip().lower()
        else:
            raise TelegramError("telegram_http_limit")
        if headers.get(b"content-encoding", b"identity") != b"identity":
            raise TelegramError("telegram_http_encoding_unsupported")
        if b"transfer-encoding" in headers:
            if b"content-length" in headers or headers[b"transfer-encoding"] != b"chunked":
                raise TelegramError("telegram_http_invalid")
            body = bytearray()
            while True:
                line = await reader.readline()
                if len(line) > 128 or re.fullmatch(rb"[0-9A-Fa-f]{1,8}\r\n", line) is None:
                    raise TelegramError("telegram_http_invalid")
                size = int(line.strip(), 16)
                if len(body) + size > maximum:
                    raise TelegramError("telegram_response_limit")
                if size == 0:
                    if await reader.readexactly(2) != b"\r\n":
                        raise TelegramError("telegram_http_invalid")
                    break
                body.extend(await reader.readexactly(size))
                if await reader.readexactly(2) != b"\r\n":
                    raise TelegramError("telegram_http_invalid")
            return TelegramHTTPResponse(status, bytes(body))
        if b"content-length" in headers:
            raw_length = headers[b"content-length"]
            if re.fullmatch(rb"[0-9]{1,9}", raw_length) is None:
                raise TelegramError("telegram_http_invalid")
            length = int(raw_length)
            if length > maximum:
                raise TelegramError("telegram_response_limit")
            return TelegramHTTPResponse(status, await reader.readexactly(length))
        body = bytearray()
        while piece := await reader.read(min(8192, maximum + 1 - len(body))):
            body.extend(piece)
            if len(body) > maximum:
                raise TelegramError("telegram_response_limit")
        return TelegramHTTPResponse(status, bytes(body))


class TelegramClient:
    def __init__(self, token: str, *, transport=None, file_transport=None,
                 document_transport=None, timeout=20):
        if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
            raise TelegramError("telegram_token_invalid")
        if (
            type(timeout) not in {int, float}
            or not math.isfinite(timeout)
            or not 1 <= timeout <= 20
        ):
            raise TelegramError("telegram_timeout_invalid")
        self._token = token
        self._token_bot_id = _integer(int(token.split(":", 1)[0]), minimum=1)
        self._transport = transport or TelegramHTTPTransport()
        self._file_transport = file_transport
        self._document_transport = document_transport
        self.timeout, self.identity, self.closed = timeout, None, False

    def __repr__(self):
        return "TelegramClient(token=<redactado>, connected=" + str(self.identity is not None) + ")"

    async def _request(self, name, parameters, *, guard=None):
        if self.closed or self._token is None:
            raise TelegramError("telegram_client_closed")
        method = "POST" if name == "sendMessage" else "GET"
        try:
            response = await self._transport(
                method, name, parameters, self._token, self.timeout, guard
            )
        except (TelegramError, asyncio.CancelledError):
            raise
        except Exception:
            raise TelegramError(
                "telegram_transport_failed",
                effect_status="unknown" if method == "POST" else "known",
            ) from None
        return self._decode_response(response, method)

    @staticmethod
    def _decode_response(response, method):
        if not isinstance(response, TelegramHTTPResponse) or len(response.body) > 262144:
            raise TelegramError(
                "telegram_response_invalid",
                effect_status="unknown" if method == "POST" else "known",
            )
        try:
            value = json.loads(response.body, object_pairs_hook=_object)
            if not isinstance(value, dict) or type(value.get("ok")) is not bool:
                raise ValueError
        except (ValueError, UnicodeError, RecursionError):
            raise TelegramError(
                "telegram_response_invalid",
                effect_status="unknown" if method == "POST" else "known",
            ) from None
        if value["ok"] is not True:
            code = value.get("error_code", response.status)
            if code == 429:
                details = value.get("parameters")
                retry = details.get("retry_after") if isinstance(details, dict) else None
                retry = retry if type(retry) is int and 1 <= retry <= 30 else None
                raise TelegramError("telegram_rate_limited", retry_after=retry)
            raise TelegramError(
                "telegram_webhook_conflict" if code == 409 else "telegram_api_rejected"
            )
        if response.status != 200 or "result" not in value:
            raise TelegramError(
                "telegram_response_invalid",
                effect_status="unknown" if method == "POST" else "known",
            )
        return value["result"]

    async def connect(self) -> BotIdentity:
        deadline = asyncio.get_running_loop().time() + self.timeout
        try:
            return await asyncio.wait_for(self._connect_verified(deadline), self.timeout)
        except TimeoutError:
            raise TelegramError("telegram_connect_timeout") from None

    @staticmethod
    def _deadline(deadline, code):
        if asyncio.get_running_loop().time() >= deadline:
            raise TelegramError(code)

    async def _connect_verified(self, deadline) -> BotIdentity:
        if self.identity is not None:
            return self.identity

        def guard():
            self._deadline(deadline, "telegram_connect_timeout")

        result = await self._request("getMe", {}, guard=guard)
        guard()
        if (
            not isinstance(result, dict)
            or result.get("is_bot") is not True
            or _integer(result.get("id"), minimum=1) != self._token_bot_id
        ):
            raise TelegramError("telegram_bot_identity_invalid")
        username = result.get("username")
        if username is not None and (
            not isinstance(username, str) or re.fullmatch(r"[A-Za-z0-9_]{1,64}", username) is None
        ):
            raise TelegramError("telegram_bot_identity_invalid")
        await self._check_webhook(deadline, "telegram_connect_timeout")
        guard()
        self.identity = BotIdentity(self._token_bot_id, username)
        return self.identity

    async def ensure_no_webhook(self):
        if self.identity is None:
            raise TelegramError("telegram_login_required")
        deadline = asyncio.get_running_loop().time() + self.timeout
        try:
            await asyncio.wait_for(
                self._check_webhook(deadline, "telegram_webhook_timeout"), self.timeout
            )
        except TimeoutError:
            raise TelegramError("telegram_webhook_timeout") from None

    async def _check_webhook(self, deadline, timeout_code):
        def guard():
            self._deadline(deadline, timeout_code)

        webhook = await self._request("getWebhookInfo", {}, guard=guard)
        guard()
        if not isinstance(webhook, dict) or not isinstance(webhook.get("url"), str):
            raise TelegramError("telegram_response_invalid")
        if webhook["url"]:
            raise TelegramError("telegram_webhook_conflict")

    async def get_updates(self, offset: int, *, limit=20, poll_timeout=10, before_dispatch=None):
        if self.identity is None:
            raise TelegramError("telegram_login_required")
        _integer(offset)
        if (
            type(limit) is not int
            or not 1 <= limit <= 20
            or type(poll_timeout) is not int
            or not 1 <= poll_timeout <= 10
            or poll_timeout >= self.timeout
        ):
            raise TelegramError("telegram_poll_limit_invalid")
        result = await self._request(
            "getUpdates",
            {
                "offset": offset,
                "limit": limit,
                "timeout": poll_timeout,
                "allowed_updates": ["message"],
            },
            guard=before_dispatch,
        )
        if not isinstance(result, list) or len(result) > limit:
            raise TelegramError("telegram_updates_invalid")
        previous = -1
        for update in result:
            if not isinstance(update, dict):
                raise TelegramError("telegram_updates_invalid")
            identity = _integer(update.get("update_id"))
            if identity <= previous:
                raise TelegramError("telegram_updates_invalid")
            previous = identity
        return result

    async def send_reply(self, incoming: IncomingMessage, text: str, *, before_dispatch):
        if self.identity is None or not callable(before_dispatch):
            raise TelegramError("telegram_delivery_not_authorized")
        _chat_id(incoming.chat_id, kind=incoming.chat_type)
        _integer(incoming.message_id, minimum=1, maximum=(1 << 31) - 1)
        _text(text, 16384)
        if len(text.encode("utf-16-le")) // 2 > 4096:
            raise TelegramError("telegram_reply_limit")
        parameters = {
                "chat_id": incoming.chat_id,
                "text": text,
                "reply_parameters": {
                    "message_id": incoming.message_id,
                    "allow_sending_without_reply": False,
                },
                "link_preview_options": {"is_disabled": True},
                "allow_paid_broadcast": False,
        }
        if incoming.message_thread_id is not None:
            parameters["message_thread_id"] = incoming.message_thread_id
        result = await self._request("sendMessage", parameters, guard=before_dispatch)
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("chat"), dict)
            or result["chat"].get("id") != incoming.chat_id
            or result["chat"].get("type") != incoming.chat_type
            or result.get("message_thread_id") != incoming.message_thread_id
        ):
            raise TelegramError("telegram_delivery_ack_invalid", effect_status="unknown")
        try:
            return _integer(result.get("message_id"), minimum=1, maximum=(1 << 31) - 1)
        except TelegramError:
            raise TelegramError("telegram_delivery_ack_invalid", effect_status="unknown") from None

    async def download_attachment(self, attachment, *, before_dispatch):
        from jarvis_core.telegram_content import MAX_ATTACHMENT_BYTES, TelegramAttachment
        from jarvis_core.telegram_files import TelegramFileTransport, validate_file_path

        if (self.identity is None or self.closed or not isinstance(attachment, TelegramAttachment)
                or not callable(before_dispatch)):
            raise TelegramError("telegram_attachment_not_authorized")
        deadline = asyncio.get_running_loop().time() + self.timeout

        def guard():
            self._deadline(deadline, "telegram_attachment_timeout")
            if self.closed or self.identity is None:
                raise TelegramError("telegram_client_closed")
            before_dispatch()

        try:
            async with asyncio.timeout(self.timeout):
                guard()
                value = await self._request("getFile", {"file_id": attachment.file_id}, guard=guard)
                guard()
                if (not isinstance(value, dict) or value.get("file_id") != attachment.file_id
                        or value.get("file_unique_id") != attachment.unique_id):
                    raise TelegramError("telegram_attachment_identity_changed")
                size = value.get("file_size")
                if size is not None:
                    _integer(size, minimum=1, maximum=MAX_ATTACHMENT_BYTES)
                if (size is not None and attachment.file_size is not None
                        and size != attachment.file_size):
                    raise TelegramError("telegram_attachment_identity_changed")
                path = validate_file_path(value.get("file_path"))
                transport = self._file_transport or TelegramFileTransport(
                    connector=getattr(self._transport, "_connector", None))
                response = await transport(path, self._token,
                    max(.001, deadline - asyncio.get_running_loop().time()), guard)
                guard()
                if (not isinstance(response, TelegramHTTPResponse) or response.status != 200
                        or not 1 <= len(response.body) <= MAX_ATTACHMENT_BYTES
                        or any(known is not None and len(response.body) != known
                               for known in (size, attachment.file_size))):
                    raise TelegramError("telegram_attachment_bytes_invalid")
                return response.body
        except TimeoutError:
            raise TelegramError("telegram_attachment_timeout") from None
        except (TelegramError, asyncio.CancelledError):
            raise
        except Exception:
            raise TelegramError("telegram_attachment_transport_failed") from None

    async def send_document(self, incoming, payload, name, mime, caption, *, before_dispatch):
        from jarvis_core.telegram_content import MAX_ATTACHMENT_BYTES
        from jarvis_core.telegram_files import TelegramDocumentTransport

        if (self.identity is None or self.closed or not isinstance(incoming, IncomingMessage)
                or type(payload) is not bytes or not 1 <= len(payload) <= MAX_ATTACHMENT_BYTES
                or not callable(before_dispatch)):
            raise TelegramError("telegram_attachment_not_authorized")
        _chat_id(incoming.chat_id, kind=incoming.chat_type)
        _integer(incoming.message_id, minimum=1, maximum=(1 << 31) - 1)
        _text(name, 256)
        _text(caption, 4096, empty=True)
        if (any(ord(c) < 32 or c in '\\"/' for c in name)
                or not isinstance(mime, str)
                or re.fullmatch(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+", mime) is None
                or len(mime) > 128 or len(caption.encode("utf-16-le")) // 2 > 1024):
            raise TelegramError("telegram_attachment_invalid")
        transport = self._document_transport or TelegramDocumentTransport(
            connector=getattr(self._transport, "_connector", None))
        try:
            response = await transport(incoming, payload, name, mime, caption, self._token,
                                       self.timeout, before_dispatch)
            result = self._decode_response(response, "POST")
        except (TelegramError, asyncio.CancelledError):
            raise
        except Exception:
            raise TelegramError(
                "telegram_attachment_delivery_unconfirmed", effect_status="unknown"
            ) from None
        document = result.get("document") if isinstance(result, dict) else None
        if (not isinstance(result, dict) or not isinstance(result.get("chat"), dict)
                or result["chat"].get("id") != incoming.chat_id
                or result["chat"].get("type") != incoming.chat_type
                or result.get("message_thread_id") != incoming.message_thread_id
                or not isinstance(document, dict) or document.get("file_name") != name
                or type(document.get("file_size")) is not int
                or document["file_size"] != len(payload)):
            raise TelegramError("telegram_delivery_ack_invalid", effect_status="unknown")
        try:
            return _integer(result.get("message_id"), minimum=1, maximum=(1 << 31) - 1)
        except TelegramError:
            raise TelegramError("telegram_delivery_ack_invalid", effect_status="unknown") from None

    async def aclose(self):
        self.closed, self.identity, self._token = True, None, None


def _journal_initial():
    return {
        "version": 2,
        "bot_id": None,
        "epoch": 0,
        "offset": 0,
        "routes": {},
        "events": {},
        "control_messages": {},
    }


def _route(row):
    base = {
        "chat_id",
        "agent_id",
        "session_id",
        "revision",
        "enabled",
    }
    policy = {"chat_type", "allowed_sender_ids", "message_thread_id", "allow_attachments"}
    if not isinstance(row, dict) or set(row) not in (base, base | policy):
        raise TelegramError("telegram_route_invalid")
    kind = row.get("chat_type", "private")
    if not isinstance(kind, str) or kind not in {"private", "group", "supergroup"}:
        raise TelegramError("telegram_route_invalid")
    _chat_id(row["chat_id"], kind=kind)
    senders, thread = row.get("allowed_sender_ids", []), row.get("message_thread_id")
    if (not isinstance(senders, (list, tuple)) or len(senders) > 32
            or any(type(item) is not int or not 1 <= item <= (1 << 52) - 1 for item in senders)
            or len(set(senders)) != len(senders)
            or (kind == "private" and senders)
            or (kind != "private" and not senders)
            or (thread is not None and (kind != "supergroup" or type(thread) is not int
                                       or not 1 <= thread < (1 << 31)))
            or type(row.get("allow_attachments", False)) is not bool):
        raise TelegramError("telegram_route_invalid")
    if (
        not isinstance(row["agent_id"], str)
        or _AGENT.fullmatch(row["agent_id"]) is None
        or not isinstance(row["session_id"], str)
        or _UUID.fullmatch(row["session_id"]) is None
        or type(row["revision"]) is not int
        or row["revision"] < 1
        or type(row["enabled"]) is not bool
    ):
        raise TelegramError("telegram_route_invalid")
    return TelegramRoute(**{**row, "allowed_sender_ids": tuple(senders)})


def _journal_validate(value):
    if (
        not isinstance(value, dict)
        or set(value)
        not in (
            {"version", "bot_id", "epoch", "offset", "routes", "events"},
            {"version", "bot_id", "epoch", "offset", "routes", "events", "control_messages"},
        )
        or type(value["version"]) is not int
        or value["version"] not in {1, 2}
        or (value["version"] == 2) != ("control_messages" in value)
        or not isinstance(value["routes"], dict)
        or len(value["routes"]) > 32
        or not isinstance(value["events"], dict)
        or len(value["events"]) > 2048
    ):
        raise TelegramError("telegram_journal_invalid")
    if value["bot_id"] is not None:
        _integer(value["bot_id"], minimum=1)
    _integer(value["offset"])
    _integer(value["epoch"])
    controls = value.get("control_messages", {})
    if not isinstance(controls, dict) or len(controls) > 256:
        raise TelegramError("telegram_journal_invalid")
    for key, marker in controls.items():
        if not isinstance(key, str) or marker is not True:
            raise TelegramError("telegram_journal_invalid")
        parts = key.split(":")
        if (len(parts) != 2 or not re.fullmatch(r"-?[1-9][0-9]{0,15}", parts[0])
                or not re.fullmatch(r"[1-9][0-9]{0,15}", parts[1])):
            raise TelegramError("telegram_journal_invalid")
        _chat_id(int(parts[0]))
        _integer(int(parts[1]), minimum=1, maximum=(1 << 31) - 1)
    for key, row in value["routes"].items():
        route = _route(row)
        if key != str(route.chat_id):
            raise TelegramError("telegram_route_invalid")
    for key, row in value["events"].items():
        fields = {
            "update_id",
            "epoch",
            "chat_id",
            "message_id",
            "agent_id",
            "session_id",
            "route_revision",
            "status",
            "outcome",
            "run_id",
            "error_code",
            "outgoing_message_id",
            "reply_sha256",
            "reply_truncated",
        }
        context = {"chat_type", "sender_id", "message_thread_id", "attachment_count"}
        if not isinstance(row, dict) or set(row) not in (fields, fields | context):
            raise TelegramError("telegram_journal_invalid")
        _integer(row["update_id"])
        _integer(row["epoch"])
        if (
            key != str(row["epoch"]) + ":" + str(row["update_id"])
            or row["epoch"] > value["epoch"]
            or (row["epoch"] == value["epoch"] and row["update_id"] >= value["offset"])
        ):
            raise TelegramError("telegram_journal_invalid")
        if (
            not isinstance(row["status"], str)
            or row["status"] not in _ACTIVE | _FINAL
            or not isinstance(row["outcome"], str)
            or row["outcome"] not in {"pending", "known", "unknown"}
            or (row["status"] in _ACTIVE) != (row["outcome"] == "pending")
            or (row["status"] == "completed" and row["outcome"] != "known")
            or type(row["reply_truncated"]) is not bool
        ):
            raise TelegramError("telegram_journal_invalid")
        if row["chat_id"] is not None:
            _chat_id(row["chat_id"])
            if (not isinstance(row["agent_id"], str) or not _AGENT.fullmatch(row["agent_id"])
                    or not isinstance(row["session_id"], str)
                    or not _UUID.fullmatch(row["session_id"])
                    or type(row["route_revision"]) is not int or row["route_revision"] < 1):
                raise TelegramError("telegram_journal_invalid")
            _integer(row["message_id"], minimum=1, maximum=(1 << 31) - 1)
            if "chat_type" in row:
                kind = row["chat_type"]
                if not isinstance(kind, str) or kind not in {"private", "group", "supergroup"}:
                    raise TelegramError("telegram_journal_invalid")
                _chat_id(row["chat_id"], kind=kind)
                _integer(row["sender_id"], minimum=1)
                _integer(row["attachment_count"], maximum=1)
                thread = row["message_thread_id"]
                if ((kind == "private" and row["sender_id"] != row["chat_id"])
                        or (thread is not None and (kind != "supergroup" or type(thread) is not int
                                                   or not 1 <= thread < (1 << 31)))):
                    raise TelegramError("telegram_journal_invalid")
            elif row["chat_id"] < 0:
                raise TelegramError("telegram_journal_invalid")
        elif any(
            row[key] is not None
            for key in ("message_id", "agent_id", "session_id", "route_revision")
        ):
            raise TelegramError("telegram_journal_invalid")
        elif "chat_type" in row:
            raise TelegramError("telegram_journal_invalid")
        if row["outgoing_message_id"] is not None:
            _integer(row["outgoing_message_id"], minimum=1, maximum=(1 << 31) - 1)
        if row["run_id"] is not None and (
            not isinstance(row["run_id"], str) or _UUID.fullmatch(row["run_id"]) is None
        ):
            raise TelegramError("telegram_journal_invalid")
        if row["reply_sha256"] is not None and (
            not isinstance(row["reply_sha256"], str) or _HASH.fullmatch(row["reply_sha256"]) is None
        ):
            raise TelegramError("telegram_journal_invalid")
        if row["error_code"] is not None and (
            not isinstance(row["error_code"], str)
            or re.fullmatch(r"[a-z][a-z0-9_]{0,127}", row["error_code"]) is None
        ):
            raise TelegramError("telegram_journal_invalid")


class TelegramJournal:
    def __init__(self, directory: Path):
        self.document = PrivateJsonDocument(
            directory,
            "telegram.json",
            initial=_journal_initial,
            validate=_journal_validate,
            max_bytes=1048576,
        )

    @property
    def offset(self):
        return self.document.read()["offset"]

    @property
    def bot_id(self):
        return self.document.read()["bot_id"]

    def _configuration_update(self, change, *, before_publish=None):
        if before_publish is not None and not callable(before_publish):
            raise TelegramError("telegram_reconciliation_invalid")
        if before_publish is not None:
            # The reviewed owner acquires both fixed locks itself, before any
            # fallible descriptor observation. No legacy pre-owner parent/runner
            # acquisition can strand a lease before custody begins.
            return self.document.update(change, before_publish=before_publish,
                                        _runner_lock_name="telegram.runner.lock")
        descriptor = self.acquire_runner()
        try:
            return self.document.update(change)
        finally:
            os.close(descriptor)

    def bind_bot(self, identity: BotIdentity):
        if not isinstance(identity, BotIdentity):
            raise TelegramError("telegram_bot_identity_invalid")
        if self.bot_id == identity.bot_id:
            return

        def change(value):
            if value["bot_id"] not in {None, identity.bot_id}:
                raise TelegramError("telegram_bot_changed")
            value["bot_id"] = identity.bot_id
            return value

        self._configuration_update(change)

    def routes(self):
        return tuple(_route(row) for _, row in sorted(self.document.read()["routes"].items()))

    def configuration_hash(self):
        value = self.document.read()
        return hashlib.sha256(
            json.dumps(
                [value["bot_id"], value["routes"]], sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()

    def acquire_runner(self):
        with self.document._parent() as parent:
            try:
                descriptor = os.open(
                    "telegram.runner.lock",
                    os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                    0o600,
                    dir_fd=parent,
                )
                info = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_nlink != 1
                    or info.st_size
                ):
                    raise TelegramError("telegram_runner_lock_unsafe")
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return descriptor
            except BaseException:
                if "descriptor" in locals():
                    os.close(descriptor)
                raise TelegramError("telegram_runner_unavailable") from None

    def _assert_runner(self, descriptor):
        try:
            with self.document._parent() as parent:
                info = os.fstat(descriptor)
                linked = os.stat("telegram.runner.lock", dir_fd=parent, follow_symlinks=False)
                if (
                    not stat.S_ISREG(linked.st_mode)
                    or linked.st_uid != os.getuid()
                    or stat.S_IMODE(linked.st_mode) != 0o600
                    or linked.st_nlink != 1
                    or linked.st_size
                    or (info.st_dev, info.st_ino) != (linked.st_dev, linked.st_ino)
                ):
                    raise TelegramError("telegram_runner_changed")
        except OSError:
            raise TelegramError("telegram_runner_changed") from None

    def set_route(self, chat_id, agent_id, session_id, *, expected_revision=None, enabled=True,
                  chat_type="private", allowed_sender_ids=(), message_thread_id=None,
                  allow_attachments=False):
        candidate = {
                "chat_id": chat_id,
                "agent_id": agent_id,
                "session_id": session_id,
                "revision": 1,
                "enabled": enabled,
        }
        candidate.update(chat_type=chat_type, allowed_sender_ids=allowed_sender_ids,
                         message_thread_id=message_thread_id, allow_attachments=allow_attachments)
        _route(candidate)
        if (chat_type == "private" and not allowed_sender_ids and message_thread_id is None
                and allow_attachments is False):
            for key in ("chat_type", "allowed_sender_ids", "message_thread_id",
                        "allow_attachments"):
                candidate.pop(key)
        else:
            candidate["allowed_sender_ids"] = list(allowed_sender_ids)

        if expected_revision is not None:
            _integer(expected_revision, minimum=1)

        def change(value):
            row = value["routes"].get(str(chat_id))
            if row is not None and row["revision"] != expected_revision:
                raise TelegramError("telegram_route_revision_conflict")
            if row is None and expected_revision is not None:
                raise TelegramError("telegram_route_revision_conflict")
            if row is None and len(value["routes"]) >= 32:
                raise TelegramError("telegram_route_limit")
            value["routes"][str(chat_id)] = {**candidate,
                "revision": row["revision"] + 1 if row else 1}
            return value

        value = self._configuration_update(change)
        return _route(value["routes"][str(chat_id)])

    def reconcile_route(self, chat_id, *, expected_revision, enabled,
                        expected_configuration_hash, before_publish):
        """Change one existing route; no token, session, reader or message effect.

        Only the host maintenance authority supplies before_publish. The exact
        configuration is checked inside the locked JSON transaction as well as
        by the host's frozen profile cut. Event history is never rewritten.
        """
        _chat_id(chat_id)
        _integer(expected_revision, minimum=1)
        if (type(enabled) is not bool or not callable(before_publish)
                or type(expected_configuration_hash) is not str
                or not _HASH.fullmatch(expected_configuration_hash)):
            raise TelegramError("telegram_reconciliation_invalid")

        def change(value):
            actual = hashlib.sha256(json.dumps(
                [value["bot_id"], value["routes"]], sort_keys=True,
                separators=(",", ":")).encode()).hexdigest()
            row = value["routes"].get(str(chat_id))
            if (actual != expected_configuration_hash or row is None
                    or row["revision"] != expected_revision or row["enabled"] is enabled):
                raise TelegramError("telegram_reconciliation_changed")
            row["enabled"] = enabled
            row["revision"] += 1
            return value

        value = self._configuration_update(change, before_publish=before_publish)
        return _route(value["routes"][str(chat_id)])

    def remove_route(self, chat_id, *, expected_revision):
        _chat_id(chat_id)

        if expected_revision is not None:
            _integer(expected_revision, minimum=1)

        def change(value):
            row = value["routes"].get(str(chat_id))
            if row is None or row["revision"] != expected_revision:
                raise TelegramError("telegram_route_revision_conflict")
            del value["routes"][str(chat_id)]
            return value

        self._configuration_update(change)

    def mark_control_message(self, chat_id, message_id):
        """Keep only physical IDs; never retain nonce, URI or command text."""
        _chat_id(chat_id)
        _integer(message_id, minimum=1, maximum=(1 << 31) - 1)
        key = str(chat_id) + ":" + str(message_id)

        def change(value):
            controls = value.setdefault("control_messages", {})
            value["version"] = 2
            if key not in controls and len(controls) >= 256:
                raise TelegramError("telegram_control_limit")
            controls[key] = True
            return value

        self.document.update(change)

    def is_control_message(self, chat_id, message_id):
        value = self.document.read()
        return str(chat_id) + ":" + str(message_id) in value.get("control_messages", {})

    def claim(
        self,
        update_id,
        incoming: IncomingMessage | None,
        *,
        error_code=None,
        permit_epoch_change=False,
    ):
        _integer(update_id, maximum=(1 << 52) - 2)
        accepted = False

        def change(value):
            nonlocal accepted
            key = str(value["epoch"]) + ":" + str(update_id)
            if incoming is not None:
                if str(incoming.chat_id) + ":" + str(incoming.message_id) in value.get(
                    "control_messages", {}
                ):
                    return value
                # A physical private message is stable across Telegram's random
                # update-ID generations. Never replay it under another update ID.
                if any(
                    row["chat_id"] == incoming.chat_id and row["message_id"] == incoming.message_id
                    for row in value["events"].values()
                ):
                    return value
            if update_id < value["offset"]:
                if not permit_epoch_change or (incoming is None and key in value["events"]):
                    return value
                value["epoch"] += 1
                value["offset"] = 0
                key = str(value["epoch"]) + ":" + str(update_id)
            elif key in value["events"]:
                return value
            if len(value["events"]) >= 2048:
                raise TelegramError("telegram_journal_limit")
            row = {
                "update_id": update_id,
                "epoch": value["epoch"],
                "chat_id": incoming.chat_id if incoming else None,
                "message_id": incoming.message_id if incoming else None,
                "agent_id": incoming.agent_id if incoming else None,
                "session_id": incoming.session_id if incoming else None,
                "route_revision": incoming.route_revision if incoming else None,
                "status": "claimed" if incoming and error_code is None else "ignored",
                "outcome": "pending" if incoming and error_code is None else "known",
                "run_id": None,
                "error_code": error_code,
                "outgoing_message_id": None,
                "reply_sha256": None,
                "reply_truncated": False,
            }
            if incoming is not None and (incoming.chat_type != "private" or incoming.attachments):
                row.update(chat_type=incoming.chat_type, sender_id=incoming.sender_id,
                           message_thread_id=incoming.message_thread_id,
                           attachment_count=len(incoming.attachments))
            value["events"][key] = row
            value["offset"] = update_id + 1
            accepted = row["status"] == "claimed"
            return value

        self.document.update(change)
        return accepted

    def transition(
        self,
        update_id,
        *,
        status,
        outcome,
        run_id=None,
        error_code=None,
        reply_sha256=None,
        reply_truncated=False,
        outgoing_message_id=None,
    ):
        def change(value):
            row = value["events"].get(str(value["epoch"]) + ":" + str(update_id))
            if row is None or row["status"] not in _ACTIVE:
                raise TelegramError("telegram_event_not_active")
            row.update(
                status=status,
                outcome=outcome,
                run_id=run_id or row["run_id"],
                error_code=error_code,
                reply_sha256=reply_sha256 or row["reply_sha256"],
                reply_truncated=reply_truncated or row["reply_truncated"],
                outgoing_message_id=outgoing_message_id,
            )
            return value

        self.document.update(change)

    def attach_run(self, incoming, run_id):
        if not isinstance(run_id, str) or not _UUID.fullmatch(run_id):
            raise TelegramError("telegram_run_identity_invalid")

        def change(value):
            row = value["events"].get(str(value["epoch"]) + ":" + str(incoming.update_id))
            if (row is None or row["status"] != "dispatching"
                    or (row["chat_id"], row["message_id"], row["session_id"], row["route_revision"])
                    != (incoming.chat_id, incoming.message_id, incoming.session_id,
                        incoming.route_revision)
                    or row["run_id"] not in {None, run_id}):
                raise TelegramError("telegram_event_not_active")
            row["run_id"] = run_id
            return value
        self.document.update(change)

    def _recover_unfinished(self):
        def change(value):
            for row in value["events"].values():
                if row["status"] in _ACTIVE:
                    row.update(
                        status="interrupted",
                        outcome="unknown",
                        error_code="telegram_interrupted_no_replay",
                    )
            return value

        self.document.update(change)

    def recover_unfinished(self):
        descriptor = self.acquire_runner()
        try:
            self._recover_unfinished()
        finally:
            os.close(descriptor)

    def history(self, *, limit=50):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise TelegramError("telegram_page_invalid")
        rows = self.document.read()["events"]
        return tuple(
            dict(rows[key])
            for key in sorted(rows, key=lambda key: tuple(map(int, key.split(":"))), reverse=True)[
                :limit
            ]
        )


def _reply(text):
    _text(text, 32000)
    suffix = "\n[Respuesta truncada; completa en Jarvis.]"
    if len(text.encode("utf-16-le")) // 2 <= 4096 and len(text.encode()) <= 16384:
        return text, False
    units, kept = 0, []
    budget = 4096 - len(suffix.encode("utf-16-le")) // 2
    for character in text:
        needed = 2 if ord(character) > 0xFFFF else 1
        if units + needed > budget:
            break
        kept.append(character)
        units += needed
    return "".join(kept) + suffix, True


class TelegramChannel:
    def __init__(
        self,
        client: TelegramClient,
        journal: TelegramJournal,
        dispatch: Callable[[IncomingMessage], Awaitable[TelegramDispatchResult]],
        *,
        clock=time.time,
        dispatch_timeout=1800,
        close_timeout=10,
        configuration_guard=None,
    ):
        if (
            not callable(dispatch)
            or type(dispatch_timeout) not in {int, float}
            or not math.isfinite(dispatch_timeout)
            or not 1 <= dispatch_timeout <= 3600
            or type(close_timeout) not in {int, float}
            or not 0 < close_timeout <= 30
            or (configuration_guard is not None and not callable(configuration_guard))
        ):
            raise TelegramError("telegram_limits_invalid")
        self.client, self.journal, self.dispatch = client, journal, dispatch
        self.clock, self.dispatch_timeout, self.close_timeout = (
            clock,
            dispatch_timeout,
            close_timeout,
        )
        self.error_code, self._worker, self._lease = None, None, None
        self._stop, self._closed = asyncio.Event(), False
        self._reviewed_hash, self._started_at = None, None
        self._send_tickets = set()
        self._poll_offset = 0
        self._last_received_at = 0
        self._host_configuration_guard = configuration_guard

    @property
    def running(self):
        return self._worker is not None and not self._worker.done() and not self._stop.is_set()

    async def connect(self):
        if self.running or self._closed:
            raise TelegramError("telegram_runner_unavailable")
        identity = await self.client.connect()
        self.journal.bind_bot(identity)
        return identity

    def start(self, *, expected_configuration_hash):
        if self._closed or self.client.identity is None:
            raise TelegramError("telegram_login_required")
        if self.running:
            raise TelegramError("telegram_already_running")
        if expected_configuration_hash != self.journal.configuration_hash():
            raise TelegramError("telegram_configuration_changed")
        if not any(route.enabled for route in self.journal.routes()):
            raise TelegramError("telegram_route_required")
        self._lease = self.journal.acquire_runner()
        try:
            self.journal._assert_runner(self._lease)
            if expected_configuration_hash != self.journal.configuration_hash():
                raise TelegramError("telegram_configuration_changed")
            if self.journal.bot_id != self.client.identity.bot_id:
                raise TelegramError("telegram_bot_changed")
            self.journal._recover_unfinished()
            self._reviewed_hash = expected_configuration_hash
            # Telegram dates have whole-second precision. A message from the
            # same second may predate the owner's start gesture, so discard
            # that entire partial second rather than authorize an old inbox.
            self._started_at = math.floor(self.clock()) + 1
            self._poll_offset = 0
            self._last_received_at = self.clock()
            self._stop, self.error_code = asyncio.Event(), None
            self._worker = asyncio.create_task(self._run())
        except BaseException:
            os.close(self._lease)
            self._lease = None
            raise
        return True

    def _guard_configuration(self):
        if self._stop.is_set() or self._reviewed_hash != self.journal.configuration_hash():
            raise TelegramError("telegram_configuration_changed")
        self.journal._assert_runner(self._lease)
        if self.client.identity is None or self.journal.bot_id != self.client.identity.bot_id:
            raise TelegramError("telegram_bot_changed")
        if self._host_configuration_guard is not None:
            self._host_configuration_guard()

    def _incoming(self, update):
        message = update.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("chat"), dict):
            return None, "telegram_update_ignored"
        chat = message["chat"]
        if type(chat.get("id")) is not int:
            return None, "telegram_sender_ignored"
        route = next(
            (item for item in self.journal.routes() if item.chat_id == chat["id"] and item.enabled),
            None,
        )
        if route is None or chat["id"] == self.client.identity.bot_id:
            return None, "telegram_sender_ignored"
        date = message.get("date")
        if type(date) is not int or not self._started_at <= date <= self.clock() + 300:
            return None, "telegram_message_predates_start"
        identity = message.get("message_id")
        try:
            _integer(identity, minimum=1, maximum=(1 << 31) - 1)
            from jarvis_core.telegram_content import message_content

            text, attachments = message_content(message, route, self.client.identity)
        except TelegramError as error:
            supported = error.code.startswith(("telegram_sender", "telegram_thread",
                                                "telegram_mention", "telegram_attachment"))
            return None, error.code if supported else "telegram_text_unsupported"
        if re.match(
            r"^/(?:start|jarvis_pair)(?:$|\s|@)", text
        ) is not None or self.journal.is_control_message(route.chat_id, identity):
            return None, "telegram_control_ignored"
        return IncomingMessage(
            update["update_id"],
            route.chat_id,
            identity,
            route.agent_id,
            route.session_id,
            text,
            route.revision,
            route.chat_type,
            message["from"]["id"],
            route.message_thread_id,
            attachments,
        ), None

    async def _process(self, update):
        self._guard_configuration()
        incoming, ignored = self._incoming(update)
        if not self.journal.claim(
            update["update_id"],
            incoming,
            error_code=ignored,
            permit_epoch_change=self._poll_offset < self.journal.offset,
        ):
            return
        self.journal.transition(incoming.update_id, status="dispatching", outcome="pending")
        ticket = None
        dispatch_returned = False
        try:
            async with asyncio.timeout(self.dispatch_timeout) as deadline:
                result = await self.dispatch(incoming)
            if self._stop.is_set() or deadline.expired():
                raise TelegramCancelled()
            if not isinstance(result, TelegramDispatchResult):
                raise TelegramError("telegram_dispatch_unconfirmed", effect_status="unknown")
            dispatch_returned = True
            if result.status != "completed" or result.outcome != "known" or not result.answer:
                self.journal.transition(
                    incoming.update_id,
                    status=result.status,
                    outcome=result.outcome,
                    run_id=result.run_id,
                    error_code=result.error_code,
                )
                return
            text, truncated = _reply(result.answer)
            digest = hashlib.sha256(text.encode()).hexdigest()
            self.journal.transition(
                incoming.update_id,
                status="send_pending",
                outcome="pending",
                run_id=result.run_id,
                reply_sha256=digest,
                reply_truncated=truncated,
            )
            ticket = object()
            self._send_tickets.add(ticket)

            def before_dispatch():
                self._guard_configuration()
                current = next(
                    (item for item in self.journal.routes() if item.chat_id == incoming.chat_id),
                    None,
                )
                if (
                    ticket not in self._send_tickets
                    or current is None
                    or not current.enabled
                    or current.revision != incoming.route_revision
                    or (current.agent_id, current.session_id)
                    != (incoming.agent_id, incoming.session_id)
                ):
                    raise TelegramError("telegram_delivery_not_authorized")
                self._send_tickets.remove(ticket)

            sent = await self.client.send_reply(incoming, text, before_dispatch=before_dispatch)
            if self._stop.is_set():
                raise TelegramCancelled()
            self.journal.transition(
                incoming.update_id,
                status="completed",
                outcome="known",
                run_id=result.run_id,
                outgoing_message_id=sent,
            )
        except asyncio.CancelledError:
            self.journal.transition(
                incoming.update_id,
                status="cancelled",
                outcome="unknown",
                error_code="telegram_cancelled_no_replay",
            )
            raise
        except TelegramError as error:
            self.journal.transition(
                incoming.update_id,
                status="failed",
                outcome="unknown"
                if not dispatch_returned or error.effect_status == "unknown"
                else "known",
                error_code=error.code,
            )
            raise
        except Exception:
            self.journal.transition(
                incoming.update_id,
                status="failed",
                outcome="unknown",
                error_code="telegram_dispatch_unconfirmed",
            )
            raise TelegramError("telegram_dispatch_unconfirmed", effect_status="unknown") from None
        finally:
            self._send_tickets.discard(ticket)

    async def _run(self):
        rate_retries = 0
        try:
            while not self._stop.is_set():
                self._guard_configuration()
                try:
                    # Telegram randomizes its update counter after a week idle.
                    # Probe unconfirmed updates at owner-reviewed start and after
                    # prolonged idle, retaining message-ID dedup and all old epochs.
                    if self.clock() - self._last_received_at >= 604800:
                        self._poll_offset = 0
                        self._last_received_at = self.clock()
                    updates = await self.client.get_updates(
                        self._poll_offset, before_dispatch=self._guard_configuration
                    )
                except TelegramError as error:
                    if (
                        error.code == "telegram_rate_limited"
                        and error.retry_after
                        and rate_retries < 2
                    ):
                        rate_retries += 1
                        try:
                            await asyncio.wait_for(self._stop.wait(), error.retry_after)
                        except TimeoutError:
                            pass
                        continue
                    raise
                rate_retries = 0
                for update in updates:
                    await self._process(update)
                if updates:
                    self._poll_offset = updates[-1]["update_id"] + 1
                    self._last_received_at = self.clock()
                if not updates:
                    try:
                        await asyncio.wait_for(self._stop.wait(), 0.25)
                    except TimeoutError:
                        pass
        except asyncio.CancelledError:
            pass
        except (TelegramError, PrivateJsonError) as error:
            self.error_code = error.code
        except Exception:
            self.error_code = "telegram_runner_failed"
        finally:
            self._stop.set()
            self._send_tickets.clear()
            if self._lease is not None:
                os.close(self._lease)
                self._lease = None

    async def stop(self):
        self._stop.set()
        if self._worker is not None and not self._worker.done():
            self._worker.cancel()
            done, pending = await asyncio.wait((self._worker,), timeout=self.close_timeout)
            if pending:
                raise TelegramError("telegram_cleanup_incomplete", effect_status="unknown")
            await asyncio.gather(*done, return_exceptions=True)

    async def aclose(self):
        await self.stop()
        self._closed = True
        await self.client.aclose()
