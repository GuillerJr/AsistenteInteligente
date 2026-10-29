"""Controlled read recovery; no real Telegram, elapsed backoff or user credentials."""

import asyncio
import json

import pytest
from test_telegram import NOW, final_event, update
from test_telegram import bot as bot
from test_telegram import channel_factory as channel_factory

from jarvis_core.telegram import (
    TelegramChannel,
    TelegramError,
    TelegramHTTPResponse,
    TelegramHTTPTransport,
)


async def turns_until(predicate):
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("Controlled recovery checkpoint not reached")


class PollScript:
    def __init__(self, channel, plan):
        self.channel, self.base, self.plan = channel, channel.client._transport, list(plan)
        self.polls = []

    async def __call__(self, method, name, parameters, token, timeout, guard):
        if name != "getUpdates":
            return await self.base(method, name, parameters, token, timeout, guard)
        assert method == "GET"
        guard()
        channel = self.channel
        self.polls.append(
            {
                "offset": parameters["offset"],
                "worker": id(channel._worker),
                "lease": channel._lease,
                "started_at": channel._started_at,
                "configuration": channel._reviewed_hash,
                "epoch": channel.journal.document.read()["epoch"],
            }
        )
        item = self.plan.pop(0) if self.plan else None
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return item()
        return await self.base(method, name, parameters, token, timeout, guard)


class ControlledWait:
    def __init__(self):
        self.delays, self.entered, self.release = [], asyncio.Queue(), asyncio.Queue()

    async def __call__(self, delay):
        self.delays.append(delay)
        self.entered.put_nowait(delay)
        await self.release.get()


def failed_read(effect_status="known"):
    return TelegramError("telegram_transport_failed", effect_status=effect_status)


async def test_read_recovers_same_offset_epoch_lease_and_one_delivery(channel_factory, bot):
    channel, journal, received = await channel_factory()
    scripted = PollScript(channel, [None, failed_read(), None])
    channel.client._transport = scripted
    waiting = ControlledWait()
    channel._wait_poll_recovery = waiting
    await bot.updates.put([update(1)])
    channel.start(expected_configuration_hash=journal.configuration_hash())
    assert await asyncio.wait_for(waiting.entered.get(), 2) == 1
    assert channel.error_code == "telegram_reconnecting" and channel.running
    assert journal.offset == 2 and len(received) == len(bot.sent()) == 1
    await bot.updates.put([update(2)])
    waiting.release.put_nowait(None)
    assert (await final_event(channel, journal, identity=2))["outcome"] == "known"
    assert len(received) == len(bot.sent()) == 2 and journal.offset == 3
    assert [row["offset"] for row in scripted.polls[:3]] == [0, 2, 2]
    for key in ("worker", "lease", "started_at", "configuration", "epoch"):
        assert len({row[key] for row in scripted.polls}) == 1
    assert channel._started_at == NOW + 1 and channel.error_code is None
    assert [row[1] for row in bot.requests].count("getMe") == 1


async def test_third_known_transport_error_stops_after_two_virtual_waits(channel_factory, bot):
    channel, journal, received = await channel_factory()
    scripted = PollScript(channel, [failed_read(), failed_read(), failed_read()])
    channel.client._transport = scripted
    delays = []

    async def virtual_wait(delay):
        delays.append(delay)
        await asyncio.sleep(0)

    channel._wait_poll_recovery = virtual_wait
    channel.start(expected_configuration_hash=journal.configuration_hash())
    await asyncio.wait_for(channel._worker, 2)
    assert len(scripted.polls) == 3 and delays == [1, 2]
    assert channel.error_code == "telegram_transport_failed" and not channel.running
    assert channel._lease is None and not received and not bot.sent()
    assert journal.offset == 0 and not journal.history()


async def test_successful_poll_resets_only_the_consecutive_transport_budget(channel_factory, bot):
    channel, journal, received = await channel_factory()

    def batch(identity):
        return lambda: TelegramHTTPResponse(
            200, json.dumps({"ok": True, "result": [update(identity)]}).encode()
        )

    scripted = PollScript(
        channel, [failed_read(), failed_read(), batch(1), failed_read(), failed_read(), batch(2)]
    )
    channel.client._transport = scripted
    delays = []

    async def virtual_wait(delay):
        delays.append(delay)
        await asyncio.sleep(0)

    channel._wait_poll_recovery = virtual_wait
    channel.start(expected_configuration_hash=journal.configuration_hash())
    assert (await final_event(channel, journal, identity=2))["outcome"] == "known"
    assert delays == [1, 2, 1, 2] and len(received) == len(bot.sent()) == 2
    assert channel.running and channel.error_code is None


@pytest.mark.parametrize(
    "error",
    [
        failed_read("unknown"),
        TelegramError("telegram_api_rejected"),
        TelegramError("telegram_updates_invalid"),
        TelegramError("telegram_http_invalid"),
        TelegramError("telegram_webhook_conflict"),
    ],
)
async def test_unknown_or_non_transport_read_errors_never_retry(channel_factory, bot, error):
    channel, journal, received = await channel_factory()
    scripted = PollScript(channel, [error])
    channel.client._transport = scripted
    waiting = ControlledWait()
    channel._wait_poll_recovery = waiting
    channel.start(expected_configuration_hash=journal.configuration_hash())
    await asyncio.wait_for(channel._worker, 2)
    assert len(scripted.polls) == 1 and not waiting.delays
    assert channel.error_code == error.code and not received and not bot.sent()


