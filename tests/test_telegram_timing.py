"""Virtual loop deadlines with owned HTTP streams; no minimum sleep or internet."""

import asyncio
import json
from unittest.mock import patch

import pytest
from test_service_telegram import telegram_service as telegram_service
from test_telegram import BOT, TOKEN

from jarvis_core.protocol import RpcError
from jarvis_core.telegram import TelegramClient, TelegramError, TelegramHTTPTransport


def response(result):
    body = json.dumps({"ok": True, "result": result}).encode()
    return f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body


async def turns_until(predicate):
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0)  # Yield reactor turns; no elapsed-time assumption.
    raise AssertionError("The controlled event was not reached")


class OwnedWriter:
    def __init__(self, *, hold_close=False):
        self.transport = self
        self.hold_close = hold_close
        self.waiting = asyncio.Event()
        self.released = asyncio.Event()
        self.closed = self.aborted = False
        self.writes = []

    def write(self, data):
        self.writes.append(data)

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    def abort(self):
        self.aborted = True
        self.released.set()

    async def wait_closed(self):
        self.waiting.set()
        if self.hold_close:
            await self.released.wait()


class ControlledHTTP:
    def __init__(self, result, *, near_deadline=True):
        self.loop = asyncio.get_running_loop()
        self.beginning = self.loop.time()
        self.clock = [self.beginning]
        self.result, self.near_deadline = result, near_deadline
        self.connections = []
        self.writer = OwnedWriter(hold_close=True)
        self.extra = OwnedWriter()
        self.transport = TelegramHTTPTransport(connector=self.connector)

    async def connector(self, host, port, **options):
        assert host == "api.telegram.org" and port == 443
        assert options["ssl"].check_hostname and options["ssl_shutdown_timeout"] == 1
        self.connections.append(host)
        reader = asyncio.StreamReader()
        if len(self.connections) == 1:
            if self.near_deadline:
                self.clock[0] = self.beginning + 19.5
            reader.feed_data(response(self.result))
            return reader, self.writer
        reader.feed_data(response({"url": ""}))
        return reader, self.extra

    def timing(self):
        return patch.object(self.loop, "time", lambda: self.clock[0])

    def expire(self):
        self.clock[0] = self.beginning + 20.1


async def test_total_connect_deadline_during_http_close_never_starts_second_wire():
    http = ControlledHTTP({"id": BOT, "is_bot": True, "username": "fixture_bot"})
    client = TelegramClient(TOKEN, transport=http.transport)
    with http.timing():
        task = asyncio.create_task(client.connect())
        try:
            await turns_until(http.writer.waiting.is_set)
            http.expire()
            await turns_until(task.done)
            with pytest.raises(TelegramError, match="telegram_connect_timeout"):
                await task
            assert len(http.connections) == 1 and len(http.writer.writes) == 1
            assert http.writer.closed and http.writer.aborted and client.identity is None
        finally:
            http.writer.released.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await client.aclose()


async def test_rpc_connect_finishes_before_30_and_clears_token_after_total_timeout(
    telegram_service,
):
    service, rpc, peer, _bot, _root = telegram_service
    assert rpc._timeout == service.server._request_timeout == 30
    http = ControlledHTTP({"id": BOT, "is_bot": True, "username": "fixture_bot"})
    clients = []

    def factory(token):
        client = TelegramClient(token, transport=http.transport)
        clients.append(client)
        return client

    service.initialize_telegram(client_factory=factory)
    with http.timing():
        task = asyncio.create_task(rpc.call("telegram.connect", {"token": TOKEN}))
        try:
            await turns_until(http.writer.waiting.is_set)
            http.expire()
            await turns_until(task.done)
            with pytest.raises(RpcError, match="telegram_connect_timeout"):
                await task
            assert http.clock[0] - http.beginning < 25
            assert len(http.connections) == 1 and http.writer.aborted
            assert clients[0].closed and clients[0]._token is None
            assert service._telegram_channel is None and not service._maintenance
            assert not service._telegram_mutating and not peer.calls
        finally:
            http.writer.released.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_handler_cancellation_during_http_close_is_not_converted_to_connection_success(
    telegram_service,
):
    service, _rpc, peer, _bot, _root = telegram_service
    http = ControlledHTTP(
        {"id": BOT, "is_bot": True, "username": "fixture_bot"}, near_deadline=False
    )
    clients = []

    def factory(token):
        client = TelegramClient(token, transport=http.transport)
        clients.append(client)
        return client

    service.initialize_telegram(client_factory=factory)
    connecting = asyncio.create_task(service.telegram_connect({"token": TOKEN}))
    await turns_until(http.writer.waiting.is_set)
    connecting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await connecting
    assert len(http.connections) == 1 and http.writer.closed and http.writer.aborted
    assert clients[0].closed and clients[0]._token is None and service._telegram_channel is None
    assert not service._maintenance and not peer.calls


async def test_pairing_start_preflight_has_total_20_rpc_budget_and_releases_lease(
    telegram_service,
):
    service, rpc, peer, bot, _root = telegram_service
    await rpc.call("telegram.connect", {"token": TOKEN})
    review = await rpc.call("telegram.pairing.review", {"agent_id": "jarvis"})
    http = ControlledHTTP({"url": ""})
    previous_client = service._telegram_channel.client
    previous_client._transport = http.transport
    with http.timing():
        task = asyncio.create_task(
            rpc.call(
                "telegram.pairing.start",
                {
                    "agent_id": "jarvis",
                    "expected_profile_revision": review["agent_revision"],
                    "review_hash": review["review_hash"],
                    "acknowledge_update_consumption": True,
                    "confirm_no_other_consumer": True,
                },
            )
        )
        try:
            await turns_until(http.writer.waiting.is_set)
            http.expire()
            await turns_until(task.done)
            with pytest.raises(RpcError, match="telegram_webhook_timeout"):
                await task
            assert http.clock[0] - http.beginning < 25
            assert http.writer.aborted and len(http.connections) == 1
            assert service._telegram_pairing._nonce is None
            assert (
                service._telegram_pairing._lease is None
                and service._telegram_pairing._worker is None
            )
            assert not service._telegram_mutating and not service._maintenance
            # The earlier successful connection remains explicit; only failed
            # connect clears its token. Pairing did not start a channel or model.
            assert previous_client.identity is not None and not previous_client.closed
            assert not peer.calls and not bot.sent()
        finally:
            http.writer.released.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_pairing_start_returns_with_listener_pending_without_waiting_for_owner(
    telegram_service,
):
    service, rpc, peer, bot, _root = telegram_service
    await rpc.call("telegram.connect", {"token": TOKEN})
    review = await rpc.call("telegram.pairing.review", {"agent_id": "jarvis"})
    value = await rpc.call(
        "telegram.pairing.start",
        {
            "agent_id": "jarvis",
            "expected_profile_revision": review["agent_revision"],
            "review_hash": review["review_hash"],
            "acknowledge_update_consumption": True,
            "confirm_no_other_consumer": True,
        },
    )
    assert value["state"] == "listening" and value["claim_id"] is None
    assert not service._telegram_pairing._worker.done() and not peer.calls and not bot.sent()
    assert not service._telegram_mutating and not service._maintenance
