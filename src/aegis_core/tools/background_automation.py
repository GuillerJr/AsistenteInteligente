from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aegis_core.async_tasks import run_blocking_owned


class QuietActionVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pathway: str = Field(pattern=r"^(accessibility|process_event)$")
    before_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    after_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    state_changed: bool = Field(strict=True)
    verified: bool = Field(strict=True)

    @model_validator(mode="after")
    def hashes_must_agree_with_state_change(self) -> QuietActionVerification:
        if self.state_changed != (self.before_sha256 != self.after_sha256):
            raise ValueError("quiet action evidence is inconsistent")
        return self


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
        verification = await run_blocking_owned(
            bridge.act,
            action,
            expected_bundle_identifier,
            expected_visual_context,
            expected_user_input_counter,
        )
        if verification is not None and not verification.verified:
            raise RuntimeError("quiet action state verification failed")
        return verification
