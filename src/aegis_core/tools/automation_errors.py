"""Bounded errors from our fixed JXA programs; never return native error text."""

from __future__ import annotations

ERROR_KEY = "_aegis_automation_error"
ERROR_CODES = frozenset({
    "access_denied",
    "execution_timeout",
    "automation_target_unavailable",
    "automation_target_ambiguous",
    "automation_output_limit",
    "automation_invalid_response",
    "automation_failed",
})


class AutomationExecutionError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code if code in ERROR_CODES else "automation_failed"
        super().__init__(self.code)


def bounded_jxa_program(source: str) -> str:
    # The try statement preserves the script's JSON completion value on success.
    # Only numeric Apple Event codes and our own exact literals cross the boundary.
    return "try {\n" + source + r"""
} catch (error) {
    let code = "automation_failed";
    const number = error && (typeof error.number === "number" ? error.number : error.errorNumber);
    const message = error && error.message;
    if (number === -1743) code = "access_denied";
    else if (number === -1712) code = "execution_timeout";
    else if (number === -1728 || message === "calendar_unavailable"
            || message === "reminder_list_unavailable") {
        code = "automation_target_unavailable";
    } else if (message === "reminder_match_not_unique"
            || message === "media_application_not_unique") {
        code = "automation_target_ambiguous";
    } else if (message === "calendar candidate limit exceeded"
            || message === "reminder candidate limit exceeded"
            || message === "contact candidate limit exceeded") {
        code = "automation_output_limit";
    }
    JSON.stringify({_aegis_automation_error: code});
}
"""


def raise_for_automation_error(output: object) -> None:
    if isinstance(output, dict) and ERROR_KEY in output:
        code = output[ERROR_KEY]
        if set(output) != {ERROR_KEY} or not isinstance(code, str) or code not in ERROR_CODES:
            raise AutomationExecutionError("automation_invalid_response")
        raise AutomationExecutionError(code)
