from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from aegis_core.activity import SwarmActivityTracker
from aegis_core.brain.agent_graph import DeviceLifecycleAction, PlanExecuteReflectRunner
from aegis_core.brain.nodes.reflector import VisualReflectionNode
from aegis_core.capability_learning import (
    CapabilityLearningCoordinator,
)
from aegis_core.contracts import (
    PLAN_MODE_METADATA,
    AgentResult,
    AgentRole,
    PolicyDecision,
    ToolAuthorization,
    ToolCall,
    ToolExecutionResult,
    UserRequest,
    is_unsigned_peer_request,
)
from aegis_core.dialogue import (
    DialogueKernel,
)
from aegis_core.engineering import (
    ENGINEERING_RESEARCH_METADATA,
    UNSIGNED_ENGINEERING_TOOLS,
    EngineeringResearchPolicy,
    is_engineering_request,
)
from aegis_core.failure_boundary import record_boundary_error
from aegis_core.langgraph_compat import END, START, StateGraph
from aegis_core.memory.profile import OwnerProfile
from aegis_core.memory.retrieval import MemoryRetriever
from aegis_core.memory.social import SocialMemory
from aegis_core.memory.task_checkpoint import TaskCheckpoint
from aegis_core.orchestration.authorization_nodes import create_authorize_tools_node
from aegis_core.orchestration.clarification import (
    may_ask_user,
    structured_clarification,
)
from aegis_core.orchestration.direct_actions import direct_tool_call
from aegis_core.orchestration.execution_nodes import create_execute_read_tools_node
from aegis_core.orchestration.inference import create_completion
from aegis_core.orchestration.memory_nodes import create_recall_memory_node
from aegis_core.orchestration.notes import create_notes
from aegis_core.orchestration.planning_nodes import create_specialist_node
from aegis_core.orchestration.private_context import (
    _request_can_access_private_context,
)
from aegis_core.orchestration.routing import (
    _respect_engineering_offline,
    _tool_names_for_request,
)
from aegis_core.orchestration.routing_nodes import create_route_node
from aegis_core.orchestration.semantic_reference import semantic_reference
from aegis_core.orchestration.semantic_tools import select_tools
from aegis_core.orchestration.skill_selection import create_skill_selection
from aegis_core.orchestration.state import (
    SwarmState,
)
from aegis_core.orchestration.synthesis_nodes import create_synthesize_node
from aegis_core.orchestration.task_progress import (
    TaskResolution,
)
from aegis_core.privacy import (
    redact_for_remote,
    sanitize_remote_reference,
)
from aegis_core.providers.base import (
    ChatProvider,
)
from aegis_core.request_intent import is_chat_only_request, is_explicit_native_ui_request
from aegis_core.scheduling import interpret_schedule_request
from aegis_core.skills import (
    SkillActivation,
    SkillRegistry,
)
from aegis_core.tools.audit import AuditSink, NullAuditSink
from aegis_core.tools.broker import PolicyContext, ToolBroker
from aegis_core.tools.computer import ComputerUseController, ComputerUseReport
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context
from aegis_core.tools.execution import ReadOnlyToolExecutor
from aegis_core.tools.verification import result_is_verified, result_matches_authorization


