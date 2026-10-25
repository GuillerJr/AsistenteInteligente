"""Real central authority, synthetic owner decisions and coordinated worker effects.

No app/profile data, model, credentials, real owner decisions or external effects.
The temporary effect file is intentionally synthetic and never replayed by a retry.
"""

import asyncio
import contextvars
import threading
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

import jarvis_core.tools as module
from jarvis_core.runtime import ApprovalGrant, ApprovalRequest
from jarvis_core.tools import (
    ApprovalAuthority,
    HostApprovalGrant,
    HostApprovalRequest,
    HostCancellation,
    HostPurpose,
    HostScope,
    ToolError,
    canonical_arguments,
    canonical_host_arguments,
    check_host_authority,
    check_tool_authority,
    host_operation_digest,
    operation_digest,
    revalidate_committed_host_authority,
)

SCOPE = HostScope("synthetic_profile", 0)
ARGS = {"review_sha256": "a" * 64, "destination_id": "synthetic_destination", "count": 3}


def operation(*, scope=SCOPE, purpose=HostPurpose.BACKUP_CREATE, identity=None, arguments=None):
    value = HostApprovalRequest(
        scope, purpose, identity or uuid4().hex,
        canonical_host_arguments(ARGS if arguments is None else arguments), "",
    )
    return replace(value, digest=host_operation_digest(value))


def tool_operation(*, identity=None):
    value = ApprovalRequest("run", "synthetic_profile", identity or uuid4().hex,
                            "backup_create", canonical_arguments(ARGS), "")
    return replace(value, digest=operation_digest(value))


async def ticket(owner, scope=SCOPE, *, count=1):
    async with asyncio.timeout(3):
        while len(owner.pending_host(scope)) < count:
            await asyncio.sleep(0)
    return owner.pending_host(scope)[-1]


async def approved(owner, request=None, cancellation=None):
    request = operation() if request is None else request
    cancellation = HostCancellation() if cancellation is None else cancellation
    task = asyncio.create_task(owner.request_host(request, cancellation))
    view = await ticket(owner, request.scope)
    assert owner.decide_host(
        view.approval_id, scope=request.scope, digest=request.digest, allow=True,
    )
    grant = await task
    assert grant is not None
    return request, grant, cancellation


@pytest.fixture
async def authority():
    owner = ApprovalAuthority()
    owner.bind_host_scope(SCOPE)
    yield owner
    await owner.close()


@pytest.fixture
async def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(
        monotonic=lambda: now[0], time_ns=lambda: 1_000_000,
    ))
    owner = ApprovalAuthority(ttl=10)
    owner.bind_host_scope(SCOPE)
    yield owner, now
    await owner.close()


@pytest.mark.parametrize("profile", ["", "x" * 129, "á", "/profile", "a.b", 3, True])
def test_scope_rejects_nonopaque_or_unbounded_profile(profile):
    with pytest.raises(ToolError, match=r"^invalid_host_scope$"):
        HostScope(profile, 0)


@pytest.mark.parametrize("generation", [-1, 2**52, True, False, 0.0, "0", None])
def test_scope_rejects_inexact_or_out_of_range_generation(generation):
    with pytest.raises(ToolError, match=r"^invalid_host_scope$"):
        HostScope("synthetic", generation)


