"""Real stdio subprocess tests against a synthetic Codex protocol peer.

Only the transport and host callbacks are exercised: these tests do not use an
account, a model, a network service, personal history or real approval decisions.
"""

import asyncio
import hashlib
import json
import os
import sys

import pytest

from jarvis_core.codex import CodexClient, CodexError

# Real stdio startup and replies need scheduling headroom. Inference, callback
# and cancellation deadlines below remain independently short and explicit.
PEER_RPC_TIMEOUT = 5

TOOL = {
    "name": "fixture_write",
    "description": "Synthetic fixture tool; never an owner permission.",
    "inputSchema": {
        "type": "object", "properties": {"value": {"type": "string"}},
        "required": ["value"], "additionalProperties": False,
    },
}


# This independently authored process speaks the documented wire shapes. It has
# no import of jarvis_core and never executes Codex or discovers existing profiles.
PEER = r'''
import json, os, sys, time
from pathlib import Path

home = Path(os.environ['HOME'])
log = home / 'synthetic-wire.jsonl'
threads = 0
turns = 0
request_number = 10000
calls = {}
call_params = {}
active = {}

def emit(message):
    print(json.dumps(message), flush=True)

def finish(state, status='completed'):
    if state.get('finished'):
        return
    state['finished'] = True
    if status == 'completed':
        emit({'method': 'item/completed', 'params': {
            'threadId': state['thread'], 'turnId': state['turn'],
            'item': {'type': 'agentMessage', 'id': 'answer', 'text': 'Recibido sintético'},
        }})
    emit({'method': 'turn/completed', 'params': {
        'threadId': state['thread'], 'turnId': state['turn'],
        'turn': {'id': state['turn'], 'status': status},
    }})

def call(state, *, overrides=None, method='item/tool/call', call_id=None):
    global request_number
    request_number += 1
    identity = request_number
    params = {
        'threadId': state['thread'], 'turnId': state['turn'],
        'callId': call_id or ('call-' + str(identity)), 'namespace': None,
        'tool': 'fixture_write', 'arguments': {'value': str(identity)},
    }
    params.update(overrides or {})
    calls[identity] = state
    call_params[identity] = dict(params)
    emit({'id': identity, 'method': method, 'params': params})

for line in sys.stdin:
    message = json.loads(line)
    with log.open('a') as stream:
        stream.write(json.dumps(message) + '\n')
    method = message.get('method')
    params = message.get('params', {})
    if method is None:
        state = calls.get(message.get('id'))
        if state:
            state['responses'].append(message)
            scenario = state['scenario']
            if scenario.startswith('delivery-') and scenario != 'delivery-no-ack':
                request = call_params[message['id']]
                item = {'type': 'dynamicToolCall', 'id': request['callId'],
                        'tool': request['tool'], 'arguments': request['arguments'],
                        'status': 'completed' if message['result']['success'] else 'failed',
                        **message['result']}
                completed = {'threadId': state['thread'], 'turnId': state['turn'], 'item': item}
                if scenario == 'delivery-content-mismatch':
                    item['contentItems'] = [{'type': 'inputText', 'text': 'OTRO CONTENIDO'}]
                elif scenario == 'delivery-no-items':
                    item.pop('contentItems')
                elif scenario == 'delivery-no-success':
                    item.pop('success')
                elif scenario == 'delivery-wrong-arguments':
                    item['arguments'] = {'value': 'DIFFERENT'}
                elif scenario == 'delivery-wrong-tool':
                    item['tool'] = 'different_tool'
                elif scenario == 'delivery-wrong-id':
                    item['id'] = 'different-call'
                elif scenario == 'delivery-wrong-thread':
                    completed['threadId'] = 'different-thread'
                elif scenario == 'delivery-wrong-turn':
                    completed['turnId'] = 'different-turn'
                elif scenario == 'delivery-namespace':
                    item['namespace'] = 'different_namespace'
                elif scenario == 'delivery-success-mismatch':
                    item['success'] = not item['success']
                elif scenario == 'delivery-bad-status':
                    item['status'] = {'invalid': 'SYNTHETIC-PRIVATE-STATUS'}
                emit({'method': 'item/completed', 'params': completed})
                if scenario == 'delivery-duplicate-ack':
                    emit({'method': 'item/completed', 'params': completed})
            if state['scenario'] == 'replay' and len(state['responses']) == 1:
                call(state, call_id='same-logical-call')
            if state['scenario'] == 'sequential-limit' and len(state['responses']) < 65:
                call(state)
            if state['scenario'] == 'sequential':
                if len(state['responses']) == 1:
                    call(state)
            if len(state['responses']) == state['expected'] and state['scenario'] not in {
                'hold', 'idle', 'malformed-hold', 'post-tool-idle',
            }:
                finish(state)
        continue
    result = {}
    if method == 'initialize':
        assert params['capabilities']['experimentalApi'] is True
        assert params['clientInfo']['name'] == 'jarvis'
    elif method == 'account/read':
        result = {'account': {'type': 'chatgpt'}}
    elif method == 'thread/start':
        threads += 1
        assert params['sandbox'] == 'read-only'
        assert params['approvalPolicy'] == 'untrusted'
        result = {'thread': {'id': 'synthetic-thread-' + str(threads)}}
    elif method == 'turn/start':
        turns += 1
        result = {'turn': {'id': 'synthetic-turn-' + str(turns)}}
    if 'id' in message:
        emit({'id': message['id'], 'result': result})
    if method == 'turn/interrupt':
        state = active[params['threadId']]
        finish(state, 'interrupted')
    if method != 'turn/start':
        continue
    scenario = params['input'][0]['text']
    state = {
        'thread': params['threadId'], 'turn': result['turn']['id'],
        'scenario': scenario, 'responses': [], 'expected': 1,
    }
    active[state['thread']] = state
    if scenario == 'plain':
        finish(state)
    elif scenario == 'idle':
        continue
    elif scenario == 'sequential':
        state['expected'] = 2
        call(state)
    elif scenario == 'malformed-hold':
        call(state, overrides={'turnId': 'foreign-turn'})
    elif scenario == 'parallel':
        state['expected'] = 2
        call(state)
        call(state)
    elif scenario == 'fanout':
        state['expected'] = 20
        for _ in range(20):
            call(state)
    elif scenario == 'replay':
        state['expected'] = 2
        call(state, call_id='same-logical-call')
    elif scenario == 'shared-logical-call':
        call(state, call_id='same-logical-call')
    elif scenario == 'sequential-limit':
        state['expected'] = 65
        call(state)
    elif scenario == 'unknown-method':
        call(state, method='item/commandExecution/requestApproval')
    elif scenario == 'unknown-tool':
        call(state, overrides={'tool': 'not_offered'})
    elif scenario == 'wrong-thread':
        call(state, overrides={'threadId': 'foreign-thread'})
    elif scenario == 'wrong-turn':
        call(state, overrides={'turnId': 'foreign-turn'})
    elif scenario == 'namespace':
        call(state, overrides={'namespace': 'foreign'})
    elif scenario == 'invalid-arguments':
        call(state, overrides={'arguments': '{"value":"not-an-object"}'})
    elif scenario == 'missing-call-id':
        call(state, overrides={'callId': ''})
    else:
        call(state)
'''


