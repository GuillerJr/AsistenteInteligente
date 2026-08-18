from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AudioMeterSample(BaseModel):
    """A bounded, non-reconstructive acoustic measurement from the native host."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    sequence: int = Field(ge=0, le=2**63 - 1)
    monotonic_nanoseconds: int = Field(ge=0, le=2**63 - 1)
    sample_rate_hz: float = Field(ge=8_000, le=192_000)
    frame_count: int = Field(ge=1, le=65_536)
    rms: float = Field(ge=0.0, le=1.0)
    peak: float = Field(ge=0.0, le=1.0)
    dbfs: float = Field(ge=-120.0, le=0.0)
    activity: float = Field(ge=0.0, le=1.0)
    voice_active: bool
    clipped: bool

    @field_validator("sample_rate_hz", "rms", "peak", "dbfs", "activity")
    @classmethod
    def values_must_be_finite(cls, value: float) -> float:
        if not (-float("inf") < value < float("inf")):
            raise ValueError("audio meter values must be finite")
        return value

    @model_validator(mode="after")
    def peak_must_not_be_below_rms(self) -> AudioMeterSample:
        if self.peak + 1e-6 < self.rms:
            raise ValueError("audio peak cannot be below RMS")
        return self


class AudioSessionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["idle", "active"]
    session_id: UUID | None = None
    opened_at: datetime | None = None
    samples_received: int = Field(default=0, ge=0)
    last_received_at: datetime | None = None
    last_sample: AudioMeterSample | None = None
    stale: bool = False

    @field_validator("opened_at", "last_received_at")
    @classmethod
    def timestamps_must_be_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("audio timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def fields_must_match_state(self) -> AudioSessionSnapshot:
        if self.state == "idle" and any(
            value is not None
            for value in (self.session_id, self.opened_at, self.last_received_at, self.last_sample)
        ):
            raise ValueError("idle audio snapshot cannot contain session data")
        if self.state == "idle" and (self.samples_received != 0 or self.stale):
            raise ValueError("idle audio snapshot counters must be empty")
        if self.state == "active" and (self.session_id is None or self.opened_at is None):
            raise ValueError("active audio snapshot requires session identity")
        if (self.last_sample is None) != (self.last_received_at is None):
            raise ValueError("audio sample and receipt timestamp must be present together")
        return self
