from __future__ import annotations

import asyncio
from collections import defaultdict
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aegis_core.brain.vision_processor import VisualState


class SilentActionKind(StrEnum):
    PRESS = "press"
    TYPE = "type"
    SCROLL = "scroll"


class SilentAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: SilentActionKind
    process_identifier: int = Field(gt=1, le=2_147_483_647)
    bundle_identifier: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]{2,254}$")
    accessibility_identifier: str | None = Field(default=None, min_length=1, max_length=256)
    accessibility_label: str | None = Field(default=None, min_length=1, max_length=256)
    text: str | None = Field(default=None, min_length=1, max_length=2_048)
    delta_x: int | None = Field(default=None, ge=-64, le=64)
    delta_y: int | None = Field(default=None, ge=-64, le=64)
    fallback_x: float | None = Field(default=None, ge=0.0, le=100_000.0)
    fallback_y: float | None = Field(default=None, ge=0.0, le=100_000.0)
    expected_state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def action_fields_are_coherent(self) -> SilentAction:
        has_selector = bool(self.accessibility_identifier or self.accessibility_label)
        if not has_selector:
            raise ValueError("silent action requires an Accessibility selector")
        if self.kind is SilentActionKind.TYPE and self.text is None:
            raise ValueError("type action requires text")
        if self.kind is not SilentActionKind.TYPE and self.text is not None:
            raise ValueError("text is only valid for type action")
        if self.kind is SilentActionKind.SCROLL and not (self.delta_x or self.delta_y):
            raise ValueError("scroll action requires a non-zero delta")
        if self.kind is not SilentActionKind.SCROLL and (
            self.delta_x is not None or self.delta_y is not None
        ):
            raise ValueError("scroll deltas are invalid for this action")
        if (self.fallback_x is None) != (self.fallback_y is None):
            raise ValueError("fallback coordinates must be paired")
        return self


class SilentExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    success: bool
    pathway: str = Field(pattern=r"^(accessibility|process_event|none)$")
    before_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    after_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    state_changed: bool
    error_code: str | None = Field(default=None, max_length=96)


class SilentNativeBridge(Protocol):
    async def observe(self, process_identifier: int, bundle_identifier: str) -> VisualState: ...

    async def accessibility_action(self, action: SilentAction) -> bool: ...

    async def process_event(self, action: SilentAction) -> bool: ...


class SilentExecutor:
    """AX-first, per-PID executor. It never activates apps or posts to the HID stream."""

    def __init__(self, bridge: SilentNativeBridge, *, verification_delay_seconds: float = 0.05):
        if not 0.01 <= verification_delay_seconds <= 0.25:
            raise ValueError("silent verification delay is out of range")
        self._bridge = bridge
        self._verification_delay = verification_delay_seconds
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def execute(self, action: SilentAction) -> SilentExecutionResult:
        async with self._locks[action.process_identifier]:
            before = await self._bridge.observe(
                action.process_identifier,
                action.bundle_identifier,
            )
            if before.state_sha256 != action.expected_state_sha256:
                return self._result(False, "none", before, before, "stale_visual_context")
            pathway = "accessibility"
            accepted = await self._bridge.accessibility_action(action)
            if not accepted:
                if action.fallback_x is None or action.fallback_y is None:
                    return self._result(
                        False,
                        "none",
                        before,
                        before,
                        "accessibility_action_unsupported",
                    )
                pathway = "process_event"
                accepted = await self._bridge.process_event(action)
            if not accepted:
                return self._result(False, pathway, before, before, "event_dispatch_rejected")
            await asyncio.sleep(self._verification_delay)
            after = await self._bridge.observe(
                action.process_identifier,
                action.bundle_identifier,
            )
            changed = before.state_sha256 != after.state_sha256
            return self._result(
                changed,
                pathway,
                before,
                after,
                None if changed else "state_verification_failed",
            )

    @staticmethod
    def _result(
        success: bool,
        pathway: str,
        before: VisualState,
        after: VisualState,
        error_code: str | None,
    ) -> SilentExecutionResult:
        return SilentExecutionResult(
            success=success,
            pathway=pathway,
            before_sha256=before.state_sha256,
            after_sha256=after.state_sha256,
            state_changed=before.state_sha256 != after.state_sha256,
            error_code=error_code,
        )
