import json
from uuid import uuid4

import pytest

from aegis_core.contracts import AgentResult, DocumentInput, InputModality, UserRequest
from aegis_core.job_execution import JobExecutionPipeline
from aegis_core.memory.conversations import ConversationCoordinator
from aegis_core.memory.errors import DecryptionAuthError
from aegis_core.memory.shared_conversation_context import search_shared_context
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.memory.task_checkpoint import TaskRecovery
from aegis_core.orchestration.graph import build_swarm_graph


def components(tmp_path):
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3", encryption_secret=b"m" * 32)
    store.initialize()
    return store, ConversationCoordinator(store, namespace="user.default")


async def exchange(
    coordinator, identifier, user, assistant="Anotado, sin ejecutar acciones.", **kw
):
    await coordinator.record_exchange(
        identifier, user_content=user, assistant_content=assistant, **kw
    )


@pytest.mark.asyncio
async def test_telegram_recalls_old_mac_chat_beyond_recent_list_and_history_window(tmp_path):
    store, coordinator = components(tmp_path)
    mac = await coordinator.create("ATLAS")
    await exchange(coordinator, mac.conversation_id, "ATLAS usa azul y mantiene la voz actual.")
    for i in range(30):
        await exchange(coordinator, mac.conversation_id, f"Revisión del componente {i}.")
    await exchange(
        coordinator, mac.conversation_id, "Corrección: ATLAS usa turquesa; la voz sigue igual."
    )
    for i in range(15):
        other = await coordinator.create(f"Otra conversación {i}")
        await exchange(coordinator, other.conversation_id, "Revisemos recetas de cocina.")
    telegram = await coordinator.create("Telegram")
    assert mac.conversation_id not in {r.conversation_id for r in await coordinator.recent()}
    context = await coordinator.shared_context(
        telegram.conversation_id, "¿Qué elegí para ATLAS?", ()
    )
    assert context["search_complete"] and context["scanned_chats"] == 16
    source = context["sources"][0]
    assert source["conversation_id"] == str(mac.conversation_id)
    serialized = json.dumps(source, ensure_ascii=False)
    assert "turquesa" in serialized and "voz actual" in serialized
    assert all(m["observed_at"] and m["digest"] for m in source["messages"])
    assert "recetas" not in json.dumps(context)
    assert b"turquesa" not in store._path.read_bytes()

    # Restart retrieves from existing encrypted sources, not an ephemeral index.
    _, reopened = components(tmp_path)
    assert await reopened.shared_context(telegram.conversation_id, "ATLAS", ()) == (
        await coordinator.shared_context(telegram.conversation_id, "ATLAS", ())
    )


@pytest.mark.asyncio
async def test_trash_deletion_namespace_and_current_chat_are_excluded(tmp_path):
    store, coordinator = components(tmp_path)
    current = await coordinator.create("Telegram")
    await exchange(coordinator, current.conversation_id, "Neblina actual")
    trash = await coordinator.create("Neblina en papelera")
    await exchange(coordinator, trash.conversation_id, "Neblina privada")
    foreign = ConversationCoordinator(store, namespace="user.other")
    alien = await foreign.create("Neblina ajena")
    await exchange(foreign, alien.conversation_id, "Neblina de otro propietario")
    assert (await coordinator.shared_context(current.conversation_id, "Neblina", ()))["sources"]
    await coordinator.archive(trash.conversation_id, archived=True)
    assert not (await coordinator.shared_context(current.conversation_id, "Neblina", ()))["sources"]
    await coordinator.archive(trash.conversation_id, archived=False)
    await coordinator.delete(trash.conversation_id)
    assert not (await coordinator.shared_context(current.conversation_id, "Neblina", ()))["sources"]


@pytest.mark.asyncio
async def test_long_attachment_keeps_matching_tail_and_bounded_context(tmp_path):
    _, coordinator = components(tmp_path)
    mac = await coordinator.create("Documento")
    for _ in range(4):
        await exchange(
            coordinator,
            mac.conversation_id,
            "Revisa el archivo adjunto.",
            documents=(
                DocumentInput(
                    name="informe.txt",
                    content="Texto general. " * 1800
                    + "El proyecto CELADON debe mantener la voz actual.",
                ),
            ),
        )
    telegram = await coordinator.create()
    context = await coordinator.shared_context(telegram.conversation_id, "CELADON", ())
    payload = json.dumps(context, ensure_ascii=False)
    assert "CELADON" in payload and "voz actual" in payload
    assert len(payload.encode()) <= 8_192
    assert context["sources"]
    assert any(d["excerpt_only"] for m in context["sources"][0]["messages"]
               for d in m.get("documents", []))


