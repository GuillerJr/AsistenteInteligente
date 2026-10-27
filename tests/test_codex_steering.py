"""Official-shaped turn/steer over our own stdio peer, without an account."""

import asyncio
import json
import sys
from uuid import uuid4

import pytest

from jarvis_core.codex import CodexClient, CodexError

PEER = r'''
import json, os, sys
from pathlib import Path
wire = Path(os.environ['HOME']) / 'steering-wire.jsonl'
def emit(value):
    print(json.dumps(value), flush=True)
thread, turn = 'owned-thread', 'owned-turn'
for line in sys.stdin:
    message = json.loads(line)
    with wire.open('a') as stream:
        stream.write(json.dumps(message) + '\n')
    method, params = message.get('method'), message.get('params', {})
    result = {}
    if method == 'account/read':
        result = {'account': {'type': 'chatgpt'}}
    elif method == 'thread/start':
        result = {'thread': {'id': thread}}
    elif method == 'turn/start':
        result = {'turn': {'id': turn}}
    elif method == 'turn/steer':
        assert params['threadId'] == thread
        assert params['expectedTurnId'] == turn
        assert len(params['clientUserMessageId']) == 32
        result = {'turnId': 'different' if params['input'][0]['text'] == 'bad-ack' else turn}
    if 'id' in message and method:
        emit({'id': message['id'], 'result': result})
    if method == 'turn/steer' and params['input'][0]['text'] != 'bad-ack':
        emit({'method': 'item/completed', 'params': {'threadId': thread, 'turnId': turn,
             'item': {'type': 'agentMessage', 'id': 'answer',
                      'text': params['input'][0]['text']}}})
        emit({'method': 'turn/completed', 'params': {'threadId': thread,
             'turn': {'id': turn, 'status': 'completed'}}})
    elif method == 'turn/interrupt':
        emit({'method': 'turn/completed', 'params': {'threadId': thread,
             'turn': {'id': turn, 'status': 'interrupted'}}})
'''


@pytest.fixture
async def steering_client(tmp_path):
    script = tmp_path / "own-stdio-peer"
    script.write_text(f"#!{sys.executable}\n" + PEER)
    script.chmod(0o700)
    client = CodexClient(script.resolve(), tmp_path.resolve() / "state", timeout=1)
    tasks = []

    async def start():
        task = asyncio.create_task(client.chat("espera", turn_timeout=3))
        tasks.append(task)
        async with asyncio.timeout(2):
            while client.active_turn_id("owned-thread") is None:
                await asyncio.sleep(.005)
        return task

    try:
        yield client, start
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.aclose()


def wire(client):
    path = client.directory / "home" / "steering-wire.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


async def test_steer_goes_to_exact_owned_turn_and_changes_actual_peer_response(steering_client):
    client, start = steering_client
    task = await start()
    identity = uuid4().hex
    receipt = await client.steer("owned-thread", "owned-turn", "MARCADOR NUEVO",
                                 request_id=identity)
    assert receipt == {"accepted": True, "turn_id": "owned-turn"}
    assert (await task)["answer"] == "MARCADOR NUEVO"
    requests = [row for row in wire(client) if row.get("method") == "turn/steer"]
    assert len(requests) == 1
    assert requests[0]["params"] == {
        "threadId": "owned-thread", "expectedTurnId": "owned-turn",
        "clientUserMessageId": identity,
        "input": [{"type": "text", "text": "MARCADOR NUEVO"}]}
    assert client.active_turn_id("owned-thread") is None


@pytest.mark.parametrize("thread,turn", [("foreign-thread", "owned-turn"),
                                        ("owned-thread", "different-turn")])
async def test_foreign_or_stale_turn_rejected_before_wire_write(steering_client, thread, turn):
    client, start = steering_client
    await start()
    with pytest.raises(CodexError, match="codex_steer_turn_changed"):
        await client.steer(thread, turn, "sintético", request_id=uuid4().hex)
    assert not any(row.get("method") == "turn/steer" for row in wire(client))


async def test_mismatched_ack_is_uncertain_and_never_retried(steering_client):
    client, start = steering_client
    await start()
    with pytest.raises(CodexError, match="codex_steer_unconfirmed"):
        await client.steer("owned-thread", "owned-turn", "bad-ack", request_id=uuid4().hex)
    assert len([row for row in wire(client) if row.get("method") == "turn/steer"]) == 1


@pytest.mark.parametrize("message", [None, True, "", " ", "a\0b", "ñ" * 4001])
async def test_invalid_input_never_starts_or_discovers_a_provider(steering_client, message):
    client, _ = steering_client
    with pytest.raises(ValueError, match="codex_steer_invalid"):
        await client.steer("owned-thread", "owned-turn", message, request_id=uuid4().hex)
    assert client._process is None
