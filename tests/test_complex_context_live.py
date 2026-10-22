"""Opt-in NVIDIA stress through persisted jobs; only synthetic private temp stores."""

import asyncio
import os

import pytest

from aegis_core.brain.nvidia_client import NvidiaBrainClient
from aegis_core.config import Settings
from aegis_core.contracts import DocumentInput, UserRequest
from aegis_core.job_execution import JobExecutionPipeline
from aegis_core.memory.conversations import ConversationCoordinator
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.orchestration.graph import build_nvidia_swarm_graph as build_swarm_graph
from aegis_core.providers.nvidia import NvidiaNimClient
from aegis_core.secrets import MacOSKeychain

pytestmark = pytest.mark.skipif(
    os.environ.get("AEGIS_RUN_NVIDIA_COMPLEX_PROBE") != "1",
    reason="explicit opt-in for synthetic NVIDIA multi-turn stress",
)


@pytest.fixture
async def session(tmp_path):
    settings = Settings()
    key = await asyncio.to_thread(
        MacOSKeychain(settings.nvidia_keychain_service, settings.nvidia_keychain_account).get
    )
    assert key
    store = SQLiteMemoryStore(tmp_path / "synthetic.db", encryption_secret=b"s" * 32)
    store.initialize()
    coordinator = ConversationCoordinator(store, namespace="stress.synthetic", history_limit=6)
    async with NvidiaNimClient(settings, lambda: key) as provider:
        graph = build_swarm_graph(
            NvidiaBrainClient(provider),
            inference_nvidia_only=True,
            dynamic_tool_names=lambda _: frozenset(),
        )
        pipeline = JobExecutionPipeline(
            graph, execution_timeout_seconds=90, conversations=coordinator
        )
        yield coordinator, pipeline


async def ask(pipeline, cid, text, expected, *, documents=(), metadata=None):
    # Pace this opt-in sequence; unlike the gate, it measures persistence, not latency.
    await asyncio.sleep(5)
    observed = []

    async def observe(invocation):
        observed.append(invocation)

    outcome = await pipeline.execute(
        UserRequest(
            text=text,
            documents=documents,
            metadata={"source_channel": "telegram", **(metadata or {})},
        ),
        conversation_id=cid,
        stream_callback=lambda _: None,
        invocation_observer=observe,
    )
    assert outcome.result is not None and outcome.result.strip() == expected
    assert outcome.conversation_persisted
    assert len(observed) == 1 and not observed[0].pending and not observed[0].tool_results


@pytest.mark.asyncio
async def test_twelve_turn_chain_keeps_actual_results_through_history_eviction(session):
    coordinator, pipeline = session
    cid = (await coordinator.create("Cadena sintética")).conversation_id
    steps = [
        ("Calcula 17 + 25.", 42),
        ("Duplica ese resultado.", 84),
        ("Súmale 8 a ese resultado.", 92),
        ("Divide ese resultado entre 4.", 23),
        ("Triplica ese resultado.", 69),
        ("Multiplica ese resultado por 3.", 207),
        ("Súmale 8 a ese resultado.", 215),
        ("Duplica ese resultado.", 430),
        ("Divide ese resultado entre 5.", 86),
        ("Triplica ese resultado.", 258),
        ("Súmale 8 a ese resultado.", 266),
        ("Duplica ese resultado.", 532),
    ]
    for prompt, expected in steps:
        await ask(pipeline, cid, prompt + " Responde únicamente con el número.", str(expected))
    assert len(await coordinator.history(cid)) == 6


@pytest.mark.asyncio
async def test_document_correction_fork_and_restart_keep_distinct_sources(session):
    coordinator, pipeline = session
    original = (await coordinator.create("Archivo sintético")).conversation_id
    old = DocumentInput(name="inventario.json", content='{"ALFA":17,"BETA":23}')
    await ask(
        pipeline,
        original,
        "Suma ALFA y BETA del documento adjunto. Responde únicamente con el número.",
        "40",
        documents=(old,),
    )
    fork = (await coordinator.fork(original)).conversation_id
    new = DocumentInput(name="inventario.json", content='{"ALFA":11,"BETA":31}')
    await ask(
        pipeline,
        original,
        "Suma ALFA y BETA del nuevo documento adjunto. Responde únicamente con el número.",
        "42",
        documents=(new,),
    )
    # Reconstruct the coordinator and pipeline; no in-memory previous response can help.
    restarted = ConversationCoordinator(
        coordinator._store, namespace="stress.synthetic", history_limit=6
    )
    fresh = JobExecutionPipeline(
        pipeline._graph_invoker._graph, execution_timeout_seconds=90, conversations=restarted
    )
    await ask(
        fresh,
        original,
        "Suma ALFA y BETA del archivo inventario.json. Responde únicamente con el número.",
        "42",
    )
    await ask(
        fresh,
        fork,
        "Suma ALFA y BETA del archivo inventario.json de este fork. "
        "Responde únicamente con el número.",
        "40",
    )


@pytest.mark.asyncio
async def test_file_survives_dialogue_window_and_cited_number_overrides_it(session):
    coordinator, pipeline = session
    cid = (await coordinator.create("Documento persistente")).conversation_id
    await coordinator.record_exchange(
        cid,
        user_content="Adjunto presupuesto.",
        assistant_content="El presupuesto adjunto contiene ALFA=17 y BETA=25.",
        documents=(DocumentInput(name="presupuesto.csv", content="item,n\nALFA,17\nBETA,25"),),
    )
    for index in range(8):
        await coordinator.record_exchange(
            cid,
            user_content=f"Tema distinto {index}.",
            assistant_content="Ese tema no modifica el archivo.",
        )
    assert not any(t.documents for t in await coordinator.history(cid))
    await ask(
        pipeline,
        cid,
        "Suma los valores del archivo presupuesto.csv que adjunté. "
        "Responde únicamente con el número.",
        "42",
    )
    await ask(
        pipeline,
        cid,
        "Duplica ese resultado. Responde únicamente con el número.",
        "26",
        metadata={"telegram_message_reference": {"text": "13"}},
    )
