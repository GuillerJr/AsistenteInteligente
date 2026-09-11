from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from aegis_core.contracts import ToolAuthorization, ToolExecutionResult
from aegis_core.tools.verification import result_is_verified, result_matches_authorization


class ReflectionStatus(StrEnum):
    VERIFIED = "verified"
    CORRECTABLE = "correctable"
    USER_INTERVENTION = "user_intervention"


@dataclass(frozen=True, slots=True)
class ReflectionDecision:
    status: ReflectionStatus
    reason: str
    visual_verified: bool
    correction_required: bool


VisualStateVerifier = Callable[[ToolAuthorization, ToolExecutionResult], Awaitable[bool]]


class VisualReflectionNode:
    """Fail-closed result reflector backed by post-action local visual verification."""

    _CORRECTABLE_REASONS = frozenset(
        {
            "error_modal",
            "layout_changed",
            "popup_detected",
            "state_verification_failed",
            "uncertain_state",
        }
    )

    def __init__(self, verifier: VisualStateVerifier | None = None) -> None:
        self._verifier = verifier

    async def evaluate(
        self,
        authorization: ToolAuthorization,
        result: ToolExecutionResult,
    ) -> ReflectionDecision:
        if not result_matches_authorization(authorization, result):
            return ReflectionDecision(
                status=ReflectionStatus.USER_INTERVENTION,
                reason="tool_result_mismatch",
                visual_verified=False,
                correction_required=False,
            )
        reason = self._reason(result)
        visual_verified = result_is_verified(result)
        if authorization.tool_name == "computer_use" and self._verifier is not None:
            visual_verified = visual_verified and await self._verifier(authorization, result)
        if visual_verified:
            return ReflectionDecision(
                status=ReflectionStatus.VERIFIED,
                reason="expected_state_observed",
                visual_verified=True,
                correction_required=False,
            )
        if reason in self._CORRECTABLE_REASONS:
            return ReflectionDecision(
                status=ReflectionStatus.CORRECTABLE,
                reason=reason,
                visual_verified=False,
                correction_required=True,
            )
        return ReflectionDecision(
            status=ReflectionStatus.USER_INTERVENTION,
            reason=reason,
            visual_verified=False,
            correction_required=False,
        )

    @staticmethod
    def _reason(result: ToolExecutionResult) -> str:
        reason = result.metadata.get("reason_code")
        if isinstance(reason, str) and reason:
            return reason
        if result.error_code:
            return result.error_code
        return "unverified_state"