def test_digest_is_canonical_bounded_and_domain_separated():
    first = operation(identity="f" * 32, arguments={"b": 2, "a": 1})
    same = operation(identity="f" * 32, arguments={"a": 1, "b": 2})
    assert first == same
    assert len(first.digest) == 64
    tool = tool_operation(identity="f" * 32)
    assert tool.digest != operation(identity="f" * 32).digest
    changes = [
        replace(first, scope=HostScope(SCOPE.profile_id, 1)),
        replace(first, scope=HostScope("other", 0)),
        replace(first, purpose=HostPurpose.RESTORE_STAGE),
        replace(first, operation_id="e" * 32),
        replace(first, arguments_json=canonical_host_arguments({"a": 2, "b": 2})),
    ]
    assert all(host_operation_digest(item) != first.digest for item in changes)
    with pytest.raises(ToolError, match=r"^noncanonical_host_arguments$"):
        host_operation_digest(replace(first, arguments_json='{"b":2,"a":1}'))
    with pytest.raises(ToolError, match=r"^host_data_limit$"):
        canonical_host_arguments({"value": "x" * 16384})
    with pytest.raises(ToolError):
        host_operation_digest(replace(first, arguments_json="[]"))
    with pytest.raises(ToolError):
        host_operation_digest(replace(first, arguments_json='{"x":NaN}'))
    with pytest.raises(ToolError, match=r"^invalid_host_approval_request$"):
        host_operation_digest(replace(first, purpose="backup.create"))
    with pytest.raises(FrozenInstanceError):
        first.scope.generation = 1


async def test_request_requires_bound_exact_scope_and_canonical_digest():
    owner = ApprovalAuthority()
    try:
        with pytest.raises(ToolError, match=r"^host_scope_mismatch$"):
            await owner.request_host(operation(), HostCancellation())
        owner.bind_host_scope(SCOPE)
        with pytest.raises(ToolError, match=r"^host_approval_digest_mismatch$"):
            await owner.request_host(replace(operation(), digest="0" * 64), HostCancellation())
        with pytest.raises(ToolError, match=r"^host_scope_mismatch$"):
            await owner.request_host(operation(scope=HostScope("other", 0)), HostCancellation())
        with pytest.raises(ToolError, match=r"^invalid_host_cancellation$"):
            await owner.request_host(operation(), asyncio.Event())
        for invalid in (None, tool_operation(), {}):
            with pytest.raises(ToolError, match=r"^invalid_host_approval_request$"):
                await owner.request_host(invalid, HostCancellation())
    finally:
        await owner.close()


async def test_host_ticket_review_and_decision_are_exact_and_immutable(authority):
    request = operation()
    task = asyncio.create_task(authority.request_host(request, HostCancellation()))
    view = await ticket(authority)
    assert view.scope == request.scope
    assert view.purpose == request.purpose
    assert view.operation_id == request.operation_id
    assert view.arguments_json == canonical_host_arguments(ARGS)
    assert view.digest == request.digest
    with pytest.raises(FrozenInstanceError):
        view.digest = "0" * 64
    assert not authority.decide_host(view.approval_id, scope=HostScope("other", 0),
                                     digest=request.digest, allow=True)
    assert not authority.decide_host(view.approval_id, scope=SCOPE,
                                     digest="0" * 64, allow=True)
    assert not authority.decide(view.approval_id, True)
    assert not task.done()
    assert authority.decide_host(view.approval_id, scope=SCOPE, digest=request.digest, allow=True)
    assert not authority.decide_host(view.approval_id, scope=SCOPE,
                                     digest=request.digest, allow=True)
    grant = await task
    with authority.execution_host(request, grant):
        authority.check_host(request, commit=True)


async def test_host_and_tool_capabilities_cannot_be_borrowed(authority):
    request, grant, _ = await approved(authority)
    tool = tool_operation(identity=request.operation_id)
    with pytest.raises(ToolError, match=r"^tool_authority_invalid$"):
        authority.consume(tool, grant)
    # A failed attempt in another domain cannot consume the real host grant.
    with authority.execution_host(request, grant):
        with pytest.raises(ToolError, match=r"^tool_authority_required$"):
            check_tool_authority(tool.tool_name, ARGS, commit=True)
        authority.check_host(request, commit=True)
    task = asyncio.create_task(authority.request(tool, asyncio.Event()))
    async with asyncio.timeout(3):
        while not authority.list_pending():
            await asyncio.sleep(0)
    view, = authority.list_pending()
    assert not authority.decide_host(view.approval_id, scope=SCOPE,
                                     digest=tool.digest, allow=True)
    assert authority.decide(view.approval_id, True)
    tool_grant = await task
    with pytest.raises(ToolError, match=r"^host_authority_invalid$"):
        with authority.execution_host(operation(), tool_grant):
            pytest.fail("borrowed a tool grant")
    with authority.execution_scope(tool, tool_grant):
        with pytest.raises(ToolError, match=r"^host_authority_required$"):
            check_host_authority(request, commit=True)
        check_tool_authority(tool.tool_name, ARGS, commit=True)


