"""CLI/ACP integration against the real signed private service transport.

Only the inference peer is synthetic. Clients load explicitly selected fresh
test keys; the service, ACL, workspace epochs, Runtime and receipts are real.
"""

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from jarvis_core.client_identity import ClientIdentity
from jarvis_core.product_acp import ACPBridge, ACPError
from jarvis_core.product_client import (
    ClientSession,
    ProductClient,
    ProductClientError,
    create_identity,
    parser,
    run_cli,
)
from jarvis_core.protocol import LocalRpcClient, RpcError
from jarvis_core.service import JarvisService


class ClientServicePeer:
    def __init__(self):
        self.calls = []
        self.entered = asyncio.Event()
        self.cleaning = asyncio.Event()
        self.release = asyncio.Event()
        self.cleanup_release = asyncio.Event()

    async def account_status(self):
        return {"connected": True, "mode": "chatgpt"}

    async def chat(self, message, *, thread_id, temporary, on_thread, on_event, **_options):
        self.calls.append((message, temporary))
        if thread_id is None:
            on_thread("client-service-synthetic-" + uuid4().hex)
        if message == "hold":
            self.entered.set()
            try:
                await self.release.wait()
            finally:
                self.cleaning.set()
                await self.cleanup_release.wait()
        answer = "Eco sintético: " + message
        on_event("answer.delta", {"text": answer})
        return {"answer": answer}

    async def aclose(self):
        self.release.set()
        self.cleanup_release.set()


@pytest.fixture
async def client_service():
    root = Path(tempfile.mkdtemp(prefix="jvc-", dir=tempfile.gettempdir())).resolve()
    profile = root / "p"
    profile.mkdir(mode=0o700)
    key_path = Path(os.environ["JARVIS_RPC_CLIENT_KEY_FILE"])
    key = ClientIdentity.load(key_path)
    peer = ClientServicePeer()
    service = JarvisService(profile, peer, bootstrap_public_key=key.public_key)
    owner = LocalRpcClient(profile, identity=key, max_frame_bytes=1024 * 1024)
    clients = []
    await service.start()
    await owner.connect()
    try:
        yield service, owner, peer, root, key_path, clients
    finally:
        peer.release.set()
        peer.cleanup_release.set()
        for client in clients:
            await client.close()
        await owner.aclose()
        await service.aclose()
        shutil.rmtree(root)


def arguments(service, key_path, *command, temporary=False):
    return parser().parse_args(["--state", str(service.directory),
        "--identity-file", str(key_path), *( ["--temporary"] if temporary else []), *command])


async def admitted_client(fixture):
    service, owner, _, root, _, clients = fixture
    selected = root / "selected-client-key"
    public = create_identity(selected)
    key_path = selected / "identity.key"
    key = ClientIdentity.load(key_path)
    status = await owner.call("clients.status")
    review = await owner.call("clients.review", {"public_key": public, "scope": "owner",
        "expected_revision": status["revision"]})
    await owner.call("clients.admit", {name: review[name]
                                       for name in ("review_id", "review_digest")})
    client = await ProductClient.connect(service.directory, key_path)
    clients.append(client)
    return client, key, key_path


async def test_cli_argv_creates_a_session_in_an_explicit_second_clients_workspace(client_service):
    service, owner, peer, root, _, _ = client_service
    await owner.call("product.initialize", {})
    private = (await owner.call("sessions.create", {"title": "Chat privado original"}))["session"]
    client, key, selected_key = await admitted_client(client_service)
    # Admission of another owner key does not inherit existing conversations.
    assert (await client.sessions())["sessions"] == []
    assert (await client.initialize())["workspaces"] == []
    folder = root / "explicit-second-workspace"
    folder.mkdir(mode=0o700)
    initialized = await run_cli(arguments(service, selected_key, "initialize",
        "--workspace-path", str(folder), "--workspace-name", "Trabajo elegido"))
    workspace = next(row for row in initialized["workspaces"] if row["path"] == str(folder))
    assert workspace["owner_id"] == key.client_id
    created = await run_cli(arguments(service, selected_key, "sessions", "create",
        "--workspace-id", workspace["workspace_id"], "--title", "Chat CLI"))
    scope = ClientSession(created["session_id"], created["temporary"])
    request_id = uuid4().hex
    exact = "  texto CLI con espacios\n$(dato, no comando)  "
    receipt = await run_cli(arguments(service, selected_key, "prompt", "--session-id",
        scope.session_id, "--message", exact, "--request-id", request_id))
    assert receipt["run_id"] == request_id and receipt["status"] == "completed"
    assert receipt["outcome"] == "known" and receipt["cleanup_confirmed"] is True
    assert peer.calls == [(exact, False)]
    messages = await client.messages(scope)
    assert messages["messages"][0]["text"] == exact
    assert messages["messages"][1]["text"] == "Eco sintético: " + exact
    with pytest.raises(ProductClientError, match="rpc_rejected"):
        await client.messages(ClientSession(private["session_id"], False))
    with pytest.raises(ProductClientError, match="rpc_rejected"):
        await client.get_run(ClientSession(private["session_id"], False), request_id)