@pytest.fixture
def tool_peer(tmp_path):
    executable = tmp_path / "synthetic-codex-tools-peer"
    # This stdlib-only peer needs neither the project's venv discovery nor user
    # site imports. Pin the current interpreter and start it in isolated mode.
    executable.write_text(f"#!{os.path.realpath(sys.executable)} -I\n" + PEER)
    executable.chmod(0o700)
    return executable


def wire(client):
    path = client.directory / "home" / "synthetic-wire.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def tool_replies(client):
    return [
        row for row in wire(client)
        if "method" not in row and type(row.get("id")) is int and row["id"] > 10000
    ]


async def wait_until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.005)


def confirmed(text="recibo sintético"):
    return {"success": True, "contentItems": [{"type": "inputText", "text": text}]}


async def test_dynamic_tool_round_trip_has_official_shapes_and_one_real_fixture_effect(
    tmp_path, tool_peer,
):
    client = CodexClient(tool_peer, tmp_path / "profile", timeout=PEER_RPC_TIMEOUT)
    effect = tmp_path / "synthetic-effect.txt"
    calls = []

    async def callback(name, arguments):
        calls.append((name, arguments))
        effect.write_text(arguments["value"])
        return confirmed(json.dumps({
            "status": "succeeded", "sha256": hashlib.sha256(effect.read_bytes()).hexdigest(),
        }))

    try:
        answer = await client.chat("one", tool_specs=[TOOL], on_tool_call=callback)
        assert answer == {"thread_id": "synthetic-thread-1", "answer": "Recibido sintético"}
        assert calls == [("fixture_write", {"value": "10001"})]
        assert effect.read_text() == "10001"
        start = next(row for row in wire(client) if row.get("method") == "thread/start")
        assert start["params"]["dynamicTools"] == [TOOL]
        reply = tool_replies(client)
        assert len(reply) == 1 and reply[0]["result"]["success"] is True
        item = reply[0]["result"]["contentItems"][0]
        assert item["type"] == "inputText"
        assert json.loads(item["text"])["sha256"] == hashlib.sha256(effect.read_bytes()).hexdigest()
        assert client._tool_turns == {} and client._server_requests == set()
    finally:
        await client.aclose()


