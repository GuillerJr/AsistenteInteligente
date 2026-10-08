from __future__ import annotations

from aegis_core.contracts import PolicyDecision, ToolAuthorization, ToolExecutionResult
from aegis_core.tools.computer import ComputerUseReport


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
        if result.metadata.get("verified") is not True or status != "completed":
            return False
        try:
            report = ComputerUseReport.model_validate_json(result.output)
        except (ValueError, RecursionError):
            return False
        return (
            report.status == "completed"
            and report.reason_code == "objective_complete"
            and report.failed_check is None
            and bool(report.postcondition_checks)
            and all(check.satisfied for check in report.postcondition_checks)
        )
    return status not in {"blocked", "partial", "failed", "step_limit"}


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
