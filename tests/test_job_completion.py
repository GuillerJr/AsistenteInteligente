from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from aegis_core.contracts import ToolExecutionResult
from aegis_core.job_completion import (
    ApprovedToolContext,
    JobCompletion,
    JobCompletionCoordinator,
)
from aegis_core.job_failures import JobFailureCode
from aegis_core.job_tool_runtime import ApprovedToolOutcome


class _Persistence:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def persist_exchange(
        self,
        conversation_id: UUID,
        *,
        user_content: str,
        assistant_content: str,
    ) -> bool:
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "user_content": user_content,
                "assistant_content": assistant_content,
            }
        )
        return True


def _coordinator(persistence: _Persistence) -> JobCompletionCoordinator:
    return JobCompletionCoordinator(
        persistence,
        owner_profile=None,
        social_memory=None,
        capability_learning=None,
    )


def test_completion_contract_requires_exactly_one_terminal_payload() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        JobCompletion(result=None, error_code=None)
    with pytest.raises(ValueError, match="exactly one"):
        JobCompletion(result="done", error_code="failed")
    with pytest.raises(ValueError, match="successful side effects"):
        JobCompletion(
            result=None,
            error_code="failed",
            conversation_persisted=True,
        )


@pytest.mark.asyncio
async def test_approved_tool_completion_persists_one_bounded_exchange() -> None:
    persistence = _Persistence()
    conversation_id = uuid4()
    context = ApprovedToolContext(
        request_id=uuid4(),
        request_text="Ejecuta la acción",
        conversation_id=conversation_id,
    )
    outcome = ApprovedToolOutcome(
        result=ToolExecutionResult(
            call_id="call-1",
            tool_name="application_open",
            success=True,
            output="{}",
            metadata={"verified": True},
        ),
        rendered_result="Aplicación abierta.",
    )

    completion = await _coordinator(persistence).from_approved_tool(context, outcome)

    assert completion.succeeded is True
    assert completion.result == "Aplicación abierta."
    assert completion.tool_name == "application_open"
    assert completion.action_verified is True
    assert completion.conversation_persisted is True
    assert persistence.calls == [
        {
            "conversation_id": conversation_id,
            "user_content": "Ejecuta la acción",
            "assistant_content": "Aplicación abierta.",
        }
    ]


@pytest.mark.asyncio
async def test_failed_approved_tool_returns_failure_without_persisting() -> None:
    persistence = _Persistence()
    outcome = ApprovedToolOutcome(
        result=ToolExecutionResult(
            call_id="call-1",
            tool_name="application_open",
            success=False,
            output="",
            error_code="native_action_failed",
        ),
        rendered_result=None,
    )

    completion = await _coordinator(persistence).from_approved_tool(
        ApprovedToolContext(
            request_id=uuid4(),
            request_text="Ejecuta la acción",
            conversation_id=uuid4(),
        ),
        outcome,
    )

    assert completion.succeeded is False
    assert completion.error_code == JobFailureCode.APPROVED_TOOL_EXECUTION_FAILED.value
    assert completion.tool_name == "application_open"
    assert persistence.calls == []
