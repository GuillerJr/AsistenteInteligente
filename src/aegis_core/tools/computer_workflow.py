"""Bounded multi-app progress journal for the existing graph, never an executor.

Persist only this JSON in the encrypted conversation interruption envelope. Runtime
must obtain fresh broker authorization before begin; restored progress grants no
permission. An interrupted or partially observed mutation cannot be replayed.
"""

from __future__ import annotations

import hashlib
import re
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aegis_core.contracts import (
    AgentRole,
    PolicyDecision,
    ToolAuthorization,
    ToolCall,
    ToolExecutionResult,
)
from aegis_core.request_intent import mask_quoted_text
from aegis_core.tools.computer import (
    ComputerUseController,
    ComputerUseReport,
    is_restricted_computer_bundle,
)
from aegis_core.tools.computer_postconditions import ComputerPostcondition, postconditions_are_bound
from aegis_core.tools.verification import result_is_verified

_MAX_SPEC_BYTES = 8_192
_MAX_STATE_BYTES = 12_288
_APPLICATION_ALIASES = {
    "com.apple.safari": ("safari",),
    "com.google.chrome": ("google chrome", "chrome"),
    "org.mozilla.firefox": ("firefox",),
    "com.microsoft.edgemac": ("microsoft edge", "edge"),
    "com.apple.textedit": ("textedit",),
    "com.apple.notes": ("notas", "notes"),
    "com.apple.preview": ("vista previa", "preview"),
    "com.spotify.client": ("spotify",),
    "com.apple.dt.xcode": ("xcode",),
    "ru.keepcoder.telegram": ("telegram",),
}


class ComputerWorkflowStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ordinal: int = Field(ge=1, le=4, strict=True)
    application_bundle_identifier: str = Field(
        min_length=3,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]+$",
    )
    objective: str = Field(min_length=1, max_length=1_000)
    max_steps: int = Field(default=8, ge=1, le=12, strict=True)
    postconditions: tuple[ComputerPostcondition, ...] = Field(default=(), max_length=6)

    @model_validator(mode="after")
    def bounded_verifiable_step(self) -> ComputerWorkflowStep:
        if not self.objective.isprintable() or self.objective != " ".join(self.objective.split()):
            raise ValueError("workflow objective must be normalized printable text")
        if is_restricted_computer_bundle(self.application_bundle_identifier):
            raise ValueError("workflow application is restricted")
        if not postconditions_are_bound(self.postconditions, self.objective):
            raise ValueError("workflow postconditions do not match its objective")
        if not self.postconditions and not ComputerUseController._is_literal_gesture(
            self.objective
        ):
            raise ValueError("workflow step requires a verifiable postcondition")
        return self

    def arguments(self) -> dict:
        return {
            "objective": self.objective,
            "application_bundle_identifier": self.application_bundle_identifier,
            "max_steps": self.max_steps,
            "postconditions": [
                condition.model_dump(mode="json") for condition in self.postconditions
            ],
            "workflow_steps": [],
        }


class ComputerWorkflowSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    goal_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    steps: tuple[ComputerWorkflowStep, ...] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def ordered_bounded_steps(self) -> ComputerWorkflowSpec:
        if tuple(step.ordinal for step in self.steps) != tuple(range(1, len(self.steps) + 1)):
            raise ValueError("workflow steps must be contiguous and ordered")
        if sum(step.max_steps for step in self.steps) > 24:
            raise ValueError("workflow action budget exceeds 24")
        if len(self.model_dump_json().encode()) > _MAX_SPEC_BYTES:
            raise ValueError("workflow specification exceeds its byte budget")
        return self


class ComputerWorkflowProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ordinal: int = Field(ge=1, le=4, strict=True)
    status: Literal[
        "pending", "executing", "verified", "partial", "blocked", "intervention_required"
    ]
    request_id: UUID | None = None
    call_id: str | None = Field(default=None, min_length=1, max_length=256)
    call_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    report: ComputerUseReport | None = None
    reason_code: Literal[
        "pending",
        "executing",
        "verified",
        "partial",
        "blocked",
        "interrupted",
        "unverifiable_result",
        "result_mismatch",
        "cancelled",
    ]


