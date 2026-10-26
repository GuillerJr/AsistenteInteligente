"""Private pairing over own local RPC/HTTP, no real bot, model or account."""

import asyncio
import json
import re
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from test_service_telegram import (
    event,
    fresh_update,
)
from test_service_telegram import (
    telegram_service as telegram_service,
)
from test_telegram import CHAT, TOKEN, update

from jarvis_core.private_json import PrivateJsonError
from jarvis_core.protocol import RpcError
from jarvis_core.state import SessionRef
from jarvis_core.telegram import (
    IncomingMessage,
    TelegramChannel,
    TelegramClient,
    TelegramError,
    TelegramHTTPTransport,
    TelegramJournal,
)
from jarvis_core.telegram_pairing import TelegramPairing


async def begin(client, *, agent="jarvis"):
    await client.call("telegram.connect", {"token": TOKEN})
    review = await client.call("telegram.pairing.review", {"agent_id": agent})
    status = await client.call(
        "telegram.pairing.start",
        {
            "agent_id": agent,
            "expected_profile_revision": review["agent_revision"],
            "review_hash": review["review_hash"],
            "acknowledge_update_consumption": True,
            "confirm_no_other_consumer": True,
        },
    )
    link = await client.call("telegram.pairing.open", {"challenge_id": status["challenge_id"]})
    parts = urlsplit(link["telegram_uri"])
    assert parts.scheme == "tg" and parts.netloc == "resolve" and not parts.fragment
    parameters = parse_qs(parts.query)
    assert set(parameters) == {"domain", "start"} and parameters["domain"] == ["fixture_bot"]
    nonce = parameters["start"][0]
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", nonce)
    assert "nonce" not in status and nonce not in json.dumps(status)
    return status, nonce


async def state(client, expected):
    async with asyncio.timeout(4):
        while True:
            value = await client.call("telegram.pairing.status")
            if value["state"] == expected:
                return value
            await asyncio.sleep(0.002)


async def accept(client, claim):
    return await client.call(
        "telegram.pairing.confirm",
        {
            "claim_id": claim["claim_id"],
            "agent_id": claim["agent_id"],
            "expected_profile_revision": claim["agent_revision"],
        },
    )


async def test_review_is_explicit_and_acknowledges_consumed_updates_without_polling(
    telegram_service,
):
    service, client, peer, bot, _root = telegram_service
    await client.call("telegram.connect", {"token": TOKEN})
    review = await client.call("telegram.pairing.review", {"agent_id": "jarvis"})
    assert review["consumes_ignored_updates"] and review["requires_exclusive_bot"]
    assert review["telegram_retains_start_message"] and review["max_seconds"] == 180
    assert not service._telegram_pairing and not peer.calls
    assert not any(row[1] == "getUpdates" for row in bot.requests)
    for key in ("acknowledge_update_consumption", "confirm_no_other_consumer"):
        request = {
            "agent_id": "jarvis",
            "expected_profile_revision": review["agent_revision"],
            "review_hash": review["review_hash"],
            "acknowledge_update_consumption": True,
            "confirm_no_other_consumer": True,
        }
        request[key] = False
        with pytest.raises(RpcError, match="telegram_pairing_review_required"):
            await client.call("telegram.pairing.start", request)
    assert not any(row[1] == "getUpdates" for row in bot.requests)


async def test_native_uri_is_single_use_and_never_in_status_history_or_durable_files(
    telegram_service,
):
    service, client, peer, bot, root = telegram_service
    status, nonce = await begin(client)
    assert status["state"] == "listening" and status["claim_id"] is None
    with pytest.raises(RpcError, match="telegram_pairing_link_unavailable"):
        await client.call("telegram.pairing.open", {"challenge_id": status["challenge_id"]})
    for method in ("telegram.status", "telegram.pairing.status", "telegram.history"):
        value = await client.call(method)
        assert nonce not in json.dumps(value) and "tg://" not in json.dumps(value)
    assert not bot.sent() and not peer.calls and not service.store.list_sessions()
    for path in (root / "profile" / "telegram").iterdir():
        if path.is_file():
            assert (
                nonce.encode() not in path.read_bytes() and TOKEN.encode() not in path.read_bytes()
            )


