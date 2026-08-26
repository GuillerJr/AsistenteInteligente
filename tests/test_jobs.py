import asyncio
import base64
import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from aegis_core.contracts import (
    MAX_IMAGE_BYTES,
    AgentResult,
    AgentRole,
    ImageInput,
    InputModality,
    ToolCall,
    ToolExecutionResult,
    UserRequest,
)
from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.jobs import (
    JobCapacityError,
    JobConfirmationError,
    JobNotFoundError,
    JobStatus,
    SwarmIpcService,
    SwarmJobManager,
)
from aegis_core.memory.conversations import ConversationCoordinator
from aegis_core.memory.sqlite import SQLiteMemoryStore
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
    }
    assert metrics["quality"]["observed"]["conversation_jobs"] == 1
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
    }
    await jobs.close()


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
                "sole_speaker_profile": True,
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
    assert user_request.metadata["sole_speaker_profile"] is True
    assert completed.evaluation is not None
    assert completed.evaluation.voice_request is True
    assert completed.evaluation.owner_verified is True
    metrics = await jobs.metrics()
    assert metrics["quality"]["observed"]["owner_recognition_rate"] == 1.0
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
