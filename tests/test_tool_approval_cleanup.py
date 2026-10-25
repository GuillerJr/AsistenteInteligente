"""Request cleanup must preserve unrelated scopes and revoke unacknowledged grants.

Synthetic owner decisions and event gates exercise actual authority requests;
there are no model calls, app data, real approvals or external effects.
"""

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest

from jarvis_core.runtime import ApprovalRequest
from jarvis_core.tools import (
    ApprovalAuthority,
    ToolError,
    canonical_arguments,
    check_tool_authority,
    operation_digest,
)

ARGS = {"marker": "synthetic"}


def operation(*, session="synthetic_session", run="synthetic_run", identity=None):
    value = ApprovalRequest(run, session, identity or uuid4().hex, "test_write",
                            canonical_arguments(ARGS), "")
    return replace(value, digest=operation_digest(value))


async def ticket(owner, request):
    async with asyncio.timeout(2):
        while True:
            entries = [view for view in owner.list_pending()
                       if (view.session_id, view.run_id, view.operation_id)
                       == (request.session_id, request.run_id, request.operation_id)]
            if entries:
                assert len(entries) == 1
                return entries[0]
            await asyncio.sleep(0)


@pytest.mark.parametrize("different", ["session", "run"])
@pytest.mark.parametrize("finish_first", ["cancel_event", "cancel_task", "deny"])
async def test_cleanup_with_same_operation_id_never_erases_other_scope(different, finish_first):
    owner = ApprovalAuthority()
    identity = uuid4().hex
    first = operation(identity=identity)
    other = operation(identity=identity,
                      session="other_session" if different == "session" else first.session_id,
                      run="other_run" if different == "run" else first.run_id)
    cancel_first = asyncio.Event()
    first_task = asyncio.create_task(owner.request(first, cancel_first))
    other_task = None
    try:
        first_ticket = await ticket(owner, first)
        other_task = asyncio.create_task(owner.request(other, asyncio.Event()))
        other_ticket = await ticket(owner, other)
        assert owner.decide(other_ticket.approval_id, True)
        other_grant = await other_task
        assert other_grant is not None
        if finish_first == "cancel_event":
            cancel_first.set()
            assert await first_task is None
        elif finish_first == "cancel_task":
            first_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first_task
        else:
            assert owner.decide(first_ticket.approval_id, False)
            assert await first_task is None
        # Different run/session is a legal separate request, even with the same opID.
        with owner.execution_scope(other, other_grant):
            check_tool_authority("test_write", ARGS, commit=True)
    finally:
        await owner.close()
        await asyncio.gather(*(task for task in (first_task, other_task) if task is not None),
                             return_exceptions=True)


class DelayedCancellation(asyncio.Event):
    """Gate only the async waiter's cleanup, after its future was cancelled."""

    def __init__(self):
        super().__init__()
        self.cleanup_entered = asyncio.Event()
        self.cleanup_release = asyncio.Event()

    async def wait(self):
        try:
            return await super().wait()
        finally:
            self.cleanup_entered.set()
            await self.cleanup_release.wait()


async def test_cancel_during_tool_grant_handoff_revokes_unacknowledged_capability():
    owner = ApprovalAuthority(max_pending=1)
    request = operation()
    cancellation = DelayedCancellation()
    task = asyncio.create_task(owner.request(request, cancellation))
    try:
        view = await ticket(owner, request)
        await asyncio.sleep(0)  # Start cancellation waiter before deciding.
        assert owner.decide(view.approval_id, True)
        await asyncio.wait_for(cancellation.cleanup_entered.wait(), 2)
        task.cancel()  # Grant exists internally, but request cannot acknowledge it.
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancellation.is_set()
        assert owner.list_pending() == ()
        # Even a mutable legacy event cannot revive the removed capability or opID.
        cancellation.clear()
        with pytest.raises(ToolError, match=r"^approval_operation_replayed$"):
            await owner.request(request, asyncio.Event())
        retry = operation()
        retry_task = asyncio.create_task(owner.request(retry, asyncio.Event()))
        retry_ticket = await ticket(owner, retry)
        assert owner.decide(retry_ticket.approval_id, True)
        retry_grant = await retry_task
        with owner.execution_scope(retry, retry_grant):
            check_tool_authority("test_write", ARGS, commit=True)
    finally:
        cancellation.cleanup_release.set()
        await owner.close()
        await asyncio.gather(task, return_exceptions=True)


async def test_cancel_after_tool_decision_before_handoff_latches_and_releases_capacity():
    owner = ApprovalAuthority(max_pending=1)
    request = operation()
    cancellation = asyncio.Event()
    task = asyncio.create_task(owner.request(request, cancellation))
    try:
        view = await ticket(owner, request)
        assert owner.decide(view.approval_id, True)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancellation.is_set()
        retry = operation()
        retry_task = asyncio.create_task(owner.request(retry, asyncio.Event()))
        retry_ticket = await ticket(owner, retry)
        assert owner.decide(retry_ticket.approval_id, False)
        assert await retry_task is None
    finally:
        await owner.close()
        await asyncio.gather(task, return_exceptions=True)
