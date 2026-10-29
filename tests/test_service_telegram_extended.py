"""Real own RPC/runtime/files, loopback bot and synthetic owner decisions."""

import asyncio
import json
import os
from uuid import uuid4

import pytest
from test_service_telegram import approval, event, fresh_update, ready
from test_service_telegram import telegram_service as telegram_service
from test_telegram import CHAT, TOKEN
from test_telegram_extended import ENTITY, GROUP, TEXT

from jarvis_core.client_identity import ClientIdentity
from jarvis_core.protocol import LocalRpcClient, RpcError
from jarvis_core.state import SessionRef


async def active_event(client):
    async with asyncio.timeout(5):
        while True:
            row = next(
                (
                    row
                    for row in (await client.call("telegram.history"))["events"]
                    if row["status"] == "dispatching" and row["run_id"] is not None
                ),
                None,
            )
            if row:
                return row
            await asyncio.sleep(0.002)


async def group_route(client, *, private_too=False):
    await client.call("telegram.connect", {"token": TOKEN})
    await client.call(
        "telegram.routes.create",
        {
            "chat_id": GROUP,
            "agent_id": "jarvis",
            "chat_type": "supergroup",
            "allowed_sender_ids": [CHAT],
            "message_thread_id": 9,
            "allow_attachments": False,
        },
    )
    if private_too:
        await client.call("telegram.routes.create", {"chat_id": CHAT, "agent_id": "jarvis"})
    return await client.call("telegram.status")


async def group_begin(client):
    review = await client.call("telegram.group.pairing.review", {"chat_id": GROUP})
    state = await client.call(
        "telegram.group.pairing.start",
        {
            "chat_id": GROUP,
            "expected_route_revision": review["route_revision"],
            "expected_profile_revision": review["agent_revision"],
            "review_hash": review["review_hash"],
            "acknowledge_update_consumption": True,
            "confirm_no_other_consumer": True,
        },
    )
    command = await client.call(
        "telegram.group.pairing.command", {"challenge_id": state["challenge_id"]}
    )
    return review, state, command["command"]


async def group_state(client, target):
    async with asyncio.timeout(5):
        while True:
            value = await client.call("telegram.group.pairing.status")
            if value["state"] == target:
                return value
            await asyncio.sleep(0.002)


async def group_confirm(client, claim):
    snapshot = await client.call("telegram.status")
    route = next(row for row in snapshot["routes"] if row["chat_id"] == GROUP)
    return await client.call(
        "telegram.group.pairing.confirm",
        {
            "claim_id": claim["claim_id"],
            "chat_id": GROUP,
            "expected_route_revision": route["revision"],
            "expected_profile_revision": claim["agent_revision"],
            "configuration_hash": snapshot["configuration_hash"],
        },
    )


async def test_route_policy_update_preserves_session_and_requires_stopped_reader(telegram_service):
    service, client, peer, bot, _root = telegram_service
    route = await ready(client)
    with pytest.raises(RpcError, match="telegram_stop_required"):
        await client.call(
            "telegram.routes.update",
            {"chat_id": CHAT, "expected_revision": route["revision"], "allow_attachments": True},
        )
    await client.call("telegram.stop")
    changed = await client.call(
        "telegram.routes.update",
        {"chat_id": CHAT, "expected_revision": route["revision"], "allow_attachments": True},
    )
    current = changed["routes"][0]
    assert current["session_id"] == route["session_id"] and current["revision"] == 2
    assert current["allow_attachments"] is True and current["allowed_sender_ids"] == []
    assert not peer.calls and not bot.sent() and not service._telegram_restored_pairings
    with pytest.raises(RpcError, match="telegram_route_revision_conflict"):
        await client.call(
            "telegram.routes.update",
            {"chat_id": CHAT, "expected_revision": route["revision"], "enabled": False},
        )


async def test_invalid_group_route_never_creates_a_session_or_reads_messages(telegram_service):
    service, client, peer, bot, _root = telegram_service
    for policy in (
        {"allowed_sender_ids": []},
        {"allowed_sender_ids": [True]},
        {"allowed_sender_ids": [CHAT], "message_thread_id": True},
        {"chat_type": "private", "allowed_sender_ids": [CHAT]},
    ):
        with pytest.raises(RpcError):
            await client.call(
                "telegram.routes.create",
                {"chat_id": GROUP, "agent_id": "jarvis", "chat_type": "supergroup", **policy},
            )
    assert not service.store.list_sessions() and not peer.calls and not bot.requests


