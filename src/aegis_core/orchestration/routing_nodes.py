from __future__ import annotations

from typing import Any

from aegis_core.capability_learning import (
    is_capability_gap_request,
)
from aegis_core.contracts import (
    AgentResult,
    AgentRole,
    RiskLevel,
    RouteDecision,
)
from aegis_core.orchestration.clarification import (
    expand_option_answer,
)
from aegis_core.orchestration.direct_actions import direct_local_response, direct_tool_call
from aegis_core.orchestration.private_context import (
    _request_can_access_private_context,
)
from aegis_core.orchestration.routing import (
    _route_request,
    _tool_names_for_request,
)
from aegis_core.orchestration.state import (
    SwarmState,
)
from aegis_core.orchestration.task_progress import (
    CONTINUATIONS,
    TaskResolution,
    resolve_quoted_reference,
    resolve_task_reference,
)
from aegis_core.request_intent import (
    folded_request,
    google_followup_text,
    google_read_entrypoint,
    google_request_tools,
)
from aegis_core.skills import (
    SkillOrigin,
    selection_instruction_text,
)
from aegis_core.tools.computer_workflow import (
    next_pending_step,
    recover_interrupted_workflow,
    workflow_step_call,
)


def create_route_node(
    *,
    browser_reference,
    dialogue,
    discovered_tool_names,
    dynamic_tool_names,
    plan_mode,
    request_tool_names,
    schedule_call_for,
    semantic_reference_for,
    semantic_skills,
    semantic_tool_selection,
    semantic_tools_for,
    skill_registry,
):
    async def route_node(state: SwarmState) -> dict[str, Any]:
        # "4" right after Jarvis offered numbered options means that option, not the
        # fourth item of some earlier list: restate it before anything interprets it.
        request = state["request"]
        if _request_can_access_private_context(request):
            chosen = expand_option_answer(request.text, state.get("conversation_history", ()))
            if chosen != request.text:
                request = request.model_copy(update={"text": chosen})
                update = {**await _route(dict(state, request=request)), "request": request}
                if plan_mode(request):
                    update.pop("direct_tool_call", None)
                return update
        update = await _route(state)
        if plan_mode(request):
            # A plan never runs a local action or a recognized command directly.
            update.pop("direct_tool_call", None)
        return update

    async def _route(state: SwarmState) -> dict[str, Any]:
        request = state["request"]
        browser_names = discovered_tool_names(request) & {
            "browser_task_open",
            "browser_task_action",
            "browser_task_close",
        }
        active_browser = browser_reference(request)
        workflow = state.get("computer_workflow")
        if workflow is not None and (
            not _request_can_access_private_context(request)
            or workflow.conversation_id != state.get("conversation_id")
        ):
            workflow = None
        if workflow is not None and _request_can_access_private_context(request):
            current = folded_request(request.text)
            cancellation = current in {"cancela", "cancelar", "cancel", "detente", "stop"}
            if cancellation:
                workflow = recover_interrupted_workflow(workflow, cancelled=True)
            if current in CONTINUATIONS or cancellation:
                workflow = recover_interrupted_workflow(workflow)
                next_step = next_pending_step(workflow)
                update = {
                    "computer_workflow": workflow,
                    "route": RouteDecision(
                        role=AgentRole.PLANNER,
                        risk=RiskLevel.CRITICAL,
                        reason="Workflow progress proposes a separately reviewed step",
                        requires_confirmation=True,
                    ),
                    "dialogue": dialogue.classify(request.text),
                    "task_resolution": TaskResolution(),
                }
                if next_step is not None:
                    update["direct_tool_call"] = workflow_step_call(workflow, request.request_id)
                else:
                    complete = all(item.status == "verified" for item in workflow.progress)
                    update["direct_local_result"] = AgentResult(
                        role=AgentRole.SYNTHESIZER,
                        model_id="local/workflow-status",
                        content=(
                            "Los pasos del flujo ya están verificados."
                            if complete
                            else "El flujo está detenido. No he repetido ninguna acción. "
                            "Comprueba su estado antes de iniciar otro flujo."
                        ),
                    )
                return update
            # A new topic cannot inherit pending actions from the old workflow.
            workflow = None
        checkpoint = state.get("task_checkpoint")
        task_cancelled = checkpoint is not None and checkpoint.status == "cancelled"
        resolution = (
            resolve_task_reference(request.text, state.get("task_checkpoint"))
            if _request_can_access_private_context(request)
            else TaskResolution()
        )
        quoted = request.metadata.get("telegram_message_reference")
        if request.metadata.get("source_channel") == "telegram" and isinstance(quoted, dict):
            resolution = resolve_quoted_reference(request.text, quoted)
        elif (
            resolution == TaskResolution()
            and (not task_cancelled)
            and _request_can_access_private_context(request)
        ):
            resolution = await semantic_reference_for(request, checkpoint)
        followup = (
            google_followup_text(request.text, state.get("conversation_history", ()))
            if _request_can_access_private_context(request) and not task_cancelled
            else None
        )
        followup = resolution.scope_text or followup
        scope_request = request.model_copy(update={"text": followup}) if followup else request
        skill_history = (
            state.get("conversation_history", ())
            if _request_can_access_private_context(request) and not task_cancelled
            else ()
        )
        public_skills_only = not _request_can_access_private_context(request)
        skill_selection_text = (
            skill_registry.selection_text(request.text, skill_history)
            if skill_registry
            else request.text
        )
        skills = (
            skill_registry.select_for_request(
                request.text, skill_history, public_only=public_skills_only
            )
            if skill_registry
            else ()
        )
        if (
            skills
            and followup is None
            and skill_history
            and skill_selection_text != selection_instruction_text(request.text)
        ):
            continued_read = request.model_copy(update={"text": skill_selection_text})
            read_names = _tool_names_for_request(continued_read)
            if "filesystem_read_text" in read_names and read_names <= {
                "filesystem_read_text",
                "system_describe_runtime",
                "system_power_status",
                "system_storage_status",
                "system_observe_status",
            }:
                # A continued document task needs its read schemas, not just its
                # procedure. History selects scope only; it never supplies tool
                # arguments or resurrects writes/network actions.
                followup, scope_request = skill_selection_text, continued_read
        skill = skills[0] if skills else None
        route = _route_request(scope_request, skill)
        if skill is not None and skill.manifest.role is not route.role:
            skills, skill = (), None
        update: dict[str, Any] = {
            "route": route,
            "dialogue": dialogue.classify(request.text),
            "task_resolution": resolution,
            "computer_workflow": workflow,
        }
        if followup is not None:
            update["tool_scope_request"] = request.model_copy(update={"text": followup})
        if skill is not None:
            update["skill"] = skill
            update["skills"] = skills
        recovery = (
            request.metadata.get("task_recovery")
            if _request_can_access_private_context(request) and not active_browser
            else None
        )
        related_recovery = isinstance(recovery, dict) and (
            folded_request(request.text) in CONTINUATIONS
            or folded_request(request.text) == folded_request(recovery.get("request_text", ""))
        )
        if related_recovery and recovery.get("status") in {"executing", "observed"}:
            observation = recovery.get("observation")
            content = (
                "La operación anterior devolvió un resultado que conservé antes de la "
                "interrupción. No la he repetido. Resultado parcial guardado: "
                + observation.get("excerpt", "")
                if isinstance(observation, dict)
                else "La operación anterior se interrumpió y no tengo un resultado verificado. "
                "Podría haberse ejecutado; comprueba su estado antes de pedir que se repita."
            )
            update["direct_local_result"] = AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id="local/recovery-status",
                content=content,
            )
            return update
        if related_recovery and recovery.get("status") == "awaiting_confirmation":
            resumed = request.model_copy(update={"text": recovery["request_text"]})
            update["tool_scope_request"] = resumed
            update["route"] = _route_request(resumed, skill)
        local_result = direct_local_response(request)
        if resolution.clarification is not None:
            update["direct_local_result"] = AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id="local/task-clarification",
                content=resolution.clarification,
            )
            return update
        scope_request = update.get("tool_scope_request", request)
        entrypoint = resolution.tool_suffix or google_read_entrypoint(scope_request.text)
        expected_google = (
            frozenset({entrypoint}) if entrypoint else google_request_tools(scope_request.text)
        )
        if (
            dynamic_tool_names is not None
            and (not browser_names)
            and expected_google
            and (
                not any(
                    name.endswith("__" + remote)
                    for name in discovered_tool_names(scope_request)
                    for remote in expected_google
                )
            )
        ):
            # A disconnected Google peer cannot silently redirect the user's data
            # request to Apple Mail/Calendar or fabricate a successful consultation.
            local_result = AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id="local/service-unavailable",
                content="Google Workspace no está disponible para esta consulta. "
                "Revisa su conexión en Configuración y vuelve a intentarlo.",
            )
        if local_result is not None:
            update["direct_local_result"] = local_result
        else:
            call = direct_tool_call(request)
            if (
                browser_names
                and call is not None
                and call.tool_name
                in {
                    "browser_open_url",
                    "browser_search",
                    "computer_use",
                }
            ):
                call = None
            if call is not None and call.tool_name in {"mail_list_recent", "calendar_list_events"}:
                domain_names = discovered_tool_names(request)
                if any(
                    name.endswith(("__search_gmail_messages", "__get_events"))
                    for name in domain_names
                ):
                    call = None
            if call is not None:
                update["direct_tool_call"] = call
            elif is_capability_gap_request(
                request,
                known_tool_names=request_tool_names(request),
                has_skill=skill is not None,
            ):
                update["capability_gap"] = True
        if (
            "direct_local_result" not in update
            and "direct_tool_call" not in update
            and (not browser_names)
            and (resolution.tool_suffix is None)
            and _request_can_access_private_context(request)
        ):
            # Let the router interpret every private request, not only those no keyword
            # rule recognized: "cada mañana resúmeme mis correos" matches the Gmail
            # rules but needs the scheduling tool. Its choice adds to the rules' tools.
            chosen = await semantic_tools_for(
                request, state.get("conversation_history", ()) if not task_cancelled else ()
            )
            if skill is not None and skill.manifest.origin is SkillOrigin.LEARNED:
                # The owner is using a skill they taught ("prepárame mi informe del
                # día"); live, the router read it as creating a skill or a goal.
                chosen = frozenset(
                    name
                    for name in chosen
                    if not (name.startswith("jarvis_") and name.endswith("_create"))
                )
            if chosen:
                semantic_tool_selection[request.request_id] = chosen
                semantic_tool_selection.move_to_end(request.request_id)
                while len(semantic_tool_selection) > 256:
                    semantic_tool_selection.popitem(last=False)
                update.pop("capability_gap", None)
                if (
                    "jarvis_schedule_create" in chosen
                    and request.metadata.get("source_channel") not in {"schedule", "goal"}
                    and (schedule_call := await schedule_call_for(request)) is not None
                ):
                    # A focused interpreter reads the schedule; the owner still approves.
                    update["direct_tool_call"] = schedule_call
                if update["route"].role is not AgentRole.PLANNER and not skill:
                    update["route"] = RouteDecision(
                        role=AgentRole.PLANNER,
                        risk=RiskLevel.MEDIUM,
                        reason="router-selected connected service",
                    )
        if (
            skill_registry is not None
            and "direct_local_result" not in update
            and "direct_tool_call" not in update
            and skill_registry.selection_needs_semantic(
                skill_selection_text, public_only=public_skills_only
            )
        ):
            selection_request = request.model_copy(update={"text": skill_selection_text})
            skills = await semantic_skills(selection_request)
            semantic_route = _route_request(scope_request, skills[0] if skills else None)
            if skills and skills[0].manifest.role is not semantic_route.role:
                skills = ()
            if skills:
                update.update(
                    skill=skills[0], skills=skills, route=_route_request(scope_request, skills[0])
                )
                update.pop("capability_gap", None)
        if browser_names:
            # Browser state is selected by the service for this exact request, not
            # by conversation references or a procedure's unrelated starter tools.
            update["tool_scope_request"] = request
            update["task_resolution"] = TaskResolution()
            update.pop("capability_gap", None)
            if update["route"].role not in {AgentRole.PLANNER, AgentRole.CODE_SECURITY}:
                update["route"] = RouteDecision(
                    role=AgentRole.PLANNER,
                    risk=RiskLevel.MEDIUM,
                    reason="request-scoped browser task",
                )
                update.pop("skill", None)
                update["skills"] = ()
        return update

    return route_node
