from pathlib import Path

import pytest

from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.tools.audit import HashChainAuditLog
from aegis_core.tools.audit_service import SystemAuditIpcService

AUTHENTICATOR = IpcAuthenticator(bytes.fromhex("ab" * 32))


@pytest.mark.asyncio
async def test_system_audit_service_records_authenticated_acoustic_event(tmp_path: Path) -> None:
    audit = HashChainAuditLog(tmp_path / "private" / "audit.jsonl")
    service = SystemAuditIpcService(audit)
    request = AUTHENTICATOR.create_request(
        "audit.system.event",
        {
            "event_type": "voice_interruption",
            "component": "acoustic_sensor",
            "data": {
                "source": "wake_word",
                "job_present": True,
                "job_cancelled": True,
            },
        },
    )

    result = await service.handle(request)
    records = audit.verify()

    assert result.ok is True
    assert result.payload == {"recorded": True}
    assert records[0].request_id == request.request_id
    assert records[0].call_id == request.nonce
    assert records[0].event_type == "voice_interruption"


@pytest.mark.asyncio
async def test_system_audit_records_only_integer_noise_floor_metrics(tmp_path: Path) -> None:
    audit = HashChainAuditLog(tmp_path / "private" / "audit.jsonl")
    service = SystemAuditIpcService(audit)
    request = AUTHENTICATOR.create_request(
        "audit.system.event",
        {
            "event_type": "noise_floor_transition",
            "component": "acoustic_sensor",
            "data": {
                "rms_microunits": 12_400,
                "noise_floor_microunits": 4_100,
                "threshold_microunits": 10_250,
                "voice_active": True,
            },
        },
    )

    result = await service.handle(request)

    assert result.ok is True
    assert audit.verify()[0].data["voice_active"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {
            "event_type": "unknown",
            "component": "acoustic_sensor",
            "data": {},
        },
        {
            "event_type": "thermal_pause",
            "component": "other",
            "data": {},
        },
        {
            "event_type": "thermal_pause",
            "component": "acoustic_sensor",
            "data": {"temperature": 1.5},
        },
    ],
)
async def test_system_audit_service_rejects_unapproved_payloads(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    audit = HashChainAuditLog(tmp_path / "private" / "audit.jsonl")
    service = SystemAuditIpcService(audit)

    result = await service.handle(
        AUTHENTICATOR.create_request("audit.system.event", payload)
    )

    assert result.ok is False
    assert result.error_code == "invalid_payload"
    assert audit.verify() == ()