@pytest.mark.parametrize("scenario,state", [
    ("delivery-match", "matched"), ("delivery-duplicate-ack", "matched"),
    ("delivery-no-items", "unconfirmed"), ("delivery-no-success", "unconfirmed"),
    ("delivery-bad-status", "unconfirmed"),
    ("delivery-no-ack", "unconfirmed"), ("delivery-wrong-id", "unconfirmed"),
    ("delivery-wrong-thread", "unconfirmed"), ("delivery-wrong-turn", "unconfirmed"),
    ("delivery-content-mismatch", "mismatch"), ("delivery-wrong-tool", "mismatch"),
    ("delivery-wrong-arguments", "mismatch"), ("delivery-namespace", "mismatch"),
    ("delivery-success-mismatch", "mismatch"),
])
async def test_dynamic_tool_delivery_ack_is_correlated_hashed_and_never_replayed(
    tmp_path, tool_peer, scenario, state,
):
    client = CodexClient(tool_peer, tmp_path / "profile", timeout=PEER_RPC_TIMEOUT)
    calls, events = [], []
    private_text = "SYNTHETIC-PRIVATE-TOOL-TEXT-7126"
    image = "data:image/jpeg;base64,U1lOVEhFVElDLVBSSVZBVEU="
    result = {"success": True, "contentItems": [
        {"type": "inputText", "text": private_text},
        {"type": "inputText", "text": "Datos externos sin verificación"},
        {"type": "inputImage", "imageUrl": image},
    ]}

    async def callback(name, arguments):
        calls.append((name, arguments))
        return result

    try:
        answer = await client.chat(scenario, tool_specs=[TOOL], on_tool_call=callback,
                                   on_event=lambda *event: events.append(event))
        assert answer["answer"] == "Recibido sintético"
        assert calls == [("fixture_write", {"value": "10001"})]
        assert len(tool_replies(client)) == 1
        diagnostics = [(kind, data) for kind, data in events if kind.startswith("tool.reply.")]
        sent = [data for kind, data in diagnostics if kind == "tool.reply.sent"]
        acks = [data for kind, data in diagnostics if kind == "tool.reply.ack"]
        assert len(sent) == 1 and any(ack["ack_state"] == state for ack in acks)
        assert sent[0]["original_write_attempted"] and sent[0]["original_write_confirmed"]
        assert sent[0]["content_items_count"] == 3 and sent[0]["text_items_count"] == 2
        assert sent[0]["image_items_count"] == 1
        assert sent[0]["text_bytes"] == sum(
            len(item["text"].encode()) for item in result["contentItems"] if "text" in item)
        expected = hashlib.sha256(json.dumps(result["contentItems"], sort_keys=True,
            ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()).hexdigest()
        assert sent[0]["content_items_sha256"] == expected
        if state == "matched":
            assert len(acks) == 1 and acks[0]["matches"] is True
            assert acks[0]["original_reply_delivered"] is True
            assert acks[0]["content_items_sha256"] == acks[0]["observed_content_items_sha256"]
            assert acks[0]["arguments_sha256"] == acks[0]["observed_arguments_sha256"]
        else:
            assert not any(ack["matches"] is True for ack in acks)
            assert not any(ack["original_reply_delivered"] for ack in acks)
        assert not any(data["physical_verified"] or data["model_consumption_verified"]
                       for _, data in diagnostics)
        serialized = json.dumps(diagnostics)
        for secret in (private_text, image, "synthetic-thread-1", "synthetic-turn-1", "call-10001"):
            assert secret not in serialized
        assert diagnostics[0][0] == "tool.reply.sent"
        assert client._tool_turns == {} and not client._server_requests
    finally:
        await client.aclose()


@pytest.mark.parametrize("phase", ["provider_guard", "callback"])
async def test_fallback_ack_never_claims_original_receipt_delivery(tmp_path, tool_peer, phase):
    from jarvis_core.provider_choice import ProviderChoiceError

    client = CodexClient(tool_peer, tmp_path / "profile", timeout=PEER_RPC_TIMEOUT)
    events, calls = [], []
    private_text = "SYNTHETIC-PRIVATE-RESULT-OR-ERROR-2391"

    def before_io(method):
        if method == "reply" and phase == "provider_guard":
            raise ProviderChoiceError("provider_choice_changed")

    async def callback(name, arguments):
        calls.append((name, arguments))
        if phase == "callback":
            raise RuntimeError(private_text)
        return confirmed(private_text)

    client.before_io = before_io
    try:
        await client.chat("delivery-match", tool_specs=[TOOL], on_tool_call=callback,
                          on_event=lambda *event: events.append(event))
        assert len(calls) == 1
        replies = tool_replies(client)
        assert len(replies) == 1 and replies[0]["result"]["success"] is False
        diagnostics = [(kind, data) for kind, data in events if kind.startswith("tool.reply.")]
        failure = next(data for kind, data in diagnostics if kind == "tool.reply.failure")
        fallback = next(data for kind, data in diagnostics if kind == "tool.reply.fallback")
        ack = next(data for kind, data in diagnostics if kind == "tool.reply.ack")
        assert failure["failure_code"] == (
            "provider_choice_changed" if phase == "provider_guard" else "tool_reply_unavailable")
        assert failure["exception_class"] == (
            "ProviderChoiceError" if phase == "provider_guard" else "RuntimeError")
        assert not failure["original_write_attempted"]
        assert fallback["reply_kind"] == "fallback" and fallback["write_confirmed"]
        assert not fallback["original_write_attempted"] and not fallback["original_write_confirmed"]
        assert ack["reply_kind"] == "fallback" and ack["matches"] is True
        assert ack["original_reply_delivered"] is False
        assert private_text not in json.dumps(diagnostics)
        assert not any(kind == "tool.reply.sent" for kind, _ in diagnostics)
    finally:
        await client.aclose()


async def test_uncertain_reply_write_never_sends_second_contradictory_result(
    tmp_path, tool_peer, monkeypatch,
):
    client = CodexClient(tool_peer, tmp_path / "profile", timeout=PEER_RPC_TIMEOUT)
    real_write = client._write
    calls, events = [], []

    async def lost_confirmation(message, **options):
        await real_write(message, **options)
        if "method" not in message and "result" in message:
            raise CodexError("codex_disconnected")

    async def callback(name, arguments):
        calls.append((name, arguments))
        return confirmed("SYNTHETIC-PRIVATE-RETURNED-TEXT")

    monkeypatch.setattr(client, "_write", lost_confirmation)
    try:
        await client.chat("delivery-match", tool_specs=[TOOL], on_tool_call=callback,
                          on_event=lambda *event: events.append(event))
        assert len(calls) == len(tool_replies(client)) == 1
        sent = [data for kind, data in events if kind == "tool.reply.sent"]
        acks = [data for kind, data in events if kind == "tool.reply.ack"]
        assert len(sent) == 1 and sent[0]["original_write_attempted"]
        assert sent[0]["original_write_confirmed"] is False
        assert len(acks) == 1 and acks[0]["matches"] is True
        assert acks[0]["original_reply_delivered"] is True
        assert acks[0]["physical_verified"] is False
        assert not any(kind == "tool.reply.fallback" for kind, _ in events)
    finally:
        await client.aclose()


async def test_delivery_observer_failure_never_changes_or_repeats_reply(
    tmp_path, tool_peer, caplog,
):
    client = CodexClient(tool_peer, tmp_path / "profile", timeout=PEER_RPC_TIMEOUT)
    calls, attempts = [], []

    async def callback(name, arguments):
        calls.append((name, arguments))
        return confirmed()

    def observer(kind, _data):
        if kind.startswith("tool.reply."):
            attempts.append(kind)
            raise RuntimeError("SYNTHETIC-PRIVATE-OBSERVER-MESSAGE")

    try:
        result = await client.chat("delivery-match", tool_specs=[TOOL], on_tool_call=callback,
                                   on_event=observer)
        assert result["answer"] == "Recibido sintético"
        assert len(calls) == len(tool_replies(client)) == 1
        assert attempts == ["tool.reply.sent", "tool.reply.ack"]
        assert "SYNTHETIC-PRIVATE" not in caplog.text
    finally:
        await client.aclose()


async def test_concurrent_server_calls_do_not_block_the_stdio_reader(tmp_path, tool_peer):
    client = CodexClient(tool_peer, tmp_path / "profile", timeout=PEER_RPC_TIMEOUT)
    entered, release = [], asyncio.Event()

    async def callback(name, arguments):
        entered.append(arguments["value"])
        await release.wait()
        return confirmed(arguments["value"])

    task = asyncio.create_task(client.chat("parallel", tool_specs=[TOOL], on_tool_call=callback))
    try:
        await wait_until(lambda: len(entered) == 2)
        assert not task.done()
        release.set()
        assert (await asyncio.wait_for(task, 3))["answer"] == "Recibido sintético"
        assert len(tool_replies(client)) == 2
        assert set(entered) == {"10001", "10002"}
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await client.aclose()


@pytest.mark.parametrize("scenario", [
    "unknown-method", "unknown-tool", "wrong-thread", "wrong-turn", "namespace",
    "invalid-arguments", "missing-call-id",
])
async def test_unoffered_or_misbound_server_request_never_reaches_callback(
    tmp_path, tool_peer, scenario,
):
    client = CodexClient(tool_peer, tmp_path / "profile", timeout=PEER_RPC_TIMEOUT)
    calls = []

    async def callback(name, arguments):
        calls.append((name, arguments))
        return confirmed()

    try:
        await client.chat(scenario, tool_specs=[TOOL], on_tool_call=callback)
        assert calls == []
        replies = tool_replies(client)
        assert len(replies) == 1
        if "error" in replies[0]:
            assert replies[0]["error"]["code"] == -32601
        else:
            assert replies[0]["result"]["success"] is False
        assert client._tool_turns == {} and client._server_requests == set()
    finally:
        await client.aclose()


async def test_replayed_logical_call_id_does_not_repeat_fixture_effect(tmp_path, tool_peer):
    client = CodexClient(tool_peer, tmp_path / "profile", timeout=PEER_RPC_TIMEOUT)
    effect = tmp_path / "synthetic-effect.txt"
    calls = 0

    async def callback(name, arguments):
        nonlocal calls
        calls += 1
        with effect.open("a") as stream:
            stream.write("one effect\n")
        return confirmed()

    try:
        await client.chat("replay", tool_specs=[TOOL], on_tool_call=callback)
        assert calls == 1 and effect.read_text() == "one effect\n"
        replies = tool_replies(client)
        assert [row["result"]["success"] for row in replies] == [True, False]
    finally:
        await client.aclose()


async def test_call_ids_and_explicit_histories_are_isolated_between_concurrent_threads(
    tmp_path, tool_peer,
):
    client = CodexClient(tool_peer, tmp_path / "profile", timeout=PEER_RPC_TIMEOUT)
    entered, release = [], asyncio.Event()

    async def callback(name, arguments):
        entered.append(arguments["value"])
        await release.wait()
        return confirmed(arguments["value"])

    tasks = [asyncio.create_task(client.chat(
        "shared-logical-call", tool_specs=[TOOL], on_tool_call=callback,
        history=[{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": f"synthetic-session-{index}"},
        ]}],
    )) for index in range(2)]
    try:
        await wait_until(lambda: len(entered) == 2)
        assert len(client._tool_turns) == 2
        release.set()
        answers = await asyncio.wait_for(asyncio.gather(*tasks), 3)
        assert len({row["thread_id"] for row in answers}) == 2
        assert len(tool_replies(client)) == 2
        assert all(row["result"]["success"] is True for row in tool_replies(client))
        injections = [row["params"] for row in wire(client)
                      if row.get("method") == "thread/inject_items"]
        assert len(injections) == 2
        assert len({row["threadId"] for row in injections}) == 2
        assert {row["items"][0]["content"][0]["text"] for row in injections} == {
            "synthetic-session-0", "synthetic-session-1",
        }
        assert client._tool_turns == {} and client._server_requests == set()
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.aclose()


