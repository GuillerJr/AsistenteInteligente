"""Groups/files over our loopback Bot API; no credentials or real Telegram."""

import asyncio
import json
from dataclasses import asdict

import pytest
from test_telegram import BOT, CHAT, SESSION, TOKEN, final_event, update
from test_telegram import bot as bot
from test_telegram import channel_factory as channel_factory

from jarvis_core.telegram import (
    BotIdentity,
    IncomingMessage,
    TelegramCancelled,
    TelegramClient,
    TelegramError,
    TelegramHTTPResponse,
    TelegramHTTPTransport,
    TelegramJournal,
    _route,
)
from jarvis_core.telegram_content import (
    MAX_ATTACHMENT_BYTES,
    TelegramAttachment,
    addressed_to_bot,
    attachments_from_message,
)
from jarvis_core.telegram_files import TelegramDocumentTransport, validate_file_path

GROUP = -100777777
TEXT = "🧪 @fixture_bot consulta"
ENTITY = {"type": "mention", "offset": 3, "length": len("@fixture_bot")}


async def group_channel(channel_factory, *, thread=9, attachments=False):
    channel, journal, received = await channel_factory()
    journal.set_route(
        GROUP,
        "jarvis",
        SESSION,
        chat_type="supergroup",
        allowed_sender_ids=[CHAT],
        message_thread_id=thread,
        allow_attachments=attachments,
    )
    return channel, journal, received


async def test_group_requires_reviewed_sender_address_and_exact_topic(channel_factory, bot):
    channel, journal, received = await group_channel(channel_factory)
    cases = [
        update(
            1,
            chat=GROUP,
            chat_type="supergroup",
            sender=CHAT,
            text="sin mención",
            message_thread_id=9,
        ),
        update(
            2,
            chat=GROUP,
            chat_type="supergroup",
            sender=CHAT + 1,
            text=TEXT,
            entities=[ENTITY],
            message_thread_id=9,
        ),
        update(
            3,
            chat=GROUP,
            chat_type="supergroup",
            sender=CHAT,
            text=TEXT,
            entities=[ENTITY],
            message_thread_id=8,
        ),
        update(
            4,
            chat=GROUP,
            chat_type="supergroup",
            sender=CHAT,
            text=TEXT,
            entities=[ENTITY],
            message_thread_id=9,
        ),
    ]
    await bot.updates.put(cases)
    channel.start(expected_configuration_hash=journal.configuration_hash())
    final = await final_event(channel, journal, identity=4)
    assert final["status"] == "completed" and final["sender_id"] == CHAT
    assert final["chat_type"] == "supergroup" and final["message_thread_id"] == 9
    assert final["attachment_count"] == 0
    assert [v.text for v in received] == [TEXT]
    history = {v["update_id"]: v for v in journal.history()}
    assert [history[v]["error_code"] for v in (1, 2, 3)] == [
        "telegram_mention_required",
        "telegram_sender_ignored",
        "telegram_thread_ignored",
    ]
    parameters = bot.sent()[0][2]
    assert parameters["chat_id"] == GROUP and parameters["message_thread_id"] == 9
    assert parameters["reply_parameters"]["message_id"] == 14


@pytest.mark.parametrize(
    "sender_chat,reply_bot,addressed",
    [
        (None, BOT, True),
        (None, BOT + 1, False),
        ({"id": GROUP}, BOT, False),
    ],
)
async def test_group_reply_addresses_only_this_bot_and_not_anonymous_sender(
    channel_factory,
    bot,
    sender_chat,
    reply_bot,
    addressed,
):
    channel, journal, received = await group_channel(channel_factory)
    message = update(
        chat=GROUP,
        chat_type="supergroup",
        sender=CHAT,
        text="respuesta",
        message_thread_id=9,
        reply_to_message={"from": {"id": reply_bot, "is_bot": True}, "chat": {"id": GROUP}},
    )
    if sender_chat:
        message["message"]["sender_chat"] = sender_chat
    await bot.updates.put([message])
    channel.start(expected_configuration_hash=journal.configuration_hash())
    row = await final_event(channel, journal)
    assert (row["status"] == "completed") is addressed
    assert bool(received) is addressed and bool(bot.sent()) is addressed


