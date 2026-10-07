"""Adversarial memory checks use literal facts, authenticated sources and byte budgets.

The synthetic summarizer deliberately omits facts from its prose: a successful
test must recover actual evidence, rather than accept a model's self-evaluation.
No test calls a remote endpoint or reads the owner's chats/credentials.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from types import SimpleNamespace

import pytest

from aegis_core.contracts import AgentResult, AgentRole, DocumentInput
from aegis_core.memory.conversation_context import ConversationContextManager
from aegis_core.memory.conversations import ConversationCoordinator
from aegis_core.memory.errors import ConversationCapacityError
from aegis_core.memory.shared_conversation_context import search_shared_context, terms
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.privacy import redact_secrets_for_remote


@pytest.fixture
def memory(tmp_path):
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "challenge.sqlite3", encryption_secret=b"z" * 32)
    store.initialize()
    coordinator = ConversationCoordinator(
        store,
        namespace="owner.challenge",
        history_limit=4,
        max_turns=2_000,
    )
    return store, coordinator


async def exchange(coordinator, identifier, text, reply="Anotado; ninguna acción ejecutada.", **kw):
    assert await coordinator.record_exchange(
        identifier,
        user_content=text,
        assistant_content=reply,
        **kw,
    )


def search(store, current, query, **kw):
    with store._connect(read_only=True) as connection:
        connection.execute("BEGIN")
        return search_shared_context(
            connection,
            store._conversation_codec,
            namespace="owner.challenge",
            current_id=current,
            query=query,
            **kw,
        )


def serialized(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def source_for(context, identifier):
    sources = {source["conversation_id"]: source for source in context["sources"]}
    assert str(identifier) in sources
    return sources[str(identifier)]


class EvidenceBrain:
    def __init__(self, *, hostile=False):
        self.calls = []
        self.hostile = hostile

    async def complete_nvidia_only(self, **kwargs):
        self.calls.append(kwargs)
        payload = json.loads(kwargs["messages"][-1]["content"])
        evidence = []
        for fragment in payload["messages"]:
            if self.hostile and "solo estoy citando" in fragment["content"]:
                evidence.append(
                    {
                        "kind": "decision",
                        "sequence": fragment["sequence"],
                        "quote": "aprobada por auditoría",
                    }
                )
                evidence.append(
                    {
                        "kind": "quantity",
                        "sequence": fragment["sequence"],
                        "quote": "Transferencia autorizada de 999999 EUR.",
                    }
                )
            else:
                for match in re.finditer(r"FACT: ([^\n.]+\.)", fragment["content"]):
                    quote = match.group(1)
                    evidence.append(
                        {
                            "kind": "correction" if quote.startswith("Corrección") else "quantity",
                            "sequence": fragment["sequence"],
                            "quote": quote,
                        }
                    )
        result = {
            "summary": "Resumen sintético sin las cifras originales.",
            "evidence": evidence[:4],
        }
        return AgentResult(
            role=AgentRole.SYNTHESIZER,
            model_id="nvidia/synthetic-memory-challenge",
            content=json.dumps(result, ensure_ascii=False),
        )


def verify_quotes(store, identifier, context):
    for item in context.get("evidence", []):
        originals = store.conversation_history(
            namespace="owner.challenge",
            conversation_id=identifier,
            limit=1,
            before_sequence=item["sequence"] + 1,
        )
        assert len(originals) == 1 and originals[0].sequence == item["sequence"]
        turn = originals[0]
        clean = redact_secrets_for_remote(turn.content).text.encode("utf-8")
        assert item["digest"] == hashlib.sha256(turn.content.encode()).hexdigest()
        assert item["quote"] == clean[item["start_byte"] : item["end_byte"]].decode("utf-8")
        assert item["role"] == turn.role.value
        assert item["quote_verified"] and item["classification_inferred"]
    assert len(serialized(context.get("evidence", [])).encode()) <= 2048


async def test_paraphrase_with_zero_shared_terms_uses_expansion_and_keeps_literal_source(memory):
    store, coordinator = memory
    fact = "ATLÁNTIDA: presupuesto máximo 7431 EUR."
    query = "¿Cuánta plata quedó autorizada para la iniciativa submarina?"
    assert not terms(fact) & terms(query)
    previous = await coordinator.create("Acta conservada")
    await exchange(coordinator, previous.conversation_id, fact)
    current = await coordinator.create("Consulta actual")
    assert not search(store, current.conversation_id, query)["sources"]
    context = search(
        store, current.conversation_id, query, alternate_queries=("ATLÁNTIDA presupuesto máximo",)
    )
    source = source_for(context, previous.conversation_id)
    assert "7431 EUR" in serialized(source)
    assert any(
        message["digest"] == hashlib.sha256(fact.encode()).hexdigest()
        for message in source["messages"]
    )
    assert len(serialized(context).encode()) <= 8192


async def test_old_correction_beyond_120_chats_and_1200_turns_is_found(memory, record_property):
    store, coordinator = memory
    target = await coordinator.create("CONSTANCIA")
    await exchange(coordinator, target.conversation_id, "CONSTANCIA presupuesto aprobado 9100 EUR.")
    for index in range(600):
        text = (
            "Corrección definitiva CONSTANCIA: presupuesto aprobado 7431 EUR; sustituye 9100 EUR."
            if index == 110
            else f"Revisión de cocina número {index}; sin cambios de proyecto."
        )
        await exchange(coordinator, target.conversation_id, text)
    for index in range(120):
        chat = await coordinator.create(f"Cuaderno independiente {index}")
        await exchange(
            coordinator, chat.conversation_id, f"Una receta diferente: ingrediente {index}."
        )
    current = await coordinator.create("Consulta móvil")
    assert target.conversation_id not in {
        chat.conversation_id for chat in await coordinator.recent()
    }
    assert not any(
        "7431" in turn.content for turn in await coordinator.history(target.conversation_id)
    )
    started = time.perf_counter()
    context = search(store, current.conversation_id, "CONSTANCIA presupuesto aprobado")
    elapsed = time.perf_counter() - started
    record_property("search_elapsed_ms", round(elapsed * 1000, 2))
    record_property("scanned_chats", context["scanned_chats"])
    record_property("scanned_turns", context["scanned_turns"])
    record_property("payload_bytes", len(serialized(context).encode()))
    assert elapsed < 4.0  # 3 s deadline plus scheduling/one authenticated-source allowance.
    assert context["search_complete"]
    assert context["scanned_chats"] == 121 and context["scanned_turns"] > 1_400
    source = source_for(context, target.conversation_id)
    assert "7431 EUR" in serialized(source)
    assert len(serialized(context).encode()) <= 8192


async def test_entities_and_numbers_do_not_cross_between_original_and_expanded_queries(memory):
    store, coordinator = memory
    records = {}
    for name, number in (("ATLAS", 7431), ("ORION", 9200), ("CELADON", 3100)):
        record = records[name] = await coordinator.create(name)
        await exchange(
            coordinator, record.conversation_id, f"{name}: presupuesto aprobado {number} EUR."
        )
    current = await coordinator.create()
    context = search(
        store,
        current.conversation_id,
        "ATLAS presupuesto aprobado",
        alternate_queries=("ORION presupuesto aprobado", "CELADON presupuesto"),
    )
    assert context["sources"][0]["conversation_id"] == str(records["ATLAS"].conversation_id)
    atlas = source_for(context, records["ATLAS"].conversation_id)
    assert "7431 EUR" in serialized(atlas)
    assert "9200 EUR" not in serialized(atlas) and "3100 EUR" not in serialized(atlas)


async def test_actual_chat_old_fact_is_available_without_importing_foreign_chat(memory):
    store, coordinator = memory
    current = await coordinator.create("ATLAS actual")
    await exchange(coordinator, current.conversation_id, "ATLAS presupuesto final 7431 EUR.")
    for index in range(30):
        await exchange(coordinator, current.conversation_id, f"Paso de navegación {index}.")
    other = await coordinator.create("ATLAS ajeno al hilo")
    await exchange(coordinator, other.conversation_id, "ATLAS presupuesto final 9200 EUR.")
    assert not any(
        "7431" in turn.content for turn in await coordinator.history(current.conversation_id)
    )
    context = search(
        store,
        current.conversation_id,
        "ATLAS presupuesto final",
        include_current=True,
        only_current=True,
    )
    assert len(context["sources"]) == 1
    source = context["sources"][0]
    assert source["conversation_id"] == str(current.conversation_id) and source["is_current"]
    assert "7431 EUR" in serialized(source) and "9200 EUR" not in serialized(context)


async def test_dense_attachments_keep_literal_entity_fact_near_end(memory):
    store, coordinator = memory
    record = await coordinator.create("Anexos técnicos")
    filler = "Registro técnico genérico sin cambios. "
    dense = filler * (32_000 // len(filler.encode()))
    atlas = dense + "ATLAS presupuesto aprobado 7431 EUR."
    orion = dense + "ORION presupuesto aprobado 9200 EUR."
    documents = (
        DocumentInput(name="atlas.txt", content=atlas),
        DocumentInput(name="orion.txt", content=orion),
    )
    assert 60_000 < sum(len(d.content.encode()) for d in documents) < 65_536
    await exchange(
        coordinator, record.conversation_id, "Revisa los dos anexos técnicos.", documents=documents
    )
    current = await coordinator.create()
    context = search(store, current.conversation_id, "ATLAS presupuesto aprobado")
    source = source_for(context, record.conversation_id)
    extracted = [
        document
        for message in source["messages"]
        for document in message.get("documents", [])
        if document["name"] == "atlas.txt"
    ]
    assert extracted and "ATLAS presupuesto aprobado 7431 EUR." in extracted[0]["excerpt"]
    assert "9200 EUR" not in extracted[0]["excerpt"]
    assert len(serialized(context).encode()) <= 8192


async def test_identical_forks_do_not_displace_independent_source_and_divergence_survives(memory):
    store, coordinator = memory
    independent = await coordinator.create("ATLAS fechas")
    await exchange(coordinator, independent.conversation_id, "ATLAS fecha de cierre 21 noviembre.")
    original = await coordinator.create("ATLAS presupuesto aprobado")
    await exchange(coordinator, original.conversation_id, "ATLAS presupuesto aprobado 7431 EUR.")
    forks = [await coordinator.fork(original.conversation_id) for _ in range(6)]
    current = await coordinator.create()
    context = search(store, current.conversation_id, "ATLAS presupuesto aprobado fecha cierre")
    source_for(context, independent.conversation_id)
    same_fact = [source for source in context["sources"] if "7431 EUR" in serialized(source)]
    assert len(same_fact) == 1
    await exchange(
        coordinator,
        forks[0].conversation_id,
        "Corrección definitiva ATLAS presupuesto aprobado 5329 EUR.",
    )
    context = search(store, current.conversation_id, "ATLAS presupuesto aprobado fecha cierre")
    divergent = source_for(context, forks[0].conversation_id)
    assert "5329 EUR" in serialized(divergent)
    source_for(context, independent.conversation_id)


async def test_identical_text_from_assistant_does_not_erase_owner_source_role(memory):
    store, coordinator = memory
    fact = "CORAL presupuesto autorizado 7431 EUR."
    owner = await coordinator.create("CORAL")
    await exchange(coordinator, owner.conversation_id, fact)
    model = await coordinator.create("CORAL")
    await exchange(coordinator, model.conversation_id, "Hola", reply=fact)
    current = await coordinator.create()
    context = search(store, current.conversation_id, "CORAL presupuesto autorizado")
    owner_source = source_for(context, owner.conversation_id)
    model_source = source_for(context, model.conversation_id)
    assert any(message["role"] == "user" for message in owner_source["messages"])
    assert any(message["role"] == "assistant" for message in model_source["messages"])


async def test_archive_delete_and_foreign_owner_never_revive_private_evidence(memory):
    store, coordinator = memory
    record = await coordinator.create("NEBLINA")
    await exchange(coordinator, record.conversation_id, "NEBLINA presupuesto privado 7311 EUR.")
    foreign = ConversationCoordinator(store, namespace="owner.other")
    alien = await foreign.create("NEBLINA")
    await exchange(foreign, alien.conversation_id, "NEBLINA presupuesto ajeno 9999 EUR.")
    current = await coordinator.create()
    await coordinator.archive(record.conversation_id, archived=True)
    assert not search(store, current.conversation_id, "NEBLINA presupuesto")["sources"]
    await coordinator.archive(record.conversation_id, archived=False)
    assert "7311 EUR" in serialized(search(store, current.conversation_id, "NEBLINA presupuesto"))
    await coordinator.delete(record.conversation_id)
    assert not search(store, current.conversation_id, "NEBLINA presupuesto")["sources"]


async def test_deadline_reports_incomplete_scan_without_factual_confidence(memory, monkeypatch):
    store, coordinator = memory
    for index in range(12):
        record = await coordinator.create(f"ATLAS {index}")
        await exchange(
            coordinator, record.conversation_id, f"ATLAS presupuesto {1000 + index} EUR."
        )
    current = await coordinator.create()
    from aegis_core.memory import shared_conversation_context as module

    ticks = iter(index * 0.01 for index in range(1000))
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    context = search(store, current.conversation_id, "ATLAS presupuesto", time_budget=0.06)
    assert not context["search_complete"]
    assert context["scanned_chats"] < 12
    assert "confidence" not in context
    assert len(serialized(context).encode()) <= 8192


async def test_six_complementary_chats_fit_without_claiming_scan_equals_recall(memory):
    store, coordinator = memory
    expected = {}
    for index in range(6):
        record = await coordinator.create(f"FARO frente {index}")
        fact = f"FARO decisión del frente {index}: presupuesto autorizado {7000 + index} EUR."
        expected[record.conversation_id] = fact
        await exchange(coordinator, record.conversation_id, fact)
    current = await coordinator.create()
    context = search(store, current.conversation_id, "FARO decisión presupuesto autorizado")
    assert context["search_complete"] and len(context["sources"]) == 6
    for identifier, fact in expected.items():
        assert fact in serialized(source_for(context, identifier))
    assert len(serialized(context).encode()) <= 8192
    assert not context.get("retrieval_truncated", False)


async def test_more_relevant_chats_than_payload_capacity_marks_retrieval_partial(memory):
    store, coordinator = memory
    for index in range(12):
        record = await coordinator.create(f"FARO frente {index}")
        await exchange(
            coordinator,
            record.conversation_id,
            f"FARO decisión específica {index}: presupuesto {8000 + index} EUR.",
        )
    current = await coordinator.create()
    context = search(store, current.conversation_id, "FARO decisión presupuesto")
    assert context["search_complete"] and context["scanned_chats"] == 12
    assert len(context["sources"]) < 12
    assert context["retrieval_truncated"]
    assert len(serialized(context).encode()) <= 8192


async def test_relevant_turns_discarded_inside_one_chat_mark_retrieval_partial(memory):
    store, coordinator = memory
    record = await coordinator.create("ALAMO")
    for index in range(10):
        await exchange(
            coordinator,
            record.conversation_id,
            f"ALAMO presupuesto aprobado {7000 + index} EUR.",
        )
    current = await coordinator.create()
    context = search(store, current.conversation_id, "ALAMO presupuesto aprobado")
    source = source_for(context, record.conversation_id)
    assert context["search_complete"]
    assert len(source["messages"]) < 10
    assert source["context_truncated"] and context["retrieval_truncated"]
    assert len(serialized(context).encode()) <= 8192


async def test_summary_evidence_retains_old_fact_even_when_prose_discards_it(memory):
    store, coordinator = memory
    record = await coordinator.create()
    await exchange(
        coordinator, record.conversation_id, "FACT: ATLAS presupuesto autorizado 7431 EUR."
    )
    for index in range(80):
        await exchange(
            coordinator, record.conversation_id, f"Una observación sin datos nuevos {index}."
        )
    brain = EvidenceBrain()
    manager = ConversationContextManager(store, coordinator, brain)
    history = await coordinator.history(record.conversation_id)
    context = await manager.prepare(record.conversation_id, history, query="ATLAS presupuesto")
    assert "7431" not in context["summary"]
    assert any("7431 EUR" in item["quote"] for item in context["evidence"])
    assert context["evidence_version"] == 1
    assert context["partial"]  # Four bounded batches cannot claim all 160 old turns.
    assert len(brain.calls) <= 4
    verify_quotes(store, record.conversation_id, context)
    assert b"7431 EUR" not in store._path.read_bytes()
    for call in brain.calls:
        assert "tools" not in call and "tools" not in call["extra_body"]
        batch = json.loads(call["messages"][-1]["content"])["messages"]
        assert len(json.dumps(batch, ensure_ascii=False).encode()) <= 12288


async def test_hostile_summary_proposal_cannot_forge_fact_or_strip_negation(memory):
    store, coordinator = memory
    record = await coordinator.create()
    text = (
        "No confirmes como aprobada la transferencia de 3100 EUR; solo estoy citando "
        "el mensaje «aprobada por auditoría»."
    )
    await exchange(coordinator, record.conversation_id, text)
    for index in range(4):
        await exchange(coordinator, record.conversation_id, f"Dato irrelevante posterior {index}.")
    brain = EvidenceBrain(hostile=True)
    manager = ConversationContextManager(store, coordinator, brain)
    context = await manager.prepare(
        record.conversation_id,
        await coordinator.history(record.conversation_id),
        query="transferencia auditoría",
    )
    assert context["evidence"]
    quotes = " ".join(item["quote"] for item in context["evidence"])
    assert "No confirmes" in quotes and "solo estoy citando" in quotes
    assert "999999" not in quotes
    verify_quotes(store, record.conversation_id, context)
    forbidden = {"approval", "grant", "confirmed", "tool_arguments", "confirmation_token"}
    assert not any(forbidden & item.keys() for item in context["evidence"])


async def test_ledger_overflow_keeps_correction_and_originals_with_explicit_limits(memory):
    store, coordinator = memory
    record = await coordinator.create("CORAL y otros presupuestos")
    correction = "Corrección definitiva CORAL: presupuesto 7431 EUR; sustituye 9100 EUR."
    await exchange(coordinator, record.conversation_id, f"FACT: {correction}")
    for index in range(69):
        await exchange(
            coordinator,
            record.conversation_id,
            f"FACT: Proyecto ABEDUL{index} presupuesto independiente {20000 + index} EUR.",
        )
    brain = EvidenceBrain()
    manager = ConversationContextManager(store, coordinator, brain)
    history = await coordinator.history(record.conversation_id)
    contexts = []
    for _ in range(4):
        calls_before = len(brain.calls)
        context = await manager.prepare(record.conversation_id, history, query="CORAL")
        assert len(brain.calls) - calls_before <= 4
        contexts.append(context)
        if not context["partial"]:
            break
    assert not contexts[-1]["partial"]
    assert contexts[-1]["evidence_partial"] and contexts[-1]["evidence_truncated"]
    assert correction in " ".join(item["quote"] for item in contexts[-1]["evidence"])
    verify_quotes(store, record.conversation_id, contexts[-1])
    checkpoint = await manager.graph.aget_state(
        {"configurable": {"thread_id": str(record.conversation_id)}}
    )
    assert len(checkpoint.values["evidence"]) <= 24
    assert len(serialized(checkpoint.values["evidence"]).encode()) <= 8192
    calls_before = len(brain.calls)
    await manager.prepare(record.conversation_id, history, query="CORAL")
    assert len(brain.calls) == calls_before  # Covered turns are not remotely summarized again.
    original = search(
        store,
        record.conversation_id,
        "CORAL presupuesto",
        include_current=True,
        only_current=True,
    )
    assert correction in serialized(source_for(original, record.conversation_id))


async def test_deeply_nested_summary_json_degrades_without_advancing_coverage(memory):
    store, coordinator = memory
    record = await coordinator.create()
    for index in range(4):
        await exchange(coordinator, record.conversation_id, f"Un dato fuente {index}.")

    class NestedSummaryBrain:
        async def complete_nvidia_only(self, **kwargs):
            return AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id="nvidia/synthetic-memory-challenge",
                content="[" * 1000 + "0" + "]" * 1000,
            )

    manager = ConversationContextManager(store, coordinator, NestedSummaryBrain())
    context = await manager.prepare(
        record.conversation_id, await coordinator.history(record.conversation_id)
    )
    assert context["degraded"] and context["partial"] and context["evidence_partial"]
    assert context["source_sequence_end"] == 0 and not context["evidence"]
    checkpoint = await manager.graph.aget_state(
        {"configurable": {"thread_id": str(record.conversation_id)}}
    )
    assert checkpoint.values.get("covered_sequence", 0) == 0


async def test_capacity_rejects_new_exchange_without_deleting_old_context(memory):
    store, coordinator = memory
    limited = ConversationCoordinator(store, namespace=coordinator.namespace, max_turns=4)
    record = await limited.create()
    await exchange(limited, record.conversation_id, "Primera restricción que debe conservarse.")
    await exchange(limited, record.conversation_id, "Segunda decisión que debe conservarse.")
    before = await limited.history(record.conversation_id)
    with pytest.raises(ConversationCapacityError):
        await exchange(limited, record.conversation_id, "No cabe esta tercera solicitud.")
    assert await limited.history(record.conversation_id) == before