async def test_one_turn_has_a_finite_total_of_distinct_calls(tmp_path, tool_peer):
    client = CodexClient(tool_peer, tmp_path / "profile", timeout=PEER_RPC_TIMEOUT)
    effects = []

    async def callback(name, arguments):
        effects.append(arguments["value"])
        return confirmed()

    try:
        await client.chat("sequential-limit", tool_specs=[TOOL], on_tool_call=callback)
        assert len(effects) == 64 and len(set(effects)) == 64
        replies = tool_replies(client)
        assert len(replies) == 65
        assert sum(row["result"]["success"] for row in replies) == 64
        assert replies[-1]["result"]["success"] is False
        assert client._server_requests == set() and client._tool_turns == {}
    finally:
        await client.aclose()


@pytest.mark.parametrize("bad_result", [None, {}, {"success": "true", "contentItems": []}])
async def test_malformed_callback_result_is_never_reported_as_confirmed_success(
    tmp_path, tool_peer, bad_result,
):
    client = CodexClient(tool_peer, tmp_path / "profile", timeout=PEER_RPC_TIMEOUT)

    async def callback(name, arguments):
        return bad_result

    try:
        await client.chat("one", tool_specs=[TOOL], on_tool_call=callback)
        result = tool_replies(client)[0]["result"]
        assert result["success"] is False
        assert result["contentItems"][0]["type"] == "inputText"
    finally:
        await client.aclose()


