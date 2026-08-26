from __future__ import annotations

from datetime import datetime
from enum import StrEnum
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


class SpeechEventType(StrEnum):
    STARTED = "started"
    ENDED = "ended"


class SpeechActivityEvent(BaseModel):
    """A non-transcriptive boundary event emitted by the native VAD."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    event: SpeechEventType
    utterance_id: UUID
    sample_sequence: int = Field(ge=0, le=2**63 - 1)
    monotonic_nanoseconds: int = Field(ge=0, le=2**63 - 1)
    duration_milliseconds: int | None = Field(default=None, ge=0, le=300_000)

    @model_validator(mode="after")
    def duration_must_match_event(self) -> SpeechActivityEvent:
        if self.event is SpeechEventType.STARTED and self.duration_milliseconds is not None:
            raise ValueError("speech start cannot contain a duration")
        if self.event is SpeechEventType.ENDED and self.duration_milliseconds is None:
            raise ValueError("speech end requires a duration")
        return self


class LocalTranscriptEvent(BaseModel):
    """A final transcript asserted to have been produced on this Mac."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    type: Literal["speech.transcript"] = "speech.transcript"
    capture_id: UUID
    sequence: int = Field(ge=0, le=2**63 - 1)
    text: str = Field(min_length=1, max_length=4_096)
    locale_identifier: str = Field(
        min_length=2,
        max_length=35,
        pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,2}$",
    )
    duration_milliseconds: int = Field(ge=0, le=60_000)
    is_final: Literal[True] = True
    on_device: Literal[True] = True
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    speaker_id: str | None = Field(
        default=None,
        min_length=2,
        max_length=32,
        pattern=r"^[a-z0-9][a-z0-9_-]{1,31}$",
    )
    speaker_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    sole_speaker_profile: bool = False

    @field_validator("text")
    @classmethod
    def text_must_be_normalized(cls, value: str) -> str:
        if value != " ".join(value.split()):
            raise ValueError("transcript text must use normalized whitespace")
        return value

    @field_validator("confidence", "speaker_confidence")
    @classmethod
    def confidence_must_be_finite(cls, value: float | None) -> float | None:
        if value is not None and not (-float("inf") < value < float("inf")):
            raise ValueError("transcript confidence must be finite")
        return value

    @model_validator(mode="after")
    def speaker_fields_must_be_paired(self) -> LocalTranscriptEvent:
        if (self.speaker_id is None) != (self.speaker_confidence is None):
            raise ValueError("speaker identity and confidence must be present together")
        if self.sole_speaker_profile and self.speaker_id is None:
            raise ValueError("sole speaker profile requires a speaker identity")
        return self


class AudioSessionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["idle", "active"]
    session_id: UUID | None = None
    opened_at: datetime | None = None
    samples_received: int = Field(default=0, ge=0)
    last_received_at: datetime | None = None
    last_sample: AudioMeterSample | None = None
    speech_state: Literal["idle", "speaking"] = "idle"
    active_utterance_id: UUID | None = None
    last_speech_event: SpeechActivityEvent | None = None
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
            for value in (
                self.session_id,
                self.opened_at,
                self.last_received_at,
                self.last_sample,
                self.active_utterance_id,
                self.last_speech_event,
            )
        ):
            raise ValueError("idle audio snapshot cannot contain session data")
        if self.state == "idle" and (self.samples_received != 0 or self.stale):
            raise ValueError("idle audio snapshot counters must be empty")
        if self.state == "idle" and self.speech_state != "idle":
            raise ValueError("idle audio snapshot cannot report speech")
        if self.state == "active" and (self.session_id is None or self.opened_at is None):
            raise ValueError("active audio snapshot requires session identity")
        if (self.last_sample is None) != (self.last_received_at is None):
            raise ValueError("audio sample and receipt timestamp must be present together")
        if (self.speech_state == "speaking") != (self.active_utterance_id is not None):
            raise ValueError("active utterance must match speech state")
        if self.last_speech_event is not None:
            event_reports_speaking = self.last_speech_event.event is SpeechEventType.STARTED
            if event_reports_speaking != (self.speech_state == "speaking"):
                raise ValueError("last speech event must match speech state")
            if event_reports_speaking and (
                self.last_speech_event.utterance_id != self.active_utterance_id
            ):
                raise ValueError("speech event must match active utterance")
        return self
