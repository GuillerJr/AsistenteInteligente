from __future__ import annotations

import hashlib
import re
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_MEMORY_CONTENT_BYTES = 16_384
MAX_MEMORY_EXCERPT_BYTES = 768
NAMESPACE_PATTERN = r"^[a-z][a-z0-9_.-]{0,63}$"
TAG_PATTERN = r"^[a-z0-9][a-z0-9_.-]{0,31}$"


class MemoryKind(StrEnum):
    EPISODIC = "episodic"
    PREFERENCE = "preference"
    SEMANTIC = "semantic"
    SUMMARY = "summary"


class MemoryEvidence(StrEnum):
    EXPLICIT_TEXT = "explicit_text"
    VERIFIED_VOICE = "verified_voice"
    USER_CONFIRMED = "user_confirmed"
    IMPORTED = "imported"
    INFERRED = "inferred"


class ConversationRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class ConversationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: UUID = Field(default_factory=uuid4)
    namespace: str = Field(pattern=NAMESPACE_PATTERN)
    title: str | None = Field(default=None, min_length=1, max_length=128)
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def timestamps_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("conversation timestamps must be timezone-aware")
        return value


class ConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    turn_id: UUID = Field(default_factory=uuid4)
    conversation_id: UUID
    sequence: int = Field(ge=1)
    role: ConversationRole
    content: str = Field(min_length=1, max_length=MAX_MEMORY_CONTENT_BYTES)
    created_at: datetime
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("content")
    @classmethod
    def content_must_fit_byte_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_MEMORY_CONTENT_BYTES:
            raise ValueError("conversation turn exceeds UTF-8 byte limit")
        return value

    @field_validator("created_at")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("conversation timestamp must be timezone-aware")
        return value

    @classmethod
    def digest_content(cls, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


class MemoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: UUID = Field(default_factory=uuid4)
    namespace: str = Field(pattern=NAMESPACE_PATTERN)
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=MAX_MEMORY_CONTENT_BYTES)
    source: str | None = Field(default=None, min_length=1, max_length=256)
    tags: tuple[str, ...] = Field(default=(), max_length=16)
    created_at: datetime
    updated_at: datetime
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: MemoryEvidence = MemoryEvidence.EXPLICIT_TEXT
    expires_at: datetime | None = None
    last_confirmed_at: datetime | None = None

    @field_validator("content")
    @classmethod
    def content_must_fit_byte_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_MEMORY_CONTENT_BYTES:
            raise ValueError("memory content exceeds UTF-8 byte limit")
        return value

    @field_validator("tags")
    @classmethod
    def tags_must_be_normalized(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("memory tags must be unique")
        for tag in value:
            if not re.fullmatch(TAG_PATTERN, tag):
                raise ValueError("invalid memory tag")
        return value

    @field_validator("created_at", "updated_at")
    @classmethod
    def timestamps_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("memory timestamps must be timezone-aware")
        return value

    @field_validator("expires_at", "last_confirmed_at")
    @classmethod
    def optional_timestamps_must_be_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("memory timestamp must be timezone-aware")
        return value

    @classmethod
    def digest_content(cls, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


class MemorySearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: UUID
    namespace: str = Field(pattern=NAMESPACE_PATTERN)
    kind: MemoryKind
    excerpt: str = Field(max_length=MAX_MEMORY_EXCERPT_BYTES)
    source: str | None = Field(default=None, max_length=256)
    tags: tuple[str, ...] = Field(default=(), max_length=16)
    updated_at: datetime
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    score: float = Field(ge=0.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: MemoryEvidence = MemoryEvidence.EXPLICIT_TEXT
    expires_at: datetime | None = None
    last_confirmed_at: datetime | None = None

    @field_validator("excerpt")
    @classmethod
    def excerpt_must_fit_byte_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_MEMORY_EXCERPT_BYTES:
            raise ValueError("memory excerpt exceeds UTF-8 byte limit")
        return value

    @field_validator("updated_at")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("memory timestamp must be timezone-aware")
        return value

    @field_validator("expires_at", "last_confirmed_at")
    @classmethod
    def optional_hit_timestamps_must_be_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("memory timestamp must be timezone-aware")
        return value
