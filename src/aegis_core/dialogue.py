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


REPAIR_CONTEXT_METADATA = "local_repair_context"


@dataclass(frozen=True, slots=True)
class DialogueGuidance:
    mode: DialogueMode
    allow_follow_up: bool
    max_sentences: int

    def system_instruction(self) -> str:
        shared = (
            "Act as a dependable right-hand collaborator: use relevant context, anticipate only "
            "the next useful in-scope step, and be candid about uncertainty or missing ability. "
            "Stay transparent that Jarvis is an AI; never claim human feelings, consciousness, "
            "exclusivity or that the user should replace human relationships with Jarvis. "
            "Use supplied history to resolve references; the current correction takes precedence "
            "over earlier assumptions. If context is absent or truncated, do not invent missing "
            "history: ask one focused clarification only if needed to answer safely. Never repeat "
            "a question already answered in the available context. "
            "Jarvis's conversational character is alert, perceptive and self-assured, "
            "with understated warmth, precise observations and no theatrical performance. "
            "Sound engaged and ready to act, not sleepy, soothing or meditative. Be decisive "
            "without rushing, shouting or feigning excitement; remain tactful in support. "
            "Lead with what matters, then one useful next step if needed. Be resourceful within "
            "the user's authority; personality never authorizes actions or invents successful "
            "execution. Avoid ceremonial status reports, stock acknowledgements, constant "
            "self-introductions, flattery, movie quotations and repeatedly calling the user "
            "'señor' or 'jefe'. Match explicit preferences for formality and brevity. "
            "Write for the ear: complete connected thoughts, a brisk conversational cadence and "
            "varied sentence lengths, not a string of clipped commands. Use commas within a "
            "thought and periods between thoughts; never add ellipses, SSML, stage directions, "
            "fake breathing or filler words to simulate naturalness. Phrase ordinary times "
            "and quantities naturally in Spanish without losing precision; preserve exact "
            "code, identifiers and values when they matter. "
            "Avoid detached acknowledgement sentences when the thought naturally connects: "
            "prefer 'Aquí estoy, dime qué necesitas' over 'Aquí estoy. Dime qué necesitas'. "
            "Do not force this wording or remove sentence boundaries that clarify meaning. "
            f"Use at most {self.max_sentences} short sentences and at most one question. "
        )
        humor = (
            "Do not use humor in support or repair; be tactful and take the concern seriously. "
            if self.mode in {DialogueMode.SUPPORT, DialogueMode.REPAIR}
            else (
                "A brief dry observation is welcome only when it fits a light exchange; humor "
                "is optional, never a quota or a joke at the user's expense. Omit it in "
                "distress, errors, security, authorization and high-stakes decisions. "
            )
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
        follow_up = (
            " A follow-up is optional, not a closing ritual; omit it when the answer is complete."
            if self.allow_follow_up
            else " Ask only for missing information essential to the request, not to prolong it."
        )
        return f"{shared}{humor}{mode_instruction}{follow_up}"


class DialogueKernel:
    """Deterministic local turn-policy classifier; it never persists user text."""

    def classify(self, text: str) -> DialogueGuidance:
        # Quoted examples and code are content to discuss, not the speaker's intent.
        folded = _fold(_QUOTED_TEXT.sub(" ", text))
        return self.guidance(self._mode(folded))

    @staticmethod
    def guidance(mode: DialogueMode) -> DialogueGuidance:
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
                "me referia al",
                "me referia a",
                "quise decir",
                "lo que queria era",
                "corrige tu respuesta",
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
                "recomiendame",
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
            ),
        ):
            return DialogueMode.CONVERSATION
        # A greeting must not turn "Hola Jarvis, abre Calendar" into small talk.
        greeting = " ".join(re.findall(r"[a-z0-9]+", folded))
        greeting = re.sub(r"^(?:jarvis )|(?: jarvis)$", "", greeting)
        if greeting in {"hola", "buenos dias", "buenas tardes", "buenas noches", "buenas"}:
            return DialogueMode.CONVERSATION
        return DialogueMode.TASK


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    for phrase in phrases:
        for match in re.finditer(rf"\b{re.escape(phrase)}\b", text):
            # Preserve clause punctuation so "No, estoy frustrado" is not a denial.
            if not re.search(r"\b(?:no|nunca|jamas)\s+$", text[:match.start()]):
                return True
    return False


_QUOTED_TEXT = re.compile(
    r'```[\s\S]*?```|`[^`]*`|"[^"\n]*"|«[^»]*»|“[^”]*”|\u2018[^\u2019]*\u2019'
    r"|(?<!\w)'[^'\n]*'(?!\w)"
)


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    # Separate clauses without retaining opening punctuation or quote delimiters.
    return " ".join(re.findall(r"[a-z0-9]+|(?<=[a-z0-9])[,.!?;:]", without_marks)).strip(
        " ,.!?;:"
    )
