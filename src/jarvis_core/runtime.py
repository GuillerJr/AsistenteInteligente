"""Original execution contracts for the Jarvis reconstruction.

Hosts supply providers and tool adapters. Model text remains separate from action
receipts. Approval grants here are contracts, not signed macOS IPC identities.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal, Protocol
from uuid import uuid4


def _safe_error_type(name: str | None) -> str | None:
    if name is None:
        return None
    if isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name):
        return name
    return "UnknownError"


@dataclass(frozen=True)
class FinalAnswer:
    text: str


@dataclass(frozen=True)
class ToolProposal:
    tool_name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    succeeded: bool
    verified: bool
    output: str = ""
    error: str | None = None
    effect_status: Literal["known", "unknown"] = "known"
    operation_id: str | None = None
    error_type: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "error_type", _safe_error_type(self.error_type))
        if type(self.succeeded) is not bool or type(self.verified) is not bool:
            raise ValueError("execution and verification flags must be booleans")
        if not isinstance(self.output, str):
            raise ValueError("tool output must be text")
        if self.effect_status not in {"known", "unknown"}:
            raise ValueError("invalid effect status")
        if self.effect_status == "unknown" and (self.succeeded or self.verified):
            raise ValueError("an uncertain effect cannot be declared successful")
        if self.operation_id is not None and (
            not isinstance(self.operation_id, str)
            or re.fullmatch(r"[a-f0-9]{32}", self.operation_id) is None
        ):
            raise ValueError("invalid operation identity")
        if self.verified and not self.succeeded:
            raise ValueError("verification requires successful execution")


@dataclass(frozen=True)
class RunContext:
    run_id: str
    session_id: str
    message: str
    offered_tools: tuple[str, ...]
    tool_results: tuple[ToolResult, ...] = ()


class Provider(Protocol):
    async def next_step(self, context: RunContext) -> FinalAnswer | ToolProposal: ...


@dataclass(frozen=True)
class ApprovalRequest:
    run_id: str
    session_id: str
    operation_id: str
    tool_name: str
    arguments_json: str
    digest: str


@dataclass(frozen=True)
class ApprovalGrant:
    digest: str


class ApprovalGate(Protocol):
    async def request(
        self,
        operation: ApprovalRequest,
        cancellation: asyncio.Event,
    ) -> ApprovalGrant | None: ...


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    validate: Callable[[Mapping[str, Any]], None]
    execute: Callable[[Mapping[str, Any], asyncio.Event], Awaitable[ToolResult]]
    requires_confirmation: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or re.fullmatch(r"[a-z][a-z0-9_]{2,63}", self.name) is None
        ):
            raise ValueError("invalid tool name")
        if type(self.requires_confirmation) is not bool:
            raise ValueError("requires_confirmation must be a boolean")


class RunPhase(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    CLEANING = "cleaning"
    SETTLED = "settled"


@dataclass(frozen=True)
class CancelReceipt:
    run_id: str
    accepted: bool


@dataclass(frozen=True)
class RunResult:
    """Completion labels the model turn; action evidence remains in separate receipts."""

    run_id: str
    status: Literal["completed", "failed", "cancelled"]
    answer: str | None
    tool_results: tuple[ToolResult, ...]
    error: str | None = None
    answer_source: Literal["model"] | None = None
    error_type: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "error_type", _safe_error_type(self.error_type))

    @property
    def action_receipts(self) -> tuple[ToolResult, ...]:
        return self.tool_results


class RunHandle:
    def __init__(self, context: RunContext) -> None:
        self._run_id = context.run_id
        self._session_id = context.session_id
        self._phase = RunPhase.QUEUED
        self._cancellation = asyncio.Event()
        self._settled: asyncio.Future[RunResult] = asyncio.get_running_loop().create_future()
        self._task: asyncio.Task[None] | None = None
        self._entered = False

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def phase(self) -> RunPhase:
        return self._phase

    def cancel(self) -> CancelReceipt:
        if self._settled.done() or self._cancellation.is_set():
            return CancelReceipt(self.run_id, accepted=False)
        self._cancellation.set()
        # Repeated requests acknowledge the existing cancellation, rather than
        # interrupting a finalizer that still owns the session's resources.
        if self._entered and self._phase is not RunPhase.CLEANING and self._task is not None:
            self._task.cancel()
        return CancelReceipt(self.run_id, accepted=True)

    async def await_settled(self) -> RunResult:
        # Cancelling one observer must not cancel the shared result or the run.
        return await asyncio.shield(self._settled)


class _RunFailure(Exception):
    def __init__(self, code: str, *, error_type: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.error_type = _safe_error_type(error_type)


def _arguments_json(value: Mapping[str, Any]) -> str:
    def check(item: Any) -> None:
        if item is None or type(item) in {str, bool, int}:
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for entry in item:
                check(entry)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for entry in item.values():
                check(entry)
            return
        raise _RunFailure("invalid_arguments")

    if type(value) is not dict:
        raise _RunFailure("invalid_arguments")
    check(value)
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


class AgentRuntime:
    def __init__(
        self,
        provider: Provider,
        tools: Sequence[ToolDefinition] = (),
        *,
        approval_gate: ApprovalGate | None = None,
        max_steps: int = 8,
        cleanup: Callable[[RunContext], Awaitable[None]] | None = None,
        execution_scope: Callable[[ApprovalRequest, ApprovalGrant], AbstractContextManager]
        | None = None,
        on_tool_result: Callable[[RunContext, ToolResult], None] | None = None,
    ) -> None:
        if type(max_steps) is not int or not 1 <= max_steps <= 100:
            raise ValueError("max_steps must be between 1 and 100")
        if len({tool.name for tool in tools}) != len(tools):
            raise ValueError("duplicate tool name")
        self._provider = provider
        self._tools = MappingProxyType({tool.name: tool for tool in tools})
        self._approval_gate = approval_gate
        self._max_steps = max_steps
        self._cleanup = cleanup
        self._execution_scope = execution_scope
        self._on_tool_result = on_tool_result
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._failed_cleanup_sessions: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    def start(
        self,
        session_id: str,
        message: str,
        *,
        offered_tools: Sequence[str] | None = None,
        run_id: str | None = None,
    ) -> RunHandle:
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be nonempty")
        if not isinstance(message, str):
            raise ValueError("message must be text")
        names = tuple(self._tools) if offered_tools is None else tuple(offered_tools)
        if len(set(names)) != len(names) or any(name not in self._tools for name in names):
            raise ValueError("offered tools must be distinct registered names")
        if run_id is not None and (
            not isinstance(run_id, str) or re.fullmatch(r"[a-f0-9]{32}", run_id) is None
        ):
            raise ValueError("invalid run identity")
        context = RunContext(run_id or uuid4().hex, session_id, message, names)
        handle = RunHandle(context)
        lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        task = asyncio.create_task(self._run(handle, context, lock))
        handle._task = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return handle

    @staticmethod
    def _check_cancelled(handle: RunHandle) -> None:
        if handle._cancellation.is_set():
            raise asyncio.CancelledError

    async def _run(self, handle: RunHandle, context: RunContext, lock: asyncio.Lock) -> None:
        handle._entered = True
        results: list[ToolResult] = []
        outcome = RunResult(context.run_id, "cancelled", None, ())
        try:
            self._check_cancelled(handle)
            async with lock:
                self._check_cancelled(handle)
                if context.session_id in self._failed_cleanup_sessions:
                    raise _RunFailure("session_cleanup_failed")
                handle._phase = RunPhase.RUNNING
                try:
                    answer = await self._steps(handle, context, results)
                    outcome = RunResult(
                        context.run_id, "completed", answer, tuple(results), answer_source="model"
                    )
                except asyncio.CancelledError as error:
                    outcome = RunResult(
                        context.run_id,
                        "cancelled",
                        None,
                        tuple(results),
                        error_type=type(error).__name__,
                    )
                except Exception as error:
                    code = error.code if isinstance(error, _RunFailure) else "execution_failed"
                    error_type = (
                        error.error_type
                        if isinstance(error, _RunFailure) and error.error_type
                        else type(error).__name__
                    )
                    outcome = RunResult(
                        context.run_id, "failed", None, tuple(results), code, error_type=error_type
                    )
                finally:
                    handle._phase = RunPhase.CLEANING
                    if self._cleanup is not None:
                        final_context = RunContext(
                            context.run_id,
                            context.session_id,
                            context.message,
                            context.offered_tools,
                            tuple(results),
                        )
                        try:
                            cancellation_type = await self._finish_cleanup(final_context)
                            if cancellation_type and outcome.error_type is None:
                                outcome = replace(outcome, error_type=cancellation_type)
                        except BaseException as error:
                            self._failed_cleanup_sessions.add(context.session_id)
                            outcome = RunResult(
                                context.run_id,
                                "failed",
                                None,
                                tuple(results),
                                "cleanup_failed",
                                error_type=type(error).__name__,
                            )
                    if handle._cancellation.is_set() and outcome.error != "cleanup_failed":
                        outcome = RunResult(
                            context.run_id,
                            "cancelled",
                            None,
                            tuple(results),
                            error_type=outcome.error_type,
                        )
        except asyncio.CancelledError as error:
            outcome = RunResult(
                context.run_id, "cancelled", None, tuple(results), error_type=type(error).__name__
            )
        except _RunFailure as error:
            outcome = RunResult(
                context.run_id,
                "failed",
                None,
                tuple(results),
                error.code,
                error_type=error.error_type or type(error).__name__,
            )
        finally:
            handle._phase = RunPhase.SETTLED
            handle._settled.set_result(outcome)

    async def _finish_cleanup(self, context: RunContext) -> str | None:
        assert self._cleanup is not None
        task = asyncio.create_task(self._cleanup(context))
        cancellation_type = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as error:
                # The cancellation receipt already exists; retain session ownership
                # until the finalizer really finishes, even under another cancel.
                cancellation_type = _safe_error_type(type(error).__name__)
        task.result()
        return cancellation_type

    async def _steps(
        self,
        handle: RunHandle,
        context: RunContext,
        results: list[ToolResult],
    ) -> str:
        for _ in range(self._max_steps):
            self._check_cancelled(handle)
            current = RunContext(
                context.run_id,
                context.session_id,
                context.message,
                context.offered_tools,
                tuple(results),
            )
            step = await self._provider.next_step(current)
            self._check_cancelled(handle)
            if isinstance(step, FinalAnswer):
                if not isinstance(step.text, str) or not step.text.strip():
                    raise _RunFailure("invalid_final_answer")
                return step.text
            if not isinstance(step, ToolProposal) or step.tool_name not in context.offered_tools:
                raise _RunFailure("tool_not_offered")
            await self._tool(handle, context, step, results)
        raise _RunFailure("step_limit")

    async def _tool(
        self,
        handle: RunHandle,
        context: RunContext,
        proposal: ToolProposal,
        results: list[ToolResult],
    ) -> None:
        tool = self._tools[proposal.tool_name]
        operation_id = uuid4().hex
        snapshot = _arguments_json(proposal.arguments)
        arguments = json.loads(snapshot)
        try:
            tool.validate(arguments)
        except Exception as error:
            raise _RunFailure("invalid_arguments", error_type=type(error).__name__) from error
        identity = json.dumps(
            {
                "run": context.run_id,
                "session": context.session_id,
                "operation": operation_id,
                "tool": tool.name,
                "arguments_json": snapshot,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(identity.encode()).hexdigest()
        operation = ApprovalRequest(
            context.run_id, context.session_id, operation_id, tool.name, snapshot, digest
        )
        grant = None
        if tool.requires_confirmation:
            if self._approval_gate is None:
                raise _RunFailure("approval_required")
            handle._phase = RunPhase.WAITING_APPROVAL
            grant = await self._approval_gate.request(operation, handle._cancellation)
            handle._phase = RunPhase.RUNNING
            self._check_cancelled(handle)
            if not isinstance(grant, ApprovalGrant) or grant.digest != digest:
                raise _RunFailure("approval_denied")
        self._check_cancelled(handle)
        try:
            tool.validate(arguments)
        except Exception as error:
            raise _RunFailure("invalid_arguments", error_type=type(error).__name__) from error
        if (
            _arguments_json(arguments) != snapshot
            or _arguments_json(proposal.arguments) != snapshot
        ):
            raise _RunFailure("arguments_changed")

        def record_uncertain(error: str, error_type: str | None = None) -> None:
            receipt = ToolResult(
                tool.name,
                succeeded=False,
                verified=False,
                error=error,
                effect_status="unknown",
                operation_id=operation_id,
                error_type=error_type,
            )
            results.append(receipt)
            if self._on_tool_result:
                self._on_tool_result(context, receipt)

        # Once dispatched, an interrupted callback may already have caused an
        # effect. Preserve uncertainty even if it suppresses cancellation and
        # returns a seemingly successful value. Never replay that effect.
        with ExitStack() as scope:
            if tool.requires_confirmation and self._execution_scope:
                try:
                    scope.enter_context(self._execution_scope(operation, grant))
                except Exception as error:
                    raise _RunFailure(
                        "approval_invalidated", error_type=type(error).__name__
                    ) from error
            try:
                result = await tool.execute(_freeze(arguments), handle._cancellation)
            except asyncio.CancelledError as error:
                record_uncertain("execution_cancelled", type(error).__name__)
                raise
            except Exception as error:
                record_uncertain("tool_execution_failed", type(error).__name__)
                raise _RunFailure(
                    "tool_execution_failed", error_type=type(error).__name__
                ) from error
        if handle._cancellation.is_set():
            cancellation = asyncio.CancelledError()
            record_uncertain("execution_cancelled", type(cancellation).__name__)
            raise cancellation
        if (
            not isinstance(result, ToolResult)
            or result.tool_name != tool.name
            or result.operation_id not in {None, operation_id}
        ):
            record_uncertain("invalid_tool_result")
            raise _RunFailure("invalid_tool_result")
        receipt = replace(result, operation_id=operation_id)
        results.append(receipt)
        if self._on_tool_result:
            self._on_tool_result(context, receipt)
        if result.effect_status == "unknown":
            raise _RunFailure("tool_outcome_unknown")
