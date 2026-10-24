"""Synthetic human decisions exercise the actual process-local authority."""

import asyncio
from dataclasses import FrozenInstanceError, replace

import pytest

from jarvis_core.runtime import ApprovalGrant, ApprovalRequest, ToolDefinition, ToolResult
from jarvis_core.tools import (
    ApprovalAuthority,
    ToolCancelled,
    ToolCatalog,
    ToolError,
    ToolSpec,
    canonical_arguments,
    check_tool_authority,
    operation_digest,
)


def operation(*, session="session", run="run", identity="one", arguments=None):
    value = ApprovalRequest(run, session, identity, "test_write",
                            canonical_arguments(arguments or {"text": "uno"}), "")
    return replace(value, digest=operation_digest(value))


async def pending(authority, *, count=1):
    async with asyncio.timeout(1):
        while len(authority.list_pending()) < count:
            await asyncio.sleep(0.001)
    return authority.list_pending()


async def grant(authority, request, cancellation=None):
    task = asyncio.create_task(authority.request(request, cancellation or asyncio.Event()))
    tickets = await pending(authority)
    assert authority.decide(tickets[-1].approval_id, True)
    return await task


async def test_grant_is_exact_unforgeable_and_consumed_once():
    authority = ApprovalAuthority()
    request = operation()
    value = await grant(authority, request)
    with pytest.raises(ToolError, match="invalid"):
        authority.consume(request, ApprovalGrant(value.digest))
    with authority.execution_scope(request, value):
        check_tool_authority("test_write", {"text": "uno"})
        check_tool_authority("test_write", {"text": "uno"}, commit=True)
        with pytest.raises(ToolError, match="mismatch"):
            check_tool_authority("test_write", {"text": "uno"}, commit=True)
    with pytest.raises(ToolError, match="invalid"):
        authority.consume(request, value)
    with pytest.raises(ToolError, match="required"):
        check_tool_authority("test_write", {"text": "uno"})


async def test_ticket_is_immutable_and_decision_cannot_be_replayed():
    authority = ApprovalAuthority()
    request = operation()
    task = asyncio.create_task(authority.request(request, asyncio.Event()))
    ticket, = await pending(authority)
    with pytest.raises(FrozenInstanceError):
        ticket.tool_name = "another_write"
    assert authority.decide(ticket.approval_id, True)
    assert not authority.decide(ticket.approval_id, True)
    authority.consume(request, await task)
    with pytest.raises(ToolError, match="replayed"):
        await authority.request(request, asyncio.Event())


async def test_context_cannot_change_destination_arguments_or_run():
    authority = ApprovalAuthority()
    request = operation()
    value = await grant(authority, request)
    with pytest.raises(ToolError, match="invalid"):
        authority.consume(replace(request, run_id="other"), value)
    value = await grant(authority, operation(identity="two"))
    with authority.execution_scope(operation(identity="two"), value):
        with pytest.raises(ToolError, match="mismatch"):
            check_tool_authority("test_write", {"text": "otro"})
        with pytest.raises(ToolError, match="mismatch"):
            check_tool_authority("other_write", {"text": "uno"})


async def test_detached_task_cannot_keep_scope_after_its_execution_returns():
    authority, release = ApprovalAuthority(), asyncio.Event()
    request = operation()
    value = await grant(authority, request)

    async def detached():
        await release.wait()
        with pytest.raises(ToolError):
            check_tool_authority("test_write", {"text": "uno"}, commit=True)

    with authority.execution_scope(request, value):
        child = asyncio.create_task(detached())
    release.set()
    await child


async def test_revocation_denies_pending_unused_and_current_execution():
    authority = ApprovalAuthority()
    first = asyncio.create_task(authority.request(operation(), asyncio.Event()))
    await pending(authority)
    assert authority.revoke("session") >= 1
    assert await first is None
    request = operation(identity="two")
    value = await grant(authority, request)
    authority.revoke("session")
    with pytest.raises(ToolError):
        authority.consume(request, value)
    request = operation(identity="three")
    value = await grant(authority, request)
    with authority.execution_scope(request, value):
        authority.revoke("session")
        with pytest.raises(ToolError, match="revoked"):
            check_tool_authority("test_write", {"text": "uno"}, commit=True)


async def test_timeout_and_cancel_after_grant_fail_closed():
    authority = ApprovalAuthority(ttl=0.02)
    assert await authority.request(operation(), asyncio.Event()) is None
    assert authority.list_pending() == ()
    cancellation = asyncio.Event()
    request = operation(identity="two")
    value = await grant(authority, request, cancellation)
    cancellation.set()
    with pytest.raises(ToolError, match="revoked"):
        authority.consume(request, value)
    request = operation(identity="three")
    value = await grant(authority, request)
    await asyncio.sleep(0.03)
    with pytest.raises(ToolError, match="revoked"):
        authority.consume(request, value)


async def test_close_releases_waiter_and_cancellation_never_leaves_pending():
    authority = ApprovalAuthority()
    cancellation = asyncio.Event()
    task = asyncio.create_task(authority.request(operation(), cancellation))
    await pending(authority)
    cancellation.set()
    assert await task is None and not authority.list_pending()
    task = asyncio.create_task(authority.request(operation(identity="two"), asyncio.Event()))
    await pending(authority)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not authority.list_pending()
    task = asyncio.create_task(authority.request(operation(identity="three"), asyncio.Event()))
    await pending(authority)
    await authority.close()
    assert await task is None
    assert await authority.request(operation(identity="four"), asyncio.Event()) is None


