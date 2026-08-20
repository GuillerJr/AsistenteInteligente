import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from aegis_core.contracts import PolicyDecision, ToolAuthorization
from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.security import AuditIntegrityIpcService
from aegis_core.tools.audit import HashChainAuditLog

AUTHENTICATOR = IpcAuthenticator(bytes.fromhex("55" * 32))
REQUEST_ID = UUID("11111111-1111-4111-8111-111111111111")


def _authorization() -> ToolAuthorization:
    return ToolAuthorization(
        call_id="call-monitor",
        tool_name="system_describe_runtime",
        call_digest="a" * 64,
        decision=PolicyDecision.ALLOW,
        reason_code="policy_allowed",
    )


@pytest.mark.asyncio
async def test_security_monitor_reports_intact_without_exposing_audit_content(
    tmp_path: Path,
) -> None:
    audit = HashChainAuditLog(tmp_path / "audit.jsonl")
    audit.record_authorization(REQUEST_ID, _authorization())
    service = AuditIntegrityIpcService(audit)
    request = AUTHENTICATOR.create_request("security.status")

    result = await service.handle(request)

    assert result.ok is True
    assert result.payload == {"state": "intact"}


@pytest.mark.asyncio
async def test_security_monitor_reports_tampering_as_aggregate_state(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    audit = HashChainAuditLog(
        path,
        clock=lambda: datetime(2026, 8, 19, 12, 0, tzinfo=UTC),
    )
    audit.record_authorization(REQUEST_ID, _authorization())
    record = json.loads(path.read_text(encoding="utf-8"))
    record["data"]["decision"] = "deny"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    service = AuditIntegrityIpcService(audit)

    result = await service.handle(AUTHENTICATOR.create_request("security.status"))

    assert result.ok is True
    assert result.payload == {"state": "compromised"}
    assert "decision" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_security_monitor_reports_oversized_audit_as_compromised(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    initial = HashChainAuditLog(path)
    initial.record_authorization(REQUEST_ID, _authorization())
    service = AuditIntegrityIpcService(HashChainAuditLog(path, max_bytes=path.stat().st_size - 1))

    result = await service.handle(AUTHENTICATOR.create_request("security.status"))

    assert result.ok is True
    assert result.payload == {"state": "compromised"}


@pytest.mark.asyncio
async def test_security_monitor_rejects_payloads_and_other_methods(tmp_path: Path) -> None:
    service = AuditIntegrityIpcService(HashChainAuditLog(tmp_path / "audit.jsonl"))

    payload = await service.handle(
        AUTHENTICATOR.create_request("security.status", {"path": "/tmp/other"})
    )
    method = await service.handle(AUTHENTICATOR.create_request("security.reset"))

    assert payload.error_code == "invalid_payload"
    assert method.error_code == "method_not_found"
