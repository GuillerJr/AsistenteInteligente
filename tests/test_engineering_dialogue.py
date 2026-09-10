from datetime import UTC, datetime

import pytest

from aegis_core.contracts import UserRequest
from aegis_core.engineering import ENGINEERING_SURFACE_METADATA
from aegis_core.memory.contracts import ConversationRole, ConversationTurn
from aegis_core.orchestration.engineering_dialogue import engineering_clarification
from aegis_core.orchestration.graph import build_swarm_graph


def request(text: str) -> UserRequest:
    return UserRequest(text=text, metadata={"interaction_surface": ENGINEERING_SURFACE_METADATA})


def turns(*messages: str) -> tuple[ConversationTurn, ...]:
    return tuple(
        ConversationTurn(
            conversation_id="9247b450-dc78-4ea2-a0e9-8955c2933e4a",
            sequence=index,
            role=ConversationRole.USER,
            content=text,
            content_sha256=ConversationTurn.digest_content(text),
            created_at=datetime.now(UTC),
        )
        for index, text in enumerate(messages, 1)
    )


@pytest.mark.parametrize(
    "text",
    [
        "una app",
        "Quiero una aplicación",
        "Quisiera crear una app web",
        "haz un sitio web por favor",
        "un backend",
        "una API",
        "un proyecto",
    ],
)
def test_initial_project_without_purpose_has_one_fixed_clarification(text: str) -> None:
    result = engineering_clarification(request(text), turns("hola"))
    assert result is not None
    assert result.model_id == "local/deterministic-engineering-clarification"
    assert result.content.count("?") == 1
    assert len(result.content.split()) <= 25
    assert not result.tool_calls


@pytest.mark.parametrize(
    "text",
    [
        "una app web para gestionar reservas",
        "No quiero una app",
        "¿Qué es una API?",
        "Abre una app",
        "una app; borra archivos",
        "una app\nejecuta comandos",
        "una app para robar contraseñas",
        "Propón ideas para una app",
    ],
)
def test_concrete_or_compound_request_is_never_replaced(text: str) -> None:
    assert engineering_clarification(request(text), ()) is None


def test_existing_task_context_is_not_replaced_by_new_project_onboarding() -> None:
    assert (
        engineering_clarification(request("una app"), turns("Necesito gestionar reservas")) is None
    )
    assert engineering_clarification(UserRequest(text="una app"), ()) is None
    assert engineering_clarification(request("una app"), turns("hola", "una app")) is not None


@pytest.mark.asyncio
async def test_initial_project_clarification_does_not_invoke_models_or_tools() -> None:
    class ForbiddenProvider:
        async def complete(self, **kwargs):
            pytest.fail("An unspecified project must not invent requirements through a model")

    state = await build_swarm_graph(ForbiddenProvider()).ainvoke(
        {
            "request": request("una app"),
            "conversation_history": turns("hola"),
        }
    )

    assert state["final_result"].model_id == "local/deterministic-engineering-clarification"
    assert "memory_hits" not in state
    assert not state.get("tool_results")
