"""Own Ogg tones and in-memory Bot API replies; no microphone, account or network."""

import asyncio
import hashlib
import json
import threading
import time
from dataclasses import replace
from email import policy
from email.parser import BytesParser
from types import SimpleNamespace

import pytest
from test_media_attachments import attachments as attachments
from test_media_audio import artificial_ogg
from test_telegram import BOT, CHAT, SESSION, TOKEN
from test_tools import grant

from jarvis_core.media_documents import MediaError
from jarvis_core.runtime import ApprovalRequest
from jarvis_core.state import SessionRef
from jarvis_core.telegram import (
    BotIdentity,
    IncomingMessage,
    TelegramCancelled,
    TelegramClient,
    TelegramError,
    TelegramHTTPResponse,
)
from jarvis_core.telegram_content import MAX_ATTACHMENT_BYTES
from jarvis_core.telegram_files import (
    TelegramDocumentTransport,
    TelegramVoiceTransport,
    validate_voice_payload,
)
from jarvis_core.telegram_tools import NAME, TelegramReplyTools
from jarvis_core.tools import ApprovalAuthority, canonical_arguments, operation_digest

GROUP = -100777777


def incoming():
    return IncomingMessage(1, GROUP, 11, "jarvis", SESSION, "nota", 1, "supergroup", CHAT, 9)


def voice_ack(*, optional=True):
    result = {
        "message_id": 501,
        "chat": {"id": GROUP, "type": "supergroup"},
        "from": {"id": BOT, "is_bot": True},
        "reply_to_message": {"message_id": 11},
        "message_thread_id": 9,
        "voice": {"file_id": "voice_fixture", "file_unique_id": "unique_fixture", "duration": 1},
    }
    if optional:
        # Reported platform size is not an independent hash/size observation of
        # the source. Telegram can transform the uploaded original container.
        result["voice"].update({"mime_type": "audio/ogg", "file_size": 99999})
    return result


def response(result):
    return TelegramHTTPResponse(200, json.dumps({"ok": True, "result": result}).encode())


class MemoryWriter:
    def __init__(self):
        self.data, self.writes, self.closed = b"", 0, False

    def write(self, data):
        self.data += data
        self.writes += 1

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


@pytest.mark.parametrize("voice", [False, True])
async def test_multipart_endpoint_and_field_are_exact_without_post_retries(voice):
    payload, writer, connections, guards = artificial_ogg(), MemoryWriter(), [], []
    ack = json.dumps({"ok": True, "result": voice_ack()}).encode()

    async def connector(host, port, **options):
        assert host == "api.telegram.org" and port == 443
        assert options["server_hostname"] == "api.telegram.org"
        assert options["ssl"].check_hostname
        connections.append(1)
        reader = asyncio.StreamReader()
        reader.feed_data(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(ack)).encode()
                         + b"\r\n\r\n" + ack)
        reader.feed_eof()
        return reader, writer

    transport_type = TelegramVoiceTransport if voice else TelegramDocumentTransport
    transport = transport_type(connector=connector)
    await transport(incoming(), payload, "nota.ogg", "audio/ogg", "PIE LOCAL 4826", TOKEN, 2,
                    lambda: guards.append(1))
    head, body = writer.data.split(b"\r\n\r\n", 1)
    method = "sendVoice" if voice else "sendDocument"
    assert head.startswith(f"POST /bot{TOKEN}/{method} HTTP/1.1\r\n".encode())
    content_type = next(line.split(b": ", 1)[1] for line in head.split(b"\r\n")
                        if line.startswith(b"Content-Type: "))
    parsed = BytesParser(policy=policy.default).parsebytes(
        b"MIME-Version: 1.0\r\nContent-Type: " + content_type + b"\r\n\r\n" + body)
    fields = {part.get_param("name", header="content-disposition"): part
              for part in parsed.iter_parts()}
    media = fields["voice" if voice else "document"]
    assert media.get_payload(decode=True) == payload and media.get_filename() == "nota.ogg"
    assert media.get_content_type() == "audio/ogg"
    assert ("disable_content_type_detection" in fields) is not voice
    assert fields["allow_paid_broadcast"].get_payload(decode=True) == b"false"
    assert fields["message_thread_id"].get_payload(decode=True) == b"9"
    assert json.loads(fields["reply_parameters"].get_payload(decode=True)) == {
        "message_id": 11, "allow_sending_without_reply": False}
    assert writer.writes == len(connections) == len(guards) == 1 and writer.closed


@pytest.mark.parametrize("error_type", [TelegramError, MediaError])
async def test_voice_wire_guard_failure_after_connect_sends_zero_bytes(error_type):
    writer, opens = MemoryWriter(), []

    async def connector(*args, **kwargs):
        opens.append(1)
        return asyncio.StreamReader(), writer

    def denied():
        raise error_type("telegram_route_unavailable")

    with pytest.raises(error_type, match="telegram_route_unavailable") as caught:
        await TelegramVoiceTransport(connector=connector)(
            incoming(), artificial_ogg(), "nota.ogg", "audio/ogg", "", TOKEN, 2, denied)
    assert caught.value.effect_status == "known" and opens == [1]
    assert writer.writes == 0 and writer.closed


