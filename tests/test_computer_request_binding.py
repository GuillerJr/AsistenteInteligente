"""The real graph keeps the owner's GUI restrictions through broker authorization."""

from __future__ import annotations

from uuid import uuid4

import pytest

from aegis_core.contracts import AgentResult, AgentRole, PolicyDecision, ToolCall, UserRequest
from aegis_core.orchestration.graph import build_nvidia_swarm_graph
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context
from aegis_core.tools.execution import ReadOnlyToolExecutor

REQUEST = (
    "Controla la app TextEdit para escribir únicamente «PRUEBA JARVIS A» "
    "en el documento nuevo vacío abierto. No abras otros documentos ni guardes."
)


class ProposedComputerAction:
    def __init__(self, value: str):
        self.call = ToolCall(
            call_id="synthetic-computer-action", tool_name="computer_use",
            requested_by=AgentRole.PLANNER, arguments={
                "objective": f"Escribe «{value}»",
                "application_bundle_identifier": "com.apple.TextEdit",
                "postconditions": [{"kind": "field_contains", "value": value}],
            },
        )

    async def complete_nvidia_only(self, **kwargs):
        role = kwargs["role"]
        return AgentResult(
            role=role, model_id="synthetic/nvidia", content="Acción pendiente de autorización",
            tool_calls=(self.call,) if role is AgentRole.PLANNER else (),
        )


class NoExecution(ReadOnlyToolExecutor):
    async def execute_async(self, authorization, policy):
        pytest.fail("A GUI request without the owner's approval must never execute")


@pytest.mark.parametrize("value,decision", [
    ("PRUEBA JARVIS A", PolicyDecision.REQUIRE_CONFIRMATION),
    ("TEXTO INVENTADO POR EL MODELO", PolicyDecision.DENY),
])
async def test_owner_objective_replaces_planner_paraphrase_before_validation(
    tmp_path, value, decision,
):
    provider = ProposedComputerAction(value)
    graph = build_nvidia_swarm_graph(
        provider, tool_broker=build_default_tool_broker(),
        policy_context=default_policy_context(tmp_path), tool_executor=NoExecution(),
    )

    state = await graph.ainvoke({"request": UserRequest(text=REQUEST)})

    actual = state["specialist_result"].tool_calls[0]
    authorization, = state["tool_authorizations"]
    assert actual.arguments["objective"] == REQUEST
    assert actual.digest() != provider.call.digest()
    assert authorization.call_digest == actual.digest()
    assert authorization.decision is decision
    if decision is PolicyDecision.REQUIRE_CONFIRMATION:
        assert authorization.normalized_arguments["objective"] == REQUEST
        assert authorization.normalized_arguments["postconditions"][0]["value"] == value
    assert not state.get("tool_results")


async def test_one_step_workflow_cannot_drop_owner_restrictions(tmp_path):
    provider = ProposedComputerAction("PRUEBA JARVIS A")
    provider.call = provider.call.model_copy(update={"arguments": {
        **provider.call.arguments,
        "workflow_steps": [{
            "ordinal": 1, "application_bundle_identifier": "com.apple.TextEdit",
            "objective": "escribir únicamente «PRUEBA JARVIS A»",
            "postconditions": [{"kind": "field_contains", "value": "PRUEBA JARVIS A"}],
        }],
    }})
    graph = build_nvidia_swarm_graph(
        provider, tool_broker=build_default_tool_broker(),
        policy_context=default_policy_context(tmp_path), tool_executor=NoExecution(),
    )

    with pytest.raises(ValueError, match="computer_workflow_requires_multiple_steps"):
        await graph.ainvoke({"request": UserRequest(text=REQUEST), "conversation_id": uuid4()})


async def test_gui_action_cannot_substitute_another_application(tmp_path):
    provider = ProposedComputerAction("PRUEBA JARVIS A")
    provider.call = provider.call.model_copy(update={"arguments": {
        **provider.call.arguments, "application_bundle_identifier": "com.apple.Safari",
    }})
    graph = build_nvidia_swarm_graph(
        provider, tool_broker=build_default_tool_broker(),
        policy_context=default_policy_context(tmp_path), tool_executor=NoExecution(),
    )

    state = await graph.ainvoke({"request": UserRequest(text=REQUEST)})

    authorization, = state["tool_authorizations"]
    assert authorization.decision is PolicyDecision.DENY
    assert authorization.reason_code == "computer_destination_not_requested"
    assert not state.get("tool_results")
