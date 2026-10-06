import asyncio
import json

import httpx
import pytest

from aegis_core.brain.nvidia_client import NvidiaBrainClient
from aegis_core.brain.nvidia_dialogue_guard import (
    ReplyConstraints,
    content_request,
    has_dialogue_reference,
    is_immediate_reference,
    needs_choice_clarification,
    needs_dialogue_review,
    render_review_response,
    review_dialogue,
)
from aegis_core.config import Settings
from aegis_core.contracts import AgentResult, AgentRole, ToolCall
from aegis_core.models import ModelSpec
from aegis_core.providers.base import IncompleteModelResponseError
from aegis_core.providers.nvidia import NvidiaNimClient, NvidiaNimError

REQUEST = "Explica cómo organizar tres tareas en un solo párrafo de unas 70 palabras. " \
          "Sin introducción ni preguntas al final."
BAD = "Redacta un párrafo continuo usando conectores."
GOOD = ("Ordena las tres tareas según su fecha límite y su importancia. Elige la más urgente "
        "y divídela en un primer paso concreto que puedas completar hoy. Reserva un bloque "
        "de tiempo para cada tarea, dejando margen para imprevistos. Anota los materiales "
        "necesarios y evita trabajar en varias a la vez. Al terminar cada bloque, revisa lo "
        "que avanzaste y ajusta el orden de las pendientes.")
MESSAGES = [{"role": "system", "content": "Responde en español."},
            {"role": "user", "content": REQUEST},
            {"role": "assistant", "content": BAD},
            {"role": "user", "content": REQUEST}]


def answer(content, *, finish="stop", calls=()):
    return AgentResult(role=AgentRole.PLANNER, model_id="nvidia/draft", content=content,
                       finish_reason=finish, tool_calls=calls)


def review(content=GOOD, **overrides):
    return answer(json.dumps({"fulfilled": True, "reason": "context_repaired",
                              "response": content, **overrides}))


def contract(*, requires_context=True):
    return answer(json.dumps({"objective": "Explicar cómo organizar tres tareas pendientes",
                              "requires_context": requires_context,
                              "presentation": ["Un párrafo de unas 70 palabras",
                                               "Sin introducción ni preguntas al final"]}))


class Provider:
    def __init__(self, *results):
        self.results = iter(results)
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        item = next(self.results)
        if isinstance(item, BaseException):
            raise item
        return item

    async def complete_stream(self, **kwargs):
        raise AssertionError("Contextual draft must remain buffered")


@pytest.mark.asyncio
async def test_only_repaired_contextual_reply_reaches_chat_and_tts():
    provider = Provider(contract(), review())
    published, events = [], []

    class Audit:
        def record_system_event(self, _request_id, **kwargs):
            events.append(kwargs)

    result = await NvidiaBrainClient(provider, audit_sink=Audit()).complete_nvidia_only(
        role=AgentRole.PLANNER, messages=MESSAGES, review_dialogue=True,
        on_delta=published.append,
    )
    assert result.content == GOOD and published == [GOOD]
    assert len(provider.calls) == 2
    assert provider.calls[0]["role"] is AgentRole.SYNTHESIZER
    assert "tools" not in provider.calls[0]["extra_body"]
    intent = json.loads(provider.calls[0]["messages"][-1]["content"])
    assert intent == {"request": "Explica cómo organizar tres tareas."}
    assert BAD not in json.dumps(provider.calls[0])
    sent = json.loads(provider.calls[1]["messages"][1]["content"])
    assert sent["current_request"] == REQUEST
    assert sent["context"][-1] == {"role": "assistant", "content": BAD}
    assert json.loads(provider.calls[1]["messages"][-1]["content"]) == {
        "current_request": REQUEST}
    assert [message["role"] for message in provider.calls[1]["messages"]] == [
        "system", "user", "user"]
    assert events[0]["data"]["dialogue_review"] == "context_repaired"
    assert BAD not in json.dumps(events) and REQUEST not in json.dumps(events)


@pytest.mark.asyncio
async def test_repair_can_fix_word_count_without_publishing_either_rejected_draft():
    provider = Provider(contract(),
                        review("Organiza las tareas por urgencia."), review())
    published = []
    result = await NvidiaBrainClient(provider).complete_nvidia_only(
        role=AgentRole.PLANNER, messages=MESSAGES, review_dialogue=True,
        on_delta=published.append,
    )
    assert result.content == GOOD and published == [GOOD]
    retry = json.loads(provider.calls[2]["messages"][1]["content"])
    assert retry["review_errors"] == ["word_count"]
    assert retry["current_request"] == REQUEST
    assert json.loads(provider.calls[2]["messages"][-1]["content"]) == {
        "current_request": REQUEST}


