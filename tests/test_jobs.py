import asyncio
import base64
import json
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from aegis_core.capability_learning import (
    CapabilityLearningCoordinator,
    CapabilityLearningStatus,
    CapabilityLearningStore,
)
from aegis_core.contracts import (
    MAX_IMAGE_BYTES,
    AgentResult,
    AgentRole,
    ImageInput,
    InputModality,
    PolicyDecision,
    ToolAuthorization,
    ToolCall,
    ToolExecutionResult,
    UserRequest,
)
from aegis_core.conversation_quality import ConversationQualityFlag
from aegis_core.dialogue import REPAIR_CONTEXT_METADATA, DialogueMode
from aegis_core.evaluation import SQLiteEvaluationStore
from aegis_core.feedback import (
    FEEDBACK_STATUS_METADATA,
    FEEDBACK_TARGET_AVAILABLE,
    OwnerFeedback,
)
from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.jobs import (
    BrainTarget,
    JobCapacityError,
    JobConfirmationError,
    JobEvaluation,
    JobNotFoundError,
    JobStatus,
    SwarmIpcService,
    SwarmJobManager,
)
from aegis_core.memory.conversations import ConversationCoordinator
from aegis_core.memory.social import SocialMemory
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.orchestration.direct_actions import (
    PUBLIC_SOURCE_AVAILABLE,
    PUBLIC_SOURCE_MISSING,
    PUBLIC_SOURCE_STATUS_METADATA,
    PUBLIC_SOURCE_URL_METADATA,
)
from aegis_core.tools.audit import HashChainAuditLog
from aegis_core.tools.broker import PolicyContext, ToolBroker
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context
from aegis_core.tools.execution import ReadOnlyToolExecutor


class ImmediateGraph:
    def __init__(self) -> None:
        self.inputs: list[dict[str, Any]] = []

    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]:
        self.inputs.append(input)
        request = input["request"]
        return {
            "final_result": AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id="fake/synthesizer",
                content=f"respuesta:{request.text}",
            )
        }


class CapabilityResearchGraph:
    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]:
        del input
        return {
            "capability_gap": True,
            "tool_results": (
                ToolExecutionResult(
                    call_id="research-1",
                    tool_name="web_research",
                    success=True,
                    output=json.dumps(
                        {
                            "query": "sincronizar fotos macOS documentación oficial",
                            "results": [
                                {
                                    "url": "https://support.apple.com/guide/photos/welcome/mac",
                                    "title": "Manual de Fotos para Mac",
                                    "content": "Documentación pública para organizar una fototeca.",
                                }
                            ],
                        }
                    ),
                ),
            ),
            "final_result": AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id="apple/system-language-model",
                content="Encontré una ruta segura; todavía no instalé un ejecutor.",
            ),
        }


class PublicSourceReferenceGraph:
    def __init__(self) -> None:
        self.inputs: list[dict[str, Any]] = []

    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]:
        self.inputs.append(input)
        request = input["request"]
        if request.text == "Investiga NVIDIA NIM":
            return {
                "tool_results": (
                    ToolExecutionResult(
                        call_id="research-sources",
                        tool_name="web_research",
                        success=True,
                        output=json.dumps(
                            {
                                "query": "NVIDIA NIM",
                                "results": [
                                    {
                                        "url": "https://docs.nvidia.com/nim/guide",
                                        "title": "NIM Guide",
                                        "content": "Official guide",
                                    },
                                    {
                                        "url": "https://build.nvidia.com/models",
                                        "title": "NVIDIA Models",
                                        "content": "Model catalog",
                                    },
                                ],
                            }
                        ),
                        metadata={
                            "results": 2,
                            "source": "public_https",
                            "verified": True,
                        },
                    ),
                ),
                "final_result": AgentResult(
                    role=AgentRole.SYNTHESIZER,
                    model_id="local/deterministic-web-research",
                    content="Encontré dos fuentes verificadas.",
                ),
            }
        return {
            "final_result": AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id="local/test-source-context",
                content="seguimiento local",
            )
        }


class FailingGraph:
    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]:
        del input
        raise RuntimeError("secret provider details")


class LargeUnicodeGraph:
    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]:
        del input
        return {
            "final_result": AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id="fake/synthesizer",
                content="🛡️" * 20_000,
            )
        }


class BlockingGraph:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]:
        del input
        self.started.set()
        await self.release.wait()
        return {
            "final_result": AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id="fake/synthesizer",
                content="done",
            )
        }


class FeedbackBlockingGraph:
    def __init__(self) -> None:
        self.calls = 0
        self.inputs: list[dict[str, Any]] = []
        self.feedback_started = asyncio.Event()

    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]:
        self.inputs.append(input)
        self.calls += 1
        if self.calls == 2:
            self.feedback_started.set()
            await asyncio.Event().wait()
        return {
            "final_result": AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id="fake/synthesizer",
                content="respuesta",
            )
        }


class StreamingGraph:
    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]:
        publish = input["stream_callback"]
        publish("Primera frase. ")
        await asyncio.sleep(0)
        publish("Segunda frase.")
        return {
            "final_result": AgentResult(
                role=AgentRole.PLANNER,
                model_id="apple/system-language-model",
                content="Primera frase. Segunda frase.",
            )
        }


class RepetitiveGraph:
    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]:
        del input
        sentence = "Podemos revisar exactamente el mismo punto otra vez"
        return {
            "final_result": AgentResult(
                role=AgentRole.PLANNER,
                model_id="apple/system-language-model",
                content=f"{sentence}. {sentence}. {sentence}. {sentence}.",
            )
        }


class DeterministicGraph:
    def __init__(self, model_id: str) -> None:
        self.model_id = model_id

    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]:
        del input
        return {
            "final_result": AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id=self.model_id,
                content="resultado local",
            )
        }


class CompletedToolGraph:
    def __init__(self, *, verified: bool) -> None:
        self.call = ToolCall(
            call_id="call-computer",
            tool_name="computer_use",
            arguments={
                "objective": "Abrir documentación",
                "application_bundle_identifier": "com.apple.Safari",
                "max_steps": 4,
            },
            requested_by=AgentRole.PLANNER,
        )
        self.verified = verified

    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]:
        del input
        specialist = AgentResult(
            role=AgentRole.PLANNER,
            model_id="fake/planner",
            content="control visual",
            tool_calls=(self.call,),
        )
        return {
            "specialist_result": specialist,
            "tool_results": (
                ToolExecutionResult(
                    call_id=self.call.call_id,
                    tool_name=self.call.tool_name,
                    success=True,
                    output="{}",
                    metadata={"verified": self.verified},
                ),
            ),
            "final_result": AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id="fake/synthesizer",
                content="resultado",
            ),
        }


class PendingNetworkGraph:
    def __init__(self, broker: ToolBroker, context: PolicyContext) -> None:
        self.broker = broker
        self.context = context
        self.call = ToolCall(
            call_id="call-network",
            tool_name="network_discover_hosts",
            arguments={"target": "127.0.0.1", "ports": [443]},
            requested_by=AgentRole.CODE_SECURITY,
        )

    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]:
        del input
        specialist = AgentResult(
            role=AgentRole.CODE_SECURITY,
            model_id="fake/code-security",
            content="network analysis",
            tool_calls=(self.call,),
        )
        return {
            "specialist_result": specialist,
            "tool_authorizations": (self.broker.authorize(self.call, self.context),),
        }


class PendingTerminalGraph:
    def __init__(self, broker: ToolBroker, context: PolicyContext) -> None:
        self.broker = broker
        self.context = context
        self.call = ToolCall(
            call_id="call-terminal",
            tool_name="terminal_run_template",
            arguments={"template": "list_listeners"},
            requested_by=AgentRole.CODE_SECURITY,
        )

    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]:
        del input
        specialist = AgentResult(
            role=AgentRole.CODE_SECURITY,
            model_id="fake/code-security",
            content="terminal diagnosis",
            tool_calls=(self.call,),
        )
        return {
            "specialist_result": specialist,
            "tool_authorizations": (self.broker.authorize(self.call, self.context),),
        }


class PendingDeterministicActionGraph:
    def __init__(self, broker: ToolBroker, context: PolicyContext) -> None:
        self.broker = broker
        self.context = context
        self.call = ToolCall(
            call_id="local-application",
            tool_name="application_open",
            arguments={"bundle_identifier": "com.apple.Safari"},
            requested_by=AgentRole.PLANNER,
        )

    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]:
        del input
        specialist = AgentResult(
            role=AgentRole.PLANNER,
            model_id="local/deterministic-action",
            content="direct action",
            tool_calls=(self.call,),
        )
        return {
            "specialist_result": specialist,
            "tool_authorizations": (self.broker.authorize(self.call, self.context),),
        }


class PendingMailGraph:
    def __init__(self, broker: ToolBroker, context: PolicyContext) -> None:
        self.broker = broker
        self.context = context
        self.call = ToolCall(
            call_id="call-mail",
            tool_name="mail_send_message",
            arguments={
                "recipients": ["owner@example.com"],
                "subject": "Estado",
                "body": "Listo.",
            },
            requested_by=AgentRole.PLANNER,
        )

    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]:
        del input
        specialist = AgentResult(
            role=AgentRole.PLANNER,
            model_id="fake/planner",
            content="mail action",
            tool_calls=(self.call,),
        )
        return {
            "specialist_result": specialist,
            "tool_authorizations": (self.broker.authorize(self.call, self.context),),
        }


class PendingConfirmedActionGraph:
    def __init__(
        self,
        broker: ToolBroker,
        context: PolicyContext,
        call: ToolCall,
    ) -> None:
        self.broker = broker
        self.context = context
        self.call = call

    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]:
        del input
        specialist = AgentResult(
            role=AgentRole.PLANNER,
            model_id="local/deterministic-action",
            content="direct action",
            tool_calls=(self.call,),
        )
        return {
            "specialist_result": specialist,
            "tool_authorizations": (self.broker.authorize(self.call, self.context),),
        }


