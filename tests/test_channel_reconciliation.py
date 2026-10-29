"""Own restored Telegram profiles, central gates and local IPC; no real account."""

import asyncio
import copy
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from test_activation import prepared as prepared
from test_memory_management import NoSdk, decide, finish
from test_recovery import bundle as bundle
from test_recovery import ctx
from test_telegram import TOKEN, update
from test_telegram import bot as bot

import jarvis_core.private_json as json_module
from jarvis_core.activation import activate_prepared_profile, review_prepared_profile
from jarvis_core.client_identity import key_id
from jarvis_core.maintenance_journal import MaintenanceJournal, _valid
from jarvis_core.private_json import PrivateJsonError
from jarvis_core.protocol import LocalRpcClient, RpcError, authenticated_client
from jarvis_core.service import JarvisService
from jarvis_core.state import SessionRef
from jarvis_core.telegram import BotIdentity, TelegramClient, TelegramError, TelegramHTTPTransport
from jarvis_core.tools import HostPurpose

CHAT, BOT = 42, 12345


@pytest.fixture
async def host(prepared):
    activate_prepared_profile(review_prepared_profile(prepared, ctx()), ctx())
    # The copied fixture already has its restore/admission records. Its own
    # historical route is disabled; add only a synthetic bot ID, never a token.
    root = Path(tempfile.mkdtemp(prefix="jr-ch26-", dir=(
        "/private/tmp" if sys.platform == "darwin" else tempfile.gettempdir())))
    root.chmod(0o700)
    # Admission pins the physical root; move no admitted profile. Use a short
    # symlink-free source path by rebuilding its admission after own restore.
    shutil.rmtree(root)
    shutil.copytree(prepared, root)
    (root / "restore_admission.json").unlink()
    activate_prepared_profile(review_prepared_profile(root, ctx()), ctx())
    provider = NoSdk()
    service = JarvisService(root, provider)
    service._telegram_journal.bind_bot(BotIdentity(BOT))
    # Open only the session metadata store before the physical review oracles.
    service.store.get_session(SessionRef("a" * 32))
    owner = key_id(os.environ["JARVIS_RPC_BOOTSTRAP_PUBLIC_KEY"])
    service.client_authority.lookup(owner, scope="owner")
    context = authenticated_client.set(owner)
    try:
        yield service, service.maintenance_coordinator, provider
    finally:
        authenticated_client.reset(context)
        await service.aclose()
        shutil.rmtree(root)


async def review(coordinator, *, enabled=True, revision=1):
    return await coordinator.channel_restore_review({
        "chat_id": CHAT, "expected_revision": revision, "enabled": enabled})


async def submit(coordinator, value):
    return await coordinator.channel_restore_reconcile({
        key: value[key] for key in ("review_id", "review_digest")})


async def change(coordinator, *, enabled=True, revision=1):
    prepared = await review(coordinator, enabled=enabled, revision=revision)
    operation = await submit(coordinator, prepared)
    await decide(coordinator)
    return prepared, await finish(coordinator, operation)


def registry(service):
    return copy.deepcopy(service._telegram_journal.document.read())


async def test_readonly_exact_review_has_no_external_authority_or_content(host):
    service, coordinator, provider = host
    before = registry(service)
    root_before = {str(p.relative_to(service.directory)): p.read_bytes()
                  for p in service.directory.rglob("*") if p.is_file()}
    value = await review(coordinator)
    metadata = value["metadata"]
    assert metadata["bot_id"] == BOT and metadata["chat_id"] == CHAT
    assert metadata["agent_id"] == "jarvis" and metadata["agent_revision"] == 1
    assert metadata["session_id"] == "a" * 32
    assert metadata["enabled_before"] is False and metadata["enabled_after"] is True
    assert metadata["external_bot_verified"] is False
    assert metadata["workspace_id"] == service._workspace_id
    assert metadata["generation"] == service.profile_lease.scope.generation
    assert 0 < value["expires_in_seconds"] <= 120
    assert registry(service) == before
    assert all((service.directory / path).read_bytes() == data
               for path, data in root_before.items())
    assert not (await coordinator.pending({}))["pending"]
    assert service._telegram_channel is None and provider.calls == 0
    assert "CONTENIDO" not in json.dumps(value) and "TOPACIO" not in json.dumps(value)