async def test_issued_host_grant_cannot_be_forged_or_consumed_twice(authority):
    request, grant, _ = await approved(authority)
    for forged in (HostApprovalGrant(grant.digest), ApprovalGrant(grant.digest)):
        with pytest.raises(ToolError, match=r"^host_authority_invalid$"):
            with authority.execution_host(request, forged):
                pytest.fail("forged host capability")
    with authority.execution_host(request, grant):
        authority.check_host(request)
        authority.check_host(request, commit=True)
        revalidate_committed_host_authority(request)
        with pytest.raises(ToolError, match=r"^host_authority_mismatch$"):
            authority.check_host(request, commit=True)
    with pytest.raises(ToolError, match=r"^host_authority_invalid$"):
        with authority.execution_host(request, grant):
            pytest.fail("replayed host capability")


@pytest.mark.parametrize("change", ["purpose", "scope", "operation", "arguments", "digest"])
async def test_lease_rejects_any_changed_identity_or_arguments(authority, change):
    request, grant, _ = await approved(authority)
    changed = {
        "purpose": replace(request, purpose=HostPurpose.RESTORE_STAGE),
        "scope": replace(request, scope=HostScope(SCOPE.profile_id, 1)),
        "operation": replace(request, operation_id=uuid4().hex),
        "arguments": replace(request, arguments_json=canonical_host_arguments({"count": 4})),
        "digest": replace(request, digest="0" * 64),
    }[change]
    with authority.execution_host(request, grant):
        with pytest.raises(ToolError, match=r"^host_authority_mismatch$"):
            authority.check_host(changed, commit=True)
        authority.check_host(request, commit=True)
        with pytest.raises(ToolError, match=r"^host_authority_mismatch$"):
            authority.revalidate_committed_host(changed)
        authority.revalidate_committed_host(request)


async def test_no_implicit_commit_and_no_foreign_authority(clock):
    authority, _now = clock
    other = ApprovalAuthority()
    other.bind_host_scope(SCOPE)
    request, grant, _ = await approved(authority)
    try:
        with authority.execution_host(request, grant):
            with pytest.raises(ToolError, match=r"^host_authority_mismatch$"):
                authority.revalidate_committed_host(request)
            with pytest.raises(ToolError, match=r"^host_authority_required$"):
                other.check_host(request, commit=True)
            with pytest.raises(ToolError, match=r"^invalid_host_commit$"):
                authority.check_host(request, commit=1)
            authority.check_host(request, commit=True)
            with pytest.raises(ToolError, match=r"^host_authority_required$"):
                other.revalidate_committed_host(request)
    finally:
        await other.close()


async def test_bind_is_one_profile_monotonic_and_idempotent_without_renewal(clock):
    authority, now = clock
    request, grant, _ = await approved(authority)
    with authority.execution_host(request, grant):
        authority.check_host(request, commit=True)
        now[0] += 9
        authority.bind_host_scope(SCOPE)
        authority.revalidate_committed_host(request)
        with pytest.raises(ToolError, match=r"^host_profile_mismatch$"):
            authority.bind_host_scope(HostScope("other", 1))
        now[0] += 1
        with pytest.raises(ToolError, match=r"^host_authority_revoked$"):
            authority.revalidate_committed_host(request)
    authority.bind_host_scope(HostScope(SCOPE.profile_id, 2))
    with pytest.raises(ToolError, match=r"^host_generation_stale$"):
        authority.bind_host_scope(HostScope(SCOPE.profile_id, 1))


