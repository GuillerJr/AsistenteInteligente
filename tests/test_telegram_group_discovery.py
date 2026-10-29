"""Signed owner setup over own loopback bot; no model, account or autoroute."""

import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from test_service_telegram import fresh_update
from test_service_telegram import telegram_service as telegram_service
from test_telegram import CHAT, TOKEN
from test_telegram_pairing import accept, state

from jarvis_core.client_identity import ClientIdentity
from jarvis_core.protocol import LocalRpcClient, RpcError


async def fresh_private(client, bot, *, connect=True, update_id=1):
    if connect:
        await client.call("telegram.connect", {"token": TOKEN})
    review = await client.call("telegram.pairing.review", {"agent_id": "jarvis"})
    started = await client.call(
        "telegram.pairing.start",
        {
            "agent_id": "jarvis",
            "expected_profile_revision": review["agent_revision"],
            "review_hash": review["review_hash"],
            "acknowledge_update_consumption": True,
            "confirm_no_other_consumer": True,
        },
    )
    link = await client.call("telegram.pairing.open", {"challenge_id": started["challenge_id"]})
    nonce = parse_qs(urlsplit(link["telegram_uri"]).query)["start"][0]
    await bot.updates.put([fresh_update(update_id, text="/start " + nonce)])
    claim = await state(client, "claimed")
    snapshot = await client.call("telegram.status")
    route = next((row for row in snapshot["routes"] if row["chat_id"] == CHAT), None)
    if route is None:
        snapshot = await accept(client, claim)
    else:
        snapshot = await client.call(
            "telegram.restore.pairing.confirm",
            {
                "claim_id": claim["claim_id"],
                "agent_id": "jarvis",
                "expected_profile_revision": claim["agent_revision"],
                "chat_id": CHAT,
                "expected_route_revision": route["revision"],
                "configuration_hash": snapshot["configuration_hash"],
            },
        )
    return snapshot


async def discover(client, *, attachments=False):
    selection = {"owner_chat_id": CHAT, "agent_id": "jarvis", "allow_attachments": attachments}
    review = await client.call("telegram.group.discover.review", selection)
    started = await client.call(
        "telegram.group.discover.start",
        {
            **selection,
            "expected_profile_revision": review["agent_revision"],
            "review_hash": review["review_hash"],
            "acknowledge_update_consumption": True,
            "confirm_no_other_consumer": True,
        },
    )
    command = await client.call(
        "telegram.group.discover.command",
        {
            "challenge_id": started["challenge_id"],
        },
    )
    return review, started, command["command"]


async def discovery_state(client, target):
    async with asyncio.timeout(4):
        while True:
            status = await client.call("telegram.group.discover.status")
            if status["state"] == target:
                return status
            await asyncio.sleep(0.002)


async def confirm(client, claim):
    return await client.call(
        "telegram.group.discover.confirm",
        {
            "claim_id": claim["claim_id"],
            "expected_profile_revision": claim["agent_revision"],
            "configuration_hash": claim["configuration_hash"],
        },
    )


async def test_unknown_group_requires_fresh_private_owner_proof_before_polling(telegram_service):
    _service, client, peer, bot, _root = telegram_service
    await client.call("telegram.connect", {"token": TOKEN})
    await client.call("telegram.routes.create", {"chat_id": CHAT, "agent_id": "jarvis"})
    with pytest.raises(RpcError, match="telegram_discovery_fresh_private_pairing_required"):
        await discover(client)
    assert not peer.calls and not any(row[1] == "getUpdates" for row in bot.requests)


@pytest.mark.parametrize(
    "kind,group,topic", [("group", -6429, None), ("supergroup", -1006429000, 9)]
)
async def test_unknown_group_command_requires_second_review_then_new_route_without_execution(
    telegram_service,
    kind,
    group,
    topic,
):
    service, client, peer, bot, _root = telegram_service
    private = await fresh_private(client, bot)
    private_session = private["routes"][0]["session_id"]
    review, started, command = await discover(client, attachments=True)
    assert review["owner_chat_id"] == CHAT and started["chat_id"] is None
    assert command not in json.dumps(await client.call("telegram.group.discover.status"))
    with pytest.raises(RpcError, match="telegram_pairing_link_unavailable"):
        await client.call(
            "telegram.group.discover.command", {"challenge_id": started["challenge_id"]}
        )
    with pytest.raises(RpcError, match="telegram_stop_required"):
        await client.call(
            "telegram.routes.create",
            {
                "chat_id": -99,
                "agent_id": "jarvis",
                "chat_type": "group",
                "allowed_sender_ids": [CHAT],
            },
        )
    updates = [
        fresh_update(
            2, chat=group, chat_type=kind, sender=CHAT + 1, text=command, message_thread_id=topic
        ),
        fresh_update(
            3,
            chat=group,
            chat_type=kind,
            sender=CHAT,
            is_bot=True,
            text=command,
            message_thread_id=topic,
        ),
        fresh_update(
            4,
            chat=group,
            chat_type=kind,
            sender=CHAT,
            text=command + " extra",
            message_thread_id=topic,
        ),
        fresh_update(
            5, chat=group, chat_type=kind, sender=CHAT, text=command, message_thread_id=topic
        ),
    ]
    await bot.updates.put(updates)
    claim = await discovery_state(client, "claimed")
    assert claim["chat_id"] == group and claim["chat_type"] == kind
    assert claim["sender_id"] == CHAT and claim["message_thread_id"] == topic
    assert len(service.store.list_sessions()) == 1 and not peer.calls and not bot.sent()
    snapshot = await confirm(client, claim)
    route = next(row for row in snapshot["routes"] if row["chat_id"] == group)
    assert route["allowed_sender_ids"] == [CHAT] and route["allow_attachments"] is True
    assert route["session_id"] != private_session and route["revision"] == 1 and route["enabled"]
    assert not snapshot["running"] and group not in snapshot["restored_pairing_required_chat_ids"]
    proof = service._telegram_restored_pairings[group]
    assert service._telegram_discovery._nonce is None
    assert command not in service._telegram_journal.document.path.read_text()
    with pytest.raises(RpcError, match="telegram_discovery_fresh_private_pairing_required"):
        await discover(client)
    # Existing private routes in new profiles can refresh proof without moving
    # their session or deleting the group's proof from this same configuration.
    refreshed = await fresh_private(client, bot, connect=False, update_id=6)
    assert (
        next(row for row in refreshed["routes"] if row["chat_id"] == CHAT)["session_id"]
        == private_session
    )
    assert service._telegram_restored_pairings[group] == proof
    assert not peer.calls and not bot.sent()


