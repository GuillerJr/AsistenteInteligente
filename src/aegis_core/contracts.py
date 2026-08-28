from __future__ import annotations

import base64
import binascii
import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AgentRole(StrEnum):
    ROUTER = "router"
    PLANNER = "planner"
    CRITICAL_REASONER = "critical_reasoner"
    CODE_SECURITY = "code_security"
    VISION = "vision"
    OMNI = "omni"
    SYNTHESIZER = "synthesizer"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class InputModality(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"


class Capability(StrEnum):
    SYSTEM_READ = "system_read"
    SYSTEM_CONTROL = "system_control"
    FILESYSTEM_READ = "filesystem_read"
    WEB_READ = "web_read"
    MAIL_READ = "mail_read"
    MAIL_WRITE = "mail_write"
    CALENDAR_READ = "calendar_read"
    CALENDAR_WRITE = "calendar_write"
    CONTACTS_READ = "contacts_read"
    CONTACTS_WRITE = "contacts_write"
    REMINDERS_READ = "reminders_read"
    REMINDERS_WRITE = "reminders_write"
    APPLICATION_CONTROL = "application_control"
    NETWORK_DISCOVERY = "network_discovery"
    PROCESS_EXECUTION = "process_execution"
    SYSTEM_ADMIN = "system_admin"
    SCREEN_CAPTURE = "screen_capture"
    AUDIO_CAPTURE = "audio_capture"
    MEMORY_QUERY = "memory_query"


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    REQUIRE_CONFIRMATION = "require_confirmation"
    DENY = "deny"


class ToolCallBasis(StrEnum):
    MODEL_PROPOSED = "model_proposed"
    EXPLICIT_LOCAL_INTENT = "explicit_local_intent"


MAX_IMAGE_BYTES = 32_768
MAX_IMAGE_BASE64_CHARS = ((MAX_IMAGE_BYTES + 2) // 3) * 4
MAX_TOOL_CALLS_PER_RESULT = 1
IMAGE_SIGNATURES = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/webp": (b"RIFF",),
}


class ImageInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    media_type: str
    data_base64: str = Field(min_length=4, max_length=MAX_IMAGE_BASE64_CHARS)

    @model_validator(mode="after")
    def validate_image(self) -> ImageInput:
        signatures = IMAGE_SIGNATURES.get(self.media_type)
        if signatures is None:
            raise ValueError("unsupported image media type")
        try:
            decoded = base64.b64decode(self.data_base64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("image data must be canonical base64") from error
        if not decoded or len(decoded) > MAX_IMAGE_BYTES:
            raise ValueError("image data size is out of range")
        if base64.b64encode(decoded).decode("ascii") != self.data_base64:
            raise ValueError("image data must be canonical base64")
        if self.media_type == "image/webp":
            valid_signature = (
                len(decoded) >= 12
                and decoded.startswith(signatures[0])
                and decoded[8:12] == b"WEBP"
            )
        else:
            valid_signature = any(decoded.startswith(signature) for signature in signatures)
        if not valid_signature:
            raise ValueError("image signature does not match media type")
        return self

    @property
    def data_uri(self) -> str:
        return f"data:{self.media_type};base64,{self.data_base64}"


class UserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: UUID = Field(default_factory=uuid4)
    text: str = Field(min_length=1, max_length=100_000)
    modalities: frozenset[InputModality] = Field(
        default_factory=lambda: frozenset({InputModality.TEXT})
    )
    image: ImageInput | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def image_must_match_modality(self) -> UserRequest:
        has_image_modality = InputModality.IMAGE in self.modalities
        if has_image_modality != (self.image is not None):
            raise ValueError("image attachment and modality must be provided together")
        return self


class RouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: AgentRole
    risk: RiskLevel
    reason: str = Field(min_length=1, max_length=1_000)
    requires_confirmation: bool = False


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: str = Field(min_length=1, max_length=256)
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    arguments: dict[str, Any] = Field(default_factory=dict)
    requested_by: AgentRole
    authorization_basis: ToolCallBasis = ToolCallBasis.MODEL_PROPOSED

    @field_validator("arguments")
    @classmethod
    def arguments_must_be_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError("tool arguments must be JSON serializable") from error
        return value

    def digest(self) -> str:
        canonical = json.dumps(
            {
                "arguments": self.arguments,
                "call_id": self.call_id,
                "requested_by": self.requested_by.value,
                "tool_name": self.tool_name,
                "authorization_basis": self.authorization_basis.value,
            },
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ConfirmationGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    grant_id: UUID = Field(default_factory=uuid4)
    call_id: str = Field(min_length=1, max_length=256)
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    call_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_by: str = Field(min_length=1, max_length=128)
    issued_at: datetime
    expires_at: datetime

    @field_validator("issued_at", "expires_at")
    @classmethod
    def timestamps_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("confirmation timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def expiry_must_follow_issue(self) -> ConfirmationGrant:
        if self.expires_at <= self.issued_at:
            raise ValueError("confirmation expiry must follow issue time")
        return self


class ToolAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: str
    tool_name: str
    call_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: PolicyDecision
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    normalized_arguments: dict[str, Any] = Field(default_factory=dict)


class ToolExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: str = Field(min_length=1, max_length=256)
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    success: bool
    output: str = Field(default="", max_length=1_048_576)
    error_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{2,63}$")
    metadata: dict[str, str | int | bool] = Field(default_factory=dict)


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: AgentRole
    model_id: str
    content: str
    finish_reason: str | None = None
    raw_usage: dict[str, int] = Field(default_factory=dict)
    tool_calls: tuple[ToolCall, ...] = Field(
        default_factory=tuple,
        max_length=MAX_TOOL_CALLS_PER_RESULT,
    )
