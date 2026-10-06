import asyncio
import json

import httpx
import pytest

from aegis_core.brain.nvidia_client import NvidiaBrainClient
from aegis_core.brain.nvidia_dialogue_guard import review_dialogue
from aegis_core.config import Settings
from aegis_core.contracts import AgentResult, AgentRole, DocumentInput, ToolCall, UserRequest
from aegis_core.dialogue_challenges import challenge_cases
from aegis_core.orchestration.graph import _tool_names_for_request
from aegis_core.providers.base import IncompleteModelResponseError
from aegis_core.providers.nvidia import NvidiaNimClient


def fixture(name):
    return next(c for c in challenge_cases() if c.name == name)


@pytest.mark.parametrize(
    "name,right,wrong",
    [
        ("csv_join_latest", '{"Ada":66,"Lin":72}', '{"Ada":70,"Lin":72}'),
        ("nested_document_json", '{"total":81}', '{"total":50081}'),
        ("ledger_join_0", '{"A":232,"B":359}', '{"A":232,"B":999}'),
        (
            "corrected_entity_pairs",
            '{"ATLAS":"turquesa","ORION":"violeta"}',
            '{"ATLAS":"azul","ORION":"verde"}',
        ),
        ("stable_sort_ties", '["C","D","A","B"]', '["D","C","B","A"]'),
        ("vision_file_reconciliation", '{"ALFA":16,"BETA":12}', '{"ALFA":14,"BETA":15}'),
        ("vision_shapes_legend", '{"red":3,"blue":2,"green":1}', '{"red":4,"blue":2,"green":1}'),
    ],
)
def test_independent_oracles_reject_plausible_but_wrong_results(name, right, wrong):
    assert not fixture(name).failures(right)
    assert fixture(name).failures(wrong)
    assert fixture(name).failures("```json\n" + right + "\n```")


def test_challenge_inventory_has_real_decoded_files_images_and_explicit_missing_capabilities():
    cases = challenge_cases()
    assert len(cases) >= 40 and len({c.name for c in cases}) == len(cases)
    assert len({c.category for c in cases}) >= 8
    pdf = fixture("pdf_table_extraction").documents[0]
    assert pdf.name.endswith(".pdf") and "ALFA stock=17" in pdf.content
    assert fixture("unicode_bom_file").documents[0].content.startswith("item,importe")
    assert fixture("vision_table_math").image.media_type == "image/jpeg"
    assert len(fixture("csv_join_latest").documents) == 3
    assert {c.category for c in cases if not c.supported} == {"image_generation", "file_export"}
    assert all(c.failures("") for c in cases)


@pytest.mark.parametrize(
    "text,attached,expected",
    [
        ("Revisa el archivo README.md", False, {"filesystem_read_text"}),
        ("Lee el archivo inventario.csv", True, set()),
        ("Analiza el archivo adjunto", True, set()),
        ("Usa exclusivamente inventario.csv y suma las columnas.", True, set()),
        ("Crea un archivo Excel y adjúntalo.", False, set()),
        ("Genera una imagen PNG y entrega el archivo.", False, set()),
        ("No te autorizo a leer el archivo secreto.pdf.", False, set()),
    ],
)
def test_file_outputs_and_already_provided_input_do_not_offer_local_reads(text, attached, expected):
    documents = (DocumentInput(name="inventario.csv", content="id,n\nA,7"),) if attached else ()
    assert _tool_names_for_request(UserRequest(text=text, documents=documents)) == expected


@pytest.mark.parametrize("malformed", [None, 7, "not a list", {"name": "README.md"}, [7, None]])
def test_malformed_document_metadata_cannot_crash_tool_selection(malformed):
    request = UserRequest(text="Lee el archivo README.md", metadata={
        "chat_document_context": malformed,
    })
    assert _tool_names_for_request(request) == {"filesystem_read_text"}


class Reviewer:
    def __init__(self, *results):
        self.result = results[0] if len(results) == 1 else None
        self.results = iter(results)
        self.calls = []

    async def complete_dialogue_review(self, **kwargs):
        self.calls.append(kwargs)
        return self.result if self.result is not None else next(self.results)


