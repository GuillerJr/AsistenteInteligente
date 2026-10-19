"""Compound analysis stays in planning instead of becoming a malformed filename."""

import json

import pytest

from aegis_core.contracts import AgentResult, AgentRole, PolicyDecision, ToolCall, UserRequest
from aegis_core.orchestration.direct_actions import direct_tool_call
from aegis_core.orchestration.graph import build_swarm_graph
from aegis_core.skills import SkillRegistry, SkillStore
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context
from aegis_core.tools.execution import ReadOnlyToolExecutor


@pytest.mark.parametrize(
    "text",
    [
        "Lee el archivo fixture.csv y analiza los datos. Calcula disponibles.",
        "Lee el archivo fixture.json y cita el presupuesto vigente.",
        "Lee el archivo README.md. Explica sus limitaciones.",
        "Read file fixture.csv and compare the values.",
        "Lee el archivo fixture.csv explicando sus columnas",
        'Lee el archivo "fixture.csv" y resume el contenido',
    ],
)
def test_compound_file_work_is_not_a_direct_read_of_the_remaining_sentence(text):
    assert direct_tool_call(UserRequest(text=text)) is None


@pytest.mark.parametrize(
    ("text", "path"),
    [
        ("Lee el archivo README.md", "README.md"),
        ("Read file notes/daily brief.txt", "notes/daily brief.txt"),
        ('Lee el archivo "notes/project roadmap"', "notes/project roadmap"),
        ("Lee el archivo «notes/presupuesto y resultados.txt»",
         "notes/presupuesto y resultados.txt"),
    ],
)
def test_exact_single_file_commands_preserve_the_actual_path(text, path):
    call = direct_tool_call(UserRequest(text=text))
    assert call.tool_name == "filesystem_read_text"
    assert call.arguments["path"] == path


@pytest.mark.asyncio
async def test_compound_read_uses_nvidia_planning_then_actual_workspace_evidence(tmp_path):
    source = "stock,reserved\n23,7\n"
    (tmp_path / "fixture.csv").write_text(source, encoding="utf-8")

    class Provider:
        def __init__(self):
            self.requests = []

        async def complete_nvidia_only(self, *, role, **kwargs):
            self.requests.append({"role": role, **kwargs})
            return AgentResult(
                role=role, model_id="synthetic/nvidia", content="Evidencia leída correctamente.",
                tool_calls=(ToolCall(
                    call_id="planned-source-read", requested_by=role,
                    tool_name="filesystem_read_text", arguments={"path": "fixture.csv"},
                ),) if role is AgentRole.CRITICAL_REASONER else (),
            )

        async def complete(self, **_kwargs):
            raise AssertionError("No inference outside NVIDIA")

    provider = Provider()
    broker = build_default_tool_broker()
    graph = build_swarm_graph(
        provider, tool_broker=broker,
        skill_registry=SkillRegistry(broker, SkillStore(tmp_path / "skills")),
        tool_executor=ReadOnlyToolExecutor(), policy_context=default_policy_context(tmp_path),
        inference_nvidia_only=True,
    )
    state = await graph.ainvoke({"request": UserRequest(
        text="Lee el archivo fixture.csv y analiza los datos. Calcula disponibles.",
    )})
    assert not state.get("direct_tool_call")
    assert state["tool_authorizations"][0].decision is PolicyDecision.ALLOW
    assert state["tool_authorizations"][0].normalized_arguments["path"] == "fixture.csv"
    assert state["tool_results"][0].output == source
    assert [call["role"] for call in provider.requests] == [
        AgentRole.CRITICAL_REASONER, AgentRole.SYNTHESIZER,
    ]
    reference = next(json.loads(message["content"].split("\n", 1)[1])
                     for message in provider.requests[-1]["messages"]
                     if isinstance(message.get("content"), str)
                     and message["content"].startswith("Datos de referencia;"))
    assert reference["tool_results"][0]["output"] == source
    assert reference["selected_skills"][0]["skill_id"] == "document-analysis-expert"
