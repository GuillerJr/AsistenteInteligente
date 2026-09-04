from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from aegis_core.contracts import InputModality, UserRequest
from aegis_core.dialogue import REPAIR_CONTEXT_METADATA
from aegis_core.feedback import (
    FEEDBACK_OWNER_UNVERIFIED,
    FEEDBACK_STATUS_METADATA,
    FEEDBACK_TARGET_AVAILABLE,
    FEEDBACK_TARGET_MISSING,
    OwnerFeedback,
    extract_owner_feedback,
)
from aegis_core.job_contracts import (
    EvaluationStore,
    JobConversationNotFoundError,
    JobError,
)
from aegis_core.job_state import JobRegistry, MutableJob, RecentPublicSources
from aegis_core.memory.conversations import ConversationCoordinator
from aegis_core.memory.errors import (
    ConversationCapacityError,
    MemoryNotFoundError,
    MemoryStoreError,
)
from aegis_core.memory.profile import OwnerProfile
from aegis_core.orchestration.direct_actions import (
    PUBLIC_SOURCE_AVAILABLE,
    PUBLIC_SOURCE_MISSING,
    PUBLIC_SOURCE_STATUS_METADATA,
    PUBLIC_SOURCE_URL_METADATA,
    public_source_reference_index,
)

LOGGER = logging.getLogger(__name__)
REPAIR_WINDOW_TTL = timedelta(minutes=2)
RECENT_PUBLIC_SOURCES_TTL = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class ResolvedJobAdmission:
    request: UserRequest
    conversation_id: UUID | None


@dataclass(frozen=True, slots=True)
class PreparedJobAdmission:
    request: UserRequest
    conversation_id: UUID | None
    authorization_request: UserRequest | None
    voice_request: bool
    owner_verified: bool
    owner_feedback_request: bool
    repair_attempt: bool
    feedback_target_id: UUID | None
    feedback_to_apply: OwnerFeedback | None