async def test_callback_error_after_effect_reports_uncertainty_without_false_no_action(
    tmp_path, tool_peer,
):
    client = CodexClient(tool_peer, tmp_path / "profile", timeout=PEER_RPC_TIMEOUT)
    effect = tmp_path / "synthetic-effect.txt"

    async def callback(name, arguments):
        effect.write_text("already committed")
        raise OSError("PRIVATE-EXCEPTION-MUST-NOT-LEAK")

    try:
        await client.chat("one", tool_specs=[TOOL], on_tool_call=callback)
        result = tool_replies(client)[0]["result"]
        assert effect.read_text() == "already committed"
        assert result["success"] is False
        text = result["contentItems"][0]["text"]
        assert "No se pudo confirmar" in text and "No repitas" in text
        assert "PRIVATE-EXCEPTION" not in text and "no se ejecutó" not in text
        assert len(tool_replies(client)) == 1
    finally:
        await client.aclose()


async def test_cancel_pending_callback_settles_turn_cleans_tasks_and_reports_unknown(
    tmp_path, tool_peer,
):
    client = CodexClient(tool_peer, tmp_path / "profile", timeout=PEER_RPC_TIMEOUT)
    entered, cancelled = asyncio.Event(), asyncio.Event()
    effect = tmp_path / "synthetic-effect.txt"

    async def callback(name, arguments):
        effect.write_text("committed before cancellation")
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return confirmed()

    task = asyncio.create_task(client.chat("hold", tool_specs=[TOOL], on_tool_call=callback))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        owned_tasks = tuple(next(iter(client._tool_turns.values())).tasks)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        await asyncio.wait_for(cancelled.wait(), 1)
        await wait_until(lambda: len(tool_replies(client)) == 1)
        result = tool_replies(client)[0]["result"]
        assert result["success"] is False
        assert "No se pudo confirmar" in result["contentItems"][0]["text"]
        assert effect.read_text() == "committed before cancellation"
        assert all(owned.done() for owned in owned_tasks)
        assert client._events == {} and client._tool_turns == {} and not client._server_requests
        assert (await client.chat("plain"))["answer"] == "Recibido sintético"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await client.aclose()