@pytest.mark.parametrize("redelivery_id", [0, 1, 500])
async def test_second_owner_confirmation_only_creates_route_and_control_never_reaches_model(
    telegram_service,
    redelivery_id,
):
    service, client, peer, bot, root = telegram_service
    initial, nonce = await begin(client)
    original = fresh_update(text="/start " + nonce)
    await bot.updates.put([original])
    claim = await state(client, "claimed")
    assert claim["challenge_id"] == initial["challenge_id"]
    assert claim["claim_id"] != claim["challenge_id"] and claim["chat_id"] == CHAT
    assert not service._telegram_journal.routes() and not service.store.list_sessions()
    assert not peer.calls and not bot.sent()
    assert service._telegram_journal.is_control_message(CHAT, original["message"]["message_id"])
    snapshot = await accept(client, claim)
    route = snapshot["routes"][0]
    assert route["chat_id"] == CHAT and route["agent_id"] == "jarvis"
    assert not service.store.list_messages(SessionRef(route["session_id"]))
    assert service._telegram_pairing._nonce is None and not snapshot["running"]
    with pytest.raises(RpcError, match="telegram_pairing_claim_invalid"):
        await accept(client, claim)
    service._telegram_channel.clock = lambda: original["message"]["date"]
    await client.call("telegram.start", {"configuration_hash": snapshot["configuration_hash"]})
    redelivery = {**original, "update_id": redelivery_id}
    next_id = redelivery_id + 1
    normal = update(
        next_id, date=original["message"]["date"] + 1, text="consulta normal", message_id=99
    )
    await bot.updates.put([redelivery, normal])
    await asyncio.wait_for(peer.entered.wait(), 3)
    await event(client, next_id)
    assert len(peer.calls) == 1 and peer.calls[0][0] == "consulta normal"
    assert nonce not in json.dumps(peer.calls, ensure_ascii=False)
    assert len(bot.sent()) == 1
    own = service.store.list_messages(SessionRef(route["session_id"]))
    assert all(nonce not in row.text for row in own)
    for path in (root / "profile" / "telegram").iterdir():
        if path.is_file():
            assert nonce.encode() not in path.read_bytes()


@pytest.mark.parametrize(
    "options",
    [
        {"sender": CHAT + 1},
        {"chat_type": "group"},
        {"is_bot": True},
        {"sender": True},
        {"chat": -1},
        {"message_id": True},
        {"date": int(time.time()) - 60},
        {"text": None},
    ],
)
async def test_only_fresh_exact_private_start_can_propose_a_claim(telegram_service, options):
    service, client, peer, bot, _root = telegram_service
    _started, nonce = await begin(client)
    arguments = {"date": int(time.time()), "text": "/start " + nonce, **options}
    await bot.updates.put([update(**arguments)])
    async with asyncio.timeout(3):
        while service._telegram_journal.offset < 2:
            await asyncio.sleep(0.002)
    assert (await client.call("telegram.pairing.status"))["state"] == "listening"
    assert not peer.calls and not bot.sent() and not service.store.list_sessions()
    history = await client.call("telegram.history")
    assert nonce not in json.dumps(history) and history["events"][0]["chat_id"] is None


@pytest.mark.parametrize("suffix", [" extra", " ", "\n", "@fixture_bot"])
async def test_start_text_must_match_exactly_without_suffixes(telegram_service, suffix):
    service, client, _peer, bot, _root = telegram_service
    _started, nonce = await begin(client)
    await bot.updates.put([fresh_update(text="/start " + nonce + suffix)])
    async with asyncio.timeout(3):
        while service._telegram_journal.offset < 2:
            await asyncio.sleep(0.002)
    assert service._telegram_pairing.claim() is None


