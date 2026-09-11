from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from aegis_core.contracts import (
    PolicyDecision,
    ToolAuthorization,
    ToolCall,
    ToolExecutionResult,
    UserRequest,
)
from aegis_core.job_contracts import JobConfirmationError
from aegis_core.job_tool_presenter import JobToolPresenter
from aegis_core.tools.audit import AuditSink
from aegis_core.tools.broker import PolicyContext, ToolBroker, VoiceConfirmationEvidence
from aegis_core.tools.confirmations import ConfirmationError, OneTimeConfirmationStore
from aegis_core.tools.verification import result_is_verified


class ConfirmationConsumptionError(JobConfirmationError):
    """Raised after an issued confirmation cannot be consumed exactly once."""


class ApprovedToolExecutionError(RuntimeError):
    """Raised when an approved execution crosses a runtime trust boundary."""


class ApprovedToolExecutor(Protocol):
    async def execute_async(
        self,
        authorization: ToolAuthorization,
        context: PolicyContext,
    ) -> ToolExecutionResult: ...


@dataclass(frozen=True, slots=True)
class ApprovedToolOutcome:
    result: ToolExecutionResult
    rendered_result: str | None

    @property
    def verified(self) -> bool:
        return result_is_verified(self.result)


class JobToolRuntime:
    """Consumes one-time grants and executes their exact authorized tool call."""

    def __init__(
        self,
        *,
        broker: ToolBroker | None,
        policy_context: PolicyContext | None,
        confirmation_store: OneTimeConfirmationStore | None,
        executor: ApprovedToolExecutor | None,
        audit_sink: AuditSink,
        presenter: JobToolPresenter,
    ) -> None:
        self._broker = broker
        self._policy_context = policy_context
        self._confirmation_store = confirmation_store
        self._executor = executor
        self._audit = audit_sink
        self._presenter = presenter

    def consume_confirmation(
        self,
        *,
        call: ToolCall,
        pending_authorization: ToolAuthorization,
        authorization_request: UserRequest | None,
        expected_call_digest: str,
        received_call_digest: str,
        approved_by: str,
        voice_confirmation: VoiceConfirmationEvidence | None,
    ) -> ToolAuthorization:
        if expected_call_digest != received_call_digest:
            raise JobConfirmationError("confirmation is unavailable")
        broker, context, store, _ = self._require_runtime()
        try:
            store.issue(
                call,
                pending_authorization,
                approved_by=approved_by,
                now=context.current_time(),
            )
        except ConfirmationError as error:
            raise JobConfirmationError("confirmation could not be issued") from error
        authorization = broker.authorize(
            call,
            context,
            request=authorization_request,
            voice_confirmation=voice_confirmation,
        )
        if (
            authorization.decision is not PolicyDecision.ALLOW
            or authorization.reason_code != "confirmation_consumed"
        ):
            raise ConfirmationConsumptionError("confirmation could not be consumed")
        return authorization

    async def execute(
        self,
        request_id: UUID,
        authorization: ToolAuthorization,
    ) -> ApprovedToolOutcome:
        _, context, _, executor = self._require_runtime()
        if (
            authorization.decision is not PolicyDecision.ALLOW
            or authorization.reason_code != "confirmation_consumed"
        ):
            raise ApprovedToolExecutionError("authorization is not an approved one-time grant")
        self._audit.record_authorization(request_id, authorization)
        result = await executor.execute_async(authorization, context)
        self._audit.record_execution(request_id, result)
        if (
            result.call_id != authorization.call_id
            or result.tool_name != authorization.tool_name
        ):
            raise ApprovedToolExecutionError("tool result does not match its authorization")
        rendered = self._presenter.format_result(result, authorization) if result.success else None
        return ApprovedToolOutcome(result=result, rendered_result=rendered)

    def _require_runtime(
        self,
    ) -> tuple[ToolBroker, PolicyContext, OneTimeConfirmationStore, ApprovedToolExecutor]:
        if (
            self._broker is None
            or self._policy_context is None
            or self._confirmation_store is None
            or self._executor is None
        ):
            raise JobConfirmationError("confirmation runtime is unavailable")
        return (
            self._broker,
            self._policy_context,
            self._confirmation_store,
            self._executor,
        )
