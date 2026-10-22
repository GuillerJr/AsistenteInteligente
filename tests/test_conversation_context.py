import asyncio
import json
from itertools import pairwise
from uuid import uuid4

import pytest

from aegis_core.brain.errors import RemoteProviderUnavailableError
from aegis_core.brain.nvidia_client import NvidiaBrainClient
from aegis_core.contracts import AgentResult, AgentRole, UserRequest
from aegis_core.langgraph_compat import END, START, StateGraph
from aegis_core.memory.conversation_context import ContextState, ConversationContextManager
from aegis_core.memory.conversations import ConversationCoordinator
from aegis_core.memory.errors import MemoryNotFoundError
from aegis_core.memory.profile import OwnerProfile
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.orchestration.graph import build_nvidia_swarm_graph as build_swarm_graph
from aegis_core.runtime_state import RuntimeSuspensionController


class SummaryBrain:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    async def complete_nvidia_only(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RemoteProviderUnavailableError("offline")
        source = json.loads(kwargs["messages"][-1]["content"])
        summary = source["previous_summary"]
        for turn in source["messages"]:
            if turn["role"] == "user":
                summary += " " + turn["content"]
        return AgentResult(role=AgentRole.SYNTHESIZER, model_id="nvidia/test",
                           content=json.dumps({"summary": summary.strip().encode("utf-8")[:3000]
                                               .decode("utf-8", errors="ignore")}))


def components(tmp_path, *, history_limit=4, fail=False):
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3", encryption_secret=b"m" * 32)
    store.initialize()
    coordinator = ConversationCoordinator(store, namespace="user.default",
                                           history_limit=history_limit)
    brain = SummaryBrain(fail)
    manager = ConversationContextManager(store, coordinator, brain)
    coordinator.context_manager = manager
    return store, coordinator, manager, brain


async def fill(coordinator, identifier):
    for i in range(10):
        await coordinator.record_exchange(identifier, user_content=f"Proyecto átomo paso {i}",
                                          assistant_content=f"Propuesta {i}; sin ejecutar.")


@pytest.mark.asyncio
async def test_real_langgraph_checkpoints_survive_restart_and_stay_encrypted(tmp_path):
    store, coordinator, _manager, brain = components(tmp_path)
    record = await coordinator.create()
    await fill(coordinator, record.conversation_id)
    context = await coordinator.prepare_context(record.conversation_id,
                                               await coordinator.history(record.conversation_id))
    assert context["source_sequence_end"] == 16
    assert not context["partial"]
    assert "paso 0" in context["summary"] and "paso 7" in context["summary"]
    assert len(brain.calls) == 2
    assert all("tools" not in c["extra_body"] for c in brain.calls)
    assert b"Proyecto" not in store._path.read_bytes()
    assert b"summary" not in store._path.read_bytes()

    reopened, coordinator2, manager2, brain2 = components(tmp_path)
    context2 = await coordinator2.prepare_context(record.conversation_id,
                                                 await coordinator2.history(record.conversation_id))
    assert context2 == context
    assert not brain2.calls
    assert len(list(manager2.saver.list({"configurable": {
        "thread_id": str(record.conversation_id),
    }}))) <= 3
    # Atomic checkpoint writes must not replace a newer title or conversation exchange.
    assert reopened.get_conversation(namespace="user.default",
                                     conversation_id=record.conversation_id).task_checkpoint


@pytest.mark.asyncio
async def test_interrupted_context_node_resumes_without_an_action_executor(tmp_path):
    _, coordinator, manager, _ = components(tmp_path)
    record = await coordinator.create()
    await fill(coordinator, record.conversation_id)
    builder = StateGraph(ContextState)
    builder.add_node("summarize", manager._summarize)
    builder.add_edge(START, "summarize")
    builder.add_edge("summarize", END)
    graph = builder.compile(checkpointer=manager.saver, interrupt_before=["summarize"])
    config = {"configurable": {"thread_id": str(record.conversation_id)}}
    turn = (await coordinator.context_batch(record.conversation_id,
                                           after_sequence=0, before_sequence=2))[0]
    await graph.ainvoke({"batch": [{"sequence": 1, "role": "user",
                                    "content": turn.content, "digest": turn.content_sha256,
                                    "start_byte": 0, "end_byte": len(turn.content.encode()),
                                    "total_bytes": len(turn.content.encode())}],
                         "coverage_version": 2}, config)
    assert (await graph.aget_state(config)).next == ("summarize",)
    context = await manager.prepare(record.conversation_id,
                                    await coordinator.history(record.conversation_id))
    assert "Proyecto átomo paso 0" in context["summary"]
    assert context["source_sequence_end"] == 16


@pytest.mark.asyncio
async def test_unavailable_summary_keeps_truthful_coverage_and_recovers(tmp_path):
    _, coordinator, manager, brain = components(tmp_path, fail=True)
    record = await coordinator.create()
    await fill(coordinator, record.conversation_id)
    history = await coordinator.history(record.conversation_id)
    context = await manager.prepare(record.conversation_id, history)
    assert context["degraded"] and context["partial"]
    assert context["source_sequence_end"] == 0
    assert context["unsummarized_sources"][0]["sequence"] == 1
    brain.fail = False
    recovered = await manager.prepare(record.conversation_id, history)
    assert recovered["source_sequence_end"] == 16
    assert not recovered["partial"]


@pytest.mark.asyncio
async def test_fork_is_independent_and_deletion_removes_checkpoints(tmp_path):
    store, coordinator, manager, _ = components(tmp_path)
    source = await coordinator.create()
    unrelated = await coordinator.create()
    await fill(coordinator, source.conversation_id)
    context = await manager.prepare(source.conversation_id,
                                    await coordinator.history(source.conversation_id))
    assert await manager.prepare(unrelated.conversation_id, ()) == {}
    fork = await coordinator.fork(source.conversation_id)
    fork_context = await manager.prepare(fork.conversation_id,
                                         await coordinator.history(fork.conversation_id))
    assert fork_context == context
    await coordinator.record_exchange(fork.conversation_id, user_content="Cambio de objetivo",
                                      assistant_content="Nueva propuesta")
    await manager.prepare(fork.conversation_id, await coordinator.history(fork.conversation_id))
    assert (await manager.prepare(source.conversation_id,
                                  await coordinator.history(source.conversation_id))) == context
    await coordinator.archive(source.conversation_id, archived=True)
    with pytest.raises(MemoryNotFoundError):
        manager.saver.get_tuple({"configurable": {"thread_id": str(source.conversation_id)}})
    await coordinator.delete(fork.conversation_id)
    with pytest.raises(MemoryNotFoundError):
        store.conversation_context(namespace="user.default", conversation_id=fork.conversation_id)


class RecordingProvider:
    def __init__(self):
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return AgentResult(role=kwargs["role"], model_id="nvidia/test", content="Entendido.")

    async def complete_stream(self, **kwargs):
        return await self.complete(**kwargs)


@pytest.mark.asyncio
async def test_nvidia_receives_existing_profile_summary_and_actual_power_state(tmp_path):
    store, _, _, _ = components(tmp_path)
    profile = OwnerProfile(store, namespace="user.default")
    await profile.observe(UserRequest(text="Me gusta el jazz."))
    runtime = RuntimeSuspensionController()
    await runtime.apply(source_id=uuid4(), sequence=1, cause="low_power_mode",
                        thermal_state="nominal", low_power_mode=True, power_source="battery")
    provider = RecordingProvider()
    graph = build_swarm_graph(NvidiaBrainClient(provider), inference_nvidia_only=True,
                              owner_profile=profile, runtime_context_provider=runtime.snapshot)
    await graph.ainvoke({"request": UserRequest(text="¿Qué música prefiero?", metadata={
        "source_channel": "telegram", "conversation_summary": {
            "summary": "Hablamos de música", "source_sequence_end": 10,
        },
    })})
    assert provider.calls
    payload = json.dumps(provider.calls[-1]["messages"], ensure_ascii=False)
    assert "jazz" in payload
    assert "Hablamos de música" in payload
    assert '"low_power_mode": true' in payload.replace('\\"', '"')
    assert "telegram" in payload
    assert "local" not in provider.calls[-1]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_checkpoint_atomic_mutations_do_not_lose_concurrent_writes(tmp_path):
    store, coordinator, _, _ = components(tmp_path)
    record = await coordinator.create()
    def add(index):
        def transform(document):
            return {**document, str(index): index}
        store.mutate_conversation_context(namespace="user.default",
                                          conversation_id=record.conversation_id,
                                          transform=transform)
    await asyncio.gather(*(asyncio.to_thread(add, i) for i in range(20)))
    assert len(store.conversation_context(namespace="user.default",
                                          conversation_id=record.conversation_id)) == 20


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["executing", "observed"])
async def test_interrupted_operation_is_not_replayed_by_short_continuation(tmp_path, status):
    from aegis_core.job_execution import JobExecutionPipeline
    from aegis_core.memory.task_checkpoint import TaskRecovery

    store, coordinator, _, _ = components(tmp_path)
    record = await coordinator.create()
    recovery = TaskRecovery(request_id=str(uuid4()), request_text="Crea un borrador de correo",
                            tool_name="mcp_google_workspace__draft_gmail_message",
                            call_digest="a" * 64, status=status)
    await coordinator.record_recovery(record.conversation_id, recovery)
    provider = RecordingProvider()
    graph = build_swarm_graph(NvidiaBrainClient(provider), inference_nvidia_only=True)
    pipeline = JobExecutionPipeline(graph, execution_timeout_seconds=20,
                                     conversations=coordinator)
    invocations = []
    async def observe(invocation):
        invocations.append(invocation)
    outcome = await pipeline.execute(UserRequest(text="continúa"),
                                     conversation_id=record.conversation_id,
                                     stream_callback=lambda _: None, invocation_observer=observe)
    assert "Podría haberse ejecutado" in outcome.result
    assert not provider.calls
    assert not invocations[0].pending and not invocations[0].tool_results
    assert store.get_conversation(namespace="user.default",
                                   conversation_id=record.conversation_id).task_recovery == recovery