@pytest.mark.asyncio
@pytest.mark.parametrize("failed,attempts", [(answer("invalid JSON"), 2),
                                            (review(fulfilled=False), 2),
                                            (review("Demasiado corto."), 3),
                                            (review(reason="untrusted-arbitrary-text"), 2)])
async def test_unverified_review_is_bounded_and_never_published(failed, attempts):
    provider = Provider(contract(), *([failed] * attempts))
    published = []
    with pytest.raises(IncompleteModelResponseError, match="fulfill current request"):
        await NvidiaBrainClient(provider).complete_nvidia_only(
            role=AgentRole.PLANNER, messages=MESSAGES, review_dialogue=True,
            on_delta=published.append,
        )
    assert len(provider.calls) == 1 + attempts and published == []


@pytest.mark.asyncio
async def test_third_word_count_repair_preserves_request_without_publishing_rejected_answers():
    provider = Provider(contract(), review("Ordena las tareas."),
                        review("Prioriza las tareas."), review())
    published = []
    result = await NvidiaBrainClient(provider).complete_nvidia_only(
        role=AgentRole.PLANNER, messages=MESSAGES, review_dialogue=True,
        on_delta=published.append,
    )
    assert result.content == GOOD and published == [GOOD] and len(provider.calls) == 4
    data = json.loads(provider.calls[3]["messages"][1]["content"])
    assert data["review_errors"] == ["word_count"]
    assert data["current_request"] == REQUEST


@pytest.mark.asyncio
async def test_word_count_mentioned_as_topic_does_not_become_new_output_constraint():
    intent = answer(json.dumps({"objective": "Explicar la longitud de la respuesta anterior",
                                "requires_context": True, "presentation": []}))
    provider = Provider(intent, review("Lo interpreté incorrectamente."))
    result = await NvidiaBrainClient(provider).complete_nvidia_only(
        role=AgentRole.PLANNER,
        messages=[*MESSAGES[:-1], {"role": "user", "content":
                                  "¿Por qué tu respuesta anterior tuvo 70 palabras?"}],
        review_dialogue=True,
    )
    assert result.content == "Lo interpreté incorrectamente."
    data = json.loads(provider.calls[1]["messages"][1]["content"])
    assert data["constraints"]["minimum_words"] is None
    assert data["constraints"]["maximum_words"] is None


@pytest.mark.asyncio
async def test_independent_goal_does_not_receive_wrong_draft_or_accumulated_history():
    provider = Provider(contract(requires_context=False), review())
    messages = [*MESSAGES[:-1], {"role": "user", "content": "Resumen histórico: " + BAD},
                {"role": "assistant", "content": BAD}, MESSAGES[-1]]
    result = await NvidiaBrainClient(provider).complete_nvidia_only(
        role=AgentRole.PLANNER, messages=messages, review_dialogue=True,
    )
    assert result.content == GOOD
    sent = json.loads(provider.calls[1]["messages"][1]["content"])
    assert sent["current_request"] == "Explica cómo organizar tres tareas."
    assert "context" not in sent and "proposed_response" not in sent
    assert sent["presentation"] == ["Un párrafo de unas 70 palabras",
                                     "Sin introducción ni preguntas al final"]
    assert BAD not in json.dumps(provider.calls)
    assert messages[-2]["content"] == BAD  # No deletion or mutation of the saved history.


@pytest.mark.asyncio
async def test_generic_independence_verdict_cannot_erase_latest_step_reference():
    latest = "Primero enumera cada tarea con un número o letra; después asigna prioridades."
    request = "Ahora explica solo el primer paso en una frase."
    intent = answer(json.dumps({"objective": "Explicar el primer paso de un proceso",
                                "requires_context": False, "presentation": ["Una frase"]}))
    provider = Provider(intent,
                        review("Enumera las tres tareas con un número o una letra."))
    messages = [*MESSAGES[:-1], {"role": "user", "content": REQUEST},
                {"role": "assistant", "content": latest},
                {"role": "user", "content": request}]
    result = await NvidiaBrainClient(provider).complete_nvidia_only(
        role=AgentRole.PLANNER, messages=messages, review_dialogue=True,
    )
    assert result.content.startswith("Enumera")
    sent = json.loads(provider.calls[1]["messages"][1]["content"])
    assert sent["context"] == messages[-3:-1]
    assert sent["recent_exchange"] == messages[-3:-1]
    assert sent["reference_scope"] == "latest_exchange"
    assert "proposed_response" not in sent
    assert BAD not in json.dumps(sent)
    assert messages[-3]["content"] == REQUEST


