"""A rejected oversized schedule edit leaves a listable, unchanged configuration."""

import json

from test_service_management import management as management

from jarvis_core.protocol import RpcError, _encode

ESCAPED_PROMPT = "\x01" * 8000
PRINTABLE_PROMPT = "x" * 8000


async def create(client, ordinal, prompt):
    return (await client.call("schedules.create", {
        "title": f"Tarea sintética {ordinal}", "prompt": prompt, "agent_id": "jarvis",
        "enabled": False, "spec": {"kind": "once", "at": 4_000_000_000},
    }))["schedule"]


def assert_transport_budget(snapshot):
    encoded_rows = json.dumps(snapshot["schedules"], ensure_ascii=True,
                              separators=(",", ":")).encode()
    assert len(encoded_rows) <= 524288
    # Use the actual transport encoder, including the response envelope. The
    # escaped controls must fit; measuring their raw UTF-8 would miss the bug.
    frame = _encode({"jsonrpc": "2.0", "id": 1, "result": snapshot}, 1024 * 1024)
    assert len(frame) <= 1024 * 1024


async def test_escaped_prompt_create_rejects_before_count_limit_without_changing_snapshot(
    management,
):
    service, client, peer, _root = management
    rejected = False
    for ordinal in range(32):
        before = await client.call("schedules.list")
        try:
            await create(client, ordinal, ESCAPED_PROMPT)
        except RpcError:
            rejected = True
            after = await client.call("schedules.list")
            assert after == before
            assert 0 < len(after["schedules"]) < 32
            assert len(service.schedule_store.list()) == len(after["schedules"])
            assert_transport_budget(after)
            break
        assert_transport_budget(await client.call("schedules.list"))
    assert rejected, "wire budget must reject before the 32-task configuration limit"
    assert not peer.calls and not service.scheduler.running
    # The same RPC connection remains usable after rejection.
    assert (await client.call("agents.list"))["agents"]


async def test_escaped_prompt_update_rejects_atomically_and_preserves_revision(management):
    service, client, peer, _root = management
    records = [await create(client, n, PRINTABLE_PROMPT) for n in range(14)]
    rejected = False
    for row in records:
        before = await client.call("schedules.list")
        original = service.schedule_store.get(row["schedule_id"])
        try:
            await client.call("schedules.update", {
                "schedule_id": row["schedule_id"], "expected_revision": row["revision"],
                "prompt": ESCAPED_PROMPT,
            })
        except RpcError:
            rejected = True
            after = await client.call("schedules.list")
            assert after == before
            assert service.schedule_store.get(row["schedule_id"]) == original
            assert original.revision == row["revision"] == 1
            assert original.prompt == PRINTABLE_PROMPT
            assert service.schedule_store.history(row["schedule_id"]) == ()
            assert_transport_budget(after)
            # A smaller valid edit succeeds using that same unchanged revision.
            changed = (await client.call("schedules.update", {
                "schedule_id": row["schedule_id"], "expected_revision": original.revision,
                "title": "A",
            }))["schedule"]
            assert changed["revision"] == original.revision + 1
            assert changed["prompt"] == PRINTABLE_PROMPT
            assert_transport_budget(await client.call("schedules.list"))
            break
        assert_transport_budget(await client.call("schedules.list"))
    assert rejected, "raw-byte-valid updates must still obey the encoded transport budget"
    assert not peer.calls and not service.scheduler.running