@pytest.mark.asyncio
async def test_voice_cannot_read_an_interrupted_task_without_verified_identity(tmp_path):
    from aegis_core.contracts import InputModality
    from aegis_core.job_execution import JobExecutionPipeline, JobExecutionRejectedError
    from aegis_core.memory.task_checkpoint import TaskRecovery

    _, coordinator, _, _ = components(tmp_path)
    record = await coordinator.create()
    await coordinator.record_recovery(record.conversation_id, TaskRecovery(
        request_id=str(uuid4()), request_text="Consulta privada", tool_name="mail",
        call_digest="a" * 64, status="awaiting_confirmation",
    ))
    graph = build_swarm_graph(NvidiaBrainClient(RecordingProvider()), inference_nvidia_only=True)
    pipeline = JobExecutionPipeline(graph, execution_timeout_seconds=20,
                                     conversations=coordinator)
    with pytest.raises(JobExecutionRejectedError, match="voice_conversation_owner_required"):
        await pipeline.execute(UserRequest(text="continúa", modalities={InputModality.AUDIO}),
                               conversation_id=record.conversation_id,
                               stream_callback=lambda _: None, invocation_observer=lambda _: None)


@pytest.mark.asyncio
async def test_unverified_voice_does_not_receive_shared_owner_memory(tmp_path):
    from aegis_core.contracts import InputModality

    store, _, _, _ = components(tmp_path)
    profile = OwnerProfile(store, namespace="user.default")
    await profile.observe(UserRequest(text="Me gusta el jazz."))
    provider = RecordingProvider()
    graph = build_swarm_graph(NvidiaBrainClient(provider), inference_nvidia_only=True,
                              owner_profile=profile)
    await graph.ainvoke({"request": UserRequest(text="Qué música prefiero",
                                               modalities={InputModality.AUDIO})})
    assert "jazz" not in json.dumps(provider.calls)


