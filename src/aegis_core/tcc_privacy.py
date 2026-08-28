from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError

from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.jobs import JobError, JobNotFoundError, JobStatus, SwarmJobManager
from aegis_core.tools.audit import AuditSink


class TCCPrivacyPermission(StrEnum):
    SCREEN_RECORDING = "screen_recording"
    ACCESSIBILITY = "accessibility"


class TCCPrivacyOperation(StrEnum):
    SCREEN_TURN = "screen_turn"
    COMPUTER_CONTROL = "computer_control"


class TCCPermissionDeniedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    error_code: Literal["tcc_permission_denied"]
    permission: TCCPrivacyPermission
    operation: TCCPrivacyOperation
    job_id: UUID | None = None


class TCCPrivacyIpcService:
    """Stops the exact active job reported by the native TCC boundary."""

    METHOD = "privacy.permission.denied"

    def __init__(self, jobs: SwarmJobManager, audit_sink: AuditSink) -> None:
        self._jobs = jobs
        self._audit_sink = audit_sink

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {self.METHOD: self.handle}

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        if request.method != self.METHOD:
            return IpcHandlerResult(ok=False, error_code="method_not_found")
        try:
            payload = TCCPermissionDeniedPayload.model_validate(request.payload)
        except ValidationError:
            return IpcHandlerResult(ok=False, error_code="invalid_payload")

        job_cancelled = False
        job_state = "not_present"
        if payload.job_id is not None:
            try:
                snapshot = await self._jobs.cancel(payload.job_id)
                job_cancelled = snapshot.status is JobStatus.CANCELLED
                job_state = snapshot.status.value
            except JobNotFoundError:
                job_state = "not_found"
            except JobError:
                job_state = "cancel_failed"

        self._audit_sink.record_system_event(
            request.request_id,
            event_type="tcc_permission_denied",
            component="privacy_control",
            call_id=request.nonce,
            data={
                "error_code": payload.error_code,
                "permission": payload.permission.value,
                "operation": payload.operation.value,
                "job_present": payload.job_id is not None,
                "job_cancelled": job_cancelled,
                "job_state": job_state,
                "retry_allowed": False,
            },
        )
        return IpcHandlerResult(
            ok=True,
            payload={
                "acknowledged": True,
                "job_cancelled": job_cancelled,
            },
        )
