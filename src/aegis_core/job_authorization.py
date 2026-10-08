from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from aegis_core.contracts import ToolAuthorization, ToolCall, UserRequest
from aegis_core.job_contracts import (
    JobConfirmationError,
    JobSnapshot,
    VoiceConfirmationVerifierProtocol,
)
from aegis_core.job_tool_presenter import JobToolPresenter
from aegis_core.job_tool_runtime import (
    ApprovedToolOutcome,
    ConfirmationConsumptionError,
    JobToolRuntime,
)
from aegis_core.tools.audit import AuditSink
from aegis_core.tools.broker import VoiceConfirmationEvidence


class ApprovalChannel(StrEnum):
    LOCAL_USER = "local_user"
    VERIFIED_OWNER_VOICE = "verified_owner_voice"
    VERIFIED_TELEGRAM = "verified_telegram"


class AuthorizationConsumptionError(JobConfirmationError):
    """The exact one-time grant was issued but could not be consumed."""


@dataclass(frozen=True, slots=True)
class ConfirmationContext:
    call: ToolCall
    authorization: ToolAuthorization
    authorization_request: UserRequest | None
    expected_call_digest: str


@dataclass(frozen=True, slots=True)
class ApprovalGrant:
    channel: ApprovalChannel
    approved_by: str
    voice_confirmation: VoiceConfirmationEvidence | None

    def __post_init__(self) -> None:
        if self.channel is ApprovalChannel.LOCAL_USER:
            valid = self.approved_by == "local-menu-bar-user" and self.voice_confirmation is None
        elif self.channel is ApprovalChannel.VERIFIED_OWNER_VOICE:
            evidence = self.voice_confirmation
            valid = (
                self.approved_by == "verified-owner-voice"
                and evidence is not None
                and evidence.authorized
            )
        elif self.channel is ApprovalChannel.VERIFIED_TELEGRAM:
            match = re.fullmatch(r"telegram-user:([1-9][0-9]{0,15})", self.approved_by)
            valid = match is not None and int(match[1]) <= 2**52 and self.voice_confirmation is None
        else:
            valid = False
        if not valid:
            raise ValueError("approval grant does not match its verified channel")


class JobAuthorizationCoordinator:
    """Owns approval-channel verification and one-time grant consumption."""

    def __init__(
        self,
        *,
        runtime: JobToolRuntime,
        presenter: JobToolPresenter,
        voice_verifier: VoiceConfirmationVerifierProtocol | None,
        audit_sink: AuditSink,
    ) -> None:
        self._runtime = runtime
        self._presenter = presenter
        self._voice_verifier = voice_verifier
        self._audit = audit_sink

    @staticmethod
    def local_user_grant() -> ApprovalGrant:
        return ApprovalGrant(
            channel=ApprovalChannel.LOCAL_USER,
            approved_by="local-menu-bar-user",
            voice_confirmation=None,
        )

    @staticmethod
    def telegram_grant(owner_id: int) -> ApprovalGrant:
        if type(owner_id) is not int or not 0 < owner_id <= 2**52:
            raise ValueError("invalid Telegram identity")
        return ApprovalGrant(ApprovalChannel.VERIFIED_TELEGRAM, f"telegram-user:{owner_id}", None)

    async def verified_voice_grant(
        self,
        pcm: bytes,
        *,
        speaker_identifier: str,
        speaker_confidence: float,
        owner_profile_match: bool,
    ) -> ApprovalGrant:
        verifier = self._voice_verifier
        if verifier is None:
            raise JobConfirmationError("voice confirmation is unavailable")
        if not isinstance(pcm, bytes) or len(pcm) % 2 or not 8_000 <= len(pcm) <= 96_000:
            raise JobConfirmationError("voice confirmation PCM is invalid")
        result = await verifier.verify(
            pcm,
            speaker_identifier=speaker_identifier,
            speaker_confidence=speaker_confidence,
            owner_profile_match=owner_profile_match,
        )
        if (
            result.authorized is not True
            or result.speaker_verified is not True
            or result.semantic_verified is not True
        ):
            raise JobConfirmationError("voice confirmation was rejected")
        return ApprovalGrant(
            channel=ApprovalChannel.VERIFIED_OWNER_VOICE,
            approved_by="verified-owner-voice",
            voice_confirmation=VoiceConfirmationEvidence(
                speaker_verified=result.speaker_verified,
                semantic_verified=result.semantic_verified,
            ),
        )

    def consume(
        self,
        confirmation: ConfirmationContext,
        *,
        received_call_digest: str,
        grant: ApprovalGrant,
    ) -> ToolAuthorization:
        try:
            return self._runtime.consume_confirmation(
                call=confirmation.call,
                pending_authorization=confirmation.authorization,
                authorization_request=confirmation.authorization_request,
                expected_call_digest=confirmation.expected_call_digest,
                received_call_digest=received_call_digest,
                approved_by=grant.approved_by,
                voice_confirmation=grant.voice_confirmation,
            )
        except ConfirmationConsumptionError as error:
            raise AuthorizationConsumptionError("confirmation could not be consumed") from error

    def confirmation_summary(
        self,
        call: ToolCall,
        authorization: ToolAuthorization,
    ) -> str | None:
        if (
            call.call_id != authorization.call_id
            or call.tool_name != authorization.tool_name
            or call.digest() != authorization.call_digest
        ):
            raise JobConfirmationError("confirmation does not match tool call")
        if not self._presenter.supports_confirmation(call.tool_name):
            return None
        return self._presenter.confirmation_summary(authorization)

    async def execute_approved(
        self,
        request_id: UUID,
        authorization: ToolAuthorization,
        *,
        request_text: str | None = None,
    ) -> ApprovedToolOutcome:
        return await self._runtime.execute(request_id, authorization, request_text=request_text)

    def record_consumed(self, snapshot: JobSnapshot, grant: ApprovalGrant) -> None:
        if grant.channel is ApprovalChannel.VERIFIED_TELEGRAM:
            self._audit.record_system_event(
                snapshot.request_id,
                event_type="telegram_tool_confirmation_consumed",
                component="tool_broker",
                data={"job_id": str(snapshot.job_id), "approved_by": grant.approved_by},
            )
            return
        if grant.channel is not ApprovalChannel.VERIFIED_OWNER_VOICE:
            return
        evidence = grant.voice_confirmation
        if evidence is None or not evidence.authorized:
            raise JobConfirmationError("voice confirmation evidence is incomplete")
        self._audit.record_system_event(
            snapshot.request_id,
            event_type="voice_tool_confirmation_consumed",
            component="tool_broker",
            data={
                "job_id": str(snapshot.job_id),
                "speaker_verified": True,
                "semantic_verified": True,
            },
        )
