"""The UI corpus and SDK peer are explicitly synthetic, isolated and repeatable."""

import asyncio
import importlib.util
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from jarvis_core.codex import CodexClient
from jarvis_core.tools import ApprovalAuthority, ToolCatalog
from jarvis_core.workspace import WorkspaceTools

_SPEC = importlib.util.spec_from_file_location(
    "jarvis_core_sessions_fixture",
    Path(__file__).resolve().parents[1] / "script/core_sessions_fixture.py",
)
fixture = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fixture)


@pytest.fixture
def corpus():
    root = fixture._TEMP / ("jarvis-core-sessions-test-" + uuid4().hex[:8])
    manifest = fixture.prepare(root)
    try:
        yield root, manifest
    finally:
        shutil.rmtree(root)


def test_prepare_has_nonempty_synthetic_context_receipts_agents_and_readonly_oracle(corpus):
    root, manifest = corpus
    before = {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    oracle = fixture.observe(root)
    assert oracle["seed_messages_preserved"] and oracle["seed_runs_preserved"]
    assert oracle["messages_count"] == 9 and oracle["runs_count"] == 5
    assert oracle["run_statuses"] == {"completed": 4, "failed": 1}
    assert manifest["agents"] == {"jarvis": 2, "analista": 1}
    assert manifest["desktop_key_prepared"] is False
    assert not (root / "profile.clients").exists()
    assert not (root / "profile" / "provider").exists()
    assert oracle["sdk_launches"] == 0 and oracle["method_counts"] == {}
    assert oracle["contract_unchanged"] and oracle["peer_unchanged"]
    assert before == {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}


async def test_own_peer_tools_then_two_restarts_never_repeat_historical_or_tool_requests(corpus):
    root, manifest = corpus
    executable = Path(manifest["codex_fixture"])
    client = CodexClient(executable, root / "profile" / "provider", timeout=3)
    calls = []

    async def tool(name, arguments):
        assert name == "workspace_write" and arguments == {
            "path": fixture.CHILD_FILE,
            "text": fixture.CHILD_BYTES.decode(),
        }
        calls.append(arguments)
        (root / "workspace" / fixture.CHILD_FILE).write_bytes(fixture.CHILD_BYTES)
        (root / "workspace" / fixture.CHILD_FILE).chmod(0o600)
        return {
            "success": True,
            "contentItems": [{"type": "inputText", "text": "Recibo sintético"}],
        }

    workspace = WorkspaceTools(root / "workspace")
    authority = ApprovalAuthority()
    specs = ToolCatalog(workspace.definitions(), workspace.specs(), authority=authority).specs()
    try:
        answer = await client.chat("PRUEBA HIJO 1", tool_specs=specs, on_tool_call=tool)
        assert "HIJO 1" in answer["answer"]
        written = await client.chat("PRUEBA HIJO 2", tool_specs=specs, on_tool_call=tool)
        assert len(calls) == 1 and "REPORTE SINTETICO" in written["answer"]
        thread = answer["thread_id"]
    finally:
        await client.aclose()
        await authority.close()
    for index in range(2):
        resumed = CodexClient(executable, root / "profile" / "provider", timeout=3)
        try:
            result = await resumed.chat(
                "PRUEBA REINICIO " + str(index),
                thread_id=thread,
                tool_specs=specs,
                on_tool_call=tool,
            )
            assert "REINICIO" in result["answer"]
        finally:
            await resumed.aclose()
    oracle = fixture.observe(root)
    assert oracle["sdk_launches"] == 3 and oracle["method_counts"]["turn/start"] == 4
    assert len(oracle["tool_proposals"]) == len(oracle["tool_replies"]) == 1
    assert oracle["child_file_matches"] and oracle["contract_unchanged"]
    assert oracle["seed_messages_preserved"] and oracle["seed_runs_preserved"]
    assert oracle["historical_unknown_replayed"] is False


async def test_own_peer_holds_then_steers_and_cancels_without_effects(corpus):
    root, manifest = corpus
    client = CodexClient(Path(manifest["codex_fixture"]), root / "profile" / "provider", timeout=3)
    tasks = []
    thread = []
    try:
        task = asyncio.create_task(client.chat("ESPERAR PRUEBA NUCLEO", on_thread=thread.append))
        tasks.append(task)
        async with asyncio.timeout(5):
            while not thread or client.active_turn_id(thread[0]) is None:
                await asyncio.sleep(0.005)
        turn = client.active_turn_id(thread[0])
        result = await client.steer(
            thread[0], turn, "PRUEBA CORRECCION 7319", request_id=uuid4().hex
        )
        assert result["accepted"] is True
        assert "7319" in (await task)["answer"]
        second = asyncio.create_task(client.chat("ESPERAR CANCELACION", thread_id=thread[0]))
        tasks.append(second)
        async with asyncio.timeout(5):
            while client.active_turn_id(thread[0]) is None:
                await asyncio.sleep(0.005)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        assert client.active_turn_id(thread[0]) is None
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.aclose()
    oracle = fixture.observe(root)
    assert len(oracle["steering_requests"]) == 1
    assert oracle["tool_proposals"] == [] and oracle["child_file_present"] is False
    assert oracle["method_counts"]["turn/interrupt"] == 1


def test_fixture_refuses_existing_ownerlike_path_and_changed_profile(corpus, tmp_path):
    root, _ = corpus
    with pytest.raises(FileExistsError):
        fixture.prepare(root)
    with pytest.raises(ValueError):
        fixture.prepare(tmp_path / "owner-profile")
    (root / "profile").rename(root / "original-profile")
    (root / "profile").mkdir(mode=0o700)
    with pytest.raises(ValueError, match="fixture_identity_mismatch"):
        fixture.observe(root)