async def test_new_generation_revokes_pending_unused_and_committed_leases(authority):
    request, grant, _ = await approved(authority)
    unused_request, unused, _ = await approved(authority)
    pending_request = operation()
    pending = asyncio.create_task(authority.request_host(pending_request, HostCancellation()))
    view = await ticket(authority)
    with authority.execution_host(request, grant):
        authority.check_host(request, commit=True)
        authority.bind_host_scope(HostScope(SCOPE.profile_id, 1))
        await asyncio.sleep(0)
        with pytest.raises(ToolError, match=r"^host_authority_revoked$"):
            authority.revalidate_committed_host(request)
    assert await pending is None
    assert not authority.decide_host(view.approval_id, scope=SCOPE,
                                     digest=pending_request.digest, allow=True)
    assert authority.pending_host(SCOPE) == ()
    with pytest.raises(ToolError, match=r"^host_authority_invalid$"):
        with authority.execution_host(unused_request, unused):
            pytest.fail("old generation grant")


async def test_host_and_chat_revocation_domains_do_not_collide(authority):
    request, grant, _ = await approved(authority)
    tool = tool_operation()
    task = asyncio.create_task(authority.request(tool, asyncio.Event()))
    async with asyncio.timeout(3):
        while not authority.list_pending():
            await asyncio.sleep(0)
    view, = authority.list_pending()
    assert authority.decide(view.approval_id, True)
    tool_grant = await task
    with authority.execution_scope(tool, tool_grant), authority.execution_host(request, grant):
        authority.check_host(request, commit=True)
        authority.revoke(tool.session_id)
        authority.revalidate_committed_host(request)
        with pytest.raises(ToolError, match=r"^tool_authority_revoked$"):
            check_tool_authority(tool.tool_name, ARGS)
    tool = replace(tool, operation_id=uuid4().hex)
    tool = replace(tool, digest=operation_digest(tool))
    task = asyncio.create_task(authority.request(tool, asyncio.Event()))
    async with asyncio.timeout(3):
        while not authority.list_pending():
            await asyncio.sleep(0)
    view, = authority.list_pending()
    assert authority.decide(view.approval_id, True)
    tool_grant = await task
    with authority.execution_scope(tool, tool_grant):
        authority.revoke_host(SCOPE)
        check_tool_authority(tool.tool_name, ARGS, commit=True)


async def test_denial_and_explicit_retry_need_new_operation_identity(authority, tmp_path):
    request = operation()
    task = asyncio.create_task(authority.request_host(request, HostCancellation()))
    view = await ticket(authority)
    assert authority.decide_host(view.approval_id, scope=SCOPE, digest=request.digest, allow=False)
    assert await task is None
    with pytest.raises(ToolError, match=r"^approval_operation_replayed$"):
        await authority.request_host(request, HostCancellation())
    request, grant, _ = await approved(authority)
    effect = tmp_path / "synthetic-effect"
    with authority.execution_host(request, grant):
        await asyncio.to_thread(_write_once, authority, request, effect)
    assert effect.read_text() == request.operation_id
    with pytest.raises(ToolError, match=r"^approval_operation_replayed$"):
        await authority.request_host(request, HostCancellation())
    # No dispatch/replay scheduler exists in the authority.
    assert effect.read_text() == request.operation_id
    retry = operation()
    pending = asyncio.create_task(authority.request_host(retry, HostCancellation()))
    retry_view = await ticket(authority)
    assert retry_view.operation_id != request.operation_id
    assert effect.read_text() == request.operation_id
    authority.decide_host(retry_view.approval_id, scope=SCOPE, digest=retry.digest, allow=False)
    assert await pending is None


