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


MODEL_REGISTRY: dict[AgentRole, ModelSpec] = {
    AgentRole.ROUTER: ModelSpec(
        model_id="nvidia/nemotron-3.5-lightning-30b-a3b",
        role=AgentRole.ROUTER,
        modalities=frozenset({InputModality.TEXT}),
        context_tokens=1_000_000,
        reasoning=True,
        tool_calling=True,
        fallback_model_id="nvidia/nemotron-3-nano-30b-a3b",
    ),
    AgentRole.PLANNER: ModelSpec(
        model_id="nvidia/nemotron-3-super-120b-a12b",
        role=AgentRole.PLANNER,
        modalities=frozenset({InputModality.TEXT}),
        context_tokens=1_000_000,
        reasoning=True,
        tool_calling=True,
        fallback_model_id="nvidia/nemotron-3-ultra-550b-a55b",
    ),
    AgentRole.CRITICAL_REASONER: ModelSpec(
        model_id="nvidia/nemotron-3-ultra-550b-a55b",
        role=AgentRole.CRITICAL_REASONER,
        modalities=frozenset({InputModality.TEXT}),
        context_tokens=1_000_000,
        reasoning=True,
        tool_calling=True,
        fallback_model_id="nvidia/nemotron-3-super-120b-a12b",
    ),
    AgentRole.CODE_SECURITY: ModelSpec(
        model_id="z-ai/glm-5.2",
        role=AgentRole.CODE_SECURITY,
        modalities=frozenset({InputModality.TEXT}),
        context_tokens=1_000_000,
        reasoning=True,
        tool_calling=True,
        fallback_model_id="poolside/laguna-xs-2.1",
    ),
    AgentRole.VISION: ModelSpec(
        model_id="meta/muse-glimmer-30b",
        role=AgentRole.VISION,
        modalities=frozenset({InputModality.TEXT, InputModality.IMAGE}),
        context_tokens=131_072,
        reasoning=True,
        tool_calling=True,
        fallback_model_id="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
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
        fallback_model_id="nvidia/nemotron-3-super-120b-a12b",
    ),
}


def model_for(role: AgentRole) -> ModelSpec:
    return MODEL_REGISTRY[role]
