"""A reply that asks the user becomes one question with options, or stays unchanged."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from test_job_interactions import _evaluation

from aegis_core.job_interactions import task_input_request
from aegis_core.orchestration.clarification import may_ask_user, structured_clarification

REPLY = ("Para agendar la reunión necesito: 1. fecha y hora, 2. duración, 3. asistentes. "
         "¿Me los indicas?")


class Router:
    def __init__(self, answer: object) -> None:
        self.answer = answer
        self.calls = 0

    async def __call__(self, messages):
        self.calls += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer if isinstance(self.answer, str) else json.dumps(self.answer)


@pytest.mark.asyncio
async def test_several_requested_facts_become_one_question_with_choices():
    router = Router({"asks": True, "question": "¿Cuándo quieres la reunión?",
                     "options": ["Hoy por la tarde", "Mañana a las 10", "Esta semana"]})
    rendered = await structured_clarification("Agenda una reunión", REPLY, router)
    assert rendered == ("¿Cuándo quieres la reunión?\n\nOpciones:\n1. Hoy por la tarde\n"
                        "2. Mañana a las 10\n3. Esta semana")
    request = task_input_request(request_text="Agenda una reunión", result=rendered,
                                 evaluation=_evaluation("Agenda una reunión", rendered),
                                 now=datetime.now(UTC))
    assert request.options == ("Hoy por la tarde", "Mañana a las 10", "Esta semana")


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    {"asks": False, "question": "", "options": []},
    {"asks": True, "question": "Dime la fecha", "options": ["Hoy", "Mañana"]},
    {"asks": True, "question": "¿Cuándo?", "options": ["Hoy"]},
    {"asks": True, "question": "¿Cuándo?", "options": ["Hoy", "Hoy"]},
    {"asks": True, "question": "¿Cuándo?", "options": ["Hoy", "x" * 81]},
    {"asks": True, "question": "¿Cuándo?\n¿Dónde?", "options": ["Hoy", "Mañana"]},
    {"asks": True, "question": "¿Cuándo?", "options": ["Hoy", "Mañana"], "send": True},
    "not json", TimeoutError(),
])
async def test_invalid_or_negative_answers_keep_the_reply(answer):
    assert await structured_clarification("Agenda una reunión", REPLY, Router(answer)) is None


@pytest.mark.asyncio
async def test_already_structured_or_empty_replies_skip_the_router():
    router = Router({"asks": True, "question": "¿Cuál?", "options": ["A", "B"]})
    assert not may_ask_user("¿Cuál?\n\nOpciones:\n1. A\n2. B")
    assert await structured_clarification("x", "   ", router) is None
    assert router.calls == 0


@pytest.mark.parametrize("reply", [
    "**Opciones:**\n\n1. Filtrar por remitente\n2. Buscar adjuntos",
    "Puedo hacer esto:\n- Filtrar\n- Buscar",
    "¿Qué prefieres?",
])
def test_bold_or_plain_lists_and_questions_reach_the_router(reply):
    assert may_ask_user(reply)


@pytest.mark.parametrize("reply", ["listo", "Python es un lenguaje de programación."])
def test_finished_answers_do_not_pay_the_router(reply):
    assert not may_ask_user(reply)


def _turns(*pairs):
    from uuid import uuid4

    from aegis_core.memory.contracts import ConversationTurn

    conversation = uuid4()
    return tuple(ConversationTurn(
        conversation_id=conversation, sequence=index, role=role, content=content,
        created_at=datetime.now(UTC), content_sha256=ConversationTurn.digest_content(content),
    ) for index, (role, content) in enumerate(pairs, 1)) if pairs else ()


OFFERED = ("**Opciones:**\n\n1. Filtrar por remitente\n2. Marcar como leídos\n"
           "3. Ver un mensaje\n4. Buscar mensajes con adjuntos\n5. Otro")


@pytest.mark.parametrize("answer", ["4", "4.", "la 4", "opción 4", "La cuarta", "cuarto"])
def test_a_number_after_offered_options_means_that_option(answer):
    from aegis_core.memory.contracts import ConversationRole
    from aegis_core.orchestration.clarification import expand_option_answer

    history = _turns((ConversationRole.USER, "hazme preguntas"),
                     (ConversationRole.ASSISTANT, OFFERED))
    assert expand_option_answer(answer, history) == "Buscar mensajes con adjuntos"
    prefixed = "Solicitud original: hazme preguntas\nRespuesta a la aclaración: " + answer
    assert expand_option_answer(prefixed, history).endswith(
        "Respuesta a la aclaración: Buscar mensajes con adjuntos"
    )


@pytest.mark.parametrize("answer,last", [
    ("9", OFFERED), ("4 correos", OFFERED), ("busca adjuntos", OFFERED),
    ("4", "Tienes 4 correos sin leer."),
])
def test_other_answers_or_replies_without_options_pass_through(answer, last):
    from aegis_core.memory.contracts import ConversationRole
    from aegis_core.orchestration.clarification import expand_option_answer

    history = _turns((ConversationRole.USER, "x"), (ConversationRole.ASSISTANT, last))
    assert expand_option_answer(answer, history) == answer
    assert expand_option_answer(answer, ()) == answer
