from __future__ import annotations

import asyncio
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class QuietActionVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pathway: str = Field(pattern=r"^(accessibility|process_event)$")
    before_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    after_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    state_changed: bool
    verified: bool


class QuietAutomationBridge(Protocol):
    def act(
        self,
        action: object,
        expected_bundle_identifier: str,
        expected_visual_context: str,
        expected_user_input_counter: int,
    ) -> QuietActionVerification | None: ...


class QuietBackgroundAutomation:
    """Runs the signed helper off-loop; native AX state verification stays in-process."""

    async def dispatch(
        self,
        bridge: QuietAutomationBridge,
        action: object,
        expected_bundle_identifier: str,
        expected_visual_context: str,
        expected_user_input_counter: int,
    ) -> QuietActionVerification | None:
        verification = await asyncio.to_thread(
            bridge.act,
            action,
            expected_bundle_identifier,
            expected_visual_context,
            expected_user_input_counter,
        )
        if verification is not None and not verification.verified:
            raise RuntimeError("quiet action state verification failed")
        return verification
