from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


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


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: AgentRole
    model_id: str
    content: str
    finish_reason: str | None = None
    raw_usage: dict[str, int] = Field(default_factory=dict)