@pytest.mark.parametrize(
    "offset,length,kind,expected",
    [
        (3, 12, "mention", True),
        (1, 12, "mention", False),
        (2, 12, "mention", False),
        (3, 11, "mention", False),
        (3, 100, "mention", False),
        (True, 12, "mention", False),
        (3, 12, "url", False),
    ],
)
def test_group_entities_use_bounded_utf16_not_substring_or_character_offsets(
    offset,
    length,
    kind,
    expected,
):
    message = {"text": TEXT, "entities": [{"type": kind, "offset": offset, "length": length}]}
    assert addressed_to_bot(message, TEXT, BotIdentity(BOT, "fixture_bot")) is expected


def test_route_extends_legacy_only_with_exact_group_policy_and_typed_members(tmp_path):
    journal = TelegramJournal(tmp_path.resolve() / "telegram")
    legacy = journal.set_route(CHAT, "jarvis", SESSION)
    assert set(journal.document.read()["routes"][str(CHAT)]) == {
        "chat_id",
        "agent_id",
        "session_id",
        "enabled",
        "revision",
    }
    current = journal.set_route(
        GROUP,
        "jarvis",
        SESSION,
        chat_type="supergroup",
        allowed_sender_ids=[CHAT],
        message_thread_id=9,
        allow_attachments=True,
    )
    assert journal.routes() == (current, legacy)
    for changed in (
        {"allowed_sender_ids": []},
        {"allowed_sender_ids": [CHAT, CHAT]},
        {"allowed_sender_ids": [True]},
        {"allowed_sender_ids": [[CHAT]]},
        {"message_thread_id": True},
        {"chat_type": []},
        {"chat_id": CHAT},
    ):
        with pytest.raises(TelegramError):
            _route({**asdict(current), **changed})
    before = journal.document.path.read_bytes()
    with pytest.raises(TelegramError):
        journal.set_route(GROUP, "jarvis", SESSION, chat_type="group", allowed_sender_ids=[])
    assert journal.document.path.read_bytes() == before


async def test_attachment_route_is_opt_in_and_journal_keeps_only_count(channel_factory, bot):
    channel, journal, received = await channel_factory()
    descriptor = {
        "file_id": "SYNTHETIC_FILE",
        "file_unique_id": "SYNTHETIC_UNIQUE",
        "file_name": "fixture.txt",
        "mime_type": "text/plain",
        "file_size": 4,
    }
    await bot.updates.put([update(document=descriptor)])
    channel.start(expected_configuration_hash=journal.configuration_hash())
    row = await final_event(channel, journal)
    assert row["error_code"] == "telegram_attachment_not_authorized" and not received
    assert not any(v[1] in {"getFile", "downloadFile"} for v in bot.requests)
    await channel.stop()
    journal.set_route(CHAT, "jarvis", SESSION, expected_revision=1, allow_attachments=True)
    await bot.updates.put([update(2, document=descriptor)])
    channel.start(expected_configuration_hash=journal.configuration_hash())
    row = await final_event(channel, journal, identity=2)
    assert row["status"] == "completed" and row["attachment_count"] == 1
    assert len(received[0].attachments) == 1
    assert "SYNTHETIC_FILE" not in journal.document.path.read_text()
    assert "fixture.txt" not in json.dumps(journal.history())


def test_supported_media_metadata_selects_one_bounded_file():
    small = {"file_id": "small", "file_unique_id": "small_unique", "file_size": 9}
    large = {
        "file_id": "large",
        "file_unique_id": "large_unique",
        "file_size": MAX_ATTACHMENT_BYTES + 1,
    }
    (attachment,) = attachments_from_message({"photo": [small, large]})
    assert attachment.file_id == "small" and attachment.mime_type == "image/jpeg"
    for invalid in (
        {"photo": []},
        {"photo": [large]},
        {"document": {**small, "file_name": "../private.txt"}},
        {"document": small, "voice": small},
        {"voice": {**small, "duration": 301}},
    ):
        with pytest.raises(TelegramError):
            attachments_from_message(invalid)


