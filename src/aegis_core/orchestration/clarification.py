"""Turn a reply that asks the user for something into one question with options.

The planner model does not reliably follow a fixed clarification format: it asks for
several facts at once or puts its options before the question. When a reply without
tool calls ends up asking the user, the router restates it as strict JSON with the
single most important question and a few likely answers; Jarvis then renders the
"Opciones:" form that every channel turns into choices. Any failure keeps the reply.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Sequence

from aegis_core.completion_boundary import optional_completion
from aegis_core.job_interactions import question_with_options
from aegis_core.memory.contracts import ConversationRole, ConversationTurn
from aegis_core.privacy import redact_secrets_for_remote

_INSTRUCTION = (
    "Recibes la petición del usuario y la respuesta que su asistente Jarvis iba a darle. "
    "Si esa respuesta pide al usuario información o una elección para poder continuar, "
    "devuelve la pregunta más importante (una sola, en español, que termine en '?') y de "
    "2 a 5 respuestas probables, cortas y concretas, que el usuario pueda elegir. Si la "
    "respuesta ya resuelve la petición o solo ofrece ayuda adicional por cortesía, "
    'devuelve asks=false. Devuelve exclusivamente JSON {"asks": true|false, '
    '"question": "texto", "options": ["opción"]}.'
)
MAX_REPLY_CHARACTERS = 6_000

Complete = Callable[[list[dict[str, str]]], Awaitable[str]]


# Shape only, never vocabulary: a reply that asks for something contains a question
# or introduces a list of what it needs. The router then decides what it means.
_ASKING_SHAPE = re.compile(r"\?|:[*_]*\s*\n\s*(?:[0-9]{1,2}[.)]|[-•*])\s")


# Text this long before the closing question is an answer, not a request for input.
_ANSWER_CHARACTERS = 280


def _answer_before_question(reply: str) -> str:
    paragraphs = [part for part in re.split(r"\n\s*\n", reply.strip()) if part.strip()]
    while paragraphs and "?" in paragraphs[-1]:
        paragraphs.pop()
    answer = "\n\n".join(paragraphs).strip()
    return answer if len(answer) >= _ANSWER_CHARACTERS and "?" not in answer else ""


def may_ask_user(reply: str) -> bool:
    return (bool(reply.strip()) and len(reply) <= MAX_REPLY_CHARACTERS
            and question_with_options(reply) is None and bool(_ASKING_SHAPE.search(reply)))


async def structured_clarification(request: str, reply: str, complete: Complete) -> str | None:
    """The reply restated as one question plus options, or None to keep it as is."""
    if not may_ask_user(reply):
        return None
    try:
        content = await optional_completion(complete, [
            {"role": "system", "content": _INSTRUCTION},
            {"role": "user", "content": json.dumps({
                "request": redact_secrets_for_remote(request[:1_000]).text,
                "reply": redact_secrets_for_remote(reply[-2_000:]).text,
            }, ensure_ascii=False)},
        ])
        payload = json.loads(content) if len(content.encode()) <= 2_048 else None
    except (RecursionError, TypeError, ValueError):
        return None
    if (not isinstance(payload, dict) or set(payload) != {"asks", "question", "options"}
            or payload["asks"] is not True):
        return None
    question, options = payload["question"], payload["options"]
    if (not isinstance(question, str) or not 3 <= len(question.strip()) <= 300
            or not question.strip().endswith("?") or "\n" in question.strip()
            or not isinstance(options, list) or not 2 <= len(options) <= 5
            or any(not isinstance(o, str) or not 1 <= len(o.strip()) <= 80 or "\n" in o
                   for o in options)
            or len({o.strip() for o in options}) != len(options)):
        return None
    rendered = question.strip() + "\n\nOpciones:\n" + "\n".join(
        f"{index}. {option.strip()}" for index, option in enumerate(options, 1)
    )
    answer = _answer_before_question(reply)
    if answer:
        # The reply already answered and ends by offering more: keep the answer and
        # offer the follow-up as choices, instead of replacing it with only a question.
        rendered = answer + "\n\n" + rendered
    # The rendered form must be exactly what the input-request parser accepts.
    return rendered if question_with_options(rendered) is not None else None


_OPTION_LIST = re.compile(
    r"(?ms)^[ \t]*[*_]*(?:Opciones|Options)[*_]*\s*:[*_]*[ \t]*\n"
    r"(?P<options>(?:\s*(?:[0-9]{1,2}[.)])[ \t]+[^\n]+\n?){2,6})\s*\Z"
)
_ORDINAL_WORDS = {"primera": 1, "primero": 1, "segunda": 2, "segundo": 2, "tercera": 3,
                  "tercero": 3, "cuarta": 4, "cuarto": 4, "quinta": 5, "quinto": 5,
                  "sexta": 6, "sexto": 6}
_ANSWER_PREFIX = "Respuesta a la aclaración: "


def _offered_options(reply: str) -> tuple[str, ...]:
    parsed = question_with_options(reply)
    if parsed is not None:
        return parsed[1]
    match = _OPTION_LIST.search(reply.strip())
    if match is None:
        return ()
    return tuple(re.sub(r"^\s*[0-9]{1,2}[.)][ \t]+", "", line).strip().strip("*").strip()
                 for line in match["options"].splitlines() if line.strip())


def _chosen_index(answer: str) -> int | None:
    folded = re.sub(r"[.!¡¿?]", "", answer.casefold()).strip()
    folded = re.sub(r"^(?:la|el|opcion|opción|la opcion|la opción|numero|número)\s+", "", folded)
    if re.fullmatch(r"[0-9]{1,2}", folded):
        return int(folded)
    return _ORDINAL_WORDS.get(folded)


def expand_option_answer(text: str, history: Sequence[ConversationTurn]) -> str:
    """Replace "4" or "la cuarta" with the option Jarvis just offered under that number.

    Only the latest assistant turn counts, and only an answer that is nothing but the
    choice; anything else is a free answer and passes through unchanged.
    """
    last = next((turn for turn in reversed(history)
                 if turn.role is ConversationRole.ASSISTANT), None)
    if last is None:
        return text
    options = _offered_options(last.content)
    if not options:
        return text
    prefix, answer = (text.rsplit(_ANSWER_PREFIX, 1)[0] + _ANSWER_PREFIX,
                      text.rsplit(_ANSWER_PREFIX, 1)[1]) if _ANSWER_PREFIX in text else ("", text)
    index = _chosen_index(answer)
    if index is None or not 1 <= index <= len(options):
        return text
    return prefix + options[index - 1]
