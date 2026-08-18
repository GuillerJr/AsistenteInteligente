from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    FILESYSTEM_READ = "filesystem_read"
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


class UserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: UUID = Field(default_factory=uuid4)
    text: str = Field(min_length=1, max_length=100_000)
    modalities: frozenset[InputModality] = Field(
        default_factory=lambda: frozenset({InputModality.TEXT})
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


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
                "requested_by": self.requested_by.value,
                "tool_name": self.tool_name,
            },
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ConfirmationGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_by: str = Field(min_length=1, max_length=128)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def expiry_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("confirmation expiry must be timezone-aware")
        return value


class ToolAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: str
    tool_name: str
    call_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: PolicyDecision
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    normalized_arguments: dict[str, Any] = Field(default_factory=dict)


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: AgentRole
    model_id: str
    content: str
    finish_reason: str | None = None
    raw_usage: dict[str, int] = Field(default_factory=dict)
    tool_calls: tuple[ToolCall, ...] = ()
