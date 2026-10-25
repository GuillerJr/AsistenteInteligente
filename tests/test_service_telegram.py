"""Own local RPC + SQLite + loopback Telegram HTTP, all accounts synthetic."""

import asyncio
import json
import shutil
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import pytest
from test_telegram import BOT, CHAT, TOKEN, FakeBot, update

from jarvis_core.protocol import LocalRpcClient, RpcError
from jarvis_core.service import JarvisService
from jarvis_core.state import SessionRef, StateError
from jarvis_core.telegram import TelegramClient, TelegramError, TelegramHTTPTransport


class TelegramPeer:
    def __init__(self):
        self.calls = []
        self.plans = {}
        self.replies = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cleaned = asyncio.Event()

    async def account_status(self):
        return {"connected": True, "mode": "chatgpt"}

    async def chat(self, message, *, on_thread, on_event, on_tool_call, **options):
        self.calls.append((message, options))
        identity = options["thread_id"] or "synthetic_" + uuid4().hex
        if options["thread_id"] is None:
            on_thread(identity)
        self.entered.set()
        try:
            if message == "esperar":
                await self.release.wait()
            for name, arguments in self.plans.get(message, ()):
                self.replies.append(await on_tool_call(name, arguments))
            return {"thread_id": identity, "answer": "Respuesta sintética: " + message}
        finally:
            if message == "esperar":
                await asyncio.sleep(0.02)
            self.cleaned.set()

    async def aclose(self):
        pass


@pytest.fixture
async def telegram_service():
    root = Path(tempfile.mkdtemp(prefix="jarvis-telegram-service-", dir="/private/tmp")).resolve()
    bot = FakeBot()
    await bot.start()
    peer = TelegramPeer()
    service = JarvisService(root / "profile", peer)
    service.initialize_telegram(
        client_factory=lambda token: TelegramClient(
            token, transport=TelegramHTTPTransport(connector=bot.connector)
        )
    )
    client = LocalRpcClient(root / "profile", max_frame_bytes=1024 * 1024)
    await service.start()
    await client.connect()
    try:
        yield service, client, peer, bot, root
    finally:
        await client.aclose()
        await service.aclose()
        await bot.close()
        shutil.rmtree(root)


async def ready(client, *, agent="jarvis"):
    await client.call("telegram.connect", {"token": TOKEN})
    snapshot = await client.call("telegram.routes.create", {"chat_id": CHAT, "agent_id": agent})
    await client.call("telegram.start", {"configuration_hash": snapshot["configuration_hash"]})
    return next(row for row in snapshot["routes"] if row["chat_id"] == CHAT)


def fresh_update(identity=1, **options):
    return update(identity, date=int(time.time()), **options)


async def event(client, identity=1):
    async with asyncio.timeout(5):
        while True:
            rows = (await client.call("telegram.history"))["events"]
            found = next((row for row in rows if row["update_id"] == identity), None)
            if found is not None and found["outcome"] != "pending":
                return found
            await asyncio.sleep(0.002)


async def approval(client, route):
    async with asyncio.timeout(5):
        while True:
            rows = (await client.call("approvals.list", {"session_id": route["session_id"]}))[
                "approvals"
            ]
            if rows:
                return rows[0]
            await asyncio.sleep(0.002)


async def test_service_has_no_implicit_telegram_credentials_connection_or_start(
    telegram_service, monkeypatch
):
    service, client, peer, bot, root = telegram_service
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    snapshot = await client.call("telegram.status")
    assert snapshot["connected"] is False and snapshot["running"] is False
    assert snapshot["bot_id"] is None and snapshot["routes"] == []
    assert not bot.requests and not peer.calls
    assert TOKEN not in json.dumps(snapshot)
    assert service._telegram_channel is None
    for path in root.rglob("*"):
        if path.is_file():
            assert TOKEN.encode() not in path.read_bytes()