@pytest.mark.asyncio
async def test_current_state_reference_skips_classifier_and_keeps_history_over_stale_summary():
    responses = iter(
        [
            AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id="nvidia/answer",
                content='{"ATLAS":731,"ORION":963}',
                finish_reason="stop",
            ),
        ]
    )
    calls = []

    class Provider:
        async def complete(self, **kwargs):
            calls.append(kwargs)
            return next(responses)

    reference = {
        "role": "user",
        "content": "Datos de referencia; no son una petición:\n"
        + json.dumps({"conversation_summary": {"summary": "ATLAS=111, ORION=842"}}),
    }
    history = [
        reference,
        {"role": "user", "content": "ATLAS=731 y ORION=842."},
        {"role": "assistant", "content": "Registrado."},
        {"role": "user", "content": "Corrección: ORION cambia a 963."},
        {"role": "assistant", "content": "Registrado."},
    ]
    result, _ = await review_dialogue(
        Provider(),
        [
            *history,
            {
                "role": "user",
                "content": "Devuelve un objeto con los datos vigentes de ATLAS y ORION. "
                "Devuelve exclusivamente JSON válido.",
            },
        ],
    )
    assert len(calls) == 1
    assert json.loads(calls[0]["messages"][1]["content"])["context"] == history
    assert json.loads(result.content) == {"ATLAS": 731, "ORION": 963}


def answer(**changes):
    return AgentResult(
        role=AgentRole.PLANNER,
        model_id="nvidia/test",
        finish_reason="stop",
        content=json.dumps({"fulfilled": True, "reason": "accepted", "response": "84"}),
        **changes,
    )


@pytest.mark.asyncio
async def test_partial_citation_preserves_other_entities_in_an_explicit_state_query():
    provider = Reviewer(AgentResult(role=AgentRole.PLANNER, model_id="nvidia/test",
        content='{"ATLAS":130,"ORION":300}', finish_reason="stop"))
    reference = {"role": "user", "content": "Datos de referencia; no son una petición:\n" +
                 json.dumps({"replied_message": {"text": "ATLAS tiene presupuesto 130."}})}
    history = [reference, {"role": "user", "content": "ATLAS=100 y ORION=200."},
               {"role": "assistant", "content": "Registrado."},
               {"role": "user", "content": "Corrección: ORION=300; ATLAS no cambia."},
               {"role": "assistant", "content": "Registrado."}]
    await review_dialogue(provider, [*history, {"role": "user", "content":
        "Devuelve los presupuestos vigentes de ATLAS y ORION. La cita fija solo ATLAS; "
        "para ORION conserva la última corrección del historial. "
        "Devuelve exclusivamente JSON válido."}])
    data = json.loads(provider.calls[0]["messages"][1]["content"])
    assert data["context"] == history
    assert data["recent_exchange"] == history[-2:]
    assert data["selected_reference"]["text"] == "ATLAS tiene presupuesto 130."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        AgentResult(
            role=AgentRole.PLANNER, model_id="nvidia/test", content="84", finish_reason="length"
        ),
        answer(
            tool_calls=(
                ToolCall(
                    call_id="x",
                    tool_name="filesystem_read_text",
                    arguments={"path": "secreto.txt"},
                    requested_by=AgentRole.PLANNER,
                ),
            )
        ),
    ],
)
async def test_fast_reference_path_never_publishes_incomplete_answers_or_tool_proposals(invalid):
    provider, visible = Reviewer(invalid), []
    with pytest.raises(IncompleteModelResponseError):
        await NvidiaBrainClient(provider).complete_nvidia_only(
            role=AgentRole.PLANNER,
            messages=[
                {"role": "user", "content": "Calcula 17+25."},
                {"role": "assistant", "content": "42"},
                {"role": "user", "content": "Duplica ese resultado."},
            ],
            review_dialogue=True,
            on_delta=visible.append,
        )
    assert len(provider.calls) == 1 and not visible