def _write_once(authority, request, effect):
    with pytest.raises(RuntimeError, match="no running event loop"):
        asyncio.get_running_loop()
    authority.check_host(request)
    authority.check_host(request, commit=True)
    effect.write_text(request.operation_id)
    authority.revalidate_committed_host(request)


async def test_cancel_from_worker_is_latched_before_delayed_loop_notification(authority):
    cancel = HostCancellation()
    request, grant, _ = await approved(authority, cancellation=cancel)
    assert not hasattr(cancel, "clear")
    with authority.execution_host(request, grant):
        authority.check_host(request, commit=True)
        # join intentionally prevents the loop from delivering notifications yet.
        result = []
        context = contextvars.copy_context()

        def worker():
            cancel.set()
            result.append(cancel.is_set())
            with pytest.raises(ToolError, match=r"^host_authority_revoked$"):
                authority.revalidate_committed_host(request)

        thread = threading.Thread(target=context.run, args=(worker,))
        thread.start()
        thread.join(1)
        assert not thread.is_alive()
        assert result == [True]
    await asyncio.sleep(0)
    await cancel.wait()
    assert cancel.is_set()


async def test_pending_cancel_from_thread_resolves_without_owner_decision(authority):
    cancellation = HostCancellation()
    request = operation()
    task = asyncio.create_task(authority.request_host(request, cancellation))
    view = await ticket(authority)
    await asyncio.to_thread(cancellation.set)
    assert await asyncio.wait_for(task, 1) is None
    assert not authority.decide_host(view.approval_id, scope=SCOPE,
                                     digest=request.digest, allow=True)
    assert authority.pending_host(SCOPE) == ()


async def test_request_task_cancellation_latches_and_cannot_leave_unused_grant(authority):
    cancellation = HostCancellation()
    request = operation()
    task = asyncio.create_task(authority.request_host(request, cancellation))
    view = await ticket(authority)
    assert authority.decide_host(view.approval_id, scope=SCOPE, digest=request.digest, allow=True)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancellation.is_set()
    assert authority.pending_host(SCOPE) == ()
    with pytest.raises(ToolError, match=r"^approval_operation_replayed$"):
        await authority.request_host(request, HostCancellation())


@pytest.mark.parametrize("invalidate", ["cancel", "revoke", "generation", "expiry", "close"])
async def test_worker_rechecks_after_await_before_any_effect(clock, tmp_path, invalidate):
    authority, now = clock
    request, grant, cancellation = await approved(authority)
    entered, release = threading.Event(), threading.Event()
    effect = tmp_path / "effect-must-not-exist"

    def worker():
        authority.check_host(request)
        entered.set()
        assert release.wait(2)
        authority.check_host(request, commit=True)
        effect.write_text("unauthorized")

    with authority.execution_host(request, grant):
        work = asyncio.create_task(asyncio.to_thread(worker))
        async with asyncio.timeout(2):
            while not entered.is_set():
                await asyncio.sleep(0)
        if invalidate == "cancel":
            cancellation.set()
        elif invalidate == "revoke":
            authority.revoke_host(SCOPE)
        elif invalidate == "generation":
            authority.bind_host_scope(HostScope(SCOPE.profile_id, 1))
        elif invalidate == "expiry":
            now[0] += 10
        else:
            await authority.close()
        release.set()
        with pytest.raises(ToolError, match=r"^host_authority_revoked$"):
            await work
    assert not effect.exists()