async def test_explicit_connect_only_verifies_identity_and_disconnect_clears_ram(telegram_service):
    service, client, peer, bot, _root = telegram_service
    snapshot = await client.call("telegram.connect", {"token": TOKEN})
    connected_client = service._telegram_channel.client
    assert snapshot["connected"] and not snapshot["running"] and snapshot["bot_id"] == BOT
    assert [row[1] for row in bot.requests] == ["getMe", "getWebhookInfo"]
    assert not peer.calls and TOKEN not in json.dumps(snapshot)
    with pytest.raises(RpcError, match="telegram_route_required"):
        await client.call("telegram.start", {"configuration_hash": snapshot["configuration_hash"]})
    disconnected = await client.call("telegram.disconnect")
    assert not disconnected["connected"] and not disconnected["running"]
    assert connected_client.closed and connected_client._token is None


async def test_route_creates_new_durable_agent_session_without_copying_other_history(
    telegram_service,
):
    service, client, peer, _bot, _root = telegram_service
    agent = (await client.call("agents.create", {"name": "Investigación"}))["agent"]
    old = service.store.create_session("Otra conversación", agent_id=agent["agent_id"])
    service.store.append_message(old.ref, "user", "MARCADOR PRIVADO DE OTRO CHAT")
    value = await client.call(
        "telegram.routes.create", {"chat_id": CHAT, "agent_id": agent["agent_id"]}
    )
    route = value["routes"][0]
    ref = SessionRef(route["session_id"])
    assert ref != old.ref and service.store.get_session(ref).agent_id == agent["agent_id"]
    assert not service.store.list_messages(ref) and not peer.calls
    assert service.telegram_uses_agent(agent["agent_id"])
    assert service.telegram_uses_session(ref)
    assert not service.telegram_uses_session(SessionRef(ref.session_id, True))


async def test_owner_review_hash_binds_complete_routes_and_current_workspace(telegram_service):
    service, client, peer, bot, root = telegram_service
    await client.call("telegram.connect", {"token": TOKEN})
    previous = await client.call("telegram.routes.create", {"chat_id": CHAT, "agent_id": "jarvis"})
    await client.call("telegram.routes.create", {"chat_id": CHAT + 1, "agent_id": "jarvis"})
    with pytest.raises(RpcError, match="telegram_configuration_changed"):
        await client.call("telegram.start", {"configuration_hash": previous["configuration_hash"]})
    previous = await client.call("telegram.status")
    workspace = root / "selected"
    workspace.mkdir(mode=0o700)
    await client.call("workspace.set", {"path": str(workspace)})
    with pytest.raises(RpcError, match="telegram_configuration_changed"):
        await client.call("telegram.start", {"configuration_hash": previous["configuration_hash"]})
    current = await client.call("telegram.status")
    assert current["workspace_id"] != previous["workspace_id"]
    started = await client.call(
        "telegram.start", {"configuration_hash": current["configuration_hash"]}
    )
    assert started["running"] and started["pinned_workspace_id"] == service._workspace_id
    assert not peer.calls and not bot.sent()
    with pytest.raises(RpcError):
        await client.call("workspace.set", {"path": str(root / "other")})


async def test_channel_reply_is_exactly_the_completed_durable_run_answer(telegram_service):
    service, client, peer, bot, root = telegram_service
    route = await ready(client)
    await bot.updates.put([fresh_update(text="consulta recibo")])
    row = await event(client)
    receipt = (
        await client.call("runs.get", {"session_id": route["session_id"], "run_id": row["run_id"]})
    )["run"]
    assert row["status"] == "completed" and row["outcome"] == "known"
    assert receipt["status"] == "completed" and receipt["outcome"] == "known"
    assert bot.sent()[0][2]["text"] == receipt["answer"] == "Respuesta sintética: consulta recibo"
    assert len(peer.calls) == 1 and peer.calls[0][1]["history"] == []
    history = await client.call("telegram.history")
    assert "consulta recibo" not in json.dumps(history) and TOKEN not in json.dumps(history)
    assert service._handles == {} and not service.authority.list_pending()
    for path in (root / "profile" / "telegram").iterdir():
        if path.is_file():
            assert TOKEN.encode() not in path.read_bytes()


@pytest.mark.parametrize("options", [{"chat": CHAT + 1}, {"chat_type": "group"}, {"text": None}])
async def test_unlisted_group_and_attachment_never_reach_service_model(telegram_service, options):
    _service, client, peer, bot, _root = telegram_service
    await ready(client)
    await bot.updates.put([fresh_update(**options)])
    row = await event(client)
    assert row["status"] == "ignored" and not peer.calls and not bot.sent()


