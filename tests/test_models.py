from aegis_core.contracts import AgentRole, InputModality
from aegis_core.models import MODEL_REGISTRY, model_for


def test_every_role_has_a_model() -> None:
    assert set(MODEL_REGISTRY) == set(AgentRole)


def test_router_is_text_only_and_supports_tools() -> None:
    router = model_for(AgentRole.ROUTER)
    assert router.modalities == frozenset({InputModality.TEXT})
    assert router.tool_calling is True


def test_vision_uses_verified_omni_endpoint() -> None:
    vision = model_for(AgentRole.VISION)

    assert vision.model_id == "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
    assert vision.fallback_model_id == "meta/muse-glimmer-30b"
    assert vision.modalities == frozenset({InputModality.TEXT, InputModality.IMAGE})
