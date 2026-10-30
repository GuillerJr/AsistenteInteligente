"""Selected ACP v1 wire contracts; no actual service, account or OS tool effects."""

import asyncio
import json
from dataclasses import replace

import pytest

from jarvis_core.product_acp import ACPBridge, ACPError, _content, serve
from jarvis_core.product_client import ProductClient, ProductClientLimits, bounded_json

SESSION = "a" * 32
RUN = "b" * 32
WORKSPACE = "c" * 32
PATH = "/fixture/project"


class ACPWire:
    def __init__(self):
        self.calls = []
        self.started = asyncio.Event()
        self.mode = "complete"
        self.cancelled = False
        self.cleanup = True
        self.now = 0
        self.permission_seen = False

    async def sleep(self, amount):
        self.now += amount
        await asyncio.sleep(0)

    async def request(self, method, params):
        self.calls.append((method, params))
        if method == "product.initialize":
            return {"workspaces": [{"workspace_id": WORKSPACE, "path": PATH}]}
        if method == "sessions.create":
            return {"session": {"session_id": SESSION, "temporary": params["temporary"]}}
        if method == "product.snapshot":
            return {"scope": {**params, "workspace_id": WORKSPACE},
                    "binding": {"workspace_id": WORKSPACE, "security_generation": 0}}
        if method == "sessions.messages":
            return {"messages": [
                {"session_id": SESSION, "sequence": 1, "role": "user", "text": "Usuario"},
                {"session_id": SESSION, "sequence": 2, "role": "system", "text": "CONTROL_INTERNO"},
                {"session_id": SESSION, "sequence": 3, "role": "assistant", "text": "Respuesta"},
            ], "has_more": False}
        if method == "runs.start":
            self.started.set()
            return {"run_id": params["request_id"], "status": "queued"}
        if method == "runs.cancel":
            self.cancelled = True
            return {"run_id": params["run_id"], "accepted": True}
        if method == "events.poll":
            rows = [{"sequence": 1, "kind": "answer.delta", "payload": {"text": "Res"}}]
            return {"events": rows if params["after"] == 0 else [], "has_more": False}
        if method == "runs.get":
            pending = ((self.mode == "cancel" and not self.cancelled)
                       or (self.mode == "permission" and not self.permission_seen))
            status = "waiting_approval" if pending and self.mode == "permission" else "running"
            return {"run": {"run_id": params["run_id"],
                "status": status if pending else "cancelled" if self.cancelled else "completed",
                "outcome": "pending" if pending else "unknown" if self.cancelled else "known",
                "cleanup_confirmed": self.cleanup if not pending else False,
                "answer": None if pending or self.cancelled else "Respuesta"}}
        if method == "approvals.list":
            self.permission_seen = True  # A synthetic native owner resolves it outside ACP.
            return {"approvals": [{"approval_id": "d" * 32, "session_id": SESSION,
                "run_id": params["run_id"], "tool_name": "synthetic_action", "digest": "e" * 64,
                "arguments": {"owner_secret": "SYNTHETIC_SECRET_NOT_FOR_OUTPUT"}}]}
        raise AssertionError(method)

    def client(self):
        return ProductClient(self.request,
            limits=replace(ProductClientLimits(), poll_interval=0.01),
            clock=lambda: self.now, sleep=self.sleep)


async def bridge_fixture(*, temporary=False):
    wire, output = ACPWire(), []

    async def emit(value):
        output.append(value)

    bridge = ACPBridge(wire.client(), emit, workspace=PATH, workspace_id=WORKSPACE,
                       temporary=temporary)
    return wire, bridge, output


async def initialize(bridge):
    return await bridge.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": 1, "clientCapabilities": {"terminal": True}}})


async def new(bridge):
    return await bridge.dispatch({"jsonrpc": "2.0", "id": 2, "method": "session/new",
        "params": {"cwd": PATH, "mcpServers": []}})


def prompt_params(**extra):
    return {"sessionId": SESSION, "prompt": [{"type": "text", "text": "  texto exacto  "}],
            "_meta": {"jarvis": {"request_id": RUN}}, **extra}


async def test_handshake_advertises_only_selected_local_v1_capabilities():
    _, bridge, _ = await bridge_fixture()
    with pytest.raises(ACPError, match="Inicializa"):
        await new(bridge)
    result = await initialize(bridge)
    assert result["protocolVersion"] == 1
    assert result["agentCapabilities"]["loadSession"] is True
    assert result["agentCapabilities"]["promptCapabilities"]["image"] is False
    assert result["_meta"]["jarvis"]["profile"] == "local-v1-subset"
    assert result["_meta"]["jarvis"]["permissions"] == "native_app_only"
    assert "fs" not in result["agentCapabilities"] and "terminal" not in result["agentCapabilities"]


async def test_new_requires_explicit_matching_cwd_and_never_launches_supplied_mcp():
    wire, bridge, _ = await bridge_fixture(temporary=True)
    await initialize(bridge)
    for params in [{"cwd": "/other", "mcpServers": []},
                   {"cwd": PATH, "mcpServers": [{"name": "unsafe", "command": "/bin/sh"}]}]:
        with pytest.raises(ACPError):
            await bridge.new_session(params)
    assert not any(method == "sessions.create" for method, _ in wire.calls)
    assert (await new(bridge))["sessionId"] == SESSION
    assert all(params["temporary"] is True for method, params in wire.calls
               if method == "product.initialize")
    created = next(params for method, params in wire.calls if method == "sessions.create")
    assert created["temporary"] is True and created["workspace_id"] == WORKSPACE