async def test_file_download_uses_only_fixed_verified_host_and_same_file_identity(bot):
    data = b"MARCADOR ARCHIVO TELEGRAM"
    bot.files["fixture_file"] = {
        "file_unique_id": "fixture_unique",
        "file_path": "documents/file_1.txt",
        "data": data,
    }
    client = TelegramClient(TOKEN, transport=TelegramHTTPTransport(connector=bot.connector))
    await client.connect()
    descriptor = TelegramAttachment(
        "fixture_file", "fixture_unique", "fixture.txt", "text/plain", len(data), "document"
    )
    guards = []
    try:
        assert (
            await client.download_attachment(descriptor, before_dispatch=lambda: guards.append(1))
            == data
        )
        assert [v[1] for v in bot.requests][-2:] == ["getFile", "downloadFile"]
        assert len(guards) >= 4
        assert TOKEN not in repr(client) and "fixture_file" not in repr(descriptor)
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/file",
        "../file",
        "documents/../file",
        "file%2fsecret",
        "https://evil.invalid/file",
        "documents/file?token=x",
        "a//b",
    ],
)
def test_download_rejects_urls_local_paths_and_ambiguous_path_components(path):
    with pytest.raises(TelegramError, match="telegram_attachment_path_invalid"):
        validate_file_path(path)


async def test_revoked_download_guard_prevents_getfile_or_file_get(bot):
    data = b"ARCHIVO"
    bot.files["fixture_file"] = {
        "file_unique_id": "fixture_unique",
        "file_path": "documents/file_1.txt",
        "data": data,
    }
    client = TelegramClient(TOKEN, transport=TelegramHTTPTransport(connector=bot.connector))
    await client.connect()
    descriptor = TelegramAttachment(
        "fixture_file", "fixture_unique", "fixture.txt", "text/plain", len(data), "document"
    )

    def revoked():
        raise TelegramError("telegram_route_unavailable")

    before = len(bot.requests)
    try:
        with pytest.raises(TelegramError, match="telegram_route_unavailable"):
            await client.download_attachment(descriptor, before_dispatch=revoked)
        assert len(bot.requests) == before
    finally:
        await client.aclose()


async def test_file_transport_ack_mismatch_and_cap_fail_without_exposing_tokens(bot):
    client = TelegramClient(
        TOKEN,
        transport=TelegramHTTPTransport(connector=bot.connector),
        file_transport=lambda *_args: asyncio.sleep(0, result=TelegramHTTPResponse(200, b"bad")),
    )
    await client.connect()
    bot.files["fixture_file"] = {
        "file_unique_id": "fixture_unique",
        "file_path": "documents/file_1.txt",
        "data": b"longer",
    }
    descriptor = TelegramAttachment(
        "fixture_file", "fixture_unique", "fixture.txt", "text/plain", 6, "document"
    )
    try:
        with pytest.raises(TelegramError, match="telegram_attachment_bytes_invalid") as caught:
            await client.download_attachment(descriptor, before_dispatch=lambda: None)
        assert TOKEN not in str(caught.value)
    finally:
        await client.aclose()


async def test_group_control_command_is_never_a_model_message_after_expiry(channel_factory, bot):
    channel, journal, received = await group_channel(channel_factory)
    await bot.updates.put(
        [
            update(
                chat=GROUP,
                chat_type="supergroup",
                sender=CHAT,
                text="/jarvis_pair@fixture_bot synthetic_nonce",
                message_thread_id=9,
                entities=[
                    {"type": "bot_command", "offset": 0, "length": len("/jarvis_pair@fixture_bot")}
                ],
            )
        ]
    )
    channel.start(expected_configuration_hash=journal.configuration_hash())
    row = await final_event(channel, journal)
    assert row["error_code"] == "telegram_control_ignored" and not received and not bot.sent()
    assert "synthetic_nonce" not in journal.document.path.read_text()


def test_group_physical_control_tombstone_survives_reopen_and_changed_payload(tmp_path):
    root = tmp_path.resolve() / "telegram"
    journal = TelegramJournal(root)
    journal.mark_control_message(GROUP, 17)
    reopened = TelegramJournal(root)
    changed = IncomingMessage(
        9, GROUP, 17, "jarvis", SESSION, "contenido distinto", 2, "supergroup", CHAT, 9
    )
    assert not reopened.claim(9, changed, permit_epoch_change=True)
    assert reopened.is_control_message(GROUP, 17)
    assert not reopened.history()


