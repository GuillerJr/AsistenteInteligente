"""Synthetic Telegram accounts over real loopback HTTP; no real token or bot calls."""

import asyncio
import json
import os
import ssl
import stat
from email import policy
from email.parser import BytesParser
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest

from jarvis_core.private_json import PrivateJsonError
from jarvis_core.telegram import (
    BotIdentity,
    TelegramChannel,
    TelegramClient,
    TelegramDispatchResult,
    TelegramError,
    TelegramHTTPResponse,
    TelegramHTTPTransport,
    TelegramJournal,
)

TOKEN = "12345:" + "A" * 35
BOT = 12345
CHAT = 777777
SESSION = "a" * 32
NOW = 1_900_000_000


def update(
    identity=1,
    *,
    chat=CHAT,
    sender=None,
    text="Consulta de prueba",
    date=NOW + 1,
    chat_type="private",
    is_bot=False,
    **message_fields,
):
    message = {
        "message_id": identity + 10,
        "date": date,
        "chat": {"id": chat, "type": chat_type},
        "from": {"id": chat if sender is None else sender, "is_bot": is_bot},
        "text": text,
        **message_fields,
    }
    return {"update_id": identity, "message": message}


class FakeBot:
    def __init__(self):
        self.updates = asyncio.Queue()
        self.requests = []
        self.connections = []
        self.tasks = set()
        self.mode = "normal"
        self.webhook = ""
        self.post_received = asyncio.Event()
        self.post_release = asyncio.Event()
        self.post_release.set()
        self.custom_body = None
        self.poll_count = 0
        self.files = {}
        self.chat_types = {}
        self.uploads = []

    async def start(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def connector(self, host, port, **options):
        assert host == "api.telegram.org" and port == 443
        assert options["server_hostname"] == "api.telegram.org"
        assert options["ssl"].check_hostname
        assert options["ssl"].verify_mode == ssl.CERT_REQUIRED
        self.connections.append((host, port))
        return await asyncio.open_connection("127.0.0.1", self.port)

    async def poll_updates(self, reader):
        # A cancelled old long poll must stop consuming queue entries before a
        # restarted client polls. Model the server-side disconnect explicitly.
        next_update = asyncio.create_task(self.updates.get())
        disconnected = asyncio.create_task(reader.read(1))
        try:
            done, _ = await asyncio.wait(
                (next_update, disconnected), return_when=asyncio.FIRST_COMPLETED
            )
            if disconnected in done:
                if next_update.done() and not next_update.cancelled():
                    self.updates.put_nowait(next_update.result())
                return None
            return next_update.result()
        finally:
            for task in (next_update, disconnected):
                if not task.done():
                    task.cancel()
            await asyncio.gather(next_update, disconnected, return_exceptions=True)

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            first, *lines = header.decode().split("\r\n")
            verb, path, version = first.split(" ")
            fields = {
                line.split(":", 1)[0].lower(): line.split(":", 1)[1].strip()
                for line in lines
                if ":" in line
            }
            assert fields["host"] == "api.telegram.org" and version == "HTTP/1.1"
            parts = urlsplit(path)
            api_method = parts.path.rsplit("/", 1)[-1]
            file_request = parts.path.startswith("/file/bot" + TOKEN + "/")
            assert file_request or parts.path.startswith("/bot" + TOKEN + "/")
            if verb == "POST":
                body = await reader.readexactly(int(fields["content-length"]))
                if fields.get("content-type", "").startswith("multipart/form-data;"):
                    multipart = BytesParser(policy=policy.default).parsebytes(
                        ("MIME-Version: 1.0\r\nContent-Type: " + fields["content-type"]
                         + "\r\n\r\n").encode() + body)
                    assert multipart.is_multipart()
                    parameters = {}
                    upload = {}
                    for part in multipart.iter_parts():
                        key = part.get_param("name", header="content-disposition")
                        if key == "document":
                            upload = {"name": part.get_filename(), "mime": part.get_content_type(),
                                      "data": part.get_payload(decode=True)}
                        else:
                            parameters[key] = part.get_payload(decode=True).decode()
                    for key in ("chat_id", "message_thread_id"):
                        if key in parameters:
                            parameters[key] = int(parameters[key])
                    self.uploads.append({**upload, "parameters": dict(parameters)})
                else:
                    parameters = json.loads(body)
            else:
                parameters = {key: values[0] for key, values in parse_qs(parts.query).items()}
            if file_request:
                api_method = "downloadFile"
                parameters = {"file_path": parts.path.split("/file/bot" + TOKEN + "/", 1)[1]}
            self.requests.append((verb, api_method, parameters))
            status, result = 200, {}
            payload = None
            if api_method == "getMe":
                result = {
                    "id": BOT if self.mode != "wrong-bot" else BOT + 1,
                    "is_bot": True,
                    "username": "fixture_bot",
                }
            elif api_method == "getWebhookInfo":
                result = {"url": self.webhook, "pending_update_count": 0}
            elif api_method == "getUpdates":
                self.poll_count += 1
                if self.mode == "poll-rate" and self.poll_count == 1:
                    status, result = 429, None
                elif self.mode == "poll-conflict":
                    status, result = 409, None
                else:
                    result = await self.poll_updates(reader)
                    if result is None:
                        return
            elif api_method == "sendMessage":
                self.post_received.set()
                await self.post_release.wait()
                if self.mode == "drop-post":
                    return
                if self.mode == "post-rate":
                    status, result = 429, None
                else:
                    result = {
                        "message_id": 500 if self.mode != "invalid-ack" else True,
                        "chat": {"id": parameters["chat_id"],
                                 "type": self.chat_types.get(parameters["chat_id"],
                                    "supergroup" if parameters["chat_id"] < 0 else "private")},
                    }
                    if "message_thread_id" in parameters:
                        result["message_thread_id"] = parameters["message_thread_id"]
            elif api_method == "getFile":
                row = self.files[parameters["file_id"]]
                result = {"file_id": parameters["file_id"], "file_unique_id": row["file_unique_id"],
                          "file_path": row["file_path"], "file_size": len(row["data"])}
            elif api_method == "downloadFile":
                row = next(row for row in self.files.values()
                           if row["file_path"] == parameters["file_path"])
                payload = row["data"]
            elif api_method == "sendDocument":
                self.post_received.set()
                await self.post_release.wait()
                if self.mode == "drop-document":
                    return
                document = self.uploads[-1]
                result = {"message_id": 501, "chat": {"id": parameters["chat_id"],
                    "type": self.chat_types.get(parameters["chat_id"],
                        "supergroup" if parameters["chat_id"] < 0 else "private")},
                    "document": {"file_name": document["name"],
                        "file_size": len(document["data"]) + (self.mode == "invalid-document-size"),
                        "file_id": "UPLOADED_SYNTHETIC", "file_unique_id": "UPLOADED_UNIQUE"}}
                if "message_thread_id" in parameters:
                    result["message_thread_id"] = parameters["message_thread_id"]
            else:
                raise AssertionError("unexpected API method")
            value = {"ok": True, "result": result}
            if status != 200:
                value = {
                    "ok": False,
                    "error_code": status,
                    "description": "NEVER LEAK " + TOKEN,
                    "parameters": {"retry_after": 1},
                }
            raw = payload if payload is not None else (
                self.custom_body if self.custom_body is not None else json.dumps(value).encode())
            if self.mode == "redirect":
                writer.write(
                    b"HTTP/1.1 302 Redirect\r\nLocation: https://evil.invalid/\r\n"
                    b"Content-Length: 0\r\n\r\n"
                )
            elif self.mode == "chunked":
                writer.write(
                    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
                    + f"{len(raw):x}\r\n".encode()
                    + raw
                    + b"\r\n0\r\n\r\n"
                )
            elif self.mode == "oversized":
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 999999999\r\n\r\n")
            elif self.mode == "ambiguous-framing":
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nTransfer-Encoding: chunked\r\n\r\nx"
                )
            else:
                writer.write(
                    f"HTTP/1.1 {status} Fixture\r\nContent-Type: application/json\r\n"
                    f"Content-Length: {len(raw)}\r\n\r\n".encode()
                    + raw
                )
            await writer.drain()
        except (asyncio.CancelledError, ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            self.tasks.discard(task)

    async def close(self):
        self.server.close()
        await self.server.wait_closed()
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def sent(self):
        return [row for row in self.requests if row[1] == "sendMessage"]


@pytest.fixture
async def bot():
    value = FakeBot()
    await value.start()
    try:
        yield value
    finally:
        await value.close()


@pytest.fixture
async def channel_factory(tmp_path, bot):
    channels = []

    async def create(dispatch=None, *, directory=None, close_timeout=1):
        if directory is None:
            directory = tmp_path.resolve() / f"channel{len(channels)}"
        journal = TelegramJournal(directory)
        if not journal.routes():
            journal.set_route(CHAT, "jarvis", SESSION)
        received = []

        async def default_dispatch(incoming):
            received.append(incoming)
            return TelegramDispatchResult(uuid4().hex, "completed", "known", "Respuesta de prueba")

        client = TelegramClient(TOKEN, transport=TelegramHTTPTransport(connector=bot.connector))
        channel = TelegramChannel(
            client,
            journal,
            dispatch or default_dispatch,
            clock=lambda: NOW,
            close_timeout=close_timeout,
        )
        channels.append(channel)
        await channel.connect()
        return channel, journal, received

    yield create
    for channel in channels:
        await channel.aclose()


async def final_event(channel, journal, *, identity=1):
    async with asyncio.timeout(4):
        while True:
            row = next((item for item in journal.history() if item["update_id"] == identity), None)
            if row and row["outcome"] != "pending":
                return row
            await asyncio.sleep(0.002)


async def test_connect_verifies_bot_and_webhook_but_never_starts_polling_or_sends(
    bot, channel_factory
):
    channel, journal, received = await channel_factory()
    assert not channel.running and journal.bot_id == BOT and not received
    assert [row[1] for row in bot.requests] == ["getMe", "getWebhookInfo"]
    assert channel.client.identity == BotIdentity(BOT, "fixture_bot")
    assert TOKEN not in repr(channel.client)
    await channel.aclose()
    assert channel.client._token is None and channel.client.identity is None


async def test_whitelisted_private_reply_uses_exact_destination_after_dispatch_receipt(
    bot, channel_factory
):
    entered, release = asyncio.Event(), asyncio.Event()
    received = []

    async def dispatch(incoming):
        received.append(incoming)
        entered.set()
        await release.wait()
        return TelegramDispatchResult("b" * 32, "completed", "known", "Respuesta autorizada")

    channel, journal, _ = await channel_factory(dispatch)
    await bot.updates.put([update()])
    channel.start(expected_configuration_hash=journal.configuration_hash())
    await asyncio.wait_for(entered.wait(), 2)
    assert not bot.sent()
    release.set()
    row = await final_event(channel, journal)
    assert row["status"] == "completed" and row["outgoing_message_id"] == 500
    assert row["run_id"] == "b" * 32 and row["reply_sha256"]
    assert len(bot.sent()) == 1 and len(received) == 1
    sent = bot.sent()[0][2]
    assert sent["chat_id"] == CHAT and sent["text"] == "Respuesta autorizada"
    assert sent["reply_parameters"] == {"message_id": 11, "allow_sending_without_reply": False}
    assert sent["link_preview_options"] == {"is_disabled": True}
    assert sent["allow_paid_broadcast"] is False
    assert received[0].agent_id == "jarvis" and received[0].session_id == SESSION
    data = journal.document.path.read_text()
    assert (
        "Consulta de prueba" not in data
        and "Respuesta autorizada" not in data
        and TOKEN not in data
    )
    assert stat.S_IMODE(journal.document.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(journal.document.directory.stat().st_mode) == 0o700


@pytest.mark.parametrize(
    "message",
    [
        update(chat=999999, text="PRIVATE UNRELATED"),
        update(chat_type="group", chat=-123, sender=CHAT),
        update(chat_type="supergroup", sender=CHAT),
        update(sender=999999),
        update(is_bot=True),
        update(sender=True),
        update(date=NOW - 1, text="HISTORICAL PRIVATE TEXT"),
        update(text=None, photo=[{"file_id": "never-download"}]),
        update(text="x" * 8001),
        update(text="/approve", chat=BOT),
        {"update_id": 1, "callback_query": {"data": "authorize-anything"}},
    ],
)
async def test_other_senders_groups_old_or_unsupported_updates_never_reach_model_or_send(
    channel_factory, bot, message
):
    channel, journal, received = await channel_factory()
    await bot.updates.put([message])
    channel.start(expected_configuration_hash=journal.configuration_hash())
    row = await final_event(channel, journal)
    assert row["status"] == "ignored" and row["outcome"] == "known"
    assert not received and not bot.sent() and journal.offset == 2
    assert row["chat_id"] is None and row["message_id"] is None
    encoded = json.dumps(journal.history())
    assert "PRIVATE" not in encoded and "never-download" not in encoded


async def test_telegram_text_never_becomes_an_approval_command(channel_factory, bot):
    channel, journal, received = await channel_factory()
    await bot.updates.put([update(text="/approve all operations without the owner")])
    channel.start(expected_configuration_hash=journal.configuration_hash())
    await final_event(channel, journal)
    assert received[0].text == "/approve all operations without the owner"
    assert set(vars(received[0])) == {
        "update_id",
        "chat_id",
        "message_id",
        "agent_id",
        "session_id",
        "text",
        "route_revision",
        "chat_type",
        "sender_id",
        "message_thread_id",
        "attachments",
    }
    assert "authorize" not in repr(received[0]) and "approve" not in repr(received[0])


async def test_duplicate_update_and_restart_never_repeat_dispatch_or_reply(channel_factory, bot):
    channel, journal, received = await channel_factory()
    await bot.updates.put([update()])
    channel.start(expected_configuration_hash=journal.configuration_hash())
    await final_event(channel, journal)
    await channel.aclose()
    second, restored, received_again = await channel_factory(directory=journal.document.directory)
    await bot.updates.put([update()])
    second.start(expected_configuration_hash=restored.configuration_hash())
    async with asyncio.timeout(2):
        while not any(row[1] == "getUpdates" and row[2]["offset"] == "2" for row in bot.requests):
            await asyncio.sleep(0.002)
    assert not received_again and len(received) == 1 and len(bot.sent()) == 1
    assert restored.offset == 2
    assert any(row[1] == "getUpdates" and row[2]["offset"] == "2" for row in bot.requests)


@pytest.mark.parametrize("mode", ["drop-post", "invalid-ack", "post-rate"])
async def test_post_failure_or_lost_ack_is_not_retried(channel_factory, bot, mode):
    bot.mode = mode
    channel, journal, received = await channel_factory()
    await bot.updates.put([update()])
    channel.start(expected_configuration_hash=journal.configuration_hash())
    row = await final_event(channel, journal)
    async with asyncio.timeout(2):
        while channel.running:
            await asyncio.sleep(0.002)
    assert row["status"] == "failed" and len(received) == 1 and len(bot.sent()) == 1
    assert row["outcome"] == ("known" if mode == "post-rate" else "unknown")
    assert channel.error_code and TOKEN not in channel.error_code
    await channel.stop()
    await bot.updates.put([update()])
    channel.start(expected_configuration_hash=journal.configuration_hash())
    await asyncio.sleep(0.05)
    assert len(received) == 1 and len(bot.sent()) == 1


async def test_cancel_before_dispatch_receipt_waits_for_cleanup_and_prevents_send(
    channel_factory, bot
):
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def dispatch(incoming):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    channel, journal, _ = await channel_factory(dispatch, close_timeout=0.03)
    await bot.updates.put([update()])
    channel.start(expected_configuration_hash=journal.configuration_hash())
    await asyncio.wait_for(entered.wait(), 2)
    with pytest.raises(TelegramError, match="telegram_cleanup_incomplete"):
        await channel.stop()
    assert cleaning.is_set() and not bot.sent()
    with pytest.raises(TelegramError, match="telegram_runner_unavailable"):
        journal.set_route(CHAT + 1, "jarvis", SESSION)
    release.set()
    await channel.stop()
    row = journal.history()[0]
    assert row["status"] == "cancelled" and row["outcome"] == "unknown"
    assert not bot.sent()


async def test_cancel_after_post_preserves_unknown_without_second_send(channel_factory, bot):
    bot.post_release.clear()
    channel, journal, _ = await channel_factory()
    await bot.updates.put([update()])
    channel.start(expected_configuration_hash=journal.configuration_hash())
    await asyncio.wait_for(bot.post_received.wait(), 2)
    await channel.stop()
    row = journal.history()[0]
    assert row["status"] == "cancelled" and row["outcome"] == "unknown"
    assert len(bot.sent()) == 1 and row["outgoing_message_id"] is None
    bot.post_release.set()


async def test_invalid_dispatch_result_never_sends_a_message(channel_factory, bot):
    async def dispatch(incoming):
        return {"status": "completed", "answer": "unbound output"}

    channel, journal, _ = await channel_factory(dispatch)
    await bot.updates.put([update()])
    channel.start(expected_configuration_hash=journal.configuration_hash())
    row = await final_event(channel, journal)
    assert row["status"] == "failed" and row["outcome"] == "unknown" and not bot.sent()


async def test_configuration_snapshot_and_single_runner_lease_are_enforced(channel_factory, bot):
    channel, journal, _ = await channel_factory()
    old_hash = journal.configuration_hash()
    journal.set_route(CHAT + 1, "jarvis", "b" * 32)
    with pytest.raises(TelegramError, match="telegram_configuration_changed"):
        channel.start(expected_configuration_hash=old_hash)
    channel.start(expected_configuration_hash=journal.configuration_hash())
    with pytest.raises(TelegramError, match="telegram_runner_unavailable"):
        journal.remove_route(CHAT, expected_revision=1)
    second, _, _ = await channel_factory(directory=journal.document.directory)
    with pytest.raises(TelegramError, match="telegram_runner_unavailable"):
        second.start(expected_configuration_hash=journal.configuration_hash())
    assert not bot.sent()


async def test_route_changed_out_of_band_during_dispatch_cannot_retarget_reply(
    channel_factory, bot
):
    async def dispatch(incoming):
        def malicious_change(value):
            value["routes"][str(CHAT)]["session_id"] = "b" * 32
            value["routes"][str(CHAT)]["revision"] += 1
            return value

        journal.document.update(malicious_change)
        return TelegramDispatchResult("c" * 32, "completed", "known", "No debe salir")

    channel, journal, _ = await channel_factory(dispatch)
    await bot.updates.put([update()])
    channel.start(expected_configuration_hash=journal.configuration_hash())
    row = await final_event(channel, journal)
    assert row["status"] == "failed" and not bot.sent()
    assert row["error_code"] == "telegram_configuration_changed"


async def test_review_cannot_start_a_connected_bot_against_a_different_journal_identity(
    channel_factory, bot
):
    channel, journal, received = await channel_factory()

    def change(value):
        value["bot_id"] = BOT + 1
        return value

    journal.document.update(change)
    with pytest.raises(TelegramError, match="telegram_bot_changed"):
        channel.start(expected_configuration_hash=journal.configuration_hash())
    assert not channel.running and not received and not bot.sent()
    assert all(row[1] != "getUpdates" for row in bot.requests)
    descriptor = journal.acquire_runner()
    os.close(descriptor)


async def test_replaced_runner_inode_cannot_send_a_completed_dispatch_result(channel_factory, bot):
    async def dispatch(incoming):
        lock = journal.document.directory / "telegram.runner.lock"
        lock.unlink()
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        return TelegramDispatchResult("d" * 32, "completed", "known", "No debe salir")

    channel, journal, _ = await channel_factory(dispatch)
    await bot.updates.put([update()])
    channel.start(expected_configuration_hash=journal.configuration_hash())
    row = await final_event(channel, journal)
    assert row["status"] == "failed" and row["error_code"] == "telegram_runner_changed"
    assert not bot.sent() and row["outgoing_message_id"] is None


async def test_read_rate_limit_has_finite_get_only_retry(channel_factory, bot):
    bot.mode = "poll-rate"
    channel, journal, received = await channel_factory()
    await bot.updates.put([update()])
    channel.start(expected_configuration_hash=journal.configuration_hash())
    row = await final_event(channel, journal)
    assert row["status"] == "completed" and bot.poll_count >= 2
    assert len(received) == 1 and len(bot.sent()) == 1


async def test_poll_conflict_stops_without_deleting_webhook_or_sending(channel_factory, bot):
    bot.mode = "poll-conflict"
    channel, _, received = await channel_factory()
    channel.start(expected_configuration_hash=channel.journal.configuration_hash())
    async with asyncio.timeout(2):
        while channel.error_code is None:
            await asyncio.sleep(0.002)
    assert channel.error_code == "telegram_webhook_conflict" and not received
    assert not bot.sent() and not any(row[1] == "deleteWebhook" for row in bot.requests)


@pytest.mark.parametrize("mode", ["redirect", "oversized", "ambiguous-framing"])
async def test_http_limits_and_redirects_fail_closed_without_following(bot, mode):
    bot.mode = mode
    client = TelegramClient(TOKEN, transport=TelegramHTTPTransport(connector=bot.connector))
    with pytest.raises(TelegramError) as error:
        await client.connect()
    assert TOKEN not in str(error.value) and TOKEN not in repr(error.value)
    assert len(bot.connections) == 1 and client.identity is None
    await client.aclose()


async def test_chunked_json_response_is_supported_without_using_an_account(bot):
    bot.mode = "chunked"
    client = TelegramClient(TOKEN, transport=TelegramHTTPTransport(connector=bot.connector))
    assert (await client.connect()).bot_id == BOT
    await client.aclose()


@pytest.mark.parametrize(
    "raw",
    [
        b"{}",
        b'{"ok":true,"ok":false,"result":{}}',
        b"[]",
        b"{invalid",
        b"\xff",
        b'{"ok":"true","result":{}}',
    ],
)
async def test_invalid_json_never_becomes_connected_identity(bot, raw):
    bot.custom_body = raw
    client = TelegramClient(TOKEN, transport=TelegramHTTPTransport(connector=bot.connector))
    with pytest.raises(TelegramError):
        await client.connect()
    assert client.identity is None
    await client.aclose()


async def test_existing_webhook_and_wrong_bot_identity_are_not_changed(bot):
    for mode, webhook in (("normal", "https://private.invalid/webhook"), ("wrong-bot", "")):
        bot.mode, bot.webhook = mode, webhook
        client = TelegramClient(TOKEN, transport=TelegramHTTPTransport(connector=bot.connector))
        with pytest.raises(TelegramError):
            await client.connect()
        assert client.identity is None
        await client.aclose()
    assert not any(row[1] == "deleteWebhook" for row in bot.requests)


@pytest.mark.parametrize(
    "token",
    [
        None,
        "",
        "bad",
        "12345:short",
        "0:" + "A" * 35,
        "12345:" + "A" * 35 + "/getMe",
        "12345:" + "A" * 35 + "\n",
    ],
)
def test_invalid_tokens_fail_before_transport_and_are_not_echoed(token):
    with pytest.raises(TelegramError, match="telegram_token_invalid") as error:
        TelegramClient(token)
    if isinstance(token, str) and len(token) > 5:
        assert token not in str(error.value)


def test_journal_route_compare_and_swap_no_group_or_invalid_session(tmp_path):
    journal = TelegramJournal(tmp_path.resolve() / "journal")
    row = journal.set_route(CHAT, "jarvis", SESSION)
    assert row.revision == 1
    with pytest.raises(TelegramError, match="telegram_route_revision_conflict"):
        journal.set_route(CHAT, "jarvis", "b" * 32)
    updated = journal.set_route(CHAT, "jarvis", "b" * 32, expected_revision=1)
    assert updated.revision == 2
    for chat_id, agent, session_id in (
        (-1, "jarvis", SESSION),
        (True, "jarvis", SESSION),
        (CHAT, "../other", SESSION),
        (CHAT, "jarvis", "bad"),
    ):
        with pytest.raises(TelegramError):
            journal.set_route(chat_id, agent, session_id)
    journal.remove_route(CHAT, expected_revision=2)
    assert not journal.routes()


def test_journal_recovery_marks_only_inflight_unknown_without_replaying(tmp_path):
    from jarvis_core.telegram import IncomingMessage

    directory = tmp_path.resolve() / "journal"
    journal = TelegramJournal(directory)
    journal.bind_bot(BotIdentity(BOT))
    journal.set_route(CHAT, "jarvis", SESSION)
    incoming = IncomingMessage(1, CHAT, 11, "jarvis", SESSION, "NEVER PERSIST THIS")
    assert journal.claim(1, incoming)
    journal.transition(
        1, status="send_pending", outcome="pending", run_id="b" * 32, reply_sha256="c" * 64
    )
    restored = TelegramJournal(directory)
    restored.recover_unfinished()
    row = restored.history()[0]
    assert row["status"] == "interrupted" and row["outcome"] == "unknown"
    assert not restored.claim(1, incoming) and restored.offset == 2
    assert "NEVER PERSIST" not in restored.document.path.read_text()
    with pytest.raises(TelegramError, match="telegram_bot_changed"):
        restored.bind_bot(BotIdentity(BOT + 1))


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "public"])
def test_journal_unsafe_files_fail_closed(tmp_path, kind):
    directory = tmp_path.resolve() / "journal"
    journal = TelegramJournal(directory)
    journal.set_route(CHAT, "jarvis", SESSION)
    path = journal.document.path
    outside = tmp_path / "outside"
    outside.write_bytes(path.read_bytes())
    outside.chmod(0o600)
    path.unlink()
    if kind == "symlink":
        path.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, path)
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.write_bytes(outside.read_bytes())
        path.chmod(0o644)
    with pytest.raises(PrivateJsonError):
        journal.history()