async def test_existing_group_is_never_overwritten_and_cancel_discards_late_claim(telegram_service):
    service, client, peer, bot, _root = telegram_service
    await client.call(
        "telegram.routes.create",
        {
            "chat_id": -6429,
            "agent_id": "jarvis",
            "chat_type": "group",
            "allowed_sender_ids": [CHAT],
            "allow_attachments": False,
        },
    )
    await fresh_private(client, bot)
    before = service._telegram_journal.document.read()["routes"]
    _review, _started, command = await discover(client)
    await bot.updates.put(
        [fresh_update(2, chat=-6429, chat_type="group", sender=CHAT, text=command)]
    )
    claim = await discovery_state(client, "claimed")
    with pytest.raises(RpcError, match="telegram_route_revision_conflict"):
        await confirm(client, claim)
    await client.call("telegram.group.discover.cancel")
    with pytest.raises(RpcError, match="telegram_pairing_claim_invalid"):
        await confirm(client, claim)
    assert service._telegram_journal.document.read()["routes"] == before
    assert len(service.store.list_sessions()) == 2 and not peer.calls and not bot.sent()


async def test_configuration_cas_change_withdraws_discovered_claim_without_new_session(
    telegram_service,
):
    service, client, peer, bot, _root = telegram_service
    await fresh_private(client, bot)
    _review, _started, command = await discover(client)
    await bot.updates.put(
        [fresh_update(2, chat=-6429, chat_type="group", sender=CHAT, text=command)]
    )
    claim = await discovery_state(client, "claimed")
    service._telegram_journal.document.update(
        lambda value: {
            **value,
            "routes": {
                key: {**row, "revision": row["revision"] + 1}
                for key, row in value["routes"].items()
            },
        }
    )
    with pytest.raises(RpcError, match="telegram_configuration_changed"):
        await confirm(client, claim)
    await client.call("telegram.group.discover.cancel")
    assert len(service.store.list_sessions()) == 1 and not peer.calls and not bot.sent()


async def test_expiry_cancels_nonce_and_reader_without_route_or_model(
    telegram_service, monkeypatch
):
    from jarvis_core import telegram_group_discovery as module

    service, client, peer, bot, _root = telegram_service
    await fresh_private(client, bot)
    instant, real = [0.0], module.TelegramGroupDiscovery
    monkeypatch.setattr(
        module,
        "TelegramGroupDiscovery",
        lambda *a, **k: real(*a, monotonic=lambda: instant[0], **k),
    )
    _review, started, _command = await discover(client)
    instant[0] = 181
    assert (await client.call("telegram.group.discover.status"))["state"] == "expired"
    with pytest.raises(RpcError, match="telegram_pairing_expired"):
        await client.call(
            "telegram.group.discover.command", {"challenge_id": started["challenge_id"]}
        )
    await client.call("telegram.group.discover.cancel")
    assert not service._telegram_discovery.busy and service._telegram_discovery._nonce is None
    assert len(service._telegram_journal.routes()) == 1 and not peer.calls and not bot.sent()


async def test_revoked_discovery_owner_cannot_borrow_another_owner_or_preserve_nonce(
    telegram_service,
):
    service, admin, peer, bot, root = telegram_service
    await admin.call("telegram.connect", {"token": TOKEN})
    operator = ClientIdentity.generate()
    review = await admin.call(
        "clients.review",
        {"public_key": operator.public_key, "scope": "owner", "expected_revision": 1},
    )
    await admin.call("clients.admit", {key: review[key] for key in ("review_id", "review_digest")})
    async with LocalRpcClient(root / "profile", identity=operator) as client:
        await fresh_private(client, bot, connect=False)
        await discover(client)
        await admin.call(
            "clients.revoke", {"client_id": operator.client_id, "expected_revision": 2}
        )
    await discovery_state(admin, "cancelled")
    assert service._telegram_discovery._nonce is None
    with pytest.raises(RpcError, match="telegram_discovery_fresh_private_pairing_required"):
        await discover(admin)
    await admin.call("telegram.group.discover.cancel")
    assert len(service._telegram_journal.routes()) == 1 and not peer.calls and not bot.sent()
