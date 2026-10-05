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
    needs_dialogue_review,
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
    provider = Provider(answer(BAD), contract(), review())
    published, events = [], []

    class Audit:
        def record_system_event(self, _request_id, **kwargs):
            events.append(kwargs)

    result = await NvidiaBrainClient(provider, audit_sink=Audit()).complete_nvidia_only(
        role=AgentRole.PLANNER, messages=MESSAGES, review_dialogue=True,
        on_delta=published.append,
    )
    assert result.content == GOOD and published == [GOOD]
    assert len(provider.calls) == 3
    assert provider.calls[1]["role"] is AgentRole.SYNTHESIZER
    assert "tools" not in provider.calls[1]["extra_body"]
    intent = json.loads(provider.calls[1]["messages"][-1]["content"])
    assert intent == {"request": "Explica cómo organizar tres tareas."}
    assert BAD not in json.dumps(provider.calls[1])
    sent = json.loads(provider.calls[2]["messages"][-1]["content"])
    assert sent["current_request"] == REQUEST
    assert sent["context"][-1] == {"role": "assistant", "content": BAD}
    assert events[0]["data"]["dialogue_review"] == "context_repaired"
    assert BAD not in json.dumps(events) and REQUEST not in json.dumps(events)


@pytest.mark.asyncio
async def test_repair_can_fix_word_count_without_publishing_either_rejected_draft():
    provider = Provider(answer(BAD), contract(),
                        review("Organiza las tareas por urgencia."), review())
    published = []
    result = await NvidiaBrainClient(provider).complete_nvidia_only(
        role=AgentRole.PLANNER, messages=MESSAGES, review_dialogue=True,
        on_delta=published.append,
    )
    assert result.content == GOOD and published == [GOOD]
    retry = json.loads(provider.calls[3]["messages"][-1]["content"])
    assert retry["review_errors"] == ["word_count"]
    assert retry["current_request"] == REQUEST


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [answer("invalid JSON"), review(fulfilled=False),
                                    review("Demasiado corto."),
                                    review(reason="untrusted-arbitrary-text")])
async def test_unverified_review_is_bounded_and_never_published(failed):
    provider = Provider(answer(BAD), contract(), failed, failed)
    published = []
    with pytest.raises(IncompleteModelResponseError, match="fulfill current request"):
        await NvidiaBrainClient(provider).complete_nvidia_only(
            role=AgentRole.PLANNER, messages=MESSAGES, review_dialogue=True,
            on_delta=published.append,
        )
    assert len(provider.calls) == 4 and published == []


@pytest.mark.asyncio
async def test_word_count_mentioned_as_topic_does_not_become_new_output_constraint():
    intent = answer(json.dumps({"objective": "Explicar la longitud de la respuesta anterior",
                                "requires_context": True, "presentation": []}))
    provider = Provider(answer(BAD), intent, review("Lo interpreté incorrectamente."))
    result = await NvidiaBrainClient(provider).complete_nvidia_only(
        role=AgentRole.PLANNER,
        messages=[*MESSAGES[:-1], {"role": "user", "content":
                                  "¿Por qué tu respuesta anterior tuvo 70 palabras?"}],
        review_dialogue=True,
    )
    assert result.content == "Lo interpreté incorrectamente."
    data = json.loads(provider.calls[2]["messages"][-1]["content"])
    assert data["constraints"]["minimum_words"] is None
    assert data["constraints"]["maximum_words"] is None


@pytest.mark.asyncio
async def test_independent_goal_does_not_receive_wrong_draft_or_accumulated_history():
    provider = Provider(answer(BAD), contract(requires_context=False), review())
    messages = [*MESSAGES[:-1], {"role": "user", "content": "Resumen histórico: " + BAD},
                {"role": "assistant", "content": BAD}, MESSAGES[-1]]
    result = await NvidiaBrainClient(provider).complete_nvidia_only(
        role=AgentRole.PLANNER, messages=messages, review_dialogue=True,
    )
    assert result.content == GOOD
    sent = json.loads(provider.calls[2]["messages"][-1]["content"])
    assert sent["current_request"] == "Explicar cómo organizar tres tareas pendientes"
    assert "context" not in sent and "proposed_response" not in sent
    assert sent["presentation"] == ["Un párrafo de unas 70 palabras",
                                     "Sin introducción ni preguntas al final"]
    assert BAD not in json.dumps(provider.calls[1:])
    assert messages[-2]["content"] == BAD  # No deletion or mutation of the saved history.


