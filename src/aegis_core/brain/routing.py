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
SPECULATIVE_VERIFIER_MODEL_ID = "nvidia/nemotron-3.5-lightning-30b-a3b"

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

_DEEP_REASONING_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:analiza|analyse|analyze|razona|reason)\b.{0,80}\b(?:paso|step)s?\b",
        r"\b(?:diseña|design)\b.{0,80}\b(?:arquitectura|architecture)\b",
        r"\b(?:encuentra|find)\b.{0,80}\b(?:vulnerabilidad|vulnerability|exploit)\b",
        r"\b(?:implementa|implement|refactoriza|refactor|debug|depura)\b",
    )
)
_MULTISTEP_PATTERN = re.compile(
    r"\b(?:primero|first|luego|then|después|afterwards|finalmente|finally)\b",
    re.IGNORECASE,
)
_EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])[\w.!#$%&'*+/=?^`{|}~-]{1,64}@[A-Z0-9.-]{1,253}\.[A-Z]{2,63}(?![\w.-])",
    re.IGNORECASE,
)
_PHONE_PATTERN = re.compile(
    r"(?<!\w)(?:\+?\d{1,3}[ .-]?)?(?:\(?\d{2,4}\)?[ .-]?){2,4}\d{2,4}(?!\w)"
)
_IBAN_PATTERN = re.compile(r"(?<![A-Z0-9])[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]){11,30}(?![A-Z0-9])")
_CARD_CANDIDATE_PATTERN = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_GOVERNMENT_ID_PATTERN = re.compile(
    r"\b(?:ssn|social security|cédula|cedula|dni|pasaporte|passport|ruc)\s*"
    r"(?:n[oº°.:#-]?\s*)?[A-Z0-9-]{6,20}\b",
    re.IGNORECASE,
)
_PERSONAL_FINANCE_PATTERN = re.compile(
    r"\b(?:mi|my)\s+(?:cuenta\s+bancaria|bank\s+account|tarjeta|card|salario|salary|"
    r"balance|saldo|ingresos|income|impuestos|tax(?:es)?)\b",
    re.IGNORECASE,
)
_FINANCIAL_VALUE_PATTERN = re.compile(
    r"\b(?:account|cuenta|routing|swift|aba|saldo|balance|salary|salario|income|ingresos)\b"
    r".{0,48}(?:\d[ -]?){4,}",
    re.IGNORECASE,
)


class CascadeTarget(StrEnum):
    LOCAL = "local_foundation"
    NVIDIA_FAST = "nvidia_fast"
    NVIDIA_DEEP = "nvidia_deep"
    NVIDIA_ROLE = "nvidia_role"


class TaskComplexity(StrEnum):
    LOCAL = "local"
    CLOUD_FAST = "cloud_fast"
    CLOUD_DEEP = "cloud_deep"
    CLOUD_VISION = "cloud_vision"


@dataclass(frozen=True, slots=True)
class RoutingPolicySnapshot:
    thermal_throttled: bool = False
    low_power_mode: bool = False
    on_battery: bool = False
    cloud_token_threshold: int = 50

    def __post_init__(self) -> None:
        if not 16 <= self.cloud_token_threshold <= 4_096:
            raise ValueError("thermal cloud token threshold is invalid")


@dataclass(frozen=True, slots=True)
class TaskClassification:
    complexity: TaskComplexity
    score: int
    estimated_tokens: int
    privacy_sensitive: bool
    requires_code_or_security: bool
    requires_tools: bool
    contains_multimodal_input: bool


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
    classification: TaskClassification | None = None

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
    runtime_policy: RoutingPolicySnapshot | None = None,
) -> CascadeDecision:
    if not 0.0 < threshold < 1.0:
        raise ValueError("confidence threshold is invalid")
    classification = classify_task(
        role=role,
        messages=messages,
        extra_body=extra_body,
        contains_non_text_input=contains_non_text_input,
    )
    if classification.privacy_sensitive:
        return CascadeDecision(
            target=CascadeTarget.LOCAL,
            reason="privacy_on_device_only",
            model_ids=None,
            classification=classification,
        )
    if classification.complexity is TaskComplexity.CLOUD_VISION:
        return CascadeDecision(
            target=CascadeTarget.NVIDIA_ROLE,
            reason="multimodal_requires_specialist",
            model_ids=None,
            classification=classification,
        )
    if classification.complexity is TaskComplexity.CLOUD_DEEP:
        return CascadeDecision(
            target=CascadeTarget.NVIDIA_DEEP,
            reason="deep_reasoning_required",
            model_ids=(DEEP_REASONING_MODEL_ID,),
            classification=classification,
        )
    if classification.requires_tools:
        return CascadeDecision(
            target=CascadeTarget.NVIDIA_FAST,
            reason="tool_planning_required",
            model_ids=(FAST_PLANNING_MODEL_ID,),
            classification=classification,
        )
    if classification.complexity is TaskComplexity.CLOUD_FAST:
        return CascadeDecision(
            target=CascadeTarget.NVIDIA_FAST,
            reason="multi_step_planning_required",
            model_ids=(FAST_PLANNING_MODEL_ID,),
            classification=classification,
        )
    policy = runtime_policy or RoutingPolicySnapshot()
    if (
        (policy.thermal_throttled or policy.low_power_mode)
        and classification.estimated_tokens > policy.cloud_token_threshold
    ):
        return CascadeDecision(
            target=CascadeTarget.NVIDIA_FAST,
            reason="thermal_latency_budget_offload",
            model_ids=(FAST_PLANNING_MODEL_ID,),
            classification=classification,
        )
    if (
        policy.on_battery
        and classification.estimated_tokens > policy.cloud_token_threshold * 2
    ):
        return CascadeDecision(
            target=CascadeTarget.NVIDIA_FAST,
            reason="battery_latency_budget_offload",
            model_ids=(FAST_PLANNING_MODEL_ID,),
            classification=classification,
        )
    if confidence.calibrated_probability < threshold:
        return CascadeDecision(
            target=CascadeTarget.NVIDIA_FAST,
            reason="confidence_below_threshold",
            model_ids=(FAST_PLANNING_MODEL_ID,),
            classification=classification,
        )
    return CascadeDecision(
        target=CascadeTarget.LOCAL,
        reason="local_confidence_accepted",
        model_ids=None,
        classification=classification,
    )


