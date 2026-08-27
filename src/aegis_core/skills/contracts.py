from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aegis_core.contracts import AgentRole
from aegis_core.secrets import contains_likely_secret_material

SKILL_ID_PATTERN = r"^[a-z][a-z0-9-]{2,63}$"
TOOL_NAME_PATTERN = r"^[a-z][a-z0-9_-]{2,63}$"
_UNSAFE_INSTRUCTION_PATTERNS = (
    "bypass the broker",
    "bypass tool broker",
    "ignore system",
    "ignore previous",
    "omite la confirmación",
    "omite la confirmacion",
    "sin confirmación",
    "sin confirmacion",
    "skip confirmation",
)


class SkillOrigin(StrEnum):
    BUILTIN = "builtin"
    LEARNED = "learned"


class SkillBody(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    skill_id: str = Field(pattern=SKILL_ID_PATTERN)
    name: str = Field(min_length=3, max_length=80)
    description: str = Field(min_length=8, max_length=300)
    role: AgentRole
    trigger_phrases: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    trigger_terms: frozenset[str] = Field(default_factory=frozenset, max_length=32)
    minimum_term_matches: int = Field(default=2, ge=1, le=4)
    instructions: tuple[str, ...] = Field(min_length=1, max_length=12)
    allowed_tools: frozenset[str] = Field(default_factory=frozenset, max_length=16)
    starter_tools: frozenset[str] = Field(default_factory=frozenset, max_length=3)
    priority: int = Field(default=50, ge=0, le=100)
    enabled: bool = True

    @field_validator("name", "description")
    @classmethod
    def prose_must_be_normalized(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if normalized != value or not value.isprintable():
            raise ValueError("skill prose must use normalized printable text")
        return value

    @field_validator("trigger_phrases")
    @classmethod
    def phrases_must_be_normalized(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(" ".join(value.casefold().split()) for value in values)
        if any(
            len(value) < 3 or len(value) > 120 or not value.isprintable() or value != original
            for value, original in zip(normalized, values, strict=True)
        ):
            raise ValueError("skill trigger phrases must be normalized lowercase text")
        if len(normalized) != len(set(normalized)):
            raise ValueError("skill trigger phrases must be unique")
        return normalized

    @field_validator("trigger_terms")
    @classmethod
    def terms_must_be_normalized(cls, values: frozenset[str]) -> frozenset[str]:
        if any(
            len(value) < 2
            or len(value) > 40
            or value != value.casefold()
            or re.fullmatch(r"[^\W_]+", value, flags=re.UNICODE) is None
            for value in values
        ):
            raise ValueError("skill trigger terms must be lowercase words")
        return values

    @field_validator("instructions")
    @classmethod
    def instructions_must_be_safe(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            normalized = " ".join(value.split())
            lowered = normalized.casefold()
            if (
                normalized != value
                or len(value) < 8
                or len(value) > 400
                or not value.isprintable()
                or contains_likely_secret_material(value)
                or any(pattern in lowered for pattern in _UNSAFE_INSTRUCTION_PATTERNS)
            ):
                raise ValueError("skill instruction is unsafe or malformed")
        return values

    @field_validator("allowed_tools", "starter_tools")
    @classmethod
    def tool_names_must_be_valid(cls, values: frozenset[str]) -> frozenset[str]:
        if any(re.fullmatch(TOOL_NAME_PATTERN, value) is None for value in values):
            raise ValueError("skill tool name is invalid")
        return values

    @model_validator(mode="after")
    def relationships_must_be_bounded(self) -> SkillBody:
        if not self.trigger_phrases and not self.trigger_terms:
            raise ValueError("skill requires at least one trigger")
        if self.trigger_terms and self.minimum_term_matches > len(self.trigger_terms):
            raise ValueError("minimum term matches exceeds trigger terms")
        if not self.starter_tools.issubset(self.allowed_tools):
            raise ValueError("starter tools must be inside the skill allowlist")
        if self.role in {AgentRole.ROUTER, AgentRole.SYNTHESIZER}:
            raise ValueError("skill role cannot route or synthesize")
        return self


class SkillDraft(SkillBody):
    """Owner-authored declarative lesson. It cannot contain executable code."""


class SkillManifest(SkillBody):
    origin: SkillOrigin
    remote_safe: bool = False

    @model_validator(mode="after")
    def learned_skill_must_stay_local(self) -> SkillManifest:
        if self.origin is SkillOrigin.LEARNED and self.remote_safe:
            raise ValueError("learned skills cannot be sent to remote models")
        return self


class SkillActivation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest: SkillManifest
    score: int = Field(ge=1, le=10_000)
    matched_phrases: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    matched_terms: frozenset[str] = Field(default_factory=frozenset, max_length=32)