async def test_second_confirmation_is_bound_to_claim_agent_revision_and_current_profile(
    telegram_service,
):
    service, client, peer, bot, _root = telegram_service
    _started, nonce = await begin(client)
    await bot.updates.put([fresh_update(text="/start " + nonce)])
    claim = await state(client, "claimed")
    with pytest.raises(RpcError, match="telegram_pairing_claim_invalid"):
        await client.call(
            "telegram.pairing.confirm",
            {
                "claim_id": "0" * 32,
                "agent_id": "jarvis",
                "expected_profile_revision": 1,
            },
        )
    await client.call(
        "agents.update",
        {
            "agent_id": "jarvis",
            "expected_revision": 1,
            "instructions": "Instrucción sintética nueva",
        },
    )
    with pytest.raises(RpcError, match="telegram_configuration_changed"):
        await accept(client, claim)
    assert not service.store.list_sessions() and not service._telegram_journal.routes()
    assert not peer.calls and not bot.sent()


async def test_tombstone_failure_never_offers_claim_or_creates_route(telegram_service, monkeypatch):
    service, client, peer, bot, _root = telegram_service
    _started, nonce = await begin(client)

    def fail(*_args):
        raise PrivateJsonError("registry_unavailable")

    monkeypatch.setattr(service._telegram_journal, "mark_control_message", fail)
    await bot.updates.put([fresh_update(text="/start " + nonce)])
    value = await state(client, "failed")
    assert value["claim_id"] is None and service._telegram_pairing._nonce is None
    assert not service._telegram_journal.routes() and not service.store.list_sessions()
    assert not peer.calls and not bot.sent()


async def test_pairing_lease_blocks_channel_other_poller_and_route_mutation(telegram_service):
    service, client, peer, _bot, _root = telegram_service
    await begin(client)
    with pytest.raises(TelegramError, match="telegram_runner_unavailable"):
        service._telegram_journal.acquire_runner()
    channel = TelegramChannel(
        service._telegram_channel.client, service._telegram_journal, lambda _: None
    )
    # No routes exists yet; an arbitrary second raw poller still cannot lease.
    with pytest.raises(RpcError):
        await client.call("telegram.routes.create", {"chat_id": CHAT, "agent_id": "jarvis"})
    with pytest.raises(RpcError):
        await client.call(
            "telegram.start",
            {"configuration_hash": service._telegram_snapshot()["configuration_hash"]},
        )
    assert not channel.running and not peer.calls
    await client.call("telegram.pairing.cancel")
    descriptor = service._telegram_journal.acquire_runner()
    import os

    os.close(descriptor)


async def test_webhook_rechecked_and_poll_conflict_stops_without_competitive_retry(
    telegram_service,
):
    _service, client, peer, bot, _root = telegram_service
    await client.call("telegram.connect", {"token": TOKEN})
    review = await client.call("telegram.pairing.review", {"agent_id": "jarvis"})
    request = {
        "agent_id": "jarvis",
        "expected_profile_revision": 1,
        "review_hash": review["review_hash"],
        "acknowledge_update_consumption": True,
        "confirm_no_other_consumer": True,
    }
    bot.webhook = "https://fixture.invalid/private-webhook"
    with pytest.raises(RpcError, match="telegram_webhook_conflict"):
        await client.call("telegram.pairing.start", request)
    assert not any(row[1] == "getUpdates" for row in bot.requests)
    bot.webhook, bot.mode = "", "poll-conflict"
    await client.call("telegram.pairing.start", request)
    value = await state(client, "failed")
    assert value["error"] == "telegram_webhook_conflict" and bot.poll_count == 1
    assert not peer.calls and not bot.sent()
    assert not any(row[1] in {"deleteWebhook", "setWebhook", "sendMessage"} for row in bot.requests)


@pytest.mark.parametrize(
    "operation", ["telegram.pairing.cancel", "telegram.stop", "telegram.disconnect"]
)
async def test_cancel_stop_disconnect_destroy_nonce_and_claim_without_any_message(
    telegram_service,
    operation,
):
    service, client, peer, bot, _root = telegram_service
    _started, nonce = await begin(client)
    await bot.updates.put([fresh_update(text="/start " + nonce)])
    claim = await state(client, "claimed")
    await client.call(operation)
    pairing = service._telegram_pairing
    assert pairing._nonce is None and pairing.claim() is None and not pairing.busy
    with pytest.raises(RpcError):
        await accept(client, claim)
    assert not service.store.list_sessions() and not peer.calls and not bot.sent()


