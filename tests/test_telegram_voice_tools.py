"""Actual central grants, local Ogg codec and scoped synthetic Telegram ingress."""

import asyncio
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_media_audio import artificial_ogg
from test_service_telegram import approval, event, fresh_update, ready
from test_service_telegram import telegram_service as telegram_service
from test_telegram import BOT, CHAT
from test_telegram_voice import response
from test_tools import grant

from jarvis_core.runtime import ApprovalRequest
from jarvis_core.state import SessionRef
from jarvis_core.telegram import TelegramError
from jarvis_core.telegram_tools import NAME, TelegramReplyTools
from jarvis_core.tools import (
    ApprovalAuthority,
    ToolError,
    canonical_arguments,
    operation_digest,
)


def voice_fixture(service, route):
    ref = SessionRef(route["session_id"])
    return service._media_store().ingest_bytes(
        service._media_scope(ref), artificial_ogg(), "sintetica.ogg", "audio/ogg",
        attachment_id=uuid4().hex, guard=lambda: None,
    )


def ack_for(incoming):
    value = {
        "message_id": 501, "from": {"id": BOT, "is_bot": True},
        "chat": {"id": incoming.chat_id, "type": incoming.chat_type},
        "reply_to_message": {"message_id": incoming.message_id},
        "voice": {
            "file_id": "synthetic_voice", "file_unique_id": "synthetic_unique", "duration": 1
        },
    }
    if incoming.message_thread_id is not None:
        value["message_thread_id"] = incoming.message_thread_id
    return response(value)


async def decide(client, route, card, allow=True):
    return await client.call("approvals.decide", {
        "session_id": route["session_id"], "approval_id": card["approval_id"],
        "digest": card["digest"], "allow": allow,
    })


async def run_receipt(client, route, final):
    return (await client.call("runs.get", {
        "session_id": route["session_id"], "run_id": final["run_id"]
    }))["run"]


@pytest.mark.parametrize("delivery", [None, "document", "voice"])
async def test_delivery_is_exact_scoped_and_default_stays_document(telegram_service, delivery):
    service, client, peer, bot, _root = telegram_service
    route = await ready(client)
    manifest, voices = voice_fixture(service, route), []
    args = {"attachment_id": manifest.attachment_id, "sha256": manifest.sha256, "caption": "PRUEBA"}
    if delivery is not None:
        args["delivery"] = delivery

    async def upload(incoming, payload, name, mime, caption, token, timeout, before_dispatch):
        before_dispatch()
        voices.append((incoming, hashlib.sha256(payload).hexdigest(), len(payload)))
        return ack_for(incoming)

    service._telegram_channel.client._voice_transport = upload
    peer.plans["envia nota"] = [(NAME, args)]
    await bot.updates.put([fresh_update(text="envia nota")])
    card = await approval(client, route)
    assert json.loads(card["arguments_json"]) == args and not voices and not bot.uploads
    await decide(client, route, card)
    final = await event(client)
    receipt = await run_receipt(client, route, final)
    action = receipt["action_receipts"][0]
    assert final["status"] == "completed" and final["outcome"] == "known"
    assert action["succeeded"] and not action["verified"]
    ack = json.loads(action["output"])
    assert ack["delivery_mode"] == (delivery or "document")
    assert ack["sha256"] == manifest.sha256 and ack["independently_verified"] is False
    assert len(voices) == int(delivery == "voice")
    assert len(bot.uploads) == int(delivery != "voice")
    if voices:
        assert voices[0][0].chat_id == CHAT and voices[0][0].message_id == 11
        assert voices[0][1:] == (manifest.sha256, manifest.byte_length)
    assert not service._telegram_run_context


async def test_denied_voice_never_decodes_or_uploads(telegram_service, monkeypatch):
    from jarvis_core import telegram_files

    service, client, peer, bot, _root = telegram_service
    route = await ready(client)
    manifest = voice_fixture(service, route)

    async def forbidden(*args, **kwargs):
        raise AssertionError("Denied proposal entered local decode or upload")

    monkeypatch.setattr(telegram_files, "validate_voice_payload", forbidden)
    service._telegram_channel.client._voice_transport = forbidden
    peer.plans["rechaza nota"] = [(NAME, {
        "attachment_id": manifest.attachment_id, "sha256": manifest.sha256, "delivery": "voice"
    })]
    await bot.updates.put([fresh_update(text="rechaza nota")])
    card = await approval(client, route)
    await decide(client, route, card, False)
    final = await event(client)
    receipt = await run_receipt(client, route, final)
    assert final["outcome"] == "known" and receipt["error"] == "approval_denied"
    assert not receipt["action_receipts"] and not bot.uploads


