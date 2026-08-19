import asyncio

import pytest

from aegis_core.activity import SwarmActivityIpcService, SwarmActivityTracker
from aegis_core.contracts import AgentRole
from aegis_core.ipc.protocol import IpcAuthenticator

AUTHENTICATOR = IpcAuthenticator(bytes.fromhex("66" * 32))


@pytest.mark.asyncio
async def test_activity_tracker_counts_roles_without_request_metadata() -> None:
    tracker = SwarmActivityTracker()

    async with tracker.track(AgentRole.CODE_SECURITY):
        async with tracker.track(AgentRole.CODE_SECURITY):
            snapshot = await tracker.snapshot()
            assert snapshot.model_dump(mode="json") == {
                "agents": [{"role": "code_security", "active_jobs": 2}]
            }
        assert (await tracker.snapshot()).agents[0].active_jobs == 1

    assert (await tracker.snapshot()).agents == ()


@pytest.mark.asyncio
async def test_activity_tracker_cleans_up_cancelled_work() -> None:
    tracker = SwarmActivityTracker()
    entered = asyncio.Event()

    async def work() -> None:
        async with tracker.track(AgentRole.VISION):
            entered.set()
            await asyncio.Future()

    task = asyncio.create_task(work())
    await entered.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert (await tracker.snapshot()).agents == ()


@pytest.mark.asyncio
async def test_activity_ipc_exposes_only_aggregate_roles() -> None:
    tracker = SwarmActivityTracker()
    service = SwarmActivityIpcService(tracker)

    async with tracker.track(AgentRole.ROUTER):
        result = await service.handle(AUTHENTICATOR.create_request("swarm.activity"))

    assert result.ok is True
    assert result.payload == {"agents": [{"role": "router", "active_jobs": 1}]}
    serialized = result.model_dump_json()
    assert "request_id" not in serialized
    assert "job_id" not in serialized


@pytest.mark.asyncio
async def test_activity_ipc_rejects_payloads_and_other_methods() -> None:
    service = SwarmActivityIpcService(SwarmActivityTracker())

    payload = await service.handle(
        AUTHENTICATOR.create_request("swarm.activity", {"request_id": "forbidden"})
    )
    method = await service.handle(AUTHENTICATOR.create_request("swarm.activity.reset"))

    assert payload.error_code == "invalid_payload"
    assert method.error_code == "method_not_found"
