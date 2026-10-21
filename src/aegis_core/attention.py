"""What is waiting for the owner ("Requiere tu respuesta", Codex's attention list).

Scheduled tasks that could not finish alone, goals that are blocked or out of budget
and actions waiting for approval each told the owner once, as a message that scrolls
away. This list gathers them in one place for the app, the notch and Telegram, with
the action that resolves each one and a snooze that hides an item for an hour. It
only reads the task and goal stores and the pending approvals and keeps snoozes in
memory; it never approves or runs anything by itself.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aegis_core.goals import GoalStatus, GoalStore
from aegis_core.job_contracts import PendingApproval
from aegis_core.scheduling import ScheduledTaskStore

SNOOZE = timedelta(hours=1)
_TASK_REASONS = {
    "needs_approval": "Necesita que apruebes una lectura con «Aprobar siempre».",
    "failed": "La última ejecución falló.",
    "timeout": "La última ejecución tardó demasiado.",
}
_GOAL_REASONS = {
    GoalStatus.BLOCKED: "Está bloqueado y necesita algo de ti.",
    GoalStatus.BUDGET_LIMITED: "Usó todos sus pasos sin terminar.",
}


class AttentionItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str
    kind: str = Field(pattern=r"^(task|goal|approval)$")
    title: str
    reason: str
    action: str = Field(pattern=r"^(run_task|resume_goal|review_approval)$")
    conversation_id: UUID | None
    updated_at: datetime | None


class AttentionList:
    def __init__(self, tasks: ScheduledTaskStore, goals: GoalStore,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC), *,
                 approvals: Callable[[], Iterable[PendingApproval]] = tuple) -> None:
        self._tasks = tasks
        self._goals = goals
        self._clock = clock
        self._snoozed: dict[str, datetime] = {}
        # Read on the event loop (the job registry has no lock of its own).
        self.approvals = approvals

    def items(self, approvals: Iterable[PendingApproval] | None = None,
              ) -> tuple[AttentionItem, ...]:
        now = self._clock()
        self._snoozed = {key: until for key, until in self._snoozed.items() if until > now}
        found: list[AttentionItem] = []
        for task in self._tasks.tasks():
            reason = _TASK_REASONS.get(task.last_status or "")
            if task.enabled and reason:
                found.append(AttentionItem(
                    item_id=f"task:{task.task_id}", kind="task", title=task.title,
                    reason=reason, action="run_task", conversation_id=task.conversation_id,
                    updated_at=task.last_run_at))
        for goal in self._goals.goals():
            reason = _GOAL_REASONS.get(goal.status)
            if reason:
                found.append(AttentionItem(
                    item_id=f"goal:{goal.goal_id}", kind="goal", title=goal.title,
                    reason=reason, action="resume_goal", conversation_id=goal.conversation_id,
                    updated_at=goal.updated_at))
        for approval in self.approvals() if approvals is None else approvals:
            if approval.expires_at <= now:
                continue
            where = ("en Telegram" if approval.source_channel == "telegram"
                     else "en la ventana de aprobación")
            found.append(AttentionItem(
                item_id=f"approval:{approval.job_id}", kind="approval",
                title=" ".join(approval.summary.split())[:80],
                reason=f"Espera tu aprobación {where}.", action="review_approval",
                conversation_id=approval.conversation_id, updated_at=approval.updated_at))
        visible = [item for item in found if item.item_id not in self._snoozed]
        return tuple(sorted(visible, key=lambda item: item.updated_at or now, reverse=True))

    def snooze(self, item_id: str) -> None:
        self._snoozed[item_id] = self._clock() + SNOOZE

    def text(self) -> str:
        items = self.items()
        if not items:
            return "No hay nada pendiente. Todo está al día."
        lines = [f"{index}. {item.title}: {item.reason}" for index, item in enumerate(items, 1)]
        return "Requiere tu atención:\n" + "\n".join(lines) + (
            "\n\nÁbrelos en la app (sección Pendientes del chat) para reintentar o reanudar.")


class _SnoozePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str = Field(pattern=r"^(task|goal|approval):[0-9a-f-]{36}$")


class AttentionIpcService:
    METHODS = frozenset({"attention.list", "attention.snooze"})

    def __init__(self, attention: AttentionList) -> None:
        self._attention = attention

    def handlers(self) -> dict[str, Any]:
        return {method: self.handle for method in self.METHODS}

    async def handle(self, request: Any) -> Any:
        from aegis_core.ipc.server import IpcHandlerResult

        try:
            if request.method == "attention.snooze":
                self._attention.snooze(_SnoozePayload.model_validate(request.payload).item_id)
            elif request.payload:
                return IpcHandlerResult(ok=False, error_code="invalid_payload")
        except ValidationError:
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        approvals = tuple(self._attention.approvals())
        items = await asyncio.to_thread(self._attention.items, approvals)
        return IpcHandlerResult(ok=True, payload={
            "items": [item.model_dump(mode="json") for item in items]})