@pytest.mark.parametrize("cancel", [False, True])
async def test_voice_lost_ack_or_cancel_after_wire_is_unknown_with_one_post(cancel):
    writer, written, opens = MemoryWriter(), asyncio.Event(), []

    async def drain():
        written.set()

    writer.drain = drain

    async def connector(*args, **kwargs):
        opens.append(1)
        reader = asyncio.StreamReader()
        if not cancel:
            reader.feed_eof()
        return reader, writer

    task = asyncio.create_task(TelegramVoiceTransport(connector=connector)(
        incoming(), artificial_ogg(), "nota.ogg", "audio/ogg", "", TOKEN, 2, lambda: None))
    assert await asyncio.wait_for(written.wait(), 2)
    if cancel:
        task.cancel()
    with pytest.raises(TelegramCancelled if cancel else TelegramError) as caught:
        await task
    assert caught.value.effect_status == "unknown"
    assert writer.writes == len(opens) == 1 and writer.closed
    assert TOKEN not in str(caught.value)


@pytest.mark.parametrize("optional", [False, True])
async def test_client_accepts_typed_voice_ack_with_optional_reported_fields(optional):
    original, uploads, commits = artificial_ogg(seconds=0.5), [], []
    digest = hashlib.sha256(original).hexdigest()

    async def upload(message, payload, name, mime, caption, token, timeout, before_dispatch):
        before_dispatch()
        uploads.append((message, payload, name, mime, caption))
        return response(voice_ack(optional=optional))

    client = TelegramClient(TOKEN, voice_transport=upload)
    client.identity = BotIdentity(BOT, "fixture_bot")
    sent = await client.send_voice(incoming(), original, "nota.ogg", "audio/ogg", "PIE 4826",
                                   before_dispatch=lambda: commits.append(1))
    assert sent == 501 and len(uploads) == len(commits) == 1
    assert uploads[0][1] == original and hashlib.sha256(original).hexdigest() == digest
    await client.aclose()


@pytest.mark.parametrize("change", [
    "chat", "chat_type", "thread", "reply", "bot", "not_bot", "duration",
    "file_id", "unique_id", "optional_mime", "optional_size", "message_id",
])
async def test_client_rejects_wrong_or_untyped_voice_ack_as_unknown_once(change):
    ack, posts = voice_ack(), []
    if change == "chat":
        ack["chat"]["id"] = CHAT
    elif change == "chat_type":
        ack["chat"]["type"] = "group"
    elif change == "thread":
        ack["message_thread_id"] = True
    elif change == "reply":
        ack["reply_to_message"]["message_id"] = 12
    elif change == "bot":
        ack["from"]["id"] = BOT + 1
    elif change == "not_bot":
        ack["from"]["is_bot"] = False
    elif change == "duration":
        ack["voice"]["duration"] = True
    elif change == "file_id":
        ack["voice"]["file_id"] = ""
    elif change == "unique_id":
        ack["voice"]["file_unique_id"] = None
    elif change == "optional_mime":
        ack["voice"]["mime_type"] = "text/plain"
    elif change == "optional_size":
        ack["voice"]["file_size"] = True
    else:
        ack["message_id"] = True

    async def upload(*args):
        args[-1]()
        posts.append(1)
        return response(ack)

    client = TelegramClient(TOKEN, voice_transport=upload)
    client.identity = BotIdentity(BOT, "fixture_bot")
    with pytest.raises(TelegramError, match="telegram_delivery_ack_invalid") as caught:
        await client.send_voice(incoming(), artificial_ogg(), "nota.ogg", "audio/ogg", "",
                                before_dispatch=lambda: None)
    assert caught.value.effect_status == "unknown" and len(posts) == 1
    assert TOKEN not in str(caught.value)
    await client.aclose()


@pytest.mark.parametrize("subtype", ["OPUS", "VORBIS"])
async def test_voice_requires_real_opus_decode_instead_of_just_an_ogg_header(subtype):
    payload = artificial_ogg(subtype=subtype)
    if subtype == "OPUS":
        duration = await validate_voice_payload(payload, "audio/ogg", guard=lambda: None)
        assert 0 < duration < 0.3
    else:
        with pytest.raises(TelegramError, match="telegram_voice_codec_invalid"):
            await validate_voice_payload(payload, "audio/ogg", guard=lambda: None)


@pytest.mark.parametrize("kind", ["mime", "header", "size"])
async def test_voice_invalid_format_header_or_size_fails_before_codec_or_wire(monkeypatch, kind):
    from jarvis_core import media_audio

    def forbidden(*args, **kwargs):
        raise AssertionError("Codec reached for ineligible source")

    monkeypatch.setattr(media_audio, "decode_ogg_to_wav", forbidden)
    payload, mime = b"OggS fake", "audio/ogg"
    if kind == "mime":
        mime = "audio/wav"
    elif kind == "header":
        payload = b"not an Ogg container"
    else:
        payload = b"OggS" + bytes(MAX_ATTACHMENT_BYTES)
    with pytest.raises(TelegramError) as caught:
        await validate_voice_payload(payload, mime, guard=lambda: None)
    assert caught.value.effect_status == "known"