async def test_document_upload_is_one_exact_threaded_multipart_reply(bot):
    client = TelegramClient(TOKEN, transport=TelegramHTTPTransport(connector=bot.connector))
    await client.connect()
    incoming = IncomingMessage(1, GROUP, 11, "jarvis", SESSION, "archivo", 1, "supergroup", CHAT, 9)
    payload, guards = b"DOCUMENTO SINTETICO 7314", []
    try:
        sent = await client.send_document(
            incoming,
            payload,
            "prueba.txt",
            "text/plain",
            "Pie 7314",
            before_dispatch=lambda: guards.append(1),
        )
        assert sent == 501 and len(guards) == len(bot.uploads) == 1
        uploaded = bot.uploads[0]
        assert uploaded["data"] == payload and uploaded["name"] == "prueba.txt"
        assert uploaded["mime"] == "text/plain"
        assert uploaded["parameters"]["chat_id"] == GROUP
        assert uploaded["parameters"]["message_thread_id"] == 9
        assert uploaded["parameters"]["caption"] == "Pie 7314"
        assert json.loads(uploaded["parameters"]["reply_parameters"]) == {
            "message_id": 11,
            "allow_sending_without_reply": False,
        }
        assert uploaded["parameters"]["allow_paid_broadcast"] == "false"
    finally:
        await client.aclose()


@pytest.mark.parametrize("mode", ["drop-document", "invalid-document-size"])
async def test_document_lost_or_wrong_ack_is_unknown_and_never_retried(bot, mode):
    client = TelegramClient(TOKEN, transport=TelegramHTTPTransport(connector=bot.connector))
    await client.connect()
    bot.mode = mode
    incoming = IncomingMessage(1, CHAT, 11, "jarvis", SESSION, "archivo")
    try:
        with pytest.raises(TelegramError) as caught:
            await client.send_document(
                incoming, b"UNA VEZ", "prueba.txt", "text/plain", "", before_dispatch=lambda: None
            )
        assert caught.value.effect_status == "unknown" and TOKEN not in str(caught.value)
        assert len(bot.uploads) == 1
        assert len([row for row in bot.requests if row[1] == "sendDocument"]) == 1
    finally:
        await client.aclose()


async def test_cancelled_document_upload_retains_unknown_outcome_without_second_request(bot):
    client = TelegramClient(TOKEN, transport=TelegramHTTPTransport(connector=bot.connector))
    await client.connect()
    bot.post_release.clear()
    incoming = IncomingMessage(1, CHAT, 11, "jarvis", SESSION, "archivo")
    task = asyncio.create_task(
        client.send_document(
            incoming, b"UNA VEZ", "prueba.txt", "text/plain", "", before_dispatch=lambda: None
        )
    )
    try:
        await asyncio.wait_for(bot.post_received.wait(), 3)
        task.cancel()
        with pytest.raises(TelegramCancelled) as caught:
            await task
        assert caught.value.effect_status == "unknown" and len(bot.uploads) == 1
    finally:
        bot.post_release.set()
        await asyncio.gather(task, return_exceptions=True)
        await client.aclose()


async def test_document_guard_is_rechecked_after_connect_and_before_any_wire_bytes(bot):
    opened, release = asyncio.Event(), asyncio.Event()
    revoked = [False]

    async def delayed_connector(*args, **kwargs):
        result = await bot.connector(*args, **kwargs)
        opened.set()
        await release.wait()
        return result

    def guard():
        if revoked[0]:
            raise TelegramError("telegram_delivery_not_authorized")

    client = TelegramClient(
        TOKEN,
        transport=TelegramHTTPTransport(connector=bot.connector),
        document_transport=TelegramDocumentTransport(connector=delayed_connector),
    )
    await client.connect()
    baseline = len(bot.requests)
    incoming = IncomingMessage(1, CHAT, 11, "jarvis", SESSION, "archivo")
    task = asyncio.create_task(
        client.send_document(
            incoming, b"NO ENVIAR", "prueba.txt", "text/plain", "", before_dispatch=guard
        )
    )
    try:
        await asyncio.wait_for(opened.wait(), 3)
        revoked[0] = True
        release.set()
        with pytest.raises(TelegramError, match="telegram_delivery_not_authorized") as caught:
            await task
        assert caught.value.effect_status == "known"
        assert len(bot.requests) == baseline and not bot.uploads
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await client.aclose()