async def test_pending_server_callback_tasks_are_bounded_and_excess_is_denied(tmp_path, tool_peer):
    client = CodexClient(tool_peer, tmp_path / "profile", timeout=PEER_RPC_TIMEOUT)
    entered, release = [], asyncio.Event()

    async def callback(name, arguments):
        entered.append(arguments["value"])
        await release.wait()
        return confirmed()

    task = asyncio.create_task(client.chat("fanout", tool_specs=[TOOL], on_tool_call=callback))
    try:
        await wait_until(lambda: len(entered) == 16 and len(tool_replies(client)) == 4)
        assert len(next(iter(client._tool_turns.values())).tasks) == 16
        assert all(row["error"]["code"] == -32601 for row in tool_replies(client))
        release.set()
        assert (await asyncio.wait_for(task, 3))["answer"] == "Recibido sintético"
        assert len(entered) == 16 and len(tool_replies(client)) == 20
        assert client._tool_turns == {} and client._server_requests == set()
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await client.aclose()


async def test_close_while_callback_is_pending_joins_owned_tasks_without_another_effect(
    tmp_path, tool_peer,
):
    client = CodexClient(tool_peer, tmp_path / "profile", timeout=PEER_RPC_TIMEOUT)
    entered, cancelled = asyncio.Event(), asyncio.Event()
    effects = []

    async def callback(name, arguments):
        effects.append(arguments["value"])
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return confirmed()

    task = asyncio.create_task(client.chat("hold", tool_specs=[TOOL], on_tool_call=callback))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        owned_tasks = tuple(next(iter(client._tool_turns.values())).tasks)
        await asyncio.wait_for(client.aclose(), 3)
        with pytest.raises(CodexError, match="codex_disconnected"):
            await asyncio.wait_for(task, 3)
        assert cancelled.is_set() and all(owned.done() for owned in owned_tasks)
        assert effects == ["10001"]
        assert client._process is None and client._reader is None and client._stderr is None
        assert client._events == {} and client._tool_turns == {} and not client._server_requests
        with pytest.raises(CodexError, match="codex_closed"):
            await client.chat("plain")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await client.aclose()


