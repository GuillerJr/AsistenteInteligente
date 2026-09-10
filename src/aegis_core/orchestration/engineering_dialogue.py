"""Deterministic admission for a new project whose purpose is still unspecified."""

from __future__ import annotations

import re
import unicodedata

from aegis_core.contracts import AgentResult, AgentRole, InputModality, UserRequest
from aegis_core.engineering import is_engineering_request
from aegis_core.memory.contracts import ConversationRole, ConversationTurn
from aegis_core.orchestration.direct_actions import direct_local_response

_UNSPECIFIED_PROJECT = re.compile(
    r"(?:(?:quiero|quisiera|necesito|me gustaria) )?"
    r"(?:(?:crear|hacer|construir|programar|desarrollar|haz|crea|construye|desarrolla) )?"
    r"(?:(?:un|una|el|la) )?"
    r"(?:app|aplicacion|pagina web|sitio web|web|api|backend|frontend|proyecto|plataforma)"
    r"(?: (?:web|movil|de escritorio|local))?(?: por favor)?"
)


def _is_unspecified_project(text: str) -> bool:
    if len(text) > 256:
        return False
    folded = "".join(
        character
        for character in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(character)
    )
    return _UNSPECIFIED_PROJECT.fullmatch(" ".join(folded.split()).strip(".!?¿¡ ")) is not None


def engineering_clarification(
    request: UserRequest,
    history: tuple[ConversationTurn, ...],
) -> AgentResult | None:
    if (
        not is_engineering_request(request)
        or request.modalities != frozenset({InputModality.TEXT})
        or request.image is not None
        or not _is_unspecified_project(request.text)
    ):
        return None
    for turn in history:
        if turn.role is not ConversationRole.USER or _is_unspecified_project(turn.content):
            continue
        previous = direct_local_response(request.model_copy(update={"text": turn.content}))
        if previous is None or previous.model_id != "local/deterministic-greeting":
            # Any substantive owner context belongs to the model, not a guessed new project.
            return None
    return AgentResult(
        role=AgentRole.CODE_SECURITY,
        model_id="local/deterministic-engineering-clarification",
        content="¿Qué debe hacer la aplicación o qué problema quieres resolver con ella?",
    )