class RecordingBrain:
    def __init__(self):
        self.calls = []

    async def complete_nvidia_only(self, **kw):
        self.calls.append(kw["messages"])
        return AgentResult(role=kw["role"], model_id="nvidia/test", content="Respuesta de prueba.")


@pytest.mark.asyncio
async def test_pipeline_shares_context_automatically_without_switching_tasks_or_permissions(
    tmp_path,
):
    _, coordinator = components(tmp_path)
    mac = await coordinator.create("ATLAS")
    await exchange(coordinator, mac.conversation_id, "ATLAS debe conservar la voz actual.")
    recovery = TaskRecovery(
        request_id=str(uuid4()),
        request_text="Envía un correo ATLAS",
        tool_name="mail_send",
        call_digest="a" * 64,
        status="awaiting_confirmation",
    )
    await coordinator.record_recovery(mac.conversation_id, recovery)
    telegram = await coordinator.create("Telegram")
    brain = RecordingBrain()
    graph = build_swarm_graph(
        brain, inference_nvidia_only=True, dynamic_tool_names=lambda _: frozenset()
    )
    pipeline = JobExecutionPipeline(graph, execution_timeout_seconds=20, conversations=coordinator)
    invocations = []

    async def observe(invocation):
        invocations.append(invocation)

    await pipeline.execute(
        UserRequest(text="¿Qué restricción tiene ATLAS?", metadata={"source_channel": "telegram"}),
        conversation_id=telegram.conversation_id,
        stream_callback=lambda _: None,
        invocation_observer=observe,
    )
    payload = json.dumps(brain.calls, ensure_ascii=False)
    assert "voz actual" in payload and "shared_conversation_context" in payload
    assert "call_digest" not in payload and "mail_send" not in payload
    assert not invocations[-1].pending and not invocations[-1].tool_results
    assert await coordinator.recovery(telegram.conversation_id) is None
    assert await coordinator.recovery(mac.conversation_id) == recovery
    assert len(await coordinator.history(telegram.conversation_id)) == 2
    assert len(await coordinator.history(mac.conversation_id)) == 2
    brain.calls.clear()
    await pipeline.execute(
        UserRequest(text="sí", metadata={"source_channel": "telegram"}),
        conversation_id=telegram.conversation_id,
        stream_callback=lambda _: None,
        invocation_observer=observe,
    )
    assert str(mac.conversation_id) not in json.dumps(brain.calls, ensure_ascii=False)
    assert not invocations[-1].pending
    assert not (await coordinator.shared_context(
        telegram.conversation_id, "continúa", await coordinator.history(telegram.conversation_id)
    ))["sources"]


@pytest.mark.asyncio
async def test_unverified_voice_cannot_forge_telegram_to_receive_other_chat_context(tmp_path):
    _, coordinator = components(tmp_path)
    record = await coordinator.create()
    await exchange(coordinator, record.conversation_id, "ATLAS dato privado")
    brain = RecordingBrain()
    graph = build_swarm_graph(
        brain, inference_nvidia_only=True, dynamic_tool_names=lambda _: frozenset()
    )
    await graph.ainvoke(
        {
            "request": UserRequest(
                text="ATLAS",
                modalities={InputModality.AUDIO},
                metadata={
                    "source_channel": "telegram",
                    "shared_conversation_context": {
                        "sources": [{"excerpt": "ATLAS dato privado"}],
                    },
                },
            )
        }
    )
    assert "ATLAS dato privado" not in json.dumps(brain.calls)


@pytest.mark.asyncio
async def test_global_search_reports_partial_coverage_and_authenticates_sources(tmp_path):
    store, coordinator = components(tmp_path)
    mac = await coordinator.create()
    await exchange(coordinator, mac.conversation_id, "ATLAS privado")
    telegram = await coordinator.create()
    with store._connect(read_only=True) as connection:
        partial = search_shared_context(
            connection,
            store._conversation_codec,
            namespace="user.default",
            current_id=telegram.conversation_id,
            query="ATLAS",
            time_budget=0,
        )
    assert not partial["search_complete"] and not partial["sources"]
    with store._connect() as connection:
        connection.execute("UPDATE conversation_turns SET sequence = 99 WHERE sequence = 1")
    with pytest.raises(DecryptionAuthError):
        await coordinator.shared_context(telegram.conversation_id, "ATLAS", ())