async def test_stop_interrupts_real_backoff_without_another_poll(channel_factory, bot):
    channel, journal, received = await channel_factory()
    scripted = PollScript(channel, [failed_read()])
    channel.client._transport = scripted
    channel.start(expected_configuration_hash=journal.configuration_hash())
    await turns_until(lambda: channel.error_code == "telegram_reconnecting")
    await channel.stop()
    assert len(scripted.polls) == 1 and channel._lease is None and channel.error_code is None
    assert not channel.running and not received and not bot.sent()


@pytest.mark.parametrize("change", ["configuration", "lease"])
async def test_context_revoked_during_wait_cannot_start_a_retry(channel_factory, bot, change):
    connected, journal, received = await channel_factory()
    authorized = [True]

    def configuration_guard():
        if not authorized[0]:
            raise TelegramError("telegram_configuration_changed")

    # The public journal setter correctly excludes configuration changes while
    # a reader owns its lease. Model a revoked host/workspace context through
    # the channel's supported guard instead of bypassing that protection.
    channel = TelegramChannel(
        connected.client,
        journal,
        connected.dispatch,
        clock=lambda: NOW,
        close_timeout=1,
        configuration_guard=configuration_guard,
    )
    scripted = PollScript(channel, [failed_read()])
    channel.client._transport = scripted
    waiting = ControlledWait()
    channel._wait_poll_recovery = waiting
    try:
        channel.start(expected_configuration_hash=journal.configuration_hash())
        await asyncio.wait_for(waiting.entered.get(), 2)
        connections_before_revocation = len(bot.connections)
        if change == "configuration":
            authorized[0] = False
        else:
            path = journal.document.directory / "telegram.runner.lock"
            path.unlink()
            path.touch(mode=0o600)
        waiting.release.put_nowait(None)
        await asyncio.wait_for(channel._worker, 2)
        assert len(scripted.polls) == 1 and not received and not bot.sent()
        assert len(bot.connections) == connections_before_revocation
        assert channel._lease is None
        assert channel.error_code == (
            "telegram_configuration_changed"
            if change == "configuration"
            else "telegram_runner_changed"
        )
    finally:
        await channel.aclose()


@pytest.mark.parametrize("result", ["transport_error", "configuration_error", "successful_poll"])
async def test_voice_unknown_stop_has_priority_over_concurrent_poll_result(
    channel_factory, bot, result
):
    channel, journal, received = await channel_factory()

    def voice_became_unknown():
        channel.error_code = "telegram_attachment_delivery_unconfirmed"
        channel._stop.set()
        if result == "transport_error":
            raise failed_read()
        if result == "configuration_error":
            raise TelegramError("telegram_configuration_changed")
        return TelegramHTTPResponse(200, json.dumps({"ok": True, "result": [update()]}).encode())

    scripted = PollScript(channel, [voice_became_unknown])
    channel.client._transport = scripted
    waiting = ControlledWait()
    channel._wait_poll_recovery = waiting
    channel.start(expected_configuration_hash=journal.configuration_hash())
    await asyncio.wait_for(channel._worker, 2)
    assert channel.error_code == "telegram_attachment_delivery_unconfirmed"
    assert len(scripted.polls) == 1 and not waiting.delays and not channel.running
    assert not received and not bot.sent() and not journal.history()


async def test_voice_unknown_during_backoff_wakes_without_retry_or_error_replacement(
    channel_factory, bot
):
    channel, journal, received = await channel_factory()
    scripted = PollScript(channel, [failed_read()])
    channel.client._transport = scripted
    channel.start(expected_configuration_hash=journal.configuration_hash())
    await turns_until(lambda: channel.error_code == "telegram_reconnecting")
    channel.error_code = "telegram_attachment_delivery_unconfirmed"
    channel._stop.set()
    await asyncio.wait_for(channel._worker, 2)
    assert (
        len(scripted.polls) == 1
        and channel.error_code == "telegram_attachment_delivery_unconfirmed"
    )
    assert not channel.running and not received and not bot.sent()


async def test_http_reader_close_is_joined_before_recovery_wait(channel_factory, bot):
    channel, journal, received = await channel_factory()
    closed, joined, waiting = [], [], ControlledWait()

    class Writer:
        transport = None

        def write(self, data):
            assert data.startswith(b"GET ") and b"/getUpdates?" in data

        async def drain(self):
            pass

        def close(self):
            closed.append(True)

        async def wait_closed(self):
            await asyncio.sleep(0)
            joined.append(True)

    async def connector(host, port, **options):
        assert host == "api.telegram.org" and port == 443 and options["ssl"].check_hostname
        reader = asyncio.StreamReader()
        reader.feed_data(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{")
        reader.feed_eof()
        return reader, Writer()

    channel.client._transport = TelegramHTTPTransport(connector=connector)

    async def recovery(delay):
        assert closed == joined == [True], (
            "The owned stream must settle before another read is scheduled"
        )
        await waiting(delay)

    channel._wait_poll_recovery = recovery
    channel.start(expected_configuration_hash=journal.configuration_hash())
    assert await asyncio.wait_for(waiting.entered.get(), 2) == 1
    assert channel.error_code == "telegram_reconnecting" and not received and not bot.sent()
    await channel.stop()