class JobAdmissionCoordinator:
    """Builds trusted job input and owns its bounded ephemeral context.

    The manager must call synchronous methods while holding its lifecycle lock.
    Conversation resolution remains asynchronous and intentionally happens before
    that lock is acquired.
    """

    def __init__(self, conversations: ConversationCoordinator | None) -> None:
        self._conversations = conversations
        self._repair_windows: dict[UUID | None, datetime] = {}
        self._recent_public_sources: dict[UUID | None, RecentPublicSources] = {}

    async def resolve(
        self,
        request: UserRequest,
        *,
        conversation_id: UUID | None,
        persist_conversation: bool,
    ) -> ResolvedJobAdmission:
        request = self._strip_internal_source_metadata(request)
        if persist_conversation:
            conversation_id = await self._resolve_persistent_conversation(conversation_id)
        elif conversation_id is not None:
            await self._require_existing_conversation(conversation_id)
        if conversation_id is not None:
            request = request.model_copy(
                update={
                    "metadata": {
                        **request.metadata,
                        "conversation_id": str(conversation_id),
                    }
                }
            )
        return ResolvedJobAdmission(request=request, conversation_id=conversation_id)

    def prepare(
        self,
        resolved: ResolvedJobAdmission,
        *,
        registry: JobRegistry,
        now: datetime,
    ) -> PreparedJobAdmission:
        self._expire(now)
        request = self._resolve_public_source_reference(
            resolved.request,
            resolved.conversation_id,
        )
        voice_request = InputModality.AUDIO in request.modalities
        owner_verified = OwnerProfile.is_verified_owner_voice(request)
        authorization_request = self._authorization_request(request) if voice_request else None
        requested_feedback = extract_owner_feedback(request.text)
        feedback_target_id = None
        feedback_to_apply = None
        repair_attempt = False
        if requested_feedback is not None:
            if voice_request and not owner_verified:
                feedback_status = FEEDBACK_OWNER_UNVERIFIED
            else:
                feedback_target_id = registry.latest_feedback_target(resolved.conversation_id)
                feedback_status = (
                    FEEDBACK_TARGET_AVAILABLE
                    if feedback_target_id is not None
                    else FEEDBACK_TARGET_MISSING
                )
                if feedback_target_id is not None:
                    feedback_to_apply = requested_feedback
            request = request.model_copy(
                update={
                    "metadata": {
                        **request.metadata,
                        FEEDBACK_STATUS_METADATA: feedback_status,
                    }
                }
            )
        elif self._repair_windows.pop(resolved.conversation_id, None) is not None:
            repair_attempt = True
            request = request.model_copy(
                update={
                    "metadata": {
                        **request.metadata,
                        REPAIR_CONTEXT_METADATA: True,
                    }
                }
            )
        return PreparedJobAdmission(
            request=request,
            conversation_id=resolved.conversation_id,
            authorization_request=authorization_request,
            voice_request=voice_request,
            owner_verified=owner_verified,
            owner_feedback_request=requested_feedback is not None,
            repair_attempt=repair_attempt,
            feedback_target_id=feedback_target_id,
            feedback_to_apply=feedback_to_apply,
        )

    def remember_public_sources(
        self,
        job: MutableJob,
        urls: tuple[str, ...],
        *,
        now: datetime,
    ) -> None:
        existing = self._recent_public_sources.get(job.conversation_id)
        if existing is not None and existing.source_request_created_at > job.created_at:
            return
        if not urls:
            self._recent_public_sources.pop(job.conversation_id, None)
            return
        self._recent_public_sources[job.conversation_id] = RecentPublicSources(
            urls=urls,
            source_request_created_at=job.created_at,
            expires_at=now + RECENT_PUBLIC_SOURCES_TTL,
        )

    def apply_owner_feedback(
        self,
        feedback_job: MutableJob,
        *,
        registry: JobRegistry,
        evaluation_store: EvaluationStore | None,
        now: datetime,
    ) -> None:
        if feedback_job.feedback_target_id is None or feedback_job.feedback_to_apply is None:
            return
        target = registry.get(feedback_job.feedback_target_id)
        if target is None or target.evaluation is None:
            return
        target.evaluation = target.evaluation.model_copy(
            update={"owner_feedback": feedback_job.feedback_to_apply}
        )
        if feedback_job.feedback_to_apply is OwnerFeedback.UNHELPFUL:
            self._repair_windows[feedback_job.conversation_id] = now + REPAIR_WINDOW_TTL
        else:
            self._repair_windows.pop(feedback_job.conversation_id, None)
        if evaluation_store is not None:
            try:
                evaluation_store.append(
                    target.job_id,
                    target.evaluation,
                    now,
                )
            except Exception:
                LOGGER.warning("owner_feedback_write_failed")

    def clear(self) -> None:
        self._repair_windows.clear()
        self._recent_public_sources.clear()

    async def _resolve_persistent_conversation(self, conversation_id: UUID | None) -> UUID:
        conversations = self._conversations
        if conversations is None:
            raise JobError("conversation coordinator is unavailable")
        try:
            if conversation_id is not None:
                try:
                    await conversations.ensure_exists(conversation_id)
                except MemoryNotFoundError:
                    conversation_id = None
            if conversation_id is None:
                conversation_id = (await conversations.create()).conversation_id
        except (ConversationCapacityError, MemoryStoreError) as error:
            raise JobError("conversation store is unavailable") from error
        return conversation_id

    async def _require_existing_conversation(self, conversation_id: UUID) -> None:
        conversations = self._conversations
        if conversations is None:
            raise JobError("conversation coordinator is unavailable")
        try:
            await conversations.ensure_exists(conversation_id)
        except MemoryNotFoundError as error:
            raise JobConversationNotFoundError("conversation does not exist") from error
        except MemoryStoreError as error:
            raise JobError("conversation store is unavailable") from error

    def _expire(self, now: datetime) -> None:
        expired_repairs = [
            conversation_id
            for conversation_id, expires_at in self._repair_windows.items()
            if expires_at <= now
        ]
        for conversation_id in expired_repairs:
            self._repair_windows.pop(conversation_id, None)
        expired_sources = [
            conversation_id
            for conversation_id, sources in self._recent_public_sources.items()
            if sources.expires_at <= now
        ]
        for conversation_id in expired_sources:
            self._recent_public_sources.pop(conversation_id, None)

    def _resolve_public_source_reference(
        self,
        request: UserRequest,
        conversation_id: UUID | None,
    ) -> UserRequest:
        source_index = public_source_reference_index(request)
        if source_index is None:
            return request
        recent = self._recent_public_sources.get(conversation_id)
        source_url = (
            recent.urls[source_index]
            if recent is not None and source_index < len(recent.urls)
            else None
        )
        source_metadata: dict[str, object] = {
            PUBLIC_SOURCE_STATUS_METADATA: PUBLIC_SOURCE_MISSING,
        }
        if source_url is not None:
            source_metadata.update(
                {
                    PUBLIC_SOURCE_STATUS_METADATA: PUBLIC_SOURCE_AVAILABLE,
                    PUBLIC_SOURCE_URL_METADATA: source_url,
                }
            )
        return request.model_copy(
            update={"metadata": {**request.metadata, **source_metadata}}
        )

    @staticmethod
    def _strip_internal_source_metadata(request: UserRequest) -> UserRequest:
        return request.model_copy(
            update={
                "metadata": {
                    key: value
                    for key, value in request.metadata.items()
                    if key
                    not in {
                        PUBLIC_SOURCE_STATUS_METADATA,
                        PUBLIC_SOURCE_URL_METADATA,
                    }
                }
            }
        )

    @staticmethod
    def _authorization_request(request: UserRequest) -> UserRequest:
        return UserRequest(
            request_id=request.request_id,
            text="voice authorization context",
            modalities=frozenset({InputModality.TEXT, InputModality.AUDIO}),
            metadata={
                key: request.metadata[key]
                for key in (
                    "speaker_identity",
                    "sole_speaker_profile",
                    "owner_speaker_profile",
                    "owner_presence_verified",
                )
                if key in request.metadata
            },
        )
