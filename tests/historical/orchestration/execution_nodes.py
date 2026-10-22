from __future__ import annotations

from dataclasses import replace
from typing import Any

from aegis_core.orchestration.routing import (
    _bounded_browser_result_reference,
)
from aegis_core.orchestration.state import (
    SwarmState,
)


def create_execute_read_tools_node(
    *,
    audit,
    browser_reference,
    context,
    execution_context,
    plan_runner,
):
    async def execute_read_tools_node(state: SwarmState) -> dict[str, Any]:
        token = execution_context.set(
            replace(
                context,
                request_id=state["request"].request_id,
                request_text=state["request"].text,
            )
        )
        try:
            outcome = await plan_runner.run(
                goal=state["request"].text,
                authorizations=state.get("tool_authorizations", ()),
                historical_context=state.get("graph_memory_context", ""),
            )
        finally:
            execution_context.reset(token)
        for result in outcome.attempts:
            audit.record_execution(state["request"].request_id, result)
        if outcome.halted or len(outcome.attempts) > len(outcome.results):
            audit.record_system_event(
                state["request"].request_id,
                event_type="plan_reflection_completed",
                component="agent_graph",
                data={
                    "goal_sha256": outcome.contract.goal_sha256,
                    "halt_reason": outcome.halt_reason,
                    "halted": outcome.halted,
                    "reflections": len(outcome.reflections),
                    "steps": len(outcome.contract.steps),
                },
            )
        update: dict[str, Any] = {
            "tool_results": outcome.results,
            "execution_contract": outcome.contract.markdown(),
            "reflection_count": len(outcome.reflections),
        }
        browser_results = tuple(
            _bounded_browser_result_reference(result)
            for result in outcome.results
            if result.tool_name.startswith("browser_task_")
        )
        if browser_results:
            update["tool_results"] = tuple(
                _bounded_browser_result_reference(result)
                if result.tool_name.startswith("browser_task_")
                else result
                for result in outcome.results
            )
        accumulated = (*state.get("browser_task_results", ()), *browser_results)[-4:]
        while (
            accumulated
            and sum(len(result.model_dump_json().encode()) for result in accumulated) > 32_768
        ):
            accumulated = accumulated[1:]
        update["browser_task_results"] = accumulated
        if browser_results:
            rounds = state.get("browser_task_round", 0) + len(browser_results)
            update["browser_task_round"] = rounds
            if rounds >= 24:
                update["errors"] = [*state.get("errors", []), "browser_task_step_limit"]
            if (
                any(
                    not result.success
                    or result.error_code is not None
                    or result.metadata.get("status") in {"partial", "blocked", "failed"}
                    for result in browser_results
                )
                or browser_reference(state["request"]) is None
            ):
                update["browser_task_stopped"] = True
        elif accumulated:
            # Keep the last bounded observations for the final answer rather
            # than trusting an unsupported "done" after an empty tool pass.
            update["tool_results"] = accumulated
        if outcome.halted:
            update["errors"] = [
                *state.get("errors", []),
                f"plan_halted_{outcome.halt_reason or 'unknown'}",
            ]
        return update

    return execute_read_tools_node
