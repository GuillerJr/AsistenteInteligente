"""Canonical public routing API.

``routing`` remains import-compatible for existing integrations while new code may use
the less ambiguous ``brain.router`` module requested by the native hybrid brain.
"""

import re
import unicodedata
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

_BROWSER_NAMES = {
    "com.apple.safari": "Safari",
    "com.google.chrome": "Chrome",
    "com.parent.arc": "Arc",
    "company.thebrowser.browser": "Arc",
    "org.mozilla.firefox": "Firefox",
}
_EXPLICIT_BROWSER_MARKERS = {
    "chrome": "com.google.Chrome",
    "google chrome": "com.google.Chrome",
    "safari": "com.apple.Safari",
    "arc": "company.thebrowser.Browser",
    "firefox": "org.mozilla.firefox",
}
_BROWSER_ACTION = re.compile(
    r"\b(?:abre|abrir|busca|buscar|navega|navegar|reproduce|reproducir|"
    r"open|search|browse|navigate|play)\b"
)


@dataclass(frozen=True, slots=True)
class BrowserIntent:
    requires_browser: bool
    generic_target: bool
    explicit_bundle_identifier: str | None


@dataclass(frozen=True, slots=True)
class BrowserRoutingDecision:
    requires_selection: bool
    selected_bundle_identifier: str | None


def analyze_browser_intent(text: str) -> BrowserIntent:
    """Classify browser targeting without invoking a model or retaining the command."""
    normalized = _fold_text(text)
    explicit = next(
        (
            bundle_identifier
            for marker, bundle_identifier in _EXPLICIT_BROWSER_MARKERS.items()
            if re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", normalized)
        ),
        None,
    )
    has_generic_target = bool(
        re.search(r"(?<!\w)(?:el navegador|the browser|navegador|browser)(?!\w)", normalized)
    )
    action = _BROWSER_ACTION.search(normalized) is not None
    return BrowserIntent(
        requires_browser=action and (has_generic_target or explicit is not None),
        generic_target=action and has_generic_target and explicit is None,
        explicit_bundle_identifier=explicit,
    )


def decide_browser_target(
    text: str,
    *,
    installed_bundle_identifiers: tuple[str, ...],
    running_bundle_identifiers: tuple[str, ...],
) -> BrowserRoutingDecision:
    """Select one deterministic browser or require an explicit owner choice."""
    intent = analyze_browser_intent(text)
    if not intent.requires_browser:
        return BrowserRoutingDecision(False, None)
    installed = {
        bundle.casefold(): bundle
        for bundle in installed_bundle_identifiers
        if bundle.casefold() in _BROWSER_NAMES
    }
    if intent.explicit_bundle_identifier is not None:
        requested = intent.explicit_bundle_identifier.casefold()
        if requested == "company.thebrowser.browser" and requested not in installed:
            requested = "com.parent.arc"
        return BrowserRoutingDecision(False, installed.get(requested))
    if not intent.generic_target:
        return BrowserRoutingDecision(False, None)
    running = tuple(
        installed[bundle.casefold()]
        for bundle in running_bundle_identifiers
        if bundle.casefold() in installed
    )
    candidates = tuple(dict.fromkeys(running or tuple(installed.values())))
    if len(candidates) == 1:
        return BrowserRoutingDecision(False, candidates[0])
    return BrowserRoutingDecision(len(candidates) > 1, None)


def browser_name(bundle_identifier: str) -> str | None:
    return _BROWSER_NAMES.get(bundle_identifier.casefold())


def _fold_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return " ".join(
        "".join(
            character for character in decomposed if not unicodedata.combining(character)
        ).split()
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
    "BrowserIntent",
    "BrowserRoutingDecision",
    "CascadeDecision",
    "CascadeTarget",
    "ConfidenceMetrics",
    "RoutingPolicySnapshot",
    "TaskClassification",
    "TaskComplexity",
    "TokenConfidence",
    "analyze_browser_intent",
    "browser_name",
    "calibrate_token_confidence",
    "classify_task",
    "contains_non_text_content",
    "contains_private_data",
    "decide_browser_target",
    "decide_cascade",
    "extract_text",
    "extract_user_request",
    "profile_for_application",
]