async def test_telegram_cannot_approve_tools_and_real_host_gate_remains_authoritative(
    telegram_service,
):
    service, client, peer, bot, _root = telegram_service
    command = "/approve: escribe el archivo sintético"
    peer.plans[command] = [("workspace_write", {"path": "telegram-test.txt", "text": "una vez"})]
    route = await ready(client)
    await bot.updates.put([fresh_update(text=command)])
    card = await approval(client, route)
    target = service._workspace_path / "telegram-test.txt"
    assert not target.exists() and not bot.sent() and len(peer.calls) == 1
    assert peer.calls[0][0] == command and not peer.replies
    # This decision is a synthetic fixture owner, never a message command.
    await client.call(
        "approvals.decide",
        {
            "session_id": route["session_id"],
            "approval_id": card["approval_id"],
            "digest": card["digest"],
            "allow": True,
        },
    )
    row = await event(client)
    assert row["status"] == "completed" and target.read_text() == "una vez"
    assert len(bot.sent()) == 1 and len(peer.replies) == 1


async def test_stop_cancels_pending_owner_gate_and_restart_never_replays(telegram_service):
    service, client, peer, bot, _root = telegram_service
    peer.plans["mutación"] = [("workspace_write", {"path": "never.txt", "text": "no"})]
    route = await ready(client)
    message = fresh_update(text="mutación")
    await bot.updates.put([message])
    await approval(client, route)
    stopped = await client.call("telegram.stop")
    assert not stopped["running"] and not service._handles and not service.authority.list_pending()
    assert not (service._workspace_path / "never.txt").exists() and not bot.sent()
    history = await client.call("telegram.history")
    assert history["events"][0]["outcome"] == "unknown"
    await bot.updates.put([message])
    await client.call("telegram.start", {"configuration_hash": stopped["configuration_hash"]})
    async with asyncio.timeout(3):
        while bot.poll_count < 3:
            await asyncio.sleep(0.002)
    assert len(peer.calls) == 1 and not bot.sent()


async def test_disconnect_waits_provider_cleanup_then_clears_token(telegram_service):
    service, client, peer, bot, _root = telegram_service
    await ready(client)
    connected_client = service._telegram_channel.client
    await bot.updates.put([fresh_update(text="esperar")])
    await asyncio.wait_for(peer.entered.wait(), 3)
    snapshot = await client.call("telegram.disconnect")
    assert peer.cleaned.is_set() and not service._handles
    assert not snapshot["connected"] and connected_client._token is None and not bot.sent()


async def test_failed_run_receipt_persistence_never_sends_model_text(telegram_service, monkeypatch):
    service, client, peer, bot, _root = telegram_service
    await ready(client)

    def fail(*args, **kwargs):
        raise StateError("synthetic_persistence_failed")

    monkeypatch.setattr(service.store, "finish_run", fail)
    await bot.updates.put([fresh_update(text="sin recibo durable")])
    row = await event(client)
    assert row["status"] == "interrupted" and row["outcome"] == "unknown"
    assert row["run_id"] and not bot.sent() and len(peer.calls) == 1
    assert service._closing and not service._handles


async def test_out_of_band_route_change_during_run_cannot_retarget_reply(telegram_service):
    service, client, peer, bot, _root = telegram_service
    route = await ready(client)
    await bot.updates.put([fresh_update(text="esperar")])
    await asyncio.wait_for(peer.entered.wait(), 3)
    other = service.store.create_session("Otra ruta sintética")

    def change(value):
        value["routes"][str(CHAT)]["session_id"] = other.ref.session_id
        value["routes"][str(CHAT)]["revision"] += 1
        return value

    service._telegram_journal.document.update(change)
    peer.release.set()
    row = await event(client)
    assert row["outcome"] == "unknown" and not bot.sent()
    assert row["session_id"] == route["session_id"]
    assert not service.store.list_messages(other.ref)


async def test_replaced_workspace_is_rejected_before_model_call(telegram_service):
    service, client, peer, bot, _root = telegram_service
    await ready(client)
    workspace = service._workspace_path
    workspace.rename(workspace.with_name("workspace-original"))
    workspace.mkdir(mode=0o700)
    await bot.updates.put([fresh_update()])
    row = await event(client)
    assert row["status"] == "failed" and not peer.calls and not bot.sent()