async def test_pending_budget_and_invalid_digest_cannot_create_an_approval():
    authority = ApprovalAuthority(max_pending=1)
    with pytest.raises(ToolError, match="digest"):
        await authority.request(replace(operation(), digest="forged"), asyncio.Event())
    task = asyncio.create_task(authority.request(operation(), asyncio.Event()))
    await pending(authority)
    with pytest.raises(ToolError, match="capacity"):
        await authority.request(operation(identity="two"), asyncio.Event())
    await authority.close()
    assert await task is None


def catalog(authority, execute, *, validate=lambda _: None, confirmation=True):
    return ToolCatalog([ToolDefinition("test_write", validate, execute,
                                       requires_confirmation=confirmation)],
                       [ToolSpec("test_write", "Escribe el recurso de prueba.", {
                           "type": "object", "properties": {"text": {"type": "string"}},
                       })], authority=authority)


async def test_catalog_snapshots_arguments_and_detects_mutation_while_awaiting_owner():
    authority, executed = ApprovalAuthority(), []

    async def execute(arguments, _):
        executed.append(arguments)
        return ToolResult("test_write", True, True)

    tools = catalog(authority, execute)
    arguments = {"text": "antes"}
    task = asyncio.create_task(tools.execute("test_write", arguments,
                                             session_id="s", run_id="r"))
    ticket, = await pending(authority)
    assert ticket.arguments_json == '{"text":"antes"}'
    arguments["text"] = "después"
    authority.decide(ticket.approval_id, True)
    result = await task
    assert not result.succeeded and result.error == "tool_arguments_changed"
    assert not executed


async def test_catalog_separates_model_text_from_action_receipt_and_guards_commit():
    authority = ApprovalAuthority()

    async def execute(arguments, _):
        check_tool_authority("test_write", arguments, commit=True)
        with pytest.raises(TypeError):
            arguments["text"] = "mutado"
        return ToolResult("test_write", True, True, output="recibo independiente")

    tools = catalog(authority, execute)
    task = asyncio.create_task(tools.execute("test_write", {"text": "dato"},
                                             session_id="s", run_id="r"))
    ticket, = await pending(authority)
    authority.decide(ticket.approval_id, True)
    result = await task
    assert result.verified and result.output == "recibo independiente"
    assert len(result.operation_id) == 32
    assert tools.specs()[0]["type"] == "function"
    assert tools.definitions()[0].requires_confirmation
    tools.specs()[0]["inputSchema"]["properties"]["text"]["type"] = "number"
    assert tools.specs()[0]["inputSchema"]["properties"]["text"]["type"] == "string"


async def test_catalog_does_not_execute_unoffered_or_denied_tool():
    authority, executed = ApprovalAuthority(), []

    async def execute(arguments, _):
        executed.append(arguments)
        return ToolResult("test_write", True, True)

    tools = catalog(authority, execute)
    result = await tools.execute("test_write", {}, session_id="s", run_id="r", offered_tools=[])
    assert result.error == "tool_not_offered"
    task = asyncio.create_task(tools.execute("test_write", {}, session_id="s", run_id="r"))
    ticket, = await pending(authority)
    authority.decide(ticket.approval_id, False)
    assert (await task).error == "approval_denied" and not executed


@pytest.mark.parametrize("kind", ["exception", "wrong_result", "cancel_flag"])
async def test_catalog_preserves_uncertainty_after_dispatch(kind):
    authority, cancellation = ApprovalAuthority(), asyncio.Event()

    async def execute(_, signal):
        if kind == "exception":
            raise RuntimeError("SECRET FROM A PEER")
        if kind == "cancel_flag":
            signal.set()
        return ToolResult("other_tool" if kind == "wrong_result" else "test_write", True, True)

    tools = catalog(authority, execute, confirmation=False)
    result = await tools.execute("test_write", {}, session_id="s", run_id="r",
                                 cancellation=cancellation)
    assert not result.verified and result.effect_status == "unknown"
    assert "SECRET" not in (result.error or "")


async def test_catalog_task_cancellation_keeps_an_uncertain_receipt():
    authority, dispatched = ApprovalAuthority(), asyncio.Event()

    async def execute(_, __):
        dispatched.set()
        await asyncio.Event().wait()

    tools = catalog(authority, execute, confirmation=False)
    task = asyncio.create_task(tools.execute("test_write", {}, session_id="s", run_id="r"))
    await dispatched.wait()
    task.cancel()
    with pytest.raises(ToolCancelled) as error:
        await task
    assert error.value.receipt.effect_status == "unknown"
    assert not error.value.receipt.verified


@pytest.mark.parametrize("arguments", [{"x": float("nan")}, {"x": object()}, {1: "bad"},
                                        {"x": "a" * 131073}])
def test_canonical_data_has_limits_and_no_non_json_values(arguments):
    with pytest.raises(ToolError):
        canonical_arguments(arguments)


def test_schema_and_catalog_metadata_fail_closed():
    with pytest.raises(ToolError):
        ToolSpec("Bad tool", "Prueba", {"type": "object"})
    with pytest.raises(ToolError):
        ToolSpec("test_tool", "Prueba", {"type": "string"})
    with pytest.raises(ToolError):
        ToolCatalog([], [ToolSpec("test_tool", "Prueba", {"type": "object"})],
                    authority=ApprovalAuthority())
