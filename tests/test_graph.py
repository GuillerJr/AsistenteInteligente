from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from aegis_core.contracts import AgentResult, AgentRole, UserRequest
from aegis_core.orchestration.graph import build_swarm_graph


class FakeProvider:
    def __init__(self) -> None:
        self.roles: list[AgentRole] = []

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
        if role is AgentRole.ROUTER:
            content = (
                '{"role":"code_security","risk":"medium","reason":"code request",'
                '"requires_confirmation":false}'
            )
        elif role is AgentRole.CODE_SECURITY:
            content = "specialist analysis"
        else:
            content = "respuesta final"
        return AgentResult(role=role, model_id=f"fake/{role.value}", content=content)


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
