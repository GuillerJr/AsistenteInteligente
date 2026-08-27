from __future__ import annotations

import re
import unicodedata
from enum import StrEnum


class OwnerFeedback(StrEnum):
    HELPFUL = "helpful"
    UNHELPFUL = "unhelpful"


FEEDBACK_STATUS_METADATA = "owner_feedback_status"
FEEDBACK_TARGET_AVAILABLE = "target_available"
FEEDBACK_TARGET_MISSING = "target_missing"
FEEDBACK_OWNER_UNVERIFIED = "owner_unverified"

_FEEDBACK_PHRASES = {
    "esa respuesta fue util": OwnerFeedback.HELPFUL,
    "esa respuesta me ayudo": OwnerFeedback.HELPFUL,
    "la respuesta fue util": OwnerFeedback.HELPFUL,
    "eso me ayudo": OwnerFeedback.HELPFUL,
    "esa respuesta no fue util": OwnerFeedback.UNHELPFUL,
    "esa respuesta no me ayudo": OwnerFeedback.UNHELPFUL,
    "la respuesta no fue util": OwnerFeedback.UNHELPFUL,
    "eso no me ayudo": OwnerFeedback.UNHELPFUL,
}


def extract_owner_feedback(text: str) -> OwnerFeedback | None:
    if not text or text.rstrip().endswith("?"):
        return None
    folded = _fold(text)
    if folded.startswith("jarvis "):
        folded = folded.removeprefix("jarvis ")
    return _FEEDBACK_PHRASES.get(folded)


def owner_feedback_response(text: str, *, status: object) -> str | None:
    feedback = extract_owner_feedback(text)
    if feedback is None:
        return None
    if status == FEEDBACK_OWNER_UNVERIFIED:
        return "No registré el feedback porque no pude verificar la voz del propietario."
    if status != FEEDBACK_TARGET_AVAILABLE:
        return "No encontré una respuesta reciente a la que aplicar ese feedback."
    if feedback is OwnerFeedback.HELPFUL:
        return "Gracias. Registré la respuesta anterior como útil."
    return "Entendido. Registré la respuesta anterior como poco útil."


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(re.findall(r"[a-z0-9]+", without_marks))
