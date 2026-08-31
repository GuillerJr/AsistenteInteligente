from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.memory.sqlite import MemoryStorageMetrics
from aegis_core.performance_profiler import (
    PerformanceAnalyticsStore,
    PerformanceIpcService,
    PerformanceProfiler,
    SpeculativeTransactionMetric,
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
        namespace_node_embeddings=13,
        namespace_node_embedding_capacity=2_000,
        graph_index_bytes=13 * 384 * 4,
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
    assert sample.namespace_node_embedding_capacity == 2_000
    assert sample.graph_index_bytes == 13 * 384 * 4
    assert "prompt" not in encoded
    assert "transcript" not in encoded
    assert "url" not in encoded
    json.loads(encoded)


@pytest.mark.asyncio
async def test_performance_ipc_exposes_graph_snapshot(
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
    assert snapshot.ok is True
    assert snapshot.payload["memory_items"] == 42
    assert snapshot.payload["thermal_state"] == "fair"
    assert snapshot.payload["namespace_node_embeddings"] == 13
    assert snapshot.payload["graph_index_bytes"] == 13 * 384 * 4


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
        AUTHENTICATOR.create_request(service.SNAPSHOT_METHOD, {"unexpected": True})
    )
    unknown = await service.handle(
        AUTHENTICATOR.create_request("performance.delete")
    )

    assert invalid.ok is False
    assert invalid.error_code == "invalid_payload"
    assert unknown.ok is False
    assert unknown.error_code == "method_not_found"


def test_store_persists_bounded_content_free_speculative_metrics(tmp_path: Path) -> None:
    store = PerformanceAnalyticsStore(tmp_path / "performance.sqlite3")
    store.initialize()
    metric = SpeculativeTransactionMetric(
        transaction_id=uuid4(),
        recorded_at=datetime.now(UTC),
        route="verified_remote",
        verifier_model_id="nvidia/nemotron-3.5-lightning-30b-a3b",
        active_first_partial_ms=80,
        active_total_ms=310,
        wall_time_ms=310,
        local_draft_ms=80,
        verifier_ms=230,
        accepted_draft_tokens=12,
        local_fallback=False,
        thermal_throttled=False,
    )

    store.record_speculative_transaction(metric)
    loaded = store.load_recent_speculative(limit=1)

    assert loaded == (metric,)
    assert "prompt" not in repr(loaded[0])
    assert "transcript" not in repr(loaded[0])
