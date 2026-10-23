"""How Jarvis is doing, for the owner (Claude's and Codex's usage panel, as a health panel).

NVIDIA has no quota to show, so the panel shows what the owner can act on: whether
NVIDIA answers and how often it is rate limited, which integrations are connected or
quarantined, and the latest failures. It reads only the tail of the audit log, whose
technical events carry no user content, plus the live MCP status and cooldown; it
changes nothing.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any

from aegis_core.failure_boundary import record_boundary_error

WINDOW = timedelta(hours=24)
TAIL_BYTES = 1_500_000
MAX_FAILURES = 8
_FAILURE_EVENTS = {
    "swarm_execution_failed": "Una petición falló",
    "job_failed": "Una petición falló",
    "mcp_tool_failed": "Una herramienta de integración falló",
    "mcp_server_quarantined": "Una integración quedó en cuarentena",
    "telegram_delivery_failed": "No se pudo entregar en Telegram",
}
_CODE_KEYS = ("error_code", "reason_code", "server_id", "tool")
_FAILURE_TITLES = {
    "incomplete_model_response": "NVIDIA devolvió una respuesta incompleta",
    "web_access_failed": "No se pudieron consultar las fuentes web",
    "web_search_unavailable": "Los proveedores de búsqueda no están disponibles",
    "confirmation_expired": "La aprobación caducó antes de ejecutar la acción",
}


def _tail_events(path: Path, limit: int = TAIL_BYTES) -> list[dict[str, Any]]:
    try:
        with path.open("rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            handle.seek(max(0, size - limit))
            raw = handle.read(limit)
    except OSError:
        return []
    lines = raw.split(b"\n")
    if len(raw) == limit:
        lines = lines[1:]  # The first line is cut where the tail began.
    events = []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and isinstance(record.get("event_type"), str):
            events.append(record)
    return events


def _when(record: dict[str, Any]) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(record.get("timestamp")).replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def health_report(events: Iterable[dict[str, Any]], *, now: datetime,
                  mcp_status: dict[str, Any] | None, credential: str | None,
                  cooldown_seconds: float) -> dict[str, Any]:
    since = now - WINDOW
    outcomes: Counter[str] = Counter()
    latencies: list[int] = []
    last_success = last_problem = None
    failures: list[dict[str, Any]] = []
    for record in events:
        when = _when(record)
        if when is None or when < since:
            continue
        data = record.get("data") if isinstance(record.get("data"), dict) else {}
        kind = record["event_type"]
        if kind == "nvidia_inference":
            outcome = str(data.get("outcome") or "unknown")
            outcomes[outcome] += 1
            if outcome == "completed":
                last_success = max(last_success or when, when)
                if isinstance(data.get("latency_ms"), int):
                    latencies.append(data["latency_ms"])
            elif outcome != "cancelled":
                last_problem = max(last_problem or when, when)
        elif kind in _FAILURE_EVENTS:
            detail = " · ".join(str(data[key]) for key in _CODE_KEYS
                                if isinstance(data.get(key), str | int))[:120]
            title = _FAILURE_TITLES.get(str(data.get("error_code")), _FAILURE_EVENTS[kind])
            failures.append({"at": when.isoformat(), "title": title,
                             "detail": detail or None})
    answered = outcomes["completed"]
    attempted = sum(count for outcome, count in outcomes.items() if outcome != "cancelled")
    servers = [
        {"server_id": str(item.get("server_id")), "state": str(item.get("state")),
         "reason_code": item.get("reason_code"),
         "available_tools": int(item.get("available_tools") or 0)}
        for item in (mcp_status or {}).get("servers", ())
        if isinstance(item, dict)
    ]
    return {
        "window_hours": int(WINDOW.total_seconds() // 3_600),
        "nvidia": {
            "credential": credential or "unavailable",
            "calls": attempted,
            "answered": answered,
            "rate_limited": outcomes["rate_limited"],
            "failed": attempted - answered - outcomes["rate_limited"],
            "median_latency_ms": int(median(latencies)) if latencies else None,
            "last_success_at": last_success.isoformat() if last_success else None,
            "last_problem_at": last_problem.isoformat() if last_problem else None,
            "cooldown_seconds": round(max(0.0, cooldown_seconds), 1),
        },
        "mcp": servers,
        "failures": sorted(failures, key=lambda item: item["at"], reverse=True)[:MAX_FAILURES],
    }


class HealthIpcService:
    METHOD = "health.status"

    def __init__(self, audit_path: Path, *, mcp_status: Callable[[], dict[str, Any]] | None,
                 credential: Callable[[], Any], cooldown: Callable[[], float] = lambda: 0.0,
                 fallback: Callable[[], dict[str, str] | None] = lambda: None,
                 memory_status: Callable[[], Any] | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._audit_path = audit_path
        self._mcp_status = mcp_status
        self._credential = credential
        self._cooldown = cooldown
        self._clock = clock
        self._fallback = fallback
        self._memory_status = memory_status

    def handlers(self) -> dict[str, Any]:
        return {self.METHOD: self.handle}

    async def handle(self, request: Any) -> Any:
        from aegis_core.ipc.server import IpcHandlerResult

        if request.method != self.METHOD:
            return IpcHandlerResult(ok=False, error_code="method_not_found")
        if request.payload:
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        events = await asyncio.to_thread(_tail_events, self._audit_path)
        try:
            credential = await self._credential()
        except Exception as error:
            record_boundary_error(error)
            credential = None
        try:
            mcp = self._mcp_status() if self._mcp_status is not None else None
        except Exception as error:
            record_boundary_error(error)
            mcp = None
        try:
            cooldown = float(self._cooldown())
        except Exception as error:
            record_boundary_error(error)
            cooldown = 0.0
        report = health_report(
            events, now=self._clock(), mcp_status=mcp,
            credential=(credential or {}).get("credential") if isinstance(credential, dict)
            else None, cooldown_seconds=cooldown)
        try:
            report["nvidia"]["last_fallback"] = self._fallback()
        except Exception as error:
            record_boundary_error(error)
            report["nvidia"]["last_fallback"] = None
        if self._memory_status is not None:
            try:
                report["memory"] = await self._memory_status()
            except Exception as error:
                record_boundary_error(error)
                report["memory"] = {"state": "degraded"}
        return IpcHandlerResult(ok=True, payload=report)