async def test_cancel_exact_telegram_event_settles_then_reader_accepts_next_message(
    telegram_service,
):
    service, client, peer, bot, _root = telegram_service
    await ready(client)
    await bot.updates.put([fresh_update(text="esperar")])
    await asyncio.wait_for(peer.entered.wait(), 3)
    row = await active_event(client)
    arguments = {
        "epoch": row["epoch"],
        "update_id": row["update_id"],
        "expected_run_id": row["run_id"],
        "expected_route_revision": row["route_revision"],
    }
    with pytest.raises(RpcError, match="telegram_event_not_active"):
        await client.call("telegram.events.cancel", {**arguments, "expected_run_id": "f" * 32})
    result = await client.call("telegram.events.cancel", arguments)
    assert result["cancel_requested"] and result["automatic_replay"] is False
    final = await event(client)
    assert final["status"] == "cancelled" and final["outcome"] == "unknown"
    assert final["run_id"] == row["run_id"] and peer.cleaned.is_set()
    assert not service._handles and not service.authority.list_pending() and not bot.sent()
    assert (await client.call("telegram.status"))["running"]
    await bot.updates.put([fresh_update(2, text="mensaje nuevo")])
    assert (await event(client, 2))["status"] == "completed"
    assert len(peer.calls) == 2 and len(bot.sent()) == 1


async def test_route_guard_withdrawn_after_proposal_never_dispatches_old_approval(telegram_service):
    service, client, peer, bot, _root = telegram_service
    peer.plans["efecto"] = [("workspace_write", {"path": "prohibido.txt", "text": "NO"})]
    route = await ready(client)
    await bot.updates.put([fresh_update(text="efecto")])
    card = await approval(client, route)

    # Deliberately inject a same-user configuration race under the runner lease;
    # this bypasses no product endpoint and the adapter must recheck at effect.
    def revoked(value):
        row = value["routes"][str(CHAT)]
        row["enabled"] = False
        row["revision"] += 1
        return value

    service._telegram_journal.document.update(revoked)
    await client.call(
        "approvals.decide",
        {
            "approval_id": card["approval_id"],
            "digest": card["digest"],
            "allow": True,
            "session_id": route["session_id"],
        },
    )
    row = await event(client)
    assert row["status"] != "completed"
    assert not (service._workspace_path / "prohibido.txt").exists() and not bot.sent()


async def test_fresh_group_claim_pins_sender_topic_and_never_sends_or_calls_model(telegram_service):
    from jarvis_core.host_profile import HostProfileError

    service, client, peer, bot, _root = telegram_service
    await group_route(client)
    review, started, command = await group_begin(client)
    assert review["chat_id"] == GROUP and review["allowed_sender_ids"] == [CHAT]
    assert started["state"] == "listening"
    with pytest.raises(HostProfileError, match="profile_not_quiescent"):
        with service.profile_gate.exclusive():
            pytest.fail("group pairing still owns its expiry and reader cleanup")
    with pytest.raises(RpcError, match="telegram_pairing_link_unavailable"):
        await client.call(
            "telegram.group.pairing.command", {"challenge_id": started["challenge_id"]}
        )
    await bot.updates.put(
        [
            fresh_update(
                1,
                chat=GROUP,
                chat_type="supergroup",
                sender=CHAT + 1,
                text=command,
                message_thread_id=9,
            ),
            fresh_update(
                2,
                chat=GROUP,
                chat_type="supergroup",
                sender=CHAT,
                text=command,
                message_thread_id=8,
            ),
            fresh_update(
                3,
                chat=GROUP,
                chat_type="supergroup",
                sender=CHAT,
                text=command,
                message_thread_id=9,
            ),
        ]
    )
    claim = await group_state(client, "claimed")
    assert service._telegram_group_pairing._lease is None
    assert claim["chat_id"] == GROUP and claim["sender_id"] == CHAT
    assert claim["message_thread_id"] == 9
    assert service._telegram_journal.is_control_message(GROUP, 13)
    snapshot = await group_confirm(client, claim)
    assert not snapshot["running"] and not peer.calls and not bot.sent()
    assert service._telegram_group_pairing._nonce is None
    assert command not in json.dumps(await client.call("telegram.history"))
    await client.call("telegram.start", {"configuration_hash": snapshot["configuration_hash"]})
    await bot.updates.put(
        [
            fresh_update(
                4,
                chat=GROUP,
                chat_type="supergroup",
                sender=CHAT,
                text=TEXT,
                entities=[ENTITY],
                message_thread_id=9,
            )
        ]
    )
    result = await event(client, 4)
    assert result["status"] == "completed" and len(peer.calls) == len(bot.sent()) == 1


