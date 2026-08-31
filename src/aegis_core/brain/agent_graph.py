from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from aegis_core.contracts import PolicyDecision, ToolAuthorization, ToolExecutionResult

MAX_REFLECTIONS_PER_TASK = 3

ToolExecutor = Callable[[ToolAuthorization], Awaitable[ToolExecutionResult]]
UICorrector = Callable[
    [ToolAuthorization, ToolExecutionResult],
    Awaitable[bool],
]


class PlanStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    CORRECTING = "correcting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PlanStep:
    ordinal: int
    call_id: str
    tool_name: str
    status: PlanStepStatus = PlanStepStatus.PENDING


@dataclass(frozen=True, slots=True)
class PlanContract:
    goal_sha256: str
    steps: tuple[PlanStep, ...]

    def markdown(self) -> str:
        lines = [
            "# Jarvis Execution Contract",
            "",
            f"Goal-SHA256: `{self.goal_sha256}`",
            "",
        ]
        markers = {
            PlanStepStatus.PENDING: " ",
            PlanStepStatus.RUNNING: "~",
            PlanStepStatus.CORRECTING: "!",
            PlanStepStatus.SUCCEEDED: "x",
            PlanStepStatus.FAILED: "!",
        }
        lines.extend(
            f"{step.ordinal}. [{markers[step.status]}] `{step.tool_name}` — {step.status.value}"
            for step in self.steps
        )
        return "\n".join(lines)

    def with_status(self, index: int, status: PlanStepStatus) -> PlanContract:
        if not 0 <= index < len(self.steps):
            raise IndexError("plan step is out of range")
        updated = list(self.steps)
        updated[index] = replace(updated[index], status=status)
        return replace(self, steps=tuple(updated))


@dataclass(frozen=True, slots=True)
class ReflectionRecord:
    step_ordinal: int
    attempt: int
    outcome: str
    accessibility_verified: bool
    correction_attempted: bool


@dataclass(frozen=True, slots=True)
class PlanExecutionOutcome:
    contract: PlanContract
    results: tuple[ToolExecutionResult, ...]
    attempts: tuple[ToolExecutionResult, ...]
    reflections: tuple[ReflectionRecord, ...]
    halted: bool
    halt_reason: str | None


class _GraphState(TypedDict, total=False):
    goal: str
    authorizations: tuple[ToolAuthorization, ...]
    contract: PlanContract
    cursor: int
    attempts: tuple[ToolExecutionResult, ...]
    final_results: dict[str, ToolExecutionResult]
    last_result: ToolExecutionResult
    reflections: tuple[ReflectionRecord, ...]
    reflection_count: int
    correction_pending: bool
    halted: bool
    halt_reason: str | None