class BlockingPersistenceCoordinator(ConversationCoordinator):
    def __init__(self, store: SQLiteMemoryStore) -> None:
        super().__init__(store, namespace="user.default")
        self.persistence_started = asyncio.Event()
        self.release_persistence = asyncio.Event()

    async def record_exchange(
        self,
        conversation_id: UUID,
        *,
        user_content: str,
        assistant_content: str,
    ) -> bool:
        del conversation_id, user_content, assistant_content
        self.persistence_started.set()
        await self.release_persistence.wait()
        return True


async def _terminal(jobs: SwarmJobManager, job_id: UUID) -> Any:
    for _ in range(200):
        snapshot = await jobs.status(job_id)
        if snapshot.status in {
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }:
            return snapshot
        await asyncio.sleep(0.001)
    raise AssertionError("job did not reach a terminal state")


async def _awaiting_confirmation(jobs: SwarmJobManager, job_id: UUID) -> Any:
    for _ in range(200):
        snapshot = await jobs.status(job_id)
        if snapshot.status is JobStatus.AWAITING_CONFIRMATION:
            return snapshot
        await asyncio.sleep(0.001)
    raise AssertionError("job did not request confirmation")


def _approval_jobs(
    tmp_path: Path,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> tuple[SwarmJobManager, HashChainAuditLog]:
    broker = build_default_tool_broker()
    base = default_policy_context(tmp_path)
    store = OneTimeConfirmationStore()
    context = PolicyContext(
        workspace_root=tmp_path,
        network_scopes=base.network_scopes,
        confirmation_store=store,
    )
    audit = HashChainAuditLog(tmp_path / "audit.jsonl", clock=clock)
    jobs = SwarmJobManager(
        PendingNetworkGraph(broker, context),
        tool_broker=broker,
        policy_context=context,
        confirmation_store=store,
        tool_executor=ReadOnlyToolExecutor(tcp_connector=lambda *_: "closed"),
        audit_sink=audit,
        clock=clock,
        monotonic_clock=monotonic_clock,
    )
    return jobs, audit


@pytest.mark.asyncio
async def test_job_completes_with_bounded_public_result() -> None:
    jobs = SwarmJobManager(ImmediateGraph())
    queued = await jobs.submit(UserRequest(text="hola"))

    completed = await _terminal(jobs, queued.job_id)

    assert queued.status is JobStatus.QUEUED
    assert completed.status is JobStatus.COMPLETED
    assert completed.result == "respuesta:hola"
    assert completed.error_code is None
    await jobs.close()


@pytest.mark.asyncio
async def test_verified_public_sources_enable_only_bounded_ephemeral_follow_up() -> None:
    current = [datetime(2026, 8, 28, 12, 0, tzinfo=UTC)]
    graph = PublicSourceReferenceGraph()
    jobs = SwarmJobManager(graph, clock=lambda: current[0])

    research = await jobs.submit(UserRequest(text="Investiga NVIDIA NIM"))
    await _terminal(jobs, research.job_id)

    unrelated = await jobs.submit(UserRequest(text="Conversemos sobre arquitectura"))
    await _terminal(jobs, unrelated.job_id)
    unrelated_request = graph.inputs[-1]["request"]
    assert PUBLIC_SOURCE_STATUS_METADATA not in unrelated_request.metadata
    assert PUBLIC_SOURCE_URL_METADATA not in unrelated_request.metadata

    follow_up = await jobs.submit(
        UserRequest(
            text="Jarvis, abre la fuente dos",
            metadata={
                PUBLIC_SOURCE_STATUS_METADATA: PUBLIC_SOURCE_AVAILABLE,
                PUBLIC_SOURCE_URL_METADATA: "https://attacker.invalid/spoofed",
            },
        )
    )
    await _terminal(jobs, follow_up.job_id)
    follow_up_request = graph.inputs[-1]["request"]
    assert follow_up_request.metadata[PUBLIC_SOURCE_STATUS_METADATA] == (
        PUBLIC_SOURCE_AVAILABLE
    )
    assert follow_up_request.metadata[PUBLIC_SOURCE_URL_METADATA] == (
        "https://build.nvidia.com/models"
    )

    current[0] += timedelta(minutes=5, microseconds=1)
    expired = await jobs.submit(UserRequest(text="Abre la primera fuente"))
    await _terminal(jobs, expired.job_id)
    expired_request = graph.inputs[-1]["request"]
    assert expired_request.metadata[PUBLIC_SOURCE_STATUS_METADATA] == PUBLIC_SOURCE_MISSING
    assert PUBLIC_SOURCE_URL_METADATA not in expired_request.metadata
    await jobs.close()


@pytest.mark.asyncio
async def test_recent_public_sources_are_isolated_by_conversation(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3")
    store.initialize()
    conversations = ConversationCoordinator(store, namespace="user.default")
    first_conversation = (await conversations.create()).conversation_id
    second_conversation = (await conversations.create()).conversation_id
    graph = PublicSourceReferenceGraph()
    jobs = SwarmJobManager(graph, conversations=conversations)

    research = await jobs.submit(
        UserRequest(text="Investiga NVIDIA NIM"),
        conversation_id=first_conversation,
    )
    await _terminal(jobs, research.job_id)

    other_follow_up = await jobs.submit(
        UserRequest(text="Abre la primera fuente"),
        conversation_id=second_conversation,
    )
    await _terminal(jobs, other_follow_up.job_id)
    assert graph.inputs[-1]["request"].metadata[PUBLIC_SOURCE_STATUS_METADATA] == (
        PUBLIC_SOURCE_MISSING
    )

    original_follow_up = await jobs.submit(
        UserRequest(text="Abre la primera fuente"),
        conversation_id=first_conversation,
    )
    await _terminal(jobs, original_follow_up.job_id)
    assert graph.inputs[-1]["request"].metadata[PUBLIC_SOURCE_URL_METADATA] == (
        "https://docs.nvidia.com/nim/guide"
    )
    await jobs.close()


@pytest.mark.asyncio
async def test_successful_capability_research_is_retained_after_the_job(tmp_path: Path) -> None:
    store = CapabilityLearningStore(tmp_path / "capabilities")
    jobs = SwarmJobManager(
        CapabilityResearchGraph(),
        capability_learning=CapabilityLearningCoordinator(store),
    )
    queued = await jobs.submit(UserRequest(text="Sincroniza fotos con mi servidor"))

    completed = await _terminal(jobs, queued.job_id)

    assert completed.status is JobStatus.COMPLETED
    records = store.load_all()
    assert len(records) == 1
    assert records[0].status is CapabilityLearningStatus.RESEARCHED
    assert len(records[0].sources) == 1
    await jobs.close()


@pytest.mark.asyncio
async def test_job_exposes_bounded_stream_and_self_evaluation() -> None:
    jobs = SwarmJobManager(StreamingGraph())
    queued = await jobs.submit(UserRequest(text="hola"))

    completed = await _terminal(jobs, queued.job_id)
    metrics = await jobs.metrics()

    assert completed.partial_result == "Primera frase. Segunda frase."
    assert completed.stream_version == 2
    assert completed.evaluation is not None
    assert completed.evaluation.brain.value == "local"
    assert completed.evaluation.stream_chunks == 2
    assert completed.evaluation.outcome_verified is True
    assert completed.evaluation.voice_request is False
    assert completed.evaluation.owner_verified is False
    assert completed.evaluation.dialogue_mode is DialogueMode.TASK
    assert completed.evaluation.response_quality_score == 100
    assert completed.evaluation.response_quality_passed is True
    assert completed.evaluation.response_quality_flags == ()
    serialized_evaluation = completed.evaluation.model_dump_json()
    assert "hola" not in serialized_evaluation
    assert "Primera frase" not in serialized_evaluation
    assert metrics["jobs"] == 1
    assert metrics["completed"] == 1
    assert metrics["brain"]["local"] == 1
    assert metrics["latency_ms"]["first_partial_p95"] is not None
    assert metrics["quality"]["status"] == "insufficient_data"
    assert metrics["quality"]["minimum_samples"] == 20
    assert metrics["quality"]["targets"] == {
        "success_rate": 0.95,
        "first_partial_p95_ms": 2_000,
        "conversation_p95_ms": 8_000,
        "action_success_rate": 0.95,
        "owner_recognition_rate": 0.9,
        "response_quality_pass_rate": 0.95,
        "owner_feedback_helpful_rate": 0.8,
        "owner_feedback_minimum_samples": 5,
        "repair_recovery_rate": 0.8,
        "repair_recovery_minimum_samples": 3,
    }
    assert metrics["quality"]["observed"]["conversation_jobs"] == 1
    assert metrics["quality"]["observed"]["response_quality_pass_rate"] == 1.0
    await jobs.close()


@pytest.mark.asyncio
async def test_job_wait_wakes_on_terminal_change_without_polling() -> None:
    graph = BlockingGraph()
    jobs = SwarmJobManager(graph)
    queued = await jobs.submit(UserRequest(text="hola"))
    await graph.started.wait()

    waiting = asyncio.create_task(
        jobs.wait_for_change(
            queued.job_id,
            after_stream_version=0,
            timeout_seconds=1,
        )
    )
    await asyncio.sleep(0)
    assert waiting.done() is False

    graph.release.set()
    completed = await waiting

    assert completed.status is JobStatus.COMPLETED
    assert completed.result == "done"
    await jobs.close()


@pytest.mark.asyncio
async def test_job_metrics_survive_manager_restart(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    store = SQLiteEvaluationStore(private / "evaluations.sqlite3")
    store.initialize()
    first = SwarmJobManager(StreamingGraph(), evaluation_store=store)
    queued = await first.submit(UserRequest(text="hola"))
    await _terminal(first, queued.job_id)
    await first.close()

    second = SwarmJobManager(ImmediateGraph(), evaluation_store=store)
    metrics = await second.metrics()

    assert metrics["jobs"] == 1
    assert metrics["brain"]["local"] == 1
    await second.close()


@pytest.mark.asyncio
async def test_metrics_correct_legacy_local_fallback_brain_without_rewriting(
    tmp_path: Path,
) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    store = SQLiteEvaluationStore(private / "evaluations.sqlite3")
    store.initialize()
    evaluation = JobEvaluation(
        brain=BrainTarget.NVIDIA,
        model_id="local/privacy-fallback",
        total_latency_ms=4_000,
        first_partial_latency_ms=4_000,
        stream_chunks=1,
        succeeded=True,
        outcome_verified=True,
        voice_request=False,
        owner_verified=False,
    )
    job_id = uuid4()
    store.append(job_id, evaluation, datetime.now(UTC))
    jobs = SwarmJobManager(ImmediateGraph(), evaluation_store=store)

    metrics = await jobs.metrics()
    stored = store.load_recent()

    assert metrics["brain"]["deterministic"] == 1
    assert metrics["brain"]["nvidia"] == 0
    assert stored[0].evaluation.brain is BrainTarget.NVIDIA
    await jobs.close()


def test_legacy_evaluation_without_conversation_quality_remains_valid() -> None:
    evaluation = JobEvaluation.model_validate(
        {
            "brain": "local",
            "model_id": "apple/system-language-model",
            "total_latency_ms": 240,
            "first_partial_latency_ms": 80,
            "stream_chunks": 2,
            "tool_name": None,
            "succeeded": True,
            "outcome_verified": True,
            "voice_request": False,
            "owner_verified": False,
        }
    )

    assert evaluation.response_quality_score is None
    assert evaluation.response_quality_passed is None
    assert evaluation.response_quality_flags == ()
    assert evaluation.repair_attempt is False
    assert evaluation.wall_latency_ms is None
    assert evaluation.confirmation_wait_ms == 0
    assert evaluation.wall_first_partial_latency_ms is None


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {"wall_latency_ms": 6_000, "confirmation_wait_ms": 4_000},
            "do not match wall latency",
        ),
        (
            {"confirmation_wait_ms": 1},
            "legacy latency cannot contain confirmation wait",
        ),
        (
            {"wall_latency_ms": 1_000, "wall_first_partial_latency_ms": 500},
            "must be present together",
        ),
    ],
)
def test_evaluation_rejects_inconsistent_latency_contract(
    changes: dict[str, int], message: str
) -> None:
    payload: dict[str, object] = {
        "brain": "local",
        "total_latency_ms": 1_000,
        "stream_chunks": 0,
        "succeeded": True,
        "outcome_verified": True,
        "voice_request": False,
        "owner_verified": False,
    }
    payload.update(changes)

    with pytest.raises(ValueError, match=message):
        JobEvaluation.model_validate(payload)