async def test_enable_then_revoke_preserves_history_session_and_requires_separate_start(host):
    service, coordinator, provider = host
    before = registry(service)
    old_scope = service.profile_lease.scope
    prepared, enabled = await change(coordinator)
    assert enabled["phase"] == "complete", enabled
    assert enabled["receipt_durable"] and enabled["receipt"]["status"] == "completed"
    assert enabled["receipt"]["purpose"] == HostPurpose.TELEGRAM_RESTORE_RECONCILE
    assert enabled["receipt"]["request_digest"] == prepared["review_digest"]
    assert enabled["receipt"]["route_revision_after"] == 2
    assert enabled["receipt"]["enabled_after"] is True
    assert service.profile_lease.scope.generation == old_scope.generation + 1
    assert (await submit(coordinator, prepared))["operation_id"] == enabled["operation_id"]
    assert service._telegram_channel is None and provider.calls == 0
    with pytest.raises(TelegramError, match="telegram_login_required"):
        await service.telegram_start({
            "configuration_hash": (await service.telegram_status({}))["configuration_hash"]})
    _, disabled = await change(coordinator, enabled=False, revision=2)
    assert disabled["phase"] == "complete", disabled
    current = registry(service)
    assert current["routes"][str(CHAT)] == {**before["routes"][str(CHAT)], "revision": 3}
    assert {key: value for key, value in current.items() if key != "routes"} == {
        key: value for key, value in before.items() if key != "routes"}
    assert service.store.get_session(SessionRef("a" * 32)).title == "Conversación sintética"
    assert service._telegram_channel is None and provider.calls == 0


@pytest.mark.parametrize("action", ["deny", "cancel", "revoke"])
async def test_negative_owner_decisions_preserve_registry_and_no_reader(host, action):
    service, coordinator, provider = host
    before = registry(service)
    operation = await submit(coordinator, await review(coordinator))
    if action == "deny":
        await decide(coordinator, False)
    elif action == "cancel":
        await coordinator.cancel({"operation_id": operation["operation_id"]})
    else:
        async with asyncio.timeout(2):
            while not (await coordinator.pending({}))["pending"]:
                await asyncio.sleep(.001)
        service.authority.revoke_host(service.profile_lease.scope)
    result = await finish(coordinator, operation)
    assert result["phase"] in {"denied", "cancelled"}, result
    assert registry(service) == before and service._telegram_channel is None
    assert provider.calls == 0


async def test_expired_pending_decision_never_renews_or_changes_route(host):
    service, coordinator, _provider = host
    before = registry(service)
    real_clock = coordinator.clock
    prepared = await review(coordinator)
    operation = await submit(coordinator, prepared)
    async with asyncio.timeout(2):
        while not (await coordinator.pending({}))["pending"]:
            await asyncio.sleep(.001)
    coordinator.clock = lambda: real_clock() + 121
    # Wake the same pending request with revocation; expiry is never a new grant.
    service.authority.revoke_host(service.profile_lease.scope)
    result = await finish(coordinator, operation)
    assert result["phase"] == "failed" and result["error_code"] == "telegram_reconciliation_expired"
    assert registry(service) == before


@pytest.mark.parametrize("mutation", ["route", "agent", "session", "workspace", "bot"])
async def test_changed_review_is_rejected_before_effect(host, mutation, tmp_path):
    service, coordinator, provider = host
    prepared = await review(coordinator)
    if mutation == "route":
        service._telegram_journal.set_route(CHAT, "jarvis", "a" * 32,
                                            expected_revision=1, enabled=False)
    elif mutation == "agent":
        service.agent_profiles.document.update(lambda value: {
            **value, "agents": {key: {**row, "revision": row["revision"] + 1}
                                for key, row in value["agents"].items()}})
    elif mutation == "session":
        service.store.update_session(SessionRef("a" * 32), archived=True)
    elif mutation == "workspace":
        replacement = tmp_path / "work"
        replacement.mkdir(mode=0o700)
        await service.set_workspace({"path": str(replacement)})
    else:
        service._telegram_journal.document.update(lambda value: {**value, "bot_id": BOT + 1})
    before = registry(service)
    with pytest.raises((TelegramError, RpcError)):
        await submit(coordinator, prepared)
    assert registry(service) == before and not (await coordinator.pending({}))["pending"]
    assert provider.calls == 0