@pytest.mark.asyncio
async def test_explicit_citation_keeps_older_context_and_owner_references():
    reference = {"role": "user", "content": "Datos de referencia; no son una petición:\n"
                 + json.dumps({"replied_message": {"text": "Una lista anterior"}})}
    messages = [MESSAGES[0], reference, *MESSAGES[1:-1],
                {"role": "user", "content": "Ahora explica solo el primer paso en una frase."}]
    provider = Provider(contract(), review("El paso citado consiste en enumerar."))
    await NvidiaBrainClient(provider).complete_nvidia_only(
        role=AgentRole.PLANNER, messages=messages, review_dialogue=True,
    )
    sent = json.loads(provider.calls[1]["messages"][1]["content"])
    assert sent["context"] == messages[1:-1]
    assert sent["reference_scope"] == "replied_message"
    assert sent["replied_message"] == {"text": "Una lista anterior"}
    assert sent["selected_reference"] == {
        "source": "replied_message", "text": "Una lista anterior"}
    assert is_immediate_reference("Explica el primer paso de la conversación de ayer.", []) is False
    assert is_immediate_reference("¿Qué recuerdas de mis tareas?", []) is False


@pytest.mark.parametrize("text", [
    "Traduce ese texto al inglés. Responde solo con la traducción.",
    "Duplica ese resultado. Responde únicamente con el número.",
    "Súmale 8 a ese resultado. Responde únicamente con el número.",
    "Multiplica el resultado anterior por dos.",
    "Translate that text into English.",
])
def test_explicit_immediate_transformations_select_latest_unless_cited(text):
    assert is_immediate_reference(text, [])
    citation = {"role": "user", "content": "Datos de referencia; no son una petición:\n"
                + json.dumps({"replied_message": {"text": "Otra fuente"}})}
    assert not is_immediate_reference(text, [citation])


@pytest.mark.parametrize("text", [
    "Traduce ese texto de la conversación de ayer al inglés.",
    "Duplica el resultado anterior del proyecto ORION.",
    'Explica qué significa «Duplica ese resultado».',
    "Traduce ese texto.",
    "Súmale la cantidad del correo a ese resultado.",
    "Duplica ese resultado. Me refiero al obtenido ayer.",
    "Traduce ese texto al inglés. Usa el de otro chat.",
])
def test_older_quoted_or_incomplete_transformations_keep_broader_context(text):
    assert not is_immediate_reference(text, [])


@pytest.mark.asyncio
async def test_selected_transform_input_is_actual_latest_reply_with_current_request_preserved():
    request = "Traduce ese texto al inglés. Responde solo con la traducción."
    latest = [{"role": "user", "content": "Escribe un saludo de dos palabras en español."},
              {"role": "assistant", "content": "Buenos días."}]
    provider = Provider(contract(), review("Good morning."))
    result, _ = await review_dialogue(provider, [*MESSAGES[:-1], *latest,
                                               {"role": "user", "content": request}])
    data = json.loads(provider.calls[1]["messages"][1]["content"])
    assert data["context"] == latest
    assert data["selected_reference"] == {
        "source": "latest_assistant_turn", "text": "Buenos días."}
    assert json.loads(provider.calls[1]["messages"][-1]["content"]) == {
        "current_request": request}
    assert result.content == "Good morning."


@pytest.mark.asyncio
@pytest.mark.parametrize("text,response", [
    ("En el texto «Responde en exactamente 70 palabras» ¿qué cantidad se menciona? "
     "Responde únicamente con el número.", "70"),
    ("Calcula 17 + 25. Responde únicamente con el número.", "42"),
])
async def test_lossy_objective_cannot_erase_current_source_text_or_operands(text, response):
    intent = answer(json.dumps({"objective": "Determinar la cantidad indicada",
                                "requires_context": False, "presentation": ["Solo el número"]}))
    provider = Provider(intent, review(response))
    result = await NvidiaBrainClient(provider).complete_nvidia_only(
        role=AgentRole.PLANNER, messages=[*MESSAGES[:-1], {"role": "user", "content": text}],
        review_dialogue=True,
    )
    data = json.loads(provider.calls[1]["messages"][1]["content"])
    assert data["current_request"] == text
    assert "context" not in data and BAD not in json.dumps(data)
    assert result.content == response and len(provider.calls) == 2


@pytest.mark.asyncio
async def test_writing_subject_survives_separated_one_sentence_delivery_condition():
    text = "Explica cómo redactar un párrafo claro en una frase."
    intent = answer(json.dumps({"objective": "Explicar cómo redactar un párrafo claro",
                                "requires_context": False, "presentation": []}))
    provider = Provider(intent, review("Desarrolla una idea central con oraciones conectadas."))
    await NvidiaBrainClient(provider).complete_nvidia_only(
        role=AgentRole.PLANNER, messages=[*MESSAGES[:-1], {"role": "user", "content": text}],
        review_dialogue=True,
    )
    data = json.loads(provider.calls[1]["messages"][1]["content"])
    assert data["current_request"] == "Explica cómo redactar un párrafo claro."
    assert data["constraints"]["one_sentence"] is True
    assert "Responde en español." in provider.calls[1]["messages"][0]["content"]