@pytest.mark.asyncio
async def test_confirmation_wait_is_excluded_from_active_latency(tmp_path: Path) -> None:
    ticks = iter((0.0, 1.0, 31.0, 32.0, 32.0))
    jobs, _ = _approval_jobs(tmp_path, monotonic_clock=lambda: next(ticks))
    queued = await jobs.submit(UserRequest(text="Escanea loopback"))
    pending = await _awaiting_confirmation(jobs, queued.job_id)
    assert pending.confirmation is not None

    await jobs.approve(queued.job_id, pending.confirmation.call_digest)
    completed = await _terminal(jobs, queued.job_id)
    metrics = await jobs.metrics()

    assert completed.evaluation is not None
    assert completed.evaluation.total_latency_ms == 2_000
    assert completed.evaluation.wall_latency_ms == 32_000
    assert completed.evaluation.confirmation_wait_ms == 30_000
    assert completed.evaluation.first_partial_latency_ms == 2_000
    assert completed.evaluation.wall_first_partial_latency_ms == 32_000
    assert metrics["latency_ms"]["p95"] == 2_000
    assert metrics["latency_ms"]["active_p95"] == 2_000
    assert metrics["latency_ms"]["wall_p95"] == 32_000
    assert metrics["latency_ms"]["confirmation_wait_p95"] == 30_000
    assert metrics["latency_ms"]["first_partial_p95"] == 2_000
    assert metrics["latency_ms"]["active_first_partial_p95"] == 2_000
    assert metrics["latency_ms"]["wall_first_partial_p95"] == 32_000
    await jobs.close()


