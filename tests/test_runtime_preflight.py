from __future__ import annotations

from pathlib import Path

import pytest

from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.provider_status import ProviderStatusIpcService
from aegis_core.runtime_preflight import RuntimePreflightIpcService
from aegis_core.security import AuditIntegrityIpcService
from aegis_core.tools.audit import HashChainAuditLog

AUTHENTICATOR = IpcAuthenticator(bytes.fromhex("32" * 32))


@pytest.mark.asyncio
async def test_runtime_preflight_returns_one_bounded_snapshot(tmp_path: Path) -> None:
    service = RuntimePreflightIpcService(
        ProviderStatusIpcService(lambda: True, local_model_probe=lambda: True),
        AuditIntegrityIpcService(HashChainAuditLog(tmp_path / "audit.jsonl")),
    )

    result = await service.handle(AUTHENTICATOR.create_request("runtime.preflight"))

    assert result.ok is True
    assert result.payload == {
        "status": "ok",
        "provider": "nvidia_nim",
        "credential": "configured",
        "local_model": "available",
        "state": "intact",
        "runtime_power_state_known": True,
    }


@pytest.mark.asyncio
async def test_runtime_preflight_rejects_payload_and_other_method(tmp_path: Path) -> None:
    service = RuntimePreflightIpcService(
        ProviderStatusIpcService(lambda: True),
        AuditIntegrityIpcService(HashChainAuditLog(tmp_path / "audit.jsonl")),
    )

    payload = await service.handle(
        AUTHENTICATOR.create_request("runtime.preflight", {"detail": True})
    )
    method = await service.handle(AUTHENTICATOR.create_request("runtime.reset"))

    assert payload.error_code == "invalid_payload"
    assert method.error_code == "method_not_found"


@pytest.mark.asyncio
async def test_runtime_preflight_requests_native_power_resynchronization(
    tmp_path: Path,
) -> None:
    service = RuntimePreflightIpcService(
        ProviderStatusIpcService(lambda: False),
        AuditIntegrityIpcService(HashChainAuditLog(tmp_path / "audit.jsonl")),
        runtime_power_known=lambda: False,
    )

    result = await service.handle(AUTHENTICATOR.create_request("runtime.preflight"))

    assert result.ok is True
    assert result.payload["runtime_power_state_known"] is False
