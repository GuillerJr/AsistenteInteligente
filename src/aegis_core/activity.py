from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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


class SwarmActivityWaitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    after_version: int = Field(ge=0, le=9_007_199_254_740_991)
    timeout_milliseconds: int = Field(ge=100, le=20_000)


class SwarmActivityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=0, le=9_007_199_254_740_991)
    changed: bool
    agents: tuple[ActiveAgent, ...] = Field(max_length=len(AgentRole))


class SwarmActivityTracker:
    def __init__(self) -> None:
        self._counts: dict[AgentRole, int] = {}
        self._condition = asyncio.Condition()
        self._version = 0

    @asynccontextmanager
    async def track(self, role: AgentRole) -> AsyncIterator[None]:
        async with self._condition:
            count = self._counts.get(role, 0) + 1
            if count > 128:
                raise RuntimeError("agent activity capacity reached")
            self._counts[role] = count
            self._publish()
        try:
            yield
        finally:
            async with self._condition:
                remaining = self._counts[role] - 1
                if remaining:
                    self._counts[role] = remaining
                else:
                    del self._counts[role]
                self._publish()

    async def snapshot(self) -> SwarmActivitySnapshot:
        async with self._condition:
            agents = self._agents()
        return SwarmActivitySnapshot(agents=agents)

    async def wait_for_change(
        self,
        after_version: int,
        timeout_seconds: float,
    ) -> SwarmActivityUpdate:
        async with self._condition:
            if after_version == self._version:
                try:
                    await asyncio.wait_for(
                        self._condition.wait_for(lambda: self._version != after_version),
                        timeout=timeout_seconds,
                    )
                except TimeoutError:
                    pass
            return SwarmActivityUpdate(
                version=self._version,
                changed=self._version != after_version,
                agents=self._agents(),
            )

    def _publish(self) -> None:
        self._version += 1
        self._condition.notify_all()

    def _agents(self) -> tuple[ActiveAgent, ...]:
        return tuple(
            ActiveAgent(role=role, active_jobs=count)
            for role, count in sorted(self._counts.items(), key=lambda item: item[0].value)
        )


class SwarmActivityIpcService:
    METHOD = "swarm.activity"
    WAIT_METHOD = "swarm.wait"
    MAX_WAIT_SECONDS = 20

    def __init__(self, tracker: SwarmActivityTracker) -> None:
        self._tracker = tracker

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {
            self.METHOD: self.handle,
            self.WAIT_METHOD: self.handle,
        }

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        if request.method == self.METHOD:
            if request.payload:
                return IpcHandlerResult(ok=False, error_code="invalid_payload")
            snapshot = await self._tracker.snapshot()
            return IpcHandlerResult(ok=True, payload=snapshot.model_dump(mode="json"))
        if request.method == self.WAIT_METHOD:
            try:
                wait = SwarmActivityWaitRequest.model_validate(request.payload)
            except ValidationError:
                return IpcHandlerResult(ok=False, error_code="invalid_payload")
            update = await self._tracker.wait_for_change(
                wait.after_version,
                wait.timeout_milliseconds / 1_000,
            )
            return IpcHandlerResult(ok=True, payload=update.model_dump(mode="json"))
        return IpcHandlerResult(ok=False, error_code="method_not_found")
