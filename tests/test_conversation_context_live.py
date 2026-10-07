"""Opt-in NVIDIA memory probes: synthetic local conversations, no external tools."""
import asyncio
import os
from uuid import uuid4

import pytest

from aegis_core.brain.nvidia_client import NvidiaBrainClient
from aegis_core.config import Settings
from aegis_core.contracts import UserRequest
from aegis_core.job_execution import JobExecutionPipeline
from aegis_core.memory.conversation_context import ConversationContextManager
from aegis_core.memory.conversations import ConversationCoordinator
from aegis_core.memory.profile import OwnerProfile
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.orchestration.graph import build_swarm_graph
from aegis_core.providers.nvidia import NvidiaNimClient
from aegis_core.runtime_state import RuntimeSuspensionController
from aegis_core.secrets import MacOSKeychain

pytestmark = pytest.mark.skipif(
    os.environ.get("AEGIS_RUN_NVIDIA_CONTEXT_PROBE") != "1",
    reason="explicit opt-in required for NVIDIA synthetic memory",
)


@pytest.mark.asyncio
async def test_real_nvidia_telegram_recalls_corrected_context_from_other_chats(tmp_path):
    settings = Settings()
    key = await asyncio.to_thread(MacOSKeychain(
        settings.nvidia_keychain_service, settings.nvidia_keychain_account,
    ).get)
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3", encryption_secret=b"m" * 32)
    store.initialize()
    coordinator = ConversationCoordinator(store, namespace="user.default")
    first = await coordinator.create("Diseño de ATLAS")
    await coordinator.record_exchange(first.conversation_id,
        user_content="Para ATLAS elegí azul. No cambies la voz actual.",
        assistant_content="Diseñaremos ATLAS en azul, conservando la voz actual.")
    correction = await coordinator.create("Corrección de ATLAS")
    await coordinator.record_exchange(correction.conversation_id,
        user_content="Corrección para ATLAS: cambia azul por turquesa. Mantén la voz actual.",
        assistant_content="ATLAS usará turquesa y conservará la voz actual; es la nueva decisión.")
    telegram = await coordinator.create("Telegram")
    observed = []
    async def observe(invocation):
        observed.append(invocation)
    async with NvidiaNimClient(settings, lambda: key) as nim:
        graph = build_swarm_graph(NvidiaBrainClient(nim), inference_nvidia_only=True,
                                 dynamic_tool_names=lambda _: frozenset())
        pipeline = JobExecutionPipeline(graph, execution_timeout_seconds=90,
                                         conversations=coordinator)
        outcome = await pipeline.execute(UserRequest(
            text="¿Qué color decidí finalmente para ATLAS en el Mac y qué restricción de voz di? "
                 "Responde en una frase.", metadata={"source_channel": "telegram"}),
            conversation_id=telegram.conversation_id, stream_callback=lambda _: None,
            invocation_observer=observe)
        answer = outcome.result.casefold()
        assert "turquesa" in answer and "voz" in answer
        assert not observed[0].pending and not observed[0].tool_results
        assert len(await coordinator.history(first.conversation_id)) == 2
        assert len(await coordinator.history(correction.conversation_id)) == 2
        print("scenario=telegram_cross_chat_correction status=ok model=" +
              observed[0].model_id)


@pytest.mark.asyncio
async def test_real_nvidia_recalls_summary_after_a_new_context_manager(tmp_path):
    settings = Settings()
    key = await asyncio.to_thread(MacOSKeychain(
        settings.nvidia_keychain_service, settings.nvidia_keychain_account,
    ).get)
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3", encryption_secret=b"m" * 32)
    store.initialize()
    coordinator = ConversationCoordinator(store, namespace="user.default", history_limit=4)
    record = await coordinator.create()
    background = "Contexto secundario de diseño sin decisiones nuevas. " * 32
    await coordinator.record_exchange(record.conversation_id,
        user_content="Mi proyecto se llama ATLAS. La restricción es mantener la voz actual.\n"
                     + background + "\nEl presupuesto aprobado para ATLAS es 730 euros.\n"
                     + background,
        assistant_content="Trabajaremos en ATLAS manteniendo la voz actual.")
    for i in range(4):
        await coordinator.record_exchange(record.conversation_id,
            user_content=f"Revisemos el componente {i} sin ejecutar herramientas.",
            assistant_content="Podemos revisar su diseño; todavía no se ha ejecutado nada.")
    async with NvidiaNimClient(settings, lambda: key) as nim:
        brain = NvidiaBrainClient(nim)
        history = await coordinator.history(record.conversation_id)
        manager = ConversationContextManager(store, coordinator, brain)
        context = await manager.prepare(record.conversation_id, history)
        assert context["summary"], "NVIDIA did not produce a complete summary within its budget"
        restarted = ConversationContextManager(store, coordinator, brain)
        recovered = await restarted.prepare(record.conversation_id, history)
        assert recovered["summary"] == context["summary"]
        graph = build_swarm_graph(brain, inference_nvidia_only=True,
                                 dynamic_tool_names=lambda _: frozenset())
        state = await graph.ainvoke({"request": UserRequest(
            text="¿Qué nombre elegí para el proyecto, qué restricción de voz di y cuál "
                 "es su presupuesto aprobado en euros? Una frase.",
            metadata={"conversation_summary": recovered}), "conversation_history": history})
        answer = state["final_result"].content.casefold()
        assert "atlas" in answer and "voz" in answer and "730" in answer
        assert not state.get("tool_results") and not state.get("tool_authorizations")
        print("scenario=summary_restart status=ok model=" + state["final_result"].model_id)


@pytest.mark.asyncio
async def test_real_nvidia_uses_corrected_owner_preference_and_current_power(tmp_path):
    settings = Settings()
    key = await asyncio.to_thread(MacOSKeychain(
        settings.nvidia_keychain_service, settings.nvidia_keychain_account,
    ).get)
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3", encryption_secret=b"m" * 32)
    store.initialize()
    profile = OwnerProfile(store, namespace="user.default")
    await profile.observe(UserRequest(text="Me gusta el jazz."))
    await profile.observe(UserRequest(text="No me gusta el jazz."))
    runtime = RuntimeSuspensionController()
    await runtime.apply(source_id=uuid4(), sequence=1, cause="low_power_mode",
                        thermal_state="nominal", low_power_mode=True, power_source="battery")
    async with NvidiaNimClient(settings, lambda: key) as nim:
        graph = build_swarm_graph(NvidiaBrainClient(nim), inference_nvidia_only=True,
                                  owner_profile=profile, runtime_context_provider=runtime.snapshot,
                                  dynamic_tool_names=lambda _: frozenset())
        state = await graph.ainvoke({"request": UserRequest(
            text="¿Me gusta el jazz? ¿Qué ahorro está activo y qué función queda pausada? "
                 "Responde brevemente.", metadata={"source_channel": "telegram"})})
        answer = state["final_result"].content.casefold()
        assert "jazz" in answer and "no" in answer
        assert "consumo" in answer or "energía" in answer
        assert "escucha" in answer or "activación" in answer or "voz en segundo plano" in answer
        assert not state.get("tool_results") and not state.get("tool_authorizations")
        print("scenario=profile_power status=ok model=" + state["final_result"].model_id)
