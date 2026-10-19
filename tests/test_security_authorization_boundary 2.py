from dataclasses import replace

import pytest

from aegis_core.contracts import AgentRole, PolicyDecision, ToolAuthorization, ToolCall
from aegis_core.security import SecurityStateLatch
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context
from aegis_core.tools.execution import ReadOnlyToolExecutor


def _call() -> ToolCall:
    return ToolCall(
        call_id="security-boundary", tool_name="system_describe_runtime", arguments={},
        requested_by=AgentRole.CODE_SECURITY,
    )


def _allowed() -> ToolAuthorization:
    call = _call()
    return ToolAuthorization(
        call_id=call.call_id, tool_name="guard_probe", call_digest=call.digest(),
        decision=PolicyDecision.ALLOW, reason_code="policy_allowed",
    )


def test_security_loss_denies_even_an_already_admitted_tool(tmp_path) -> None:
    latch = SecurityStateLatch()
    context = replace(
        default_policy_context(tmp_path), security_compromised=lambda: latch.compromised
    )
    broker = build_default_tool_broker()
    assert broker.authorize(_call(), context).decision is PolicyDecision.ALLOW
    latch.compromise("synthetic_integrity_failure")
    rejected = broker.authorize(_call(), context)
    assert rejected.decision is PolicyDecision.DENY
    assert rejected.reason_code == "security_compromised"


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_security_loss_blocks_dispatch_before_any_handler_runs(
    tmp_path, asynchronous
) -> None:
    latch = SecurityStateLatch()
    context = replace(
        default_policy_context(tmp_path), security_compromised=lambda: latch.compromised
    )
    calls = []

    def handler(*args):
        calls.append(args)
        raise AssertionError("a compromised runtime must not call an executor")

    async def async_handler(*args):
        return handler(*args)

    executor = ReadOnlyToolExecutor(
        extra_handlers={} if asynchronous else {"guard_probe": handler},
        extra_async_handlers={"guard_probe": async_handler} if asynchronous else {},
    )
    latch.compromise("synthetic_integrity_failure")
    result = await executor.execute_async(_allowed(), context)
    assert result.success is False
    assert result.error_code == "security_compromised"
    assert calls == []


def test_security_loss_is_rechecked_inside_the_blocking_worker(tmp_path) -> None:
    context = replace(default_policy_context(tmp_path), security_compromised=lambda: True)
    result = ReadOnlyToolExecutor().execute(_allowed(), context)
    assert result.error_code == "security_compromised"


@pytest.mark.parametrize("value", [None, 0, "false"])
def test_security_probe_requires_an_explicit_false_result(tmp_path, value) -> None:
    context = replace(default_policy_context(tmp_path), security_compromised=lambda: value)
    assert build_default_tool_broker().authorize(_call(), context).decision is PolicyDecision.DENY


def test_failed_security_probe_fails_closed(tmp_path) -> None:
    def failed():
        raise OSError("private diagnostic detail")

    context = replace(default_policy_context(tmp_path), security_compromised=failed)
    result = build_default_tool_broker().authorize(_call(), context)
    assert result.decision is PolicyDecision.DENY
    assert "diagnostic" not in result.model_dump_json()


async def test_graph_guards_execution_recovery_and_device_callbacks(tmp_path, monkeypatch) -> None:
    from unittest.mock import AsyncMock

    import aegis_core.orchestration.graph as graph_module
    from aegis_core.contracts import ToolExecutionResult

    latch = SecurityStateLatch()
    context = replace(
        default_policy_context(tmp_path), security_compromised=lambda: latch.compromised
    )
    real_runner = graph_module.PlanExecuteReflectRunner
    callbacks = {}

    def capture(executor, **kwargs):
        callbacks.update(kwargs, executor=executor)
        return real_runner(executor, **kwargs)

    monkeypatch.setattr(graph_module, "PlanExecuteReflectRunner", capture)
    executor = AsyncMock()
    device = AsyncMock()
    graph_module.build_swarm_graph(
        AsyncMock(), policy_context=context, tool_executor=executor,
        device_waker=device, device_verifier=device,
    )
    executor.reset_mock()
    device.reset_mock()
    latch.compromise("synthetic_integrity_failure")
    authorization = _allowed()
    failed = ToolExecutionResult(
        call_id=authorization.call_id, tool_name=authorization.tool_name,
        success=False, error_code="ui_blocked",
    )
    assert (await callbacks["executor"](authorization)).error_code == "security_compromised"
    assert await callbacks["ui_corrector"](authorization, failed) is False
    assert await callbacks["ui_reloader"](authorization) is False
    assert await callbacks["ui_reevaluator"](authorization) is False
    assert await callbacks["device_waker"](authorization) == (False, 0)
    assert await callbacks["device_verifier"](authorization) == (False, 0)
    assert executor.mock_calls == []
    device.assert_not_called()