async def test_archived_route_session_cannot_start_channel(telegram_service):
    _service, client, peer, bot, _root = telegram_service
    await client.call("telegram.connect", {"token": TOKEN})
    snapshot = await client.call("telegram.routes.create", {"chat_id": CHAT, "agent_id": "jarvis"})
    route = snapshot["routes"][0]
    await client.call("sessions.update", {"session_id": route["session_id"], "archived": True})
    with pytest.raises(RpcError, match="telegram_route_unavailable"):
        await client.call("telegram.start", {"configuration_hash": snapshot["configuration_hash"]})
    assert (
        not peer.calls
        and not bot.sent()
        and not any(row[1] == "getUpdates" for row in bot.requests)
    )


@pytest.mark.parametrize("identity", [True, "777777", 0, -1, 1 << 52])
async def test_route_rejects_invalid_chat_before_creating_session(telegram_service, identity):
    service, client, _peer, _bot, _root = telegram_service
    with pytest.raises(RpcError):
        await client.call("telegram.routes.create", {"chat_id": identity, "agent_id": "jarvis"})
    assert not service.store.list_sessions() and not service._telegram_journal.routes()


async def test_failed_route_write_leaves_only_its_own_empty_unprivileged_session(
    telegram_service, monkeypatch
):
    service, client, peer, _bot, _root = telegram_service
    original = service.store.create_session("Conversación anterior sintética")
    service.store.append_message(original.ref, "user", "Contenido anterior sintético")

    def fail(*args, **kwargs):
        raise TelegramError("telegram_journal_unavailable")

    monkeypatch.setattr(service._telegram_journal, "set_route", fail)
    with pytest.raises(RpcError, match="telegram_journal_unavailable"):
        await client.call("telegram.routes.create", {"chat_id": CHAT, "agent_id": "jarvis"})
    rows = service.store.list_sessions()
    assert len(rows) == 2 and len(service.store.list_messages(original.ref)) == 1
    new = next(row for row in rows if row.ref != original.ref)
    assert not service.store.list_messages(new.ref) and not peer.calls
    assert not service._telegram_journal.routes() and not service.authority.list_pending()


async def test_remove_route_is_revision_checked_and_unlocks_own_session_deletion(telegram_service):
    service, client, _peer, _bot, _root = telegram_service
    snapshot = await client.call("telegram.routes.create", {"chat_id": CHAT, "agent_id": "jarvis"})
    route = snapshot["routes"][0]
    with pytest.raises(RpcError) as blocked:
        await client.call("sessions.delete", {"session_id": route["session_id"]})
    assert blocked.value.code == -32036
    assert service.store.get_session(SessionRef(route["session_id"]))
    with pytest.raises(RpcError, match="telegram_route_revision_conflict"):
        await client.call("telegram.routes.remove", {"chat_id": CHAT, "expected_revision": 2})
    removed = await client.call("telegram.routes.remove", {"chat_id": CHAT, "expected_revision": 1})
    assert removed["routes"] == [] and service.store.get_session(SessionRef(route["session_id"]))
    assert (await client.call("sessions.delete", {"session_id": route["session_id"]}))["deleted"]


async def test_restart_restores_routes_but_never_token_connection_or_polling(telegram_service):
    service, client, peer, bot, root = telegram_service
    route = await ready(client)
    await client.call("telegram.stop")
    await client.aclose()
    await service.aclose()
    previous_polls = bot.poll_count
    restarted = JarvisService(root / "profile", TelegramPeer())
    try:
        snapshot = await restarted.telegram_status({})
        assert not snapshot["connected"] and not snapshot["running"]
        assert snapshot["routes"][0]["session_id"] == route["session_id"]
        assert bot.poll_count == previous_polls and not peer.calls
        assert restarted._telegram_channel is None
    finally:
        await restarted.aclose()


