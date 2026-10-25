"""Real central leases, synthetic decisions, and an explicit authority clock.

No sleeps to expire a lease, real effects, network, account, or model. Continuing
a committed bounded operation must never create or renew an execution right.
"""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

import jarvis_core.tools as module
from jarvis_core.runtime import ApprovalRequest
from jarvis_core.tools import (
    ApprovalAuthority,
    ToolError,
    canonical_arguments,
    check_tool_authority,
    operation_digest,
    revalidate_committed_tool_authority,
)

ARGS = {"url": "https://fixture.example.org/start"}
NAME = "web_fetch"


@pytest.fixture
async def authority(monkeypatch):
    now = [1000.0]
    # Replace only this module's reference; asyncio's real clock keeps running.
    monkeypatch.setattr(
        module, "time", SimpleNamespace(monotonic=lambda: now[0], time_ns=lambda: 1_000_000)
    )
    value = ApprovalAuthority(ttl=10)
    yield value, now
    await value.close()


async def approved(authority, *, name=NAME, arguments=None, session="synthetic-chat"):
    arguments = ARGS if arguments is None else arguments
    operation = ApprovalRequest(
        uuid4().hex, session, uuid4().hex, name, canonical_arguments(arguments), ""
    )
    operation = replace(operation, digest=operation_digest(operation))
    cancellation = asyncio.Event()
    task = asyncio.create_task(authority.request(operation, cancellation))
    async with asyncio.timeout(3):
        while True:
            entries = [
                item
                for item in authority.list_pending()
                if item.operation_id == operation.operation_id
            ]
            if entries:
                assert entries[0].arguments_json == canonical_arguments(arguments)
                assert authority.decide(entries[0].approval_id, True)
                break
            if task.done():
                pytest.fail("The synthetic central approval was not issued")
            await asyncio.sleep(0)
        grant = await task
    assert grant is not None
    return operation, grant, cancellation


def test_continuation_without_any_scope_requires_authority():
    with pytest.raises(ToolError, match=r"^tool_authority_required$"):
        revalidate_committed_tool_authority(NAME, ARGS)


async def test_uncommitted_lease_cannot_be_continued_or_implicitly_committed(authority):
    owner, _now = authority
    operation, grant, _cancel = await approved(owner)
    with owner.execution_scope(operation, grant):
        with pytest.raises(ToolError, match=r"^tool_authority_mismatch$"):
            revalidate_committed_tool_authority(NAME, ARGS)
        # Failed continuation did not consume the original explicit commit.
        check_tool_authority(NAME, ARGS, commit=True)
        revalidate_committed_tool_authority(NAME, ARGS)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("other_tool", ARGS),
        (NAME, {"url": "https://fixture.example.org/changed"}),
        (NAME, {"url": ARGS["url"], "extra": True}),
    ],
)
async def test_continuation_requires_the_same_name_and_exact_arguments(authority, name, arguments):
    owner, _now = authority
    operation, grant, _cancel = await approved(owner)
    with owner.execution_scope(operation, grant):
        check_tool_authority(NAME, ARGS, commit=True)
        with pytest.raises(ToolError, match=r"^tool_authority_mismatch$"):
            revalidate_committed_tool_authority(name, arguments)
        revalidate_committed_tool_authority(NAME, dict(ARGS))


async def test_repeated_valid_continuations_never_allow_another_commit_or_grant_replay(authority):
    owner, now = authority
    operation, grant, _cancel = await approved(owner)
    with owner.execution_scope(operation, grant):
        check_tool_authority(NAME, ARGS, commit=True)
        for _ in range(5):
            revalidate_committed_tool_authority(NAME, ARGS)
        with pytest.raises(ToolError, match=r"^tool_authority_mismatch$"):
            check_tool_authority(NAME, ARGS, commit=True)
        # Continuations close to expiry do not renew the original ten seconds.
        now[0] = 1009.99
        revalidate_committed_tool_authority(NAME, ARGS)
        now[0] = 1010.0
        with pytest.raises(ToolError, match=r"^tool_authority_revoked$"):
            revalidate_committed_tool_authority(NAME, ARGS)
    with pytest.raises(ToolError, match=r"^tool_authority_invalid$"):
        owner.consume(operation, grant)


@pytest.mark.parametrize("invalidator", ["cancel", "epoch", "expiry", "close"])
async def test_original_cancel_epoch_expiry_and_close_still_apply_after_commit(
    authority, invalidator
):
    owner, now = authority
    operation, grant, cancellation = await approved(owner)
    with owner.execution_scope(operation, grant):
        check_tool_authority(NAME, ARGS, commit=True)
        revalidate_committed_tool_authority(NAME, ARGS)
        if invalidator == "cancel":
            cancellation.set()
        elif invalidator == "epoch":
            owner.revoke("synthetic-chat")
        elif invalidator == "expiry":
            now[0] = 1010.0
        else:
            await owner.close()
        with pytest.raises(ToolError, match=r"^tool_authority_revoked$"):
            revalidate_committed_tool_authority(NAME, ARGS)


async def test_revoking_an_unrelated_session_does_not_renew_or_revoke_this_one(authority):
    owner, _now = authority
    operation, grant, _cancellation = await approved(owner)
    with owner.execution_scope(operation, grant):
        check_tool_authority(NAME, ARGS, commit=True)
        owner.revoke("different-synthetic-chat")
        revalidate_committed_tool_authority(NAME, ARGS)


async def test_detached_task_retaining_context_cannot_continue_an_inactive_scope(authority):
    owner, _now = authority
    operation, grant, _cancel = await approved(owner)
    release = asyncio.Event()

    async def detached():
        await release.wait()
        with pytest.raises(ToolError, match=r"^tool_authority_mismatch$"):
            revalidate_committed_tool_authority(NAME, ARGS)

    with owner.execution_scope(operation, grant):
        check_tool_authority(NAME, ARGS, commit=True)
        revalidate_committed_tool_authority(NAME, ARGS)
        child = asyncio.create_task(detached())
    release.set()
    await child
    with pytest.raises(ToolError, match=r"^tool_authority_required$"):
        revalidate_committed_tool_authority(NAME, ARGS)


async def test_nested_other_tool_owns_its_own_lease_and_cannot_inherit_the_outer_continuation(
    authority,
):
    owner, _now = authority
    outer, outer_grant, _outer_cancel = await approved(owner)
    inner_args = {"text": "synthetic"}
    inner, inner_grant, _inner_cancel = await approved(
        owner, name="other_tool", arguments=inner_args
    )
    with owner.execution_scope(outer, outer_grant):
        check_tool_authority(NAME, ARGS, commit=True)
        with owner.execution_scope(inner, inner_grant):
            with pytest.raises(ToolError, match=r"^tool_authority_mismatch$"):
                revalidate_committed_tool_authority(NAME, ARGS)
            with pytest.raises(ToolError, match=r"^tool_authority_mismatch$"):
                revalidate_committed_tool_authority("other_tool", inner_args)
            check_tool_authority("other_tool", inner_args, commit=True)
            revalidate_committed_tool_authority("other_tool", inner_args)
            with pytest.raises(ToolError, match=r"^tool_authority_mismatch$"):
                revalidate_committed_tool_authority(NAME, ARGS)
        # Resetting the nested context restores only the still-active outer one.
        revalidate_committed_tool_authority(NAME, ARGS)
        with pytest.raises(ToolError, match=r"^tool_authority_mismatch$"):
            revalidate_committed_tool_authority("other_tool", inner_args)
