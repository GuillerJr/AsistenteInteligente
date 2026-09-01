from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler

MAX_VISUAL_ELEMENTS = 128
MAX_VISUAL_TEXT_BYTES = 8_192


class VisionSource(StrEnum):
    SCREEN = "screen"
    CAMERA = "camera"


class NormalizedRect(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def rectangle_must_fit_canvas(self) -> NormalizedRect:
        if self.x + self.width > 1.000_001 or self.y + self.height > 1.000_001:
            raise ValueError("visual rectangle exceeds normalized canvas")
        return self

    def center(self, *, pixel_width: int, pixel_height: int) -> tuple[int, int]:
        return (
            round((self.x + self.width / 2.0) * pixel_width),
            round((self.y + self.height / 2.0) * pixel_height),
        )


class VisualElement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str = Field(pattern=r"^(text|rectangle|object)$")
    label: str = Field(default="", max_length=512)
    confidence: float = Field(ge=0.0, le=1.0)
    bounds: NormalizedRect

    @field_validator("label")
    @classmethod
    def label_must_be_safe(cls, value: str) -> str:
        if "\0" in value or len(value.encode("utf-8")) > 1_024:
            raise ValueError("visual label is invalid")
        return " ".join(value.split())


class ActiveVisionPayload(BaseModel):
    """Metadata-only bridge contract. Raw frames are intentionally impossible to submit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: VisionSource
    bundle_identifier: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]{2,254}$",
    )
    pixel_width: int = Field(ge=1, le=4_096)
    pixel_height: int = Field(ge=1, le=4_096)
    frame_sequence: int = Field(ge=0)
    captured_monotonic_ns: int = Field(ge=1)
    scene_summary: str = Field(default="", max_length=2_048)
    elements: tuple[VisualElement, ...] = Field(default=(), max_length=MAX_VISUAL_ELEMENTS)

    @field_validator("scene_summary")
    @classmethod
    def summary_must_be_safe(cls, value: str) -> str:
        if "\0" in value or len(value.encode("utf-8")) > 4_096:
            raise ValueError("visual summary is invalid")
        return " ".join(value.split())


@dataclass(frozen=True, slots=True)
class VisualState:
    source: VisionSource
    bundle_identifier: str | None
    pixel_width: int
    pixel_height: int
    frame_sequence: int
    state_sha256: str
    markdown: str
    elements: tuple[VisualElement, ...]

    def locate(self, label: str, *, minimum_confidence: float = 0.70) -> tuple[int, int] | None:
        needle = " ".join(label.casefold().split())
        if not needle:
            return None
        candidates = (
            element
            for element in self.elements
            if element.confidence >= minimum_confidence
            and needle in element.label.casefold()
        )
        match = max(candidates, key=lambda item: item.confidence, default=None)
        if match is None:
            return None
        return match.bounds.center(
            pixel_width=self.pixel_width,
            pixel_height=self.pixel_height,
        )


class VisionProcessor:
    """Validates bounded on-device observations and produces prompt-safe visual state."""

    def process(self, payload: ActiveVisionPayload | dict[str, object]) -> VisualState:
        observation = (
            payload
            if isinstance(payload, ActiveVisionPayload)
            else ActiveVisionPayload.model_validate(payload)
        )
        ordered = tuple(
            sorted(
                observation.elements,
                key=lambda item: (
                    round(item.bounds.y, 5),
                    round(item.bounds.x, 5),
                    item.kind,
                    item.label.casefold(),
                ),
            )
        )
        canonical = {
            "bundle_identifier": observation.bundle_identifier,
            "elements": [item.model_dump(mode="json") for item in ordered],
            "scene_summary": observation.scene_summary,
            "source": observation.source.value,
        }
        digest = hashlib.sha256(
            json.dumps(canonical, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()
        lines = [
            f"# Active Vision size={observation.pixel_width}x{observation.pixel_height} "
            f"source={observation.source.value}",
        ]
        if observation.bundle_identifier:
            lines.append(f"- Application: `{self._escape(observation.bundle_identifier)}`")
        if observation.scene_summary:
            lines.append(f"- Local scene: {self._escape(observation.scene_summary)}")
        for element in ordered:
            center = element.bounds.center(
                pixel_width=observation.pixel_width,
                pixel_height=observation.pixel_height,
            )
            label = self._escape(element.label or element.kind)
            lines.append(
                f"- {element.kind} `{label}` center=({center[0]},{center[1]}) "
                f"confidence={element.confidence:.3f}"
            )
        markdown = self._bound_markdown(lines)
        return VisualState(
            source=observation.source,
            bundle_identifier=observation.bundle_identifier,
            pixel_width=observation.pixel_width,
            pixel_height=observation.pixel_height,
            frame_sequence=observation.frame_sequence,
            state_sha256=digest,
            markdown=markdown,
            elements=ordered,
        )

    @staticmethod
    def changed(before: VisualState, after: VisualState) -> bool:
        if before.source != after.source or before.bundle_identifier != after.bundle_identifier:
            return False
        return before.state_sha256 != after.state_sha256

    @staticmethod
    def contains_any(state: VisualState, expected_terms: Sequence[str]) -> bool:
        haystack = " ".join(element.label for element in state.elements).casefold()
        return any(" ".join(term.casefold().split()) in haystack for term in expected_terms if term)

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace("`", "\\`")[:512]

    @staticmethod
    def _bound_markdown(lines: list[str]) -> str:
        encoded = "\n".join(lines).encode("utf-8")
        if len(encoded) <= MAX_VISUAL_TEXT_BYTES:
            return encoded.decode("utf-8")
        return encoded[:MAX_VISUAL_TEXT_BYTES].decode("utf-8", errors="ignore").rsplit(
            "\n", maxsplit=1
        )[0]


class ActiveVisionIpcService:
    OBSERVE_METHOD = "vision.active.observe"

    def __init__(self, processor: VisionProcessor | None = None) -> None:
        self._processor = processor or VisionProcessor()
        self._latest: VisualState | None = None

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {self.OBSERVE_METHOD: self.handle}

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        try:
            payload = ActiveVisionPayload.model_validate(request.payload)
            state = self._processor.process(payload)
        except ValueError:
            return IpcHandlerResult(ok=False, error_code="invalid_visual_observation")
        current = self._latest
        if current is not None and (
            current.source == state.source
            and current.bundle_identifier == state.bundle_identifier
            and state.frame_sequence <= current.frame_sequence
        ):
            return IpcHandlerResult(ok=False, error_code="stale_visual_observation")
        self._latest = state
        return IpcHandlerResult(
            ok=True,
            payload={
                "accepted": True,
                "state_sha256": state.state_sha256,
                "element_count": len(state.elements),
            },
        )

    @property
    def latest(self) -> VisualState | None:
        return self._latest
