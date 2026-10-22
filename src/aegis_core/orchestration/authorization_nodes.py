from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aegis_core.contracts import (
    MAX_TOOL_CALLS_PER_RESULT,
    PolicyDecision,
    ToolAuthorization,
    ToolCall,
    is_unsigned_peer_request,
)
from aegis_core.engineering import (
    ENGINEERING_WORKSPACE_METADATA,
    UNSIGNED_ENGINEERING_TOOLS,
    is_engineering_request,
)
from aegis_core.mcp.browser_tasks import canonical_browser_url, explicit_browser_urls
from aegis_core.orchestration.private_context import (
    _request_can_access_private_context,
)
from aegis_core.orchestration.state import (
    SwarmState,
)
from aegis_core.orchestration.task_progress import (
    TaskResolution,
    reject_duplicate_calls,
)
from aegis_core.request_intent import is_explicit_native_ui_request
from aegis_core.tools.computer_workflow import (
    ComputerWorkflowSpec,
    computer_destination_matches_request,
    create_workflow,
    validate_workflow_request,
    workflow_step_call,
)


def create_authorize_tools_node(
    *,
    audit,
    broker,
    browser_reference,
    context,
    effective_tool_names,
    plan_mode,
    tool_call_preparer,
):
    async def authorize_tools_node(state: SwarmState) -> dict[str, Any]:
        specialist = state["specialist_result"]
        if len(specialist.tool_calls) > MAX_TOOL_CALLS_PER_RESULT:
            raise ValueError("specialist returned too many tool calls")
        reject_duplicate_calls(specialist.tool_calls)
        prepared_update: dict[str, Any] = {}
        if tool_call_preparer is not None and specialist.tool_calls:
            calls = tuple([await asyncio.to_thread(tool_call_preparer, call)
                           for call in specialist.tool_calls])
            prepared = {call.call_id: call for call in calls}
            specialist = specialist.model_copy(update={"tool_calls": calls})
            prepared_update = {
                "specialist_result": specialist,
                "specialist_results": tuple(result.model_copy(update={
                    "tool_calls": tuple(prepared.get(call.call_id, call)
                                        for call in result.tool_calls),
                }) for result in state.get("specialist_results", (specialist,))),
            }
        browser_calls = tuple(
            call for call in specialist.tool_calls if call.tool_name.startswith("browser_task_")
        )
        if browser_calls and len(specialist.tool_calls) != 1:
            raise ValueError("browser_task_requires_one_operation")
        browser_update: dict[str, Any] = {}
        if browser_calls and browser_calls[0].tool_name == "browser_task_open":
            # Page text and the model's paraphrase cannot replace the trusted
            # instruction displayed in the exact destination approval.
            original = browser_calls[0]
            proposed_url = original.arguments.get("url")
            if isinstance(proposed_url, str):
                try:
                    proposed_url = canonical_browser_url(proposed_url)
                except ValueError:
                    pass  # The argument model denies invalid destinations below.
            normalized_call = original.model_copy(
                update={
                    "arguments": {
                        **original.arguments,
                        "objective": state["request"].text,
                        "url": proposed_url,
                    }
                }
            )
            specialist = specialist.model_copy(update={"tool_calls": (normalized_call,)})
            browser_update = {"specialist_result": specialist, "specialist_results": (specialist,)}
        computer_update: dict[str, Any] = {}
        if len(specialist.tool_calls) == 1:
            original = specialist.tool_calls[0]
            if (
                original.tool_name == "computer_use"
                and not original.arguments.get("workflow_steps")
                and not state.get("computer_workflow")
                and is_explicit_native_ui_request(state["request"].text)
            ):
                # The owner approves their complete instruction, including negative
                # constraints. A planner paraphrase cannot drop those restrictions
                # or supply new literals for the executor's postcondition checks.
                normalized_call = original.model_copy(update={"arguments": {
                    **original.arguments, "objective": state["request"].text,
                }})
                specialist = specialist.model_copy(update={"tool_calls": (normalized_call,)})
                computer_update = {
                    "specialist_result": specialist, "specialist_results": (specialist,),
                }
        direct_call = state.get("direct_tool_call")
        workflow_update: dict[str, Any] = {}
        if specialist.tool_calls:
            proposed = specialist.tool_calls[0]
            steps = proposed.arguments.get("workflow_steps")
            if proposed.tool_name == "computer_use" and steps:
                conversation_id = state.get("conversation_id")
                if conversation_id is None or not _request_can_access_private_context(
                    state["request"]
                ):
                    raise ValueError("computer_workflow_requires_private_conversation")
                spec = ComputerWorkflowSpec(
                    goal_sha256=hashlib.sha256(state["request"].text.encode()).hexdigest(),
                    steps=steps,
                )
                if (
                    len(spec.steps) == 1
                    and is_explicit_native_ui_request(state["request"].text)
                ):
                    # A one-step wrapper must not evade the complete single-app
                    # objective above by supplying a shortened workflow objective.
                    raise ValueError("computer_workflow_requires_multiple_steps")
                if not validate_workflow_request(spec, state["request"].text):
                    raise ValueError("computer_workflow_does_not_match_request")
                workflow = create_workflow(
                    conversation_id=conversation_id,
                    request_id=state["request"].request_id,
                    spec=spec,
                )
                direct_call = workflow_step_call(workflow, state["request"].request_id)
                specialist = specialist.model_copy(update={"tool_calls": (direct_call,)})
                workflow_update = {
                    "computer_workflow": workflow, "direct_tool_call": direct_call,
                    "specialist_result": specialist, "specialist_results": (specialist,),
                }
        active_skill = state.get("skill")
        effective_names = effective_tool_names(
            state["request"],
            active_skill,
            capability_gap=(
                state.get("capability_gap", False)
                and state.get("capability_knowledge") is None
            ),
            scope_request=state.get("tool_scope_request"),
            skills=state.get("skills", (active_skill,) if active_skill else ()),
        )
        resolution = state.get("task_resolution", TaskResolution())
        if resolution.tool_suffix:
            effective_names = frozenset(name for name in effective_names
                                        if name.endswith("__" + resolution.tool_suffix))
        allowed_names = (
            frozenset({direct_call.tool_name})
            if direct_call is not None
            else (effective_names if active_skill is not None else (effective_names or None))
        )
        if plan_mode(state["request"]):
            allowed_names = frozenset()
        if is_unsigned_peer_request(state["request"]):
            # Never widen to "any tool" for a peer that is not a signed Jarvis client.
            allowed_names = (
                effective_names if allowed_names is None else allowed_names
            ) & UNSIGNED_ENGINEERING_TOOLS

        def authorize(call: ToolCall) -> ToolAuthorization:
            binding_error = None
            if (
                call.tool_name == "computer_use"
                and not workflow_update
                and not state.get("computer_workflow")
                and not computer_destination_matches_request(
                    state["request"].text,
                    str(call.arguments.get("application_bundle_identifier", "")),
                )
            ):
                binding_error = "computer_destination_not_requested"
            if call.tool_name == "browser_task_open":
                literals = explicit_browser_urls(state["request"].text)
                if literals and call.arguments.get("url") not in literals:
                    binding_error = "browser_destination_not_requested"
                active_browser = browser_reference(state["request"])
                destination = call.arguments.get("url")
                current_url = active_browser.get("expected_url") if active_browser else None
                if isinstance(destination, str) and isinstance(current_url, str):
                    target, current = urlparse(destination), urlparse(current_url)
                    if (target.scheme, target.netloc) == (current.scheme, current.netloc):
                        binding_error = "browser_task_access_already_active"
            if call.tool_name == "browser_task_action":
                fields = ("verify_text", "wait_text", "wait_text_gone")
                if call.arguments.get("operation") == "fill":
                    fields += ("text",)
                if any(
                    isinstance(call.arguments.get(field), str)
                    and call.arguments[field]
                    and call.arguments[field] not in state["request"].text
                    for field in fields
                ):
                    binding_error = "browser_postcondition_not_requested"
                verify_url = call.arguments.get("verify_url")
                if isinstance(verify_url, str) and verify_url:
                    try:
                        verified_url = canonical_browser_url(verify_url)
                    except ValueError:
                        verified_url = verify_url
                    if verified_url not in explicit_browser_urls(state["request"].text):
                        binding_error = "browser_postcondition_not_requested"
            if binding_error is not None:
                return ToolAuthorization(
                    call_id=call.call_id,
                    tool_name=call.tool_name,
                    call_digest=call.digest(),
                    decision=PolicyDecision.DENY,
                    reason_code=binding_error,
                )
            return broker.authorize(
                call,
                replace(
                    context,
                    request_id=state["request"].request_id,
                    request_text=state["request"].text,
                ),
                allowed_names=allowed_names,
                request=state["request"],
            )

        authorizations = tuple(authorize(call) for call in specialist.tool_calls)
        authorizations = tuple(
            authorization.model_copy(update={
                "decision": PolicyDecision.DENY,
                "reason_code": "browser_postcondition_not_requested",
            }) if "__browser_" in authorization.tool_name and any(
                isinstance(authorization.normalized_arguments.get(field), str)
                and authorization.normalized_arguments[field]
                and authorization.normalized_arguments[field] not in state["request"].text
                for field in (("verify_text", "verify_url") + (
                    ("text", "textGone")
                    if authorization.tool_name.endswith("__browser_wait_for") else ()
                ))
            ) else authorization
            for authorization in authorizations
        )
        if is_engineering_request(state["request"]):
            workspace = state["request"].metadata.get(ENGINEERING_WORKSPACE_METADATA, ".")
            authorizations = tuple(
                authorization.model_copy(update={
                    "decision": PolicyDecision.DENY,
                    "reason_code": "engineering_workspace_mismatch",
                })
                if authorization.tool_name == "filesystem_read_text" and (
                    not isinstance(workspace, str)
                    or not Path(str(authorization.normalized_arguments.get("path", "")))
                    .is_relative_to(Path(workspace))
                ) else authorization
                for authorization in authorizations
            )
        for authorization in authorizations:
            audit.record_authorization(state["request"].request_id, authorization)
        return {"tool_authorizations": authorizations, **prepared_update,
                **browser_update, **computer_update, **workflow_update}

    return authorize_tools_node
