from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class RuntimePowerState(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class RuntimeStateError(RuntimeError):
    """Raised when a native lifecycle transition cannot be accepted safely."""


class StaleRuntimeStateError(RuntimeStateError):
    pass


@dataclass(frozen=True, slots=True)
class RuntimePowerSnapshot:
    state: RuntimePowerState
    cause: str
    thermal_state: str
    low_power_mode: bool
    source_id: UUID
    sequence: int
    changed: bool
    power_source: str = "unknown"


class RuntimeSuspensionController:
    """Authoritative event-driven gate for non-essential daemon work."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._active = asyncio.Event()
        self._active.set()
        self._suspended = False
        self._source_id: UUID | None = None
        self._sequence = -1
        self._cause = "startup"
        self._thermal_state = "nominal"
        self._low_power_mode = False
        self._power_source = "unknown"

    @property
    def suspended(self) -> bool:
        return self._suspended

    async def wait_until_active(self) -> None:
        await self._active.wait()

    async def apply(
        self,
        *,
        source_id: UUID,
        sequence: int,
        cause: str,
        thermal_state: str,
        low_power_mode: bool,
        power_source: str = "unknown",
    ) -> RuntimePowerSnapshot:
        if sequence < 0:
            raise RuntimeStateError("runtime state sequence cannot be negative")
        should_suspend = thermal_state in {"serious", "critical", "unknown"} or low_power_mode
        if power_source not in {"ac", "battery", "ups", "unknown"}:
            raise RuntimeStateError("runtime power source is invalid")
        async with self._lock:
            if self._source_id == source_id and sequence <= self._sequence:
                raise StaleRuntimeStateError("runtime state sequence is stale")
            previous = self._suspended
            self._source_id = source_id
            self._sequence = sequence
            self._cause = cause
            self._thermal_state = thermal_state
            self._low_power_mode = low_power_mode
            self._power_source = power_source
            self._suspended = should_suspend
            if should_suspend:
                self._active.clear()
            else:
                self._active.set()
            return RuntimePowerSnapshot(
                state=(
                    RuntimePowerState.SUSPENDED
                    if should_suspend
                    else RuntimePowerState.ACTIVE
                ),
                cause=cause,
                thermal_state=thermal_state,
                low_power_mode=low_power_mode,
                source_id=source_id,
                sequence=sequence,
                changed=previous != should_suspend,
                power_source=power_source,
            )

    def snapshot(self) -> RuntimePowerSnapshot | None:
        source_id = self._source_id
        if source_id is None:
            return None
        return RuntimePowerSnapshot(
            state=(
                RuntimePowerState.SUSPENDED if self._suspended else RuntimePowerState.ACTIVE
            ),
            cause=self._cause,
            thermal_state=self._thermal_state,
            low_power_mode=self._low_power_mode,
            source_id=source_id,
            sequence=self._sequence,
            changed=False,
            power_source=self._power_source,
        )
