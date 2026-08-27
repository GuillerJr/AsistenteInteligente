from aegis_core.contracts import AgentRole, InputModality
from aegis_core.models import MODEL_REGISTRY, model_for


def test_every_role_has_a_model() -> None:
    assert set(MODEL_REGISTRY) == set(AgentRole)


def test_router_is_text_only_and_supports_tools() -> None:
    router = model_for(AgentRole.ROUTER)
    assert router.modalities == frozenset({InputModality.TEXT})
    assert router.tool_calling is True


def test_tool_roles_use_live_verified_nvidia_endpoints() -> None:
    planner = model_for(AgentRole.PLANNER)
    reasoner = model_for(AgentRole.CRITICAL_REASONER)
    security = model_for(AgentRole.CODE_SECURITY)

    assert planner.model_id == "nvidia/nemotron-3.5-lightning-30b-a3b"
    assert planner.fallback_model_id == "nvidia/nemotron-3-nano-30b-a3b"
    assert planner.context_tokens == 1_000_000
    assert reasoner.model_id == "nvidia/nemotron-3-super-120b-a12b"
    assert reasoner.fallback_model_id == "openai/gpt-oss-120b"
    assert security.model_id == "deepseek-ai/deepseek-v4-pro-0813"
    assert security.fallback_model_id == "deepseek-ai/deepseek-v4-flash-0731"


def test_vision_uses_verified_omni_endpoint() -> None:
    vision = model_for(AgentRole.VISION)

    assert vision.model_id == "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
    assert vision.fallback_model_id == "meta/muse-glimmer-30b"
    assert vision.modalities == frozenset({InputModality.TEXT, InputModality.IMAGE})