async def test_large_unicode_answer_is_truncated_without_second_post(channel_factory, bot):
    async def dispatch(incoming):
        return TelegramDispatchResult("b" * 32, "completed", "known", "😀" * 3000)

    channel, journal, _ = await channel_factory(dispatch)
    await bot.updates.put([update()])
    channel.start(expected_configuration_hash=journal.configuration_hash())
    row = await final_event(channel, journal)
    assert row["status"] == "completed" and row["reply_truncated"]
    assert len(bot.sent()) == 1
    text = bot.sent()[0][2]["text"]
    assert "Respuesta truncada" in text and len(text.encode("utf-16-le")) // 2 <= 4096


def test_dispatch_result_requires_a_bound_run_and_safe_status():
    for run_id, status, outcome in (
        (None, "completed", "known"),
        ("bad", "completed", "known"),
        (None, [], "known"),
        (None, "failed", []),
    ):
        with pytest.raises(TelegramError):
            TelegramDispatchResult(run_id, status, outcome, "Output")


@pytest.mark.parametrize("method", ["update", "remove"])
def test_route_revision_booleans_never_match_integer_revision(tmp_path, method):
    journal = TelegramJournal(tmp_path.resolve() / "journal")
    journal.set_route(CHAT, "jarvis", SESSION)
    with pytest.raises(TelegramError, match="telegram_identity_invalid"):
        if method == "update":
            journal.set_route(CHAT, "jarvis", SESSION, expected_revision=True)
        else:
            journal.remove_route(CHAT, expected_revision=True)
    assert journal.routes()[0].revision == 1