@pytest.mark.asyncio
async def test_successful_job_records_explicit_social_context(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3")
    store.initialize()
    social = SocialMemory(store, namespace="user.default")
    jobs = SwarmJobManager(ImmediateGraph(), social_memory=social)

    queued = await jobs.submit(UserRequest(text="Mi proyecto actual es Jarvis."))
    await _terminal(jobs, queued.job_id)

    recalled = await social.recall()
    assert [hit.excerpt for hit in recalled] == ["Tema activo del propietario: Jarvis."]
    await jobs.close()


@pytest.mark.parametrize(
    "model_id",
    [
        "local/deterministic-action",
        "local/deterministic-calculator",
        "local/deterministic-clock",
        "local/deterministic-empty-read",
        "local/deterministic-power",
        "local/deterministic-read-error",
        "local/deterministic-runtime",
        "local/deterministic-storage",
        "local/degraded-quorum",
        "local/privacy-fallback",
    ],
)
@pytest.mark.asyncio
async def test_deterministic_results_are_never_counted_as_nvidia(model_id: str) -> None:
    jobs = SwarmJobManager(DeterministicGraph(model_id))
    queued = await jobs.submit(UserRequest(text="consulta local"))

    completed = await _terminal(jobs, queued.job_id)
    metrics = await jobs.metrics()

    assert completed.evaluation is not None
    assert completed.evaluation.brain is BrainTarget.DETERMINISTIC
    assert metrics["brain"]["deterministic"] == 1
    assert metrics["brain"]["nvidia"] == 0
    await jobs.close()


@pytest.mark.asyncio
async def test_quality_gate_requires_twenty_successful_fast_jobs() -> None:
    jobs = SwarmJobManager(StreamingGraph())

    for index in range(20):
        queued = await jobs.submit(UserRequest(text=f"turno {index}"))
        await _terminal(jobs, queued.job_id)

    metrics = await jobs.metrics()

    assert metrics["quality"]["status"] == "competitive"
    assert metrics["quality"]["passes"] == {
        "success_rate": True,
        "first_partial_p95_ms": True,
        "conversation_p95_ms": True,
        "action_success_rate": None,
        "owner_recognition_rate": None,
        "response_quality_pass_rate": True,
        "owner_feedback_helpful_rate": None,
        "repair_recovery_rate": None,
    }
    await jobs.close()


@pytest.mark.asyncio
async def test_conversation_quality_gate_detects_repetitive_responses() -> None:
    jobs = SwarmJobManager(RepetitiveGraph())

    for index in range(20):
        queued = await jobs.submit(UserRequest(text=f"Conversemos sobre el turno {index}"))
        await _terminal(jobs, queued.job_id)

    metrics = await jobs.metrics()

    assert metrics["quality"]["status"] == "needs_attention"
    assert metrics["quality"]["passes"]["response_quality_pass_rate"] is False
    assert metrics["quality"]["observed"]["response_quality_pass_rate"] == 0.0
    assert metrics["quality"]["observed"]["response_quality_flags"][
        ConversationQualityFlag.REPEATED_SENTENCE.value
    ] == 20
    await jobs.close()


@pytest.mark.asyncio
async def test_explicit_feedback_updates_only_the_previous_job_evaluation() -> None:
    graph = ImmediateGraph()
    jobs = SwarmJobManager(graph)
    original = await jobs.submit(UserRequest(text="Explícame el avance"))
    await _terminal(jobs, original.job_id)

    feedback = await jobs.submit(UserRequest(text="Esa respuesta no fue útil"))
    feedback_completed = await _terminal(jobs, feedback.job_id)
    original_updated = await jobs.status(original.job_id)
    metrics = await jobs.metrics()

    assert original_updated.evaluation is not None
    assert graph.inputs[1]["request"].metadata[FEEDBACK_STATUS_METADATA] == (
        FEEDBACK_TARGET_AVAILABLE
    )
    assert original_updated.evaluation.owner_feedback is OwnerFeedback.UNHELPFUL
    assert feedback_completed.evaluation is not None
    assert feedback_completed.evaluation.feedback_event is True
    assert feedback_completed.evaluation.owner_feedback is None
    assert feedback_completed.evaluation.response_quality_score is None
    assert metrics["quality"]["observed"]["owner_feedback_count"] == 1
    assert metrics["quality"]["observed"]["feedback_jobs"] == 1
    assert metrics["quality"]["observed"]["owner_feedback_helpful_rate"] == 0.0
    assert metrics["quality"]["passes"]["owner_feedback_helpful_rate"] is None
    await jobs.close()


@pytest.mark.asyncio
async def test_unhelpful_feedback_marks_exactly_one_next_turn_for_local_repair() -> None:
    graph = ImmediateGraph()
    jobs = SwarmJobManager(graph)
    original = await jobs.submit(UserRequest(text="Explícame el avance"))
    await _terminal(jobs, original.job_id)
    feedback = await jobs.submit(UserRequest(text="Esa respuesta no fue útil"))
    await _terminal(jobs, feedback.job_id)

    correction = await jobs.submit(UserRequest(text="Necesitaba un resumen ejecutivo"))
    correction_completed = await _terminal(jobs, correction.job_id)
    following = await jobs.submit(UserRequest(text="Ahora enumera los riesgos"))
    await _terminal(jobs, following.job_id)

    assert graph.inputs[2]["request"].metadata[REPAIR_CONTEXT_METADATA] is True
    assert correction_completed.evaluation is not None
    assert correction_completed.evaluation.repair_attempt is True
    assert REPAIR_CONTEXT_METADATA not in graph.inputs[3]["request"].metadata
    await jobs.close()


@pytest.mark.asyncio
async def test_helpful_feedback_clears_a_pending_repair_window() -> None:
    graph = ImmediateGraph()
    jobs = SwarmJobManager(graph)
    original = await jobs.submit(UserRequest(text="Explícame el avance"))
    await _terminal(jobs, original.job_id)
    unhelpful = await jobs.submit(UserRequest(text="Esa respuesta no fue útil"))
    await _terminal(jobs, unhelpful.job_id)
    helpful = await jobs.submit(UserRequest(text="Esa respuesta fue útil"))
    await _terminal(jobs, helpful.job_id)

    next_turn = await jobs.submit(UserRequest(text="Continúa"))
    await _terminal(jobs, next_turn.job_id)

    assert REPAIR_CONTEXT_METADATA not in graph.inputs[3]["request"].metadata
    await jobs.close()


@pytest.mark.asyncio
async def test_repair_window_expires_without_polling() -> None:
    current = [datetime(2026, 8, 26, 12, 0, tzinfo=UTC)]
    graph = ImmediateGraph()
    jobs = SwarmJobManager(graph, clock=lambda: current[0])
    original = await jobs.submit(UserRequest(text="Explícame el avance"))
    await _terminal(jobs, original.job_id)
    feedback = await jobs.submit(UserRequest(text="Esa respuesta no fue útil"))
    await _terminal(jobs, feedback.job_id)
    current[0] += timedelta(minutes=3)

    next_turn = await jobs.submit(UserRequest(text="Continúa"))
    await _terminal(jobs, next_turn.job_id)

    assert REPAIR_CONTEXT_METADATA not in graph.inputs[2]["request"].metadata
    await jobs.close()


@pytest.mark.asyncio
async def test_explicit_feedback_measures_repair_recovery_without_storing_text() -> None:
    graph = ImmediateGraph()
    jobs = SwarmJobManager(graph)
    original = await jobs.submit(UserRequest(text="Explícame el avance"))
    await _terminal(jobs, original.job_id)
    negative = await jobs.submit(UserRequest(text="Esa respuesta no fue útil"))
    await _terminal(jobs, negative.job_id)
    repair = await jobs.submit(UserRequest(text="Necesitaba un resumen ejecutivo"))
    await _terminal(jobs, repair.job_id)
    positive = await jobs.submit(UserRequest(text="Esa respuesta fue útil"))
    await _terminal(jobs, positive.job_id)

    repair_completed = await jobs.status(repair.job_id)
    metrics = await jobs.metrics()

    assert repair_completed.evaluation is not None
    assert repair_completed.evaluation.repair_attempt is True
    assert repair_completed.evaluation.owner_feedback is OwnerFeedback.HELPFUL
    serialized = repair_completed.evaluation.model_dump_json()
    assert "resumen ejecutivo" not in serialized
    assert metrics["quality"]["observed"]["repair_attempts"] == 1
    assert metrics["quality"]["observed"]["repair_rated_count"] == 1
    assert metrics["quality"]["observed"]["repair_recovery_rate"] == 1.0
    assert metrics["quality"]["passes"]["repair_recovery_rate"] is None
    await jobs.close()


@pytest.mark.asyncio
async def test_repair_recovery_gate_requires_three_explicit_ratings() -> None:
    jobs = SwarmJobManager(StreamingGraph())

    for index in range(3):
        original = await jobs.submit(UserRequest(text=f"solicitud base {index}"))
        await _terminal(jobs, original.job_id)
        negative = await jobs.submit(UserRequest(text="Esa respuesta no fue útil"))
        await _terminal(jobs, negative.job_id)
        repair = await jobs.submit(UserRequest(text=f"corrección precisa {index}"))
        await _terminal(jobs, repair.job_id)
        verdict = "Esa respuesta fue útil" if index < 2 else "Esa respuesta no fue útil"
        rating = await jobs.submit(UserRequest(text=verdict))
        await _terminal(jobs, rating.job_id)

    metrics = await jobs.metrics()

    assert metrics["quality"]["observed"]["repair_attempts"] == 3
    assert metrics["quality"]["observed"]["repair_rated_count"] == 3
    assert metrics["quality"]["observed"]["repair_recovery_rate"] == 0.6667
    assert metrics["quality"]["passes"]["repair_recovery_rate"] is False
    await jobs.close()


@pytest.mark.asyncio
async def test_unverified_voice_feedback_does_not_update_previous_job() -> None:
    jobs = SwarmJobManager(ImmediateGraph())
    original = await jobs.submit(UserRequest(text="Explícame el avance"))
    await _terminal(jobs, original.job_id)

    feedback = await jobs.submit(
        UserRequest(
            text="Esa respuesta fue útil",
            modalities=frozenset({InputModality.TEXT, InputModality.AUDIO}),
            metadata={"speech_on_device": True},
        )
    )
    await _terminal(jobs, feedback.job_id)
    original_updated = await jobs.status(original.job_id)

    assert original_updated.evaluation is not None
    assert original_updated.evaluation.owner_feedback is None
    await jobs.close()


@pytest.mark.asyncio
async def test_cancelled_feedback_never_updates_previous_job() -> None:
    graph = FeedbackBlockingGraph()
    jobs = SwarmJobManager(graph)
    original = await jobs.submit(UserRequest(text="Explícame el avance"))
    await _terminal(jobs, original.job_id)

    feedback = await jobs.submit(UserRequest(text="Esa respuesta no fue útil"))
    await graph.feedback_started.wait()
    await jobs.cancel(feedback.job_id)
    original_updated = await jobs.status(original.job_id)

    assert original_updated.evaluation is not None
    assert original_updated.evaluation.owner_feedback is None
    next_turn = await jobs.submit(UserRequest(text="Continúa"))
    await _terminal(jobs, next_turn.job_id)
    assert REPAIR_CONTEXT_METADATA not in graph.inputs[2]["request"].metadata
    await jobs.close()


@pytest.mark.asyncio
async def test_owner_feedback_quality_gate_requires_five_rated_responses() -> None:
    jobs = SwarmJobManager(StreamingGraph())

    for index in range(20):
        original = await jobs.submit(UserRequest(text=f"turno {index}"))
        await _terminal(jobs, original.job_id)
        if index < 5:
            verdict = "útil" if index < 4 else "no fue útil"
            text = (
                "Esa respuesta fue útil"
                if verdict == "útil"
                else "Esa respuesta no fue útil"
            )
            feedback = await jobs.submit(UserRequest(text=text))
            await _terminal(jobs, feedback.job_id)

    metrics = await jobs.metrics()

    assert metrics["quality"]["observed"]["owner_feedback_count"] == 5
    assert metrics["quality"]["observed"]["owner_feedback_helpful_rate"] == 0.8
    assert metrics["quality"]["passes"]["owner_feedback_helpful_rate"] is True
    assert metrics["quality"]["status"] == "competitive"
    assert metrics["quality"]["observed"]["conversation_jobs"] == 20
    assert metrics["quality"]["observed"]["feedback_jobs"] == 5
    await jobs.close()


@pytest.mark.asyncio
async def test_owner_feedback_survives_manager_restart(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    store = SQLiteEvaluationStore(private / "evaluations.sqlite3")
    store.initialize()
    first = SwarmJobManager(ImmediateGraph(), evaluation_store=store)
    original = await first.submit(UserRequest(text="Explícame el avance"))
    await _terminal(first, original.job_id)
    feedback = await first.submit(UserRequest(text="Esa respuesta fue útil"))
    await _terminal(first, feedback.job_id)
    await first.close()

    second = SwarmJobManager(ImmediateGraph(), evaluation_store=store)
    metrics = await second.metrics()

    assert metrics["quality"]["observed"]["owner_feedback_count"] == 1
    assert metrics["quality"]["observed"]["owner_feedback_helpful_rate"] == 1.0
    await second.close()


@pytest.mark.asyncio
async def test_repair_recovery_metric_survives_manager_restart(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    store = SQLiteEvaluationStore(private / "evaluations.sqlite3")
    store.initialize()
    first = SwarmJobManager(ImmediateGraph(), evaluation_store=store)
    original = await first.submit(UserRequest(text="Explícame el avance"))
    await _terminal(first, original.job_id)
    negative = await first.submit(UserRequest(text="Esa respuesta no fue útil"))
    await _terminal(first, negative.job_id)
    repair = await first.submit(UserRequest(text="Necesitaba un resumen ejecutivo"))
    await _terminal(first, repair.job_id)
    positive = await first.submit(UserRequest(text="Esa respuesta fue útil"))
    await _terminal(first, positive.job_id)
    await first.close()

    second = SwarmJobManager(ImmediateGraph(), evaluation_store=store)
    metrics = await second.metrics()

    assert metrics["quality"]["observed"]["repair_attempts"] == 1
    assert metrics["quality"]["observed"]["repair_rated_count"] == 1
    assert metrics["quality"]["observed"]["repair_recovery_rate"] == 1.0
    await second.close()


@pytest.mark.asyncio
async def test_action_quality_counts_only_verified_outcomes() -> None:
    jobs = SwarmJobManager(CompletedToolGraph(verified=False))
    queued = await jobs.submit(UserRequest(text="Controla Safari"))

    completed = await _terminal(jobs, queued.job_id)
    metrics = await jobs.metrics()

    assert completed.status is JobStatus.COMPLETED
    assert completed.evaluation is not None
    assert completed.evaluation.succeeded is True
    assert completed.evaluation.outcome_verified is False
    assert completed.evaluation.tool_name == "computer_use"
    assert metrics["quality"]["observed"]["action_success_rate"] == 0.0
    assert metrics["quality"]["passes"]["action_success_rate"] is False
    await jobs.close()


@pytest.mark.asyncio
async def test_direct_action_is_measured_as_deterministic_brain(tmp_path: Path) -> None:
    broker = build_default_tool_broker()
    context = default_policy_context(tmp_path)
    jobs = SwarmJobManager(
        PendingDeterministicActionGraph(broker, context),
        tool_broker=broker,
        policy_context=context,
    )
    queued = await jobs.submit(UserRequest(text="Abre Safari"))
    await _awaiting_confirmation(jobs, queued.job_id)

    cancelled = await jobs.cancel(queued.job_id)
    metrics = await jobs.metrics()

    assert cancelled.evaluation is not None
    assert cancelled.evaluation.brain is BrainTarget.DETERMINISTIC
    assert metrics["brain"]["deterministic"] == 1
    assert metrics["brain"]["nvidia"] == 0
    await jobs.close()


@pytest.mark.asyncio
async def test_exact_pending_call_is_approved_once_over_ipc_and_audited(tmp_path: Path) -> None:
    jobs, audit = _approval_jobs(tmp_path)
    service = SwarmIpcService(jobs)
    authenticator = IpcAuthenticator(bytes.fromhex("6d" * 32))
    queued = await jobs.submit(UserRequest(text="Escanea loopback"))
    pending = await _awaiting_confirmation(jobs, queued.job_id)
    confirmation = pending.confirmation
    assert confirmation is not None
    assert confirmation.summary == "Sondeo TCP en 127.0.0.1/32; puertos 443"
    assert "normalized_arguments" not in pending.model_dump(mode="json")

    with pytest.raises(JobConfirmationError):
        await jobs.approve(queued.job_id, "f" * 64)

    approval = authenticator.create_request(
        "jobs.approve",
        {
            "job_id": str(queued.job_id),
            "call_digest": confirmation.call_digest,
        },
    )
    accepted = await service.handle(approval)
    completed = await _terminal(jobs, queued.job_id)
    replay = await service.handle(approval)

    assert accepted.ok is True
    assert accepted.payload["status"] == JobStatus.RUNNING
    assert completed.status is JobStatus.COMPLETED
    assert completed.confirmation is None
    assert completed.result == (
        "Sondeo TCP completado en 127.0.0.1/32. 127.0.0.1: sin puertos abiertos"
    )
    assert replay.error_code == "confirmation_unavailable"
    assert [record.event_type for record in audit.verify()] == [
        "tool_authorization",
        "tool_execution",
    ]
    await jobs.close()


@pytest.mark.asyncio
async def test_fixed_terminal_template_uses_same_exact_approval_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "aegis_core.tools.execution.subprocess.run",
        lambda command, **_: subprocess.CompletedProcess(
            command,
            0,
            stdout=b"COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n",
        ),
    )
    broker = build_default_tool_broker()
    base = default_policy_context(tmp_path)
    store = OneTimeConfirmationStore()
    context = PolicyContext(
        workspace_root=tmp_path,
        network_scopes=base.network_scopes,
        confirmation_store=store,
    )
    audit = HashChainAuditLog(tmp_path / "terminal-audit.jsonl")
    jobs = SwarmJobManager(
        PendingTerminalGraph(broker, context),
        tool_broker=broker,
        policy_context=context,
        confirmation_store=store,
        tool_executor=ReadOnlyToolExecutor(),
        audit_sink=audit,
    )
    queued = await jobs.submit(UserRequest(text="Lista listeners locales"))
    pending = await _awaiting_confirmation(jobs, queued.job_id)
    confirmation = pending.confirmation
    assert confirmation is not None
    assert confirmation.tool_name == "terminal_run_template"
    assert confirmation.summary == "Diagnóstico local: listeners TCP"

    running = await jobs.approve(queued.job_id, confirmation.call_digest)
    completed = await _terminal(jobs, queued.job_id)

    assert running.status is JobStatus.RUNNING
    assert completed.status is JobStatus.COMPLETED
    assert completed.result == (
        "Listeners TCP locales:\nCOMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME"
    )
    assert [record.event_type for record in audit.verify()] == [
        "tool_authorization",
        "tool_execution",
    ]
    await jobs.close()


def test_security_posture_result_is_presented_as_bounded_spanish_statuses() -> None:
    result = ToolExecutionResult(
        call_id="call-posture",
        tool_name="terminal_run_template",
        success=True,
        output=(
            '{"filevault":"enabled","firewall":"disabled",'
            '"gatekeeper":"enabled","sip":"unavailable"}'
        ),
        metadata={"template": "security_posture"},
    )

    assert SwarmJobManager._format_tool_result(result) == (
        "Postura de seguridad de macOS:\n"
        "SIP: no disponible\n"
        "Gatekeeper: activado\n"
        "FileVault: activado\n"
        "Firewall: desactivado"
    )


@pytest.mark.asyncio
async def test_pending_confirmation_expires_fail_closed(tmp_path: Path) -> None:
    current = [datetime(2026, 8, 19, 12, 0, tzinfo=UTC)]
    jobs, _ = _approval_jobs(tmp_path, clock=lambda: current[0])
    queued = await jobs.submit(UserRequest(text="Escanea loopback"))
    pending = await _awaiting_confirmation(jobs, queued.job_id)
    assert pending.confirmation is not None

    current[0] += timedelta(minutes=3)
    expired = await jobs.status(queued.job_id)

    assert expired.status is JobStatus.FAILED
    assert expired.error_code == "confirmation_expired"
    assert expired.confirmation is None
    with pytest.raises(JobConfirmationError):
        await jobs.approve(queued.job_id, pending.confirmation.call_digest)
    await jobs.close()


@pytest.mark.asyncio
async def test_pending_confirmation_can_be_explicitly_denied(tmp_path: Path) -> None:
    jobs, _ = _approval_jobs(tmp_path)
    queued = await jobs.submit(UserRequest(text="Escanea loopback"))
    await _awaiting_confirmation(jobs, queued.job_id)

    denied = await jobs.cancel(queued.job_id)

    assert denied.status is JobStatus.CANCELLED
    assert denied.confirmation is None
    await jobs.close()


@pytest.mark.asyncio
async def test_job_failure_does_not_expose_exception_details() -> None:
    jobs = SwarmJobManager(FailingGraph())
    queued = await jobs.submit(UserRequest(text="hola"))

    failed = await _terminal(jobs, queued.job_id)

    assert failed.status is JobStatus.FAILED
    assert failed.error_code == "swarm_execution_failed"
    assert failed.result is None
    assert "secret provider details" not in repr(failed)
    await jobs.close()


@pytest.mark.asyncio
async def test_job_result_is_bounded_by_utf8_bytes() -> None:
    jobs = SwarmJobManager(LargeUnicodeGraph())
    queued = await jobs.submit(UserRequest(text="grande"))

    completed = await _terminal(jobs, queued.job_id)

    assert completed.status is JobStatus.COMPLETED
    assert completed.result is not None
    assert len(completed.result.encode("utf-8")) <= 24_576
    await jobs.close()


@pytest.mark.asyncio
async def test_job_result_bound_accounts_for_json_escaping() -> None:
    class ControlCharacterGraph:
        async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]:
            del input
            return {
                "final_result": AgentResult(
                    role=AgentRole.SYNTHESIZER,
                    model_id="fake/synthesizer",
                    content="\x00" * 20_000,
                )
            }

    jobs = SwarmJobManager(ControlCharacterGraph())
    queued = await jobs.submit(UserRequest(text="control"))
    completed = await _terminal(jobs, queued.job_id)

    assert completed.result is not None
    assert len(json.dumps(completed.result, ensure_ascii=False).encode("utf-8")) <= 24_576
    await jobs.close()


@pytest.mark.asyncio
async def test_running_job_can_be_cancelled() -> None:
    graph = BlockingGraph()
    jobs = SwarmJobManager(graph)
    queued = await jobs.submit(UserRequest(text="espera"))
    await graph.started.wait()

    cancelled = await jobs.cancel(queued.job_id)

    assert cancelled.status is JobStatus.CANCELLED
    await jobs.close()


@pytest.mark.asyncio
async def test_timed_out_job_fails_safely_and_releases_capacity() -> None:
    graph = BlockingGraph()
    jobs = SwarmJobManager(graph, max_jobs=1, execution_timeout_seconds=0.01)
    first = await jobs.submit(UserRequest(text="espera"))
    await graph.started.wait()

    failed = await _terminal(jobs, first.job_id)
    graph.release.set()
    second = await jobs.submit(UserRequest(text="continua"))
    completed = await _terminal(jobs, second.job_id)

    assert failed.status is JobStatus.FAILED
    assert failed.error_code == "swarm_execution_timeout"
    assert failed.result is None
    assert completed.status is JobStatus.COMPLETED
    with pytest.raises(JobNotFoundError):
        await jobs.status(first.job_id)
    await jobs.close()


@pytest.mark.asyncio
async def test_queued_job_can_be_cancelled_before_it_starts() -> None:
    graph = BlockingGraph()
    jobs = SwarmJobManager(graph)
    queued = await jobs.submit(UserRequest(text="espera"))

    cancelled = await jobs.cancel(queued.job_id)

    assert cancelled.status is JobStatus.CANCELLED
    await jobs.close()


@pytest.mark.asyncio
async def test_capacity_fails_closed_when_all_jobs_are_active() -> None:
    graph = BlockingGraph()
    jobs = SwarmJobManager(graph, max_jobs=1)
    first = await jobs.submit(UserRequest(text="primero"))

    with pytest.raises(JobCapacityError):
        await jobs.submit(UserRequest(text="segundo"))

    await jobs.cancel(first.job_id)
    await jobs.close()


@pytest.mark.asyncio
async def test_terminal_job_is_evicted_when_capacity_is_reused() -> None:
    jobs = SwarmJobManager(ImmediateGraph(), max_jobs=1)
    first = await jobs.submit(UserRequest(text="primero"))
    await _terminal(jobs, first.job_id)

    second = await jobs.submit(UserRequest(text="segundo"))

    with pytest.raises(JobNotFoundError):
        await jobs.status(first.job_id)
    assert second.job_id != first.job_id
    await jobs.close()


@pytest.mark.asyncio
async def test_swarm_ipc_service_validates_payload_and_hides_missing_jobs() -> None:
    jobs = SwarmJobManager(ImmediateGraph())
    service = SwarmIpcService(jobs)
    authenticator = IpcAuthenticator(bytes.fromhex("77" * 32))
    invalid = authenticator.create_request("swarm.submit", {"text": ""})
    missing = authenticator.create_request("jobs.status", {"job_id": str(uuid4())})

    invalid_result = await service.handle(invalid)
    missing_result = await service.handle(missing)

    assert invalid_result.ok is False
    assert invalid_result.error_code == "invalid_payload"
    assert missing_result.ok is False
    assert missing_result.error_code == "job_not_found"
    await jobs.close()


@pytest.mark.asyncio
async def test_voice_submit_forces_audio_modality_and_local_metadata() -> None:
    graph = ImmediateGraph()
    jobs = SwarmJobManager(graph)
    service = SwarmIpcService(jobs)
    authenticator = IpcAuthenticator(bytes.fromhex("79" * 32))
    capture_id = uuid4()
    request = authenticator.create_request(
        "voice.submit",
        {
            "transcript": {
                "capture_id": str(capture_id),
                "sequence": 1,
                "text": "Analiza el sistema",
                "locale_identifier": "es-EC",
                "duration_milliseconds": 800,
                "is_final": True,
                "on_device": True,
                "speaker_id": "guillermo",
                "speaker_confidence": 0.88,
                "owner_speaker_profile": True,
                "owner_presence_verified": True,
            }
        },
    )

    submitted = await service.handle(request)
    completed = await _terminal(jobs, UUID(submitted.payload["job_id"]))
    user_request = graph.inputs[0]["request"]

    assert user_request.text == "Analiza el sistema"
    assert user_request.modalities == frozenset({InputModality.TEXT, InputModality.AUDIO})
    assert user_request.metadata["speech_capture_id"] == str(capture_id)
    assert user_request.metadata["speech_locale"] == "es-EC"
    assert user_request.metadata["speech_on_device"] is True
    assert user_request.metadata["speaker_identity"] == {
        "confidence": 0.88,
        "id": "guillermo",
    }
    assert user_request.metadata["sole_speaker_profile"] is False
    assert user_request.metadata["owner_speaker_profile"] is True
    assert user_request.metadata["owner_presence_verified"] is True
    assert completed.evaluation is not None
    assert completed.evaluation.voice_request is True
    assert completed.evaluation.owner_verified is True
    metrics = await jobs.metrics()
    assert metrics["quality"]["observed"]["owner_recognition_rate"] == 1.0
    await jobs.close()


@pytest.mark.asyncio
async def test_verified_voice_resolves_persistent_conversation_in_one_submit(
    tmp_path: Path,
) -> None:
    _, conversations = _conversation_components(tmp_path)
    graph = ImmediateGraph()
    jobs = SwarmJobManager(graph, conversations=conversations)
    service = SwarmIpcService(jobs)
    authenticator = IpcAuthenticator(bytes.fromhex("76" * 32))
    stale_conversation_id = uuid4()
    request = authenticator.create_request(
        "voice.submit",
        {
            "transcript": {
                "capture_id": str(uuid4()),
                "sequence": 1,
                "text": "Recuerda este contexto",
                "locale_identifier": "es-EC",
                "duration_milliseconds": 700,
                "is_final": True,
                "on_device": True,
                "speaker_id": "owner",
                "speaker_confidence": 0.91,
                "owner_speaker_profile": True,
                "owner_presence_verified": True,
            },
            "conversation_id": str(stale_conversation_id),
            "persist_conversation": True,
        },
    )

    submitted = await service.handle(request)
    resolved_id = UUID(submitted.payload["conversation_id"])
    completed = await _terminal(jobs, UUID(submitted.payload["job_id"]))

    assert submitted.ok is True
    assert resolved_id != stale_conversation_id
    assert completed.conversation_persisted is True
    assert graph.inputs[0]["request"].metadata["conversation_id"] == str(resolved_id)
    assert [turn.content for turn in await conversations.history(resolved_id)] == [
        "Recuerda este contexto",
        "respuesta:Recuerda este contexto",
    ]
    await jobs.close()


@pytest.mark.asyncio
async def test_unverified_voice_cannot_request_persistent_conversation(
    tmp_path: Path,
) -> None:
    _, conversations = _conversation_components(tmp_path)
    graph = ImmediateGraph()
    jobs = SwarmJobManager(graph, conversations=conversations)
    service = SwarmIpcService(jobs)
    authenticator = IpcAuthenticator(bytes.fromhex("75" * 32))
    request = authenticator.create_request(
        "voice.submit",
        {
            "transcript": {
                "capture_id": str(uuid4()),
                "sequence": 1,
                "text": "Guarda esto",
                "locale_identifier": "es-EC",
                "duration_milliseconds": 600,
                "is_final": True,
                "on_device": True,
            },
            "persist_conversation": True,
        },
    )

    rejected = await service.handle(request)

    assert rejected.ok is False
    assert rejected.error_code == "owner_verification_required"
    assert graph.inputs == []
    await jobs.close()


@pytest.mark.asyncio
async def test_voice_submit_consumes_capture_id_once() -> None:
    graph = ImmediateGraph()
    jobs = SwarmJobManager(graph)
    service = SwarmIpcService(jobs)
    authenticator = IpcAuthenticator(bytes.fromhex("78" * 32))
    transcript = {
        "capture_id": str(uuid4()),
        "sequence": 1,
        "text": "Revisa el estado local",
        "locale_identifier": "es-EC",
        "duration_milliseconds": 600,
        "is_final": True,
        "on_device": True,
    }

    submitted = await service.handle(
        authenticator.create_request("voice.submit", {"transcript": transcript})
    )
    replayed = await service.handle(
        authenticator.create_request("voice.submit", {"transcript": transcript})
    )
    await _terminal(jobs, UUID(submitted.payload["job_id"]))

    assert submitted.ok is True
    assert replayed.ok is False
    assert replayed.error_code == "voice_capture_replayed"
    assert len(graph.inputs) == 1
    await jobs.close()


@pytest.mark.asyncio
async def test_image_submit_adds_ephemeral_typed_attachment() -> None:
    graph = ImmediateGraph()
    jobs = SwarmJobManager(graph)
    service = SwarmIpcService(jobs)
    authenticator = IpcAuthenticator(bytes.fromhex("7c" * 32))
    encoded = base64.b64encode(b"\x89PNG\r\n\x1a\ncontent").decode("ascii")
    request = authenticator.create_request(
        "image.submit",
        {
            "text": "Describe la imagen",
            "image": {"media_type": "image/png", "data_base64": encoded},
        },
    )

    submitted = await service.handle(request)
    await _terminal(jobs, UUID(submitted.payload["job_id"]))
    user_request = graph.inputs[0]["request"]

    assert user_request.modalities == frozenset({InputModality.TEXT, InputModality.IMAGE})
    assert user_request.image == ImageInput(media_type="image/png", data_base64=encoded)
    assert encoded not in json.dumps(submitted.payload)
    await jobs.close()


@pytest.mark.asyncio
async def test_spoken_image_submit_preserves_voice_identity_and_modality(
    tmp_path: Path,
) -> None:
    _, conversations = _conversation_components(tmp_path)
    graph = ImmediateGraph()
    jobs = SwarmJobManager(graph, conversations=conversations)
    service = SwarmIpcService(jobs)
    authenticator = IpcAuthenticator(bytes.fromhex("7f" * 32))
    capture_id = uuid4()
    encoded = base64.b64encode(b"\x89PNG\r\n\x1a\ncontent").decode("ascii")
    request = authenticator.create_request(
        "image.submit",
        {
            "text": "¿Qué ves?",
            "image": {"media_type": "image/png", "data_base64": encoded},
            "voice_context": {
                "capture_id": str(capture_id),
                "locale_identifier": "es-EC",
                "speaker_id": "guillermo",
                "speaker_confidence": 0.92,
                "owner_speaker_profile": True,
                "owner_presence_verified": True,
            },
            "persist_conversation": True,
        },
    )

    submitted = await service.handle(request)
    completed = await _terminal(jobs, UUID(submitted.payload["job_id"]))
    user_request = graph.inputs[0]["request"]

    assert user_request.modalities == frozenset(
        {InputModality.TEXT, InputModality.AUDIO, InputModality.IMAGE}
    )
    assert user_request.metadata["speech_capture_id"] == str(capture_id)
    assert user_request.metadata["speech_on_device"] is True
    assert user_request.metadata["speaker_identity"] == {
        "confidence": 0.92,
        "id": "guillermo",
    }
    assert user_request.metadata["sole_speaker_profile"] is False
    assert user_request.metadata["owner_speaker_profile"] is True
    assert user_request.metadata["owner_presence_verified"] is True
    assert completed.conversation_persisted is True
    assert UUID(submitted.payload["conversation_id"]) == completed.conversation_id
    await jobs.close()


@pytest.mark.asyncio
async def test_image_submit_rejects_invalid_image_before_dispatch() -> None:
    graph = ImmediateGraph()
    jobs = SwarmJobManager(graph)
    service = SwarmIpcService(jobs)
    authenticator = IpcAuthenticator(bytes.fromhex("7d" * 32))
    request = authenticator.create_request(
        "image.submit",
        {
            "text": "Describe la imagen",
            "image": {
                "media_type": "image/png",
                "data_base64": base64.b64encode(b"not-a-png").decode("ascii"),
            },
        },
    )

    result = await service.handle(request)

    assert result.error_code == "invalid_payload"
    assert graph.inputs == []
    await jobs.close()


def test_maximum_image_submit_fits_default_ipc_frame() -> None:
    authenticator = IpcAuthenticator(bytes.fromhex("7e" * 32))
    image = b"\x89PNG\r\n\x1a\n" + b"x" * (MAX_IMAGE_BYTES - 8)
    request = authenticator.create_request(
        "image.submit",
        {
            "text": "Describe la imagen",
            "image": {
                "media_type": "image/png",
                "data_base64": base64.b64encode(image).decode("ascii"),
            },
        },
    )

    assert len(request.model_dump_json().encode("utf-8") + b"\n") <= 65_536


@pytest.mark.asyncio
async def test_voice_submit_rejects_partial_or_remote_transcript() -> None:
    graph = ImmediateGraph()
    jobs = SwarmJobManager(graph)
    service = SwarmIpcService(jobs)
    authenticator = IpcAuthenticator(bytes.fromhex("7a" * 32))

    for field in ("is_final", "on_device"):
        transcript = {
            "capture_id": str(uuid4()),
            "sequence": 1,
            "text": "No ejecutar",
            "locale_identifier": "es-EC",
            "duration_milliseconds": 500,
            "is_final": True,
            "on_device": True,
        }
        transcript[field] = False
        result = await service.handle(
            authenticator.create_request("voice.submit", {"transcript": transcript})
        )
        assert result.error_code == "invalid_payload"

    assert graph.inputs == []
    await jobs.close()


@pytest.mark.asyncio
async def test_voice_submit_reuses_pre_provider_secret_filter() -> None:
    graph = ImmediateGraph()
    jobs = SwarmJobManager(graph)
    service = SwarmIpcService(jobs)
    authenticator = IpcAuthenticator(bytes.fromhex("7b" * 32))
    request = authenticator.create_request(
        "voice.submit",
        {
            "transcript": {
                "capture_id": str(uuid4()),
                "sequence": 1,
                "text": "nvapi-example-credential",
                "locale_identifier": "es-US",
                "duration_milliseconds": 500,
                "is_final": True,
                "on_device": True,
            }
        },
    )

    result = await service.handle(request)

    assert result.error_code == "secret_material_rejected"
    assert graph.inputs == []
    await jobs.close()


@pytest.mark.asyncio
async def test_swarm_ipc_service_cancels_active_job() -> None:
    graph = BlockingGraph()
    jobs = SwarmJobManager(graph)
    service = SwarmIpcService(jobs)
    authenticator = IpcAuthenticator(bytes.fromhex("88" * 32))
    submit = authenticator.create_request("swarm.submit", {"text": "espera"})
    submitted = await service.handle(submit)
    await graph.started.wait()
    cancel = authenticator.create_request("jobs.cancel", {"job_id": submitted.payload["job_id"]})

    cancelled = await service.handle(cancel)

    assert cancelled.ok is True
    assert cancelled.payload["status"] == JobStatus.CANCELLED
    await jobs.close()


def _conversation_components(
    tmp_path: Path,
) -> tuple[SQLiteMemoryStore, ConversationCoordinator]:
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3")
    store.initialize()
    return store, ConversationCoordinator(store, namespace="user.default")


@pytest.mark.asyncio
async def test_owner_feedback_never_crosses_conversation_boundary(tmp_path: Path) -> None:
    store, conversations = _conversation_components(tmp_path)
    first_conversation = store.create_conversation(namespace="user.default")
    second_conversation = store.create_conversation(namespace="user.default")
    graph = ImmediateGraph()
    jobs = SwarmJobManager(graph, conversations=conversations)

    first = await jobs.submit(
        UserRequest(text="Primera conversación"),
        conversation_id=first_conversation.conversation_id,
    )
    await _terminal(jobs, first.job_id)
    second = await jobs.submit(
        UserRequest(text="Segunda conversación"),
        conversation_id=second_conversation.conversation_id,
    )
    await _terminal(jobs, second.job_id)
    feedback = await jobs.submit(
        UserRequest(text="Esa respuesta no fue útil"),
        conversation_id=first_conversation.conversation_id,
    )
    await _terminal(jobs, feedback.job_id)

    first_updated = await jobs.status(first.job_id)
    second_unchanged = await jobs.status(second.job_id)
    assert first_updated.evaluation is not None
    assert first_updated.evaluation.owner_feedback is OwnerFeedback.UNHELPFUL
    assert second_unchanged.evaluation is not None
    assert second_unchanged.evaluation.owner_feedback is None
    second_next = await jobs.submit(
        UserRequest(text="Continúa la segunda"),
        conversation_id=second_conversation.conversation_id,
    )
    await _terminal(jobs, second_next.job_id)
    first_next = await jobs.submit(
        UserRequest(text="Necesitaba más precisión"),
        conversation_id=first_conversation.conversation_id,
    )
    await _terminal(jobs, first_next.job_id)
    assert REPAIR_CONTEXT_METADATA not in graph.inputs[3]["request"].metadata
    assert graph.inputs[4]["request"].metadata[REPAIR_CONTEXT_METADATA] is True
    await jobs.close()


@pytest.mark.asyncio
async def test_unverified_voice_cannot_reuse_or_modify_an_existing_conversation(
    tmp_path: Path,
) -> None:
    store, conversations = _conversation_components(tmp_path)
    conversation = store.create_conversation(namespace="user.default")
    graph = ImmediateGraph()
    jobs = SwarmJobManager(graph, conversations=conversations)
    owner_turn = await jobs.submit(
        UserRequest(text="Contexto privado del propietario"),
        conversation_id=conversation.conversation_id,
    )
    await _terminal(jobs, owner_turn.job_id)

    unverified = await jobs.submit(
        UserRequest(
            text="Continúa",
            modalities=frozenset({InputModality.TEXT, InputModality.AUDIO}),
            metadata={"speech_on_device": True},
        ),
        conversation_id=conversation.conversation_id,
    )
    rejected = await _terminal(jobs, unverified.job_id)

    assert rejected.status is JobStatus.FAILED
    assert rejected.error_code == "voice_conversation_owner_required"
    assert len(graph.inputs) == 1
    assert len(await conversations.history(conversation.conversation_id)) == 2

    verified = await jobs.submit(
        UserRequest(
            text="Continúa",
            modalities=frozenset({InputModality.TEXT, InputModality.AUDIO}),
            metadata={
                "speaker_identity": {"id": "owner", "confidence": 0.91},
                "owner_speaker_profile": True,
                "owner_presence_verified": True,
                "speech_on_device": True,
            },
        ),
        conversation_id=conversation.conversation_id,
    )
    completed = await _terminal(jobs, verified.job_id)

    assert completed.status is JobStatus.COMPLETED
    assert len(graph.inputs) == 2
    assert len(graph.inputs[1]["conversation_history"]) == 2
    assert len(await conversations.history(conversation.conversation_id)) == 4
    await jobs.close()


@pytest.mark.asyncio
async def test_confirmed_mail_action_preserves_conversation_and_hides_body_from_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "aegis_core.tools.execution.subprocess.run",
        lambda command, **_: subprocess.CompletedProcess(
            command,
            0,
            stdout=b'{"sent":true,"recipient_count":1}',
        ),
    )
    store, conversations = _conversation_components(tmp_path)
    conversation = store.create_conversation(namespace="user.default")
    broker = build_default_tool_broker()
    confirmation_store = OneTimeConfirmationStore()
    base = default_policy_context(tmp_path)
    context = PolicyContext(
        workspace_root=tmp_path,
        network_scopes=base.network_scopes,
        confirmation_store=confirmation_store,
    )
    jobs = SwarmJobManager(
        PendingMailGraph(broker, context),
        conversations=conversations,
        tool_broker=broker,
        policy_context=context,
        confirmation_store=confirmation_store,
        tool_executor=ReadOnlyToolExecutor(),
    )
    queued = await jobs.submit(
        UserRequest(text="Envía el estado"),
        conversation_id=conversation.conversation_id,
    )
    pending = await _awaiting_confirmation(jobs, queued.job_id)
    confirmation = pending.confirmation
    assert confirmation is not None
    assert confirmation.summary == "Enviar correo a owner@example.com; asunto: Estado"
    assert "Listo" not in confirmation.summary

    await jobs.approve(queued.job_id, confirmation.call_digest)
    completed = await _terminal(jobs, queued.job_id)
    history = store.conversation_history(
        namespace="user.default",
        conversation_id=conversation.conversation_id,
    )

    assert completed.status is JobStatus.COMPLETED
    assert completed.conversation_persisted is True
    assert completed.result == "Correo enviado a 1 destinatario(s)."
    assert [turn.content for turn in history] == [
        "Envía el estado",
        "Correo enviado a 1 destinatario(s).",
    ]
    await jobs.close()