def classify_task(
    *,
    role: AgentRole,
    messages: Sequence[Mapping[str, Any]],
    extra_body: Mapping[str, Any] | None = None,
    contains_non_text_input: bool = False,
) -> TaskClassification:
    prompt = extract_user_request(messages)
    all_text = extract_text(messages)
    terms = frozenset(re.findall(r"\w+", prompt.casefold(), flags=re.UNICODE))
    requires_tools = bool(
        extra_body and (extra_body.get("tools") or extra_body.get("tool_choice"))
    )
    requires_code_or_security = role in {
        AgentRole.CODE_SECURITY,
        AgentRole.CRITICAL_REASONER,
    } or not terms.isdisjoint(_CODE_TERMS | _SECURITY_TERMS)
    deep_pattern_hits = sum(bool(pattern.search(prompt)) for pattern in _DEEP_REASONING_PATTERNS)
    multistep_hits = len(terms & _MULTISTEP_TERMS) + len(_MULTISTEP_PATTERN.findall(prompt))
    estimated_tokens = _estimate_tokens(prompt)
    privacy_sensitive = contains_private_data(all_text)
    if contains_non_text_input or role in {AgentRole.VISION, AgentRole.OMNI}:
        complexity = TaskComplexity.CLOUD_VISION
        score = 100
    elif requires_code_or_security or deep_pattern_hits:
        complexity = TaskComplexity.CLOUD_DEEP
        score = min(100, 70 + deep_pattern_hits * 10)
    elif requires_tools or multistep_hits >= 2:
        complexity = TaskComplexity.CLOUD_FAST
        score = min(69, 45 + multistep_hits * 5 + (10 if requires_tools else 0))
    else:
        complexity = TaskComplexity.LOCAL
        score = min(44, max(0, estimated_tokens // 4))
    return TaskClassification(
        complexity=complexity,
        score=score,
        estimated_tokens=estimated_tokens,
        privacy_sensitive=privacy_sensitive,
        requires_code_or_security=requires_code_or_security,
        requires_tools=requires_tools,
        contains_multimodal_input=contains_non_text_input,
    )


def contains_private_data(text: str) -> bool:
    if not text:
        return False
    normalized = " ".join(text.split())
    if (
        _EMAIL_PATTERN.search(normalized)
        or _IBAN_PATTERN.search(normalized.upper())
        or _GOVERNMENT_ID_PATTERN.search(normalized)
        or _PERSONAL_FINANCE_PATTERN.search(normalized)
        or _FINANCIAL_VALUE_PATTERN.search(normalized)
    ):
        return True
    for candidate in _CARD_CANDIDATE_PATTERN.finditer(normalized):
        digits = "".join(character for character in candidate.group() if character.isdigit())
        if 13 <= len(digits) <= 19 and _luhn_valid(digits):
            return True
    for candidate in _PHONE_PATTERN.finditer(normalized):
        raw = candidate.group()
        digits = "".join(character for character in raw if character.isdigit())
        if 8 <= len(digits) <= 15 and any(separator in raw for separator in "+-(). "):
            return True
    return False


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


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    lexical_units = len(re.findall(r"\w+|[^\w\s]", text, flags=re.UNICODE))
    return max(1, lexical_units, math.ceil(len(text) / 4))


def _luhn_valid(digits: str) -> bool:
    checksum = 0
    parity = len(digits) % 2
    for index, character in enumerate(digits):
        value = ord(character) - ord("0")
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        checksum += value
    return checksum % 10 == 0