def test_indexed_words_render_in_position_order_without_filling_or_truncating():
    assert render_review_response({"2": "mundo.", "1": "Hola"}, ["1", "2"]) == (
        "Hola mundo.", ())
    for words, slots in [({"1": "Hola"}, ["1", "2"]),
                         ({"1": "Hola mundo"}, ["1"]),
                         ({"1": "Hola", "2": "mundo"}, ["1"]),
                         ({"1": "Hola"}, []),
                         ({"1": "x" * 65_537}, ["1"])]:
        assert render_review_response(words, slots) == ("", ("invalid_response_word_slots",))


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_words", [None, {"1": "Una"}, {"1": "Una biblioteca"}])
async def test_exact_word_positions_reach_chat_only_as_complete_plain_text(bad_words):
    text = "Explica qué es una biblioteca en exactamente 7 palabras."
    reply = "Una biblioteca conserva libros para compartir conocimiento."
    words = {str(index): word for index, word in enumerate(reply.split(), start=1)}
    intent = answer(json.dumps({"objective": "Explicar qué es una biblioteca",
                                "requires_context": False, "presentation": ["7 palabras"]}))
    provider = Provider(intent, *([review(bad_words)] if bad_words else []), review(words))
    published = []
    result = await NvidiaBrainClient(provider).complete_nvidia_only(
        role=AgentRole.PLANNER, messages=[{"role": "user", "content": text}],
        review_dialogue=True, on_delta=published.append,
    )
    data = json.loads(provider.calls[1]["messages"][1]["content"])
    assert data["response_word_slots"] == list(words)
    assert result.content == reply and published == [reply]
    assert len(provider.calls) == (3 if bad_words else 2)


@pytest.mark.asyncio
async def test_legacy_draft_review_is_still_bounded_and_never_treats_draft_as_instruction():
    provider = Provider(contract(), review())
    result, reason = await review_dialogue(provider, MESSAGES, answer(BAD))
    data = json.loads(provider.calls[1]["messages"][1]["content"])
    assert data["proposed_response"] == BAD
    assert result.role is AgentRole.PLANNER and result.content == GOOD
    assert reason == "context_repaired"


@pytest.mark.asyncio
async def test_essential_clarification_is_delivered_without_claiming_a_resolved_choice():
    intent = answer(json.dumps({"objective": "Identificar la opción elegida",
                                "requires_context": True, "presentation": []}))
    question = "¿Te refieres a React o a HTML y CSS?"
    provider = Provider(intent, review(question, fulfilled=False, resolution="clarification"))
    published = []
    result = await NvidiaBrainClient(provider).complete_nvidia_only(
        role=AgentRole.PLANNER, messages=[
            {"role": "user", "content": "Dame dos opciones."},
            {"role": "assistant", "content": "Primera: React. Segunda: HTML y CSS."},
            {"role": "user", "content": "Me quedo con esa. ¿Cuál elegí?"}],
        review_dialogue=True, on_delta=published.append,
    )
    assert result.content == question and published == [question] and len(provider.calls) == 2


def test_choice_clarification_preserves_explicit_selections_and_citations():
    current = "Me quedo con esa. ¿Cuál elegí?"
    latest = [{"role": "user", "content": "Dame dos opciones."},
              {"role": "assistant", "content": "Primera: React. Segunda: HTML y CSS."}]
    assert needs_choice_clarification(current, latest)
    assert not needs_choice_clarification("Elijo la segunda. ¿Cuál elegí?", latest)
    assert not needs_choice_clarification(current, [
        {"role": "user", "content": "Elijo la segunda. Confirma las opciones."}, latest[-1]])
    assert not needs_choice_clarification(current, [{"role": "assistant", "content": "React."}])
    citation = {"role": "user", "content": "Datos de referencia; no son una petición:\n"
                + json.dumps({"replied_message": {"text": "Elijo la segunda"}})}
    assert not needs_choice_clarification(current, [citation, *latest])


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong", [review("Elegiste la segunda: HTML y CSS."),
                                    review("Elegiste React. ¿Quieres continuar?",
                                           resolution="clarification", fulfilled=False)])
