from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from aegis_core.contracts import InputModality, ToolAuthorization, ToolCall, UserRequest
from aegis_core.job_contracts import MAX_JOB_RESULT_BYTES, EmptyAgentResponseError, JobError
from aegis_core.job_graph import GraphInvocation, JobGraphInvoker, SwarmGraph
from aegis_core.memory.contracts import MAX_MEMORY_CONTENT_BYTES, ConversationTurn
from aegis_core.memory.conversations import ConversationCoordinator
from aegis_core.memory.profile import OwnerProfile

StreamCallback = Callable[[str], None]
InvocationObserver = Callable[[GraphInvocation], Awaitable[None]]


class JobExecutionRejectedError(JobError):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


@dataclass(frozen=True, slots=True)
class JobExecutionOutcome:
    invocation: GraphInvocation
    result: str | None
    conversation_persisted: bool | None

    @property
    def pending_confirmation(self) -> tuple[ToolCall, ToolAuthorization] | None:
        return self.invocation.pending[0] if self.invocation.pending else None


class JobExecutionPipeline:
    """Executes one graph turn and owns its atomic conversation exchange.

    Job lifecycle transitions deliberately remain in ``SwarmJobManager``. This
    component is limited to model execution and the serialized conversation
    round-trip. Post-result learning and approved-tool persistence live at the
    completion boundary.
    """

    def __init__(
        self,
        graph: SwarmGraph,
        *,
        execution_timeout_seconds: float,
        conversations: ConversationCoordinator | None,
    ) -> None:
        self._graph_invoker = JobGraphInvoker(graph)
        self._execution_timeout_seconds = execution_timeout_seconds
        self._conversations = conversations

    async def execute(
        self,
        request: UserRequest,
        *,
        conversation_id: UUID | None,
        stream_callback: StreamCallback,
        invocation_observer: InvocationObserver,
    ) -> JobExecutionOutcome:
        if conversation_id is None:
            return await self._execute_with_history(
                request,
                (),
                conversation_id=None,
                stream_callback=stream_callback,
                invocation_observer=invocation_observer,
            )

        conversations = self._conversations
        if conversations is None:
            raise JobError("conversation coordinator is unavailable")
        async with conversations.serialized(conversation_id):
            history = await conversations.history(conversation_id)
            if (
                InputModality.AUDIO in request.modalities
                and not OwnerProfile.is_verified_owner_voice(request)
                and history
            ):
                raise JobExecutionRejectedError("voice_conversation_owner_required")
            return await self._execute_with_history(
                request,
                history,
                conversation_id=conversation_id,
                stream_callback=stream_callback,
                invocation_observer=invocation_observer,
            )

    async def persist_exchange(
        self,
        conversation_id: UUID,
        *,
        user_content: str,
        assistant_content: str,
    ) -> bool:
        conversations = self._conversations
        if conversations is None:
            raise JobError("conversation coordinator is unavailable")
        async with conversations.serialized(conversation_id):
            return await self._record_exchange(
                conversation_id,
                user_content=user_content,
                assistant_content=bound_conversation_content(assistant_content),
            )

    async def _execute_with_history(
        self,
        request: UserRequest,
        history: tuple[ConversationTurn, ...],
        *,
        conversation_id: UUID | None,
        stream_callback: StreamCallback,
        invocation_observer: InvocationObserver,
    ) -> JobExecutionOutcome:
        invocation = await asyncio.wait_for(
            self._graph_invoker.invoke(
                request,
                history,
                stream_callback=stream_callback,
            ),
            timeout=self._execution_timeout_seconds,
        )
        await invocation_observer(invocation)
        if len(invocation.pending) > 1:
            raise JobExecutionRejectedError("multiple_confirmations_unsupported")
        if invocation.pending:
            return JobExecutionOutcome(
                invocation=invocation,
                result=None,
                conversation_persisted=None,
            )
        final_result = invocation.final_result
        if final_result is None:
            raise ValueError("graph did not return a final agent result")
        result = validate_job_result(final_result.content)
        conversation_persisted = None
        if conversation_id is not None:
            conversation_persisted = await self._record_exchange(
                conversation_id,
                user_content=request.text,
                assistant_content=bound_conversation_content(result),
            )
        return JobExecutionOutcome(
            invocation=invocation,
            result=result,
            conversation_persisted=conversation_persisted,
        )

    async def _record_exchange(
        self,
        conversation_id: UUID,
        *,
        user_content: str,
        assistant_content: str,
    ) -> bool:
        conversations = self._conversations
        if conversations is None:
            raise JobError("conversation coordinator is unavailable")
        persistence = asyncio.create_task(
            conversations.record_exchange(
                conversation_id,
                user_content=user_content,
                assistant_content=assistant_content,
            )
        )
        try:
            return await asyncio.shield(persistence)
        except asyncio.CancelledError:
            return await persistence


def bound_job_result(content: str) -> str:
    if len(json.dumps(content, ensure_ascii=False).encode("utf-8")) <= MAX_JOB_RESULT_BYTES:
        return content
    lower = 0
    upper = len(content)
    while lower < upper:
        midpoint = (lower + upper + 1) // 2
        size = len(json.dumps(content[:midpoint], ensure_ascii=False).encode("utf-8"))
        if size <= MAX_JOB_RESULT_BYTES:
            lower = midpoint
        else:
            upper = midpoint - 1
    return content[:lower]


def validate_job_result(content: str) -> str:
    result = bound_job_result(content)
    if not result.strip():
        raise EmptyAgentResponseError("agent response is empty")
    return result


def bound_conversation_content(content: str) -> str:
    encoded = content.encode("utf-8")
    if len(encoded) <= MAX_MEMORY_CONTENT_BYTES:
        return content
    return encoded[:MAX_MEMORY_CONTENT_BYTES].decode("utf-8", errors="ignore")
