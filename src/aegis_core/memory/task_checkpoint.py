"""Bounded, encrypted conversation state; never an authorization or an executor."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ResultReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["mail", "file", "event"]
    identifier: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_.:@/-]+$")
    label: str = Field(default="", max_length=180)
    position: int = Field(ge=1, le=20)
    container_id: str | None = Field(default=None, min_length=1, max_length=256,
                                     pattern=r"^[A-Za-z0-9_.:@/-]+$")


class VerifiedTaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str = Field(min_length=1, max_length=256)
    call_id: str = Field(min_length=1, max_length=256)
    observed_at: datetime
    excerpt: str = Field(max_length=3_072)
    truncated: bool = False
    arguments_json: str = Field(default="", max_length=3_072)
    arguments_truncated: bool = False
    references: tuple[ResultReference, ...] = Field(default=(), max_length=20)

    @field_validator("observed_at")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("task evidence requires an aware timestamp")
        return value


class TaskStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instruction: str = Field(min_length=1, max_length=512)
    tool_suffixes: tuple[str, ...] = Field(min_length=1, max_length=3)
    verified: bool = False


class TaskCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    active_intent: str = Field(min_length=1, max_length=2_048)
    status: Literal["answered", "needs_action", "verified", "cancelled"]
    next_action: str = Field(default="", max_length=512)
    sequence: int = Field(ge=1)
    results: tuple[VerifiedTaskResult, ...] = Field(default=(), max_length=3)
    steps: tuple[TaskStep, ...] = Field(default=(), max_length=4)
    updated_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())

    @field_validator("updated_at")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("task checkpoint requires an aware timestamp")
        return value


class TaskRecovery(BaseModel):
    """An interruption journal, never a reusable approval or executable tool call."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    request_text: str = Field(max_length=2_048)
    tool_name: str = Field(max_length=256)
    call_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["awaiting_confirmation", "executing", "observed", "completed"]
    observation: VerifiedTaskResult | None = None
