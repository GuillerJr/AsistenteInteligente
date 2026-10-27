"""Cancellation keeps an uncertain adapter's partial evidence without promotion."""

import pytest

from jarvis_core.runtime import AgentRuntime, ToolDefinition, ToolProposal, ToolResult
from jarvis_core.tools import ToolCancelled


@pytest.mark.parametrize("raising", [False, True])
@pytest.mark.parametrize("wrong_identity", [False, True])
async def test_cancel_preserves_only_correct_uncertain_partial_receipt(raising, wrong_identity):
    evidence = '{"completed_files":["synthetic.txt"],"publication_uncertain":true}'
    events = []

    class Peer:
        calls = 0

        async def next_step(self, _context):
            self.calls += 1
            return ToolProposal("fixture_effect", {})

    peer = Peer()

    async def execute(_arguments, cancellation):
        cancellation.set()
        receipt = ToolResult(
            "other_tool" if wrong_identity else "fixture_effect",
            False,
            False,
            output=evidence,
            error="publication_uncertain",
            effect_status="unknown",
        )
        if raising:
            raise ToolCancelled(receipt)
        return receipt

    runtime = AgentRuntime(
        peer,
        [ToolDefinition("fixture_effect", lambda _: None, execute)],
        on_tool_result=lambda _context, value: events.append(value),
    )
    handle = runtime.start("synthetic-session", "test", offered_tools=("fixture_effect",))
    result = await handle.await_settled()
    assert result.status == "cancelled" and peer.calls == 1
    assert len(result.tool_results) == 1 and len(events) == 1
    receipt = result.tool_results[0]
    assert receipt.effect_status == "unknown" and not receipt.succeeded and not receipt.verified
    assert (receipt.output == evidence) is (not wrong_identity)
    assert receipt.tool_name == "fixture_effect" and receipt.operation_id is not None
    assert events[0] == receipt
