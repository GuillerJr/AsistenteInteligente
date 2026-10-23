import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from aegis_core.health import HealthIpcService, health_report

NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


def event(kind, minutes_ago, **data):
    return {"timestamp": (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z"),
            "event_type": kind, "data": data}


def test_report_counts_nvidia_outcomes_and_lists_recent_failures():
    events = [
        event("nvidia_inference", 5, outcome="completed", latency_ms=900),
        event("nvidia_inference", 4, outcome="completed", latency_ms=1_500),
        event("nvidia_inference", 3, outcome="rate_limited"),
        event("nvidia_inference", 2, outcome="failed"),
        event("nvidia_inference", 1, outcome="cancelled"),
        event("nvidia_inference", 60 * 30, outcome="failed"),  # Outside the 24 h window.
        event("mcp_server_quarantined", 10, server_id="google-workspace",
              reason_code="startup_or_admission_failed", contains_user_content=False),
        event("noise_floor_transition", 1),
    ]
    report = health_report(events, now=NOW, credential="configured", cooldown_seconds=4.2,
                           mcp_status={"servers": [{"server_id": "google-workspace",
                                                    "state": "quarantined",
                                                    "reason_code": "restart_failed",
                                                    "available_tools": 0}]})
    nvidia = report["nvidia"]
    assert (nvidia["calls"], nvidia["answered"], nvidia["rate_limited"], nvidia["failed"]) == (
        4, 2, 1, 1)
    assert nvidia["median_latency_ms"] == 1_200 and nvidia["cooldown_seconds"] == 4.2
    assert nvidia["last_problem_at"].startswith("2026-10-21T11:58")
    assert report["mcp"][0]["state"] == "quarantined"
    assert report["failures"] == [{
        "at": (NOW - timedelta(minutes=10)).isoformat(),
        "title": "Una integración quedó en cuarentena",
        "detail": "startup_or_admission_failed · google-workspace"}]


@pytest.mark.asyncio
async def test_ipc_reads_the_audit_tail_and_rejects_payloads(tmp_path):
    log = tmp_path / "audit.jsonl"
    log.write_text("cut line\n" + "\n".join(json.dumps(e) for e in [
        event("nvidia_inference", 1, outcome="completed", latency_ms=700)]) + "\n")

    async def credential():
        return {"provider": "nvidia_nim", "credential": "configured"}

    service = HealthIpcService(log, mcp_status=lambda: {"servers": []}, credential=credential,
                               cooldown=lambda: 0.0, clock=lambda: NOW)
    result = await service.handle(SimpleNamespace(method="health.status", payload={}))
    assert result.ok and result.payload["nvidia"]["answered"] == 1
    assert result.payload["nvidia"]["credential"] == "configured"
    rejected = await service.handle(SimpleNamespace(method="health.status", payload={"x": 1}))
    assert not rejected.ok


def test_incomplete_response_is_explained_in_spanish_with_code_as_detail():
    report = health_report(
        [event("swarm_execution_failed", 1, error_code="incomplete_model_response")],
        now=NOW, mcp_status=None, credential="configured", cooldown_seconds=0,
    )
    assert report["failures"][0]["title"] == "El proveedor de IA devolvió una respuesta incompleta"
    assert report["failures"][0]["detail"] == "incomplete_model_response"


def test_codex_health_counts_its_own_calls_and_omits_the_retired_provider():
    report = health_report([
        event("nvidia_inference", 1, outcome="failed"),
        event("provider_inference", 1, provider="codex", outcome="completed", latency_ms=700),
        event("provider_inference", 1, provider="other", outcome="failed"),
    ], now=NOW, mcp_status=None, credential="configured", cooldown_seconds=0, provider="codex")
    assert "nvidia" not in report
    assert report["inference"]["provider"] == "codex"
    assert report["inference"]["state"] == "ready"
    assert report["inference"]["answered"] == 1
    assert report["inference"]["failed"] == 0


@pytest.mark.asyncio
async def test_health_exposes_runtime_fallback_and_memory_readiness(tmp_path):
    async def credential():
        return {"credential": "configured"}

    async def memory():
        return {"state": "indexing", "pending": 3}

    fallback = {"role": "router", "requested_model_id": "test/selected",
                "actual_model_id": "test/backup", "reason": "timeout"}
    service = HealthIpcService(
        tmp_path / "absent.jsonl", mcp_status=None, credential=credential,
        fallback=lambda: fallback, memory_status=memory, clock=lambda: NOW,
    )
    result = await service.handle(SimpleNamespace(method="health.status", payload={}))
    assert result.ok
    assert result.payload["nvidia"]["last_fallback"] == fallback
    assert result.payload["memory"] == {"state": "indexing", "pending": 3}


@pytest.mark.asyncio
async def test_failed_memory_probe_does_not_make_health_unavailable(tmp_path):
    async def credential():
        return {"credential": "configured"}

    async def memory():
        raise RuntimeError("probe failed")

    service = HealthIpcService(
        tmp_path / "absent.jsonl", mcp_status=None, credential=credential,
        memory_status=memory, clock=lambda: NOW,
    )
    result = await service.handle(SimpleNamespace(method="health.status", payload={}))
    assert result.ok
    assert result.payload["memory"] == {"state": "degraded"}
