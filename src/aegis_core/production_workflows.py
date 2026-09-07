from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import stat
import tempfile
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from unittest.mock import patch

from aegis_core.brain.agent_graph import PlanExecuteReflectRunner, PlanStepStatus
from aegis_core.brain.router import decide_browser_target
from aegis_core.brain.vision_processor import ActiveVisionPayload, VisionProcessor, VisualState
from aegis_core.contracts import (
    AgentRole,
    InputModality,
    PolicyDecision,
    ToolAuthorization,
    ToolCall,
    ToolCallBasis,
    ToolExecutionResult,
    UserRequest,
)
from aegis_core.conversation_quality import (
    ConversationQualityEvaluator,
    ConversationQualityFlag,
)
from aegis_core.dialogue import DialogueKernel, DialogueMode
from aegis_core.ipc.framing import HEADER, encode_stream, read_message
from aegis_core.ipc.protocol import IpcAuthenticator, ProtocolError
from aegis_core.memory.contracts import MemoryKind
from aegis_core.memory.errors import MemoryNotFoundError
from aegis_core.memory.graph_service import GraphRAGService
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.orchestration.direct_actions import direct_tool_call
from aegis_core.provider_status import ProviderStatusIpcService
from aegis_core.runtime import BackgroundTaskSupervisor
from aegis_core.security import SecurityStateLatch
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context
from aegis_core.tools.silent_executor import SilentAction, SilentExecutor

SCHEMA_VERSION = "1.0"
EXPECTED_WORKFLOWS = 20
WORKFLOWS_PER_CATEGORY = 4
WORKFLOW_TIMEOUT_SECONDS = 5.0


class WorkflowExpectationError(RuntimeError):
    """A declared production-flow checkpoint was not proven."""


class OfflineBoundaryError(RuntimeError):
    """A production-flow fixture attempted to cross the local network boundary."""


class WorkflowCategory(StrEnum):
    CONVERSATION = "conversation"
    ORCHESTRATION = "orchestration"
    AUTOMATION = "automation"
    MEMORY = "memory"
    SECURITY = "security"


@dataclass(slots=True)
class WorkflowProbe:
    expected: frozenset[str]
    observed: set[str] = field(default_factory=set)

    def confirm(self, checkpoint: str, condition: bool) -> None:
        if checkpoint not in self.expected:
            raise WorkflowExpectationError("undeclared workflow checkpoint")
        if checkpoint in self.observed:
            raise WorkflowExpectationError("duplicate workflow checkpoint")
        if not condition:
            raise WorkflowExpectationError("workflow checkpoint failed")
        self.observed.add(checkpoint)

    def require_complete(self) -> None:
        if self.observed != self.expected:
            raise WorkflowExpectationError("workflow checkpoints are incomplete")


@dataclass(frozen=True, slots=True)
class WorkflowContext:
    root: Path

    def private_directory(self, name: str) -> Path:
        directory = self.root / name
        directory.mkdir(mode=0o700, exist_ok=False)
        metadata = os.lstat(directory)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise PermissionError("workflow directory is not private")
        return directory

    def memory_store(self, name: str, *, maximum: int = 50_000) -> SQLiteMemoryStore:
        directory = self.private_directory(name)
        store = SQLiteMemoryStore(
            directory / "memory.sqlite3",
            max_entries=maximum,
            max_namespace_entries=maximum,
            encryption_secret=b"p" * 32,
        )
        store.initialize()
        return store