async def test_start_rejects_route_change_after_review_before_channel_lease(
    telegram_service, monkeypatch
):
    service, client, peer, bot, _root = telegram_service
    await client.call("telegram.connect", {"token": TOKEN})
    snapshot = await client.call("telegram.routes.create", {"chat_id": CHAT, "agent_id": "jarvis"})
    original = service._telegram_validate_route

    def changed_after_review(route):
        ref = original(route)
        service._telegram_journal.set_route(
            CHAT, route.agent_id, route.session_id, expected_revision=route.revision
        )
        return ref

    monkeypatch.setattr(service, "_telegram_validate_route", changed_after_review)
    with pytest.raises(RpcError, match="telegram_configuration_changed"):
        await client.call("telegram.start", {"configuration_hash": snapshot["configuration_hash"]})
    assert not peer.calls and not any(row[1] == "getUpdates" for row in bot.requests)
    assert not service.telegram_blocks_workspace_change()


async def test_connect_in_progress_blocks_other_mutations_without_exposing_token(telegram_service):
    service, client, peer, bot, _root = telegram_service
    entered, release = asyncio.Event(), asyncio.Event()
    transport = TelegramHTTPTransport(connector=bot.connector)

    async def delayed_transport(*arguments):
        if arguments[1] == "getMe":
            entered.set()
            await release.wait()
        return await transport(*arguments)

    service.initialize_telegram(
        client_factory=lambda token: TelegramClient(token, transport=delayed_transport)
    )
    second = LocalRpcClient(service.directory, max_frame_bytes=1024 * 1024)
    await second.connect()
    connecting = asyncio.create_task(client.call("telegram.connect", {"token": TOKEN}))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        snapshot = await second.call("telegram.status")
        assert not snapshot["connected"] and TOKEN not in json.dumps(snapshot)
        for name, arguments in (
            ("telegram.routes.create", {"chat_id": CHAT, "agent_id": "jarvis"}),
            ("telegram.disconnect", {}),
            ("telegram.stop", {}),
        ):
            with pytest.raises(RpcError):
                await second.call(name, arguments)
        release.set()
        assert (await connecting)["connected"] and not peer.calls
    finally:
        release.set()
        await asyncio.gather(connecting, return_exceptions=True)
        await second.aclose()


async def test_route_limit_rejects_the_33rd_without_orphaning_an_extra_session(telegram_service):
    service, client, peer, _bot, _root = telegram_service
    for identity in range(CHAT, CHAT + 32):
        await client.call("telegram.routes.create", {"chat_id": identity, "agent_id": "jarvis"})
    with pytest.raises(RpcError, match="telegram_route_limit"):
        await client.call("telegram.routes.create", {"chat_id": CHAT + 32, "agent_id": "jarvis"})
    assert len(service.store.list_sessions()) == 32 and not peer.calls


async def test_failure_after_runtime_acceptance_cancels_own_run_and_pending_gate(
    telegram_service, monkeypatch
):
    service, client, peer, bot, _root = telegram_service
    peer.plans["fallo después de aceptar"] = [
        ("workspace_write", {"path": "must-not-exist.txt", "text": "no"})
    ]
    await ready(client)
    original = service.start_run

    async def fail_after_acceptance(arguments):
        await original(arguments)
        await asyncio.sleep(0)
        raise RpcError(-32000, "Fallo sintético sin credenciales")

    monkeypatch.setattr(service, "start_run", fail_after_acceptance)
    await bot.updates.put([fresh_update(text="fallo después de aceptar")])
    row = await event(client)
    assert row["status"] == "interrupted" and row["outcome"] == "unknown"
    assert row["run_id"] and not service._handles and not service.authority.list_pending()
    assert not (service._workspace_path / "must-not-exist.txt").exists() and not bot.sent()


async def test_wrong_receipt_identity_cannot_leave_a_live_runtime_or_authority(
    telegram_service, monkeypatch
):
    service, client, peer, bot, _root = telegram_service
    await ready(client)

    async def wrong_receipt(ref, handle):
        await asyncio.sleep(0)
        return {"run_id": "0" * 32, "status": "completed", "outcome": "known", "answer": "no"}

    monkeypatch.setattr(service, "_await_run_receipt", wrong_receipt)
    await bot.updates.put([fresh_update(text="esperar")])
    row = await event(client)
    assert row["status"] == "interrupted" and row["outcome"] == "unknown"
    assert peer.cleaned.is_set() and not service._handles and not bot.sent()