class ComputerWorkflowState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    workflow_id: UUID = Field(default_factory=uuid4)
    conversation_id: UUID
    source_request_id: UUID
    spec: ComputerWorkflowSpec
    progress: tuple[ComputerWorkflowProgress, ...] = Field(min_length=1, max_length=4)
    revision: int = Field(default=0, ge=0, le=32, strict=True)
    cancelled: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def valid_progress(self) -> ComputerWorkflowState:
        if len(self.progress) != len(self.spec.steps):
            raise ValueError("workflow progress must match its specification")
        incomplete = False
        call_ids = set()
        for index, progress in enumerate(self.progress):
            if progress.ordinal != index + 1:
                raise ValueError("workflow progress is not ordered")
            if incomplete and progress.status != "pending":
                raise ValueError("later workflow step cannot precede an incomplete step")
            incomplete = incomplete or progress.status != "verified"
            if progress.status == "pending":
                if any(
                    value is not None
                    for value in (
                        progress.request_id,
                        progress.call_id,
                        progress.call_digest,
                        progress.report,
                    )
                ):
                    raise ValueError("pending workflow progress cannot contain execution state")
            else:
                if (
                    progress.request_id is None
                    or progress.call_id is None
                    or progress.call_digest is None
                ):
                    raise ValueError("observed workflow step requires runtime call identity")
                if progress.call_id in call_ids:
                    raise ValueError("workflow call IDs cannot be reused")
                call_ids.add(progress.call_id)
            if progress.report is not None and (
                progress.report.application_bundle_identifier
                != self.spec.steps[index].application_bundle_identifier
            ):
                raise ValueError("workflow report belongs to a different application")
            if progress.status == "verified" and (
                progress.report is None
                or progress.report.status != "completed"
                or not progress.report.postcondition_checks
                or not all(check.satisfied for check in progress.report.postcondition_checks)
            ):
                raise ValueError("workflow verification requires observed postconditions")
            if progress.status == "verified":
                expected = [condition.kind for condition in self.spec.steps[index].postconditions]
                expected = expected or ["action_performed"]
                if [check.kind for check in progress.report.postcondition_checks] != expected:
                    raise ValueError("workflow observation checks do not match its specification")
        if len(self.model_dump_json().encode()) > _MAX_STATE_BYTES:
            raise ValueError("workflow state exceeds its byte budget")
        return self


def create_workflow(
    *,
    conversation_id: UUID,
    request_id: UUID,
    spec: ComputerWorkflowSpec,
) -> ComputerWorkflowState:
    return ComputerWorkflowState(
        conversation_id=conversation_id,
        source_request_id=request_id,
        spec=spec,
        progress=tuple(
            ComputerWorkflowProgress(
                ordinal=step.ordinal,
                status="pending",
                reason_code="pending",
            )
            for step in spec.steps
        ),
    )


def validate_workflow_request(spec: ComputerWorkflowSpec, request_text: str) -> bool:
    """Only explicit ordered app instructions can seed a workflow, never screen-derived plans.

    This intentionally does not invent semantic decomposition. Each bounded objective
    must be an actual user instruction in order, inside its named app section. A model
    may supply AX postconditions for those literal instructions; the owner still approves
    every concrete call separately.
    """
    if spec.goal_sha256 != hashlib.sha256(request_text.encode()).hexdigest():
        return False
    folded = " ".join(request_text.casefold().split())
    instructions = mask_quoted_text(folded)
    app_mentions = []
    for step in spec.steps:
        bundle = step.application_bundle_identifier.casefold()
        aliases = (bundle, *_APPLICATION_ALIASES.get(bundle, ()))
        for alias in aliases:
            app_mentions.extend(
                (match.start(), bundle)
                for match in re.finditer(
                    rf"(?<!\w){re.escape(alias)}(?!\w)",
                    instructions,
                )
            )
    app_mentions = sorted(set(app_mentions))
    end = 0
    for step in spec.steps:
        literal = " ".join(step.objective.casefold().split())
        start = folded.find(literal, end)
        if start < 0:
            return False
        if not instructions[start : start + min(5, len(literal))].strip():
            return False
        applicable = [bundle for position, bundle in app_mentions if position <= start]
        if not applicable or applicable[-1] != step.application_bundle_identifier.casefold():
            return False
        if not postconditions_are_bound(step.postconditions, request_text):
            return False
        end = start + len(literal)
    return True


def next_pending_step(state: ComputerWorkflowState) -> ComputerWorkflowStep | None:
    """A proposed next step, not an authorization or an automatic resumption."""
    if state.cancelled:
        return None
    for step, progress in zip(state.spec.steps, state.progress, strict=True):
        if progress.status == "verified":
            continue
        return step if progress.status == "pending" else None
    return None