def build_swarm_graph(
    provider: ChatProvider,
    *,
    local_provider: ChatProvider | None = None,
    tool_broker: ToolBroker | None = None,
    policy_context: PolicyContext | None = None,
    tool_executor: ReadOnlyToolExecutor | None = None,
    audit_sink: AuditSink | None = None,
    memory_retriever: MemoryRetriever | None = None,
    owner_profile: OwnerProfile | None = None,
    social_memory: SocialMemory | None = None,
    dialogue_kernel: DialogueKernel | None = None,
    memory_namespace: str = "user.default",
    memory_limit: int = 5,
    memory_max_context_bytes: int = 4_096,
    owner_profile_limit: int = 10,
    personality_instruction: Callable[[], str] | None = None,
    conversation_max_context_bytes: int = 4_096,
    social_context_max_bytes: int = 1_536,
    activity_tracker: SwarmActivityTracker | None = None,
    skill_registry: SkillRegistry | None = None,
    capability_learning: CapabilityLearningCoordinator | None = None,
    device_waker: DeviceLifecycleAction | None = None,
    device_verifier: DeviceLifecycleAction | None = None,
    focus_priority_provider: Callable[[], dict[str, str | bool]] | None = None,
    dynamic_tool_names: Callable[[UserRequest], frozenset[str]] | None = None,
    semantic_tool_catalogue: Callable[[], Sequence[tuple[str, str]]] | None = None,
    inference_nvidia_only: bool | None = None,
    runtime_context_provider: Callable[[], Any] | None = None,
    browser_task_context: Callable[[UUID], dict[str, Any] | None] | None = None,
    person_name_detector: Callable[[str], Awaitable[tuple[str, ...]]] | None = None,
    tool_call_preparer: Callable[[ToolCall], ToolCall] | None = None,
) -> Any:
    if local_provider is not None:
        raise ValueError(
            "select one configured inference provider; implicit fallback is unavailable"
        )

    if not 1 <= memory_limit <= 10:
        raise ValueError("memory limit is out of range")
    if not 512 <= memory_max_context_bytes <= 16_384:
        raise ValueError("memory context limit is out of range")
    if not 1 <= owner_profile_limit <= 10:
        raise ValueError("owner profile limit is out of range")
    if not 512 <= conversation_max_context_bytes <= 16_384:
        raise ValueError("conversation context limit is out of range")
    if not 512 <= social_context_max_bytes <= 4_096:
        raise ValueError("social context limit is out of range")
    broker = tool_broker or build_default_tool_broker()
    context = policy_context or default_policy_context(Path.cwd())
    executor = tool_executor or ReadOnlyToolExecutor()
    audit = audit_sink or NullAuditSink()
    activity = activity_tracker or SwarmActivityTracker()
    dialogue = dialogue_kernel or DialogueKernel()
    semantic_skill_cache: OrderedDict[str, tuple[str, ...]] = OrderedDict()
    # Router-chosen MCP tools per request; they only widen what the planner is offered.
    semantic_tool_selection: OrderedDict[UUID, frozenset[str]] = OrderedDict()
    execution_context: ContextVar[PolicyContext] = ContextVar(
        "swarm_execution_context",
        default=context,
    )

    def browser_reference(request: UserRequest) -> dict[str, Any] | None:
        if browser_task_context is None:
            return None
        reference = browser_task_context(request.request_id)
        if reference is None:
            return None
        if not isinstance(reference, dict) or reference.get("task_request_id") != str(
            request.request_id
        ):
            raise ValueError("browser_task_context_identity_invalid")
        clean = sanitize_remote_reference(reference)
        if len(json.dumps(clean, ensure_ascii=False, allow_nan=False).encode()) > 16_384:
            raise ValueError("browser_task_context_exceeds_limit")
        return clean

    def discovered_tool_names(request: UserRequest) -> frozenset[str]:
        names = (
            frozenset(dynamic_tool_names(request))
            if dynamic_tool_names is not None
            else frozenset()
        ) | semantic_tool_selection.get(request.request_id, frozenset())
        if "browser_task_open" in names and "browser_task_action" not in names:
            direct = direct_tool_call(request)
            if direct is not None and direct.tool_name == "browser_open_url":
                # Merely opening an explicit URL (or an approved recent source)
                # does not require a CDP session. Compound browser work still
                # follows the task/session/snapshot approval boundary below.
                names -= {"browser_task_open", "browser_task_close"}
        if request.metadata.get("source_channel") in {"schedule", "goal"}:
            # A scheduled run or goal step does its work; it can never create, list or
            # stop tasks or goals (live, a run of "resúmeme los correos cada mañana"
            # tried to schedule itself).
            names = frozenset(name for name in names if not name.startswith("jarvis_"))
        if names & {"browser_task_open", "browser_task_action", "browser_task_close"}:
            # A pre-existing MCP browser grant cannot bypass the task's exact
            # destination approval or its request/session/snapshot binding.
            names = frozenset(name for name in names if "__browser_" not in name)
        return names

    def scoped_builtin_names(request: UserRequest) -> frozenset[str]:
        names = _tool_names_for_request(request)
        discovered = discovered_tool_names(request)
        if any(name.endswith("__search_gmail_messages") for name in discovered):
            names -= {"mail_list_recent", "mail_send_message"}
        if any(name.endswith("__get_events") for name in discovered):
            names -= {"calendar_list_events", "calendar_create_event"}
        if discovered & {"browser_task_open", "browser_task_action", "browser_task_close"}:
            names -= {"browser_open_url", "browser_search", "computer_use"}
        return names

    def request_tool_names(request: UserRequest) -> frozenset[str]:
        return scoped_builtin_names(request) | discovered_tool_names(request)

    def scheduling_only(request: UserRequest) -> frozenset[str] | None:
        chosen = semantic_tool_selection.get(request.request_id, frozenset())
        if request.metadata.get("source_channel") in {"schedule", "goal"}:
            return None
        # The router read the request as scheduling ("los martes a las 18:30 dime mis
        # reuniones") or as a goal ("hazte cargo hasta terminarlo: …"); offering the
        # calendar or mail too made the planner run it now instead.
        for create, prefix in (
            ("jarvis_schedule_create", "jarvis_schedule_"),
            ("jarvis_goal_create", "jarvis_goal_"),
            ("jarvis_skill_create", "jarvis_skill_"),
            ("jarvis_plugin_install", "jarvis_plugin_"),
        ):
            if create in chosen:
                return frozenset(name for name in chosen if name.startswith(prefix))
        return None

    async def remote_redaction_for(text: str):
        # On-device NaturalLanguage tagging finds names without cue words; it fails
        # soft, so a missing helper only falls back to the cue-based detector.
        names: tuple[str, ...] = ()
        if person_name_detector is not None:
            try:
                names = await person_name_detector(text)
            except Exception as error:
                record_boundary_error(error)
                names = ()
        return redact_for_remote(text, person_names=names)

    def effective_tool_names(
        request: UserRequest,
        skill: SkillActivation | None,
        *,
        capability_gap: bool = False,
        scope_request: UserRequest | None = None,
        skills: tuple[SkillActivation, ...] = (),
    ) -> frozenset[str]:
        names = _unrestricted_tool_names(
            request,
            skill,
            capability_gap=capability_gap,
            scope_request=scope_request,
            skills=skills,
        )
        if is_chat_only_request(request.text):
            names = frozenset()
        elif is_explicit_native_ui_request(request.text):
            # The requested app is the destination. A semantic guess, old service
            # context or a Skill starter cannot substitute a cloud write for its UI.
            names = frozenset({"computer_use"})
        if is_unsigned_peer_request(request):
            return names & UNSIGNED_ENGINEERING_TOOLS
        # A plan is written from the request and its context: live, offering even the
        # read tools made it answer with a canned read result or ask for account ids.
        return frozenset() if plan_mode(request) else names

    def plan_mode(request: UserRequest) -> bool:
        return request.metadata.get(PLAN_MODE_METADATA) is True

    def _unrestricted_tool_names(
        request: UserRequest,
        skill: SkillActivation | None,
        *,
        capability_gap: bool,
        scope_request: UserRequest | None,
        skills: tuple[SkillActivation, ...],
    ) -> frozenset[str]:
        if capability_gap:
            if (
                is_engineering_request(request)
                and request.metadata.get(ENGINEERING_RESEARCH_METADATA)
                == EngineeringResearchPolicy.OFFLINE.value
            ):
                return frozenset()
            return frozenset({"web_research"})
        scope = scope_request or request
        discovered = discovered_tool_names(scope)
        requested = scoped_builtin_names(scope) | discovered
        if (scheduling := scheduling_only(request)) is not None:
            requested = scheduling
        # Only trusted local ToolDefinitions can declare offline operation; neither
        # a Skill nor a remote schema/name can grant this property.
        offline_safe_names = frozenset(
            name
            for name in requested
            if name.startswith("mcp_")
            and is_engineering_request(request)
            and (definition := broker.definition(name)) is not None
            and definition.offline_safe
        )
        if skill is None:
            return _respect_engineering_offline(request, requested, offline_safe_names)
        manifests = [item.manifest for item in skills] or [skill.manifest]
        allowed = frozenset().union(*(manifest.allowed_tools for manifest in manifests))
        starters = frozenset().union(*(manifest.starter_tools for manifest in manifests))
        combined = (requested & allowed) | starters | discovered
        if discovered & {"browser_task_open", "browser_task_action", "browser_task_close"}:
            combined -= {"browser_open_url", "browser_search", "computer_use"}
        return _respect_engineering_offline(
            request,
            combined,
            offline_safe_names,
        )

    async def execute_plan_step(
        authorization: ToolAuthorization,
    ) -> ToolExecutionResult:
        active_context = execution_context.get()
        if active_context.is_security_compromised():
            return ToolExecutionResult(
                call_id=authorization.call_id,
                tool_name=authorization.tool_name,
                success=False,
                error_code="security_compromised",
            )
        return await executor.execute_async(authorization, active_context)

    async def correct_plan_ui_block(
        authorization: ToolAuthorization,
        result: ToolExecutionResult,
    ) -> bool:
        if context.is_security_compromised():
            return False
        return await executor.correct_ui_block(authorization, result, execution_context.get())

    async def reload_plan_ui(authorization: ToolAuthorization) -> bool:
        if context.is_security_compromised():
            return False
        return await executor.reload_ui(authorization)

    async def reevaluate_plan_ui(authorization: ToolAuthorization) -> bool:
        if context.is_security_compromised():
            return False
        return await executor.reevaluate_ui(authorization)

    async def verify_reflected_ui(
        authorization: ToolAuthorization,
        result: ToolExecutionResult,
    ) -> bool:
        if not result_matches_authorization(authorization, result) or not result_is_verified(
            result
        ):
            return False
        try:
            report = ComputerUseReport.model_validate_json(result.output)
        except ValueError:
            return False
        if report.application_bundle_identifier != authorization.normalized_arguments.get(
            "application_bundle_identifier"
        ):
            return False
        if [check.kind for check in report.postcondition_checks] == ["action_performed"]:
            # An acknowledged literal gesture has no persistent semantic state to
            # re-read. It proves delivery of that reviewed gesture, nothing more.
            return (
                not authorization.normalized_arguments.get("postconditions")
                and report.last_action is not None
                and ComputerUseController._is_literal_gesture(
                    authorization.normalized_arguments.get("objective", "")
                )
            )
        expected = authorization.normalized_arguments.get("postconditions", ())
        if not expected:
            expected = tuple(
                condition.model_dump()
                for condition in ComputerUseController._inferred_postconditions(
                    authorization.normalized_arguments.get("objective", "")
                )
            )
        if [check.kind for check in report.postcondition_checks] != [
            condition["kind"] for condition in expected
        ]:
            return False
        if context.is_security_compromised():
            return False
        return await executor.reevaluate_ui(authorization, result=result)

    def guarded_device_action(action: DeviceLifecycleAction | None) -> DeviceLifecycleAction | None:
        if action is None:
            return None

        async def run(authorization: ToolAuthorization) -> tuple[bool, int]:
            if context.is_security_compromised():
                return False, 0
            return await action(authorization)

        return run

    plan_runner = PlanExecuteReflectRunner(
        execute_plan_step,
        ui_corrector=correct_plan_ui_block,
        ui_reevaluator=reevaluate_plan_ui,
        ui_reloader=reload_plan_ui,
        device_waker=guarded_device_action(device_waker),
        device_verifier=guarded_device_action(device_verifier),
        visual_reflector=VisualReflectionNode(verify_reflected_ui),
    )

    complete_for = create_completion(
        provider,
        activity,
    )

    semantic_skills = create_skill_selection(
        complete_for=complete_for,
        semantic_skill_cache=semantic_skill_cache,
        skill_registry=skill_registry,
    )

    plan_note, project_note, temporary_note, owner_voice = create_notes(
        personality_instruction=personality_instruction,
        plan_mode=plan_mode,
    )




    async def semantic_tools_for(request: UserRequest, history) -> frozenset[str]:
        if semantic_tool_catalogue is None:
            return frozenset()
        catalogue = tuple(semantic_tool_catalogue())
        if request.metadata.get("source_channel") in {"schedule", "goal"}:
            # A step does the work itself: offering "create goal" to a request that
            # starts with its objective made the router pick it instead of the mail tools.
            catalogue = tuple(entry for entry in catalogue if not entry[0].startswith("jarvis_"))

        async def complete(messages: list[dict[str, str]]) -> str:
            async with asyncio.timeout(4.0):
                result = await complete_for(
                    AgentRole.ROUTER,
                    audit_request_id=request.request_id,
                    max_tokens=400,
                    temperature=0.0,
                    structured_time_budget=4.0,
                    extra_body={"response_format": {"type": "json_object"}},
                    messages=messages,
                )
            if result.tool_calls:
                raise ValueError("router proposed a tool")
            return result.content

        attachments = tuple(document.name for document in request.documents) + tuple(
            item.get("name", "")
            for item in request.metadata.get("chat_document_context", ())
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        )
        chosen = await select_tools(
            request.text, history, catalogue, complete, attachments=attachments
        )
        task_tools = any(name.startswith("jarvis_") for name, _ in catalogue)
        if (
            history
            and task_tools
            and (not chosen or any(name.startswith("jarvis_") for name in chosen))
        ):
            # Jarvis's own tasks depend only on the current words. After several
            # scheduling turns the router read "revisa mis correos sin leer" and
            # "gracias" as task requests, and missed "¿qué tareas tengo programadas?";
            # its task choice must therefore match one made without history.
            alone = await select_tools(
                request.text, (), catalogue, complete, attachments=attachments
            )
            # Only tools that create or remove need the agreement; a list is read-only
            # and "¿cómo van mis objetivos?" lost it to a history-free misreading.
            return frozenset(
                name
                for name in chosen
                if not name.startswith("jarvis_") or name.endswith("_list") or name in alone
            ) | frozenset(name for name in alone if name.startswith("jarvis_"))
        return chosen

    async def schedule_call_for(request: UserRequest) -> ToolCall | None:
        async def complete(messages: list[dict[str, str]]) -> str:
            async with asyncio.timeout(6.0):
                result = await complete_for(
                    AgentRole.ROUTER,
                    audit_request_id=request.request_id,
                    max_tokens=400,
                    temperature=0.0,
                    structured_time_budget=6.0,
                    extra_body={"response_format": {"type": "json_object"}},
                    messages=messages,
                )
            if result.tool_calls:
                raise ValueError("schedule interpreter proposed a tool")
            return result.content

        arguments = await interpret_schedule_request(
            request.text, now=datetime.now(UTC), complete=complete
        )
        if arguments is None:
            return None
        return ToolCall(
            call_id=f"schedule-{request.request_id}",
            tool_name="jarvis_schedule_create",
            arguments=arguments,
            requested_by=AgentRole.PLANNER,
        )

    async def semantic_reference_for(
        request: UserRequest,
        checkpoint: TaskCheckpoint | None,
    ) -> TaskResolution:
        async def complete(messages: list[dict[str, str]]) -> str:
            async with asyncio.timeout(4.0):
                result = await complete_for(
                    AgentRole.ROUTER,
                    audit_request_id=request.request_id,
                    max_tokens=128,
                    temperature=0.0,
                    structured_time_budget=4.0,
                    extra_body={"response_format": {"type": "json_object"}},
                    messages=messages,
                )
            if result.tool_calls:
                raise ValueError("router proposed a tool")
            return result.content

        return await semantic_reference(request.text, checkpoint, complete)

    route_node = create_route_node(
        browser_reference=browser_reference,
        dialogue=dialogue,
        discovered_tool_names=discovered_tool_names,
        dynamic_tool_names=dynamic_tool_names,
        plan_mode=plan_mode,
        request_tool_names=request_tool_names,
        schedule_call_for=schedule_call_for,
        semantic_reference_for=semantic_reference_for,
        semantic_skills=semantic_skills,
        semantic_tool_selection=semantic_tool_selection,
        semantic_tools_for=semantic_tools_for,
        skill_registry=skill_registry,
    )

    def route_after_local_classification(state: SwarmState) -> str:
        if "direct_local_result" in state:
            return "direct_response"
        return "direct_action" if "direct_tool_call" in state else "recall_memory"

    def direct_response_node(state: SwarmState) -> dict[str, AgentResult]:
        result = state["direct_local_result"]
        callback = state.get("stream_callback")
        if callback is not None:
            callback(result.content)
        return {"specialist_result": result, "final_result": result}

    def direct_action_node(state: SwarmState) -> dict[str, Any]:
        call = state["direct_tool_call"]
        result = AgentResult(
            role=call.requested_by,
            model_id="local/deterministic-action",
            content="Acción local pendiente de autorización.",
            tool_calls=(call,),
        )
        return {"specialist_result": result, "specialist_results": (result,)}

    specialist_node = create_specialist_node(
        broker=broker,
        browser_reference=browser_reference,
        complete_for=complete_for,
        conversation_max_context_bytes=conversation_max_context_bytes,
        dialogue=dialogue,
        discovered_tool_names=discovered_tool_names,
        effective_tool_names=effective_tool_names,
        focus_priority_provider=focus_priority_provider,
        memory_max_context_bytes=memory_max_context_bytes,
        owner_voice=owner_voice,
        plan_mode=plan_mode,
        plan_note=plan_note,
        project_note=project_note,
        remote_redaction_for=remote_redaction_for,
        runtime_context_provider=runtime_context_provider,
        skill_registry=skill_registry,
        social_context_max_bytes=social_context_max_bytes,
        temporary_note=temporary_note,
    )

    recall_memory_node = create_recall_memory_node(
        capability_learning=capability_learning,
        memory_limit=memory_limit,
        memory_namespace=memory_namespace,
        memory_retriever=memory_retriever,
        owner_profile=owner_profile,
        owner_profile_limit=owner_profile_limit,
        social_memory=social_memory,
    )

    authorize_tools_node = create_authorize_tools_node(
        audit=audit,
        broker=broker,
        browser_reference=browser_reference,
        context=context,
        effective_tool_names=effective_tool_names,
        plan_mode=plan_mode,
        tool_call_preparer=tool_call_preparer,
    )

    def route_after_authorization(state: SwarmState) -> str:
        if any(
            authorization.decision is PolicyDecision.REQUIRE_CONFIRMATION
            for authorization in state.get("tool_authorizations", ())
        ):
            return "await_confirmation"
        return "execute"

    execute_read_tools_node = create_execute_read_tools_node(
        audit=audit,
        browser_reference=browser_reference,
        context=context,
        execution_context=execution_context,
        plan_runner=plan_runner,
    )

    def route_after_execution(state: SwarmState) -> str:
        results = state.get("tool_results", ())
        if (
            not state.get("browser_task_stopped", False)
            and 0 < state.get("browser_task_round", 0) < 24
            and state.get("specialist_result") is not None
            and state["specialist_result"].tool_calls
            and results
            and all(
                result.tool_name == "browser_task_action"
                and result.success
                and result.error_code is None
                for result in results
            )
            and browser_reference(state["request"]) is not None
        ):
            return "specialist"
        return "synthesize"

    async def clarification_for(state: SwarmState, reply: str) -> str | None:
        request = state["request"]
        if (
            is_engineering_request(request)
            or not _request_can_access_private_context(request)
            or (not may_ask_user(reply))
        ):
            return None

        async def complete(messages: list[dict[str, str]]) -> str:
            async with asyncio.timeout(4.0):
                result = await complete_for(
                    AgentRole.ROUTER,
                    audit_request_id=request.request_id,
                    max_tokens=256,
                    temperature=0.0,
                    structured_time_budget=4.0,
                    extra_body={"response_format": {"type": "json_object"}},
                    messages=messages,
                )
            if result.tool_calls:
                raise ValueError("router proposed a tool")
            return result.content

        return await structured_clarification(request.text, reply, complete)

    synthesize_node = create_synthesize_node(
        browser_reference=browser_reference,
        clarification_for=clarification_for,
        complete_for=complete_for,
        conversation_max_context_bytes=conversation_max_context_bytes,
        dialogue=dialogue,
        memory_max_context_bytes=memory_max_context_bytes,
        owner_voice=owner_voice,
        plan_mode=plan_mode,
        plan_note=plan_note,
        project_note=project_note,
        remote_redaction_for=remote_redaction_for,
        runtime_context_provider=runtime_context_provider,
        skill_registry=skill_registry,
        social_context_max_bytes=social_context_max_bytes,
        temporary_note=temporary_note,
    )

    builder = StateGraph(SwarmState)
    builder.add_node("route", route_node)
    builder.add_node("direct_response", direct_response_node)
    builder.add_node("direct_action", direct_action_node)
    builder.add_node("recall_memory", recall_memory_node)
    builder.add_node("specialist", specialist_node)
    builder.add_node("authorize_tools", authorize_tools_node)
    builder.add_node("execute_read_tools", execute_read_tools_node)
    builder.add_node("synthesize", synthesize_node)
    builder.add_edge(START, "route")
    builder.add_conditional_edges(
        "route",
        route_after_local_classification,
        {
            "direct_response": "direct_response",
            "direct_action": "direct_action",
            "recall_memory": "recall_memory",
        },
    )
    builder.add_edge("direct_response", END)
    builder.add_edge("direct_action", "authorize_tools")
    builder.add_edge("recall_memory", "specialist")
    builder.add_edge("specialist", "authorize_tools")
    builder.add_conditional_edges(
        "authorize_tools",
        route_after_authorization,
        {"await_confirmation": END, "execute": "execute_read_tools"},
    )
    builder.add_conditional_edges(
        "execute_read_tools",
        route_after_execution,
        {"specialist": "specialist", "synthesize": "synthesize"},
    )
    builder.add_edge("synthesize", END)
    # At most 24 browser operations, each using specialist/authorization/execution.
    # The operation counter remains authoritative; this is only LangGraph's guard.
    return builder.compile().with_config({"recursion_limit": 128})


def build_nvidia_swarm_graph(provider: ChatProvider, **kwargs: Any) -> Any:
    """Production graph: reject local configuration and disable compatibility inference."""
    if kwargs.get("local_provider") is not None or kwargs.get("inference_nvidia_only") is False:
        raise ValueError("production inference must use NVIDIA exclusively")
    if not callable(getattr(provider, "complete_nvidia_only", None)):
        raise ValueError("production provider must support NVIDIA-only completion")
    kwargs["inference_nvidia_only"] = True
    return build_swarm_graph(provider, **kwargs)
