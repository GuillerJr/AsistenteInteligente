"""Dot's page in the chat window: what Jarvis does on its own, around the clock.

Like the page of Codex's always-on "dot", it gathers in one place what is running now
(scheduled tasks and goals at work), what Jarvis keeps an eye on next (enabled tasks
by their next run, subscriptions that only speak when something is new), the latest
results it delivered and what waits for the owner. One switch pauses Dot: scheduled
tasks and goals stop starting on their own until it is resumed, while the owner can
still run any of them by hand. The pause survives restarts.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

MAX_UPCOMING = 8
MAX_RECENT = 8
MAX_EXCERPT = 280


class DotPause:
    """Owner-only flag file; an unreadable file means not paused."""

    def __init__(self, path: Path, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self._path = path
        self._clock = clock
        self._cached: tuple[bool, str | None] | None = None

    def state(self) -> tuple[bool, str | None]:
        if self._cached is None:
            try:
                value = json.loads(self._path.read_text(encoding="utf-8"))
                self._cached = (value.get("paused") is True,
                                value.get("since") if isinstance(value.get("since"), str) else None)
            except (OSError, ValueError, AttributeError):
                self._cached = (False, None)
        return self._cached

    def is_paused(self) -> bool:
        return self.state()[0]

    def set(self, paused: bool) -> None:
        since = self._clock().isoformat() if paused else None
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self._path.with_name(f".{self._path.name}.{uuid4().hex}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"paused": paused, "since": since}, handle)
            os.replace(temporary, self._path)
        finally:
            temporary.unlink(missing_ok=True)
        self._cached = (paused, since)


def _excerpt(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= MAX_EXCERPT else flat[: MAX_EXCERPT - 1].rstrip() + "…"


class DotIpcService:
    METHODS = frozenset({"dot.overview", "dot.pause", "dot.resume"})

    def __init__(self, pause: DotPause, *, schedules: Any, goals: Any, scheduler: Any,
                 goal_runner: Any, inbox: Any, attention: Any,
                 on_resume: Callable[[], None] = lambda: None) -> None:
        self._pause = pause
        self._schedules = schedules
        self._goals = goals
        self._scheduler = scheduler
        self._goal_runner = goal_runner
        self._inbox = inbox
        self._attention = attention
        self._on_resume = on_resume

    def handlers(self) -> dict[str, Any]:
        return {method: self.handle for method in self.METHODS}

    def overview(self, approvals: tuple = ()) -> dict[str, Any]:
        tasks = self._schedules.tasks()
        goals = self._goals.goals()
        running_tasks = set(self._scheduler.running)
        running_goals = set(self._goal_runner.running)
        now = [
            {"kind": "schedule", "title": task.title, "detail": "Tarea programada en curso",
             "conversation_id": str(task.conversation_id) if task.conversation_id else None}
            for task in tasks if task.task_id in running_tasks
        ] + [
            {"kind": "goal", "title": goal.title,
             "detail": f"Objetivo · paso {goal.steps_used + 1} de {goal.max_steps}",
             "conversation_id": str(goal.conversation_id) if goal.conversation_id else None}
            for goal in goals if goal.goal_id in running_goals
        ]
        active_goals = [goal.public() for goal in goals if goal.status.value in {
            "active", "paused", "blocked", "budget_limited"}]
        upcoming = sorted((task for task in tasks if task.enabled and task.next_run_at),
                          key=lambda task: task.next_run_at)[:MAX_UPCOMING]
        recent = sorted(self._inbox.after(None), key=lambda item: item.delivered_at,
                        reverse=True)[:MAX_RECENT]
        paused, since = self._pause.state()
        return {
            "paused": paused,
            "paused_since": since,
            "now": now,
            "goals": active_goals,
            "upcoming": [{
                "task_id": str(task.task_id), "title": task.title,
                "schedule": task.schedule.describe(),
                "next_run_at": task.next_run_at.isoformat(),
                # A subscription speaks only when something changed.
                "monitoring": task.notify.value == "when_new",
                "last_status": task.last_status,
            } for task in upcoming],
            "recent": [{
                # Deliveries start with "⏰ <title>" for Telegram; the page shows the title.
                "title": item.title,
                "text": _excerpt(item.text.removeprefix(f"⏰ {item.title}")),
                "delivered_at": item.delivered_at.isoformat(),
                "conversation_id": str(item.conversation_id) if item.conversation_id else None,
            } for item in recent],
            "attention": len(self._attention.items(approvals)),
            "counts": {"schedules": sum(1 for task in tasks if task.enabled),
                       "goals": len(active_goals)},
        }

    async def handle(self, request: Any) -> Any:
        import asyncio

        from aegis_core.ipc.server import IpcHandlerResult

        if request.method not in self.METHODS:
            return IpcHandlerResult(ok=False, error_code="method_not_found")
        if request.payload:
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        if request.method != "dot.overview":
            paused = request.method == "dot.pause"
            await asyncio.to_thread(self._pause.set, paused)
            if not paused:
                self._on_resume()
        # Pending approvals live on the event loop; the stores are read in a thread.
        approvals = tuple(self._attention.approvals())
        return IpcHandlerResult(ok=True,
                                payload=await asyncio.to_thread(self.overview, approvals))
