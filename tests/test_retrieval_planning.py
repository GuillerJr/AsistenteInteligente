import asyncio
import json
from types import SimpleNamespace

import pytest

from aegis_core.brain.errors import RemoteProviderUnavailableError
from aegis_core.brain.nvidia_client import NvidiaBrainClient
from aegis_core.contracts import AgentResult, AgentRole
from aegis_core.memory.conversations import ConversationCoordinator
from aegis_core.memory.retrieval_planning import NvidiaRetrievalPlanner, should_expand_query
from aegis_core.memory.sqlite import SQLiteMemoryStore


class PlannerBrain:
    def __init__(self, result='{"queries":["presupuesto tope inversión"]}'):
        self.calls = []
        self.result = result

    async def complete_nvidia_only(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return SimpleNamespace(content=self.result)


async def test_structured_provider_budget_leaves_time_for_configured_remote_fallback():
    class Provider:
        def __init__(self):
            self.arguments = None

        async def complete(self, **kwargs):
            self.arguments = kwargs
            return AgentResult(role=kwargs["role"], model_id="nvidia/test", content="{}")
    provider = Provider()
    await NvidiaBrainClient(provider).complete_nvidia_only(
        role=AgentRole.SYNTHESIZER, messages=[{"role": "user", "content": "synthetic"}],
        extra_body={"response_format": {"type": "json_object"}}, structured_time_budget=4,
    )
    assert provider.arguments["timeout_seconds"] == 4
    assert provider.arguments["primary_timeout_seconds"] == 2


@pytest.mark.parametrize("query", ["sí", "continúa", "hola", "no", "hazlo",
                                 "Cuáles son mis últimos cinco correos sin leer",
                                 "¿Tengo ahorro activo?", "¿Qué es un volcán?"])
def test_does_not_add_remote_latency_to_continuations_live_actions_or_general_questions(query):
    assert not should_expand_query(query, {})


async def test_paraphrase_is_bounded_redacted_cached_and_preserves_entity():
    brain = PlannerBrain('{"queries":["ATLAS presupuesto", "ORION presupuesto", "tope"]}')
    planner = NvidiaRetrievalPlanner(brain)
    history = (SimpleNamespace(role=SimpleNamespace(value="user"),
                               content="Anterior: ATLAS, token=nvapi-" + "A" * 64),)
    query = "¿Cuál era mi margen anterior para ATLAS?"
    result = await planner.expand(query, history, {})
    assert result == ("ATLAS presupuesto",)
    assert brain.calls[0]["role"] == AgentRole.SYNTHESIZER
    assert brain.calls[0]["max_tokens"] == 256
    payload = json.loads(brain.calls[0]["messages"][-1]["content"])
    assert "nvapi-" not in json.dumps(payload)
    assert await planner.expand(query, history, {}) == result
    assert len(brain.calls) == 1
    assert await planner.expand(query, history,
                                {"query_coverage": .9}) == ()
    assert len(brain.calls) == 1


@pytest.mark.parametrize("response", [
    "not json", '{"queries":[]}', '{"queries":[1]}', '{"queries":"presupuesto"}',
    '{"queries":["presupuesto"],"tool":"approve"}',
    json.dumps({"queries": ["a" * 385]}), json.dumps({"queries": ["x\ny"]}),
    RemoteProviderUnavailableError("offline"),
])
async def test_malformed_suggestions_or_provider_failure_keep_local_fallback(response):
    assert await NvidiaRetrievalPlanner(PlannerBrain(response)).expand(
        "¿Cuánto podía gastar?", (), {},
    ) == ()


async def test_uppercase_emphasis_and_entity_boundaries_are_distinct():
    brain = PlannerBrain('{"queries":["presupuesto máximo"]}')
    assert await NvidiaRetrievalPlanner(brain).expand("¿CUÁNTO PODÍA GASTAR?", (), {})
    other = PlannerBrain('{"queries":["ATLAS2 presupuesto"]}')
    assert not await NvidiaRetrievalPlanner(other).expand("¿Cuánto tenía ATLAS?", (), {})


async def test_timeout_is_bounded_and_external_cancellation_propagates():
    class SlowBrain:
        async def complete_nvidia_only(self, **kwargs):
            await asyncio.sleep(60)
    planner = NvidiaRetrievalPlanner(SlowBrain(), timeout_seconds=.01)
    assert await planner.expand("¿Cuánto podía gastar?", (), {}) == ()
    task = asyncio.create_task(planner.expand("¿Qué elegí antes?", (), {}))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_semantic_reformulation_recovers_zero_overlap_source_and_scopes_desktop(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db", encryption_secret=b"x" * 32)
    store.initialize()
    coordinator = ConversationCoordinator(store, namespace="owner", history_limit=4)
    old = await coordinator.create("Notas")
    await coordinator.record_exchange(old.conversation_id,
        user_content="El presupuesto aprobado es 730 euros.", assistant_content="Anotado.")
    for index in range(8):
        await coordinator.record_exchange(old.conversation_id,
            user_content=f"Revisión visual del componente {index}.", assistant_content="Visto.")
    mobile = await coordinator.create("Telegram")
    query = "¿Cuánto podía gastar?"
    lexical = await coordinator.source_context(mobile.conversation_id, query, (), across_chats=True)
    assert not lexical["sources"]
    coordinator.retrieval_planner = NvidiaRetrievalPlanner(PlannerBrain())
    expanded = await coordinator.source_context(
        mobile.conversation_id, query, (), across_chats=True,
    )
    assert expanded["sources"][0]["conversation_id"] == str(old.conversation_id)
    assert "730 euros" in json.dumps(expanded, ensure_ascii=False)
    assert expanded["query_coverage"]["query_count"] > 1
    assert not (await coordinator.source_context(mobile.conversation_id, query, ()))["sources"]
    local = await coordinator.source_context(old.conversation_id, query,
                                             await coordinator.history(old.conversation_id))
    assert local["scope"] == "current_chat"
    assert local["sources"][0]["conversation_id"] == str(old.conversation_id)
    assert "730 euros" in json.dumps(local, ensure_ascii=False)
    assert all(message["sequence"] < 15 for message in local["sources"][0]["messages"])
    await coordinator.delete(old.conversation_id)
    assert not (await coordinator.source_context(mobile.conversation_id, query,
                                                 (), across_chats=True))["sources"]


async def test_planning_failure_does_not_hide_existing_evidence(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db", encryption_secret=b"x" * 32)
    store.initialize()
    coordinator = ConversationCoordinator(store, namespace="owner", history_limit=2)
    old = await coordinator.create("ATLAS")
    await coordinator.record_exchange(old.conversation_id,
        user_content="ATLAS mantiene el tope de 730 euros.", assistant_content="Anotado.")
    mobile = await coordinator.create("Telegram")
    query = "¿Qué margen habíamos elegido para ATLAS?"
    before = await coordinator.source_context(mobile.conversation_id, query, (), across_chats=True)
    coordinator.retrieval_planner = NvidiaRetrievalPlanner(PlannerBrain(
        RemoteProviderUnavailableError("unavailable"),
    ))
    after = await coordinator.source_context(mobile.conversation_id, query, (), across_chats=True)
    assert before == after
    assert "730" in json.dumps(after)


async def test_historical_amount_paraphrase_without_owner_pronoun_triggers_expansion():
    brain = PlannerBrain('{"queries":["ATLÁNTIDA presupuesto máximo"]}')
    result = await NvidiaRetrievalPlanner(brain).expand(
        "¿Cuánta plata quedó autorizada para la iniciativa submarina?", (), {},
    )
    assert result == ("ATLÁNTIDA presupuesto máximo",)
    assert len(brain.calls) == 1


async def test_deeply_nested_bounded_json_uses_local_fallback_instead_of_raising():
    response = "[" * 1000 + "0" + "]" * 1000
    assert len(response.encode()) < 2048
    planner = NvidiaRetrievalPlanner(PlannerBrain(response))
    assert await planner.expand("¿Qué decidimos antes?", (), {}) == ()


async def test_retrieval_uses_actual_byte_window_not_only_loaded_turn_count(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db", encryption_secret=b"x" * 32)
    store.initialize()
    coordinator = ConversationCoordinator(store, namespace="owner", history_limit=12,
                                           context_max_bytes=512)
    record = await coordinator.create("ATLAS")
    await coordinator.record_exchange(record.conversation_id,
        user_content="ATLAS tiene un límite de 730 euros.", assistant_content="Anotado.")
    await coordinator.record_exchange(record.conversation_id,
        user_content="Revisión de componentes. " * 100,
        assistant_content="Comentarios generales del diseño. " * 100)
    history = await coordinator.history(record.conversation_id)
    assert history[0].sequence == 1  # Loaded, but evicted from the real prompt by bytes.
    context = await coordinator.source_context(record.conversation_id, "Límite ATLAS", history)
    assert "730 euros" in json.dumps(context, ensure_ascii=False)
    assert context["sources"][0]["is_current"]