async def test_history_is_only_explicit_input_for_its_new_thread_and_never_discovered(
    tmp_path, tool_peer,
):
    client = CodexClient(tool_peer, tmp_path / "profile", timeout=PEER_RPC_TIMEOUT)
    history = [{
        "type": "message", "role": "user",
        "content": [{"type": "input_text", "text": "SYNTHETIC-OWN-SESSION-ONLY"}],
    }]
    try:
        first = await client.chat("plain", history=history)
        await client.chat("plain", thread_id=first["thread_id"], history=[{"foreign": "ignored"}])
        second = await client.chat("plain")
        injections = [row for row in wire(client) if row.get("method") == "thread/inject_items"]
        assert len(injections) == 1
        assert injections[0]["params"] == {"threadId": first["thread_id"], "items": history}
        assert second["thread_id"] != first["thread_id"]
        methods = [row.get("method") for row in wire(client)]
        assert "thread/list" not in methods and "thread/read" not in methods
        assert "thread/resume" not in methods
    finally:
        await client.aclose()


async def test_explicit_history_budget_failure_does_not_start_turn_or_inject_partial_data(
    tmp_path, tool_peer,
):
    client = CodexClient(tool_peer, tmp_path / "profile", timeout=PEER_RPC_TIMEOUT)
    try:
        with pytest.raises(CodexError, match="codex_history_limit"):
            await client.chat("plain", history=[{"synthetic": "x"}] * 101)
        methods = [row.get("method") for row in wire(client)]
        assert "thread/inject_items" not in methods and "turn/start" not in methods
        assert client._events == {} and client._tool_turns == {}
    finally:
        await client.aclose()


async def test_human_waits_for_multiple_sequential_callbacks_do_not_spend_inference_budget(
    tmp_path, tool_peer,
):
    client = CodexClient(tool_peer, tmp_path / "profile", timeout=PEER_RPC_TIMEOUT)
    entered = []
    releases = [asyncio.Event(), asyncio.Event()]

    async def callback(name, arguments):
        index = len(entered)
        entered.append(arguments["value"])
        await releases[index].wait()
        return confirmed()

    task = asyncio.create_task(client.chat(
        "sequential", tool_specs=[TOOL], on_tool_call=callback, turn_timeout=0.6,
    ))
    try:
        for index, release in enumerate(releases):
            await wait_until(lambda index=index: len(entered) == index + 1)
            # This represents a synthetic human wait longer than the entire
            # inference budget; no decision is made on the owner's behalf.
            # Keep the wait longer than the budget while leaving enough margin
            # for the real stdio peer's scheduling and private log writes.
            await asyncio.sleep(0.9)
            assert not task.done()
            release.set()
        assert (await asyncio.wait_for(task, 3))["answer"] == "Recibido sintético"
        assert len(entered) == 2 and len(tool_replies(client)) == 2
        assert client._tool_turns == {} and not client._server_requests
    finally:
        for release in releases:
            release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await client.aclose()