@pytest.mark.parametrize("invalidate", ["cancel", "revoke", "generation", "expiry", "close"])
async def test_committed_worker_cannot_continue_after_invalidation(clock, tmp_path, invalidate):
    authority, now = clock
    request, grant, cancellation = await approved(authority)
    committed, release = threading.Event(), threading.Event()
    first, forbidden = tmp_path / "first", tmp_path / "forbidden"

    def worker():
        authority.check_host(request, commit=True)
        first.write_text("known first effect")
        committed.set()
        assert release.wait(2)
        authority.revalidate_committed_host(request)
        forbidden.write_text("unauthorized continuation")

    with authority.execution_host(request, grant):
        work = asyncio.create_task(asyncio.to_thread(worker))
        async with asyncio.timeout(2):
            while not committed.is_set():
                await asyncio.sleep(0)
        if invalidate == "cancel":
            cancellation.set()
        elif invalidate == "revoke":
            authority.revoke_host(SCOPE)
        elif invalidate == "generation":
            authority.bind_host_scope(HostScope(SCOPE.profile_id, 1))
        elif invalidate == "expiry":
            now[0] += 10
        else:
            await authority.close()
        release.set()
        with pytest.raises(ToolError, match=r"^host_authority_revoked$"):
            await work
    assert first.read_text() == "known first effect"
    assert not forbidden.exists()


async def test_context_copied_to_detached_thread_is_invalid_after_scope_returns(authority):
    request, grant, _ = await approved(authority)
    with authority.execution_host(request, grant):
        context = contextvars.copy_context()
    # A copied ContextVar must not outlive its real authority scope.
    def late():
        with pytest.raises(ToolError, match=r"^host_authority_mismatch$"):
            context.run(authority.check_host, request, commit=True)
    await asyncio.to_thread(late)
    with pytest.raises(ToolError, match=r"^host_authority_required$"):
        authority.check_host(request)


async def test_two_worker_commits_are_serialized_and_only_one_can_write(authority, tmp_path):
    request, grant, _ = await approved(authority)
    barrier = threading.Barrier(2)
    outputs = [tmp_path / "one", tmp_path / "two"]

    def worker(target):
        barrier.wait(timeout=2)
        try:
            authority.check_host(request, commit=True)
        except ToolError as error:
            assert str(error) == "host_authority_mismatch"
            return False
        target.write_text("effect")
        return True

    with authority.execution_host(request, grant):
        results = await asyncio.gather(*(asyncio.to_thread(worker, path) for path in outputs))
    assert sorted(results) == [False, True]
    assert sum(path.exists() for path in outputs) == 1


async def test_capacity_32_is_shared_by_host_and_tool_and_released_on_cancel():
    authority = ApprovalAuthority(max_pending=32)
    authority.bind_host_scope(SCOPE)
    tasks, cancellations = [], []
    try:
        for index in range(32):
            if index % 2:
                cancellation = asyncio.Event()
                task = asyncio.create_task(authority.request(tool_operation(), cancellation))
            else:
                cancellation = HostCancellation()
                task = asyncio.create_task(authority.request_host(operation(), cancellation))
            cancellations.append(cancellation)
            tasks.append(task)
        async with asyncio.timeout(3):
            while len(authority.list_pending()) + len(authority.pending_host(SCOPE)) != 32:
                await asyncio.sleep(0)
        for request, cancellation, method in (
            (operation(), HostCancellation(), authority.request_host),
            (tool_operation(), asyncio.Event(), authority.request),
        ):
            with pytest.raises(ToolError, match=r"^approval_capacity$"):
                await method(request, cancellation)
        cancellations[0].set()
        assert await tasks[0] is None
        request = operation()
        extra = asyncio.create_task(authority.request_host(request, HostCancellation()))
        tasks.append(extra)
        view = await ticket(authority, count=16)
        assert view.operation_id == request.operation_id
        assert authority.decide_host(view.approval_id, scope=SCOPE,
                                     digest=request.digest, allow=False)
        assert await extra is None
    finally:
        await authority.close()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_issued_and_active_host_lease_still_hold_shared_capacity():
    authority = ApprovalAuthority(max_pending=1)
    authority.bind_host_scope(SCOPE)
    try:
        request, grant, _ = await approved(authority)
        for method, value, cancellation in (
            (authority.request_host, operation(), HostCancellation()),
            (authority.request, tool_operation(), asyncio.Event()),
        ):
            with pytest.raises(ToolError, match=r"^approval_capacity$"):
                await method(value, cancellation)
        with authority.execution_host(request, grant):
            with pytest.raises(ToolError, match=r"^approval_capacity$"):
                await authority.request_host(operation(), HostCancellation())
        request, grant, _ = await approved(authority)
        with authority.execution_host(request, grant):
            authority.check_host(request, commit=True)
    finally:
        await authority.close()