class PlanExecuteReflectRunner:
    def __init__(
        self,
        executor: ToolExecutor,
        *,
        ui_corrector: UICorrector | None = None,
        maximum_reflections: int = MAX_REFLECTIONS_PER_TASK,
    ) -> None:
        if not 1 <= maximum_reflections <= MAX_REFLECTIONS_PER_TASK:
            raise ValueError("reflection limit is out of range")
        self._executor = executor
        self._ui_corrector = ui_corrector
        self._maximum_reflections = maximum_reflections
        self._graph = self._build_graph()

    async def run(
        self,
        *,
        goal: str,
        authorizations: Sequence[ToolAuthorization],
    ) -> PlanExecutionOutcome:
        allowed = tuple(
            authorization
            for authorization in authorizations
            if authorization.decision is PolicyDecision.ALLOW
        )
        state = await self._graph.ainvoke(
            {
                "goal": goal,
                "authorizations": allowed,
                "cursor": 0,
                "attempts": (),
                "final_results": {},
                "reflections": (),
                "reflection_count": 0,
                "correction_pending": False,
                "halted": False,
                "halt_reason": None,
            },
            config={"recursion_limit": 32},
        )
        contract = state["contract"]
        final_by_id = state.get("final_results", {})
        results = tuple(
            final_by_id[authorization.call_id]
            for authorization in allowed
            if authorization.call_id in final_by_id
        )
        return PlanExecutionOutcome(
            contract=contract,
            results=results,
            attempts=state.get("attempts", ()),
            reflections=state.get("reflections", ()),
            halted=state.get("halted", False),
            halt_reason=state.get("halt_reason"),
        )

    def _build_graph(self) -> Any:
        builder = StateGraph(_GraphState)
        builder.add_node("planner", self._planner_node)
        builder.add_node("executor", self._executor_node)
        builder.add_node("reflector", self._reflector_node)
        builder.add_node("self_correction", self._self_correction_node)
        builder.add_edge(START, "planner")
        builder.add_conditional_edges(
            "planner",
            self._route_after_planning,
            {"execute": "executor", "end": END},
        )
        builder.add_edge("executor", "reflector")
        builder.add_conditional_edges(
            "reflector",
            self._route_after_reflection,
            {"correct": "self_correction", "execute": "executor", "end": END},
        )
        builder.add_conditional_edges(
            "self_correction",
            self._route_after_correction,
            {"execute": "executor", "end": END},
        )
        return builder.compile()

    @staticmethod
    async def _planner_node(state: _GraphState) -> dict[str, Any]:
        goal = state["goal"]
        authorizations = state["authorizations"]
        contract = PlanContract(
            goal_sha256=hashlib.sha256(goal.encode("utf-8")).hexdigest(),
            steps=tuple(
                PlanStep(
                    ordinal=index + 1,
                    call_id=authorization.call_id,
                    tool_name=authorization.tool_name,
                )
                for index, authorization in enumerate(authorizations)
            ),
        )
        return {"contract": contract}

    async def _executor_node(self, state: _GraphState) -> dict[str, Any]:
        cursor = state["cursor"]
        authorization = state["authorizations"][cursor]
        contract = state["contract"].with_status(cursor, PlanStepStatus.RUNNING)
        task = asyncio.create_task(
            self._executor(authorization),
            name=f"plan-step-{cursor + 1}-{authorization.tool_name}",
        )
        result = await task
        return {
            "contract": contract,
            "last_result": result,
            "attempts": (*state.get("attempts", ()), result),
            "correction_pending": False,
        }

    async def _reflector_node(self, state: _GraphState) -> dict[str, Any]:
        cursor = state["cursor"]
        result = state["last_result"]
        verified = self._result_verified(result)
        if verified:
            final_results = dict(state.get("final_results", {}))
            final_results[result.call_id] = result
            return {
                "contract": state["contract"].with_status(
                    cursor,
                    PlanStepStatus.SUCCEEDED,
                ),
                "cursor": cursor + 1,
                "final_results": final_results,
                "reflections": (
                    *state.get("reflections", ()),
                    ReflectionRecord(
                        step_ordinal=cursor + 1,
                        attempt=self._attempt_count(state, result.call_id),
                        outcome="verified",
                        accessibility_verified=True,
                        correction_attempted=False,
                    ),
                ),
            }
        reflection_count = state.get("reflection_count", 0) + 1
        can_correct = (
            reflection_count < self._maximum_reflections
            and self._ui_corrector is not None
            and self._is_correctable_ui_block(result)
        )
        reflections = (
            *state.get("reflections", ()),
            ReflectionRecord(
                step_ordinal=cursor + 1,
                attempt=self._attempt_count(state, result.call_id),
                outcome=self._safe_outcome(result),
                accessibility_verified=False,
                correction_attempted=can_correct,
            ),
        )
        if can_correct:
            return {
                "contract": state["contract"].with_status(
                    cursor,
                    PlanStepStatus.CORRECTING,
                ),
                "reflection_count": reflection_count,
                "reflections": reflections,
                "correction_pending": True,
            }
        final_results = dict(state.get("final_results", {}))
        final_results[result.call_id] = result
        return {
            "contract": state["contract"].with_status(cursor, PlanStepStatus.FAILED),
            "final_results": final_results,
            "reflection_count": reflection_count,
            "reflections": reflections,
            "halted": True,
            "halt_reason": (
                "reflection_limit"
                if reflection_count >= self._maximum_reflections
                else "step_failed"
            ),
        }

    async def _self_correction_node(self, state: _GraphState) -> dict[str, Any]:
        if self._ui_corrector is None:
            return {"halted": True, "halt_reason": "correction_unavailable"}
        authorization = state["authorizations"][state["cursor"]]
        corrected = await self._ui_corrector(authorization, state["last_result"])
        if not corrected:
            final_results = dict(state.get("final_results", {}))
            final_results[state["last_result"].call_id] = state["last_result"]
            return {
                "contract": state["contract"].with_status(
                    state["cursor"],
                    PlanStepStatus.FAILED,
                ),
                "final_results": final_results,
                "correction_pending": False,
                "halted": True,
                "halt_reason": "self_correction_failed",
            }
        return {"correction_pending": False}

    @staticmethod
    def _route_after_planning(state: _GraphState) -> str:
        return "execute" if state["authorizations"] else "end"

    @staticmethod
    def _route_after_reflection(state: _GraphState) -> str:
        if state.get("halted", False):
            return "end"
        if state.get("correction_pending", False):
            return "correct"
        return "end" if state["cursor"] >= len(state["authorizations"]) else "execute"

    @staticmethod
    def _route_after_correction(state: _GraphState) -> str:
        return "end" if state.get("halted", False) else "execute"

    @staticmethod
    def _result_verified(result: ToolExecutionResult) -> bool:
        if not result.success:
            return False
        verified = result.metadata.get("verified")
        status = result.metadata.get("status")
        if verified is False or status in {"blocked", "failed", "step_limit"}:
            return False
        return True

    @staticmethod
    def _is_correctable_ui_block(result: ToolExecutionResult) -> bool:
        return (
            result.tool_name == "computer_use"
            and result.metadata.get("status") == "blocked"
            and result.metadata.get("reason_code") == "uncertain_state"
            and result.metadata.get("steps") == 0
        )

    @staticmethod
    def _safe_outcome(result: ToolExecutionResult) -> str:
        if result.error_code is not None:
            return result.error_code
        reason = result.metadata.get("reason_code")
        return reason if isinstance(reason, str) else "unverified_state"

    @staticmethod
    def _attempt_count(state: _GraphState, call_id: str) -> int:
        return sum(result.call_id == call_id for result in state.get("attempts", ()))
