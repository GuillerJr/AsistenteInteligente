"""Synthetic browser-task orchestration; no browser, network or owner content."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from aegis_core.contracts import (
    AgentResult,
    AgentRole,
    Capability,
    PolicyDecision,
    RiskLevel,
    ToolCall,
    ToolExecutionResult,
    UserRequest,
)
from aegis_core.job_contracts import JobStatus
from aegis_core.jobs import SwarmJobManager
from aegis_core.mcp.browser_tasks import (
    BrowserTaskActionArguments,
    BrowserTaskCloseArguments,
    BrowserTaskOpenArguments,
    is_browser_task_request,
)
from aegis_core.orchestration.graph import build_swarm_graph
from aegis_core.tools.broker import PolicyContext, PolicyViolation, ToolDefinition
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker
from aegis_core.tools.execution import ReadOnlyToolExecutor

REQUEST = "Abre https://example.com y selecciona Informes en mi navegador"


class SyntheticBrowser:
    def __init__(self, *, partial=False, block=False):
        self.contexts = {}
        self.calls = []
        self.cleaned = []
        self.partial = partial
        self.block = block
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    def names_for_request(self, request):
        if request.request_id in self.contexts:
            return frozenset({"browser_task_action", "browser_task_close"})
        return frozenset({"browser_task_open"}) if is_browser_task_request(request) else frozenset()

    def context_for_request(self, request_id):
        data = self.contexts.get(request_id)
        return dict(data) if data else None

    def guard(self, arguments, context):
        if not isinstance(context.request_id, UUID) or not context.request_text:
            raise PolicyViolation("request_context_missing")
        if isinstance(arguments, BrowserTaskOpenArguments):
            if arguments.objective != context.request_text:
                raise PolicyViolation("original_objective_missing")
        else:
            current = self.contexts.get(context.request_id)
            if current is None or arguments.session_id != UUID(current["session_id"]):
                raise PolicyViolation("foreign_session")
            if isinstance(arguments, BrowserTaskActionArguments) and (
                arguments.expected_url != current["expected_url"]
                or arguments.snapshot_sha256 != current["snapshot_sha256"]
            ):
                raise PolicyViolation("stale_snapshot")
        return arguments

    def definitions(self):
        roles = frozenset({AgentRole.PLANNER, AgentRole.CODE_SECURITY})
        return tuple(
            ToolDefinition(
                name=name,
                description="Synthetic browser boundary",
                arguments_model=model,
                capability=Capability.APPLICATION_CONTROL,
                risk=risk,
                allowed_roles=roles,
                requires_confirmation=confirm,
                argument_guard=self.guard,
            )
            for name, model, risk, confirm in (
                ("browser_task_open", BrowserTaskOpenArguments, RiskLevel.HIGH, True),
                ("browser_task_action", BrowserTaskActionArguments, RiskLevel.MEDIUM, False),
                ("browser_task_close", BrowserTaskCloseArguments, RiskLevel.MEDIUM, False),
            )
        )

    async def handle(self, authorization, context):
        await asyncio.sleep(0)
        self.calls.append((authorization, context))
        if authorization.tool_name == "browser_task_open":
            assert authorization.reason_code == "confirmation_consumed"
            data = {
                "session_id": str(uuid4()),
                "task_request_id": str(context.request_id),
                "expected_url": authorization.normalized_arguments["url"],
                "snapshot_sha256": hashlib.sha256(b"initial").hexdigest(),
                "snapshot": '- button "Informes" [ref=e1]',
                "verified": True,
                "goal_verified": False,
                "verification_scope": "browser_task_access",
                "scope": "browser_task",
                "remaining_actions": 24,
            }
            self.contexts[context.request_id] = data
        elif authorization.tool_name == "browser_task_action":
            self.guard(
                BrowserTaskActionArguments.model_validate(authorization.normalized_arguments),
                context,
            )
            self.started.set()
            if self.block:
                await self.release.wait()
            data = dict(self.contexts[context.request_id])
            data.update(
                snapshot_sha256=hashlib.sha256(str(len(self.calls)).encode()).hexdigest(),
                verified=not self.partial,
                goal_verified=not self.partial,
                verification_scope="new_accessible_literal",
                snapshot='- heading "Informes"',
                action_executed=True,
            )
            if self.partial:
                self.contexts.pop(context.request_id)
                data["outcome"] = "action_performed"
            else:
                self.contexts[context.request_id] = data
        else:
            data = self.contexts.pop(context.request_id)
            data.update(closed=True, goal_verified=False,
                        verification_scope="browser_task_disconnect")
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=True,
            output=json.dumps({"structuredContent": data, "isError": False}),
            metadata={
                "verified": data["verified"],
                "goal_verified": data["goal_verified"],
                "verification_scope": data["verification_scope"],
                "status": "partial" if self.partial else "completed",
            },
        )

    async def finish_request(self, request_id):
        self.cleaned.append(request_id)
        self.contexts.pop(request_id, None)


class BrowserProvider:
    def __init__(self, browser, *, repeat=1, wrong_url=False, foreign=None, unrequested=None):
        self.browser = browser
        self.repeat = repeat
        self.wrong_url = wrong_url
        self.foreign = foreign
        self.unrequested = unrequested
        self.requests = []
        self.actions = 0
        self.actions_by_request = {}

    async def complete_nvidia_only(self, **kwargs):
        self.requests.append(kwargs)
        role = kwargs["role"]
        if role is AgentRole.SYNTHESIZER:
            return AgentResult(
                role=role, model_id="synthetic/nvidia", content="Resultado observado."
            )
        request_id = kwargs["request_id"]
        context = self.browser.context_for_request(request_id)
        calls = ()
        if context is None:
            calls = (
                ToolCall(
                    call_id="open",
                    requested_by=role,
                    tool_name="browser_task_open",
                    arguments={
                        "url": "https://other.example" if self.wrong_url else "https://example.com",
                        "objective": "Model invented a different objective",
                    },
                ),
            )
        elif self.actions_by_request.get(request_id, 0) < self.repeat:
            self.actions += 1
            self.actions_by_request[request_id] = self.actions_by_request.get(request_id, 0) + 1
            args = {
                "session_id": self.foreign or context["session_id"],
                "expected_url": context["expected_url"],
                "snapshot_sha256": context["snapshot_sha256"],
                "operation": "click",
                "target": "e1",
                "verify_text": self.unrequested
                or ("Reportes" if "Reportes" in kwargs["messages"][-1]["content"] else "Informes"),
            }
            calls = (
                ToolCall(
                    call_id=f"action-{self.actions}",
                    requested_by=role,
                    tool_name="browser_task_action",
                    arguments=args,
                ),
            )
        return AgentResult(role=role, model_id="synthetic/nvidia", content="Fin.", tool_calls=calls)


def setup(tmp_path, browser, provider, **job_options):
    grants = OneTimeConfirmationStore()
    context = PolicyContext(workspace_root=tmp_path, confirmation_store=grants)
    broker = build_default_tool_broker(browser.definitions())
    executor = ReadOnlyToolExecutor(
        extra_async_handlers={
            name: browser.handle
            for name in ("browser_task_open", "browser_task_action", "browser_task_close")
        }
    )
    graph = build_swarm_graph(
        provider,
        tool_broker=broker,
        policy_context=context,
        tool_executor=executor,
        inference_nvidia_only=True,
        dynamic_tool_names=browser.names_for_request,
        browser_task_context=browser.context_for_request,
    )
    jobs = SwarmJobManager(
        graph,
        tool_broker=broker,
        policy_context=context,
        confirmation_store=grants,
        tool_executor=executor,
        request_cleanup=browser.finish_request,
        **job_options,
    )
    return graph, jobs, context, broker


async def wait_status(jobs, job_id, statuses):
    for _ in range(400):
        result = await jobs.status(job_id)
        if result.status in statuses:
            return result
        await asyncio.sleep(0.001)
    raise AssertionError("bounded synthetic job did not reach expected state")


async def open_and_approve(jobs, request):
    submitted = await jobs.submit(request)
    waiting = await wait_status(jobs, submitted.job_id, {JobStatus.AWAITING_CONFIRMATION})
    assert waiting.confirmation.tool_name == "browser_task_open"
    await jobs.approve(submitted.job_id, waiting.confirmation.call_digest)
    return submitted.job_id


async def test_browser_open_approval_then_snapshot_action_and_done_preserves_request(tmp_path):
    browser = SyntheticBrowser()
    provider = BrowserProvider(browser)
    _, jobs, original_context, _ = setup(tmp_path, browser, provider)
    request = UserRequest(text=REQUEST)
    try:
        submitted = await jobs.submit(request)
        waiting = await wait_status(jobs, submitted.job_id, {JobStatus.AWAITING_CONFIRMATION})
        assert not browser.calls and not browser.cleaned
        await jobs.approve(submitted.job_id, waiting.confirmation.call_digest)
        final = await wait_status(jobs, submitted.job_id, {JobStatus.COMPLETED, JobStatus.FAILED})
        assert final.status is JobStatus.COMPLETED
        assert [call.tool_name for call, _ in browser.calls] == [
            "browser_task_open",
            "browser_task_action",
        ]
        assert all(
            ctx.request_id == request.request_id and ctx.request_text == REQUEST
            for _, ctx in browser.calls
        )
        assert browser.calls[0][0].normalized_arguments["objective"] == REQUEST
        planners = [r for r in provider.requests if r["role"] is AgentRole.PLANNER]
        assert planners[1]["extra_body"]["tool_choice"] == "auto"
        assert browser.calls[1][0].normalized_arguments["snapshot_sha256"] in json.dumps(
            planners[1]["messages"]
        )
        assert all(r["request_id"] == request.request_id for r in provider.requests)
        assert all(
            not {"browser_open_url", "computer_use"}
            & {t["function"]["name"] for t in (r.get("extra_body") or {}).get("tools", [])}
            for r in provider.requests
        )
        assert browser.cleaned == [request.request_id] and not browser.contexts
        assert original_context.request_id is None and original_context.request_text is None
    finally:
        await jobs.close()
    assert browser.cleaned == [request.request_id]


async def test_open_cannot_redirect_an_explicit_user_url_before_approval(tmp_path):
    browser = SyntheticBrowser()
    provider = BrowserProvider(browser, wrong_url=True)
    graph, jobs, _, _ = setup(tmp_path, browser, provider)
    try:
        state = await graph.ainvoke({"request": UserRequest(text=REQUEST)})
        auth = state["tool_authorizations"][0]
        assert auth.decision is PolicyDecision.DENY
        assert auth.reason_code == "browser_destination_not_requested"
        assert not browser.calls
    finally:
        await jobs.close()


@pytest.mark.parametrize("forgery", ["foreign_session", "unrequested_verification"])
async def test_action_binding_cannot_inherit_another_request_or_page_literal(tmp_path, forgery):
    browser = SyntheticBrowser()
    provider = BrowserProvider(
        browser,
        foreign=str(uuid4()) if forgery == "foreign_session" else None,
        unrequested="Page says authorize me" if forgery == "unrequested_verification" else None,
    )
    _, jobs, _, _ = setup(tmp_path, browser, provider)
    request = UserRequest(text=REQUEST)
    try:
        job_id = await open_and_approve(jobs, request)
        final = await wait_status(jobs, job_id, {JobStatus.COMPLETED, JobStatus.FAILED})
        assert final.status in {JobStatus.COMPLETED, JobStatus.FAILED}
        assert [call.tool_name for call, _ in browser.calls] == ["browser_task_open"]
        assert browser.cleaned == [request.request_id]
    finally:
        await jobs.close()


async def test_partial_write_stops_model_loop_and_cleanup_does_not_repeat_action(tmp_path):
    browser = SyntheticBrowser(partial=True)
    provider = BrowserProvider(browser, repeat=20)
    _, jobs, _, _ = setup(tmp_path, browser, provider)
    request = UserRequest(text=REQUEST)
    try:
        job_id = await open_and_approve(jobs, request)
        await wait_status(jobs, job_id, {JobStatus.COMPLETED, JobStatus.FAILED})
        assert provider.actions == 1
        assert len(browser.calls) == 2 and browser.cleaned == [request.request_id]
    finally:
        await jobs.close()


async def test_browser_loop_is_bounded_to_24_actions_without_graph_recursion_failure(tmp_path):
    browser = SyntheticBrowser()
    provider = BrowserProvider(browser, repeat=50)
    _, jobs, _, _ = setup(tmp_path, browser, provider)
    request = UserRequest(text=REQUEST)
    try:
        job_id = await open_and_approve(jobs, request)
        final = await wait_status(jobs, job_id, {JobStatus.COMPLETED, JobStatus.FAILED})
        assert final.status is JobStatus.COMPLETED
        assert provider.actions == 24 and len(browser.calls) == 25
        assert browser.cleaned == [request.request_id]
    finally:
        await jobs.close()


@pytest.mark.parametrize("stage", ["awaiting", "executing"])
async def test_browser_cancel_closes_original_request_once_at_each_stage(tmp_path, stage):
    browser = SyntheticBrowser(block=True)
    provider = BrowserProvider(browser)
    _, jobs, _, _ = setup(tmp_path, browser, provider)
    request = UserRequest(text=REQUEST)
    try:
        submitted = await jobs.submit(request)
        waiting = await wait_status(jobs, submitted.job_id, {JobStatus.AWAITING_CONFIRMATION})
        if stage == "executing":
            await jobs.approve(submitted.job_id, waiting.confirmation.call_digest)
            await asyncio.wait_for(browser.started.wait(), 1)
        cancelled = await jobs.cancel(submitted.job_id)
        assert cancelled.status is JobStatus.CANCELLED
        assert browser.cleaned == [request.request_id]
        assert not browser.contexts
        await jobs.cancel(submitted.job_id)
    finally:
        await jobs.close()
    assert browser.cleaned == [request.request_id]


async def test_browser_timeout_closes_original_request_without_retry(tmp_path):
    browser = SyntheticBrowser(block=True)
    provider = BrowserProvider(browser)
    _, jobs, _, _ = setup(tmp_path, browser, provider, execution_timeout_seconds=0.3)
    request = UserRequest(text=REQUEST)
    try:
        job_id = await open_and_approve(jobs, request)
        await asyncio.wait_for(browser.started.wait(), 1)
        final = await wait_status(jobs, job_id, {JobStatus.FAILED})
        assert final.error_code == "swarm_execution_timeout"
        assert len(browser.calls) == 2 and browser.cleaned == [request.request_id]
    finally:
        await jobs.close()


async def test_parallel_requests_receive_independent_execution_contexts(tmp_path):
    browser = SyntheticBrowser()
    provider = BrowserProvider(browser, repeat=100)
    graph, jobs, base, broker = setup(tmp_path, browser, provider)
    requests = [
        UserRequest(text=REQUEST),
        UserRequest(text=REQUEST.replace("Informes", "Reportes")),
    ]
    for request in requests:
        browser.contexts[request.request_id] = {
            "session_id": str(uuid4()),
            "task_request_id": str(request.request_id),
            "expected_url": "https://example.com/",
            "snapshot_sha256": "a" * 64,
            "verified": True,
            "goal_verified": False,
            "snapshot": "synthetic",
        }
    provider.repeat = 1
    # One request executes; another proposes a request-local literal that the
    # shared mock provider deliberately gets wrong. Neither can inherit context.
    try:
        await asyncio.gather(*(graph.ainvoke({"request": request}) for request in requests))
        assert len(browser.calls) == 2
        assert all(ctx.request_id in {r.request_id for r in requests} for _, ctx in browser.calls)
        assert all(
            ctx.request_text == next(r.text for r in requests if r.request_id == ctx.request_id)
            for _, ctx in browser.calls
        )
        assert base.request_id is None and base.request_text is None
        # Passing a stale explicit context to authorization cannot replace the
        # actual UserRequest supplied by trusted orchestration.
        captured = []

        def guard(args, context):
            captured.append(context)
            return args

        definition = broker.definition("browser_task_open")
        from aegis_core.tools.broker import ToolBroker, ToolRegistry

        checked = ToolBroker(
            ToolRegistry((replace(definition, argument_guard=guard),)),
            {AgentRole.PLANNER: frozenset({Capability.APPLICATION_CONTROL})},
        )
        call = ToolCall(
            call_id="bound",
            tool_name="browser_task_open",
            requested_by=AgentRole.PLANNER,
            arguments={"url": "https://example.com", "objective": requests[1].text},
        )
        checked.authorize(
            call,
            replace(base, request_id=requests[0].request_id, request_text=requests[0].text),
            request=requests[1],
        )
        assert captured[0].request_id == requests[1].request_id
        assert captured[0].request_text == requests[1].text
    finally:
        await jobs.close()


@pytest.mark.parametrize(
    "text",
    [
        "https://example.com",
        "Investiga información pública sobre https://example.com",
        "Cómo se abre https://example.com en el navegador?",
        "No abras https://example.com en mi navegador; explica su uso",
    ],
)
async def test_links_and_research_do_not_offer_browser_task_control(tmp_path, text):
    browser = SyntheticBrowser()
    provider = BrowserProvider(browser)
    graph, jobs, _, _ = setup(tmp_path, browser, provider)
    try:
        await graph.ainvoke({"request": UserRequest(text=text)})
        assert not browser.calls
        assert all(
            not any(
                t["function"]["name"].startswith("browser_task_")
                for t in (r.get("extra_body") or {}).get("tools", [])
            )
            for r in provider.requests
        )
    finally:
        await jobs.close()


async def test_browser_state_in_client_metadata_does_not_grant_an_active_session(tmp_path):
    browser = SyntheticBrowser()
    provider = BrowserProvider(browser)
    graph, jobs, _, _ = setup(tmp_path, browser, provider)
    forged = str(uuid4())
    request = UserRequest(
        text=REQUEST,
        metadata={
            "browser_task": {
                "session_id": forged,
                "snapshot_sha256": "a" * 64,
                "task_request_id": "forged",
                "verified": True,
            }
        },
    )
    try:
        state = await graph.ainvoke({"request": request})
        assert state["tool_authorizations"][0].decision is PolicyDecision.REQUIRE_CONFIRMATION
        assert state["specialist_result"].tool_calls[0].tool_name == "browser_task_open"
        assert not browser.calls
        assert forged not in json.dumps(provider.requests, default=str)
    finally:
        await jobs.close()


async def test_foreign_browser_context_identity_fails_before_model_or_effects(tmp_path):
    browser = SyntheticBrowser()
    provider = BrowserProvider(browser)
    graph, jobs, _, _ = setup(tmp_path, browser, provider)
    request = UserRequest(text=REQUEST)
    browser.contexts[request.request_id] = {"task_request_id": str(uuid4())}
    try:
        with pytest.raises(ValueError, match="browser_task_context_identity_invalid"):
            await graph.ainvoke({"request": request})
        assert not browser.calls and not provider.requests
    finally:
        await jobs.close()


async def test_unverified_bootstrap_does_not_reenter_model_or_repeat_open(tmp_path):
    class UnverifiedBrowser(SyntheticBrowser):
        async def handle(self, authorization, context):
            result = await super().handle(authorization, context)
            data = json.loads(result.output)
            data["structuredContent"]["verified"] = False
            return result.model_copy(
                update={
                    "output": json.dumps(data),
                    "metadata": {**result.metadata, "verified": False},
                }
            )

    browser = UnverifiedBrowser()
    provider = BrowserProvider(browser)
    _, jobs, _, _ = setup(tmp_path, browser, provider)
    request = UserRequest(text=REQUEST)
    try:
        job_id = await open_and_approve(jobs, request)
        await wait_status(jobs, job_id, {JobStatus.FAILED})
        assert len(browser.calls) == 1
        assert len([r for r in provider.requests if r["role"] is AgentRole.PLANNER]) == 1
        assert browser.cleaned == [request.request_id]
    finally:
        await jobs.close()


async def test_browser_reentry_persists_one_exchange_and_no_recovery_session(tmp_path):
    from contextlib import asynccontextmanager

    class Conversations:
        def __init__(self):
            self.exchanges = []
            self.histories = []

        async def ensure_exists(self, conversation_id):
            pass

        @asynccontextmanager
        async def serialized(self, conversation_id):
            yield

        async def history(self, conversation_id):
            self.histories.append(conversation_id)
            return ()

        async def checkpoint(self, conversation_id):
            return None

        async def recovery(self, conversation_id):
            return None

        async def prepare_context(self, *args):
            return None

        async def source_context(self, *args, **kwargs):
            return {}

        async def documents(self, conversation_id):
            return ()

        async def record_recovery(self, *args, **kwargs):
            raise AssertionError("ephemeral browser session must not enter recovery")

        async def record_exchange(self, conversation_id, **kwargs):
            self.exchanges.append((conversation_id, kwargs))
            return True

    conversations = Conversations()
    browser = SyntheticBrowser()
    provider = BrowserProvider(browser, repeat=3)
    _, jobs, _, _ = setup(tmp_path, browser, provider, conversations=conversations)
    request, conversation_id = UserRequest(text=REQUEST), uuid4()
    try:
        submitted = await jobs.submit(request, conversation_id=conversation_id)
        waiting = await wait_status(jobs, submitted.job_id, {JobStatus.AWAITING_CONFIRMATION})
        assert not conversations.exchanges
        await jobs.approve(submitted.job_id, waiting.confirmation.call_digest)
        final = await wait_status(jobs, submitted.job_id, {JobStatus.COMPLETED, JobStatus.FAILED})
        assert final.status is JobStatus.COMPLETED
        assert len(conversations.histories) == 2 and len(conversations.exchanges) == 1
        assert conversations.exchanges[0][1]["user_content"] == REQUEST
        assert all(r["request_id"] == request.request_id for r in provider.requests)
        assert browser.cleaned == [request.request_id]
    finally:
        await jobs.close()


async def test_confirmation_expiry_closes_request_autonomously_and_only_once(tmp_path, monkeypatch):
    from datetime import timedelta

    import aegis_core.job_lifecycle as lifecycle_module
    import aegis_core.job_state as state_module
    import aegis_core.jobs as jobs_module

    ttl = timedelta(seconds=0.03)
    for module in (jobs_module, lifecycle_module, state_module):
        monkeypatch.setattr(module, "PENDING_CONFIRMATION_TTL", ttl)
    browser = SyntheticBrowser()
    provider = BrowserProvider(browser)
    _, jobs, _, _ = setup(tmp_path, browser, provider)
    request = UserRequest(text=REQUEST)
    try:
        submitted = await jobs.submit(request)
        await wait_status(jobs, submitted.job_id, {JobStatus.AWAITING_CONFIRMATION})
        assert not browser.cleaned
        # Deliberately do not poll status: the manager-owned timer must clean up.
        await asyncio.sleep(0.08)
        assert browser.cleaned == [request.request_id]
        assert (await jobs.status(submitted.job_id)).status is JobStatus.FAILED
    finally:
        await jobs.close()
    assert browser.cleaned == [request.request_id]


async def test_close_joins_cleanup_even_when_cancel_waiter_is_cancelled(tmp_path):
    started, release = asyncio.Event(), asyncio.Event()
    browser = SyntheticBrowser()
    provider = BrowserProvider(browser)
    _, jobs, _, _ = setup(tmp_path, browser, provider)
    real_cleanup = jobs._request_cleanup

    async def slow_cleanup(request_id):
        started.set()
        await release.wait()
        await real_cleanup(request_id)

    jobs._request_cleanup = slow_cleanup
    request = UserRequest(text=REQUEST)
    submitted = await jobs.submit(request)
    await wait_status(jobs, submitted.job_id, {JobStatus.AWAITING_CONFIRMATION})
    cancelled = asyncio.create_task(jobs.cancel(submitted.job_id))
    await started.wait()
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    close = asyncio.create_task(jobs.close())
    await asyncio.sleep(0)
    assert not close.done() and not browser.cleaned
    release.set()
    await asyncio.wait_for(close, 1)
    assert browser.cleaned == [request.request_id]


async def test_pending_cleanup_has_bounded_admission_and_reused_request_id_is_rejected(tmp_path):
    from aegis_core.job_contracts import JobCapacityError, JobError

    started, release = asyncio.Event(), asyncio.Event()
    browser = SyntheticBrowser()
    provider = BrowserProvider(browser)
    _, jobs, _, _ = setup(tmp_path, browser, provider, max_jobs=1)
    real_cleanup = jobs._request_cleanup

    async def slow_cleanup(request_id):
        started.set()
        await release.wait()
        await real_cleanup(request_id)

    jobs._request_cleanup = slow_cleanup
    request = UserRequest(text=REQUEST)
    submitted = await jobs.submit(request)
    await wait_status(jobs, submitted.job_id, {JobStatus.AWAITING_CONFIRMATION})
    cancelled = asyncio.create_task(jobs.cancel(submitted.job_id))
    await started.wait()
    try:
        with pytest.raises(JobCapacityError):
            await jobs.submit(UserRequest(text=REQUEST))
        release.set()
        await cancelled
        with pytest.raises(JobError, match="identity"):
            await jobs.submit(request)
        assert browser.cleaned == [request.request_id]
    finally:
        release.set()
        await jobs.close()


async def test_browser_open_resumption_keeps_the_budget_spent_on_preparation(tmp_path):
    monotonic = [0.0]

    class SlowBootstrap(SyntheticBrowser):
        async def handle(self, authorization, context):
            if authorization.tool_name == "browser_task_open":
                await asyncio.sleep(0.04)
            return await super().handle(authorization, context)

    class PreparationProvider(BrowserProvider):
        async def complete_nvidia_only(self, **kwargs):
            result = await super().complete_nvidia_only(**kwargs)
            if kwargs["role"] is AgentRole.PLANNER and not self.browser.contexts:
                monotonic[0] += 0.025
            return result

    browser = SlowBootstrap()
    provider = PreparationProvider(browser)
    _, jobs, _, _ = setup(
        tmp_path,
        browser,
        provider,
        execution_timeout_seconds=0.045,
        monotonic_clock=lambda: monotonic[0],
    )
    request = UserRequest(text=REQUEST)
    try:
        job_id = await open_and_approve(jobs, request)
        final = await wait_status(jobs, job_id, {JobStatus.FAILED, JobStatus.COMPLETED})
        assert final.status is JobStatus.FAILED
        assert final.error_code == "swarm_execution_timeout"
        assert final.execution_budget_ms == 45
        assert not browser.calls and provider.actions == 0
        assert browser.cleaned == [request.request_id]
    finally:
        await jobs.close()


async def test_browser_task_coexistence_does_not_offer_legacy_mcp_or_skill_starter_bypass(tmp_path):
    from aegis_core.skills.registry import SkillRegistry, SkillStore

    browser = SyntheticBrowser()
    provider = BrowserProvider(browser)
    grants = OneTimeConfirmationStore()
    context = PolicyContext(workspace_root=tmp_path, confirmation_store=grants)
    legacy = ToolDefinition(
        name="mcp_playwright__browser_snapshot",
        description="Legacy browser snapshot",
        arguments_model=BrowserTaskCloseArguments,
        capability=Capability.APPLICATION_CONTROL,
        risk=RiskLevel.MEDIUM,
        allowed_roles=frozenset({AgentRole.PLANNER}),
    )
    broker = build_default_tool_broker((*browser.definitions(), legacy))
    skills = SkillRegistry(broker, store=SkillStore(tmp_path / "skills"))
    graph = build_swarm_graph(
        provider,
        tool_broker=broker,
        policy_context=context,
        inference_nvidia_only=True,
        skill_registry=skills,
        dynamic_tool_names=lambda request: browser.names_for_request(request) | {legacy.name},
        browser_task_context=browser.context_for_request,
    )
    state = await graph.ainvoke(
        {
            "request": UserRequest(
                text="Controla mi Mac en Chrome: abre https://example.com y selecciona Informes"
            )
        }
    )
    planners = [r for r in provider.requests if r["role"] is AgentRole.PLANNER]
    assert planners
    offered = {
        tool["function"]["name"]
        for request in planners
        for tool in (request.get("extra_body") or {}).get("tools", [])
    }
    assert "browser_task_open" in offered
    assert not offered & {legacy.name, "browser_open_url", "computer_use"}
    assert state["tool_authorizations"][0].decision is PolicyDecision.REQUIRE_CONFIRMATION
    assert not browser.calls


async def test_large_browser_result_keeps_bounded_provenance_through_done(tmp_path):
    from aegis_core.orchestration.graph import _bounded_browser_result_reference

    class LargeBrowser(SyntheticBrowser):
        def __init__(self):
            super().__init__()
            self.raw_result = None

        async def handle(self, authorization, context):
            result = await super().handle(authorization, context)
            if authorization.tool_name == "browser_task_action":
                payload = json.loads(result.output)
                payload["structuredContent"]["snapshot"] = (
                    '- heading "Informes"\n' + ('- paragraph "' + "\\" * 5 + '"\n') * 1_000
                )
                result = result.model_copy(update={"output": json.dumps(payload)})
                self.raw_result = result
            return result

    browser = LargeBrowser()
    provider = BrowserProvider(browser)
    _, jobs, _, _ = setup(tmp_path, browser, provider)
    try:
        job_id = await open_and_approve(jobs, UserRequest(text=REQUEST))
        final = await wait_status(jobs, job_id, {JobStatus.COMPLETED, JobStatus.FAILED})
        assert final.status is JobStatus.COMPLETED and final.result != "Fin."
        raw = browser.raw_result
        original_output = raw.output
        assert len(raw.model_dump_json().encode()) > 32_768
        reference = _bounded_browser_result_reference(raw)
        assert raw.output == original_output
        assert len(reference.model_dump_json().encode()) <= 16_384
        data = json.loads(reference.output)
        assert data["original_output_sha256"] == hashlib.sha256(raw.output.encode()).hexdigest()
        assert data["structuredContent"]["snapshot"].startswith('- heading "Informes"\n')
        assert data["structuredContent"]["snapshot_truncated"] is True
        assert data["structuredContent"]["excerpt_only"] is True
        assert data["structuredContent"]["goal_verified"] is True
        assert data["structuredContent"]["snapshot"].endswith("\n")
        synthesizers = [r for r in provider.requests if r["role"] is AgentRole.SYNTHESIZER]
        assert len(synthesizers) == 1
        reference_text = "\n".join(message["content"] for message in synthesizers[0]["messages"])
        assert data["original_output_sha256"] in reference_text
        assert "snapshot_truncated" in reference_text and "Informes" in reference_text
        assert browser.cleaned and len(browser.calls) == 2
    finally:
        await jobs.close()


def test_oversized_non_snapshot_evidence_fails_closed_without_mutating_result():
    from aegis_core.orchestration.graph import _bounded_browser_result_reference

    raw = ToolExecutionResult(
        call_id="bounded",
        tool_name="browser_task_action",
        success=True,
        output=json.dumps({"structuredContent": {"snapshot": "visible", "unknown": "a" * 40_000}}),
        metadata={"verified": True, "goal_verified": True},
    )
    reference = _bounded_browser_result_reference(raw)
    assert raw.success and len(raw.output) > 40_000
    assert not reference.success and reference.error_code == "browser_task_evidence_limit"
    assert reference.metadata["verified"] is False
    assert reference.metadata["goal_verified"] is False
    assert (
        reference.metadata["original_output_sha256"]
        == hashlib.sha256(raw.output.encode()).hexdigest()
    )
    assert len(reference.model_dump_json().encode()) <= 16_384


@pytest.mark.parametrize(
    "url", ["https://example.com", "https://EXAMPLE.com:443", "https://example.com/"],
)
async def test_canonical_literal_with_curly_quotes_stays_exact_before_approval(tmp_path, url):
    browser = SyntheticBrowser()
    provider = BrowserProvider(browser)
    _, jobs, _, _ = setup(tmp_path, browser, provider)
    request = UserRequest(text=f'Abre “{url}” y selecciona Informes en mi navegador')
    try:
        job_id = await open_and_approve(jobs, request)
        final = await wait_status(jobs, job_id, {JobStatus.COMPLETED, JobStatus.FAILED})
        assert final.status is JobStatus.COMPLETED
        authorization, context = browser.calls[0]
        assert authorization.normalized_arguments["url"] == "https://example.com/"
        assert authorization.normalized_arguments["objective"] == request.text
        assert context.request_text == request.text
        assert len(browser.calls) == 2 and browser.cleaned == [request.request_id]
    finally:
        await jobs.close()


async def test_curly_quoted_literal_cannot_be_substituted_with_another_destination(tmp_path):
    browser = SyntheticBrowser()
    provider = BrowserProvider(browser, wrong_url=True)
    graph, jobs, _, _ = setup(tmp_path, browser, provider)
    try:
        state = await graph.ainvoke({"request": UserRequest(text='Abre “https://example.com”')})
        authorization = state["tool_authorizations"][0]
        assert authorization.decision is PolicyDecision.DENY
        assert authorization.reason_code == "browser_destination_not_requested"
        assert not browser.calls
    finally:
        await jobs.close()


async def test_empty_model_pass_after_access_cannot_claim_objective_complete(tmp_path):
    browser = SyntheticBrowser()
    provider = BrowserProvider(browser, repeat=0)
    _, jobs, _, _ = setup(tmp_path, browser, provider)
    request = UserRequest(text=REQUEST)
    try:
        job_id = await open_and_approve(jobs, request)
        final = await wait_status(jobs, job_id, {JobStatus.COMPLETED, JobStatus.FAILED})
        assert final.status is JobStatus.COMPLETED
        assert final.result == (
            "Accedí al sitio aprobado. No ejecuté acciones adicionales verificadas."
        )
        assert [call.tool_name for call, _ in browser.calls] == ["browser_task_open"]
        assert browser.cleaned == [request.request_id]
        assert provider.actions == 0
    finally:
        await jobs.close()


@pytest.mark.parametrize("through_browser", [True, False])
async def test_browser_channel_does_not_depend_on_disconnected_google_peer(
    tmp_path, through_browser,
):
    browser = SyntheticBrowser()
    provider = BrowserProvider(browser, repeat=0)
    graph, jobs, _, _ = setup(tmp_path, browser, provider)
    text = "Revisa mis últimos cinco correos sin leer"
    if through_browser:
        text = "Abre https://example.com en mi navegador y " + text.casefold()
    try:
        state = await graph.ainvoke({"request": UserRequest(text=text)})
        if through_browser:
            authorization = state["tool_authorizations"][0]
            assert authorization.tool_name == "browser_task_open"
            assert authorization.decision is PolicyDecision.REQUIRE_CONFIRMATION
            assert authorization.normalized_arguments["objective"] == text
            assert "direct_local_result" not in state
        else:
            assert state["final_result"].model_id == "local/service-unavailable"
            assert "Google Workspace no está disponible" in state["final_result"].content
            assert not state.get("tool_authorizations") and not provider.requests
        assert not browser.calls
    finally:
        await jobs.close()


async def test_provider_repeated_open_cannot_reapprove_or_reopen_current_origin(tmp_path):
    class ReopeningProvider(BrowserProvider):
        async def complete_nvidia_only(self, **kwargs):
            context = self.browser.context_for_request(kwargs["request_id"])
            if context and kwargs["role"] is AgentRole.PLANNER:
                self.requests.append(kwargs)
                return AgentResult(
                    role=kwargs["role"], model_id="synthetic/nvidia", content="Tarea completa.",
                    tool_calls=(ToolCall(
                        call_id="reopen", requested_by=kwargs["role"],
                        tool_name="browser_task_open",
                        arguments={"url": "https://example.com/another-path", "objective": REQUEST},
                    ),),
                )
            return await super().complete_nvidia_only(**kwargs)

    browser = SyntheticBrowser()
    provider = ReopeningProvider(browser)
    _, jobs, _, _ = setup(tmp_path, browser, provider)
    request = UserRequest(text=REQUEST)
    try:
        job_id = await open_and_approve(jobs, request)
        final = await wait_status(
            jobs, job_id, {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.AWAITING_CONFIRMATION},
        )
        assert final.status is JobStatus.COMPLETED
        assert final.result == (
            "Accedí al sitio aprobado. No ejecuté acciones adicionales verificadas."
        )
        assert [call.tool_name for call, _ in browser.calls] == ["browser_task_open"]
        assert browser.cleaned == [request.request_id] and provider.actions == 0
        planners = [r for r in provider.requests if r["role"] is AgentRole.PLANNER]
        offered = {item["function"]["name"] for item in planners[-1]["extra_body"]["tools"]}
        assert {"browser_task_action", "browser_task_close"} <= offered
        assert "browser_task_open" not in offered
        assert "ALREADY been approved" in planners[-1]["messages"][0]["content"]
    finally:
        await jobs.close()