async def test_prompt_streams_updates_then_one_real_completion_without_rewriting_input():
    wire, bridge, output = await bridge_fixture()
    await initialize(bridge)
    await new(bridge)
    result = await bridge.prompt(prompt_params())
    assert result["stopReason"] == "end_turn"
    chunks = [row["params"]["update"]["content"]["text"] for row in output]
    assert "".join(chunks) == "Respuesta"
    started = next(params for method, params in wire.calls if method == "runs.start")
    assert started["message"] == "  texto exacto  " and started["request_id"] == RUN
    with pytest.raises(ACPError, match="ya se utilizó"):
        await bridge.prompt(prompt_params())
    assert len([method for method, _ in wire.calls if method == "runs.start"]) == 1


async def test_permissions_stay_in_native_authority_and_raw_arguments_are_not_relayed():
    wire, bridge, output = await bridge_fixture()
    wire.mode = "permission"
    await initialize(bridge)
    await new(bridge)
    result = await bridge.prompt(prompt_params())
    assert result["stopReason"] == "end_turn"
    assert any(row["params"]["update"]["sessionUpdate"] == "tool_call" for row in output)
    assert "SYNTHETIC_SECRET_NOT_FOR_OUTPUT" not in json.dumps(output)
    assert not any(method == "approvals.decide" for method, _ in wire.calls)
    assert not any(row.get("method") == "session/request_permission" for row in output)


@pytest.mark.parametrize("cleanup", [False, True])
async def test_cancel_is_concurrent_and_requires_cleanup_receipt_not_accepted_ack(cleanup):
    wire, bridge, _ = await bridge_fixture()
    wire.mode, wire.cleanup = "cancel", cleanup
    await initialize(bridge)
    await new(bridge)
    task = asyncio.create_task(bridge.prompt(prompt_params()))
    await wire.started.wait()
    await bridge.cancel({"sessionId": SESSION})
    if cleanup:
        result = await task
        assert result["stopReason"] == "cancelled"
        assert result["_meta"]["jarvis"]["outcome"] == "unknown"
        assert result["_meta"]["jarvis"]["cleanupConfirmed"] is True
    else:
        with pytest.raises(ACPError, match="sin confirmar"):
            await task
        assert SESSION in bridge.active
        with pytest.raises(ACPError, match="pendiente de cierre"):
            await bridge.prompt(prompt_params(_meta={"jarvis": {"request_id": "f" * 32}}))
    await bridge.close()
    assert len([method for method, _ in wire.calls if method == "runs.cancel"]) == 1


async def test_load_replays_only_selected_user_facing_history_without_internal_controls():
    wire, bridge, output = await bridge_fixture()
    await initialize(bridge)
    result = await bridge.load_session({"sessionId": SESSION, "cwd": PATH, "mcpServers": []})
    assert result == {}
    assert [row["params"]["update"]["sessionUpdate"] for row in output] == [
        "user_message_chunk", "agent_message_chunk"]
    assert "CONTROL_INTERNO" not in json.dumps(output)
    assert all(params["session_id"] == SESSION for method, params in wire.calls
               if method in {"product.snapshot", "sessions.messages"})


def test_text_and_resource_references_are_prompt_data_never_file_or_shell_operations():
    assert _content([{"type": "text", "text": "a"}, {"type": "text", "text": " b"}]) == "a b"
    link = {"type": "resource_link", "uri": "file:///fixture/$(not-executed)", "name": "doc"}
    assert json.loads(_content([link])) == link
    with pytest.raises(ACPError):
        _content([{"type": "image", "data": "AA==", "mimeType": "image/png"}])


async def test_unknown_sessions_and_rpc_approval_methods_cannot_cross_scope():
    wire, bridge, _ = await bridge_fixture()
    await initialize(bridge)
    with pytest.raises(ACPError, match="no está cargada"):
        await bridge.prompt(prompt_params())
    with pytest.raises(ACPError, match="no está disponible"):
        await bridge.dispatch({"jsonrpc": "2.0", "id": 4,
            "method": "approvals.decide", "params": {"allow": True}})
    assert not any(method in {"runs.start", "approvals.decide"} for method, _ in wire.calls)


async def test_stdio_rejects_duplicate_request_ids_and_bad_frames_without_raw_logging():
    wire, bridge, _ = await bridge_fixture()
    reader = asyncio.StreamReader()

    class Writer:
        def __init__(self):
            self.frames = []

        def write(self, data):
            self.frames.append(json.loads(data))

        async def drain(self):
            pass

    writer = Writer()
    request = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
               "params": {"protocolVersion": 1}}
    reader.feed_data(bounded_json(request) + bounded_json(request) + b'{"raw_secret":invalid}\n')
    reader.feed_eof()
    await serve(reader, writer, bridge)
    assert writer.frames[0]["result"]["protocolVersion"] == 1
    assert writer.frames[1]["error"]["code"] == -32600
    assert writer.frames[2]["error"]["code"] == -32700
    assert "raw_secret" not in json.dumps(writer.frames)
    assert len([method for method, _ in wire.calls if method == "product.initialize"]) == 1