async def test_authority_rechecked_after_staging_immediately_before_publication(host, monkeypatch):
    service, coordinator, _provider = host
    prepared = await review(coordinator)
    before = registry(service)
    original = json_module.PrivateJsonDocument._write_temporary
    loop = asyncio.get_running_loop()
    revoked = threading.Event()

    def revoke_on_loop():
        service.authority.revoke_host(service.profile_lease.scope)
        revoked.set()

    def revoke_at_staging(document, parent, data):
        name = original(document, parent, data)
        if document.name == "telegram.json":
            loop.call_soon_threadsafe(revoke_on_loop)
            assert revoked.wait(2), "synthetic loop did not revoke during staging"
        return name

    monkeypatch.setattr(json_module.PrivateJsonDocument, "_write_temporary", revoke_at_staging)
    operation = await submit(coordinator, prepared)
    await decide(coordinator)
    result = await finish(coordinator, operation)
    assert result["phase"] == "failed", result
    assert registry(service) == before
    assert not list((service.directory / "telegram").glob(".registry-*"))


async def test_fsync_ack_loss_preserves_unknown_and_inspect_never_replays(host, monkeypatch):
    service, coordinator, provider = host
    prepared = await review(coordinator)
    real_replace = json_module.os.replace
    real_fsync = json_module.os.fsync
    published = False

    def mark_publish(src, dst, *args, **kwargs):
        nonlocal published
        result = real_replace(src, dst, *args, **kwargs)
        if dst == "telegram.json":
            published = True
        return result

    def lose_confirmation(fd):
        nonlocal published
        if published:
            published = False
            raise OSError("synthetic lost directory fsync acknowledgement")
        return real_fsync(fd)

    monkeypatch.setattr(json_module.os, "replace", mark_publish)
    monkeypatch.setattr(json_module.os, "fsync", lose_confirmation)
    operation = await submit(coordinator, prepared)
    await decide(coordinator)
    result = await finish(coordinator, operation)
    assert result["phase"] == "publication_unknown", result
    assert result["receipt"]["status"] == "unknown" and result["receipt_durable"]
    assert registry(service)["routes"][str(CHAT)]["enabled"] is True
    history = copy.deepcopy(result["receipt"])
    observed = await coordinator.channel_restore_inspect({"chat_id": CHAT})
    assert observed["route"]["revision"] == 2 and observed["observation_only"]
    assert observed["permits_replay"] is False
    assert observed["prior_acknowledgement"] == "not_established"
    assert (await submit(coordinator, prepared))["operation_id"] == result["operation_id"]
    assert (await coordinator.status({
        "operation_id": result["operation_id"]}))["receipt"] == history
    previous = MaintenanceJournal(service.directory).previous
    assert previous[-1]["receipt"] == history and previous[-1]["phase"] == "publication_unknown"
    assert provider.calls == 0 and service._telegram_channel is None


async def test_real_local_rpc_gate_then_inspection_no_transport_or_provider(host):
    service, coordinator, provider = host
    await service.start()
    client = LocalRpcClient(service.directory)
    await client.connect()
    try:
        value = await client.call("telegram.restore.review", {
            "chat_id": CHAT, "expected_revision": 1, "enabled": True})
        operation = await client.call("telegram.restore.reconcile", {
            key: value[key] for key in ("review_id", "review_digest")})
        async with asyncio.timeout(3):
            while not (pending := (await client.call("maintenance.pending"))["pending"]):
                await asyncio.sleep(.001)
        card = pending[0]
        assert card["purpose"] == HostPurpose.TELEGRAM_RESTORE_RECONCILE
        assert json.loads(card["arguments_json"])["review"] == value["metadata"]
        await client.call("maintenance.decide", {"approval_id": card["approval_id"],
            "digest": card["digest"], "allow": True, **card["scope"]})
        result = await finish(coordinator, operation)
        assert result["phase"] == "complete", result
        observed = await client.call("telegram.restore.inspect", {"chat_id": CHAT})
        assert observed["route"]["enabled"] is True and observed["permits_replay"] is False
        assert not provider.calls and service._telegram_channel is None
    finally:
        await client.aclose()


