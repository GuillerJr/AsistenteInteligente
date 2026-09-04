from __future__ import annotations

import base64
import binascii
import json
from collections import deque
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aegis_core.audio.contracts import LocalTranscriptEvent, LocalVoiceContext
from aegis_core.contracts import ImageInput, InputModality, UserRequest
from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.job_contracts import (
    MAX_JOB_WAIT_SECONDS,
    JobCapacityError,
    JobConfirmationError,
    JobConversationNotFoundError,
    JobError,
    JobNotFoundError,
    JobSnapshot,
)
from aegis_core.memory.contracts import MAX_MEMORY_CONTENT_BYTES
from aegis_core.memory.profile import OwnerProfile
from aegis_core.secrets import contains_likely_secret_material

MAX_IMAGE_SUBMIT_PAYLOAD_BYTES = 60_000
MAX_RECENT_VOICE_CAPTURES = 256


class SubmitJobPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=50_000)
    modalities: frozenset[InputModality] = Field(
        default_factory=lambda: frozenset({InputModality.TEXT})
    )
    conversation_id: UUID | None = None

    @model_validator(mode="after")
    def conversation_text_must_fit_persistent_limit(self) -> SubmitJobPayload:
        if (
            self.conversation_id is not None
            and len(self.text.encode("utf-8")) > MAX_MEMORY_CONTENT_BYTES
        ):
            raise ValueError("conversation request exceeds persistent turn limit")
        return self


class JobIdPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: UUID


class JobWaitPayload(JobIdPayload):
    model_config = ConfigDict(extra="forbid", frozen=True)

    after_stream_version: int = Field(ge=0, le=100_000)
    timeout_milliseconds: int = Field(ge=100, le=MAX_JOB_WAIT_SECONDS * 1_000)


class JobApprovalPayload(JobIdPayload):
    call_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class VoiceJobApprovalPayload(JobApprovalPayload):
    model_config = ConfigDict(extra="forbid", frozen=True)

    encoding: Literal["pcm_s16le"]
    sample_rate_hz: Literal[16_000]
    channels: Literal[1]
    pcm_base64: str = Field(min_length=10_668, max_length=128_000)
    speaker_identifier: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,31}$")
    speaker_confidence: float = Field(ge=0, le=1)
    owner_profile_match: bool

    def decoded_pcm(self) -> bytes:
        try:
            pcm = base64.b64decode(self.pcm_base64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("voice approval PCM is not valid base64") from error
        if len(pcm) % 2 or not 8_000 <= len(pcm) <= 96_000:
            raise ValueError("voice approval PCM is outside the bounded duration")
        return pcm


class VoiceSubmitPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    transcript: LocalTranscriptEvent
    conversation_id: UUID | None = None
    persist_conversation: bool = False
    active_application_bundle_identifier: str | None = Field(
        default=None,
        min_length=3,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]+$",
    )
    preferred_browser_bundle_identifier: (
        Literal[
            "com.apple.Safari",
            "com.google.Chrome",
            "com.parent.arc",
            "company.thebrowser.Browser",
            "org.mozilla.firefox",
        ]
        | None
    ) = None


class ImageSubmitPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=4_096)
    image: ImageInput
    voice_context: LocalVoiceContext | None = None
    conversation_id: UUID | None = None
    persist_conversation: bool = False

    @model_validator(mode="after")
    def conversation_text_must_fit_persistent_limit(self) -> ImageSubmitPayload:
        payload_bytes = len(
            json.dumps(
                self.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if payload_bytes > MAX_IMAGE_SUBMIT_PAYLOAD_BYTES:
            raise ValueError("image request exceeds IPC payload limit")
        if (
            self.conversation_id is not None
            and len(self.text.encode("utf-8")) > MAX_MEMORY_CONTENT_BYTES
        ):
            raise ValueError("conversation request exceeds persistent turn limit")
        return self


class JobManagerProtocol(Protocol):
    async def metrics(self) -> dict[str, Any]: ...

    async def submit(
        self,
        request: UserRequest,
        *,
        conversation_id: UUID | None = None,
        persist_conversation: bool = False,
    ) -> JobSnapshot: ...

    async def approve(self, job_id: UUID, call_digest: str) -> JobSnapshot: ...

    async def approve_by_voice(
        self,
        *,
        job_id: UUID,
        call_digest: str,
        pcm: bytes,
        speaker_identifier: str,
        speaker_confidence: float,
        owner_profile_match: bool,
    ) -> JobSnapshot: ...

    async def wait_for_change(
        self,
        job_id: UUID,
        *,
        after_stream_version: int,
        timeout_seconds: float,
    ) -> JobSnapshot: ...

    async def status(self, job_id: UUID) -> JobSnapshot: ...

    async def cancel(self, job_id: UUID) -> JobSnapshot: ...


class SwarmIpcService:
    """Strict IPC adapter for the job manager's public operations."""

    WAIT_METHOD = "jobs.wait"
    MAX_WAIT_SECONDS = MAX_JOB_WAIT_SECONDS
    METHODS = frozenset(
        {
            "swarm.submit",
            "voice.submit",
            "image.submit",
            "jobs.status",
            WAIT_METHOD,
            "jobs.cancel",
            "jobs.approve",
            "jobs.approve.voice",
            "jobs.metrics",
        }
    )

    def __init__(self, jobs: JobManagerProtocol) -> None:
        self._jobs = jobs
        self._voice_capture_ids: deque[UUID] = deque(maxlen=MAX_RECENT_VOICE_CAPTURES)

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {method: self.handle for method in self.METHODS}

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        try:
            if request.method == "jobs.metrics":
                if request.payload:
                    return IpcHandlerResult(ok=False, error_code="invalid_payload")
                return IpcHandlerResult(ok=True, payload=await self._jobs.metrics())
            if request.method in {"swarm.submit", "voice.submit", "image.submit"}:
                image = None
                voice_capture_id = None
                if request.method == "voice.submit":
                    voice_payload = VoiceSubmitPayload.model_validate(request.payload)
                    text = voice_payload.transcript.text
                    voice_capture_id = voice_payload.transcript.capture_id
                    modalities = frozenset({InputModality.TEXT, InputModality.AUDIO})
                    conversation_id = voice_payload.conversation_id
                    persist_conversation = voice_payload.persist_conversation
                    voice_metadata = {
                        "speech_capture_id": str(voice_payload.transcript.capture_id),
                        "speech_locale": voice_payload.transcript.locale_identifier,
                        "speech_on_device": True,
                        **(
                            {
                                "speaker_identity": {
                                    "confidence": voice_payload.transcript.speaker_confidence,
                                    "id": voice_payload.transcript.speaker_id,
                                }
                            }
                            if voice_payload.transcript.speaker_id is not None
                            else {}
                        ),
                        "sole_speaker_profile": voice_payload.transcript.sole_speaker_profile,
                        "owner_speaker_profile": voice_payload.transcript.owner_speaker_profile,
                        "owner_presence_verified": (
                            voice_payload.transcript.owner_presence_verified
                        ),
                        **(
                            {
                                "active_application_bundle_identifier": (
                                    voice_payload.active_application_bundle_identifier
                                )
                            }
                            if voice_payload.active_application_bundle_identifier is not None
                            else {}
                        ),
                        **(
                            {
                                "preferred_browser_bundle_identifier": (
                                    voice_payload.preferred_browser_bundle_identifier
                                )
                            }
                            if voice_payload.preferred_browser_bundle_identifier is not None
                            else {}
                        ),
                    }
                elif request.method == "image.submit":
                    image_payload = ImageSubmitPayload.model_validate(request.payload)
                    text = image_payload.text
                    conversation_id = image_payload.conversation_id
                    persist_conversation = image_payload.persist_conversation
                    image = image_payload.image
                    voice_context = image_payload.voice_context
                    if voice_context is None:
                        modalities = frozenset({InputModality.TEXT, InputModality.IMAGE})
                        voice_metadata = {}
                    else:
                        voice_capture_id = voice_context.capture_id
                        modalities = frozenset(
                            {
                                InputModality.TEXT,
                                InputModality.AUDIO,
                                InputModality.IMAGE,
                            }
                        )
                        voice_metadata = {
                            "speech_capture_id": str(voice_context.capture_id),
                            "speech_locale": voice_context.locale_identifier,
                            "speech_on_device": True,
                            **(
                                {
                                    "speaker_identity": {
                                        "confidence": voice_context.speaker_confidence,
                                        "id": voice_context.speaker_id,
                                    }
                                }
                                if voice_context.speaker_id is not None
                                else {}
                            ),
                            "sole_speaker_profile": voice_context.sole_speaker_profile,
                            "owner_speaker_profile": voice_context.owner_speaker_profile,
                            "owner_presence_verified": voice_context.owner_presence_verified,
                        }
                else:
                    payload = SubmitJobPayload.model_validate(request.payload)
                    text = payload.text
                    modalities = payload.modalities
                    conversation_id = payload.conversation_id
                    persist_conversation = False
                    voice_metadata = {}
                if contains_likely_secret_material(text):
                    return IpcHandlerResult(
                        ok=False,
                        error_code="secret_material_rejected",
                    )
                if voice_capture_id is not None:
                    if voice_capture_id in self._voice_capture_ids:
                        return IpcHandlerResult(
                            ok=False,
                            error_code="voice_capture_replayed",
                        )
                    self._voice_capture_ids.append(voice_capture_id)
                user_request = UserRequest(
                    text=text,
                    modalities=modalities,
                    image=image,
                    metadata={
                        "ipc_request_id": str(request.request_id),
                        **voice_metadata,
                        **(
                            {"conversation_id": str(conversation_id)}
                            if conversation_id is not None
                            else {}
                        ),
                    },
                )
                if (
                    persist_conversation
                    and InputModality.AUDIO in modalities
                    and not OwnerProfile.is_verified_owner_voice(user_request)
                ):
                    return IpcHandlerResult(
                        ok=False,
                        error_code="owner_verification_required",
                    )
                snapshot = await self._jobs.submit(
                    user_request,
                    conversation_id=conversation_id,
                    persist_conversation=persist_conversation,
                )
            else:
                if request.method == "jobs.approve":
                    approval = JobApprovalPayload.model_validate(request.payload)
                    snapshot = await self._jobs.approve(
                        approval.job_id,
                        approval.call_digest,
                    )
                elif request.method == "jobs.approve.voice":
                    approval = VoiceJobApprovalPayload.model_validate(request.payload)
                    snapshot = await self._jobs.approve_by_voice(
                        job_id=approval.job_id,
                        call_digest=approval.call_digest,
                        pcm=approval.decoded_pcm(),
                        speaker_identifier=approval.speaker_identifier,
                        speaker_confidence=approval.speaker_confidence,
                        owner_profile_match=approval.owner_profile_match,
                    )
                elif request.method == self.WAIT_METHOD:
                    wait = JobWaitPayload.model_validate(request.payload)
                    snapshot = await self._jobs.wait_for_change(
                        wait.job_id,
                        after_stream_version=wait.after_stream_version,
                        timeout_seconds=wait.timeout_milliseconds / 1_000,
                    )
                else:
                    payload = JobIdPayload.model_validate(request.payload)
                    if request.method == "jobs.status":
                        snapshot = await self._jobs.status(payload.job_id)
                    elif request.method == "jobs.cancel":
                        snapshot = await self._jobs.cancel(payload.job_id)
                    else:
                        return IpcHandlerResult(ok=False, error_code="method_not_found")
        except ValueError:
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        except JobNotFoundError:
            return IpcHandlerResult(ok=False, error_code="job_not_found")
        except JobConversationNotFoundError:
            return IpcHandlerResult(ok=False, error_code="conversation_not_found")
        except JobCapacityError:
            return IpcHandlerResult(ok=False, error_code="job_capacity_reached")
        except JobConfirmationError:
            return IpcHandlerResult(ok=False, error_code="confirmation_unavailable")
        except JobError:
            return IpcHandlerResult(ok=False, error_code="job_manager_unavailable")
        return IpcHandlerResult(ok=True, payload=snapshot.model_dump(mode="json"))