async def test_real_cli_client_sharing_upgrade_and_cancel_wait_for_native_cleanup(client_service):
    service, owner, peer, _, _, _ = client_service
    selected = (await owner.call("product.initialize", {}))["workspaces"][0]
    row = (await owner.call("sessions.create", {"title": "Chat compartido"}))["session"]
    scope = ClientSession(row["session_id"], row["temporary"])
    client, key, _ = await admitted_client(client_service)

    async def membership(role, revision):
        return await owner.call("product.coordination", {**scope.params, "kind": "session.member",
            "arguments": {"member_id": key.client_id, "role": role,
                          "expected_revision": revision}})

    await membership("read", 1)
    assert (await client.messages(scope))["messages"] == []
    with pytest.raises(ProductClientError, match="rpc_rejected"):
        await client.start_prompt(scope, "read member must not start", request_id=uuid4().hex)
    assert peer.calls == []
    accessible = (await client.initialize())["workspaces"]
    assert any(row["workspace_id"] == selected["workspace_id"] for row in accessible)
    # Shared workspace use is tied to the existing chat, not ownership to create
    # an unrelated chat or permission to inherit its conversation ACL.
    with pytest.raises(ProductClientError, match="rpc_rejected"):
        await client.create_session(workspace_id=selected["workspace_id"])
    await membership("collaborate", 2)
    request_id = uuid4().hex
    await client.start_prompt(scope, "hold", request_id=request_id)
    await asyncio.wait_for(peer.entered.wait(), 5)
    cancellation = asyncio.create_task(client.cancel_run(scope, request_id))
    try:
        await asyncio.wait_for(peer.cleaning.wait(), 5)
        assert not cancellation.done(), "A cancellation ACK is not a completed cleanup"
        actual = (await owner.call("runs.get", {**scope.params, "run_id": request_id}))["run"]
        assert actual["status"] in {"cancelling", "cleaning"}
        peer.cleanup_release.set()
        receipt = await asyncio.wait_for(cancellation, 5)
        assert receipt["cancel_accepted"] is True and receipt["cancellation_confirmed"] is True
        assert receipt["cleanup_confirmed"] is True and receipt["effects_known"] is False
        assert receipt["outcome"] == "unknown" and receipt["permits_replay"] is False
    finally:
        peer.cleanup_release.set()
        await asyncio.gather(cancellation, return_exceptions=True)
    await membership("remove", 3)
    with pytest.raises(ProductClientError, match="rpc_rejected"):
        await client.messages(scope)
    assert len(peer.calls) == 1
    assert service._handles == {}


async def test_cli_status_uses_no_inference_and_missing_identity_never_falls_back(client_service):
    service, _, peer, root, selected, _ = client_service
    status = await run_cli(arguments(service, selected, "status"))
    assert status["profile_id"] == service.profile_lease.scope.profile_id
    assert peer.calls == []
    # The harness has a perfectly usable implicit env key; this command still
    # rejects its missing explicit identity instead of discovering that key.
    incomplete = parser().parse_args(["--state", str(service.directory), "status"])
    with pytest.raises(ProductClientError, match="explicit_paths_required"):
        await run_cli(incomplete)
    with pytest.raises(ProductClientError, match="connection_unavailable"):
        await ProductClient.connect(service.directory, root / "missing-selected.key")
    insecure = root / "insecure-selected-key"
    create_identity(insecure)
    (insecure / "identity.key").chmod(0o644)
    with pytest.raises(ProductClientError, match="connection_unavailable"):
        await ProductClient.connect(service.directory, insecure / "identity.key")
    assert peer.calls == []