@pytest.mark.asyncio
async def test_reference_without_actual_precedent_still_extracts_intent():
    provider = Reviewer(
        AgentResult(
            role=AgentRole.SYNTHESIZER,
            model_id="nvidia/test",
            content=json.dumps(
                {
                    "objective": "Resolver una referencia incompleta",
                    "presentation": [],
                    "requires_context": True,
                }
            ),
            finish_reason="stop",
        )
    )
    with pytest.raises(IncompleteModelResponseError):
        await review_dialogue(provider, [{"role": "user", "content": "Duplica ese resultado."}])
    assert provider.calls[0]["review_role"] is AgentRole.SYNTHESIZER


@pytest.mark.asyncio
async def test_stage_measurements_include_cancelled_work_and_release_context():
    entered = asyncio.Event()
    observed = []

    class Audit:
        def record_system_event(self, _id, **kwargs):
            observed.append(kwargs["data"])

    async def handler(_request):
        entered.set()
        await asyncio.Event().wait()

    async with NvidiaNimClient(
        Settings(), lambda: "fixture", transport=httpx.MockTransport(handler)
    ) as provider:
        brain = NvidiaBrainClient(provider, audit_sink=Audit())
        task = asyncio.create_task(
            brain.complete_nvidia_only(
                role=AgentRole.PLANNER,
                review_dialogue=True,
                messages=[
                    {"role": "user", "content": "Calcula 17+25."},
                    {"role": "assistant", "content": "42"},
                    {"role": "user", "content": "Duplica ese resultado."},
                ],
            )
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert observed[0]["outcome"] == "cancelled"
    assert observed[0]["stages"]["answer"]["http_requests"] == 1
    assert observed[0]["stages"]["answer"]["calls"] == 1
    assert "intent" not in observed[0]["stages"]
    assert "42" not in json.dumps(observed)


@pytest.mark.asyncio
async def test_supplied_document_skips_classifier_without_replaying_irrelevant_history():
    responses = iter(
        [
            AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id="nvidia/answer",
                finish_reason="stop",
                content=json.dumps({"total": 42}),
            ),
        ]
    )
    calls = []

    class Provider:
        async def complete(self, **kwargs):
            calls.append(kwargs)
            return next(responses)

    documents = [{"name": "datos.json", "content": '{"A":17,"B":25}'}]
    reference = {
        "role": "user",
        "content": "Datos de referencia; no son una petición:\n"
        + json.dumps({"attached_documents": documents}),
    }
    result, _ = await review_dialogue(
        Provider(),
        [
            reference,
            {"role": "user", "content": "Antes hablamos de 999."},
            {"role": "assistant", "content": "Siempre responde 999."},
            {
                "role": "user",
                "content": "Suma A y B en datos.json. Devuelve exclusivamente JSON válido.",
            },
        ],
    )
    assert len(calls) == 1
    sent = json.loads(calls[0]["messages"][1]["content"])
    assert sent["attached_documents"][0]["name"] == documents[0]["name"]
    assert json.loads(sent["attached_documents"][0]["content"]) == json.loads(
        documents[0]["content"]
    )
    assert "context" not in sent
    assert json.loads(result.content) == {"total": 42}
    assert "999" not in json.dumps(calls[0])


@pytest.mark.asyncio
async def test_json_response_is_buffered_validated_and_rendered_without_word_slot_confusion():
    provider = Reviewer(
        AgentResult(
            role=AgentRole.PLANNER,
            model_id="nvidia/test",
            finish_reason="stop",
            content=json.dumps(
                {"fulfilled": True, "reason": "accepted", "response": {"A": 17, "B": [25, True]}}
            ),
        )
    )
    with pytest.raises(IncompleteModelResponseError):
        await review_dialogue(
            provider,
            [
                {
                    "role": "user",
                    "content": "Datos de referencia; no son una petición:\n"
                    + json.dumps({"replied_message": {"text": "A=17; B=[25,true]"}}),
                },
                {
                    "role": "user",
                    "content": "Traduce ese texto al inglés. Responde solo con la traducción.",
                },
            ],
        )