@pytest.mark.parametrize("field,value", [
    ("permits_replay", True), ("network_started", True), ("enabled_after", False),
    ("request_digest", "a" * 64), ("route_revision_after", 9),
    ("configuration_hash_after", None), ("bot_id", True),
])
async def test_durable_receipt_rejects_forged_authority_and_inconsistent_effect(host, field, value):
    _service, coordinator, _provider = host
    _review, result = await change(coordinator)
    receipt = copy.deepcopy(result["receipt"])
    receipt[field] = value
    document = {"version": 1, "operations": [{
        "operation_id": result["operation_id"], "purpose": result["kind"],
        "review_digest": result["review_digest"], "phase": result["phase"], "receipt": receipt}]}
    with pytest.raises(PrivateJsonError, match="maintenance_journal_invalid"):
        _valid(document)


async def fresh_claim(service, bot):
    service.initialize_telegram(client_factory=lambda token: TelegramClient(
        token, transport=TelegramHTTPTransport(connector=bot.connector)))
    await service.telegram_connect({"token": TOKEN})
    value = await service.telegram_pairing_review({"agent_id": "jarvis"})
    status = await service.telegram_pairing_start({
        "agent_id": "jarvis", "expected_profile_revision": value["agent_revision"],
        "review_hash": value["review_hash"], "acknowledge_update_consumption": True,
        "confirm_no_other_consumer": True})
    link = await service.telegram_pairing_open({"challenge_id": status["challenge_id"]})
    nonce = parse_qs(urlsplit(link["telegram_uri"]).query)["start"][0]
    await bot.updates.put([update(chat=CHAT, text="/start " + nonce, date=int(time.time()) + 1)])
    async with asyncio.timeout(4):
        while (status := await service.telegram_pairing_status({}))["state"] != "claimed":
            await asyncio.sleep(.002)
    snapshot = await service.telegram_status({})
    route = next(row for row in snapshot["routes"] if row["chat_id"] == CHAT)
    return {"claim_id": status["claim_id"], "agent_id": "jarvis",
            "expected_profile_revision": status["agent_revision"], "chat_id": CHAT,
            "expected_route_revision": route["revision"],
            "configuration_hash": snapshot["configuration_hash"]}


async def test_local_reconcile_cannot_start_without_fresh_private_chat_pairing(host, bot):
    service, coordinator, provider = host
    await change(coordinator)
    service.initialize_telegram(client_factory=lambda token: TelegramClient(
        token, transport=TelegramHTTPTransport(connector=bot.connector)))
    snapshot = await service.telegram_connect({"token": TOKEN})
    assert snapshot["restored_pairing_required_chat_ids"] == [CHAT]
    with pytest.raises(TelegramError, match="telegram_restore_fresh_pairing_required"):
        await service.telegram_start({"configuration_hash": snapshot["configuration_hash"]})
    assert not any(row[1] == "getUpdates" for row in bot.requests)
    assert provider.calls == 0


async def test_fresh_pairing_confirms_route_without_mutating_session_or_starting(host, bot):
    service, coordinator, provider = host
    await change(coordinator)
    before_routes = registry(service)["routes"]
    request = await fresh_claim(service, bot)
    snapshot = await service.telegram_restore_pairing_confirm(request)
    assert snapshot["restored_pairing_required_chat_ids"] == []
    assert snapshot["running"] is False
    assert registry(service)["routes"] == before_routes
    assert len(service.store.list_sessions()) == 1
    assert not bot.sent() and not provider.calls
    # All consumed proof is one-use. A later close drops RAM attestations and
    # the same claim can never silently authorize this or another connection.
    with pytest.raises(TelegramError, match="telegram_pairing_claim_invalid"):
        await service.telegram_restore_pairing_confirm(request)
    await service.telegram_disconnect({})
    assert not service._telegram_restored_pairings
    reconnected = await service.telegram_connect({"token": TOKEN})
    assert reconnected["restored_pairing_required_chat_ids"] == [CHAT]
    with pytest.raises(TelegramError, match="telegram_restore_fresh_pairing_required"):
        await service.telegram_start({"configuration_hash": reconnected["configuration_hash"]})