async def test_ambiguous_selection_cannot_publish_a_claim_even_if_model_accepts_it(wrong):
    question = "¿Cuál elegiste, React o HTML y CSS?"
    provider = Provider(contract(), wrong,
                        review(question, resolution="clarification", fulfilled=False))
    published = []
    result = await NvidiaBrainClient(provider).complete_nvidia_only(
        role=AgentRole.PLANNER, messages=[
            {"role": "user", "content": "Dame dos opciones."},
            {"role": "assistant", "content": "Primera: React. Segunda: HTML y CSS."},
            {"role": "user", "content": "Me quedo con esa. ¿Cuál elegí?"}],
        review_dialogue=True, on_delta=published.append,
    )
    data = json.loads(provider.calls[2]["messages"][1]["content"])
    assert data["required_resolution"] == "clarification"
    assert data["review_errors"] == ["ambiguous_choice"]
    assert result.content == question and published == [question]


@pytest.mark.asyncio
async def test_clarification_status_cannot_publish_an_assertion_instead_of_a_question():
    failed = review("Elegiste React.", fulfilled=False, resolution="clarification")
    provider = Provider(contract(), failed, failed)
    with pytest.raises(IncompleteModelResponseError):
        await NvidiaBrainClient(provider).complete_nvidia_only(
            role=AgentRole.PLANNER, messages=MESSAGES, review_dialogue=True)


@pytest.mark.asyncio
async def test_unknown_personal_fact_can_deliver_a_honest_limitation():
    intent = answer(json.dumps({"objective": "Identificar el color de ORION",
                                "requires_context": True, "presentation": []}))
    text = "No tengo registrado el color que elegiste para ORION."
    provider = Provider(intent, review(text, fulfilled=False, resolution="limitation"))
    result = await NvidiaBrainClient(provider).complete_nvidia_only(
        role=AgentRole.PLANNER, messages=[*MESSAGES[:-1],
            {"role": "user", "content": "¿Qué color elegí para ORION?"}], review_dialogue=True)
    assert result.content == text


@pytest.mark.parametrize("text", ["Ahora explica solo el primer paso en una frase.",
                                     "Elijo la segunda.", "Amplía tu respuesta.",
                                     "¿Qué significa eso?", "Continue", "sí"])
def test_explicit_backward_references_retain_context(text):
    assert has_dialogue_reference(text)
    assert needs_dialogue_review([{"role": "user", "content": text}])


@pytest.mark.parametrize("text", [REQUEST, "Dime dos frutas.",
                                     'Traduce "el primer paso" al inglés.',
                                     "Explica qué es una API en un párrafo."])
def test_independent_topics_and_quoted_reference_examples_are_not_dependencies(text):
    assert not has_dialogue_reference(text)


def test_declared_paragraph_delivery_is_separated_from_substance_without_changing_original():
    assert content_request(REQUEST, ReplyConstraints.from_request(REQUEST)) == \
        "Explica cómo organizar tres tareas."
    assert "70 palabras" in REQUEST
    for text in ("Explica cómo redactar un párrafo en un solo párrafo de 70 palabras.",
                 "Explica cómo organizar ideas en un solo párrafo de 70 palabras.",
                 'Traduce "en un solo párrafo de 70 palabras".',
                 "¿Por qué la respuesta anterior tuvo 70 palabras?",
                 "Escribe un solo párrafo sobre tres tareas."):
        assert content_request(text, ReplyConstraints.from_request(text)) == text
    for text in ('Explica el significado de "en una frase".',
                 'Traduce «en una frase».', "Explica cómo redactar una frase."):
        assert content_request(text, ReplyConstraints.from_request(text)) == text


@pytest.mark.asyncio
async def test_separated_output_constraints_survive_empty_model_presentation():
    intent = answer(json.dumps({"objective": "Explicar cómo organizar tres tareas pendientes",
                                "requires_context": False, "presentation": []}))
    provider = Provider(intent, review("Organiza tus tareas."), review())
    result = await NvidiaBrainClient(provider).complete_nvidia_only(
        role=AgentRole.PLANNER, messages=MESSAGES, review_dialogue=True,
    )
    assert result.content == GOOD
    data = json.loads(provider.calls[1]["messages"][1]["content"])
    assert data["constraints"]["minimum_words"] == 56
    assert data["constraints"]["maximum_words"] == 84
    assert data["constraints"]["one_paragraph"] is True
    assert "word_count" in json.loads(provider.calls[2]["messages"][1]["content"])["review_errors"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_contract", [answer("not JSON"), answer('{}'), answer(
    '{"objective":"invented","requires_context":"false","presentation":[]}')])
async def test_invalid_or_coerced_contract_never_discards_context_or_publishes_draft(bad_contract):
    provider = Provider(bad_contract, bad_contract)
    published = []
    with pytest.raises(IncompleteModelResponseError, match="current dialogue objective"):
        await NvidiaBrainClient(provider).complete_nvidia_only(
            role=AgentRole.PLANNER, messages=MESSAGES, review_dialogue=True,
            on_delta=published.append,
        )
    assert published == [] and len(provider.calls) == 2