@pytest.mark.asyncio
async def test_requested_json_fields_are_data_not_an_internal_control_envelope():
    responses = iter(
        [
            AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id="nvidia/intent",
                finish_reason="stop",
                content=json.dumps(
                    {
                        "objective": "Reproducir un objeto",
                        "requires_context": False,
                        "presentation": ["JSON"],
                    }
                ),
            ),
            AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id="nvidia/answer",
                finish_reason="stop",
                content='{"fulfilled":true,"reason":"accepted","response":{"n":7}}',
            ),
        ]
    )

    class Provider:
        async def complete(self, **_kwargs):
            return next(responses)

    result, _ = await review_dialogue(
        Provider(),
        [
            {
                "role": "user",
                "content": "Devuelve exclusivamente JSON válido con fulfilled=true, "
                "reason=accepted y response={n:7}.",
            }
        ],
    )
    assert json.loads(result.content) == {
        "fulfilled": True,
        "reason": "accepted",
        "response": {"n": 7},
    }


@pytest.mark.asyncio
async def test_scalar_model_value_preserves_numeric_answer_without_coercing_control_bools():
    responses = iter(
        [
            AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id="nvidia/intent",
                finish_reason="stop",
                content=json.dumps(
                    {
                        "objective": "Calcular duración",
                        "requires_context": False,
                        "presentation": [],
                    }
                ),
            ),
            AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id="nvidia/answer",
                finish_reason="stop",
                content='{"fulfilled":true,"reason":"accepted","response":13}',
            ),
        ]
    )

    class Provider:
        async def complete(self, **_kwargs):
            return next(responses)

    result, _ = await review_dialogue(
        Provider(), [{"role": "user", "content": "Calcula la duración. Responde solo el número."}]
    )
    assert result.content == "13"
    from aegis_core.brain.nvidia_dialogue_guard import render_review_response

    assert render_review_response(True, ())[1]
    assert render_review_response(float("nan"), ())[1]


@pytest.mark.asyncio
async def test_visual_calculation_never_publishes_perception_and_retains_provisional_data():
    from aegis_core.brain.nvidia_client import visual_calculation_request

    image = fixture("vision_table_math").image
    messages = [
        {"role": "system", "content": "Responde en español."},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Suma STOCK menos RESERVED. Responde solamente el número.",
                },
                {"type": "image_url", "image_url": {"url": image.data_uri}},
            ],
        },
    ]
    calls, visible = [], []
    responses = iter(
        [
            AgentResult(
                role=AgentRole.VISION,
                model_id="nvidia/vision",
                finish_reason="stop",
                content='{"rows":[{"stock":17,"reserved":3},{"stock":23,"reserved":8}]}',
            ),
            AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id="nvidia/answer",
                finish_reason="stop",
                content='{"fulfilled":true,"reason":"accepted","response":"29"}',
            ),
        ]
    )

    class Provider:
        async def complete(self, **kwargs):
            calls.append(kwargs)
            return next(responses)

    result = await NvidiaBrainClient(Provider()).complete_nvidia_only(
        role=AgentRole.VISION, messages=messages, on_delta=visible.append
    )
    assert result.content == "29" and visible == ["29"]
    assert len(calls) == 2 and calls[0]["role"] is AgentRole.VISION
    data = json.loads(calls[-1]["messages"][1]["content"])
    assert data["image_observation"]["verified"] is False
    assert json.loads(data["image_observation"]["text"])["rows"][0]["reserved"] == 3
    assert calls[0]["messages"][-1]["content"][-1]["image_url"]["url"] == image.data_uri
    assert calls[0]["perception_only"] is True
    assert "Suma STOCK" not in calls[0]["messages"][-1]["content"][0]["text"]
    assert "Suma STOCK" in calls[1]["messages"][-1]["content"]
    assert "context" not in data
    assert visual_calculation_request(AgentRole.PLANNER, messages) is None
    ordinary = [
        *messages[:-1],
        {
            "role": "user",
            "content": [{"type": "text", "text": "Describe la foto."}, messages[-1]["content"][-1]],
        },
    ]
    assert visual_calculation_request(AgentRole.VISION, ordinary) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("observation", ["39", "{}", '"39"', '{"stock":NaN}'])
