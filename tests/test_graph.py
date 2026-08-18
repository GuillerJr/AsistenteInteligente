from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from aegis_core.contracts import (
    AgentResult,
    AgentRole,
    PolicyDecision,
    ToolCall,
    UserRequest,
)
from aegis_core.orchestration.graph import build_swarm_graph
from aegis_core.tools.audit import HashChainAuditLog
from aegis_core.tools.defaults import default_policy_context


class FakeProvider:
    def __init__(self, tool_calls: tuple[ToolCall, ...] = ()) -> None:
        self.roles: list[AgentRole] = []
        self.tool_calls = tool_calls
        self.extra_bodies: list[Mapping[str, Any] | None] = []

    async def complete(
        self,
        *,
        role: AgentRole,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int | None = None,
        temperature: float | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> AgentResult:
        self.roles.append(role)
        self.extra_bodies.append(extra_body)
        if role is AgentRole.ROUTER:
            content = (
                '{"role":"code_security","risk":"medium","reason":"code request",'
                '"requires_confirmation":false}'
            )
        elif role is AgentRole.CODE_SECURITY:
            content = "specialist analysis"
        else:
            content = "respuesta final"
        return AgentResult(
            role=role,
            model_id=f"fake/{role.value}",
            content=content,
            tool_calls=self.tool_calls if role is AgentRole.CODE_SECURITY else (),
        )


@pytest.mark.asyncio
async def test_graph_routes_to_code_security_then_synthesizes() -> None:
    provider = FakeProvider()
    graph = build_swarm_graph(provider)
    state = await graph.ainvoke({"request": UserRequest(text="Revisa este código")})

    assert provider.roles == [
        AgentRole.ROUTER,
        AgentRole.CODE_SECURITY,
        AgentRole.SYNTHESIZER,
    ]
    assert state["final_result"].content == "respuesta final"
    assert state["tool_authorizations"] == ()
    assert state["tool_results"] == ()
    assert provider.extra_bodies[1]["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_graph_authorizes_but_does_not_execute_high_risk_tool_call() -> None:
    call = ToolCall(
        call_id="call-network",
        tool_name="network_discover_hosts",
        arguments={"target": "192.168.1.0/24", "mode": "ping"},
        requested_by=AgentRole.CODE_SECURITY,
    )
    provider = FakeProvider(tool_calls=(call,))
    graph = build_swarm_graph(provider)

    state = await graph.ainvoke({"request": UserRequest(text="Escanea mi red local")})

    authorization = state["tool_authorizations"][0]
    assert authorization.decision is PolicyDecision.REQUIRE_CONFIRMATION
    assert authorization.reason_code == "confirmation_required"
    assert state["tool_results"] == ()


@pytest.mark.asyncio
async def test_graph_executes_and_audits_allowed_read_only_tool(tmp_path) -> None:
    (tmp_path / "evidence.txt").write_text("trusted observation", encoding="utf-8")
    call = ToolCall(
        call_id="call-file",
        tool_name="filesystem_read_text",
        arguments={"path": "evidence.txt", "max_bytes": 128},
        requested_by=AgentRole.CODE_SECURITY,
    )
    audit = HashChainAuditLog(tmp_path / ".aegis" / "audit.jsonl")
    provider = FakeProvider(tool_calls=(call,))
    graph = build_swarm_graph(
        provider,
        policy_context=default_policy_context(tmp_path),
        audit_sink=audit,
    )

    state = await graph.ainvoke({"request": UserRequest(text="Lee la evidencia")})

    assert state["tool_authorizations"][0].decision is PolicyDecision.ALLOW
    assert state["tool_results"][0].success is True
    assert state["tool_results"][0].output == "trusted observation"
    assert [record.event_type for record in audit.verify()] == [
        "tool_authorization",
        "tool_execution",
    ]