@pytest.mark.asyncio
async def test_contract_schema_repair_is_bounded_and_does_not_replay_invalid_output():
    provider = Provider(answer("UNTRUSTED_INVALID_OUTPUT"), contract(), review())
    published = []
    result = await NvidiaBrainClient(provider).complete_nvidia_only(
        role=AgentRole.PLANNER, messages=MESSAGES, review_dialogue=True,
        on_delta=published.append,
    )
    assert result.content == GOOD and published == [GOOD] and len(provider.calls) == 3
    repair = json.loads(provider.calls[1]["messages"][-1]["content"])
    assert repair["request"] == "Explica cómo organizar tres tareas."
    assert repair["validation_errors"] == ["invalid_contract_schema"]
    assert "UNTRUSTED_INVALID_OUTPUT" not in json.dumps(provider.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [asyncio.CancelledError(),
                                    IncompleteModelResponseError("cut off")])
@pytest.mark.parametrize("stage", ["contract", "review"])
async def test_cancelled_or_incomplete_review_never_leaks_draft(error, stage):
    provider = Provider(*([contract()] if stage == "review" else []), error)
    published = []
    with pytest.raises(type(error)):
        await NvidiaBrainClient(provider).complete_nvidia_only(
            role=AgentRole.PLANNER, messages=MESSAGES, review_dialogue=True,
            on_delta=published.append,
        )
    assert published == [] and len(provider.calls) == (2 if stage == "review" else 1)


@pytest.mark.asyncio
async def test_review_timeout_never_leaks_draft(monkeypatch):
    timeout = asyncio.timeout
    monkeypatch.setattr("aegis_core.brain.nvidia_dialogue_guard.asyncio.timeout",
                        lambda _seconds: timeout(0.01))

    class Stalled(Provider):
        async def complete(self, **kwargs):
            if self.calls:
                await asyncio.sleep(10)
            return await super().complete(**kwargs)

    published = []
    with pytest.raises(IncompleteModelResponseError, match="timed out"):
        await NvidiaBrainClient(Stalled(contract())).complete_nvidia_only(
            role=AgentRole.PLANNER, messages=MESSAGES, review_dialogue=True,
            on_delta=published.append,
        )
    assert published == []


@pytest.mark.asyncio
async def test_reviewer_cannot_propose_tools_and_tool_planning_is_not_reviewed():
    call = ToolCall(call_id="mail", tool_name="mail_search", arguments={},
                    requested_by=AgentRole.PLANNER)
    provider = Provider(answer("", finish="tool_calls", calls=(call,)))
    with pytest.raises(IncompleteModelResponseError, match="reviewer proposed a tool"):
        await NvidiaBrainClient(provider).complete_nvidia_only(
            role=AgentRole.PLANNER, messages=MESSAGES, review_dialogue=True,
        )
    provider = Provider(answer("", finish="tool_calls", calls=(call,)))
    result = await NvidiaBrainClient(provider).complete_nvidia_only(
        role=AgentRole.PLANNER, messages=MESSAGES, review_dialogue=True,
        extra_body={"tools": [{"type": "function"}], "tool_choice": "required"},
    )
    assert result.tool_calls == (call,) and len(provider.calls) == 1


@pytest.mark.asyncio
async def test_context_and_draft_credentials_are_removed_before_review():
    secret = "nvapi-" + "a" * 64
    provider = Provider(contract(), review())
    messages = [*MESSAGES[:-1], {"role": "user", "content": secret}, MESSAGES[-1]]
    await review_dialogue(provider, messages, answer(BAD + " " + secret))
    assert secret not in json.dumps(provider.calls)


def test_explicit_constraints_are_current_and_quoted_examples_are_not_instructions():
    assert not needs_dialogue_review([{"role": "user", "content": "Hola"}])
    assert needs_dialogue_review(MESSAGES)
    assert needs_dialogue_review([MESSAGES[-1]])
    constraints = ReplyConstraints.from_request(REQUEST)
    assert constraints.violations(GOOD) == ()
    assert "no_introduction" in constraints.violations("Aquí tienes: " + GOOD)
    assert "no_final_question" in constraints.violations(GOOD + " ¿Te sirve?")
    assert "one_paragraph" in constraints.violations(GOOD + "\nOtra línea.")
    quoted = ReplyConstraints.from_request('Explica qué significa "unas 70 palabras".')
    assert quoted.minimum_words is None
    assert ReplyConstraints.from_request("Responde en 10 palabras").minimum_words == 10
    maximum = ReplyConstraints.from_request("Responde en menos de 70 palabras")
    assert maximum.maximum_words == 69 and maximum.minimum_words is None
    minimum = ReplyConstraints.from_request("Responde en al menos 70 palabras")
    assert minimum.minimum_words == 70 and minimum.maximum_words is None
    word_range = ReplyConstraints.from_request("Responde entre 50 y 70 palabras")
    assert word_range.minimum_words == 50 and word_range.maximum_words == 70


