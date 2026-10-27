"""Per-turn memory transport with an own stdio protocol peer, no account/model.

The peer records synthetic input only. These cases prove transport boundaries,
not MiniLM semantic quality or ChatGPT inference.
"""

from __future__ import annotations

import asyncio
import json
import sys

import pytest

from jarvis_core.codex import CodexClient, CodexError

MEMORY_PEER = r"""
import json, os, sys
from pathlib import Path
wire = Path(os.environ['HOME']) / 'memory-wire.jsonl'
threads = 0
turns = 0
def emit(value):
    print(json.dumps(value), flush=True)
for line in sys.stdin:
    message = json.loads(line)
    with wire.open('a') as stream:
        stream.write(json.dumps(message) + '\n')
    method, params = message.get('method'), message.get('params', {})
    result = {}
    if method == 'account/read':
        result = {'account': {'type': 'chatgpt'}}
    elif method == 'thread/start':
        threads += 1
        result = {'thread': {'id': 'synthetic-memory-thread-' + str(threads)}}
    elif method == 'turn/start':
        turns += 1
        result = {'turn': {'id': 'synthetic-turn-' + str(turns)}}
    if 'id' in message:
        emit({'id': message['id'], 'result': result})
    if method == 'turn/start':
        turn = 'synthetic-turn-' + str(turns)
        emit({'method': 'item/completed', 'params': {'threadId': params['threadId'],
            'turnId': turn, 'item': {'type': 'agentMessage', 'id': 'answer',
                                   'text': 'Respuesta sintética'}}})
        emit({'method': 'turn/completed', 'params': {'threadId': params['threadId'],
            'turnId': turn, 'turn': {'id': turn, 'status': 'completed'}}})
"""


def write_memory_peer(path):
    path.write_text(f"#!{sys.executable}\n" + MEMORY_PEER)
    path.chmod(0o700)
    return path.resolve()


def memory_wire(client):
    path = client.directory / "home" / "memory-wire.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.fixture
async def client(tmp_path):
    value = CodexClient(
        # Local protocol fixture gets first-spawn grace under macOS load; no
        # product transport, inference timeout or quality assertion changes.
        write_memory_peer(tmp_path / "memory-peer"),
        tmp_path.resolve() / "provider",
        timeout=8,
    )
    try:
        yield value
    finally:
        await value.aclose()


async def test_memory_is_fresh_turn_input_on_same_thread_not_profile_or_skill_history(client):
    first = await client.chat(
        "pregunta uno",
        instructions="PERFIL SINTÉTICO",
        context_data="SKILL SINTÉTICA",
        memory_context='{"notes":["CITA PRIMERA"]}',
    )
    await client.chat(
        "pregunta dos", thread_id=first["thread_id"], memory_context='{"notes":["CITA SEGUNDA"]}'
    )
    rows = memory_wire(client)
    turns = [row["params"] for row in rows if row.get("method") == "turn/start"]
    assert len(turns) == 2 and turns[0]["threadId"] == turns[1]["threadId"]
    assert "CITA PRIMERA" in turns[0]["input"][0]["text"]
    assert "CITA SEGUNDA" in turns[1]["input"][0]["text"]
    assert "CITA PRIMERA" not in json.dumps(turns[1])
    assert [turn["input"][-1]["text"] for turn in turns] == ["pregunta uno", "pregunta dos"]
    assert all(len(turn["input"]) == 2 for turn in turns)
    for row in rows:
        if row.get("method") in {"thread/start", "thread/inject_items"}:
            assert "CITA PRIMERA" not in json.dumps(row)
            assert "CITA SEGUNDA" not in json.dumps(row)
    assert len([row for row in rows if row.get("method") == "thread/start"]) == 1


async def test_resumed_owned_thread_receives_current_capsule_after_client_restart(client):
    first = await client.chat("turno anterior", memory_context="CITA VIEJA")
    await client.aclose()
    replacement = CodexClient(client.executable, client.directory, timeout=8)
    try:
        await replacement.chat(
            "turno actual", thread_id=first["thread_id"], memory_context="CITA ACTUAL"
        )
        rows = memory_wire(replacement)
        assert [
            row["params"]["threadId"] for row in rows if row.get("method") == "thread/resume"
        ] == [first["thread_id"]]
        latest = [row["params"] for row in rows if row.get("method") == "turn/start"][-1]
        assert "CITA ACTUAL" in latest["input"][0]["text"]
        assert "CITA VIEJA" not in json.dumps(latest)
        assert not any(row.get("method") in {"thread/list", "thread/read"} for row in rows)
    finally:
        await replacement.aclose()


async def test_final_write_guard_rechecks_after_waiting_and_never_sends_revoked_capsule(client):
    first = await client.chat("calentar")
    revoked = False

    def check(method):
        if method == "turn/start" and revoked:
            raise CodexError("memory_context_changed")

    client.before_io = check
    await client._write_lock.acquire()
    task = asyncio.create_task(
        client.chat("rechazar", thread_id=first["thread_id"], memory_context="CITA REVOCADA")
    )
    try:
        await asyncio.sleep(0.02)
        assert not task.done()
        revoked = True
    finally:
        client._write_lock.release()
    with pytest.raises(CodexError, match="memory_context_changed"):
        await task
    rows = memory_wire(client)
    assert len([row for row in rows if row.get("method") == "turn/start"]) == 1
    assert "CITA REVOCADA" not in json.dumps(rows)
    assert not client._events and not client._pending


@pytest.mark.parametrize("capsule", ["ñ" * 4001, None, []])
async def test_memory_context_byte_and_type_limits_fail_before_start(client, capsule):
    with pytest.raises(ValueError, match="codex_context_invalid"):
        await client.chat("consulta", memory_context=capsule)
    assert client._process is None and not client.directory.exists()
