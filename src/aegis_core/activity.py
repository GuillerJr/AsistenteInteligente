from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pydantic import BaseModel, ConfigDict, Field

from aegis_core.contracts import AgentRole
from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler


class ActiveAgent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: AgentRole
    active_jobs: int = Field(ge=1, le=128)


class SwarmActivitySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agents: tuple[ActiveAgent, ...] = Field(max_length=len(AgentRole))


class SwarmActivityTracker:
    def __init__(self) -> None:
        self._counts: dict[AgentRole, int] = {}
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def track(self, role: AgentRole) -> AsyncIterator[None]:
        async with self._lock:
            count = self._counts.get(role, 0) + 1
            if count > 128:
                raise RuntimeError("agent activity capacity reached")
            self._counts[role] = count
        try:
            yield
        finally:
            async with self._lock:
                remaining = self._counts[role] - 1
                if remaining:
                    self._counts[role] = remaining
                else:
                    del self._counts[role]

    async def snapshot(self) -> SwarmActivitySnapshot:
        async with self._lock:
            agents = tuple(
                ActiveAgent(role=role, active_jobs=count)
                for role, count in sorted(self._counts.items(), key=lambda item: item[0].value)
            )
        return SwarmActivitySnapshot(agents=agents)


class SwarmActivityIpcService:
    METHOD = "swarm.activity"

    def __init__(self, tracker: SwarmActivityTracker) -> None:
        self._tracker = tracker

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {self.METHOD: self.handle}

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        if request.method != self.METHOD:
            return IpcHandlerResult(ok=False, error_code="method_not_found")
        if request.payload:
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        snapshot = await self._tracker.snapshot()
        return IpcHandlerResult(ok=True, payload=snapshot.model_dump(mode="json"))
