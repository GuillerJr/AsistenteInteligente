"""Canonical public routing API.

``routing`` remains import-compatible for existing integrations while new code may use
the less ambiguous ``brain.router`` module requested by the native hybrid brain.
"""

from dataclasses import dataclass

from aegis_core.brain.routing import (
    DEEP_REASONING_MODEL_ID,
    FAST_PLANNING_MODEL_ID,
    SPECULATIVE_VERIFIER_MODEL_ID,
    CascadeDecision,
    CascadeTarget,
    ConfidenceMetrics,
    RoutingPolicySnapshot,
    TaskClassification,
    TaskComplexity,
    TokenConfidence,
    calibrate_token_confidence,
    classify_task,
    contains_non_text_content,
    contains_private_data,
    decide_cascade,
    extract_text,
    extract_user_request,
)


@dataclass(frozen=True, slots=True)
class ApplicationModelProfile:
    name: str
    instructions: str


_APPLICATION_PROFILES = {
    "com.google.chrome": ApplicationModelProfile(
        "browser_expert",
        "Prioriza DOM/CDP y Accessibility; verifica navegación, carga y reproducción.",
    ),
    "com.apple.safari": ApplicationModelProfile(
        "browser_expert",
        "Prioriza elementos web accesibles; verifica URL, título y estado de carga.",
    ),
    "com.apple.finder": ApplicationModelProfile(
        "finder_expert",
        "Usa la jerarquía visible; no borres, muevas ni sobrescribas sin confirmar.",
    ),
    "com.apple.mail": ApplicationModelProfile(
        "mail_expert",
        "Distingue lectura, borrador y envío; cualquier envío requiere autorización.",
    ),
    "com.apple.ical": ApplicationModelProfile(
        "calendar_expert",
        "Verifica zona horaria, inicio, fin y calendario antes de proponer cambios.",
    ),
}
_DEFAULT_PROFILE = ApplicationModelProfile(
    "macos_generalist",
    "Prioriza estado local observable, Accessibility y acciones reversibles y mínimas.",
)


def profile_for_application(bundle_identifier: str | None) -> ApplicationModelProfile:
    if not bundle_identifier:
        return _DEFAULT_PROFILE
    return _APPLICATION_PROFILES.get(bundle_identifier.casefold(), _DEFAULT_PROFILE)

__all__ = [
    "DEEP_REASONING_MODEL_ID",
    "FAST_PLANNING_MODEL_ID",
    "SPECULATIVE_VERIFIER_MODEL_ID",
    "ApplicationModelProfile",
    "CascadeDecision",
    "CascadeTarget",
    "ConfidenceMetrics",
    "RoutingPolicySnapshot",
    "TaskClassification",
    "TaskComplexity",
    "TokenConfidence",
    "calibrate_token_confidence",
    "classify_task",
    "contains_non_text_content",
    "contains_private_data",
    "decide_cascade",
    "extract_text",
    "extract_user_request",
    "profile_for_application",
]
