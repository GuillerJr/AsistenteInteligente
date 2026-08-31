from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class BehaviorStatus(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    RUNNING = "running"
    USER_INTERVENTION = "user_intervention"


@dataclass(frozen=True, slots=True)
class BehaviorResult:
    status: BehaviorStatus
    node: str
    reason: str = ""


@dataclass(slots=True)
class BehaviorContext:
    graph_context: str = ""
    values: dict[str, Any] = field(default_factory=dict)
    trace: list[BehaviorResult] = field(default_factory=list)
    action_history: list[str] = field(default_factory=list)
    intervention_reason: str | None = None


class BehaviorNode(ABC):
    def __init__(self, name: str) -> None:
        if not name or len(name) > 96:
            raise ValueError("behavior node name is invalid")
        self.name = name

    @abstractmethod
    async def tick(self, context: BehaviorContext) -> BehaviorResult:
        raise NotImplementedError

    @staticmethod
    def _record(context: BehaviorContext, result: BehaviorResult) -> BehaviorResult:
        context.trace.append(result)
        return result


class Sequence(BehaviorNode):
    def __init__(self, name: str, children: tuple[BehaviorNode, ...]) -> None:
        super().__init__(name)
        if not children:
            raise ValueError("behavior sequence cannot be empty")
        self._children = children

    async def tick(self, context: BehaviorContext) -> BehaviorResult:
        for child in self._children:
            result = await child.tick(context)
            if result.status is not BehaviorStatus.SUCCESS:
                return self._record(
                    context,
                    BehaviorResult(result.status, self.name, result.reason),
                )
        return self._record(context, BehaviorResult(BehaviorStatus.SUCCESS, self.name))


class Selector(BehaviorNode):
    def __init__(self, name: str, children: tuple[BehaviorNode, ...]) -> None:
        super().__init__(name)
        if not children:
            raise ValueError("behavior selector cannot be empty")
        self._children = children

    async def tick(self, context: BehaviorContext) -> BehaviorResult:
        last_reason = "selector_exhausted"
        for child in self._children:
            result = await child.tick(context)
            if result.status in {
                BehaviorStatus.SUCCESS,
                BehaviorStatus.RUNNING,
                BehaviorStatus.USER_INTERVENTION,
            }:
                return self._record(
                    context,
                    BehaviorResult(result.status, self.name, result.reason),
                )
            last_reason = result.reason or last_reason
        return self._record(
            context,
            BehaviorResult(BehaviorStatus.FAILURE, self.name, last_reason),
        )


ConditionPredicate = Callable[[BehaviorContext], bool]


class Condition(BehaviorNode):
    def __init__(self, name: str, predicate: ConditionPredicate) -> None:
        super().__init__(name)
        self._predicate = predicate

    async def tick(self, context: BehaviorContext) -> BehaviorResult:
        try:
            matched = self._predicate(context)
        except Exception:
            matched = False
        return self._record(
            context,
            BehaviorResult(
                BehaviorStatus.SUCCESS if matched else BehaviorStatus.FAILURE,
                self.name,
                "condition_matched" if matched else "condition_not_matched",
            ),
        )


ActionHandler = Callable[[BehaviorContext], Awaitable[BehaviorStatus | bool]]


class Action(BehaviorNode):
    def __init__(
        self,
        name: str,
        handler: ActionHandler,
        *,
        cycle_detector: CycleDetector | None = None,
    ) -> None:
        super().__init__(name)
        self._handler = handler
        self._cycle_detector = cycle_detector

    async def tick(self, context: BehaviorContext) -> BehaviorResult:
        context.action_history.append(self.name)
        if self._cycle_detector is not None:
            reason = self._cycle_detector.detect(context.action_history)
            if reason is not None:
                context.intervention_reason = reason
                return self._record(
                    context,
                    BehaviorResult(
                        BehaviorStatus.USER_INTERVENTION,
                        self.name,
                        reason,
                    ),
                )
        try:
            raw = await self._handler(context)
        except Exception:
            raw = BehaviorStatus.FAILURE
        if isinstance(raw, bool):
            status = BehaviorStatus.SUCCESS if raw else BehaviorStatus.FAILURE
        elif isinstance(raw, BehaviorStatus):
            status = raw
        else:
            status = BehaviorStatus.FAILURE
        return self._record(
            context,
            BehaviorResult(
                status,
                self.name,
                "" if status is BehaviorStatus.SUCCESS else "action_failed",
            ),
        )


class CycleDetector:
    """Stops repeated A/B oscillation or the same action after a third recurrence."""

    def detect(self, history: list[str]) -> str | None:
        if history.count(history[-1]) >= 3:
            return "behavior_action_repeated_three_times"
        if len(history) >= 6:
            tail = history[-6:]
            if tail[0] == tail[2] == tail[4] and tail[1] == tail[3] == tail[5]:
                return "behavior_action_cycle_detected"
        return None


PrimaryAction = Callable[[], Awaitable[bool]]
RecoveryAction = Callable[[], Awaitable[bool]]
InterventionHandler = Callable[[str], Awaitable[None]]


class TacticalUIBehaviorTree:
    """Permanent fail-closed tree for AX/web obstacles with bounded recovery."""

    def __init__(
        self,
        *,
        try_action: PrimaryAction,
        detect_modal: Callable[[], bool],
        dismiss_modal: RecoveryAction,
        reevaluate: RecoveryAction,
        reload_action: RecoveryAction,
        retry_parent: PrimaryAction,
        on_user_intervention: InterventionHandler | None = None,
    ) -> None:
        self._try_action = try_action
        self._detect_modal = detect_modal
        self._dismiss_modal = dismiss_modal
        self._reevaluate = reevaluate
        self._reload_action = reload_action
        self._retry_parent = retry_parent
        self._intervention = on_user_intervention
        detector = CycleDetector()

        async def run(callback: Callable[[], Awaitable[bool]]) -> BehaviorStatus:
            return BehaviorStatus.SUCCESS if await callback() else BehaviorStatus.FAILURE

        self._root = Selector(
            "root_selector",
            (
                Action("try_action", lambda _: run(self._try_action), cycle_detector=detector),
                Sequence(
                    "obstacle_recovery_sequence",
                    (
                        Condition(
                            "detect_modal_condition",
                            lambda context: self._detect_modal()
                            or self._historical_modal_hint(context.graph_context),
                        ),
                        Action(
                            "dismiss_modal_action",
                            lambda _: run(self._dismiss_modal),
                            cycle_detector=detector,
                        ),
                        Action(
                            "reevaluate_tree_status",
                            lambda _: run(self._reevaluate),
                            cycle_detector=detector,
                        ),
                        Action(
                            "reload_action",
                            lambda _: run(self._reload_action),
                            cycle_detector=detector,
                        ),
                        Action(
                            "retry_parent_input",
                            lambda _: run(self._retry_parent),
                            cycle_detector=detector,
                        ),
                    ),
                ),
            ),
        )

    async def run(self, *, graph_context: str = "") -> BehaviorContext:
        context = BehaviorContext(graph_context=graph_context[:8_192])
        result = BehaviorResult(BehaviorStatus.FAILURE, "root_selector")
        for _ in range(3):
            result = await self._root.tick(context)
            if result.status in {
                BehaviorStatus.SUCCESS,
                BehaviorStatus.USER_INTERVENTION,
            }:
                break
        if result.status is BehaviorStatus.FAILURE:
            context.intervention_reason = "behavior_recovery_exhausted"
        if result.status is BehaviorStatus.USER_INTERVENTION:
            reason = context.intervention_reason or "behavior_cycle_detected"
            if self._intervention is not None:
                await self._intervention(reason)
        elif context.intervention_reason is not None and self._intervention is not None:
            await self._intervention(context.intervention_reason)
        return context

    @staticmethod
    def _historical_modal_hint(graph_context: str) -> bool:
        normalized = graph_context.casefold()
        return any(
            marker in normalized
            for marker in ("cookie", "modal", "diálogo", "dialog", "popup", "consent")
        )
