from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum


class DialogueMode(StrEnum):
    TASK = "task"
    CONVERSATION = "conversation"
    SUPPORT = "support"
    ADVICE = "advice"
    BRAINSTORM = "brainstorm"
    REPAIR = "repair"


@dataclass(frozen=True, slots=True)
class DialogueGuidance:
    mode: DialogueMode
    allow_follow_up: bool
    max_sentences: int

    def system_instruction(self) -> str:
        shared = (
            "Stay transparent that Jarvis is an AI; never claim human feelings, consciousness, "
            "exclusivity or that the user should replace human relationships with Jarvis. "
        )
        mode_instruction = {
            DialogueMode.TASK: (
                "Task mode: prioritize the requested outcome, be direct, and do not add a social "
                "question after a complete answer."
            ),
            DialogueMode.CONVERSATION: (
                "Conversation mode: respond naturally to the substance, show genuine-seeming "
                "attention without pretending to be human, and ask at most one relevant follow-up "
                "only when it helps the exchange continue."
            ),
            DialogueMode.SUPPORT: (
                "Support mode: acknowledge the expressed experience before offering solutions. "
                "Do not diagnose, exaggerate emotion, become possessive, or rush into tools. Ask "
                "at most one gentle question when useful."
            ),
            DialogueMode.ADVICE: (
                "Advice mode: identify the decision and its main tradeoff, give a clear but "
                "non-controlling recommendation, and distinguish preference from fact."
            ),
            DialogueMode.BRAINSTORM: (
                "Brainstorm mode: collaborate, offer a small set of meaningfully different ideas, "
                "and invite selection rather than presenting a single answer as inevitable."
            ),
            DialogueMode.REPAIR: (
                "Repair mode: acknowledge the mismatch briefly, restate the corrected "
                "understanding, and continue without defensiveness, excuses or repeating the "
                "same assumption."
            ),
        }[self.mode]
        return f"{shared}{mode_instruction}"


class DialogueKernel:
    """Deterministic local turn-policy classifier; it never persists user text."""

    def classify(self, text: str) -> DialogueGuidance:
        folded = _fold(text)
        mode = self._mode(folded)
        return DialogueGuidance(
            mode=mode,
            allow_follow_up=mode
            in {
                DialogueMode.CONVERSATION,
                DialogueMode.SUPPORT,
                DialogueMode.ADVICE,
                DialogueMode.BRAINSTORM,
            },
            max_sentences={
                DialogueMode.TASK: 3,
                DialogueMode.CONVERSATION: 4,
                DialogueMode.SUPPORT: 5,
                DialogueMode.ADVICE: 5,
                DialogueMode.BRAINSTORM: 6,
                DialogueMode.REPAIR: 3,
            }[mode],
        )

    @staticmethod
    def _mode(folded: str) -> DialogueMode:
        if _contains_any(
            folded,
            (
                "eso no fue lo que dije",
                "eso no es lo que dije",
                "me entendiste mal",
                "no me entendiste",
                "no era eso",
                "te equivocaste",
            ),
        ):
            return DialogueMode.REPAIR
        if _contains_any(
            folded,
            (
                "me siento triste",
                "me siento solo",
                "me siento sola",
                "estoy preocupado",
                "estoy preocupada",
                "estoy frustrado",
                "estoy frustrada",
                "estoy abrumado",
                "estoy abrumada",
                "necesito desahogarme",
                "tuve un mal dia",
            ),
        ):
            return DialogueMode.SUPPORT
        if _contains_any(
            folded,
            (
                "lluvia de ideas",
                "hagamos brainstorming",
                "pensemos ideas",
                "dame ideas",
                "exploremos opciones",
            ),
        ):
            return DialogueMode.BRAINSTORM
        if _contains_any(
            folded,
            (
                "que me recomiendas",
                "que harias",
                "dame un consejo",
                "ayudame a decidir",
                "cual me conviene",
            ),
        ):
            return DialogueMode.ADVICE
        if _contains_any(
            folded,
            (
                "como estas",
                "conversemos",
                "hablemos",
                "quiero conversar",
                "que opinas",
                "hola jarvis",
            ),
        ):
            return DialogueMode.CONVERSATION
        return DialogueMode.TASK


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    padded = f" {text} "
    return any(f" {phrase} " in padded for phrase in phrases)


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(re.findall(r"[a-z0-9]+", without_marks))
