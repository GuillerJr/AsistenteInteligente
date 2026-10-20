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
    assert planner.fallback_model_id == "openai/gpt-oss-20b"
    assert planner.context_tokens == 1_000_000
    assert reasoner.model_id == "nvidia/nemotron-3-ultra-550b-a55b"
    assert reasoner.fallback_model_id == "nvidia/nemotron-3.5-lightning-30b-a3b"
    assert security.model_id == "nvidia/nemotron-3.5-lightning-30b-a3b"
    assert security.fallback_model_id == "nvidia/nemotron-3-ultra-550b-a55b"
    assert security.modalities == frozenset({InputModality.TEXT})
    assert security.context_tokens == 1_000_000
    assert security.reasoning and security.tool_calling


def test_vision_uses_verified_omni_endpoint() -> None:
    vision = model_for(AgentRole.VISION)

    assert vision.model_id == "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
    assert vision.fallback_model_id == "meta/muse-glimmer-30b"
    assert vision.modalities == frozenset({InputModality.TEXT, InputModality.IMAGE})


def test_saved_preferences_with_retired_models_migrate_to_live_successors(tmp_path):
    import json

    from aegis_core.models import RETIRED_MODEL_REPLACEMENTS
    from aegis_core.settings_store import load_model_preferences

    retired = {
        "version": 1,
        "models": [
            {"model_id": "nvidia/nemotron-3.5-lightning-30b-a3b", "images": False, "tools": True},
            {"model_id": "openai/gpt-oss-20b", "images": False, "tools": True},
            {"model_id": "nvidia/nemotron-3-super-120b-a12b", "images": False, "tools": True},
            {"model_id": "openai/gpt-oss-120b", "images": False, "tools": True},
            {"model_id": "deepseek-ai/deepseek-v4-flash-0731", "images": False, "tools": True},
            {"model_id": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning", "images": True,
             "tools": False},
            {"model_id": "meta/muse-glimmer-30b", "images": True, "tools": False},
        ],
        "roles": {
            "router": {"primary": "nvidia/nemotron-3.5-lightning-30b-a3b",
                       "fallback": "openai/gpt-oss-20b"},
            "planner": {"primary": "nvidia/nemotron-3.5-lightning-30b-a3b",
                        "fallback": "openai/gpt-oss-120b"},
            "critical_reasoner": {"primary": "nvidia/nemotron-3-super-120b-a12b",
                                  "fallback": "openai/gpt-oss-120b"},
            "code_security": {"primary": "nvidia/nemotron-3.5-lightning-30b-a3b",
                              "fallback": "nvidia/nemotron-3-super-120b-a12b"},
            "vision": {"primary": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
                       "fallback": "meta/muse-glimmer-30b"},
            "omni": {"primary": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
                     "fallback": "meta/muse-glimmer-30b"},
            "synthesizer": {"primary": "nvidia/nemotron-3.5-lightning-30b-a3b",
                            "fallback": "openai/gpt-oss-20b"},
        },
    }
    tmp_path.chmod(0o700)
    path = tmp_path / "model_preferences.json"
    path.write_text(json.dumps(retired))
    path.chmod(0o600)

    preferences = load_model_preferences(path)

    assigned = {
        identifier
        for selection in preferences.roles.values()
        for identifier in (selection.primary, selection.fallback)
    }
    assert not assigned & set(RETIRED_MODEL_REPLACEMENTS)
    reasoner = preferences.roles[AgentRole.CRITICAL_REASONER]
    assert reasoner.primary == "nvidia/nemotron-3-ultra-550b-a55b"
    assert reasoner.fallback == "nvidia/nemotron-3.5-lightning-30b-a3b"
    # The planner's retired fallback became its own primary; it gets a distinct one.
    planner = preferences.roles[AgentRole.PLANNER]
    assert planner.fallback not in {None, planner.primary}
