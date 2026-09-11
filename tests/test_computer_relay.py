from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
from uuid import uuid4

import pytest

from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.tools.computer import ComputerAction, ComputerUseError
from aegis_core.tools.computer_relay import (
    ComputerCommandRelay,
    ComputerRelayCompletePayload,
    ComputerRelayIpcService,
    RelayedComputerBridge,
)

AUTHENTICATOR = IpcAuthenticator(bytes.fromhex("5a" * 32))


@pytest.mark.asyncio
async def test_expired_command_is_not_dispatched_before_timeout_callback_runs() -> None:
    relay = ComputerCommandRelay(asyncio.get_running_loop())
    request = asyncio.create_task(relay._request({"command": "act"}, timeout_seconds=1))
    try:
        await asyncio.sleep(0)
        assert relay._queued is not None
        relay._queued = replace(relay._queued, expires_at=0)
        assert await relay.next_command(0.01) is None
    finally:
        relay.close()
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.asyncio
async def test_expired_delivered_result_is_never_accepted() -> None:
    relay = ComputerCommandRelay(asyncio.get_running_loop())
    request = asyncio.create_task(relay._request({"command": "act"}, timeout_seconds=1))
    try:
        command = await relay.next_command(0.1)
        assert command is not None
        relay._delivered[command.command_id] = 0
        assert not relay.complete(command.command_id, {"status": "ok"})
    finally:
        relay.close()
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_response_rejects_non_json_numbers(value: float) -> None:
    with pytest.raises(ValueError, match="not JSON"):
        ComputerRelayCompletePayload(command_id=uuid4(), response={"value": value})


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_expired_or_cancelled_command_does_not_block_next_request(cancel: bool) -> None:
    relay = ComputerCommandRelay(asyncio.get_running_loop())
    expired = asyncio.create_task(relay._request({"command": "old"}, timeout_seconds=0.01))
    await asyncio.sleep(0)
    if cancel:
        expired.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else ComputerUseError):
        await expired
    fresh = asyncio.create_task(relay._request({"command": "new"}, timeout_seconds=1))
    try:
        await asyncio.sleep(0)
        command = await relay.next_command(0.1)
        assert command is not None and command.payload == {"command": "new"}
        assert relay.complete(command.command_id, {"status": "ok"})
        assert await fresh == {"status": "ok"}
    finally:
        relay.close()
        await asyncio.gather(fresh, return_exceptions=True)


@pytest.mark.asyncio
async def test_close_wakes_all_waiters_without_waiting_for_poll_timeout() -> None:
    relay = ComputerCommandRelay(asyncio.get_running_loop())
    waiters = [asyncio.create_task(relay.next_command(20)) for _ in range(3)]
    await asyncio.sleep(0)
    relay.close()
    assert await asyncio.wait_for(asyncio.gather(*waiters), timeout=0.2) == [None] * 3


@pytest.mark.asyncio
async def test_completion_requires_delivery_and_is_single_use() -> None:
    relay = ComputerCommandRelay(asyncio.get_running_loop())
    request = asyncio.create_task(relay._request({"command": "capture"}, timeout_seconds=1))
    try:
        await asyncio.sleep(0)
        command_id = next(iter(relay._pending))
        assert not relay.complete(command_id, {"status": "ok"})
        command = await relay.next_command(0.1)
        assert command is not None and command.command_id == command_id
        assert relay.complete(command_id, {"status": "ok"})
        assert not relay.complete(command_id, {"status": "ok"})
        await request
    finally:
        relay.close()
        await asyncio.gather(request, return_exceptions=True)


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
async def test_relay_preserves_observation_context_for_one_action() -> None:
    relay = ComputerCommandRelay(asyncio.get_running_loop())
    service = ComputerRelayIpcService(relay)
    bridge = RelayedComputerBridge(relay)
    context = "a" * 64
    action = asyncio.create_task(
        asyncio.to_thread(
            bridge.act,
            ComputerAction(action="key", key="left", modifiers=[]),
            "com.apple.Safari",
            context,
            123_456,
        )
    )
    await asyncio.sleep(0)
    pending = await service.handle(
        AUTHENTICATOR.create_request(
            service.WAIT_METHOD,
            {"timeout_milliseconds": 1_000},
        )
    )

    assert pending.payload["command"]["expected_visual_context"] == context
    assert pending.payload["command"]["expected_user_input_counter"] == 123_456
    completed = await service.handle(
        AUTHENTICATOR.create_request(
            service.COMPLETE_METHOD,
            {
                "command_id": pending.payload["command_id"],
                "response": {
                    "status": "ok",
                    "frontmost_bundle_identifier": "com.apple.Safari",
                    "display_identifier": 7,
                },
            },
        )
    )

    assert completed.ok is True
    await action
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
                    "visual_context": "a" * 64,
                    "visual_signature": "0" * 64,
                    "user_input_counter": 123_456,
                    "frontmost_bundle_identifier": "com.apple.Safari",
                    "local_perception": {
                        "windows": ["Documentación"],
                        "items": [],
                        "secure_content": False,
                        "truncated": False,
                    },
                },
            },
        )
    )

    observation = await capture
    assert observation.image.data_base64 == encoded
    assert observation.perception.windows == ("Documentación",)
    assert observation.visual_context == "a" * 64
    assert observation.visual_signature == "0" * 64
    assert observation.user_input_counter == 123_456
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


@pytest.mark.asyncio
async def test_relay_preserves_inactive_user_session_failure() -> None:
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

    completed = await service.handle(
        AUTHENTICATOR.create_request(
            service.COMPLETE_METHOD,
            {
                "command_id": pending.payload["command_id"],
                "response": {
                    "status": "error",
                    "reason": "user_session_inactive",
                },
            },
        )
    )

    assert completed.ok is True
    with pytest.raises(ComputerUseError, match="user_session_inactive"):
        await activation
    relay.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    ["capture_failed", "computer_observation_changed", "computer_user_takeover"],
)
async def test_relay_preserves_bounded_action_failure(reason: str) -> None:
    relay = ComputerCommandRelay(asyncio.get_running_loop())
    service = ComputerRelayIpcService(relay)
    bridge = RelayedComputerBridge(relay)
    action = asyncio.create_task(
        asyncio.to_thread(
            bridge.act,
            ComputerAction(action="key", key="left", modifiers=[]),
            "com.apple.Safari",
            "a" * 64,
            123_456,
        )
    )
    await asyncio.sleep(0)
    pending = await service.handle(
        AUTHENTICATOR.create_request(
            service.WAIT_METHOD,
            {"timeout_milliseconds": 1_000},
        )
    )

    completed = await service.handle(
        AUTHENTICATOR.create_request(
            service.COMPLETE_METHOD,
            {
                "command_id": pending.payload["command_id"],
                "response": {
                    "status": "error",
                    "reason": reason,
                },
            },
        )
    )

    assert completed.ok is True
    with pytest.raises(ComputerUseError, match=reason):
        await action
    relay.close()