@pytest.mark.parametrize(
    ("tool_name", "arguments", "output", "summary", "expected"),
    [
        (
            "reminder_create",
            {"title": "Revisar informe"},
            '{"created":true,"list":"Recordatorios","title":"Revisar informe"}',
            "Crear recordatorio «Revisar informe»",
            "Recordatorio «Revisar informe» creado en «Recordatorios».",
        ),
        (
            "reminder_complete",
            {"title": "Revisar informe"},
            '{"completed":true,"list":"Recordatorios","title":"Revisar informe"}',
            "Completar recordatorio «Revisar informe»",
            "Recordatorio «Revisar informe» completado en «Recordatorios».",
        ),
        (
            "contact_create",
            {"first_name": "Ada", "email": "ada@example.com"},
            '{"created":true,"name":"Ada"}',
            "Crear contacto «Ada»; correo: ada@example.com",
            "Contacto «Ada» creado.",
        ),
        (
            "system_audio_set",
            {"volume_percent": 42, "muted": None},
            '{"output_muted":false,"output_volume_percent":42}',
            "Cambiar audio del Mac: volumen al 42 %",
            "Audio del Mac con sonido, volumen al 42 %.",
        ),
        (
            "media_control",
            {"action": "next"},
            '{"action":"next","bundle_identifier":"com.apple.Music"}',
            "Control multimedia: siguiente pista",
            "Pasé a la siguiente pista.",
        ),
        (
            "spotlight_open",
            {"query": "Informe.pdf"},
            '{"name":"Informe.pdf","opened":true}',
            "Abrir resultado exacto de Spotlight: Informe.pdf",
            "Abrí Informe.pdf desde Spotlight.",
        ),
        (
            "browser_search",
            {"query": "arquitectura segura", "browser": "safari"},
            '{"browser":"safari","opened":true,"query":"arquitectura segura"}',
            "Buscar con DuckDuckGo en Safari: arquitectura segura",
            "Búsqueda abierta en Safari: arquitectura segura",
        ),
    ],
)
@pytest.mark.asyncio
async def test_local_mutations_complete_through_the_exact_confirmation_flow(
    tmp_path: Path,
    tool_name: str,
    arguments: dict[str, object],
    output: str,
    summary: str,
    expected: str,
) -> None:
    class FixedExecutor:
        async def execute_async(
            self,
            authorization: ToolAuthorization,
            context: PolicyContext,
        ) -> ToolExecutionResult:
            del context
            assert authorization.reason_code == "confirmation_consumed"
            return ToolExecutionResult(
                call_id=authorization.call_id,
                tool_name=authorization.tool_name,
                success=True,
                output=output,
                metadata={"verified": True},
            )

    broker = build_default_tool_broker()
    confirmation_store = OneTimeConfirmationStore()
    base = default_policy_context(tmp_path)
    context = PolicyContext(
        workspace_root=tmp_path,
        network_scopes=base.network_scopes,
        confirmation_store=confirmation_store,
    )
    call = ToolCall(
        call_id=f"call-{tool_name}",
        tool_name=tool_name,
        arguments=arguments,
        requested_by=AgentRole.PLANNER,
    )
    jobs = SwarmJobManager(
        PendingConfirmedActionGraph(broker, context, call),
        tool_broker=broker,
        policy_context=context,
        confirmation_store=confirmation_store,
        tool_executor=FixedExecutor(),
    )

    queued = await jobs.submit(UserRequest(text="Ejecuta la acción local"))
    pending = await _awaiting_confirmation(jobs, queued.job_id)
    assert pending.confirmation is not None
    assert pending.confirmation.summary == summary
    await jobs.approve(queued.job_id, pending.confirmation.call_digest)
    completed = await _terminal(jobs, queued.job_id)

    assert completed.status is JobStatus.COMPLETED
    assert completed.result == expected
    assert completed.evaluation is not None
    assert completed.evaluation.outcome_verified is True
    await jobs.close()