async def test_close_resolves_pending_invalidates_unused_and_active_and_cannot_rebind(authority):
    current, current_grant, _ = await approved(authority)
    unused, unused_grant, _ = await approved(authority)
    request = operation()
    task = asyncio.create_task(authority.request_host(request, HostCancellation()))
    await ticket(authority)
    with authority.execution_host(current, current_grant):
        authority.check_host(current, commit=True)
        await authority.close()
        await authority.close()
        with pytest.raises(ToolError, match=r"^host_authority_revoked$"):
            authority.revalidate_committed_host(current)
    assert await task is None
    assert await authority.request_host(operation(), HostCancellation()) is None
    with pytest.raises(ToolError, match=r"^host_authority_invalid$"):
        with authority.execution_host(unused, unused_grant):
            pytest.fail("grant after close")
    with pytest.raises(ToolError, match=r"^host_authority_closed$"):
        authority.bind_host_scope(SCOPE)


async def test_emission_and_owner_decision_remain_on_one_async_loop(authority):
    request = operation()
    task = asyncio.create_task(authority.request_host(request, HostCancellation()))
    view = await ticket(authority)

    def foreign_loop():
        async def attempt():
            for action in (
                lambda: authority.pending_host(SCOPE),
                lambda: authority.bind_host_scope(SCOPE),
                lambda: authority.decide_host(view.approval_id, scope=SCOPE,
                                               digest=request.digest, allow=True),
            ):
                with pytest.raises(ToolError, match=r"^approval_loop_mismatch$"):
                    action()
            with pytest.raises(ToolError, match=r"^approval_loop_mismatch$"):
                await authority.request_host(operation(), HostCancellation())
        asyncio.run(attempt())

    await asyncio.to_thread(foreign_loop)
    assert not task.done()
    authority.decide_host(view.approval_id, scope=SCOPE, digest=request.digest, allow=False)
    assert await task is None


async def test_cancel_callback_arriving_after_waiter_cancel_is_safe_and_cannot_rearm():
    cancellation = HostCancellation()
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    errors = []
    loop.set_exception_handler(lambda _loop, context: errors.append(context))
    try:
        waiter = asyncio.create_task(cancellation.wait())
        await asyncio.sleep(0)  # Registers the waiter's future before the worker sets the latch.
        worker = threading.Thread(target=cancellation.set)
        worker.start()
        worker.join(1)  # Notification is queued; the owning loop has not run it yet.
        assert not worker.is_alive()
        assert cancellation.is_set()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        await asyncio.sleep(0)
        assert errors == []
        cancellation.set()
        await cancellation.wait()
        assert cancellation.is_set()
    finally:
        loop.set_exception_handler(previous)


async def test_precancelled_request_does_not_consume_identity_or_a_slot(authority):
    request, cancellation = operation(), HostCancellation()
    cancellation.set()
    assert await authority.request_host(request, cancellation) is None
    assert authority.pending_host(SCOPE) == ()
    # No review or grant was emitted; a fresh explicit request may use this ID.
    same, grant, _ = await approved(authority, request)
    with authority.execution_host(same, grant):
        authority.check_host(same, commit=True)


async def test_expiry_resolves_pending_and_cannot_issue_late_owner_grant(clock):
    authority, now = clock
    request = operation()
    pending = asyncio.create_task(authority.request_host(request, HostCancellation()))
    view = await ticket(authority)
    now[0] += 10
    assert authority.pending_host(SCOPE) == ()
    assert await pending is None
    assert not authority.decide_host(view.approval_id, scope=SCOPE,
                                     digest=request.digest, allow=True)
    with pytest.raises(ToolError, match=r"^approval_operation_replayed$"):
        await authority.request_host(request, HostCancellation())