@pytest.mark.asyncio
async def test_summary_covers_messages_evicted_by_byte_budget_not_only_turn_count(tmp_path):
    _, coordinator, manager, _ = components(tmp_path, history_limit=12)
    coordinator.context_max_bytes = 512
    record = await coordinator.create()
    for i in range(6):
        await coordinator.record_exchange(record.conversation_id,
                                          user_content=f"Objetivo {i} " + "á" * 300,
                                          assistant_content="Propuesta " + "漢" * 300)
    history = await coordinator.history(record.conversation_id)
    assert len(history) == 12
    context = await manager.prepare(record.conversation_id, history)
    assert context["source_sequence_end"] >= 8
    assert "Objetivo 0" in context["summary"]
    assert not context["partial"]


class FactSummaryBrain(SummaryBrain):
    async def complete_nvidia_only(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RemoteProviderUnavailableError("offline")
        source = json.loads(kwargs["messages"][-1]["content"])
        text = " ".join(t["content"] for t in source["messages"])
        summary = source["previous_summary"] or "Sin nuevas decisiones."
        if "7531 EUR" in text:
            summary = "ATLAS: presupuesto 7531 EUR; conservar esta restricción."
        return AgentResult(role=AgentRole.SYNTHESIZER, model_id="nvidia/test",
                           content=json.dumps({"summary": summary}))


def fragments(brain):
    return [fragment for call in brain.calls
            for fragment in json.loads(call["messages"][-1]["content"])["messages"]]


@pytest.mark.asyncio
async def test_long_turn_middle_reaches_summary_without_any_source_bytes_omitted(tmp_path):
    _, coordinator, _, _ = components(tmp_path)
    brain = FactSummaryBrain()
    manager = ConversationContextManager(coordinator._store, coordinator, brain)
    record = await coordinator.create()
    content = "Introducción del proyecto. " * 130 + "ATLAS requiere 7531 EUR. " + "Contexto. " * 350
    await coordinator.record_exchange(record.conversation_id, user_content=content,
                                      assistant_content="No he modificado el presupuesto.")
    for i in range(4):
        await coordinator.record_exchange(record.conversation_id, user_content=f"Paso {i}",
                                          assistant_content="Pendiente.")
    context = await manager.prepare(record.conversation_id,
                                    await coordinator.history(record.conversation_id))
    original_fragments = [f for f in fragments(brain) if f["sequence"] == 1]
    assert len(original_fragments) > 1
    assert "".join(f["content"] for f in original_fragments) == content
    assert "7531 EUR" in context["summary"]
    assert not context["partial"] and context["source_sequence_end"] == 6
    assert all(f["end_byte"] - f["start_byte"] == len(f["content"].encode())
               for f in original_fragments)


@pytest.mark.asyncio
async def test_large_history_resumes_byte_cursor_after_restart_without_skipping_sources(tmp_path):
    store, coordinator, _, _ = components(tmp_path)
    brain = FactSummaryBrain()
    manager = ConversationContextManager(store, coordinator, brain)
    record = await coordinator.create()
    for i in range(7):
        await coordinator.record_exchange(record.conversation_id,
            user_content=f"Inicio {i}. " + "漢á🌍" * 1800 + f" Fin {i}.",
            assistant_content=f"Respuesta {i}. " + "á" * 5000)
    history = await coordinator.history(record.conversation_id)
    context = await manager.prepare(record.conversation_id, history)
    assert context["partial"] and context["partial_turn"]["processed_bytes"] > 0
    assert context["partial_turn"]["sequence"] == context["source_sequence_end"] + 1
    assert len(brain.calls) <= 4
    all_fragments = fragments(brain)
    # Resume using a fresh manager/store, not state retained in a Python object.
    _, coordinator2, _, _ = components(tmp_path)
    brain2 = FactSummaryBrain()
    restarted = ConversationContextManager(coordinator2._store, coordinator2, brain2)
    for _ in range(10):
        previous_calls = len(brain2.calls)
        context = await restarted.prepare(record.conversation_id, history)
        assert len(brain2.calls) - previous_calls <= 4
        if not context["partial"]:
            break
    assert not context["partial"] and context["source_sequence_end"] == 12
    all_fragments += fragments(brain2)
    originals = store.conversation_history(namespace="user.default",
                                           conversation_id=record.conversation_id, limit=14)
    for turn in originals[:12]:
        pieces = [f for f in all_fragments if f["sequence"] == turn.sequence]
        assert "".join(f["content"] for f in pieces) == turn.content
        assert pieces[0]["start_byte"] == 0
        assert all(a["end_byte"] == b["start_byte"] for a, b in pairwise(pieces))
        assert pieces[-1]["end_byte"] == len(turn.content.encode())
    raw = store._path.read_bytes()
    assert b"Inicio" not in raw and b"partial_offset" not in raw


@pytest.mark.asyncio
async def test_failed_partial_batch_retries_without_claiming_unprocessed_turns(tmp_path):
    store, coordinator, _, _ = components(tmp_path)
    record = await coordinator.create()
    for i in range(4):
        await coordinator.record_exchange(record.conversation_id,
            user_content=f"Inicio {i}. " + "z" * 15000,
            assistant_content="Respuesta.")
    brain = FactSummaryBrain()
    manager = ConversationContextManager(store, coordinator, brain)
    calls = 0
    original = brain.complete_nvidia_only
    async def fail_after_first(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            brain.fail = True
        return await original(**kwargs)
    brain.complete_nvidia_only = fail_after_first
    history = await coordinator.history(record.conversation_id)
    partial = await manager.prepare(record.conversation_id, history)
    assert partial["degraded"] and partial["source_sequence_end"] == 0
    assert 0 < partial["partial_turn"]["processed_bytes"] < 15020
    assert partial["unsummarized_sources"][0]["start_byte"] == (
        partial["partial_turn"]["processed_bytes"]
    )
    brain.complete_nvidia_only, brain.fail = original, False
    complete = await manager.prepare(record.conversation_id, history)
    assert not complete["partial"] and not complete["degraded"]
    assert complete["source_sequence_end"] == 6


@pytest.mark.asyncio
async def test_legacy_clipped_coverage_is_rebuilt_from_authenticated_history(tmp_path):
    store, coordinator, manager, _ = components(tmp_path)
    record = await coordinator.create()
    await coordinator.record_exchange(record.conversation_id,
        user_content="Información. " * 300 + "ATLAS requiere 7531 EUR.",
        assistant_content="Pendiente.")
    for i in range(3):
        await coordinator.record_exchange(record.conversation_id, user_content=f"Paso {i}",
                                          assistant_content="Pendiente.")
    config = {"configurable": {"thread_id": str(record.conversation_id)}}
    await manager.graph.aupdate_state(config, {"summary": "Resumen antiguo sin el presupuesto.",
                                              "covered_sequence": 4}, as_node="summarize")
    brain = FactSummaryBrain()
    reopened = ConversationContextManager(store, coordinator, brain)
    context = await reopened.prepare(record.conversation_id,
                                     await coordinator.history(record.conversation_id))
    assert brain.calls and fragments(brain)[0]["sequence"] == 1
    assert "7531 EUR" in context["summary"] and not context["partial"]
    assert "Resumen antiguo" not in json.dumps(brain.calls)


def test_fragments_redact_whole_credentials_before_utf8_boundaries():
    from datetime import UTC, datetime

    from aegis_core.memory.contracts import ConversationRole, ConversationTurn
    from aegis_core.memory.conversation_context import _coverage_after, _fragment_batch
    from aegis_core.privacy import redact_secrets_for_remote

    text = "漢" * 677 + " nvapi-abcdefghijklmnop1234567890 " + "🌍" * 900
    turn = ConversationTurn(conversation_id=uuid4(), sequence=1, role=ConversationRole.USER,
                            content=text, content_sha256=ConversationTurn.digest_content(text),
                            created_at=datetime.now(UTC))
    batch = _fragment_batch((turn,), {})
    joined = "".join(f["content"] for f in batch)
    assert joined == redact_secrets_for_remote(text).text
    assert "abcdefghijklmnop" not in json.dumps(batch)
    assert _coverage_after({}, batch)["covered_sequence"] == 1
    broken = [*batch]
    broken[1] = {**broken[1], "start_byte": broken[1]["start_byte"] + 1}
    with pytest.raises(ValueError, match="noncontiguous"):
        _coverage_after({}, broken)


def test_fragment_batch_budget_counts_escaped_json_and_preserves_all_control_characters():
    from datetime import UTC, datetime

    from aegis_core.memory.contracts import ConversationRole, ConversationTurn
    from aegis_core.memory.conversation_context import (
        _BATCH_BYTES,
        _coverage_after,
        _fragment_batch,
    )

    text = "\x00" * 12000
    turn = ConversationTurn(conversation_id=uuid4(), sequence=1, role=ConversationRole.USER,
                            content=text, content_sha256=ConversationTurn.digest_content(text),
                            created_at=datetime.now(UTC))
    state, pieces = {}, []
    for _ in range(10):
        batch = _fragment_batch((turn,), state)
        assert len(json.dumps(batch, ensure_ascii=False).encode()) <= _BATCH_BYTES
        pieces += batch
        state = _coverage_after(state, batch)
        if state["covered_sequence"]:
            break
    assert state["covered_sequence"] == 1
    assert "".join(piece["content"] for piece in pieces) == text


@pytest.mark.asyncio
async def test_summary_timeout_stays_partial_and_pending_context_recovers(tmp_path, monkeypatch):
    import aegis_core.memory.conversation_context as module

    store, coordinator, _, _ = components(tmp_path)
    record = await coordinator.create()
    await fill(coordinator, record.conversation_id)
    brain = FactSummaryBrain()
    original = brain.complete_nvidia_only
    cancelled = asyncio.Event()
    async def stalled(**kwargs):
        try:
            await asyncio.sleep(5)
        finally:
            cancelled.set()
        return await original(**kwargs)
    brain.complete_nvidia_only = stalled
    manager = ConversationContextManager(store, coordinator, brain)
    monkeypatch.setattr(module, "_PREPARATION_SECONDS", 0.05)
    history = await coordinator.history(record.conversation_id)
    context = await asyncio.wait_for(manager.prepare(record.conversation_id, history), timeout=1)
    assert cancelled.is_set()
    assert context["partial"] and context["source_sequence_end"] == 0
    assert context["unsummarized_sources"][0]["sequence"] == 1
    # The interrupted checkpoint can only resume a summary, never an action.
    brain.complete_nvidia_only = original
    monkeypatch.setattr(module, "_PREPARATION_SECONDS", 10)
    restarted = ConversationContextManager(store, coordinator, brain)
    recovered = await restarted.prepare(record.conversation_id, history)
    assert not recovered["partial"] and recovered["source_sequence_end"] == 16
    assert len(brain.calls) <= 4


@pytest.mark.asyncio
async def test_interrupted_fragment_node_resumes_from_true_byte_offset(tmp_path):
    from aegis_core.memory.conversation_context import _fragment_batch

    store, coordinator, _, _ = components(tmp_path)
    record = await coordinator.create()
    await coordinator.record_exchange(record.conversation_id,
        user_content="Contexto. " * 1500, assistant_content="Pendiente.")
    for i in range(3):
        await coordinator.record_exchange(record.conversation_id,
            user_content=f"Paso {i}.", assistant_content="Pendiente.")
    brain = FactSummaryBrain()
    manager = ConversationContextManager(store, coordinator, brain)
    config = {"configurable": {"thread_id": str(record.conversation_id)}}
    source = await coordinator.context_batch(record.conversation_id,
                                            after_sequence=0, before_sequence=5)
    state = await manager.graph.ainvoke({"batch": _fragment_batch(source, {}),
                                         "coverage_version": 2}, config)
    assert state["covered_sequence"] == 0 and state["partial_offset"] > 0
    builder = StateGraph(ContextState)
    builder.add_node("summarize", manager._summarize)
    builder.add_edge(START, "summarize")
    builder.add_edge("summarize", END)
    interrupted = builder.compile(checkpointer=manager.saver, interrupt_before=["summarize"])
    await interrupted.ainvoke({"batch": _fragment_batch(source, state)}, config)
    assert (await interrupted.aget_state(config)).next == ("summarize",)
    recovered = await ConversationContextManager(store, coordinator, brain).prepare(
        record.conversation_id, await coordinator.history(record.conversation_id)
    )
    assert not recovered["partial"] and recovered["source_sequence_end"] == 4
    chunks = [f for f in fragments(brain) if f["sequence"] == 1]
    assert "".join(f["content"] for f in chunks) == source[0].content


@pytest.mark.asyncio
async def test_escaped_summary_cannot_overflow_the_checkpoint_envelope(tmp_path):
    _, coordinator, manager, brain = components(tmp_path)
    record = await coordinator.create()
    await fill(coordinator, record.conversation_id)
    async def oversized(**kwargs):
        return AgentResult(role=AgentRole.SYNTHESIZER, model_id="nvidia/test",
                           content=json.dumps({"summary": "Dato " + "\x00" * 2500}))
    brain.complete_nvidia_only = oversized
    context = await manager.prepare(record.conversation_id,
                                    await coordinator.history(record.conversation_id))
    assert context["partial"] and context["degraded"]
    assert context["source_sequence_end"] == 0 and not context["summary"]


@pytest.mark.asyncio
async def test_restored_pending_intent_requires_a_new_exact_broker_approval(tmp_path):
    from test_nvidia_dialogue import Provider, answer

    from aegis_core.contracts import Capability, PolicyDecision, RiskLevel, ToolCall
    from aegis_core.job_execution import JobExecutionPipeline
    from aegis_core.mcp.client import McpArguments
    from aegis_core.memory.task_checkpoint import TaskRecovery
    from aegis_core.request_intent import google_request_tools
    from aegis_core.tools.broker import ToolDefinition
    from aegis_core.tools.defaults import build_default_tool_broker

    _, coordinator, _, _ = components(tmp_path)
    record = await coordinator.create()
    name = "mcp_google_workspace__search_gmail_messages"
    await coordinator.record_recovery(record.conversation_id, TaskRecovery(
        request_id=str(uuid4()), request_text="Revisa mis correos pendientes",
        tool_name=name, call_digest="a" * 64, status="awaiting_confirmation",
    ))
    definition = ToolDefinition(
        name=name, description="Search Gmail", arguments_model=McpArguments,
        capability=Capability.MAIL_READ, risk=RiskLevel.HIGH,
        allowed_roles=frozenset({AgentRole.PLANNER}), requires_confirmation=True,
        parameters_schema={"type": "object", "properties": {"query": {"type": "string"}},
                           "required": ["query"], "additionalProperties": False},
    )
    call = ToolCall(call_id="new-call", tool_name=name, arguments={"query": "is:unread"},
                    requested_by=AgentRole.PLANNER)
    provider = Provider([answer("", calls=(call,))])
    graph = build_swarm_graph(NvidiaBrainClient(provider), inference_nvidia_only=True,
                              tool_broker=build_default_tool_broker((definition,)),
                              dynamic_tool_names=lambda req: frozenset({name})
                              if google_request_tools(req.text) else frozenset())
    pipeline = JobExecutionPipeline(graph, execution_timeout_seconds=20,
                                     conversations=coordinator)
    async def observe(_):
        pass
    outcome = await pipeline.execute(UserRequest(text="continúa"),
                                     conversation_id=record.conversation_id,
                                     stream_callback=lambda _: None, invocation_observer=observe)
    _, authorization = outcome.pending_confirmation
    assert authorization.decision is PolicyDecision.REQUIRE_CONFIRMATION
    assert authorization.call_digest != "a" * 64
    assert not outcome.invocation.tool_results
    assert (await coordinator.recovery(record.conversation_id)).call_digest == call.digest()
    assert (await coordinator.recovery(record.conversation_id)).request_text == \
        "Revisa mis correos pendientes"


@pytest.mark.asyncio
async def test_tool_observation_is_durable_before_answer_generation(tmp_path):
    from aegis_core.contracts import ToolExecutionResult
    from aegis_core.job_execution import JobExecutionPipeline

    _, coordinator, _, _ = components(tmp_path)
    record = await coordinator.create()
    pipeline = JobExecutionPipeline(build_swarm_graph(NvidiaBrainClient(RecordingProvider()),
                                     inference_nvidia_only=True),
                                     execution_timeout_seconds=20, conversations=coordinator)
    await pipeline.record_recovery(record.conversation_id, request_id=uuid4(),
        request_text="Consulta mi agenda", tool_name="calendar_list_events",
        call_digest="a" * 64, status="observed", result=ToolExecutionResult(
            call_id="observed-call", tool_name="calendar_list_events", success=True,
            output='{"events": [], "refresh_token": "not-for-context"}',
            metadata={"verified": True},
        ))
    recovery = await coordinator.recovery(record.conversation_id)
    assert recovery.status == "observed"
    assert recovery.observation is not None
    assert "not-for-context" not in recovery.observation.excerpt