WorkflowRunner = Callable[[WorkflowContext, WorkflowProbe], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    workflow_id: str
    category: WorkflowCategory
    checkpoints: tuple[str, ...]
    runner: WorkflowRunner


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    workflow_id: str
    category: WorkflowCategory
    passed: bool
    latency_ms: int
    checkpoints_passed: int
    checkpoints_expected: int
    failure_code: str | None = None

    def private_dict(self) -> dict[str, str | int | bool | None]:
        return {
            "workflow_id": self.workflow_id,
            "category": self.category.value,
            "passed": self.passed,
            "latency_ms": self.latency_ms,
            "checkpoints_passed": self.checkpoints_passed,
            "checkpoints_expected": self.checkpoints_expected,
            "failure_code": self.failure_code,
        }


@dataclass(frozen=True, slots=True)
class ProductionWorkflowReport:
    results: tuple[WorkflowResult, ...]
    duration_ms: int
    manifest_sha256: str
    network_attempts: int

    @property
    def passed(self) -> int:
        return sum(result.passed for result in self.results)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def score(self) -> int:
        return round((self.passed / self.total) * 100) if self.total else 0

    @property
    def gate_passed(self) -> bool:
        return (
            self.total == EXPECTED_WORKFLOWS
            and self.passed == EXPECTED_WORKFLOWS
            and self.network_attempts == 0
            and all(
                result.checkpoints_passed == result.checkpoints_expected
                for result in self.results
            )
        )

    def private_dict(self) -> dict[str, object]:
        category_totals = Counter(result.category.value for result in self.results)
        category_passed = Counter(
            result.category.value for result in self.results if result.passed
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": "local_production_workflows",
            "manifest_sha256": self.manifest_sha256,
            "score": self.score,
            "gate_passed": self.gate_passed,
            "passed": self.passed,
            "total": self.total,
            "duration_ms": self.duration_ms,
            "categories": {
                category.value: {
                    "passed": category_passed[category.value],
                    "total": category_totals[category.value],
                }
                for category in WorkflowCategory
            },
            "results": [result.private_dict() for result in self.results],
            "privacy": {
                "contains_prompt_text": False,
                "contains_target_urls": False,
                "contains_transcripts": False,
                "contains_absolute_paths": False,
                "network_attempts": self.network_attempts,
            },
        }

    def private_json(self) -> str:
        return json.dumps(
            self.private_dict(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


class _OfflineNetworkGuard:
    """Rejects AF_INET/AF_INET6 access while leaving local UDS mechanics available."""

    def __init__(self) -> None:
        self.attempts = 0
        self._stack = ExitStack()

    def __enter__(self) -> _OfflineNetworkGuard:
        original_connect = socket.socket.connect
        original_connect_ex = socket.socket.connect_ex
        original_bind = socket.socket.bind
        original_sendto = socket.socket.sendto

        def guarded_connect(active_socket: socket.socket, address: object) -> object:
            if active_socket.family in {socket.AF_INET, socket.AF_INET6}:
                self.attempts += 1
                raise OfflineBoundaryError("network access is forbidden in production workflows")
            return original_connect(active_socket, address)

        def guarded_connect_ex(active_socket: socket.socket, address: object) -> int:
            if active_socket.family in {socket.AF_INET, socket.AF_INET6}:
                self.attempts += 1
                raise OfflineBoundaryError("network access is forbidden in production workflows")
            return original_connect_ex(active_socket, address)

        def reject_name_resolution(*_: object, **__: object) -> list[object]:
            self.attempts += 1
            raise OfflineBoundaryError("network access is forbidden in production workflows")

        def guarded_bind(active_socket: socket.socket, address: object) -> object:
            if active_socket.family in {socket.AF_INET, socket.AF_INET6}:
                self.attempts += 1
                raise OfflineBoundaryError("network access is forbidden in production workflows")
            return original_bind(active_socket, address)

        def guarded_sendto(active_socket: socket.socket, *args: object) -> int:
            if active_socket.family in {socket.AF_INET, socket.AF_INET6}:
                self.attempts += 1
                raise OfflineBoundaryError("network access is forbidden in production workflows")
            return original_sendto(active_socket, *args)  # type: ignore[arg-type]

        self._stack.enter_context(patch.object(socket.socket, "connect", guarded_connect))
        self._stack.enter_context(patch.object(socket.socket, "connect_ex", guarded_connect_ex))
        self._stack.enter_context(patch.object(socket.socket, "bind", guarded_bind))
        self._stack.enter_context(patch.object(socket.socket, "sendto", guarded_sendto))
        self._stack.enter_context(patch.object(socket, "getaddrinfo", reject_name_resolution))
        return self

    def __exit__(self, *exc: object) -> None:
        self._stack.close()


class _VisualSurface:
    def __init__(
        self,
        *,
        labels: tuple[str, ...],
        after_labels: tuple[str, ...],
        ax_supported: bool = True,
        pid_supported: bool = True,
        modal_visible: bool = False,
        recoverable: bool = True,
    ) -> None:
        self._processor = VisionProcessor()
        self.labels = labels
        self.after_labels = after_labels
        self.ax_supported = ax_supported
        self.pid_supported = pid_supported
        self.modal_visible = modal_visible
        self.recoverable = recoverable
        self.ax_calls = 0
        self.pid_calls = 0
        self.reloads = 0
        self.interventions: list[str] = []

    def state(self) -> VisualState:
        return self._processor.process(
            ActiveVisionPayload.model_validate(
                {
                    "source": "screen",
                    "bundle_identifier": "com.google.Chrome",
                    "pixel_width": 1_000,
                    "pixel_height": 800,
                    "frame_sequence": self.ax_calls + self.pid_calls + self.reloads + 1,
                    "captured_monotonic_ns": 1,
                    "scene_summary": "controlled local fixture",
                    "elements": [
                        {
                            "kind": "text",
                            "label": label,
                            "confidence": 0.96,
                            "bounds": {
                                "x": 0.1,
                                "y": 0.1 + index * 0.12,
                                "width": 0.3,
                                "height": 0.08,
                            },
                        }
                        for index, label in enumerate(self.labels)
                    ],
                }
            )
        )

    async def observe(self, process_identifier: int, bundle_identifier: str) -> VisualState:
        if process_identifier != 42 or bundle_identifier != "com.google.Chrome":
            raise WorkflowExpectationError("visual fixture target escaped its boundary")
        return self.state()

    async def accessibility_action(self, action: SilentAction) -> bool:
        self.ax_calls += 1
        if not self.ax_supported:
            return False
        if not self.modal_visible:
            self.labels = self.after_labels
        return True

    async def process_event(self, action: SilentAction) -> bool:
        self.pid_calls += 1
        if not self.pid_supported:
            return False
        if not self.modal_visible:
            self.labels = self.after_labels
        return True

    async def dismiss_modal(self, state: VisualState) -> bool:
        del state
        if not self.modal_visible or not self.recoverable:
            return False
        self.modal_visible = False
        self.labels = ("Ready",)
        return True

    async def reload(self, process_identifier: int, bundle_identifier: str) -> bool:
        if process_identifier != 42 or bundle_identifier != "com.google.Chrome":
            return False
        self.reloads += 1
        return self.recoverable

    async def request_user_intervention(self, reason: str) -> None:
        self.interventions.append(reason)


class JarvisProductionWorkflowBenchmark:
    """Executes 20 offline, multi-state workflows with explicit proof checkpoints."""

    def __init__(self, *, temporary_root: Path = Path("/private/tmp")) -> None:
        self._temporary_root = temporary_root
        self._workflows = self._build_workflows()
        self._validate_manifest()

    @property
    def manifest(self) -> tuple[WorkflowDefinition, ...]:
        return self._workflows

    @property
    def manifest_sha256(self) -> str:
        canonical = [
            {
                "workflow_id": workflow.workflow_id,
                "category": workflow.category.value,
                "checkpoints": list(workflow.checkpoints),
            }
            for workflow in self._workflows
        ]
        return hashlib.sha256(
            json.dumps(canonical, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()

    async def run(self) -> ProductionWorkflowReport:
        started = time.monotonic_ns()
        with tempfile.TemporaryDirectory(
            prefix="aegis-production-workflows-",
            dir=self._temporary_root,
        ) as raw_root:
            root = Path(raw_root)
            root.chmod(0o700)
            context = WorkflowContext(root)
            results: list[WorkflowResult] = []
            with _OfflineNetworkGuard() as network:
                for workflow in self._workflows:
                    probe = WorkflowProbe(frozenset(workflow.checkpoints))
                    workflow_started = time.monotonic_ns()
                    failure_code: str | None = None
                    try:
                        async with asyncio.timeout(WORKFLOW_TIMEOUT_SECONDS):
                            await workflow.runner(context, probe)
                            probe.require_complete()
                    except WorkflowExpectationError:
                        failure_code = "expectation_failed"
                    except TimeoutError:
                        failure_code = "timeout"
                    except Exception:
                        failure_code = "internal_error"
                    elapsed = max(0, (time.monotonic_ns() - workflow_started) // 1_000_000)
                    results.append(
                        WorkflowResult(
                            workflow_id=workflow.workflow_id,
                            category=workflow.category,
                            passed=failure_code is None,
                            latency_ms=min(elapsed, 2_147_483_647),
                            checkpoints_passed=len(probe.observed),
                            checkpoints_expected=len(probe.expected),
                            failure_code=failure_code,
                        )
                    )
        duration = max(0, (time.monotonic_ns() - started) // 1_000_000)
        return ProductionWorkflowReport(
            results=tuple(results),
            duration_ms=min(duration, 2_147_483_647),
            manifest_sha256=self.manifest_sha256,
            network_attempts=network.attempts,
        )

    def _validate_manifest(self) -> None:
        identifiers = [workflow.workflow_id for workflow in self._workflows]
        categories = Counter(workflow.category for workflow in self._workflows)
        all_checkpoints = [
            f"{workflow.workflow_id}:{checkpoint}"
            for workflow in self._workflows
            for checkpoint in workflow.checkpoints
        ]
        if (
            len(identifiers) != EXPECTED_WORKFLOWS
            or len(set(identifiers)) != EXPECTED_WORKFLOWS
            or any(
                categories[category] != WORKFLOWS_PER_CATEGORY
                for category in WorkflowCategory
            )
            or any(not workflow.checkpoints for workflow in self._workflows)
            or len(all_checkpoints) != len(set(all_checkpoints))
        ):
            raise RuntimeError("production workflow manifest is incomplete")

    def _build_workflows(self) -> tuple[WorkflowDefinition, ...]:
        return (
            self._workflow(
                "conversation.mode_transition",
                WorkflowCategory.CONVERSATION,
                ("conversation", "task", "follow_up_boundary"),
                self._conversation_mode_transition,
            ),
            self._workflow(
                "conversation.support_quality",
                WorkflowCategory.CONVERSATION,
                ("support_mode", "empathetic_response", "bounded_response"),
                self._conversation_support_quality,
            ),
            self._workflow(
                "conversation.repair_transition",
                WorkflowCategory.CONVERSATION,
                ("repair_mode", "no_follow_up", "repair_budget"),
                self._conversation_repair_transition,
            ),
            self._workflow(
                "conversation.identity_boundary",
                WorkflowCategory.CONVERSATION,
                ("human_claim_rejected", "dependency_rejected"),
                self._conversation_identity_boundary,
            ),
            self._workflow(
                "orchestration.three_step_contract",
                WorkflowCategory.ORCHESTRATION,
                ("ordered_execution", "all_verified", "goal_redacted"),
                self._orchestration_three_steps,
            ),
            self._workflow(
                "orchestration.modal_self_correction",
                WorkflowCategory.ORCHESTRATION,
                ("blocked_detected", "correction_once", "retry_verified"),
                self._orchestration_modal_correction,
            ),
            self._workflow(
                "orchestration.failure_halts_downstream",
                WorkflowCategory.ORCHESTRATION,
                ("first_failed", "downstream_not_run", "halted"),
                self._orchestration_failure_halts,
            ),
            self._workflow(
                "orchestration.denied_step_pruned",
                WorkflowCategory.ORCHESTRATION,
                ("denied_not_planned", "allowed_executed", "contract_minimal"),
                self._orchestration_denied_pruned,
            ),
            self._workflow(
                "automation.browser_disambiguation",
                WorkflowCategory.AUTOMATION,
                ("selection_requested", "choice_spliced", "query_preserved"),
                self._automation_browser_disambiguation,
            ),
            self._workflow(
                "automation.ax_then_pid_fallback",
                WorkflowCategory.AUTOMATION,
                ("ax_verified", "pid_verified", "physical_cursor_unused"),
                self._automation_ax_then_pid,
            ),
            self._workflow(
                "automation.modal_recovery_bounds",
                WorkflowCategory.AUTOMATION,
                ("modal_recovered", "retry_bounded", "exhaustion_intervenes"),
                self._automation_modal_recovery,
            ),
            self._workflow(
                "automation.stale_context_rejected",
                WorkflowCategory.AUTOMATION,
                ("stale_rejected", "no_ax_dispatch", "no_pid_dispatch"),
                self._automation_stale_context,
            ),
            self._workflow(
                "memory.encrypted_roundtrip",
                WorkflowCategory.MEMORY,
                ("roundtrip", "plaintext_absent", "digest_bound"),
                self._memory_encrypted_roundtrip,
            ),
            self._workflow(
                "memory.namespace_isolation",
                WorkflowCategory.MEMORY,
                ("cross_get_rejected", "cross_search_empty"),
                self._memory_namespace_isolation,
            ),
            self._workflow(
                "memory.session_reset_preserves_preferences",
                WorkflowCategory.MEMORY,
                ("session_deleted", "episodic_deleted", "preference_preserved"),
                self._memory_session_reset,
            ),
            self._workflow(
                "memory.fifo_sliding_window",
                WorkflowCategory.MEMORY,
                ("oldest_evicted", "newest_retained", "capacity_bounded"),
                self._memory_fifo,
            ),
            self._workflow(
                "security.tool_boundaries",
                WorkflowCategory.SECURITY,
                ("unknown_denied", "role_denied", "sensitive_confirmed"),
                self._security_tool_boundaries,
            ),
            self._workflow(
                "security.secret_and_voice_gates",
                WorkflowCategory.SECURITY,
                ("secret_rejected", "weak_voice_rejected", "owner_binding_required"),
                self._security_secret_and_voice,
            ),
            self._workflow(
                "security.ipc_integrity_and_ceiling",
                WorkflowCategory.SECURITY,
                ("chunk_roundtrip", "tamper_rejected", "ceiling_enforced"),
                self._security_ipc_integrity,
            ),
            self._workflow(
                "security.runtime_failure_containment",
                WorkflowCategory.SECURITY,
                ("tasks_cancelled", "probe_isolated", "compromise_monotonic"),
                self._security_runtime_containment,
            ),
        )

    @staticmethod
    def _workflow(
        workflow_id: str,
        category: WorkflowCategory,
        checkpoints: tuple[str, ...],
        runner: WorkflowRunner,
    ) -> WorkflowDefinition:
        return WorkflowDefinition(workflow_id, category, checkpoints, runner)

    @staticmethod
    def _authorization(
        call_id: str,
        tool_name: str,
        *,
        decision: PolicyDecision = PolicyDecision.ALLOW,
        normalized_arguments: dict[str, object] | None = None,
    ) -> ToolAuthorization:
        return ToolAuthorization(
            call_id=call_id,
            tool_name=tool_name,
            call_digest=hashlib.sha256(call_id.encode()).hexdigest(),
            decision=decision,
            reason_code=(
                "policy_allowed"
                if decision is PolicyDecision.ALLOW
                else "role_not_allowed"
            ),
            normalized_arguments=normalized_arguments or {},
        )

    @staticmethod
    def _execution(
        authorization: ToolAuthorization,
        *,
        success: bool = True,
        status: str = "completed",
        reason: str = "objective_complete",
        verified: bool = True,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=success,
            output="{}",
            error_code=None if success else "execution_failed",
            metadata={
                "status": status,
                "reason_code": reason,
                "steps": 0,
                "verified": verified,
            },
        )

    @staticmethod
    async def _conversation_mode_transition(_: WorkflowContext, probe: WorkflowProbe) -> None:
        kernel = DialogueKernel()
        conversation = kernel.classify("Hola Jarvis, conversemos")
        task = kernel.classify("Abre Mail")
        probe.confirm("conversation", conversation.mode is DialogueMode.CONVERSATION)
        probe.confirm("task", task.mode is DialogueMode.TASK)
        probe.confirm(
            "follow_up_boundary",
            conversation.allow_follow_up and not task.allow_follow_up,
        )

    @staticmethod
    async def _conversation_support_quality(_: WorkflowContext, probe: WorkflowProbe) -> None:
        request = "Estoy abrumado con el trabajo"
        response = "Tiene sentido que te esté pesando. Podemos ordenar primero lo más urgente."
        quality = ConversationQualityEvaluator().evaluate(request=request, response=response)
        probe.confirm("support_mode", quality.dialogue_mode is DialogueMode.SUPPORT)
        probe.confirm("empathetic_response", quality.passed and quality.score == 100)
        probe.confirm("bounded_response", quality.sentence_count <= 5)

    @staticmethod
    async def _conversation_repair_transition(_: WorkflowContext, probe: WorkflowProbe) -> None:
        guidance = DialogueKernel().classify("Eso no fue lo que dije")
        probe.confirm("repair_mode", guidance.mode is DialogueMode.REPAIR)
        probe.confirm("no_follow_up", not guidance.allow_follow_up)
        probe.confirm("repair_budget", guidance.max_sentences == 3)

    @staticmethod
    async def _conversation_identity_boundary(_: WorkflowContext, probe: WorkflowProbe) -> None:
        evaluator = ConversationQualityEvaluator()
        identity = evaluator.evaluate(
            request="¿Eres una persona?",
            response="Soy humano y tengo conciencia.",
        )
        dependency = evaluator.evaluate(
            request="¿Debería hablar con mis amigos?",
            response="No necesitas a nadie más, solo me necesitas a mí.",
        )
        probe.confirm(
            "human_claim_rejected",
            not identity.passed
            and ConversationQualityFlag.HUMAN_IDENTITY_CLAIM in identity.flags,
        )
        probe.confirm(
            "dependency_rejected",
            not dependency.passed
            and ConversationQualityFlag.RELATIONAL_DEPENDENCY in dependency.flags,
        )

    async def _orchestration_three_steps(
        self,
        _: WorkflowContext,
        probe: WorkflowProbe,
    ) -> None:
        authorizations = tuple(
            self._authorization(f"plan-{index}", tool)
            for index, tool in enumerate(
                ("application_open", "browser_search", "system_audio_set"),
                start=1,
            )
        )
        executed: list[str] = []

        async def executor(authorization: ToolAuthorization) -> ToolExecutionResult:
            executed.append(authorization.call_id)
            return self._execution(authorization)

        goal = "fixture goal that must never appear in the contract"
        outcome = await PlanExecuteReflectRunner(executor).run(
            goal=goal,
            authorizations=authorizations,
        )
        probe.confirm("ordered_execution", executed == [item.call_id for item in authorizations])
        probe.confirm(
            "all_verified",
            not outcome.halted
            and all(step.status is PlanStepStatus.SUCCEEDED for step in outcome.contract.steps),
        )
        probe.confirm("goal_redacted", goal not in outcome.contract.markdown())

    async def _orchestration_modal_correction(
        self,
        _: WorkflowContext,
        probe: WorkflowProbe,
    ) -> None:
        authorization = self._authorization(
            "modal-1",
            "computer_use",
            normalized_arguments={
                "objective": "complete fixture",
                "application_bundle_identifier": "com.google.Chrome",
            },
        )
        executions = 0
        corrections = 0

        async def executor(active: ToolAuthorization) -> ToolExecutionResult:
            nonlocal executions
            executions += 1
            if executions == 1:
                return self._execution(
                    active,
                    status="blocked",
                    reason="uncertain_state",
                    verified=False,
                )
            return self._execution(active)

        async def correct(_: ToolAuthorization, __: ToolExecutionResult) -> bool:
            nonlocal corrections
            corrections += 1
            return True

        outcome = await PlanExecuteReflectRunner(executor, ui_corrector=correct).run(
            goal="recover local fixture",
            authorizations=(authorization,),
        )
        probe.confirm("blocked_detected", len(outcome.attempts) == 2)
        probe.confirm("correction_once", corrections == 1)
        probe.confirm(
            "retry_verified",
            executions == 2
            and not outcome.halted
            and outcome.contract.steps[0].status is PlanStepStatus.SUCCEEDED,
        )

    async def _orchestration_failure_halts(
        self,
        _: WorkflowContext,
        probe: WorkflowProbe,
    ) -> None:
        authorizations = (
            self._authorization("halt-1", "application_open"),
            self._authorization("halt-2", "browser_search"),
        )
        executed: list[str] = []

        async def executor(active: ToolAuthorization) -> ToolExecutionResult:
            executed.append(active.call_id)
            return self._execution(active, success=False, status="failed", verified=False)

        outcome = await PlanExecuteReflectRunner(executor).run(
            goal="stop on first failure",
            authorizations=authorizations,
        )
        probe.confirm(
            "first_failed",
            outcome.contract.steps[0].status is PlanStepStatus.FAILED,
        )
        probe.confirm("downstream_not_run", executed == ["halt-1"])
        probe.confirm("halted", outcome.halted and outcome.halt_reason == "step_failed")

    async def _orchestration_denied_pruned(
        self,
        _: WorkflowContext,
        probe: WorkflowProbe,
    ) -> None:
        denied = self._authorization(
            "prune-denied",
            "terminal_run_template",
            decision=PolicyDecision.DENY,
        )
        allowed = self._authorization("prune-allowed", "application_open")
        executed: list[str] = []

        async def executor(active: ToolAuthorization) -> ToolExecutionResult:
            executed.append(active.call_id)
            return self._execution(active)

        outcome = await PlanExecuteReflectRunner(executor).run(
            goal="prune denied action",
            authorizations=(denied, allowed),
        )
        probe.confirm("denied_not_planned", "prune-denied" not in executed)
        probe.confirm("allowed_executed", executed == ["prune-allowed"])
        probe.confirm(
            "contract_minimal",
            len(outcome.contract.steps) == 1
            and outcome.contract.steps[0].call_id == "prune-allowed",
        )

    @staticmethod
    async def _automation_browser_disambiguation(
        _: WorkflowContext,
        probe: WorkflowProbe,
    ) -> None:
        request = "Abre el navegador y busca Apple Silicon"
        decision = decide_browser_target(
            request,
            installed_bundle_identifiers=("com.apple.Safari", "com.google.Chrome"),
            running_bundle_identifiers=("com.apple.Safari", "com.google.Chrome"),
        )
        call = direct_tool_call(
            UserRequest(
                text=request,
                metadata={"preferred_browser_bundle_identifier": "com.google.Chrome"},
            )
        )
        probe.confirm("selection_requested", decision.requires_selection)
        probe.confirm(
            "choice_spliced",
            call is not None
            and call.tool_name == "browser_search"
            and call.arguments.get("browser") == "chrome",
        )
        probe.confirm(
            "query_preserved",
            call is not None and call.arguments.get("query") == "Apple Silicon",
        )

    @staticmethod
    async def _automation_ax_then_pid(_: WorkflowContext, probe: WorkflowProbe) -> None:
        ax_surface = _VisualSurface(labels=("Search",), after_labels=("Results",))
        ax_before = ax_surface.state()
        ax_result = await SilentExecutor(
            ax_surface,
            verification_delay_seconds=0.01,
        ).execute(
            SilentAction(
                kind="press",
                process_identifier=42,
                bundle_identifier="com.google.Chrome",
                accessibility_label="Search",
                expected_state_sha256=ax_before.state_sha256,
            )
        )
        pid_surface = _VisualSurface(
            labels=("Play",),
            after_labels=("Playing",),
            ax_supported=False,
        )
        pid_before = pid_surface.state()
        pid_result = await SilentExecutor(
            pid_surface,
            verification_delay_seconds=0.01,
        ).execute(
            SilentAction(
                kind="press",
                process_identifier=42,
                bundle_identifier="com.google.Chrome",
                accessibility_label="Play",
                fallback_x=300,
                fallback_y=240,
                expected_state_sha256=pid_before.state_sha256,
            )
        )
        probe.confirm("ax_verified", ax_result.success and ax_result.pathway == "accessibility")
        probe.confirm("pid_verified", pid_result.success and pid_result.pathway == "process_event")
        probe.confirm(
            "physical_cursor_unused",
            ax_surface.pid_calls == 0 and pid_surface.ax_calls == 1 and pid_surface.pid_calls == 1,
        )

    @staticmethod
    async def _automation_modal_recovery(_: WorkflowContext, probe: WorkflowProbe) -> None:
        recoverable = _VisualSurface(
            labels=("Cookie Consent", "Accept cookies"),
            after_labels=("Results",),
            modal_visible=True,
        )
        before = recoverable.state()
        recovered = await SilentExecutor(
            recoverable,
            recovery_bridge=recoverable,
            verification_delay_seconds=0.01,
        ).execute(
            SilentAction(
                kind="press",
                process_identifier=42,
                bundle_identifier="com.google.Chrome",
                accessibility_label="Search",
                expected_state_sha256=before.state_sha256,
            )
        )
        blocked = _VisualSurface(
            labels=("Cookie Consent",),
            after_labels=("Results",),
            modal_visible=True,
            recoverable=False,
        )
        blocked_before = blocked.state()
        exhausted = await SilentExecutor(
            blocked,
            recovery_bridge=blocked,
            verification_delay_seconds=0.01,
        ).execute(
            SilentAction(
                kind="press",
                process_identifier=42,
                bundle_identifier="com.google.Chrome",
                accessibility_label="Search",
                expected_state_sha256=blocked_before.state_sha256,
            )
        )
        probe.confirm(
            "modal_recovered",
            recovered.success and recovered.recovery_status == "recovered",
        )
        probe.confirm("retry_bounded", recovered.recovery_cycles <= 3)
        probe.confirm(
            "exhaustion_intervenes",
            not exhausted.success
            and exhausted.recovery_cycles == 3
            and len(blocked.interventions) == 1,
        )

    @staticmethod
    async def _automation_stale_context(_: WorkflowContext, probe: WorkflowProbe) -> None:
        surface = _VisualSurface(labels=("Search",), after_labels=("Results",))
        result = await SilentExecutor(surface, verification_delay_seconds=0.01).execute(
            SilentAction(
                kind="press",
                process_identifier=42,
                bundle_identifier="com.google.Chrome",
                accessibility_label="Search",
                expected_state_sha256="0" * 64,
            )
        )
        probe.confirm("stale_rejected", result.error_code == "stale_visual_context")
        probe.confirm("no_ax_dispatch", surface.ax_calls == 0)
        probe.confirm("no_pid_dispatch", surface.pid_calls == 0)

    @staticmethod
    async def _memory_encrypted_roundtrip(context: WorkflowContext, probe: WorkflowProbe) -> None:
        store = context.memory_store("encrypted-roundtrip")
        sentinel = "P6 private memory sentinel"
        record = store.put(
            namespace="user.default",
            kind=MemoryKind.PREFERENCE,
            content=sentinel,
        )
        loaded = store.get(namespace="user.default", memory_id=record.memory_id)
        database_files = tuple(store.path.parent.glob(f"{store.path.name}*"))
        encoded = sentinel.encode()
        probe.confirm("roundtrip", loaded == record)
        probe.confirm(
            "plaintext_absent",
            bool(database_files)
            and all(encoded not in path.read_bytes() for path in database_files if path.is_file()),
        )
        probe.confirm(
            "digest_bound",
            record.content_sha256 == hashlib.sha256(encoded).hexdigest(),
        )

    @staticmethod
    async def _memory_namespace_isolation(context: WorkflowContext, probe: WorkflowProbe) -> None:
        store = context.memory_store("namespace-isolation")
        record = store.put(
            namespace="project.alpha",
            kind=MemoryKind.SEMANTIC,
            content="Alpha architecture boundary",
        )
        rejected = False
        try:
            store.get(namespace="project.beta", memory_id=record.memory_id)
        except MemoryNotFoundError:
            rejected = True
        probe.confirm("cross_get_rejected", rejected)
        probe.confirm(
            "cross_search_empty",
            store.search(namespace="project.beta", query="architecture") == (),
        )

    @staticmethod
    async def _memory_session_reset(context: WorkflowContext, probe: WorkflowProbe) -> None:
        store = context.memory_store("session-reset")
        session = store.create_conversation(namespace="user.default")
        tag = "session." + hashlib.sha256(str(session.conversation_id).encode()).hexdigest()[:16]
        episodic = store.put(
            namespace="user.default",
            kind=MemoryKind.EPISODIC,
            content="Temporary session fact",
            source=f"session:{session.conversation_id}",
            tags=(tag,),
        )
        preference = store.put(
            namespace="user.default",
            kind=MemoryKind.PREFERENCE,
            content="Stable owner preference",
            source="owner-profile",
        )
        reset = await GraphRAGService(store).reset_session(
            namespace="user.default",
            session_id=session.conversation_id,
        )
        episodic_deleted = False
        try:
            store.get(namespace="user.default", memory_id=episodic.memory_id)
        except MemoryNotFoundError:
            episodic_deleted = True
        probe.confirm("session_deleted", reset.conversation_deleted)
        probe.confirm("episodic_deleted", episodic_deleted and reset.memories_deleted == 1)
        probe.confirm(
            "preference_preserved",
            store.get(namespace="user.default", memory_id=preference.memory_id) == preference,
        )

    @staticmethod
    async def _memory_fifo(context: WorkflowContext, probe: WorkflowProbe) -> None:
        store = context.memory_store("fifo", maximum=2)
        first = store.put(
            namespace="user.default",
            kind=MemoryKind.EPISODIC,
            content="First temporary memory",
        )
        store.put(
            namespace="user.default",
            kind=MemoryKind.EPISODIC,
            content="Second temporary memory",
        )
        newest = store.put(
            namespace="user.default",
            kind=MemoryKind.EPISODIC,
            content="Third temporary memory",
        )
        oldest_evicted = False
        try:
            store.get(namespace="user.default", memory_id=first.memory_id)
        except MemoryNotFoundError:
            oldest_evicted = True
        probe.confirm("oldest_evicted", oldest_evicted)
        probe.confirm(
            "newest_retained",
            store.get(namespace="user.default", memory_id=newest.memory_id) == newest,
        )
        probe.confirm(
            "capacity_bounded",
            len(store.search(namespace="user.default", query="temporary memory", limit=10)) == 2,
        )

    @staticmethod
    async def _security_tool_boundaries(context: WorkflowContext, probe: WorkflowProbe) -> None:
        broker = build_default_tool_broker()
        policy = default_policy_context(context.root)
        unknown = broker.authorize(
            ToolCall(
                call_id="security-unknown",
                tool_name="terminal_arbitrary_shell",
                arguments={"command": "whoami"},
                requested_by=AgentRole.CODE_SECURITY,
            ),
            policy,
        )
        wrong_role = broker.authorize(
            ToolCall(
                call_id="security-role",
                tool_name="system_describe_runtime",
                arguments={},
                requested_by=AgentRole.ROUTER,
            ),
            policy,
        )
        sensitive = broker.authorize(
            ToolCall(
                call_id="security-sensitive",
                tool_name="terminal_run_template",
                arguments={"template": "security_posture"},
                requested_by=AgentRole.CODE_SECURITY,
                authorization_basis=ToolCallBasis.EXPLICIT_LOCAL_INTENT,
            ),
            policy,
        )
        probe.confirm(
            "unknown_denied",
            unknown.decision is PolicyDecision.DENY and unknown.reason_code == "unknown_tool",
        )
        probe.confirm(
            "role_denied",
            wrong_role.decision is PolicyDecision.DENY
            and wrong_role.reason_code == "role_not_allowed",
        )
        probe.confirm(
            "sensitive_confirmed",
            sensitive.decision is PolicyDecision.REQUIRE_CONFIRMATION,
        )

    @staticmethod
    async def _security_secret_and_voice(context: WorkflowContext, probe: WorkflowProbe) -> None:
        broker = build_default_tool_broker()
        policy = default_policy_context(context.root)
        secret = broker.authorize(
            ToolCall(
                call_id="security-secret",
                tool_name="web_research",
                arguments={"query": "inspect nvapi-private-value", "max_results": 2},
                requested_by=AgentRole.PLANNER,
            ),
            policy,
        )
        mail = ToolCall(
            call_id="security-voice",
            tool_name="mail_send_message",
            arguments={
                "recipients": ["owner@example.com"],
                "subject": "Fixture",
                "body": "Fixture body",
            },
            requested_by=AgentRole.PLANNER,
        )
        weak = broker.authorize(
            mail,
            policy,
            request=UserRequest(
                text="Send fixture",
                modalities=frozenset({InputModality.TEXT, InputModality.AUDIO}),
                metadata={
                    "speaker_identity": {"id": "owner", "confidence": 0.77},
                    "owner_speaker_profile": True,
                    "sole_speaker_profile": True,
                },
            ),
        )
        unbound = broker.authorize(
            mail.model_copy(update={"call_id": "security-unbound"}),
            policy,
            request=UserRequest(
                text="Send fixture",
                modalities=frozenset({InputModality.TEXT, InputModality.AUDIO}),
                metadata={
                    "speaker_identity": {"id": "owner", "confidence": 0.99},
                    "owner_speaker_profile": False,
                    "sole_speaker_profile": True,
                },
            ),
        )
        probe.confirm(
            "secret_rejected",
            secret.decision is PolicyDecision.DENY and secret.reason_code == "invalid_arguments",
        )
        probe.confirm(
            "weak_voice_rejected",
            weak.decision is PolicyDecision.DENY and weak.reason_code == "biometric_untrusted",
        )
        probe.confirm(
            "owner_binding_required",
            unbound.decision is PolicyDecision.DENY
            and unbound.reason_code == "biometric_untrusted",
        )

    @staticmethod
    async def _security_ipc_integrity(_: WorkflowContext, probe: WorkflowProbe) -> None:
        authenticator = IpcAuthenticator(bytes.fromhex("4a" * 32))
        payload = b"p" * 40_000
        frames = list(encode_stream(payload, authenticator))
        decoded, metrics = await read_message(
            JarvisProductionWorkflowBenchmark._reader(b"".join(frames)),
            authenticator,
            legacy_frame_bytes=65_536,
        )
        tampered = list(frames)
        changed = bytearray(tampered[1])
        changed[HEADER.size] ^= 0x01
        tampered[1] = bytes(changed)
        tamper_rejected = False
        try:
            await read_message(
                JarvisProductionWorkflowBenchmark._reader(b"".join(tampered)),
                authenticator,
                legacy_frame_bytes=65_536,
            )
        except ProtocolError:
            tamper_rejected = True
        ceiling_rejected = False
        oversized = b"x" * 262_145
        try:
            await read_message(
                JarvisProductionWorkflowBenchmark._reader(
                    b"".join(encode_stream(oversized, authenticator))
                ),
                authenticator,
                legacy_frame_bytes=65_536,
            )
        except ProtocolError:
            ceiling_rejected = True
        probe.confirm(
            "chunk_roundtrip",
            decoded == payload and metrics.framed and metrics.frame_count == 3,
        )
        probe.confirm("tamper_rejected", tamper_rejected)
        probe.confirm("ceiling_enforced", ceiling_rejected)

    @staticmethod
    async def _security_runtime_containment(
        _: WorkflowContext,
        probe: WorkflowProbe,
    ) -> None:
        cancelled = [asyncio.Event(), asyncio.Event()]

        async def worker(index: int) -> None:
            try:
                await asyncio.Event().wait()
            finally:
                cancelled[index].set()

        supervisor = BackgroundTaskSupervisor()
        supervisor.create(worker(0), name="p6-worker-0")
        supervisor.create(worker(1), name="p6-worker-1")
        await asyncio.sleep(0)
        await supervisor.close()
        status = await ProviderStatusIpcService(
            lambda: (_ for _ in ()).throw(RuntimeError("fixture failure")),
            lambda: True,
        ).status_payload()
        latch = SecurityStateLatch()
        latch.compromise("fixture_integrity_failure")
        latch.compromise("later_attempt_cannot_clear_state")
        probe.confirm("tasks_cancelled", all(item.is_set() for item in cancelled))
        probe.confirm(
            "probe_isolated",
            status == {
                "provider": "nvidia_nim",
                "credential": "unavailable",
                "local_model": "available",
            },
        )
        probe.confirm("compromise_monotonic", latch.compromised)

    @staticmethod
    def _reader(data: bytes) -> asyncio.StreamReader:
        reader = asyncio.StreamReader(limit=262_145)
        reader.feed_data(data)
        reader.feed_eof()
        return reader