async def test_unused_expiry_and_continuation_cannot_renew_original_ttl(clock):
    authority, now = clock
    unused, unused_grant, _ = await approved(authority)
    request, grant, _ = await approved(authority)
    with authority.execution_host(request, grant):
        authority.check_host(request, commit=True)
        for delta in (2, 2, 2, 3):
            now[0] += delta
            await asyncio.to_thread(authority.revalidate_committed_host, request)
        now[0] += 1
        with pytest.raises(ToolError, match=r"^host_authority_revoked$"):
            await asyncio.to_thread(authority.revalidate_committed_host, request)
    with pytest.raises(ToolError, match=r"^host_authority_revoked$"):
        with authority.execution_host(unused, unused_grant):
            pytest.fail("expired unused grant")


async def test_revoke_does_not_reissue_same_operation_even_after_generation_changes(authority):
    request, grant, _ = await approved(authority)
    assert authority.revoke_host(SCOPE) > 0
    with pytest.raises(ToolError, match=r"^host_authority_invalid$"):
        with authority.execution_host(request, grant):
            pytest.fail("revoked grant")
    newer = HostScope(SCOPE.profile_id, 1)
    authority.bind_host_scope(newer)
    updated = replace(request, scope=newer)
    updated = replace(updated, digest=host_operation_digest(updated))
    with pytest.raises(ToolError, match=r"^approval_operation_replayed$"):
        await authority.request_host(updated, HostCancellation())
    fresh, fresh_grant, _ = await approved(authority, operation(scope=newer))
    with authority.execution_host(fresh, fresh_grant):
        authority.check_host(fresh, commit=True)


async def test_waiter_timeout_is_finite_without_polling_or_owner_action():
    owner = ApprovalAuthority(ttl=0.02)
    owner.bind_host_scope(SCOPE)
    try:
        async with asyncio.timeout(1):
            assert await owner.request_host(operation(), HostCancellation()) is None
        assert owner.pending_host(SCOPE) == ()
    finally:
        await owner.close()


async def test_cancel_during_grant_handoff_latches_and_releases_capacity(monkeypatch):
    owner = ApprovalAuthority(max_pending=1)
    owner.bind_host_scope(SCOPE)
    cleanup_entered, cleanup_release = asyncio.Event(), asyncio.Event()
    original_wait = HostCancellation.wait

    async def delayed_cleanup(cancellation):
        try:
            await original_wait(cancellation)
        finally:
            cleanup_entered.set()
            await cleanup_release.wait()

    monkeypatch.setattr(HostCancellation, "wait", delayed_cleanup)
    cancellation = HostCancellation()
    request = operation()
    task = asyncio.create_task(owner.request_host(request, cancellation))
    try:
        view = await ticket(owner)
        # Ensure the cancellation waiter is running before allowing the owner grant.
        await asyncio.sleep(0)
        assert owner.decide_host(view.approval_id, scope=SCOPE, digest=request.digest, allow=True)
        await asyncio.wait_for(cleanup_entered.wait(), 1)
        # The grant was internally issued, but the request has not acknowledged it.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancellation.is_set()
        monkeypatch.setattr(HostCancellation, "wait", original_wait)
        fresh, grant, _ = await approved(owner)
        with owner.execution_host(fresh, grant):
            owner.check_host(fresh, commit=True)
    finally:
        cleanup_release.set()
        await owner.close()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("value", ["\ud800", "\udfff"])
def test_invalid_unicode_is_rejected_with_safe_code(value):
    with pytest.raises(ToolError, match=r"^invalid_host_arguments$"):
        canonical_host_arguments({"destination": value})
    request = operation()
    with pytest.raises(ToolError, match=r"^invalid_host_arguments$"):
        host_operation_digest(replace(request, arguments_json=value))
