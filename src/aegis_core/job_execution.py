from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from aegis_core.contracts import (
    DocumentInput,
    InputModality,
    ToolAuthorization,
    ToolCall,
    ToolExecutionResult,
    UserRequest,
)
from aegis_core.job_contracts import MAX_JOB_RESULT_BYTES, EmptyAgentResponseError, JobError
from aegis_core.job_graph import GraphInvocation, JobGraphInvoker, SwarmGraph
from aegis_core.memory.contracts import MAX_MEMORY_CONTENT_BYTES, ConversationTurn
from aegis_core.memory.conversations import ConversationCoordinator
from aegis_core.memory.profile import OwnerProfile
from aegis_core.memory.task_checkpoint import TaskCheckpoint, TaskRecovery, VerifiedTaskResult
from aegis_core.orchestration.task_progress import CONTINUATIONS, verified_task_result
from aegis_core.privacy import redact_secrets_for_remote, sanitize_remote_reference
from aegis_core.request_intent import folded_request
from aegis_core.tools.verification import result_is_verified

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
            checkpoint = await conversations.checkpoint(conversation_id)
            recovery = await conversations.recovery(conversation_id)
            if (
                InputModality.AUDIO in request.modalities
                and not OwnerProfile.is_verified_owner_voice(request)
                and (history or checkpoint or recovery)
            ):
                raise JobExecutionRejectedError("voice_conversation_owner_required")
            if recovery and recovery.status != "completed":
                request = request.model_copy(update={"metadata": {
                    **request.metadata, "task_recovery": recovery.model_dump(mode="json"),
                }})
            context = await conversations.prepare_context(conversation_id, history)
            if context:
                request = request.model_copy(update={"metadata": {
                    **request.metadata, "conversation_summary": context,
                }})
            if (request.metadata.get("source_channel") == "telegram"
                    and InputModality.AUDIO not in request.modalities):
                shared = await conversations.shared_context(conversation_id, request.text, history)
                request = request.model_copy(update={"metadata": {
                    **request.metadata, "shared_conversation_context": shared,
                }})
            documents = await conversations.documents(conversation_id)
            if documents:
                request = request.model_copy(update={"metadata": {
                    **request.metadata,
                    "chat_document_context": [d.model_dump() for d in documents],
                }})
            return await self._execute_with_history(
                request,
                history,
                conversation_id=conversation_id,
                stream_callback=stream_callback,
                invocation_observer=invocation_observer,
                task_checkpoint=checkpoint,
            )

    async def record_recovery(self, conversation_id: UUID | None, *, request_id,
                              request_text, tool_name, call_digest, status, result=None) -> None:
        if conversation_id is None or self._conversations is None:
            return
        previous = await self._conversations.recovery(conversation_id)
        if (previous and previous.status != "completed" and previous.tool_name == tool_name
                and folded_request(request_text) in CONTINUATIONS):
            request_text = previous.request_text
        text = redact_secrets_for_remote(request_text).text.encode("utf-8")[:2_048]
        observation = verified_task_result(result) if result is not None else None
        if result is not None and observation is None and result_is_verified(result):
            clean = sanitize_remote_reference({"result": result.output})["result"]
            excerpt = clean if isinstance(clean, str) else json.dumps(clean, ensure_ascii=False)
            observation = VerifiedTaskResult(
                tool_name=result.tool_name, call_id=result.call_id, observed_at=datetime.now(UTC),
                excerpt=excerpt.encode("utf-8")[:3_072].decode("utf-8", errors="ignore"),
                truncated=len(excerpt.encode("utf-8")) > 3_072,
            )
        recovery = TaskRecovery(
            request_id=str(request_id), request_text=text.decode("utf-8", errors="ignore"),
            tool_name=tool_name, call_digest=call_digest, status=status,
            observation=observation,
        )
        await self._conversations.record_recovery(
            conversation_id, recovery, tool_results=(result,) if result is not None else (),
        )

    async def persist_exchange(
        self,
        conversation_id: UUID,
        *,
        user_content: str,
        assistant_content: str,
        documents: tuple[DocumentInput, ...] = (),
        image_name: str | None = None,
        tool_results: tuple[ToolExecutionResult, ...] = (),
        source_checkpoint: TaskCheckpoint | None = None,
    ) -> bool:
        conversations = self._conversations
        if conversations is None:
            raise JobError("conversation coordinator is unavailable")
        async with conversations.serialized(conversation_id):
            return await self._record_exchange(
                conversation_id,
                user_content=user_content,
                assistant_content=bound_conversation_content(assistant_content),
                documents=documents,
                image_name=image_name,
                tool_results=tool_results,
                source_checkpoint=source_checkpoint,
            )

    async def _execute_with_history(
        self,
        request: UserRequest,
        history: tuple[ConversationTurn, ...],
        *,
        conversation_id: UUID | None,
        stream_callback: StreamCallback,
        invocation_observer: InvocationObserver,
        task_checkpoint: TaskCheckpoint | None = None,
    ) -> JobExecutionOutcome:
        invocation = await asyncio.wait_for(
            self._graph_invoker.invoke(
                request,
                history,
                stream_callback=stream_callback,
                task_checkpoint=task_checkpoint,
            ),
            timeout=self._execution_timeout_seconds,
        )
        await invocation_observer(invocation)
        if len(invocation.pending) > 1:
            raise JobExecutionRejectedError("multiple_confirmations_unsupported")
        if invocation.pending:
            call, authorization = invocation.pending[0]
            await self.record_recovery(
                conversation_id, request_id=request.request_id, request_text=request.text,
                tool_name=call.tool_name, call_digest=authorization.call_digest,
                status="awaiting_confirmation",
            )
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
                documents=request.documents,
                image_name=request.image_name,
                tool_results=invocation.tool_results,
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
        documents: tuple[DocumentInput, ...] = (),
        image_name: str | None = None,
        tool_results: tuple[ToolExecutionResult, ...] = (),
        source_checkpoint: TaskCheckpoint | None = None,
    ) -> bool:
        conversations = self._conversations
        if conversations is None:
            raise JobError("conversation coordinator is unavailable")
        persistence = asyncio.create_task(
            conversations.record_exchange(
                conversation_id,
                user_content=user_content,
                assistant_content=assistant_content,
                **({"documents": documents} if documents else {}),
                **({"image_name": image_name} if image_name else {}),
                **({"tool_results": tool_results} if tool_results else {}),
                **({"source_checkpoint": source_checkpoint}
                   if source_checkpoint is not None else {}),
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