async def test_group_configuration_change_rejects_claim_without_new_history_or_authority(
    telegram_service,
):
    service, client, peer, bot, _root = telegram_service
    await group_route(client)
    _review, _started, command = await group_begin(client)
    await bot.updates.put(
        [
            fresh_update(
                chat=GROUP, chat_type="supergroup", sender=CHAT, text=command, message_thread_id=9
            )
        ]
    )
    claim = await group_state(client, "claimed")

    def altered(value):
        value["routes"][str(GROUP)]["allowed_sender_ids"] = [CHAT + 1]
        return value

    service._telegram_journal.document.update(altered)
    with pytest.raises(RpcError, match="telegram_configuration_changed"):
        await group_confirm(client, claim)
    assert not service._telegram_restored_pairings and not peer.calls and not bot.sent()
    await client.call("telegram.group.pairing.cancel")
    assert service._telegram_group_pairing._nonce is None


async def test_starting_another_pairing_preserves_other_current_ram_proof(telegram_service):
    service, client, peer, bot, _root = telegram_service
    await group_route(client, private_too=True)
    _review, _started, command = await group_begin(client)
    await bot.updates.put(
        [
            fresh_update(
                chat=GROUP, chat_type="supergroup", sender=CHAT, text=command, message_thread_id=9
            )
        ]
    )
    await group_confirm(client, await group_state(client, "claimed"))
    proof = service._telegram_restored_pairings[GROUP]
    private = await client.call("telegram.pairing.review", {"agent_id": "jarvis"})
    await client.call(
        "telegram.pairing.start",
        {
            "agent_id": "jarvis",
            "expected_profile_revision": private["agent_revision"],
            "review_hash": private["review_hash"],
            "acknowledge_update_consumption": True,
            "confirm_no_other_consumer": True,
        },
    )
    assert service._telegram_restored_pairings[GROUP] == proof
    assert not peer.calls and not bot.sent()
    await client.call("telegram.pairing.cancel")
    assert service._telegram_restored_pairings[GROUP] == proof
    assert service._telegram_pairing_owner is None


def output_fixture(service, route):
    # Explicit synthetic import into this route's own attachment scope. It
    # supplies bytes only; the later external upload still needs central consent.
    ref = SessionRef(route["session_id"])
    return service._media_store().ingest_bytes(
        service._media_scope(ref),
        b"ARCHIVO SALIDA TELEGRAM 4826",
        "salida.txt",
        "text/plain",
        attachment_id=uuid4().hex,
        guard=lambda: None,
    )


async def test_attachment_reply_uses_central_approval_and_exact_incoming_destination(
    telegram_service,
):
    service, client, peer, bot, _root = telegram_service
    route = await ready(client)
    manifest = output_fixture(service, route)
    arguments = {
        "attachment_id": manifest.attachment_id,
        "sha256": manifest.sha256,
        "caption": "Archivo de prueba 4826",
    }
    peer.plans["devuelve archivo"] = [("telegram_send_attachment", arguments)]
    await bot.updates.put([fresh_update(text="devuelve archivo")])
    card = await approval(client, route)
    assert card["tool_name"] == "telegram_send_attachment" and not bot.uploads
    assert json.loads(card["arguments_json"]) == arguments
    row = await active_event(client)
    destination = await client.call(
        "telegram.destination", {"session_id": route["session_id"], "run_id": row["run_id"]}
    )
    assert destination["chat_id"] == CHAT and destination["message_id"] == 11
    assert destination["maximum_attachment_bytes"] == 5 * 1024 * 1024
    for mode in ("session", "always"):
        with pytest.raises(RpcError, match="aprobación nueva"):
            await client.call(
                "approvals.decide",
                {
                    "approval_id": card["approval_id"],
                    "digest": card["digest"],
                    "allow": True,
                    "remember": mode,
                    "session_id": route["session_id"],
                },
            )
    assert not bot.uploads
    await client.call(
        "approvals.decide",
        {
            "approval_id": card["approval_id"],
            "digest": card["digest"],
            "allow": True,
            "session_id": route["session_id"],
        },
    )
    final = await event(client)
    assert final["status"] == "completed" and len(bot.uploads) == len(bot.sent()) == 1
    assert bot.uploads[0]["data"] == b"ARCHIVO SALIDA TELEGRAM 4826"
    assert bot.uploads[0]["parameters"]["chat_id"] == CHAT
    receipt = (
        await client.call(
            "runs.get", {"session_id": route["session_id"], "run_id": final["run_id"]}
        )
    )["run"]
    action = receipt["action_receipts"][0]
    assert action["succeeded"] is True and action["verified"] is False
    assert action["effect_status"] == "known"
    assert json.loads(action["output"])["delivery"] == "telegram_api_ack"
    assert not service._telegram_run_context