@pytest.mark.parametrize("receive_claim", [False, True])
async def test_ttl_expires_listener_or_claim_and_invalidates_confirmation(
    telegram_service,
    receive_claim,
):
    service, client, peer, bot, _root = telegram_service
    service.initialize_telegram(
        client_factory=lambda token: TelegramClient(
            token, transport=TelegramHTTPTransport(connector=bot.connector)
        ),
        pairing_factory=lambda client, journal: TelegramPairing(client, journal, ttl=0.12),
    )
    _started, nonce = await begin(client)
    if receive_claim:
        await bot.updates.put([fresh_update(text="/start " + nonce)])
        claim = await state(client, "claimed")
    expired = await state(client, "expired")
    assert expired["claim_id"] is None and service._telegram_pairing._nonce is None
    if receive_claim:
        with pytest.raises(RpcError):
            await accept(client, claim)
    assert not service.store.list_sessions() and not peer.calls and not bot.sent()


async def test_late_cancelled_start_message_is_control_even_without_a_tombstone(telegram_service):
    service, client, peer, bot, _root = telegram_service
    _started, nonce = await begin(client)
    await client.call("telegram.pairing.cancel")
    snapshot = await client.call("telegram.routes.create", {"chat_id": CHAT, "agent_id": "jarvis"})
    date = int(time.time())
    service._telegram_channel.clock = lambda: date
    await client.call("telegram.start", {"configuration_hash": snapshot["configuration_hash"]})
    await bot.updates.put([update(text="/start " + nonce, date=date + 1)])
    result = await event(client)
    assert result["status"] == "ignored" and result["error_code"] == "telegram_control_ignored"
    assert not peer.calls and not bot.sent()


def test_control_tombstone_survives_restart_generation_and_changed_text(tmp_path):
    journal = TelegramJournal(tmp_path.resolve() / "journal")
    journal.mark_control_message(CHAT, 11)
    restarted = TelegramJournal(journal.document.directory)
    incoming = IncomingMessage(1, CHAT, 11, "jarvis", "a" * 32, "changed text")
    assert not restarted.claim(1, incoming, permit_epoch_change=True)
    assert restarted.is_control_message(CHAT, 11)
    assert restarted.history() == ()


def test_v1_journal_upgrades_only_on_tombstone_write_preserving_routes(tmp_path):
    journal = TelegramJournal(tmp_path.resolve() / "journal")
    route = journal.set_route(CHAT, "jarvis", "a" * 32)

    def old(value):
        value["version"] = 1
        value.pop("control_messages")
        return value

    journal.document.update(old)
    reopened = TelegramJournal(journal.document.directory)
    assert reopened.document.read()["version"] == 1 and reopened.routes() == (route,)
    reopened.mark_control_message(CHAT, 11)
    assert reopened.document.read()["version"] == 2 and reopened.routes() == (route,)


async def test_absolute_ttl_includes_webhook_preflight_and_never_starts_expired_poll(
    telegram_service,
):
    service, client, peer, bot, _root = telegram_service
    instant = [0.0]
    transport = TelegramHTTPTransport(connector=bot.connector)

    async def delayed_preflight(*arguments):
        if arguments[1] == "getWebhookInfo" and service._telegram_pairing is not None:
            instant[0] = 181.0
        return await transport(*arguments)

    service.initialize_telegram(
        client_factory=lambda token: TelegramClient(token, transport=delayed_preflight),
        pairing_factory=lambda client, journal: TelegramPairing(
            client,
            journal,
            monotonic=lambda: instant[0],
        ),
    )
    await client.call("telegram.connect", {"token": TOKEN})
    review = await client.call("telegram.pairing.review", {"agent_id": "jarvis"})
    with pytest.raises(RpcError, match="telegram_pairing_expired"):
        await client.call(
            "telegram.pairing.start",
            {
                "agent_id": "jarvis",
                "expected_profile_revision": review["agent_revision"],
                "review_hash": review["review_hash"],
                "acknowledge_update_consumption": True,
                "confirm_no_other_consumer": True,
            },
        )
    assert (await client.call("telegram.pairing.status"))["state"] == "expired"
    assert service._telegram_pairing._nonce is None and not service._telegram_pairing.busy
    assert not any(row[1] == "getUpdates" for row in bot.requests) and not peer.calls