@pytest.mark.parametrize("field,value", [
    ("chat_id", CHAT + 1), ("expected_route_revision", 99),
    ("expected_profile_revision", 99), ("configuration_hash", "a" * 64),
])
async def test_fresh_pairing_cannot_be_lent_to_different_route_or_review(host, bot, field, value):
    service, coordinator, provider = host
    await change(coordinator)
    request = await fresh_claim(service, bot)
    with pytest.raises(TelegramError, match="telegram_configuration_changed"):
        await service.telegram_restore_pairing_confirm({**request, field: value})
    assert not service._telegram_restored_pairings
    assert not bot.sent() and provider.calls == 0


async def test_agent_revision_change_retires_fresh_proof_before_model_dispatch(host, bot):
    service, coordinator, provider = host
    await change(coordinator)
    snapshot = await service.telegram_restore_pairing_confirm(await fresh_claim(service, bot))
    await service.telegram_start({"configuration_hash": snapshot["configuration_hash"]})
    await service.update_agent({"agent_id": "jarvis", "expected_revision": 1,
                                "name": "Jarvis revisado sintético"})
    await bot.updates.put([update(2, chat=CHAT, text="No despachar", date=int(time.time()) + 1)])
    async with asyncio.timeout(4):
        while service._telegram_channel.running:
            await asyncio.sleep(.002)
    assert service._telegram_channel.error_code == "telegram_restore_fresh_pairing_required"
    assert not provider.calls and not bot.sent()


async def test_explicit_pairing_cancel_withdraws_ram_proof_without_route_change(host, bot):
    service, coordinator, provider = host
    await change(coordinator)
    await service.telegram_restore_pairing_confirm(await fresh_claim(service, bot))
    before = registry(service)["routes"]
    await service.telegram_pairing_cancel({})
    snapshot = await service.telegram_status({})
    assert snapshot["restored_pairing_required_chat_ids"] == [CHAT]
    assert registry(service)["routes"] == before
    with pytest.raises(TelegramError, match="telegram_restore_fresh_pairing_required"):
        await service.telegram_start({"configuration_hash": snapshot["configuration_hash"]})
    assert not provider.calls and not bot.sent()


async def test_restored_group_reconciliation_preserves_typed_policy_without_fresh_claim(host):
    service, coordinator, provider = host
    service._telegram_journal.remove_route(CHAT, expected_revision=1)
    group = -100424242
    service._telegram_journal.set_route(group, "jarvis", "a" * 32, enabled=False,
        chat_type="supergroup", allowed_sender_ids=[CHAT], message_thread_id=9,
        allow_attachments=True)
    value = await coordinator.channel_restore_review({
        "chat_id": group, "expected_revision": 1, "enabled": True})
    policy = {key: value["metadata"][key] for key in (
        "chat_type", "allowed_sender_ids", "message_thread_id", "allow_attachments")}
    assert policy == {"chat_type": "supergroup", "allowed_sender_ids": [CHAT],
                      "message_thread_id": 9, "allow_attachments": True}
    operation = await submit(coordinator, value)
    await decide(coordinator)
    result = await finish(coordinator, operation)
    assert result["phase"] == "complete" and result["receipt_durable"]
    assert all(result["receipt"][key] == item for key, item in policy.items())
    observed = await coordinator.channel_restore_inspect({"chat_id": group})
    assert observed["route"]["chat_id"] == group and observed["route"]["enabled"]
    assert observed["permits_replay"] is False and service._telegram_channel is None
    assert not provider.calls and not service._telegram_restored_pairings
    assert (await service.telegram_status({}))["restored_pairing_required_chat_ids"] == [group]
    forged = copy.deepcopy(result["receipt"])
    forged.pop("chat_type")
    with pytest.raises(PrivateJsonError, match="maintenance_journal_invalid"):
        _valid({"version": 1, "operations": [{
            "operation_id": result["operation_id"], "purpose": result["kind"],
            "review_digest": result["review_digest"], "phase": result["phase"],
            "receipt": forged}]})