async def test_recovery_cannot_interrupt_another_live_runner(channel_factory, bot):
    channel, journal, _ = await channel_factory()
    channel.start(expected_configuration_hash=journal.configuration_hash())
    with pytest.raises(TelegramError, match="telegram_runner_unavailable"):
        journal.recover_unfinished()
    assert channel.running and not bot.sent()


async def test_connect_has_one_total_deadline_without_retrying_or_leaking_token():
    calls = []

    async def slow_transport(method, api, parameters, token, timeout, before_dispatch):
        calls.append(api)
        await asyncio.sleep(0.7)
        result = {"id": BOT, "is_bot": True} if api == "getMe" else {"url": ""}
        return TelegramHTTPResponse(200, json.dumps({"ok": True, "result": result}).encode())

    client = TelegramClient(TOKEN, transport=slow_transport, timeout=1)
    with pytest.raises(TelegramError, match="telegram_connect_timeout"):
        await client.connect()
    assert calls == ["getMe", "getWebhookInfo"] and client.identity is None
    await client.aclose()


async def test_callback_exception_after_dispatch_is_unknown_and_never_replied(channel_factory, bot):
    async def dispatch(incoming):
        raise TelegramError("synthetic_callback_failed")

    channel, journal, _ = await channel_factory(dispatch)
    await bot.updates.put([update()])
    channel.start(expected_configuration_hash=journal.configuration_hash())
    row = await final_event(channel, journal)
    assert row["status"] == "failed" and row["outcome"] == "unknown" and not bot.sent()


