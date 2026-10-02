"""Opt-in NVIDIA test using synthetic geometry only, never desktop/chat data."""
import asyncio
import os

import pytest
from test_mascot import AUTH, world

from aegis_core.brain.nvidia_client import NvidiaBrainClient
from aegis_core.config import Settings
from aegis_core.mascot import MascotAutonomyService
from aegis_core.providers.nvidia import NvidiaNimClient
from aegis_core.secrets import MacOSKeychain

pytestmark = pytest.mark.skipif(
    os.environ.get("AEGIS_RUN_NVIDIA_MASCOT_PROBE") != "1",
    reason="explicit opt-in required for NVIDIA synthetic mascot geometry",
)


@pytest.mark.asyncio
async def test_nvidia_generates_exploration_from_actual_room_geometry():
    settings = Settings()
    key = await asyncio.to_thread(MacOSKeychain(
        settings.nvidia_keychain_service, settings.nvidia_keychain_account,
    ).get)
    async with NvidiaNimClient(settings, lambda: key) as nim:
        service = MascotAutonomyService(NvidiaBrainClient(nim))
        snapshot = world()
        try:
            response = await service.handle(AUTH.create_request("mascot.plan.request", snapshot))
            assert response.payload["status"] == "pending"
            for _ in range(60):
                await asyncio.sleep(0.25)
                status = await service.handle(AUTH.create_request(
                    "mascot.plan.status", {"revision": snapshot["revision"]}))
                if status.payload["status"] != "pending":
                    break
            assert status.payload["status"] == "ready", status.payload["status"]
            assert status.payload["provider"] == "nvidia"
            targets = status.payload["plan"]["targets"]
            assert any(abs(p["x"] - 0.5) + abs(p["y"] - 0.5) > 0.1 for p in targets)
        finally:
            await service.close()