@pytest.mark.asyncio
async def test_generic_independence_verdict_cannot_erase_latest_step_reference():
    latest = "Primero enumera cada tarea con un número o letra; después asigna prioridades."
    request = "Ahora explica solo el primer paso en una frase."
    intent = answer(json.dumps({"objective": "Explicar el primer paso de un proceso",
                                "requires_context": False, "presentation": ["Una frase"]}))
    provider = Provider(answer("Identifica el objetivo."), intent,
                        review("Enumera las tres tareas con un número o una letra."))
    messages = [*MESSAGES[:-1], {"role": "user", "content": REQUEST},
                {"role": "assistant", "content": latest},
                {"role": "user", "content": request}]
    result = await NvidiaBrainClient(provider).complete_nvidia_only(
        role=AgentRole.PLANNER, messages=messages, review_dialogue=True,
    )
    assert result.content.startswith("Enumera")
    sent = json.loads(provider.calls[2]["messages"][-1]["content"])
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
    provider = Provider(answer(BAD), contract(), review("El paso citado consiste en enumerar."))
    await NvidiaBrainClient(provider).complete_nvidia_only(
        role=AgentRole.PLANNER, messages=messages, review_dialogue=True,
    )
    sent = json.loads(provider.calls[2]["messages"][-1]["content"])
    assert sent["context"] == messages[1:-1]
    assert "reference_scope" not in sent
    assert is_immediate_reference("Explica el primer paso de la conversación de ayer.", []) is False
    assert is_immediate_reference("¿Qué recuerdas de mis tareas?", []) is False


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


@pytest.mark.asyncio
async def test_separated_output_constraints_survive_empty_model_presentation():
    intent = answer(json.dumps({"objective": "Explicar cómo organizar tres tareas pendientes",
                                "requires_context": False, "presentation": []}))
    provider = Provider(answer(BAD), intent, review("Organiza tus tareas."), review())
    result = await NvidiaBrainClient(provider).complete_nvidia_only(
        role=AgentRole.PLANNER, messages=MESSAGES, review_dialogue=True,
    )
    assert result.content == GOOD
    data = json.loads(provider.calls[2]["messages"][-1]["content"])
    assert data["constraints"]["minimum_words"] == 56
    assert data["constraints"]["maximum_words"] == 84
    assert data["constraints"]["one_paragraph"] is True
    assert "word_count" in json.loads(provider.calls[3]["messages"][-1]["content"])["review_errors"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_contract", [answer("not JSON"), answer('{}'), answer(
    '{"objective":"invented","requires_context":"false","presentation":[]}')])
async def test_invalid_or_coerced_contract_never_discards_context_or_publishes_draft(bad_contract):
    provider = Provider(answer(BAD), bad_contract)
    published = []
    with pytest.raises(IncompleteModelResponseError, match="current dialogue objective"):
        await NvidiaBrainClient(provider).complete_nvidia_only(
            role=AgentRole.PLANNER, messages=MESSAGES, review_dialogue=True,
            on_delta=published.append,
        )
    assert published == [] and len(provider.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [asyncio.CancelledError(),
                                    IncompleteModelResponseError("cut off")])
@pytest.mark.parametrize("stage", ["contract", "review"])
async def test_cancelled_or_incomplete_review_never_leaks_draft(error, stage):
    provider = Provider(answer(BAD), *([contract()] if stage == "review" else []), error)
    published = []
    with pytest.raises(type(error)):
        await NvidiaBrainClient(provider).complete_nvidia_only(
            role=AgentRole.PLANNER, messages=MESSAGES, review_dialogue=True,
            on_delta=published.append,
        )
    assert published == [] and len(provider.calls) == (3 if stage == "review" else 2)


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
        await NvidiaBrainClient(Stalled(answer(BAD))).complete_nvidia_only(
            role=AgentRole.PLANNER, messages=MESSAGES, review_dialogue=True,
            on_delta=published.append,
        )
    assert published == []


@pytest.mark.asyncio
async def test_reviewer_cannot_propose_tools_and_tool_planning_is_not_reviewed():
    call = ToolCall(call_id="mail", tool_name="mail_search", arguments={},
                    requested_by=AgentRole.PLANNER)
    provider = Provider(answer(BAD), answer("", finish="tool_calls", calls=(call,)))
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
    provider = Provider(answer(BAD + " " + secret), contract(), review())
    messages = [*MESSAGES[:-1], {"role": "user", "content": secret}, MESSAGES[-1]]
    await NvidiaBrainClient(provider).complete_nvidia_only(
        role=AgentRole.PLANNER, messages=messages, review_dialogue=True,
    )
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


@pytest.mark.asyncio
async def test_review_provider_authentication_failure_never_publishes_old_answer():
    from aegis_core.brain.errors import RemoteAuthenticationError

    provider = Provider(answer(BAD), NvidiaNimError("private error", status_code=401))
    published = []
    with pytest.raises(RemoteAuthenticationError):
        await NvidiaBrainClient(provider).complete_nvidia_only(
            role=AgentRole.PLANNER, messages=MESSAGES, review_dialogue=True,
            on_delta=published.append,
        )
    assert published == [] and len(provider.calls) == 2