@pytest.mark.asyncio
async def test_review_uses_configured_independent_nvidia_model_then_remote_fallback():
    attempted = []

    async def handler(request):
        payload = json.loads(request.content)
        attempted.append(payload)
        if len(attempted) == 1:
            return httpx.Response(410, json={"error": "unavailable"})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                              "message": {"content": review().content}}]})

    async with NvidiaNimClient(Settings(), lambda: "test", transport=httpx.MockTransport(handler),
                               ) as nim:
        nim._models[AgentRole.SYNTHESIZER] = ModelSpec(
            model_id="configured/primary", role=AgentRole.SYNTHESIZER, modalities=frozenset(),
            context_tokens=4096, reasoning=False, tool_calling=False,
            fallback_model_id="configured/reviewer",
        )
        original = dict(nim._models)
        result = await nim.complete_dialogue_review(
            draft_model_id="configured/primary", messages=MESSAGES,
            max_tokens=1024, temperature=0.0,
            extra_body={"response_format": {"type": "json_object"}},
        )
        assert nim._models == original
    assert [item["model"] for item in attempted] == ["configured/reviewer", "configured/primary"]
    assert all("tools" not in item for item in attempted)
    assert result.model_id == "configured/primary"


@pytest.mark.asyncio
@pytest.mark.parametrize("draft", [None, answer(BAD)])
async def test_fresh_intent_then_answer_uses_actual_prior_stage_model(draft):
    class Reviewer(Provider):
        async def complete_dialogue_review(self, **kwargs):
            return await self.complete(**kwargs)

        def completion_model_id(self, _role):
            return "configured/primary"

    provider = Reviewer(contract(), review())
    await review_dialogue(provider, MESSAGES, draft)
    assert provider.calls[0]["draft_model_id"] == (draft.model_id if draft else "")
    assert provider.calls[1]["draft_model_id"] == (
        draft.model_id if draft else "nvidia/draft")
    assert provider.calls[0]["review_role"] is AgentRole.SYNTHESIZER
    assert provider.calls[1]["review_role"] is AgentRole.CRITICAL_REASONER


@pytest.mark.asyncio
async def test_fresh_intent_then_answer_uses_separate_role_models_without_mutating_preferences():
    attempted = []

    async def handler(request):
        attempted.append(json.loads(request.content)["model"])
        content = contract().content if len(attempted) == 1 else review().content
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                              "message": {"content": content}}]})

    async with NvidiaNimClient(Settings(), lambda: "test", transport=httpx.MockTransport(handler),
                               ) as nim:
        nim._models[AgentRole.SYNTHESIZER] = ModelSpec(
            model_id="configured/intent", role=AgentRole.SYNTHESIZER, modalities=frozenset(),
            context_tokens=4096, reasoning=False, tool_calling=False,
            fallback_model_id="configured/answer",
        )
        nim._models[AgentRole.CRITICAL_REASONER] = ModelSpec(
            model_id="configured/reasoner", role=AgentRole.CRITICAL_REASONER,
            modalities=frozenset(), context_tokens=4096, reasoning=True, tool_calling=False,
            fallback_model_id="configured/reasoner-fallback",
        )
        original = dict(nim._models)
        published = []
        result = await NvidiaBrainClient(nim).complete_nvidia_only(
            role=AgentRole.PLANNER, messages=MESSAGES, review_dialogue=True,
            on_delta=published.append,
        )
        assert nim._models == original
    assert attempted == ["configured/intent", "configured/reasoner"]
    assert published == [GOOD] and result.content == GOOD


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["intent", "answer"])
async def test_rejected_real_stage_switches_configured_peer_once_before_publication(phase):
    attempted = []

    async def handler(request):
        attempted.append(json.loads(request.content)["model"])
        sequence = [answer("not JSON"), contract(), review()] if phase == "intent" \
            else [contract(), review("Ordena las tareas."), review()]
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                              "message": {"content": sequence[len(attempted) - 1].content}}]})

    async with NvidiaNimClient(Settings(), lambda: "test", transport=httpx.MockTransport(handler),
                               ) as nim:
        nim._models[AgentRole.SYNTHESIZER] = ModelSpec(
            model_id="configured/a", role=AgentRole.SYNTHESIZER, modalities=frozenset(),
            context_tokens=4096, reasoning=False, tool_calling=False,
            fallback_model_id="configured/b",
        )
        nim._models[AgentRole.CRITICAL_REASONER] = ModelSpec(
            model_id="configured/c", role=AgentRole.CRITICAL_REASONER,
            modalities=frozenset(), context_tokens=4096, reasoning=True, tool_calling=False,
            fallback_model_id="configured/d",
        )
        original = dict(nim._models)
        published = []
        result = await NvidiaBrainClient(nim).complete_nvidia_only(
            role=AgentRole.PLANNER, messages=MESSAGES, review_dialogue=True,
            on_delta=published.append,
        )
        assert nim._models == original
    assert attempted == (["configured/a", "configured/b", "configured/c"] if phase == "intent"
                         else ["configured/a", "configured/c", "configured/d"])
    assert published == [GOOD] and result.content == GOOD


