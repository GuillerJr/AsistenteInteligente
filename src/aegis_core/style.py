from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from aegis_core.memory.contracts import MemorySearchHit

OWNER_STYLE_TAG = "owner-style"


class StyleDimension(StrEnum):
    VERBOSITY = "verbosity"
    TONE = "tone"
    FOLLOW_UP = "follow_up"
    REPETITION = "repetition"


@dataclass(frozen=True, slots=True)
class StylePreference:
    dimension: StyleDimension
    setting: str
    source: str
    content: str
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _StyleRule:
    dimension: StyleDimension
    setting: str
    phrases: tuple[str, ...]
    content: str

    @property
    def tag(self) -> str:
        return f"style.{self.dimension.value}.{self.setting}"


_RULES = (
    _StyleRule(
        StyleDimension.VERBOSITY,
        "concise",
        (
            "dame menos detalle",
            "hablame mas breve",
            "prefiero respuestas breves",
            "quiero respuestas breves",
            "responde mas breve",
            "respondeme mas breve",
            "se mas breve",
            "se mas conciso",
        ),
        "Preferencia de estilo: respuestas breves y sin detalle innecesario.",
    ),
    _StyleRule(
        StyleDimension.VERBOSITY,
        "detailed",
        (
            "dame mas detalle",
            "explica mas",
            "prefiero respuestas detalladas",
            "quiero respuestas detalladas",
            "responde con mas detalle",
            "se mas detallado",
        ),
        "Preferencia de estilo: respuestas detalladas cuando el tema lo requiera.",
    ),
    _StyleRule(
        StyleDimension.TONE,
        "natural",
        (
            "hablame mas natural",
            "quiero que hables mas natural",
            "se mas natural",
            "suena mas natural",
        ),
        "Preferencia de estilo: tono natural y poco mecánico.",
    ),
    _StyleRule(
        StyleDimension.TONE,
        "direct",
        (
            "hablame mas directo",
            "responde mas directo",
            "se mas directo",
            "ve al grano",
        ),
        "Preferencia de estilo: tono directo y orientado al resultado.",
    ),
    _StyleRule(
        StyleDimension.TONE,
        "warm",
        (
            "hablame con mas calidez",
            "quiero un tono mas calido",
            "se mas calido",
        ),
        "Preferencia de estilo: tono cálido sin fingir emociones humanas.",
    ),
    _StyleRule(
        StyleDimension.FOLLOW_UP,
        "none",
        (
            "no hagas preguntas de seguimiento",
            "no termines con preguntas",
            "sin preguntas de seguimiento",
        ),
        "Preferencia de estilo: no cerrar con preguntas salvo aclaración imprescindible.",
    ),
    _StyleRule(
        StyleDimension.FOLLOW_UP,
        "contextual",
        (
            "haz preguntas de seguimiento",
            "preguntame cuando sea util",
            "puedes hacer preguntas de seguimiento",
        ),
        "Preferencia de estilo: una pregunta de seguimiento solo cuando sea útil.",
    ),
    _StyleRule(
        StyleDimension.REPETITION,
        "avoid",
        (
            "evita repetir",
            "no repitas",
            "no seas repetitiva",
            "no seas repetitivo",
        ),
        "Preferencia de estilo: evitar repetir información ya comprendida.",
    ),
)
_INSTRUCTION_BY_TAG = {
    "style.verbosity.concise": (
        "Owner style: be concise and omit nonessential detail."
    ),
    "style.verbosity.detailed": (
        "Owner style: include useful detail when the request benefits from it."
    ),
    "style.tone.natural": (
        "Owner style: use natural, varied phrasing and avoid scripted filler."
    ),
    "style.tone.direct": "Owner style: lead with the outcome and be direct.",
    "style.tone.warm": (
        "Owner style: sound warm while remaining transparent that Jarvis is an AI."
    ),
    "style.follow_up.none": (
        "Owner style: do not end with a question unless clarification is required."
    ),
    "style.follow_up.contextual": (
        "Owner style: ask at most one relevant follow-up only when useful."
    ),
    "style.repetition.avoid": (
        "Owner style: do not repeat information the owner has already understood."
    ),
}
_RESET_STYLE_PHRASES = frozenset(
    {
        "olvida mis preferencias de estilo",
        "reinicia tus preferencias de estilo",
        "restablece tu estilo",
    }
)
_ACKNOWLEDGEMENT_BY_TAG = {
    "style.verbosity.concise": "respuestas más breves",
    "style.verbosity.detailed": "más detalle cuando sea útil",
    "style.tone.natural": "un tono más natural",
    "style.tone.direct": "un tono más directo",
    "style.tone.warm": "un tono más cálido",
    "style.follow_up.none": "evitar preguntas finales innecesarias",
    "style.follow_up.contextual": "preguntas de seguimiento solo cuando ayuden",
    "style.repetition.avoid": "menos repetición",
}


def extract_style_preferences(text: str) -> tuple[StylePreference, ...]:
    if not text or text.rstrip().endswith("?"):
        return ()
    folded = _fold(text)
    matches = _matching_rules(folded)

    preferences = []
    for dimension in StyleDimension:
        rules = matches.get(dimension, [])
        settings = {rule.setting for rule in rules}
        if len(settings) != 1:
            continue
        rule = rules[0]
        preferences.append(
            StylePreference(
                dimension=dimension,
                setting=rule.setting,
                source=f"owner-profile:style:{dimension.value}",
                content=rule.content,
                tags=(OWNER_STYLE_TAG, "communication", rule.tag),
            )
        )
    return tuple(preferences)


def style_reset_requested(text: str) -> bool:
    return _fold(text) in _RESET_STYLE_PHRASES


def style_feedback_response(text: str) -> str | None:
    if not text or text.rstrip().endswith("?"):
        return None
    if style_reset_requested(text):
        return "Entendido. Desde el próximo turno usaré el estilo predeterminado."
    folded = _fold(text)
    matches = _matching_rules(folded)
    if not matches:
        return None
    preferences = extract_style_preferences(text)
    if not preferences:
        return "No cambié el estilo porque recibí preferencias contradictorias."
    adjustments = [
        _ACKNOWLEDGEMENT_BY_TAG[next(tag for tag in preference.tags if tag.startswith("style."))]
        for preference in preferences
    ]
    rendered = (
        adjustments[0]
        if len(adjustments) == 1
        else f"{', '.join(adjustments[:-1])} y {adjustments[-1]}"
    )
    return f"Entendido. Desde el próximo turno aplicaré {rendered}."


def owner_style_instruction(hits: tuple[MemorySearchHit, ...]) -> str:
    selected: dict[StyleDimension, str] = {}
    for hit in hits:
        if OWNER_STYLE_TAG not in hit.tags:
            continue
        for tag in hit.tags:
            instruction = _INSTRUCTION_BY_TAG.get(tag)
            if instruction is None:
                continue
            dimension_value = tag.split(".", 2)[1]
            try:
                dimension = StyleDimension(dimension_value)
            except ValueError:
                continue
            selected.setdefault(dimension, instruction)
    return " ".join(selected[dimension] for dimension in StyleDimension if dimension in selected)


def _matching_rules(value: str) -> dict[StyleDimension, list[_StyleRule]]:
    matches: dict[StyleDimension, list[_StyleRule]] = {}
    for rule in _RULES:
        if any(_contains_phrase(value, phrase) for phrase in rule.phrases):
            matches.setdefault(rule.dimension, []).append(rule)
    return matches


def _contains_phrase(value: str, phrase: str) -> bool:
    return f" {phrase} " in f" {value} "


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(re.findall(r"[a-z0-9]+", without_marks))
