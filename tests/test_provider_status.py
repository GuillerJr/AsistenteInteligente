from __future__ import annotations

import pytest

from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.provider_status import ProviderStatusIpcService

AUTHENTICATOR = IpcAuthenticator(bytes.fromhex("31" * 32))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configured", "expected"),
    [(True, "configured"), (False, "missing")],
)
async def test_provider_status_exposes_only_configuration_state(
    configured: bool,
    expected: str,
) -> None:
    service = ProviderStatusIpcService(lambda: configured)

    result = await service.handle(AUTHENTICATOR.create_request("provider.status"))

    assert result.ok is True
    assert result.payload == {
        "provider": "nvidia_nim",
        "credential": expected,
    }


@pytest.mark.asyncio
async def test_provider_status_hides_probe_failures() -> None:
    def fail() -> bool:
        raise RuntimeError("private keychain detail")

    result = await ProviderStatusIpcService(fail).handle(
        AUTHENTICATOR.create_request("provider.status")
    )

    assert result.ok is True
    assert result.payload == {
        "provider": "nvidia_nim",
        "credential": "unavailable",
    }
    assert "private" not in repr(result)


@pytest.mark.asyncio
async def test_provider_status_reports_local_brain_without_private_details() -> None:
    service = ProviderStatusIpcService(lambda: False, local_model_probe=lambda: True)

    result = await service.handle(AUTHENTICATOR.create_request("provider.status"))

    assert result.ok is True
    assert result.payload == {
        "provider": "nvidia_nim",
        "credential": "missing",
        "local_model": "available",
    }


@pytest.mark.asyncio
async def test_provider_status_rejects_payload_and_unknown_method() -> None:
    service = ProviderStatusIpcService(lambda: True)

    payload = await service.handle(
        AUTHENTICATOR.create_request("provider.status", {"reveal": True})
    )
    method = await service.handle(AUTHENTICATOR.create_request("provider.reset"))

    assert payload.ok is False
    assert payload.error_code == "invalid_payload"
    assert method.ok is False
    assert method.error_code == "method_not_found"