async def test_changed_bot_configuration_cannot_confirm_an_already_proposed_chat(telegram_service):
    service, client, peer, bot, _root = telegram_service
    _started, nonce = await begin(client)
    await bot.updates.put([fresh_update(text="/start " + nonce)])
    claim = await state(client, "claimed")

    def changed(value):
        value["bot_id"] += 1
        return value

    service._telegram_journal.document.update(changed)
    with pytest.raises(RpcError, match="telegram_pairing_claim_invalid"):
        await accept(client, claim)
    assert not service.store.list_sessions() and not service._telegram_journal.routes()
    assert not peer.calls and not bot.sent()


def test_control_tombstone_limit_fails_closed_without_evicting_prior_markers(tmp_path):
    journal = TelegramJournal(tmp_path.resolve() / "journal")

    def filled(value):
        value["control_messages"] = {f"{CHAT}:{number}": True for number in range(1, 257)}
        return value

    journal.document.update(filled)
    before = journal.document.path.read_bytes()
    with pytest.raises(TelegramError, match="telegram_control_limit"):
        journal.mark_control_message(CHAT, 257)
    assert journal.document.path.read_bytes() == before
    assert journal.is_control_message(CHAT, 1) and journal.is_control_message(CHAT, 256)


async def test_expiry_while_persisting_control_never_offers_a_late_claim(
    telegram_service,
    monkeypatch,
):
    service, client, peer, bot, _root = telegram_service
    instant = [0.0]
    service.initialize_telegram(
        client_factory=lambda token: TelegramClient(
            token,
            transport=TelegramHTTPTransport(connector=bot.connector),
        ),
        pairing_factory=lambda client, journal: TelegramPairing(
            client,
            journal,
            monotonic=lambda: instant[0],
        ),
    )
    _started, nonce = await begin(client)
    original = service._telegram_journal.mark_control_message

    def slow_persist(chat_id, message_id):
        original(chat_id, message_id)
        instant[0] = 181.0

    monkeypatch.setattr(service._telegram_journal, "mark_control_message", slow_persist)
    await bot.updates.put([fresh_update(text="/start " + nonce)])
    expired = await state(client, "expired")
    assert expired["claim_id"] is None and service._telegram_pairing._nonce is None
    assert service._telegram_journal.is_control_message(CHAT, 11)
    assert not service.store.list_sessions() and not peer.calls and not bot.sent()


async def test_late_http_connector_cannot_send_get_updates_after_absolute_expiry(
    telegram_service,
):
    service, client, peer, bot, _root = telegram_service
    instant = [0.0]
    transport = TelegramHTTPTransport(connector=bot.connector)

    async def delayed_dispatch(*arguments):
        if arguments[1] == "getUpdates":
            instant[0] = 181.0
        return await transport(*arguments)

    service.initialize_telegram(
        client_factory=lambda token: TelegramClient(token, transport=delayed_dispatch),
        pairing_factory=lambda client, journal: TelegramPairing(
            client,
            journal,
            monotonic=lambda: instant[0],
        ),
    )
    await client.call("telegram.connect", {"token": TOKEN})
    review = await client.call("telegram.pairing.review", {"agent_id": "jarvis"})
    await client.call(
        "telegram.pairing.start",
        {
            "agent_id": "jarvis",
            "expected_profile_revision": review["agent_revision"],
            "review_hash": review["review_hash"],
            "acknowledge_update_consumption": True,
            "confirm_no_other_consumer": True,
        },
    )
    await state(client, "expired")
    assert not any(row[1] == "getUpdates" for row in bot.requests)
    assert service._telegram_journal.offset == 0 and not peer.calls
