from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from aegis_core.contracts import AgentRole

DEEP_REASONING_MODEL_ID = "deepseek-ai/deepseek-v4-flash-0731"
FAST_PLANNING_MODEL_ID = "openai/gpt-oss-20b"

_CODE_TERMS = frozenset(
    {
        "api",
        "arquitectura",
        "code",
        "código",
        "debug",
        "depura",
        "implement",
        "implementa",
        "programa",
        "programar",
        "refactor",
        "refactoriza",
        "script",
        "swift",
        "python",
        "typescript",
    }
)
_SECURITY_TERMS = frozenset(
    {
        "attack",
        "ataque",
        "ciberseguridad",
        "credential",
        "credencial",
        "exploit",
        "firewall",
        "malware",
        "pentest",
        "security",
        "seguridad",
        "threat",
        "vulnerabilidad",
        "vulnerability",
    }
)
_MULTISTEP_TERMS = frozenset(
    {
        "automatiza",
        "automate",
        "después",
        "ejecuta",
        "execute",
        "first",
        "luego",
        "primero",
        "then",
    }
)


class CascadeTarget(StrEnum):
    LOCAL = "local_foundation"
    NVIDIA_FAST = "nvidia_fast"
    NVIDIA_DEEP = "nvidia_deep"
    NVIDIA_ROLE = "nvidia_role"


@dataclass(frozen=True, slots=True)
class TokenConfidence:
    selected_log_probability: float
    top_log_probabilities: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ConfidenceMetrics:
    calibrated_probability: float
    mean_selected_probability: float
    normalized_entropy: float
    token_count: int

    @classmethod
    def unavailable(cls) -> ConfidenceMetrics:
        return cls(
            calibrated_probability=0.0,
            mean_selected_probability=0.0,
            normalized_entropy=1.0,
            token_count=0,
        )


@dataclass(frozen=True, slots=True)
class CascadeDecision:
    target: CascadeTarget
    reason: str
    model_ids: tuple[str, ...] | None

    @property
    def escalates(self) -> bool:
        return self.target is not CascadeTarget.LOCAL


def calibrate_token_confidence(tokens: Sequence[TokenConfidence]) -> ConfidenceMetrics:
    if not tokens:
        return ConfidenceMetrics.unavailable()
    selected_probabilities: list[float] = []
    normalized_entropies: list[float] = []
    per_token_scores: list[float] = []
    for token in tokens:
        selected = _probability(token.selected_log_probability)
        top = tuple(
            probability
            for probability in (_probability(value) for value in token.top_log_probabilities)
            if probability > 0.0
        )
        if len(top) <= 1:
            normalized_entropy = 0.0
        else:
            total = math.fsum(top)
            distribution = tuple(value / total for value in top)
            entropy = -math.fsum(value * math.log(value) for value in distribution)
            normalized_entropy = entropy / math.log(len(distribution))
        selected_probabilities.append(selected)
        normalized_entropies.append(normalized_entropy)
        per_token_scores.append(selected * (1.0 - 0.35 * normalized_entropy))
    ordered = sorted(per_token_scores)
    lower_decile = ordered[max(0, math.ceil(len(ordered) * 0.1) - 1)]
    calibrated = 0.6 * math.fsum(per_token_scores) / len(per_token_scores) + 0.4 * lower_decile
    return ConfidenceMetrics(
        calibrated_probability=max(0.0, min(1.0, calibrated)),
        mean_selected_probability=math.fsum(selected_probabilities) / len(selected_probabilities),
        normalized_entropy=math.fsum(normalized_entropies) / len(normalized_entropies),
        token_count=len(tokens),
    )


def decide_cascade(
    *,
    role: AgentRole,
    messages: Sequence[Mapping[str, Any]],
    confidence: ConfidenceMetrics,
    threshold: float,
    extra_body: Mapping[str, Any] | None = None,
    contains_non_text_input: bool = False,
) -> CascadeDecision:
    if not 0.0 < threshold < 1.0:
        raise ValueError("confidence threshold is invalid")
    if contains_non_text_input or role in {AgentRole.VISION, AgentRole.OMNI}:
        return CascadeDecision(
            target=CascadeTarget.NVIDIA_ROLE,
            reason="multimodal_requires_specialist",
            model_ids=None,
        )
    prompt = extract_user_request(messages)
    terms = frozenset(re.findall(r"\w+", prompt.casefold(), flags=re.UNICODE))
    if role in {AgentRole.CODE_SECURITY, AgentRole.CRITICAL_REASONER} or not terms.isdisjoint(
        _CODE_TERMS | _SECURITY_TERMS
    ):
        return CascadeDecision(
            target=CascadeTarget.NVIDIA_DEEP,
            reason="deep_reasoning_required",
            model_ids=(DEEP_REASONING_MODEL_ID,),
        )
    if extra_body and (extra_body.get("tools") or extra_body.get("tool_choice")):
        return CascadeDecision(
            target=CascadeTarget.NVIDIA_FAST,
            reason="tool_planning_required",
            model_ids=(FAST_PLANNING_MODEL_ID,),
        )
    if len(terms & _MULTISTEP_TERMS) >= 2:
        return CascadeDecision(
            target=CascadeTarget.NVIDIA_FAST,
            reason="multi_step_planning_required",
            model_ids=(FAST_PLANNING_MODEL_ID,),
        )
    if confidence.calibrated_probability < threshold:
        return CascadeDecision(
            target=CascadeTarget.NVIDIA_FAST,
            reason="confidence_below_threshold",
            model_ids=(FAST_PLANNING_MODEL_ID,),
        )
    return CascadeDecision(
        target=CascadeTarget.LOCAL,
        reason="local_confidence_accepted",
        model_ids=None,
    )


def extract_text(messages: Sequence[Mapping[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
    return "\n".join(parts)


def extract_user_request(messages: Sequence[Mapping[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return extract_text((message,))
    return ""


def contains_non_text_content(messages: Sequence[Mapping[str, Any]]) -> bool:
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            continue
        if not isinstance(content, list):
            return True
        if any(not isinstance(item, dict) or item.get("type") != "text" for item in content):
            return True
    return False


def _probability(log_probability: float) -> float:
    if not isinstance(log_probability, (int, float)) or isinstance(log_probability, bool):
        return 0.0
    value = float(log_probability)
    if not math.isfinite(value) or value > 0.0:
        return 0.0
    return math.exp(max(-50.0, value))