async def test_transport_exception_does_not_leak_token_or_private_url():
    async def broken(*args):
        raise RuntimeError("https://api.telegram.org/bot" + TOKEN + "/getMe?private=data")

    client = TelegramClient(TOKEN, transport=broken)
    with pytest.raises(TelegramError) as error:
        await client.connect()
    assert str(error.value) == "telegram_transport_failed" and TOKEN not in repr(error.value)
    await client.aclose()


async def test_randomized_update_id_after_restart_does_not_drop_fresh_private_message(
    channel_factory, bot
):
    channel, journal, first = await channel_factory()
    await bot.updates.put([update(1000, message_id=50)])
    channel.start(expected_configuration_hash=journal.configuration_hash())
    await final_event(channel, journal, identity=1000)
    await channel.aclose()
    second, restored, fresh = await channel_factory(directory=journal.document.directory)
    await bot.updates.put([update(2, message_id=51, text="Nueva época")])
    second.start(expected_configuration_hash=restored.configuration_hash())
    row = await final_event(second, restored, identity=2)
    assert row["status"] == "completed" and row["epoch"] == 1
    assert restored.offset == 3 and len(first) == len(fresh) == 1 and len(bot.sent()) == 2
    assert len(restored.history()) == 2


async def test_same_private_message_is_not_replayed_under_a_new_update_generation(
    channel_factory, bot
):
    channel, journal, first = await channel_factory()
    await bot.updates.put([update(1000, message_id=50)])
    channel.start(expected_configuration_hash=journal.configuration_hash())
    await final_event(channel, journal, identity=1000)
    await channel.aclose()
    second, restored, fresh = await channel_factory(directory=journal.document.directory)
    await bot.updates.put([update(2, message_id=50)])
    second.start(expected_configuration_hash=restored.configuration_hash())
    await asyncio.sleep(0.05)
    assert len(first) == 1 and not fresh and len(bot.sent()) == 1
    assert restored.offset == 1001 and len(restored.history()) == 1
