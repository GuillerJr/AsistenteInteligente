from __future__ import annotations

import asyncio
import base64

import pytest

from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.tools.computer_relay import (
    ComputerCommandRelay,
    ComputerRelayIpcService,
    RelayedComputerBridge,
)

AUTHENTICATOR = IpcAuthenticator(bytes.fromhex("5a" * 32))


@pytest.mark.asyncio
async def test_relay_round_trips_one_authenticated_helper_command() -> None:
    relay = ComputerCommandRelay(asyncio.get_running_loop())
    service = ComputerRelayIpcService(relay)
    bridge = RelayedComputerBridge(relay)

    activation = asyncio.create_task(asyncio.to_thread(bridge.activate, "com.apple.Safari"))
    await asyncio.sleep(0)
    pending = await service.handle(
        AUTHENTICATOR.create_request(
            service.WAIT_METHOD,
            {"timeout_milliseconds": 1_000},
        )
    )

    assert pending.ok is True
    assert pending.payload["available"] is True
    assert pending.payload["command"] == {
        "protocol_version": "1.0",
        "command": "activate",
        "bundle_identifier": "com.apple.Safari",
    }
    completed = await service.handle(
        AUTHENTICATOR.create_request(
            service.COMPLETE_METHOD,
            {
                "command_id": pending.payload["command_id"],
                "response": {
                    "status": "ok",
                    "frontmost_bundle_identifier": "com.apple.Safari",
                },
            },
        )
    )

    assert completed.ok is True
    assert completed.payload == {"accepted": True}
    await activation
    relay.close()


@pytest.mark.asyncio
async def test_relay_preserves_bounded_screenshot_without_persisting_it() -> None:
    relay = ComputerCommandRelay(asyncio.get_running_loop())
    service = ComputerRelayIpcService(relay)
    bridge = RelayedComputerBridge(relay)
    capture = asyncio.create_task(asyncio.to_thread(bridge.capture, "com.apple.Safari"))
    await asyncio.sleep(0)
    pending = await service.handle(
        AUTHENTICATOR.create_request(
            service.WAIT_METHOD,
            {"timeout_milliseconds": 1_000},
        )
    )
    encoded = base64.b64encode(b"\xff\xd8\xff\xd9").decode("ascii")

    await service.handle(
        AUTHENTICATOR.create_request(
            service.COMPLETE_METHOD,
            {
                "command_id": pending.payload["command_id"],
                "response": {
                    "status": "ok",
                    "media_type": "image/jpeg",
                    "data_base64": encoded,
                    "frontmost_bundle_identifier": "com.apple.Safari",
                },
            },
        )
    )

    image = await capture
    assert image.data_base64 == encoded
    relay.close()


@pytest.mark.asyncio
async def test_relay_wait_is_bounded_and_rejects_stale_completion() -> None:
    relay = ComputerCommandRelay(asyncio.get_running_loop())
    service = ComputerRelayIpcService(relay)
    empty = await service.handle(
        AUTHENTICATOR.create_request(
            service.WAIT_METHOD,
            {"timeout_milliseconds": 100},
        )
    )
    stale = await service.handle(
        AUTHENTICATOR.create_request(
            service.COMPLETE_METHOD,
            {
                "command_id": "b582754d-a3a1-4841-ae66-d58a88c507f4",
                "response": {"status": "error", "reason": "computer_helper_failed"},
            },
        )
    )

    assert empty.payload == {"available": False}
    assert stale.ok is False
    assert stale.error_code == "computer_command_unavailable"
    relay.close()