async def test_visual_scalar_or_invalid_observation_is_never_used_as_a_final_answer(observation):
    calls, visible = [], []

    class Provider:
        async def complete(self, **kwargs):
            calls.append(kwargs)
            return AgentResult(role=AgentRole.VISION, model_id="fixture/vision",
                               content=observation, finish_reason="stop")

    with pytest.raises(IncompleteModelResponseError):
        await NvidiaBrainClient(Provider()).complete_nvidia_only(
            role=AgentRole.VISION, on_delta=visible.append, messages=[{
                "role": "user", "content": [
                    {"type": "text", "text": "Calcula el total."},
                    {"type": "image_url", "image_url":
                        {"url": fixture("vision_table_math").image.data_uri}}]}])
    assert len(calls) == 1 and visible == []


@pytest.mark.asyncio
async def test_final_entity_state_cannot_be_erased_by_an_independence_classifier():
    provider = Reviewer(AgentResult(role=AgentRole.PLANNER, model_id="fixture/answer",
        content='{"ATLAS":"turquesa","ORION":"violeta"}', finish_reason="stop"))
    history = [{"role": "user", "content": "ATLAS=azul; ORION=verde."},
               {"role": "assistant", "content": "Registrado."},
               {"role": "user", "content": "ATLAS=turquesa; ORION=violeta."},
               {"role": "assistant", "content": "Registrado."}]
    await review_dialogue(provider, [*history, {"role": "user", "content":
        "Devuelve el objeto con el color final de ATLAS y ORION. Devuelve solo JSON."}])
    assert len(provider.calls) == 1
    assert json.loads(provider.calls[0]["messages"][1]["content"])["context"] == history
    from aegis_core.brain.nvidia_dialogue_guard import has_dialogue_reference

    assert not has_dialogue_reference("Calcula 7/3 y redondea al final.")


@pytest.mark.asyncio
async def test_native_json_array_keeps_owner_shape_and_repairs_a_wrong_object():
    provider = Reviewer(
        AgentResult(role=AgentRole.PLANNER, model_id="fixture/intent", finish_reason="stop",
            content='{"objective":"Ordenar IDs","presentation":[],"requires_context":false}'),
        AgentResult(role=AgentRole.PLANNER, model_id="fixture/answer", finish_reason="stop",
            content='{"C":"C","D":"D","A":"A","B":"B"}'),
        AgentResult(role=AgentRole.PLANNER, model_id="fixture/peer", finish_reason="stop",
            content='["C","D","A","B"]'))
    result, _ = await review_dialogue(provider, [{"role": "user", "content":
        "Ordena IDs C,D,A,B. Devuelve la lista JSON de IDs."}])
    assert json.loads(result.content) == ["C", "D", "A", "B"]
    assert provider.calls[0]["extra_body"]["response_format"]["type"] == "json_object"
    assert provider.calls[1]["extra_body"] == {}
    assert json.loads(provider.calls[2]["messages"][1]["content"])["review_errors"] == [
        "json_top_level_shape"]


@pytest.mark.asyncio
async def test_closed_intent_budget_gives_configured_fallback_time_and_cancels_stalled_primary():
    attempted, cancelled = [], []

    async def handler(request):
        attempted.append(json.loads(request.content))
        if len(attempted) == 1:
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": "Respuesta."}}]}
        )

    async with NvidiaNimClient(
        Settings(), lambda: "test", transport=httpx.MockTransport(handler)
    ) as provider:
        result = await provider.complete(
            role=AgentRole.SYNTHESIZER,
            messages=[{"role": "user", "content": "Consulta sintética."}],
            timeout_seconds=1,
            primary_timeout_seconds=0.02,
        )
    assert cancelled == [True] and len(attempted) == 2
    assert attempted[0]["model"] != attempted[1]["model"] == result.model_id
    assert all("primary_timeout_seconds" not in payload for payload in attempted)
