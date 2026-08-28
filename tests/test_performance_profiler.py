from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.memory.sqlite import MemoryStorageMetrics
from aegis_core.performance_profiler import (
    PerformanceAnalyticsStore,
    PerformanceIpcService,
    PerformanceProfiler,
)
from aegis_core.runtime_state import RuntimePowerSnapshot, RuntimePowerState
from aegis_core.tools.audit import NullAuditSink

AUTHENTICATOR = IpcAuthenticator(bytes.fromhex("43" * 32))


def memory_metrics() -> MemoryStorageMetrics:
    return MemoryStorageMetrics(
        memory_items=42,
        memory_capacity=50_000,
        namespace_memory_items=21,
        namespace_memory_capacity=2_000,
        namespace_vectors=13,
        namespace_vector_capacity=2_000,
        sqlite_vec_loaded=True,
    )


def thermal_metrics() -> RuntimePowerSnapshot:
    return RuntimePowerSnapshot(
        state=RuntimePowerState.ACTIVE,
        cause="thermal_recovery",
        thermal_state="fair",
        low_power_mode=False,
        source_id=uuid4(),
        sequence=3,
        changed=False,
    )


@pytest.mark.asyncio
async def test_profiler_persists_only_allow_listed_operational_metrics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PerformanceAnalyticsStore(tmp_path / "performance.sqlite3")
    store.initialize()
    profiler = PerformanceProfiler(
        store,
        memory_probe=memory_metrics,
        thermal_probe=thermal_metrics,
    )
    monkeypatch.setattr(profiler, "_afm_loopback_process", lambda: (321, 4_096))

    sample = await profiler.collect()
    loaded = store.load_recent(limit=1)
    encoded = sample.model_dump_json()

    assert loaded == (sample,)
    assert sample.thermal_state == "fair"
    assert sample.afm_loopback_available is True
    assert sample.memory_capacity == 50_000
    assert sample.namespace_vector_capacity == 2_000
    assert "prompt" not in encoded
    assert "transcript" not in encoded
    assert "url" not in encoded
    json.loads(encoded)


@pytest.mark.asyncio
async def test_performance_ipc_exposes_snapshot_and_bounded_sqlite_vec_soak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PerformanceAnalyticsStore(tmp_path / "performance.sqlite3")
    store.initialize()
    profiler = PerformanceProfiler(
        store,
        memory_probe=memory_metrics,
        thermal_probe=thermal_metrics,
    )
    monkeypatch.setattr(profiler, "_afm_loopback_process", lambda: (None, None))
    service = PerformanceIpcService(profiler, NullAuditSink())

    snapshot = await service.handle(
        AUTHENTICATOR.create_request(service.SNAPSHOT_METHOD)
    )
    soak = await service.handle(
        AUTHENTICATOR.create_request(
            service.SQLITE_VEC_SOAK_METHOD,
            {"cycles": 2, "budget_bytes": 8 * 1_024 * 1_024},
        )
    )

    assert snapshot.ok is True
    assert snapshot.payload["memory_items"] == 42
    assert snapshot.payload["thermal_state"] == "fair"
    assert soak.ok is True
    assert soak.payload["report"]["cycles"] == 2
    assert soak.payload["report"]["budget_bytes"] == 8 * 1_024 * 1_024
    assert soak.payload["sample"]["soak_cycles"] == 2


@pytest.mark.asyncio
async def test_performance_ipc_rejects_unbounded_or_unknown_requests(
    tmp_path: Path,
) -> None:
    store = PerformanceAnalyticsStore(tmp_path / "performance.sqlite3")
    store.initialize()
    service = PerformanceIpcService(
        PerformanceProfiler(
            store,
            memory_probe=memory_metrics,
            thermal_probe=thermal_metrics,
        ),
        NullAuditSink(),
    )

    invalid = await service.handle(
        AUTHENTICATOR.create_request(
            service.SQLITE_VEC_SOAK_METHOD,
            {"cycles": 1_001},
        )
    )
    unknown = await service.handle(
        AUTHENTICATOR.create_request("performance.delete")
    )

    assert invalid.ok is False
    assert invalid.error_code == "performance_probe_failed"
    assert unknown.ok is False
    assert unknown.error_code == "method_not_found"


@pytest.mark.asyncio
async def test_sqlite_vec_soak_stops_during_thermal_pressure(tmp_path: Path) -> None:
    store = PerformanceAnalyticsStore(tmp_path / "performance.sqlite3")
    store.initialize()

    def thermal_pressure() -> RuntimePowerSnapshot:
        return RuntimePowerSnapshot(
            state=RuntimePowerState.SUSPENDED,
            cause="thermal_pause",
            thermal_state="serious",
            low_power_mode=False,
            source_id=uuid4(),
            sequence=4,
            changed=True,
        )

    service = PerformanceIpcService(
        PerformanceProfiler(
            store,
            memory_probe=memory_metrics,
            thermal_probe=thermal_pressure,
        ),
        NullAuditSink(),
    )

    result = await service.handle(
        AUTHENTICATOR.create_request(
            service.SQLITE_VEC_SOAK_METHOD,
            {"cycles": 2},
        )
    )

    assert result.ok is False
    assert result.error_code == "performance_probe_failed"