async def test_codec_cancel_waits_for_join_and_never_commits_any_effect(monkeypatch):
    from jarvis_core import media_audio

    entered, release, cleaned = threading.Event(), threading.Event(), threading.Event()

    def codec(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        cleaned.set()
        raise MediaError("media_cancelled")

    monkeypatch.setattr(media_audio, "decode_ogg_to_wav", codec)
    task = asyncio.create_task(validate_voice_payload(b"OggS source", "audio/ogg",
                                                     guard=lambda: None))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not cleaned.is_set()
    finally:
        release.set()
    with pytest.raises(TelegramCancelled) as caught:
        await task
    assert cleaned.is_set() and caught.value.effect_status == "known"


async def test_voice_deadline_and_sha_mismatch_remain_local_known_failures():
    payload = artificial_ogg()
    with pytest.raises(MediaError, match="media_timeout"):
        await validate_voice_payload(payload, "audio/ogg", guard=lambda: None,
                                     deadline=time.monotonic() - 1)
    with pytest.raises(MediaError, match="media_attachment_changed"):
        await validate_voice_payload(payload, "audio/ogg", guard=lambda: None,
                                     expected_sha256="f" * 64)


async def test_voice_rejects_real_overlong_opus_without_upload_or_transcoding():
    payload = artificial_ogg(seconds=120.1)
    with pytest.raises(MediaError, match="media_audio_limit"):
        await validate_voice_payload(payload, "audio/ogg", guard=lambda: None)


async def test_client_context_revoked_during_preparation_never_calls_upload():
    uploads, current = [], [True]

    def preparation():
        if not current[0]:
            raise TelegramError("telegram_route_unavailable")
        current[0] = False

    async def upload(*args):
        uploads.append(1)
        raise AssertionError("Revoked context reached upload")

    client = TelegramClient(TOKEN, voice_transport=upload)
    client.identity = BotIdentity(BOT, "fixture_bot")
    with pytest.raises(TelegramError, match="telegram_route_unavailable") as caught:
        await client.send_voice(incoming(), artificial_ogg(), "nota.ogg", "audio/ogg", "",
                                before_dispatch=lambda: None, preparation_guard=preparation)
    assert not uploads and caught.value.effect_status == "known"
    await client.aclose()


@pytest.mark.parametrize("invalidation", ["revoked", "expired"])
async def test_real_grant_invalidated_at_wire_is_known_zero_bytes_and_does_not_pause(
    attachments, invalidation
):
    _root, store, scope = attachments
    manifest = store.ingest_bytes(
        scope, artificial_ogg(), "nota.ogg", "audio/ogg", attachment_id="c" * 32,
        guard=lambda: None,
    )
    args = {"attachment_id": manifest.attachment_id, "sha256": manifest.sha256,
            "delivery": "voice"}
    operation = ApprovalRequest("d" * 32, scope.session_id, "e" * 32, NAME,
                                canonical_arguments(args), "")
    operation = replace(operation, digest=operation_digest(operation))
    authority = ApprovalAuthority()
    ticket = await grant(authority, operation)
    issued = authority._issued[id(ticket)]
    writer, opens = MemoryWriter(), []

    async def connector(*_args, **_kwargs):
        # The original grant was valid through local decode. Only at the final
        # connection boundary do we revoke it or expire its actual lease.
        opens.append(1)
        if invalidation == "revoked":
            authority.revoke(scope.session_id)
        else:
            issued.deadline = time.monotonic() - 1
        return asyncio.StreamReader(), writer

    client = TelegramClient(TOKEN, voice_transport=TelegramVoiceTransport(connector=connector))
    client.identity = BotIdentity(BOT, "fixture_bot")
    channel = SimpleNamespace(client=client, error_code=None, _stop=asyncio.Event(),
                              _send_tickets={"unchanged"})
    ref = SessionRef(scope.session_id)
    service = SimpleNamespace(
        _telegram_run_context={operation.run_id: {
            "channel": channel, "incoming": incoming(), "ref": ref, "guard": lambda: None,
        }},
        _assert_run_client=lambda _run: None,
        _media_store=lambda: store,
        _media_scope=lambda _ref: scope,
    )
    try:
        with authority.execution_scope(operation, ticket):
            result = await TelegramReplyTools(service).execute(args, asyncio.Event())
        assert not result.succeeded and result.error == "tool_authority_revoked"
        assert result.effect_status == "known" and writer.writes == 0 and writer.closed
        assert opens == [1] and not channel._stop.is_set() and channel.error_code is None
        assert channel._send_tickets == {"unchanged"}
    finally:
        await client.aclose()