def workflow_step_call(state: ComputerWorkflowState, request_id: UUID) -> ToolCall:
    """Propose one exact step; this carries no execution grant from the journal."""
    step = next_pending_step(state)
    if step is None:
        raise ValueError("workflow requires intervention or has no remaining step")
    return ToolCall(
        call_id=f"workflow-{state.workflow_id}-{step.ordinal}-{request_id}",
        tool_name="computer_use",
        requested_by=AgentRole.PLANNER,
        arguments=step.arguments(),
    )


def _replace_progress(
    state: ComputerWorkflowState,
    progress: ComputerWorkflowProgress,
) -> ComputerWorkflowState:
    updated = list(state.progress)
    updated[progress.ordinal - 1] = progress
    return ComputerWorkflowState.model_validate(
        {
            **state.model_dump(mode="json"),
            "progress": [item.model_dump(mode="json") for item in updated],
            "revision": state.revision + 1,
        }
    )


def begin_workflow_step(
    state: ComputerWorkflowState,
    *,
    conversation_id: UUID,
    request_id: UUID,
    authorization: ToolAuthorization,
) -> ComputerWorkflowState:
    """Write this state durably BEFORE executing the fresh, exact broker authorization."""
    step = next_pending_step(state)
    if conversation_id != state.conversation_id or step is None:
        raise ValueError("workflow cannot begin in this conversation or state")
    if (
        authorization.tool_name != "computer_use"
        or authorization.decision is not PolicyDecision.ALLOW
        or authorization.reason_code != "confirmation_consumed"
        or authorization.normalized_arguments != step.arguments()
    ):
        raise ValueError("workflow requires fresh approval for its exact next step")
    if authorization.call_id in {item.call_id for item in state.progress}:
        raise ValueError("workflow call identity cannot be reused")
    return _replace_progress(
        state,
        ComputerWorkflowProgress(
            ordinal=step.ordinal,
            status="executing",
            reason_code="executing",
            request_id=request_id,
            call_id=authorization.call_id,
            call_digest=authorization.call_digest,
        ),
    )


def observe_workflow_step(
    state: ComputerWorkflowState,
    result: ToolExecutionResult,
) -> ComputerWorkflowState:
    running = [progress for progress in state.progress if progress.status == "executing"]
    if len(running) != 1:
        raise ValueError("workflow has no uniquely executing step")
    progress = running[0]
    report = None
    reason, status = "unverifiable_result", "intervention_required"
    if result.tool_name != "computer_use" or result.call_id != progress.call_id:
        reason = "result_mismatch"
    elif result.error_code is None and len(result.output.encode()) <= 4_096:
        try:
            report = ComputerUseReport.model_validate_json(result.output)
            step = state.spec.steps[progress.ordinal - 1]
            if report.application_bundle_identifier != step.application_bundle_identifier:
                report, reason = None, "result_mismatch"
            else:
                expected_kinds = [condition.kind for condition in step.postconditions]
                actual_kinds = [check.kind for check in report.postcondition_checks]
                checks_match = (
                    actual_kinds == expected_kinds
                    if expected_kinds
                    else actual_kinds == ["action_performed"]
                )
                if (
                    result_is_verified(result)
                    and report.status == "completed"
                    and checks_match
                    and all(check.satisfied for check in report.postcondition_checks)
                ):
                    status = reason = "verified"
                elif report.status == "partial" or report.steps:
                    status = reason = "partial"
                else:
                    status = reason = "blocked"
        except (ValidationError, ValueError, RecursionError):
            report = None
    return _replace_progress(
        state,
        ComputerWorkflowProgress(
            **{**progress.model_dump(), "status": status, "reason_code": reason, "report": report},
        ),
    )


def recover_interrupted_workflow(
    state: ComputerWorkflowState,
    *,
    cancelled: bool = False,
) -> ComputerWorkflowState:
    """Crash/restart never replays an executing step, even if its output was lost."""
    for progress in state.progress:
        if progress.status == "executing":
            state = _replace_progress(
                state,
                ComputerWorkflowProgress(
                    **{
                        **progress.model_dump(),
                        "status": "intervention_required",
                        "reason_code": "cancelled" if cancelled else "interrupted",
                    },
                ),
            )
    if cancelled and not state.cancelled:
        return ComputerWorkflowState.model_validate(
            {**state.model_dump(), "cancelled": True, "revision": state.revision + 1}
        )
    return state