@pytest.mark.asyncio
async def test_fresh_generation_uses_configured_primary_before_remote_fallback():
    attempted = []

    async def handler(request):
        attempted.append(json.loads(request.content)["model"])
        if len(attempted) == 1:
            return httpx.Response(410, json={"error": "unavailable"})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                              "message": {"content": review().content}}]})

    async with NvidiaNimClient(Settings(), lambda: "test", transport=httpx.MockTransport(handler),
                               ) as nim:
        nim._models[AgentRole.SYNTHESIZER] = ModelSpec(
            model_id="configured/primary", role=AgentRole.SYNTHESIZER, modalities=frozenset(),
            context_tokens=4096, reasoning=False, tool_calling=False,
            fallback_model_id="configured/fallback",
        )
        original = dict(nim._models)
        await nim.complete_dialogue_review(
            draft_model_id="", messages=MESSAGES, max_tokens=1024, temperature=0.0,
            extra_body={"response_format": {"type": "json_object"}},
        )
        assert nim._models == original
    assert attempted == ["configured/primary", "configured/fallback"]


@pytest.mark.asyncio
async def test_review_deadline_leaves_time_for_configured_remote_fallback():
    models = []

    async def handler(request):
        payload = json.loads(request.content)
        models.append(payload["model"])
        assert "timeout_seconds" not in payload
        if len(models) == 1:
            await asyncio.sleep(1)
            pytest.fail("Stalled reviewer was not bounded")
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                              "message": {"content": review().content}}]})

    async with NvidiaNimClient(Settings(), lambda: "test", transport=httpx.MockTransport(handler),
                               ) as nim:
        result = await nim.complete_dialogue_review(
            draft_model_id="nvidia/nemotron-3.5-lightning-30b-a3b", messages=MESSAGES,
            max_tokens=1024, temperature=0.0, timeout_seconds=0.06,
            extra_body={"response_format": {"type": "json_object"}},
        )
    assert models == ["openai/gpt-oss-20b", "nvidia/nemotron-3.5-lightning-30b-a3b"]
    assert result.model_id == models[-1]


def test_closed_synthesis_uses_low_effort_only_on_documented_gpt_oss_models():
    payload = {"messages": []}
    synth = NvidiaNimClient._attempt_payload(payload, AgentRole.SYNTHESIZER, "openai/gpt-oss-20b")
    assert synth["reasoning_effort"] == "low"
    planner = NvidiaNimClient._attempt_payload(payload, AgentRole.PLANNER, "openai/gpt-oss-20b")
    assert "reasoning_effort" not in planner
    custom = NvidiaNimClient._attempt_payload(payload, AgentRole.SYNTHESIZER, "configured/other")
    assert "reasoning_effort" not in custom
    assert payload == {"messages": []}


@pytest.mark.parametrize("limit,budget", [(4096, 512), (1024, 512), (128, 64)])
def test_closed_super_assembly_bounds_reasoning_without_changing_critical_role(limit, budget):
    model = "nvidia/nemotron-3-super-120b-a12b"
    payload = {"messages": [], "max_tokens": limit}
    closed = NvidiaNimClient._attempt_payload(payload, AgentRole.SYNTHESIZER, model)
    assert closed["chat_template_kwargs"] == {
        "enable_thinking": True, "low_effort": True, "reasoning_budget": budget,
    }
    critical = NvidiaNimClient._attempt_payload(payload, AgentRole.CRITICAL_REASONER, model)
    assert "chat_template_kwargs" not in critical
    assert payload == {"messages": [], "max_tokens": limit}


@pytest.mark.asyncio
async def test_review_provider_authentication_failure_never_publishes_old_answer():
    from aegis_core.brain.errors import RemoteAuthenticationError

    provider = Provider(NvidiaNimError("private error", status_code=401))
    published = []
    with pytest.raises(RemoteAuthenticationError):
        await NvidiaBrainClient(provider).complete_nvidia_only(
            role=AgentRole.PLANNER, messages=MESSAGES, review_dialogue=True,
            on_delta=published.append,
        )
    assert published == [] and len(provider.calls) == 1
