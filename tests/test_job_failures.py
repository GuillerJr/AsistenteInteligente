from __future__ import annotations

import logging
from typing import Any
from uuid import UUID, uuid4

import pytest

from aegis_core.job_contracts import EmptyAgentResponseError
from aegis_core.job_execution import JobExecutionRejectedError
from aegis_core.job_failures import JobFailureCode, JobFailurePolicy
from aegis_core.memory.errors import (
    ConversationCapacityError,
    MemoryNotFoundError,
    MemoryStoreError,
)
from aegis_core.tools.audit import NullAuditSink


class _RecordingAudit(NullAuditSink):
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record_system_event(
        self,
        request_id: UUID,
        *,
        event_type: str,
        component: str,
        data: dict[str, str | int | bool | None],
        call_id: str | None = None,
    ) -> None:
        self.events.append(
            {
                "request_id": request_id,
                "event_type": event_type,
                "component": component,
                "data": data,
                "call_id": call_id,
            }
        )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError(), JobFailureCode.SWARM_EXECUTION_TIMEOUT.value),
        (
            ConversationCapacityError(),
            JobFailureCode.CONVERSATION_CAPACITY_REACHED.value,
        ),
        (MemoryNotFoundError(), JobFailureCode.CONVERSATION_NOT_FOUND.value),
        (MemoryStoreError(), JobFailureCode.CONVERSATION_UNAVAILABLE.value),
        (EmptyAgentResponseError(), JobFailureCode.EMPTY_AGENT_RESPONSE.value),
        (JobExecutionRejectedError("policy_rejected"), "policy_rejected"),
    ],
)
def test_execution_failure_mapping_is_stable(error: Exception, expected: str) -> None:
    audit = _RecordingAudit()

    code = JobFailurePolicy(audit).execution_code(
        error,
        request_id=uuid4(),
        job_id=uuid4(),
    )

    assert code == expected
    assert audit.events == []


def test_unexpected_failure_is_audited_without_sensitive_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    audit = _RecordingAudit()
    request_id = uuid4()
    job_id = uuid4()
    caplog.set_level(logging.ERROR, logger="aegis_core.job_failures")

    code = JobFailurePolicy(audit).execution_code(
        RuntimeError("provider leaked nvapi-secret-material"),
        request_id=request_id,
        job_id=job_id,
    )

    assert code == JobFailureCode.SWARM_EXECUTION_FAILED.value
    assert "nvapi-secret-material" not in caplog.text
    assert audit.events == [
        {
            "request_id": request_id,
            "event_type": "swarm_execution_failed",
            "component": "job_execution",
            "data": {"job_id": str(job_id), "error_type": "RuntimeError"},
            "call_id": None,
        }
    ]


def test_approved_tool_exception_requires_identity_and_emits_safe_audit() -> None:
    audit = _RecordingAudit()
    policy = JobFailurePolicy(audit)
    request_id = uuid4()
    job_id = uuid4()

    code = policy.approved_tool_code(
        request_id=request_id,
        job_id=job_id,
        error=RuntimeError("private tool output"),
    )

    assert code == JobFailureCode.APPROVED_TOOL_EXECUTION_FAILED.value
    assert audit.events[0]["event_type"] == "approved_tool_execution_failed"
    assert audit.events[0]["data"] == {
        "job_id": str(job_id),
        "error_type": "RuntimeError",
    }

    with pytest.raises(ValueError, match="requires job identity"):
        policy.approved_tool_code(error=RuntimeError())
