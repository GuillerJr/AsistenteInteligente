from __future__ import annotations

from aegis_core.contracts import PolicyDecision, ToolAuthorization, ToolExecutionResult


def result_matches_authorization(
    authorization: ToolAuthorization,
    result: ToolExecutionResult,
) -> bool:
    return (
        authorization.decision is PolicyDecision.ALLOW
        and result.call_id == authorization.call_id
        and result.tool_name == authorization.tool_name
    )


def result_is_verified(result: ToolExecutionResult) -> bool:
    """Require real boolean evidence; UI automation also needs terminal completion."""
    if not result.success or result.error_code is not None:
        return False
    if "verified" in result.metadata and result.metadata["verified"] is not True:
        return False
    status = result.metadata.get("status")
    if result.tool_name == "computer_use":
        return result.metadata.get("verified") is True and status == "completed"
    return status not in {"blocked", "failed", "step_limit"}


def result_is_recoverable_ui_block(result: ToolExecutionResult) -> bool:
    return (
        result.tool_name == "computer_use"
        and result.error_code is None
        and result.metadata.get("verified") is False
        and result.metadata.get("status") == "blocked"
        and result.metadata.get("reason_code") == "uncertain_state"
        and type(result.metadata.get("steps")) is int
        and result.metadata["steps"] == 0
    )
