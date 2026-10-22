from __future__ import annotations

import json
import re
from uuid import UUID

from aegis_core.contracts import PolicyDecision, ToolAuthorization, ToolExecutionResult
from aegis_core.tools.computer import ComputerUseReport
from aegis_core.tools.native_receipts import (
    NATIVE_RECEIPT_SOURCES,
    decode_native_json,
    native_receipt_is_valid,
    native_result_is_acknowledged,
    smart_tv_receipt_is_valid,
    smart_tv_result_is_acknowledged,
)


def result_matches_authorization(
    authorization: ToolAuthorization,
    result: ToolExecutionResult,
) -> bool:
    matched = (
        authorization.decision is PolicyDecision.ALLOW
        and result.call_id == authorization.call_id
        and result.tool_name == authorization.tool_name
    )
    if not matched:
        return False
    if result.success and (result.tool_name in NATIVE_RECEIPT_SOURCES
                           or result.tool_name == "smart_tv_control"):
        try:
            payload = decode_native_json(result.output)
        except (ValueError, RecursionError):
            return False
        if result.tool_name == "smart_tv_control":
            return smart_tv_receipt_is_valid(payload, authorization.normalized_arguments)
        return native_receipt_is_valid(
            result.tool_name, payload, authorization.normalized_arguments
        )
    return True


def result_is_verified(result: ToolExecutionResult) -> bool:
    """Verify evidence within its declared scope, not necessarily the user's goal."""
    if not result.success or result.error_code is not None:
        return False
    if "verified" in result.metadata and result.metadata["verified"] is not True:
        return False
    status = result.metadata.get("status")
    if result.tool_name in NATIVE_RECEIPT_SOURCES:
        return (status not in {"blocked", "partial", "failed", "step_limit"}
                and native_result_is_acknowledged(result))
    if result.tool_name == "smart_tv_control":
        return (status not in {"blocked", "partial", "failed", "step_limit"}
                and smart_tv_result_is_acknowledged(result))
    if result.tool_name in {"browser_task_open", "browser_task_action", "browser_task_close"}:
        if result.metadata.get("verified") is not True:
            return False
        try:
            payload = json.loads(result.output)
            data = payload["structuredContent"]
            if not isinstance(payload, dict) or not isinstance(data, dict):
                return False
            UUID(data["session_id"])
            if payload.get("isError") is True or data.get("verified") is not True:
                return False
            if result.tool_name == "browser_task_close":
                return (data.get("closed") is True
                        and data.get("goal_verified") is False
                        and result.metadata.get("goal_verified") is False
                        and data.get("verification_scope") == "browser_task_disconnect")
            if (not isinstance(data.get("expected_url"), str)
                    or not data["expected_url"].startswith("https://")
                    or not isinstance(data.get("snapshot_sha256"), str)
                    or re.fullmatch(r"[a-f0-9]{64}", data["snapshot_sha256"]) is None):
                return False
            if result.tool_name == "browser_task_open":
                return (data.get("goal_verified") is False
                        and result.metadata.get("goal_verified") is False
                        and data.get("verification_scope") == "browser_task_access")
            return (type(data.get("goal_verified")) is bool
                    and data["goal_verified"] is result.metadata.get("goal_verified")
                    and status not in {"blocked", "partial", "failed", "step_limit"})
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
            return False
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


def result_goal_is_verified(result: ToolExecutionResult) -> bool:
    """An acknowledged command/access grant must not become a verified external goal."""
    return (result_is_verified(result)
            and ("goal_verified" not in result.metadata
                 or result.metadata["goal_verified"] is True))


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