@pytest.mark.parametrize("prior_metadata_read", [False, True])
async def test_denied_attachment_reply_never_uploads_and_accepts_new_message(
    telegram_service, prior_metadata_read
):
    service, client, peer, bot, _root = telegram_service
    route = await ready(client)
    manifest = output_fixture(service, route)
    peer.plans["archivo denegado"] = [
        (
            "telegram_send_attachment",
            {"attachment_id": manifest.attachment_id, "sha256": manifest.sha256},
        )
    ]
    if prior_metadata_read:
        peer.plans["archivo denegado"].insert(0, ("media_list", {}))
    await bot.updates.put([fresh_update(text="archivo denegado")])
    card = await approval(client, route)
    await client.call(
        "approvals.decide",
        {
            "approval_id": card["approval_id"],
            "digest": card["digest"],
            "allow": False,
            "session_id": route["session_id"],
        },
    )
    final = await event(client)
    assert final["status"] == "failed" and final["outcome"] == "known"
    assert final["error_code"] == "approval_denied" and not bot.uploads and not bot.sent()
    receipt = (await client.call("runs.get", {
        "session_id": route["session_id"], "run_id": final["run_id"],
    }))["run"]
    assert receipt["error"] == "approval_denied" and receipt["outcome"] == "known"
    assert [item["tool_name"] for item in receipt["action_receipts"]] == (
        ["media_list"] if prior_metadata_read else []
    )
    assert all(item["effect_status"] == "known" for item in receipt["action_receipts"])
    assert not service._handles and not service.authority.list_pending()
    assert (await client.call("telegram.status"))["running"]
    await bot.updates.put([fresh_update(2, text="mensaje nuevo tras rechazar")])
    following = await event(client, 2)
    assert following["status"] == "completed" and following["outcome"] == "known"
    assert len(bot.sent()) == 1 and not bot.uploads and len(peer.calls) == 2
    assert (await event(client))["run_id"] == final["run_id"]


async def test_old_remembered_attachment_rule_cannot_authorize_new_destination_run(
    telegram_service,
):
    from jarvis_core.client_identity import key_id

    service, client, peer, bot, _root = telegram_service
    route = await ready(client)
    manifest = output_fixture(service, route)
    args = {"attachment_id": manifest.attachment_id, "sha256": manifest.sha256}
    # Represent an old persisted rule. The productive approval endpoint refuses
    # creating this rule now, and the pending gate must ignore old copies too.
    service.automation_permissions.remember(
        tool="telegram_send_attachment",
        arguments=args,
        client_id=key_id(os.environ["JARVIS_RPC_BOOTSTRAP_PUBLIC_KEY"]),
        agent_id="jarvis",
        workspace_id=service._workspace_id,
        session_id=route["session_id"],
        temporary=False,
        mode="always",
    )
    peer.plans["no heredar permiso"] = [("telegram_send_attachment", args)]
    await bot.updates.put([fresh_update(text="no heredar permiso")])
    card = await approval(client, route)
    assert card["tool_name"] == "telegram_send_attachment" and not bot.uploads
    await client.call(
        "approvals.decide",
        {
            "approval_id": card["approval_id"],
            "digest": card["digest"],
            "allow": False,
            "session_id": route["session_id"],
        },
    )
    assert (await event(client))["status"] == "failed" and not bot.uploads