async def test_real_acp_bridge_pins_workspace_epoch_and_rejects_drift_before_model_dispatch(
        client_service):
    service, owner, peer, root, selected_key, clients = client_service
    client = await ProductClient.connect(service.directory, selected_key)
    clients.append(client)
    workspace = (await client.initialize())["workspaces"][0]
    emitted = []

    async def emit(value):
        emitted.append(value)

    bridge = ACPBridge(client, emit, workspace=workspace["path"],
                       workspace_id=workspace["workspace_id"])
    await bridge.initialize({"protocolVersion": 1})
    created = await bridge.new_session({"cwd": workspace["path"], "mcpServers": []})
    scope = ClientSession(created["sessionId"])
    initial = await client.snapshot(scope, run_id="host")
    result = await bridge.prompt({"sessionId": scope.session_id,
        "prompt": [{"type": "text", "text": "ACP exacto"}],
        "_meta": {"jarvis": {"request_id": uuid4().hex}}})
    assert result["stopReason"] == "end_turn"
    chunks = [event["params"]["update"]["content"]["text"] for event in emitted]
    assert "".join(chunks) == "Eco sintético: ACP exacto"
    replacement = root / "native-selected-other"
    replacement.mkdir(mode=0o700)
    await owner.call("workspace.set", {**scope.params, "path": str(replacement)})
    with pytest.raises(ACPError, match=r"carpeta|vínculo"):
        await bridge.prompt({"sessionId": scope.session_id,
            "prompt": [{"type": "text", "text": "must not redirect"}]})
    await owner.call("workspace.set", {**scope.params, "path": workspace["path"]})
    with pytest.raises(ACPError, match="vínculo"):
        await bridge.prompt({"sessionId": scope.session_id,
            "prompt": [{"type": "text", "text": "old epoch must not revive"}]})
    # Even if a native change lands after the adapter's preflight, the atomic
    # server-side expected pair must reject before creating any inference run.
    with pytest.raises(ProductClientError, match="rpc_rejected"):
        await client.start_prompt(scope, "stale atomic pair", request_id=uuid4().hex,
            expected_workspace_id=workspace["workspace_id"],
            expected_security_generation=initial["binding"]["security_generation"])
    assert peer.calls == [("ACP exacto", False)]
    await bridge.load_session({"sessionId": scope.session_id, "cwd": workspace["path"],
                               "mcpServers": []})
    fresh = await bridge.prompt({"sessionId": scope.session_id,
        "prompt": [{"type": "text", "text": "fresh explicit load"}]})
    assert fresh["stopReason"] == "end_turn" and len(peer.calls) == 2
    await bridge.close()


async def test_real_acp_cancel_waits_for_cleanup_and_keeps_unknown_effect_metadata(client_service):
    service, _, peer, _, selected_key, clients = client_service
    client = await ProductClient.connect(service.directory, selected_key)
    clients.append(client)
    workspace = (await client.initialize())["workspaces"][0]

    async def emit(_value):
        pass

    bridge = ACPBridge(client, emit, workspace=workspace["path"],
                       workspace_id=workspace["workspace_id"])
    await bridge.initialize({"protocolVersion": 1})
    created = await bridge.new_session({"cwd": workspace["path"], "mcpServers": []})
    request_id = uuid4().hex
    prompt = asyncio.create_task(bridge.prompt({"sessionId": created["sessionId"],
        "prompt": [{"type": "text", "text": "hold"}],
        "_meta": {"jarvis": {"request_id": request_id}}}))
    try:
        await asyncio.wait_for(peer.entered.wait(), 5)
        await bridge.cancel({"sessionId": created["sessionId"]})
        await asyncio.wait_for(peer.cleaning.wait(), 5)
        assert not prompt.done()
        peer.cleanup_release.set()
        result = await asyncio.wait_for(prompt, 5)
        assert result["stopReason"] == "cancelled"
        assert result["_meta"]["jarvis"]["cleanupConfirmed"] is True
        assert result["_meta"]["jarvis"]["outcome"] == "unknown"
        assert result["_meta"]["jarvis"]["permitsReplay"] is False
        assert peer.calls == [("hold", False)]
    finally:
        peer.cleanup_release.set()
        await bridge.close()
        await asyncio.gather(prompt, return_exceptions=True)


async def test_real_acp_loaded_session_cannot_inject_a_foreign_run_reference(client_service):
    service, owner, peer, _, selected_key, clients = client_service
    client = await ProductClient.connect(service.directory, selected_key)
    clients.append(client)
    await client.initialize()
    first = await client.create_session("Primera")
    second = await client.create_session("Segunda")
    started = await client.start_prompt(first, "only first", request_id=uuid4().hex)
    await client.wait_run(first, started["run_id"])
    with pytest.raises(ProductClientError, match="rpc_rejected"):
        await client.get_run(second, started["run_id"])
    with pytest.raises(RpcError):
        await owner.call("runs.cancel", {**second.params, "run_id": started["run_id"]})
    assert (await client.messages(second))["messages"] == []
    assert peer.calls == [("only first", False)]