async def test_inference_clock_stays_paused_until_all_overlapping_callbacks_finish(
    tmp_path, tool_peer,
):
    client = CodexClient(tool_peer, tmp_path / "profile", timeout=PEER_RPC_TIMEOUT)
    entered, release_first, release_last = [], asyncio.Event(), asyncio.Event()

    async def callback(name, arguments):
        entered.append(arguments["value"])
        await (release_first if arguments["value"] == "10001" else release_last).wait()
        return confirmed()

    task = asyncio.create_task(client.chat(
        "parallel", tool_specs=[TOOL], on_tool_call=callback, turn_timeout=0.6,
    ))
    try:
        await wait_until(lambda: len(entered) == 2)
        release_first.set()
        await wait_until(lambda: len(tool_replies(client)) == 1)
        await asyncio.sleep(0.9)
        assert not task.done()
        release_last.set()
        assert (await asyncio.wait_for(task, 3))["answer"] == "Recibido sintético"
    finally:
        release_first.set()
        release_last.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await client.aclose()


@pytest.mark.parametrize("scenario", ["idle", "malformed-hold", "post-tool-idle"])
async def test_idle_inference_still_expires_and_malformed_calls_cannot_pause_it(
    tmp_path, tool_peer, scenario,
):
    client = CodexClient(tool_peer, tmp_path / "profile", timeout=PEER_RPC_TIMEOUT)
    called = []

    async def callback(name, arguments):
        called.append(arguments["value"])
        return confirmed()

    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(client.chat(
                scenario, tool_specs=[TOOL], on_tool_call=callback, turn_timeout=0.3,
            ), 3)
        assert len(called) == (1 if scenario == "post-tool-idle" else 0)
        assert client._events == {} and client._tool_turns == {} and not client._server_requests
        assert any(row.get("method") == "turn/interrupt" for row in wire(client))
    finally:
        await client.aclose()


@pytest.mark.parametrize("suppress_cancellation", [False, True])
async def test_stuck_host_callback_has_its_own_finite_ceiling_and_an_uncertain_reply(
    tmp_path, tool_peer, suppress_cancellation,
):
    client = CodexClient(tool_peer, tmp_path / "profile", timeout=PEER_RPC_TIMEOUT)
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def callback(name, arguments):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if suppress_cancellation:
                return confirmed("An expired callback cannot confirm success")
            raise
        finally:
            cancelled.set()
        return confirmed()

    try:
        result = await asyncio.wait_for(client.chat(
            "one", tool_specs=[TOOL], on_tool_call=callback,
            turn_timeout=1, host_tool_timeout=0.1,
        ), 3)
        assert result["answer"] == "Recibido sintético" and entered.is_set() and cancelled.is_set()
        reply = tool_replies(client)[0]["result"]
        assert reply["success"] is False
        assert "No se pudo confirmar" in reply["contentItems"][0]["text"]
        assert client._tool_turns == {} and not client._server_requests
    finally:
        await client.aclose()


async def test_cancellation_remains_prompt_while_the_inference_budget_is_paused(
    tmp_path, tool_peer,
):
    client = CodexClient(tool_peer, tmp_path / "profile", timeout=PEER_RPC_TIMEOUT)
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def callback(name, arguments):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return confirmed()

    task = asyncio.create_task(client.chat(
        "hold", tool_specs=[TOOL], on_tool_call=callback, turn_timeout=0.6,
    ))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await asyncio.sleep(0.9)
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert cancelled.is_set() and client._events == {} and client._tool_turns == {}
        assert not client._server_requests
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await client.aclose()


@pytest.mark.parametrize("parameter", ["turn_timeout", "host_tool_timeout"])
@pytest.mark.parametrize("limit", [None, True, 0, -1, float("inf"), float("nan"), 3601])
async def test_inference_and_host_wait_limits_are_finite_before_launch(
    tmp_path, tool_peer, parameter, limit,
):
    client = CodexClient(tool_peer, tmp_path / "profile", timeout=PEER_RPC_TIMEOUT)
    try:
        with pytest.raises(ValueError, match="codex_timeout_invalid"):
            await client.chat("plain", **{parameter: limit})
        assert client._process is None and not client.directory.exists()
    finally:
        await client.aclose()