async def test_incoming_document_imports_locally_and_requires_separate_inspection_approval(
    telegram_service,
):
    service, client, peer, bot, _root = telegram_service
    route = await ready(client)
    await client.call("telegram.stop")
    snapshot = await client.call(
        "telegram.routes.update",
        {"chat_id": CHAT, "expected_revision": route["revision"], "allow_attachments": True},
    )
    route = snapshot["routes"][0]
    await client.call("telegram.start", {"configuration_hash": snapshot["configuration_hash"]})
    payload = b"CONTENIDO PRIVADO SINTETICO 6429"
    bot.files["documento_6429"] = {
        "file_unique_id": "unico_6429",
        "file_path": "documents/prueba_6429.txt",
        "data": payload,
    }
    await bot.updates.put(
        [
            fresh_update(
                text="recibe documento",
                document={
                    "file_id": "documento_6429",
                    "file_unique_id": "unico_6429",
                    "file_name": "prueba_6429.txt",
                    "mime_type": "text/plain",
                    "file_size": len(payload),
                },
            )
        ]
    )
    imported = await event(client)
    assert imported["status"] == "completed" and imported["attachment_count"] == 1
    rows = (await client.call("media.list", {"session_id": route["session_id"]}))["attachments"]
    assert len(rows) == 1 and rows[0]["source"] == "telegram"
    attachment = rows[0]
    assert attachment["format"] == "jarvis-attachment/2"
    assert attachment["ingress_run_id"] == imported["run_id"]
    assert attachment["source_kind"] == "document" and attachment["catalog_sequence"] == 1
    assert 0 < attachment["imported_at_unix_ms"] < 2**53
    assert "file_id" not in attachment and "chat_id" not in attachment
    assert payload.decode() not in json.dumps(peer.calls)
    assert attachment["sha256"] in peer.calls[0][1]["context_data"]
    assert "media_inspect" in peer.calls[0][1]["context_data"]
    peer.plans["lee documento"] = [
        (
            "media_inspect",
            {
                "attachment_id": attachment["attachment_id"],
                "sha256": attachment["sha256"],
            },
        )
    ]
    await bot.updates.put([fresh_update(2, text="lee documento")])
    card = await approval(client, route)
    assert card["tool_name"] == "media_inspect" and not peer.replies
    await client.call(
        "approvals.decide",
        {
            "approval_id": card["approval_id"],
            "digest": card["digest"],
            "allow": True,
            "session_id": route["session_id"],
        },
    )
    final = await event(client, 2)
    receipt = (
        await client.call(
            "runs.get",
            {
                "session_id": route["session_id"],
                "run_id": final["run_id"],
            },
        )
    )["run"]
    action = receipt["action_receipts"][0]
    assert final["status"] == "completed" and action["succeeded"]
    assert payload.decode() in action["output"]
    assert len([row for row in bot.requests if row[1] == "downloadFile"]) == 1
    assert not service._telegram_run_context


async def test_lost_upload_ack_pauses_reader_and_restart_does_not_repeat_attachment(
    telegram_service,
):
    service, client, peer, bot, _root = telegram_service
    route = await ready(client)
    manifest = output_fixture(service, route)
    peer.plans["archivo incierto"] = [
        (
            "telegram_send_attachment",
            {"attachment_id": manifest.attachment_id, "sha256": manifest.sha256},
        )
    ]
    bot.mode = "drop-document"
    await bot.updates.put([fresh_update(text="archivo incierto")])
    card = await approval(client, route)
    await client.call(
        "approvals.decide",
        {
            "approval_id": card["approval_id"],
            "digest": card["digest"],
            "allow": True,
            "session_id": route["session_id"],
        },
    )
    final = await event(client)
    assert final["outcome"] == "unknown" and len(bot.uploads) == 1 and not bot.sent()
    assert (await client.call("telegram.status"))["running"] is False
    receipt = (
        await client.call(
            "runs.get", {"session_id": route["session_id"], "run_id": final["run_id"]}
        )
    )["run"]
    assert receipt["action_receipts"][0]["effect_status"] == "unknown"
    await client.call("telegram.stop")
    snapshot = await client.call("telegram.status")
    await client.call("telegram.start", {"configuration_hash": snapshot["configuration_hash"]})
    replay = fresh_update(2, text="archivo incierto")
    replay["message"]["message_id"] = 11
    polls = bot.poll_count
    await bot.updates.put([replay])
    async with asyncio.timeout(3):
        while bot.poll_count <= polls + 1:
            await asyncio.sleep(0.002)
    assert len(bot.uploads) == len(peer.calls) == 1
    assert not service._telegram_run_context