def test_confirmed_audio_result_must_match_the_authorized_value() -> None:
    authorization = ToolAuthorization(
        call_id="call-audio-mismatch",
        tool_name="system_audio_set",
        call_digest="a" * 64,
        decision=PolicyDecision.ALLOW,
        reason_code="confirmation_consumed",
        normalized_arguments={"volume_percent": 42, "muted": None},
    )
    result = ToolExecutionResult(
        call_id=authorization.call_id,
        tool_name=authorization.tool_name,
        success=True,
        output='{"output_muted":false,"output_volume_percent":99}',
    )

    with pytest.raises(ValueError, match="does not match authorization"):
        SwarmJobManager._format_tool_result(result, authorization)


@pytest.mark.asyncio
async def test_jobs_persist_complete_exchanges_and_reuse_history(tmp_path) -> None:
    store, conversations = _conversation_components(tmp_path)
    conversation = store.create_conversation(namespace="user.default")
    graph = ImmediateGraph()
    jobs = SwarmJobManager(graph, conversations=conversations)

    first = await jobs.submit(
        UserRequest(text="primera"),
        conversation_id=conversation.conversation_id,
    )
    await _terminal(jobs, first.job_id)
    second = await jobs.submit(
        UserRequest(text="segunda"),
        conversation_id=conversation.conversation_id,
    )
    completed = await _terminal(jobs, second.job_id)
    history = store.conversation_history(
        namespace="user.default",
        conversation_id=conversation.conversation_id,
    )

    assert completed.conversation_id == conversation.conversation_id
    assert completed.conversation_persisted is True
    assert [turn.content for turn in history] == [
        "primera",
        "respuesta:primera",
        "segunda",
        "respuesta:segunda",
    ]
    assert [turn.content for turn in graph.inputs[1]["conversation_history"]] == [
        "primera",
        "respuesta:primera",
    ]
    await jobs.close()


