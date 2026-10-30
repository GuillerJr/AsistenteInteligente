"""Provider counters are correlated wire data, never model estimates or USD."""

import sys
from pathlib import Path

import pytest

from jarvis_core.codex import CodexClient


class CounterPeer(CodexClient):
    def __init__(self, directory, samples):
        super().__init__(Path(sys.executable), directory)
        self.samples = iter(samples)
        self.turn = 0

    async def account_status(self):
        return {"connected": True, "mode": "chatgpt"}

    async def request(self, method, params):
        if method == "thread/start":
            return {"thread": {"id": "own-thread"}}
        if method == "thread/resume":
            return {}
        assert method == "turn/start"
        self.turn += 1
        turn_id = str(self.turn)
        queue = self._events[params["threadId"]]
        for turn, tokens in next(self.samples):
            queue.put_nowait(("thread/tokenUsage/updated", {
                "threadId": params["threadId"], "turnId": turn_id if turn is None else turn,
                "tokenUsage": {"total": {"totalTokens": tokens}},
            }))
        queue.put_nowait(("item/completed", {"turnId": turn_id,
            "item": {"type": "agentMessage", "id": "answer", "text": "Cuatro"}}))
        queue.put_nowait(("turn/completed", {"turn": {"id": turn_id,
                                                     "status": "completed"}}))
        return {"turn": {"id": turn_id}}


async def test_counter_uses_final_cumulative_delta_and_ignores_another_turn(tmp_path):
    client = CounterPeer(tmp_path / "provider", [
        [(None, 10), ("another-turn", 999), (None, 25)],
        [("1", 9999), (None, 40)],
    ])
    events = []
    try:
        await client.chat("uno", on_event=lambda *row: events.append(row))
        assert events[-1] == ("usage.final", {
            "run_total_tokens": 25, "cost": None, "source": "codex_cumulative_counter"})
        events.clear()
        await client.chat("dos", thread_id="own-thread", on_event=lambda *row: events.append(row))
        assert events[-1][1]["run_total_tokens"] == 15
        assert all(row[1]["cost"] is None for row in events)
    finally:
        await client.aclose()


async def test_resumed_thread_has_unknown_prior_counter_then_establishes_baseline(tmp_path):
    client = CounterPeer(tmp_path / "provider", [[(None, 100)], [(None, 125)]])
    events = []
    try:
        await client.chat("uno", thread_id="own-thread", on_event=lambda *row: events.append(row))
        assert events[-1][1]["run_total_tokens"] is None
        events.clear()
        await client.chat("dos", thread_id="own-thread", on_event=lambda *row: events.append(row))
        assert events[-1][1]["run_total_tokens"] == 25
    finally:
        await client.aclose()


@pytest.mark.parametrize("bad", [True, -1, "25", 10**16])
async def test_invalid_counter_cannot_become_zero_or_established_usage(tmp_path, bad):
    client = CounterPeer(tmp_path / "provider", [[(None, bad)]])
    events = []
    try:
        await client.chat("uno", on_event=lambda *row: events.append(row))
        assert not events
        assert "own-thread" not in client._thread_token_totals
    finally:
        await client.aclose()


async def test_missing_counter_loses_known_baseline_instead_of_undercounting(tmp_path):
    client = CounterPeer(tmp_path / "provider", [[(None, 10)], [], [(None, 50)]])
    events = []
    try:
        await client.chat("uno")
        await client.chat("dos", thread_id="own-thread")
        await client.chat("tres", thread_id="own-thread", on_event=lambda *row: events.append(row))
        assert events[-1][1]["run_total_tokens"] is None
    finally:
        await client.aclose()