async def test_expired_group_challenge_discards_command_and_claim_without_model(
    telegram_service,
    monkeypatch,
):
    from jarvis_core import telegram_group_management as group_api
    from jarvis_core.telegram_group_pairing import TelegramGroupPairing

    service, client, peer, bot, _root = telegram_service
    instant = [0.0]
    monkeypatch.setattr(
        group_api,
        "TelegramGroupPairing",
        lambda *args, **kwargs: TelegramGroupPairing(*args, monotonic=lambda: instant[0], **kwargs),
    )
    await group_route(client)
    _review, started, _command = await group_begin(client)
    instant[0] = 181.0
    value = await client.call("telegram.group.pairing.status")
    assert value["state"] == "expired" and value["claim_id"] is None
    assert service._telegram_group_pairing._nonce is None
    with pytest.raises(RpcError, match="telegram_pairing_expired"):
        await client.call(
            "telegram.group.pairing.command", {"challenge_id": started["challenge_id"]}
        )
    await client.call("telegram.group.pairing.cancel")
    assert not service._telegram_group_pairing.busy and not peer.calls and not bot.sent()


async def test_revoked_group_pairing_owner_withdraws_nonce_without_borrowing_admin(
    telegram_service,
):
    service, admin, peer, bot, root = telegram_service
    await group_route(admin)
    operator = ClientIdentity.generate()
    review = await admin.call(
        "clients.review",
        {"public_key": operator.public_key, "scope": "owner", "expected_revision": 1},
    )
    await admin.call("clients.admit", {key: review[key] for key in ("review_id", "review_digest")})
    async with LocalRpcClient(root / "profile", identity=operator) as client:
        _review, _started, _command = await group_begin(client)
        await admin.call(
            "clients.revoke", {"client_id": operator.client_id, "expected_revision": 2}
        )
        value = await admin.call("telegram.group.pairing.status")
        assert value["state"] == "cancelled" and value["claim_id"] is None
        assert service._telegram_group_pairing._nonce is None
        assert not service._telegram_restored_pairings and not peer.calls and not bot.sent()
    await admin.call("telegram.group.pairing.cancel")


async def test_revoked_channel_owner_cancels_pending_effect_and_releases_only_after_cleanup(
    telegram_service,
):
    service, admin, peer, bot, root = telegram_service
    await admin.call("telegram.connect", {"token": TOKEN})
    snapshot = await admin.call("telegram.routes.create", {"chat_id": CHAT, "agent_id": "jarvis"})
    route = snapshot["routes"][0]
    operator = ClientIdentity.generate()
    review = await admin.call(
        "clients.review",
        {
            "public_key": operator.public_key,
            "scope": "owner",
            "expected_revision": 1,
        },
    )
    await admin.call("clients.admit", {key: review[key] for key in ("review_id", "review_digest")})
    peer.plans["efecto revocado"] = [
        (
            "workspace_write",
            {
                "path": "sin-autoridad.txt",
                "text": "NO ESCRIBIR",
            },
        )
    ]
    async with LocalRpcClient(root / "profile", identity=operator) as client:
        await client.call("telegram.start", {"configuration_hash": snapshot["configuration_hash"]})
        await bot.updates.put([fresh_update(text="efecto revocado")])
        await approval(admin, route)
        await admin.call(
            "clients.revoke",
            {
                "client_id": operator.client_id,
                "expected_revision": 2,
            },
        )
        final = await event(admin)
        assert final["status"] == "cancelled" and final["outcome"] == "unknown"
    await admin.call("telegram.stop")
    assert not service._handles and not service.authority.list_pending()
    assert not (service._workspace_path / "sin-autoridad.txt").exists() and not bot.sent()
    assert not service._telegram_run_context and service._telegram_owner is None
    lease = service._telegram_journal.acquire_runner()
    os.close(lease)
