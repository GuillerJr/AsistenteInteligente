from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Protocol
from uuid import UUID

from aegis_core.contracts import (
    PolicyDecision,
    ToolAuthorization,
    ToolCall,
    ToolExecutionResult,
    UserRequest,
)
from aegis_core.failure_boundary import record_boundary_error
from aegis_core.job_contracts import JobConfirmationError
from aegis_core.job_tool_presenter import JobToolPresenter
from aegis_core.tools.audit import AuditSink
from aegis_core.tools.broker import PolicyContext, ToolBroker, VoiceConfirmationEvidence
from aegis_core.tools.confirmations import ConfirmationError, OneTimeConfirmationStore
from aegis_core.tools.verification import result_goal_is_verified, result_is_verified


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

    @property
    def goal_verified(self) -> bool:
        return result_goal_is_verified(self.result)


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
        if not (
            expected_call_digest
            == received_call_digest
            == pending_authorization.call_digest
            == call.digest()
        ):
            raise JobConfirmationError("confirmation is unavailable")
        broker, context, store, _ = self._require_runtime()
        if authorization_request is not None:
            context = replace(
                context, request_id=authorization_request.request_id,
                request_text=authorization_request.text,
            )
        if context.is_security_compromised():
            raise ConfirmationConsumptionError("confirmation runtime is compromised")
        try:
            grant = store.issue(
                call,
                pending_authorization,
                approved_by=approved_by,
                now=context.current_time(),
                **(
                    {"request_id": authorization_request.request_id}
                    if authorization_request is not None
                    else {}
                ),
            )
        except ConfirmationError as error:
            raise JobConfirmationError("confirmation could not be issued") from error
        try:
            authorization = broker.authorize(
                call,
                context,
                request=authorization_request,
                voice_confirmation=voice_confirmation,
            )
            if (
                authorization.decision is not PolicyDecision.ALLOW
                or authorization.reason_code != "confirmation_consumed"
                or authorization.call_id != pending_authorization.call_id
                or authorization.tool_name != pending_authorization.tool_name
                or authorization.call_digest != expected_call_digest
                or _canonical_arguments(authorization)
                != _canonical_arguments(pending_authorization)
            ):
                raise ConfirmationConsumptionError("confirmation could not be consumed")
            return authorization
        except Exception as error:
            record_boundary_error(error)
            raise ConfirmationConsumptionError("confirmation could not be consumed") from error
        finally:
            # A policy denial, exception or cancellation must leave no live grant behind.
            # Already-consumed grants remain consumed; revoke never makes them reusable.
            store.revoke(grant.grant_id)

    async def execute(
        self,
        request_id: UUID,
        authorization: ToolAuthorization,
        *,
        request_text: str | None = None,
    ) -> ApprovedToolOutcome:
        _, context, _, executor = self._require_runtime()
        context = replace(context, request_id=request_id, request_text=request_text)
        if (
            authorization.decision is not PolicyDecision.ALLOW
            or authorization.reason_code != "confirmation_consumed"
        ):
            raise ApprovedToolExecutionError("authorization is not an approved one-time grant")
        if context.is_security_compromised():
            raise ApprovedToolExecutionError("confirmation runtime is compromised")
        self._audit.record_authorization(request_id, authorization)
        if context.is_security_compromised():
            raise ApprovedToolExecutionError("confirmation runtime is compromised")
        result = await executor.execute_async(authorization, context)
        if result.call_id != authorization.call_id or result.tool_name != authorization.tool_name:
            # Reject the executor's attribution before publishing either audit or
            # owner-visible activity. An unrelated receipt cannot prove this action.
            self._audit.record_execution(request_id, ToolExecutionResult(
                call_id=authorization.call_id, tool_name=authorization.tool_name,
                success=False, error_code="tool_result_mismatch", metadata={"verified": False},
            ))
            raise ApprovedToolExecutionError("tool result does not match its authorization")
        try:
            rendered = (
                self._presenter.format_result(result, authorization) if result.success else None
            )
        except ValueError as error:
            # The presenter already distinguishes valid acknowledgements from
            # evidence that contradicts the authorized target or tool contract.
            self._audit.record_execution(request_id, ToolExecutionResult(
                call_id=authorization.call_id, tool_name=authorization.tool_name,
                success=False, error_code="tool_result_invalid", metadata={"verified": False},
            ))
            raise ApprovedToolExecutionError("tool result evidence is invalid") from error
        self._audit.record_execution(request_id, result)
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


def _canonical_arguments(authorization: ToolAuthorization) -> str:
    return json.dumps(
        authorization.normalized_arguments, sort_keys=True, allow_nan=False, separators=(",", ":")
    )
