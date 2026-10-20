from __future__ import annotations

from dataclasses import dataclass

from aegis_core.contracts import AgentRole, InputModality


@dataclass(frozen=True, slots=True)
class ModelSpec:
    model_id: str
    role: AgentRole
    modalities: frozenset[InputModality]
    context_tokens: int
    reasoning: bool
    tool_calling: bool
    fallback_model_id: str | None = None


# Interpreting what a free-form request needs is a small structured call. NVIDIA
# removed the 120B Super endpoint (404 "Function not found", 2026-10-20); with the
# current prompts the router model got 17/17 in about 0.5 s, while 550B Ultra got
# 13/17 in 3.8 s, so the router model leads and GPT-OSS 20B is its fallback.
INTERPRETATION_MODEL_IDS = ("nvidia/nemotron-3.5-lightning-30b-a3b",
                            "openai/gpt-oss-20b")

# Endpoints NVIDIA withdrew (404/410 on 2026-10-20) and their live-verified successor.
# Saved model preferences are migrated on load so a retired choice cannot strand a role.
RETIRED_MODEL_REPLACEMENTS = {
    "nvidia/nemotron-3-super-120b-a12b": "nvidia/nemotron-3-ultra-550b-a55b",
    "openai/gpt-oss-120b": "nvidia/nemotron-3.5-lightning-30b-a3b",
    "deepseek-ai/deepseek-v4-flash-0731": "nvidia/nemotron-3-ultra-550b-a55b",
}

MODEL_REGISTRY: dict[AgentRole, ModelSpec] = {
    AgentRole.ROUTER: ModelSpec(
        model_id="nvidia/nemotron-3.5-lightning-30b-a3b",
        role=AgentRole.ROUTER,
        modalities=frozenset({InputModality.TEXT}),
        context_tokens=1_000_000,
        reasoning=True,
        tool_calling=True,
        fallback_model_id="openai/gpt-oss-20b",
    ),
    AgentRole.PLANNER: ModelSpec(
        model_id="nvidia/nemotron-3.5-lightning-30b-a3b",
        role=AgentRole.PLANNER,
        modalities=frozenset({InputModality.TEXT}),
        context_tokens=1_000_000,
        reasoning=True,
        tool_calling=True,
        fallback_model_id="openai/gpt-oss-20b",
    ),
    AgentRole.CRITICAL_REASONER: ModelSpec(
        model_id="nvidia/nemotron-3-ultra-550b-a55b",
        role=AgentRole.CRITICAL_REASONER,
        modalities=frozenset({InputModality.TEXT}),
        context_tokens=1_000_000,
        reasoning=True,
        tool_calling=True,
        fallback_model_id="nvidia/nemotron-3.5-lightning-30b-a3b",
    ),
    AgentRole.CODE_SECURITY: ModelSpec(
        model_id="nvidia/nemotron-3.5-lightning-30b-a3b",
        role=AgentRole.CODE_SECURITY,
        modalities=frozenset({InputModality.TEXT}),
        context_tokens=1_000_000,
        reasoning=True,
        tool_calling=True,
        fallback_model_id="nvidia/nemotron-3-ultra-550b-a55b",
    ),
    AgentRole.VISION: ModelSpec(
        model_id="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        role=AgentRole.VISION,
        modalities=frozenset({InputModality.TEXT, InputModality.IMAGE}),
        context_tokens=262_144,
        reasoning=True,
        tool_calling=False,
        fallback_model_id="meta/muse-glimmer-30b",
    ),
    AgentRole.OMNI: ModelSpec(
        model_id="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        role=AgentRole.OMNI,
        modalities=frozenset(InputModality),
        context_tokens=262_144,
        reasoning=True,
        tool_calling=False,
        fallback_model_id="meta/muse-glimmer-30b",
    ),
    AgentRole.SYNTHESIZER: ModelSpec(
        model_id="nvidia/nemotron-3.5-lightning-30b-a3b",
        role=AgentRole.SYNTHESIZER,
        modalities=frozenset({InputModality.TEXT}),
        context_tokens=1_000_000,
        reasoning=False,
        tool_calling=False,
        fallback_model_id="openai/gpt-oss-20b",
    ),
}


# Hybrid brain targets. The per-role registry above serves the LangGraph specialists;
# these fixed escalation targets serve the local-first hybrid client. Both live here
# so the README and every caller describe a single source of truth.
FAST_PLANNING_MODEL_ID = "openai/gpt-oss-20b"
DEEP_REASONING_MODEL_ID = "nvidia/nemotron-3-ultra-550b-a55b"
SPECULATIVE_VERIFIER_MODEL_ID = MODEL_REGISTRY[AgentRole.ROUTER].model_id


def model_for(role: AgentRole) -> ModelSpec:
    return MODEL_REGISTRY[role]


def engineering_model_ids() -> tuple[str, ...]:
    """Primary and fallback model for engineering sessions (code/security role)."""
    spec = MODEL_REGISTRY[AgentRole.CODE_SECURITY]
    return tuple(
        model_id for model_id in (spec.model_id, spec.fallback_model_id) if model_id is not None
    )