async def test_document_grant_cannot_be_changed_to_voice_delivery():
    args = {"attachment_id": "a" * 32, "sha256": "b" * 64, "delivery": "document"}
    operation = ApprovalRequest("c" * 32, "d" * 32, "e" * 32, NAME, canonical_arguments(args), "")
    operation = replace(operation, digest=operation_digest(operation))
    authority = ApprovalAuthority()
    issued = await grant(authority, operation)
    with authority.execution_scope(operation, issued):
        with pytest.raises(ToolError, match="tool_authority_mismatch"):
            await TelegramReplyTools(SimpleNamespace()).execute(
                {**args, "delivery": "voice"}, asyncio.Event())


async def test_voice_cannot_borrow_another_incoming_session_even_with_its_own_grant():
    args = {"attachment_id": "a" * 32, "sha256": "b" * 64, "delivery": "voice"}
    operation = ApprovalRequest("c" * 32, "d" * 32, "e" * 32, NAME, canonical_arguments(args), "")
    operation = replace(operation, digest=operation_digest(operation))
    authority = ApprovalAuthority()
    issued = await grant(authority, operation)
    service = SimpleNamespace(_telegram_run_context={
        operation.run_id: {"ref": SessionRef("f" * 32)}
    })
    with authority.execution_scope(operation, issued):
        result = await TelegramReplyTools(service).execute(args, asyncio.Event())
    assert not result.succeeded and result.effect_status == "known"
    assert result.error == "telegram_route_unavailable"


@pytest.mark.parametrize("delivery", [True, None, "audio", [], {}])
def test_delivery_schema_rejects_ambiguous_or_unknown_modes(delivery):
    with pytest.raises(ToolError, match="telegram_attachment_delivery_invalid"):
        TelegramReplyTools.validate({"attachment_id": "a" * 32, "sha256": "b" * 64,
                                     "delivery": delivery})


async def test_revoked_original_after_decode_cannot_be_uploaded(telegram_service):
    service, client, peer, bot, _root = telegram_service
    route = await ready(client)
    manifest, uploads = voice_fixture(service, route), []

    async def upload(incoming, payload, name, mime, caption, token, timeout, before_dispatch):
        service._media_store().revoke(service._media_scope(SessionRef(route["session_id"])),
                                     manifest.attachment_id, manifest.sha256, guard=lambda: None)
        before_dispatch()
        uploads.append(1)
        return ack_for(incoming)

    service._telegram_channel.client._voice_transport = upload
    peer.plans["retira antes del envio"] = [(NAME, {
        "attachment_id": manifest.attachment_id, "sha256": manifest.sha256, "delivery": "voice"
    })]
    await bot.updates.put([fresh_update(text="retira antes del envio")])
    await decide(client, route, await approval(client, route))
    final = await event(client)
    receipt = await run_receipt(client, route, final)
    assert final["outcome"] == "known" and not uploads
    assert receipt["action_receipts"][0]["error"] == "media_attachment_revoked"
    assert receipt["action_receipts"][0]["effect_status"] == "known"


async def test_lost_voice_ack_pauses_reader_without_replay_after_manual_start(telegram_service):
    service, client, peer, bot, _root = telegram_service
    route = await ready(client)
    manifest, posts = voice_fixture(service, route), []

    async def upload(*args):
        args[-1]()
        posts.append(1)
        raise TelegramError("telegram_attachment_delivery_unconfirmed", effect_status="unknown")

    service._telegram_channel.client._voice_transport = upload
    peer.plans["voz incierta"] = [(NAME, {
        "attachment_id": manifest.attachment_id, "sha256": manifest.sha256, "delivery": "voice"
    })]
    await bot.updates.put([fresh_update(text="voz incierta")])
    await decide(client, route, await approval(client, route))
    final = await event(client)
    receipt = await run_receipt(client, route, final)
    assert final["outcome"] == "unknown" and posts == [1] and not bot.sent()
    assert receipt["action_receipts"][0]["effect_status"] == "unknown"
    assert (await client.call("telegram.status"))["running"] is False
    await client.call("telegram.stop")
    snapshot = await client.call("telegram.status")
    await client.call("telegram.start", {"configuration_hash": snapshot["configuration_hash"]})
    replay = fresh_update(2, text="voz incierta")
    replay["message"]["message_id"] = 11
    before = bot.poll_count
    await bot.updates.put([replay])
    async with asyncio.timeout(3):
        while bot.poll_count <= before + 1:
            await asyncio.sleep(0.002)
    assert posts == [1] and len(peer.calls) == 1
