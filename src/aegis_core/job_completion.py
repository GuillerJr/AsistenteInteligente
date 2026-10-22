from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from aegis_core.capability_learning import CapabilityLearningCoordinator
from aegis_core.contracts import (
    TEMPORARY_CONVERSATION_METADATA,
    DocumentInput,
    ToolExecutionResult,
    UserRequest,
)
from aegis_core.job_execution import JobExecutionOutcome, bound_job_result
from aegis_core.job_failures import JobFailureCode
from aegis_core.job_tool_runtime import ApprovedToolOutcome
from aegis_core.memory.profile import OwnerProfile
from aegis_core.memory.social import SocialMemory
from aegis_core.memory.task_checkpoint import TaskCheckpoint


@dataclass(frozen=True, slots=True)
class ApprovedToolContext:
    request_id: UUID
    request_text: str
    conversation_id: UUID | None
    documents: tuple[DocumentInput, ...] = ()
    image_name: str | None = None
    task_checkpoint: TaskCheckpoint | None = None
    source_channel: str | None = None
    skill_guidance: str | None = None


@dataclass(frozen=True, slots=True)
class JobCompletion:
    result: str | None
    error_code: str | None
    conversation_persisted: bool | None = None
    public_sources: tuple[str, ...] | None = None
    tool_name: str | None = None
    action_verified: bool = False

    def __post_init__(self) -> None:
        if (self.result is None) == (self.error_code is None):
            raise ValueError("completion requires exactly one result or error")
        if self.result is not None and not self.result.strip():
            raise ValueError("completion result is empty")
        if self.error_code is not None and (
            self.conversation_persisted is not None or self.public_sources is not None
        ):
            raise ValueError("failed completion cannot publish successful side effects")
        if self.action_verified and self.tool_name is None:
            raise ValueError("verified completion requires a tool")

    @property
    def succeeded(self) -> bool:
        return self.error_code is None


class ConversationPersistence(Protocol):
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
        request_id: UUID | None = None,
    ) -> bool: ...


class JobCompletionCoordinator:
    """Builds one terminal contract from graph or approved-tool side effects."""

    def __init__(
        self,
        persistence: ConversationPersistence,
        *,
        owner_profile: OwnerProfile | None,
        social_memory: SocialMemory | None,
        capability_learning: CapabilityLearningCoordinator | None,
        approved_result_summarizer: Callable[
            [ApprovedToolContext, ApprovedToolOutcome], Awaitable[str | None]
        ] | None = None,
    ) -> None:
        self._persistence = persistence
        self._owner_profile = owner_profile
        self._social_memory = social_memory
        self._capability_learning = capability_learning
        self._approved_result_summarizer = approved_result_summarizer

    async def from_graph(
        self,
        request: UserRequest,
        outcome: JobExecutionOutcome,
    ) -> JobCompletion:
        if outcome.pending_confirmation is not None:
            raise ValueError("pending graph execution cannot be completed")
        if outcome.result is None:
            raise ValueError("completed graph execution is missing a result")
        await self._observe(request, outcome)
        return JobCompletion(
            result=outcome.result,
            error_code=None,
            conversation_persisted=outcome.conversation_persisted,
            public_sources=outcome.invocation.public_sources,
            tool_name=outcome.invocation.tool_name,
            action_verified=outcome.invocation.action_verified,
        )

    async def from_approved_tool(
        self,
        context: ApprovedToolContext,
        outcome: ApprovedToolOutcome,
    ) -> JobCompletion:
        result = outcome.result
        if not result.success:
            return JobCompletion(
                result=None,
                error_code=(
                    "browser_task_chrome_connection_failed"
                    if result.tool_name == "browser_task_open"
                    and result.error_code == "browser_task_chrome_connection_failed"
                    else JobFailureCode.APPROVED_TOOL_EXECUTION_FAILED.value
                ),
                tool_name=result.tool_name,
                action_verified=False,
            )
        if outcome.rendered_result is None:
            raise ValueError("approved tool result is empty")
        summarized = (
            await self._approved_result_summarizer(context, outcome)
            if self._approved_result_summarizer is not None else None
        )
        rendered = bound_job_result(summarized or outcome.rendered_result)
        conversation_persisted = None
        if context.conversation_id is not None:
            conversation_persisted = await self._persistence.persist_exchange(
                context.conversation_id,
                user_content=context.request_text,
                assistant_content=rendered,
                **({"documents": context.documents} if context.documents else {}),
                **({"image_name": context.image_name} if context.image_name else {}),
                **({"tool_results": (result,)}
                   if outcome.verified and result.metadata.get("integration") == "google-workspace"
                   else {}),
                **({"source_checkpoint": context.task_checkpoint}
                   if context.task_checkpoint is not None else {}),
                **({"request_id": context.request_id}
                   if context.source_channel == "telegram" else {}),
            )
        return JobCompletion(
            result=rendered,
            error_code=None,
            conversation_persisted=conversation_persisted,
            tool_name=result.tool_name,
            action_verified=outcome.goal_verified,
        )

    async def _observe(self, request: UserRequest, outcome: JobExecutionOutcome) -> None:
        if request.metadata.get(TEMPORARY_CONVERSATION_METADATA) is True:
            return  # A temporary chat teaches Jarvis nothing.
        observers: list[Awaitable[object]] = []
        if self._owner_profile is not None:
            observers.append(self._owner_profile.observe(request))
        if self._social_memory is not None:
            observers.append(self._social_memory.observe(request))
        invocation = outcome.invocation
        if self._capability_learning is not None and invocation.capability_gap:
            observers.append(
                self._capability_learning.observe(
                    request,
                    invocation.capability_research,
                )
            )
        if observers:
            await asyncio.gather(*observers)
