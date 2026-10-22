"""Exercise unattended ownership against real jobs and controlled failing adapters."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_jobs import BlockingGraph, _awaiting_confirmation
from test_standing_grants import TOOL, _jobs

from aegis_core.contracts import Capability, UserRequest
from aegis_core.job_contracts import JobStatus
from aegis_core.jobs import SwarmJobManager
from aegis_core.scheduling import run_unattended


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", [False, True])
async def test_last_fifty_milliseconds_respect_real_job_poll_contract(tmp_path, pending):
    jobs = (_jobs(tmp_path, {TOOL: Capability.MAIL_READ})[0] if pending
            else SwarmJobManager(BlockingGraph()))
    now = [datetime.now(UTC)]
    submitted = []

    class ClockedJobs:
        async def submit(self, request, **kwargs):
            snapshot = await jobs.submit(request, **kwargs)
            submitted.append(snapshot.job_id)
            if pending:
                snapshot = await _awaiting_confirmation(jobs, snapshot.job_id)
                now[0] = snapshot.confirmation.expires_at - timedelta(milliseconds=50)
            return snapshot

        async def wait_for_change(self, *args, **kwargs):
            snapshot = await jobs.wait_for_change(*args, **kwargs)
            now[0] += timedelta(seconds=kwargs["timeout_seconds"])
            return snapshot

        async def cancel(self, job_id):
            return await jobs.cancel(job_id)

    async def notify(*_):
        pass

    try:
        _, status = await asyncio.wait_for(run_unattended(
            ClockedJobs(), UserRequest(text="Escanea loopback"), conversation_id=None,
            title="Prueba sintética", clock=lambda: now[0], timeout_seconds=0.05,
            on_approval=notify,
        ), timeout=2)
        assert status == ("approval_expired" if pending else "timeout")
        assert len(submitted) == 1
        assert (await jobs.status(submitted[0])).status is JobStatus.CANCELLED
        assert not jobs.has_active_jobs and jobs.pending_approvals() == ()
    finally:
        await jobs.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["submitted", "notification", "poll"])
async def test_observer_failure_settles_the_admitted_job_before_propagating(stage, caplog):
    now = datetime.now(UTC)
    job_id = uuid4()
    cancelled = []
    cancel_entered, release_cancel = asyncio.Event(), asyncio.Event()
    snapshot = SimpleNamespace(
        job_id=job_id, status=JobStatus.AWAITING_CONFIRMATION,
        result=None, error_code=None, stream_version=0,
        confirmation=SimpleNamespace(call_digest="f" * 64,
                                     expires_at=now + timedelta(minutes=2)),
    )

    class FailingObserverJobs:
        async def submit(self, *_args, **_kwargs):
            return snapshot

        async def wait_for_change(self, *_args, **_kwargs):
            raise OSError("synthetic private observer data")

        async def cancel(self, identity):
            cancel_entered.set()
            await release_cancel.wait()
            cancelled.append(identity)

    def submitted(_):
        if stage == "submitted":
            raise OSError("synthetic private observer data")

    async def notify(*_):
        if stage == "notification":
            raise OSError("synthetic private observer data")

    run = asyncio.create_task(run_unattended(
        FailingObserverJobs(), UserRequest(text="Prueba"), conversation_id=None,
        title="Prueba sintética", clock=lambda: now, on_submitted=submitted, on_approval=notify,
    ))
    cancellation_started = asyncio.create_task(cancel_entered.wait())
    try:
        await asyncio.wait(
            (run, cancellation_started),
            timeout=1, return_when=asyncio.FIRST_COMPLETED,
        )
        assert cancel_entered.is_set() and not run.done()
        release_cancel.set()
        with pytest.raises(OSError, match="synthetic private observer data"):
            await run
        assert cancelled == [job_id]
        assert "synthetic private observer data" not in caplog.text
    finally:
        release_cancel.set()
        if not run.done():
            run.cancel()
        cancellation_started.cancel()
        await asyncio.gather(run, cancellation_started, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_during_admission_still_drains_when_identity_callback_fails(caplog):
    admitted, release = asyncio.Event(), asyncio.Event()
    job_id = uuid4()
    cancelled = []

    class SlowAdmissionJobs:
        async def submit(self, *_args, **_kwargs):
            admitted.set()
            await release.wait()
            return SimpleNamespace(job_id=job_id, status=JobStatus.RUNNING)

        async def cancel(self, identity):
            cancelled.append(identity)

    def submitted(_):
        raise OSError("synthetic private observer data")

    run = asyncio.create_task(run_unattended(
        SlowAdmissionJobs(), UserRequest(text="Prueba"), conversation_id=None,
        title="Prueba sintética", clock=lambda: datetime.now(UTC), on_submitted=submitted,
    ))
    try:
        await asyncio.wait_for(admitted.wait(), 1)
        run.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run, 1)
        assert cancelled == [job_id]
        assert "synthetic private observer data" not in caplog.text
    finally:
        release.set()
        if not run.done():
            run.cancel()
        await asyncio.gather(run, return_exceptions=True)
