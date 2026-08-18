from aegis_core.contracts import AgentRole, InputModality
from aegis_core.models import MODEL_REGISTRY, model_for


def test_every_role_has_a_model() -> None:
    assert set(MODEL_REGISTRY) == set(AgentRole)


def test_router_is_text_only_and_supports_tools() -> None:
    router = model_for(AgentRole.ROUTER)
    assert router.modalities == frozenset({InputModality.TEXT})
    assert router.tool_calling is True
