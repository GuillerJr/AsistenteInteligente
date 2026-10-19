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


@pytest.mark.parametrize("reported_verified", [False, True])
async def test_chrome_connection_failure_is_preserved_without_success_or_raw_peer_details(
    reported_verified,
):
    persistence = _Persistence()
    summarized = []

    async def summarizer(context, outcome):
        summarized.append(outcome)
        raise AssertionError("failed Chrome access must never be summarized as success")

    coordinator = JobCompletionCoordinator(
        persistence, owner_profile=None, social_memory=None, capability_learning=None,
        approved_result_summarizer=summarizer,
    )
    raw = "Could not connect to chrome /synthetic/private/path nvapi-test-not-a-real-token"
    outcome = ApprovedToolOutcome(
        result=ToolExecutionResult(
            call_id="chrome-access", tool_name="browser_task_open", success=False,
            error_code="browser_task_chrome_connection_failed", output=raw,
            metadata={"verified": reported_verified, "goal_verified": reported_verified},
        ),
        rendered_result="Peer falsely claims success " + raw,
    )
    completion = await coordinator.from_approved_tool(
        ApprovedToolContext(
            request_id=uuid4(), request_text="Abre el sitio en Chrome directo",
            conversation_id=uuid4(),
        ),
        outcome,
    )
    assert completion.error_code == "browser_task_chrome_connection_failed"
    assert not completion.succeeded and completion.action_verified is False
    assert completion.result is None and completion.conversation_persisted is None
    assert completion.public_sources is None and completion.tool_name == "browser_task_open"
    assert persistence.calls == [] and summarized == []
    assert raw not in repr(completion) and "/synthetic/private/path" not in repr(completion)


@pytest.mark.parametrize(
    ("tool_name", "error_code"),
    [
        ("browser_task_action", "browser_task_chrome_connection_failed"),
        ("browser_task_close", "browser_task_chrome_connection_failed"),
        ("application_open", "browser_task_chrome_connection_failed"),
        ("browser_task_open", "browser_task_execution_failed"),
        ("browser_task_open", "browser_task_chrome_connection_failed_unknown"),
        ("browser_task_open", None),
    ],
)
async def test_approved_failure_whitelist_does_not_publish_other_tools_or_peer_codes(
    tool_name, error_code,
):
    persistence = _Persistence()
    outcome = ApprovedToolOutcome(
        result=ToolExecutionResult(
            call_id="failure", tool_name=tool_name, success=False,
            output="synthetic-private-peer-detail", error_code=error_code,
            metadata={"verified": True},
        ),
        rendered_result=None,
    )
    completion = await _coordinator(persistence).from_approved_tool(
        ApprovedToolContext(request_id=uuid4(), request_text="Tarea", conversation_id=uuid4()),
        outcome,
    )
    assert completion.error_code == "approved_tool_execution_failed"
    assert completion.result is None and not completion.succeeded and not completion.action_verified
    assert completion.conversation_persisted is None and persistence.calls == []
    assert "synthetic-private-peer-detail" not in repr(completion)