@pytest.mark.asyncio
async def test_failed_conversation_job_does_not_persist_partial_turn(tmp_path) -> None:
    store, conversations = _conversation_components(tmp_path)
    conversation = store.create_conversation(namespace="user.default")
    jobs = SwarmJobManager(FailingGraph(), conversations=conversations)

    queued = await jobs.submit(
        UserRequest(text="no persistir"),
        conversation_id=conversation.conversation_id,
    )
    failed = await _terminal(jobs, queued.job_id)
    history = store.conversation_history(
        namespace="user.default",
        conversation_id=conversation.conversation_id,
    )

    assert failed.status is JobStatus.FAILED
    assert history == ()
    await jobs.close()


@pytest.mark.asyncio
async def test_swarm_ipc_rejects_unknown_conversation(tmp_path) -> None:
    _, conversations = _conversation_components(tmp_path)
    jobs = SwarmJobManager(ImmediateGraph(), conversations=conversations)
    service = SwarmIpcService(jobs)
    authenticator = IpcAuthenticator(bytes.fromhex("ab" * 32))
    request = authenticator.create_request(
        "swarm.submit",
        {"text": "hola", "conversation_id": str(uuid4())},
    )

    result = await service.handle(request)

    assert result.error_code == "conversation_not_found"
    await jobs.close()


