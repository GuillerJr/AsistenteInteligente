from __future__ import annotations

import logging
from enum import StrEnum
from uuid import UUID

from aegis_core.brain.errors import (
    BrainUnavailableError,
    ModelContextLimitError,
    RemoteProviderUnavailableError,
)
from aegis_core.job_contracts import EmptyAgentResponseError
from aegis_core.job_execution import JobExecutionRejectedError
from aegis_core.memory.errors import (
    ConversationCapacityError,
    MemoryNotFoundError,
    MemoryStoreError,
)
from aegis_core.tools.audit import AuditSink

LOGGER = logging.getLogger(__name__)


class JobFailureCode(StrEnum):
    """Stable public failures; exception messages must never cross this boundary."""

    SWARM_EXECUTION_TIMEOUT = "swarm_execution_timeout"
    CONVERSATION_CAPACITY_REACHED = "conversation_capacity_reached"
    CONVERSATION_NOT_FOUND = "conversation_not_found"
    CONVERSATION_UNAVAILABLE = "conversation_unavailable"
    EMPTY_AGENT_RESPONSE = "empty_agent_response"
    SWARM_EXECUTION_FAILED = "swarm_execution_failed"
    CONFIRMATION_TOOL_UNSUPPORTED = "confirmation_tool_unsupported"
    APPROVED_TOOL_EXECUTION_FAILED = "approved_tool_execution_failed"
    BRAIN_UNAVAILABLE = "brain_unavailable"
    REMOTE_PROVIDER_UNAVAILABLE = "remote_provider_unavailable"
    MODEL_CONTEXT_LIMIT = "model_context_limit"


class JobFailurePolicy:
    """Maps internal failures to bounded public codes and privacy-safe audit events."""

    _EXECUTION_MAPPINGS: tuple[tuple[type[Exception], JobFailureCode], ...] = (
        (TimeoutError, JobFailureCode.SWARM_EXECUTION_TIMEOUT),
        (ConversationCapacityError, JobFailureCode.CONVERSATION_CAPACITY_REACHED),
        (MemoryNotFoundError, JobFailureCode.CONVERSATION_NOT_FOUND),
        (MemoryStoreError, JobFailureCode.CONVERSATION_UNAVAILABLE),
        (BrainUnavailableError, JobFailureCode.BRAIN_UNAVAILABLE),
        (ModelContextLimitError, JobFailureCode.MODEL_CONTEXT_LIMIT),
        (RemoteProviderUnavailableError, JobFailureCode.REMOTE_PROVIDER_UNAVAILABLE),
        (EmptyAgentResponseError, JobFailureCode.EMPTY_AGENT_RESPONSE),
    )

    def __init__(self, audit_sink: AuditSink) -> None:
        self._audit = audit_sink

    def execution_code(
        self,
        error: Exception,
        *,
        request_id: UUID,
        job_id: UUID,
    ) -> str:
        if isinstance(error, JobExecutionRejectedError):
            return error.error_code
        for error_type, code in self._EXECUTION_MAPPINGS:
            if isinstance(error, error_type):
                return code.value
        self._record_unexpected(
            error,
            request_id=request_id,
            job_id=job_id,
            event_type=JobFailureCode.SWARM_EXECUTION_FAILED.value,
            component="job_execution",
        )
        return JobFailureCode.SWARM_EXECUTION_FAILED.value

    def approved_tool_code(
        self,
        error: Exception,
        *,
        request_id: UUID,
        job_id: UUID,
    ) -> str:
        self._record_unexpected(
            error,
            request_id=request_id,
            job_id=job_id,
            event_type=JobFailureCode.APPROVED_TOOL_EXECUTION_FAILED.value,
            component="job_authorization",
        )
        return JobFailureCode.APPROVED_TOOL_EXECUTION_FAILED.value

    def _record_unexpected(
        self,
        error: Exception,
        *,
        request_id: UUID,
        job_id: UUID,
        event_type: str,
        component: str,
    ) -> None:
        error_type = type(error).__name__
        # Exception messages may contain prompts, credentials or provider payloads.
        # Only the bounded class name is safe for operational logs and audit storage.
        LOGGER.error(
            "job operation failed job_id=%s component=%s error_type=%s",
            job_id,
            component,
            error_type,
        )
        self._audit.record_system_event(
            request_id,
            event_type=event_type,
            component=component,
            data={"job_id": str(job_id), "error_type": error_type},
        )