@pytest.mark.asyncio
async def test_swarm_ipc_rejects_secret_before_provider_dispatch() -> None:
    graph = ImmediateGraph()
    jobs = SwarmJobManager(graph)
    service = SwarmIpcService(jobs)
    authenticator = IpcAuthenticator(bytes.fromhex("ac" * 32))
    request = authenticator.create_request(
        "swarm.submit",
        {"text": "nvapi-example-credential"},
    )

    result = await service.handle(request)

    assert result.error_code == "secret_material_rejected"
    assert graph.inputs == []
    await jobs.close()


@pytest.mark.asyncio
async def test_conversation_job_reports_when_secret_filter_skips_persistence(
    tmp_path,
) -> None:
    store, conversations = _conversation_components(tmp_path)
    conversation = store.create_conversation(namespace="user.default")
    jobs = SwarmJobManager(ImmediateGraph(), conversations=conversations)

    queued = await jobs.submit(
        UserRequest(text="nvapi-example-credential"),
        conversation_id=conversation.conversation_id,
    )
    completed = await _terminal(jobs, queued.job_id)

    assert completed.status is JobStatus.COMPLETED
    assert completed.conversation_persisted is False
    assert (
        store.conversation_history(
            namespace="user.default",
            conversation_id=conversation.conversation_id,
        )
        == ()
    )
    await jobs.close()


@pytest.mark.asyncio
async def test_conversation_capacity_has_stable_job_error(tmp_path) -> None:
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3")
    store.initialize()
    conversations = ConversationCoordinator(
        store,
        namespace="user.default",
        max_turns=2,
    )
    conversation = store.create_conversation(namespace="user.default")
    jobs = SwarmJobManager(ImmediateGraph(), conversations=conversations)

    first = await jobs.submit(
        UserRequest(text="primera"),
        conversation_id=conversation.conversation_id,
    )
    await _terminal(jobs, first.job_id)
    second = await jobs.submit(
        UserRequest(text="segunda"),
        conversation_id=conversation.conversation_id,
    )
    failed = await _terminal(jobs, second.job_id)

    assert failed.status is JobStatus.FAILED
    assert failed.error_code == "conversation_capacity_reached"
    await jobs.close()


@pytest.mark.asyncio
async def test_cancelled_conversation_job_leaves_no_partial_history(tmp_path) -> None:
    store, conversations = _conversation_components(tmp_path)
    conversation = store.create_conversation(namespace="user.default")
    graph = BlockingGraph()
    jobs = SwarmJobManager(graph, conversations=conversations)
    queued = await jobs.submit(
        UserRequest(text="cancelar"),
        conversation_id=conversation.conversation_id,
    )
    await graph.started.wait()

    cancelled = await jobs.cancel(queued.job_id)

    assert cancelled.status is JobStatus.CANCELLED
    assert (
        store.conversation_history(
            namespace="user.default",
            conversation_id=conversation.conversation_id,
        )
        == ()
    )
    await jobs.close()


@pytest.mark.asyncio
async def test_cancel_during_atomic_persistence_linearizes_as_completion(tmp_path) -> None:
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3")
    store.initialize()
    conversations = BlockingPersistenceCoordinator(store)
    conversation = store.create_conversation(namespace="user.default")
    jobs = SwarmJobManager(ImmediateGraph(), conversations=conversations)
    queued = await jobs.submit(
        UserRequest(text="terminar"),
        conversation_id=conversation.conversation_id,
    )
    await conversations.persistence_started.wait()

    cancellation = asyncio.create_task(jobs.cancel(queued.job_id))
    await asyncio.sleep(0)
    assert cancellation.done() is False
    conversations.release_persistence.set()
    completed = await cancellation

    assert completed.status is JobStatus.COMPLETED
    assert completed.conversation_persisted is True
    await jobs.close()
