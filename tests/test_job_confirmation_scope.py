import asyncio
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from test_jobs import PendingNetworkGraph, _awaiting_confirmation, _terminal

from aegis_core.brain.nvidia_client import NvidiaBrainClient
from aegis_core.config import Settings
from aegis_core.contracts import AgentResult, AgentRole, PolicyDecision, UserRequest
from aegis_core.job_contracts import JobConfirmationError, JobStatus
from aegis_core.jobs import SwarmJobManager
from aegis_core.providers.nvidia import NvidiaNimClient
from aegis_core.tools.broker import PolicyContext
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context
from aegis_core.tools.execution import ReadOnlyToolExecutor


def runtime(tmp_path):
    broker = build_default_tool_broker()
    base = default_policy_context(tmp_path)
    grants = OneTimeConfirmationStore()
    context = PolicyContext(
        workspace_root=tmp_path, network_scopes=base.network_scopes, confirmation_store=grants
    )
    return broker, context, grants


@pytest.mark.parametrize("channel", ["local", "telegram", "voice"])
async def test_nvidia_reused_wire_id_requires_new_approval_without_reapplying_old_grant(
    tmp_path, channel
):
    broker, context, grants = runtime(tmp_path)
    wire_ids, executions = [], []

    async def endpoint(request):
        wire_ids.append("remote-call-0")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "remote-call-0",
                                    "type": "function",
                                    "function": {
                                        "name": "network_discover_hosts",
                                        "arguments": '{"target":"127.0.0.1","ports":[443]}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        )

    settings = Settings.model_construct(model_preferences_path=tmp_path / "models.json")
    async with NvidiaNimClient(
        settings, lambda: "synthetic", transport=httpx.MockTransport(endpoint)
    ) as provider:
        brain = NvidiaBrainClient(provider)

        class EndpointGraph:
            def __init__(self):
                self.authorizations = []

            async def ainvoke(self, input):
                specialist = await brain.complete_nvidia_only(
                    role=AgentRole.CODE_SECURITY,
                    messages=[{"role": "user", "content": input["request"].text}],
                    extra_body={
                        "tools": broker.schemas_for(
                            AgentRole.CODE_SECURITY, names=frozenset({"network_discover_hosts"})
                        ),
                        "tool_choice": {
                            "type": "function",
                            "function": {"name": "network_discover_hosts"},
                        },
                    },
                )
                authorization = broker.authorize(
                    specialist.tool_calls[0], context, request=input["request"]
                )
                self.authorizations.append(authorization)
                result = {"specialist_result": specialist, "tool_authorizations": (authorization,)}
                if authorization.decision is PolicyDecision.DENY:
                    result["final_result"] = AgentResult(
                        role=AgentRole.SYNTHESIZER,
                        model_id="fixture/denied",
                        content="No se puede repetir esta llamada.",
                    )
                return result

        class VoiceVerifier:
            async def verify(self, *args, **kwargs):
                return SimpleNamespace(
                    authorized=True, speaker_verified=True, semantic_verified=True
                )

        def connector(*args):
            executions.append(args)
            return "closed"

        graph = EndpointGraph()
        jobs = SwarmJobManager(
            graph,
            tool_broker=broker,
            policy_context=context,
            confirmation_store=grants,
            voice_confirmation_verifier=VoiceVerifier(),
            tool_executor=ReadOnlyToolExecutor(tcp_connector=connector),
        )

        async def approve(job_id, digest):
            if channel == "telegram":
                return await jobs.approve_from_telegram(job_id, digest, owner_id=123)
            if channel == "voice":
                return await jobs.approve_by_voice(
                    job_id=job_id,
                    call_digest=digest,
                    pcm=b"\0" * 8000,
                    speaker_identifier="owner",
                    speaker_confidence=0.9,
                    owner_profile_match=True,
                )
            return await jobs.approve(job_id, digest)

        try:
            first = await jobs.submit(UserRequest(text="Consulta sintética uno"))
            pending = await _awaiting_confirmation(jobs, first.job_id)
            await approve(first.job_id, pending.confirmation.call_digest)
            assert (await _terminal(jobs, first.job_id)).status is JobStatus.COMPLETED
            with pytest.raises(JobConfirmationError):
                await approve(first.job_id, pending.confirmation.call_digest)
            second_request = UserRequest(text="Consulta sintética dos")
            second = await jobs.submit(second_request)
            pending_again = await _awaiting_confirmation(jobs, second.job_id)
            assert first.request_id != second.request_id
            assert pending.confirmation.call_digest == pending_again.confirmation.call_digest
            assert len(executions) == 1  # The old grant never executes this new request.
            await approve(second.job_id, pending_again.confirmation.call_digest)
            assert (await _terminal(jobs, second.job_id)).status is JobStatus.COMPLETED
            assert len(executions) == 2
            # Reusing that request identity cannot obtain or execute another grant.
            repeated = await jobs.submit(second_request)
            await _terminal(jobs, repeated.job_id)
            assert graph.authorizations[-1].reason_code == "confirmation_replayed"
            assert len(executions) == 2
            assert wire_ids == ["remote-call-0"] * 3
        finally:
            await jobs.close()


def short_approval_lifetime(monkeypatch):
    from aegis_core import job_lifecycle, job_state, jobs

    for module in (job_lifecycle, job_state, jobs):
        monkeypatch.setattr(module, "PENDING_CONFIRMATION_TTL", timedelta(seconds=0.03))


async def test_orphan_approval_expires_without_any_client_status_poll(tmp_path, monkeypatch):
    short_approval_lifetime(monkeypatch)
    broker, context, grants = runtime(tmp_path)
    jobs = SwarmJobManager(
        PendingNetworkGraph(broker, context),
        tool_broker=broker,
        policy_context=context,
        confirmation_store=grants,
    )
    try:
        submitted = await jobs.submit(UserRequest(text="Consulta sintética"))
        pending = await _awaiting_confirmation(jobs, submitted.job_id)
        # The client disconnects after receiving the decision. No status, wait,
        # approval or cancellation call runs during or after its expiry.
        await asyncio.sleep(0.07)
        assert not jobs.has_active_jobs
        snapshot = jobs._lifecycle.snapshot(submitted.job_id)
        assert snapshot.status is JobStatus.FAILED
        assert snapshot.error_code == "confirmation_expired"
        assert not jobs._confirmation_expiries and not jobs._confirmation_expiry_tasks
        with pytest.raises(JobConfirmationError):
            await jobs.approve(submitted.job_id, pending.confirmation.call_digest)
    finally:
        await jobs.close()


@pytest.mark.parametrize("decision", ["approve", "cancel", "close"])
async def test_confirmation_timer_is_cancelled_and_joined_after_decision(
    tmp_path, monkeypatch, decision
):
    short_approval_lifetime(monkeypatch)
    broker, context, grants = runtime(tmp_path)
    executor = ReadOnlyToolExecutor(tcp_connector=lambda *_: "closed")
    jobs = SwarmJobManager(
        PendingNetworkGraph(broker, context),
        tool_broker=broker,
        policy_context=context,
        confirmation_store=grants,
        tool_executor=executor,
    )
    try:
        submitted = await jobs.submit(UserRequest(text="Consulta sintética"))
        pending = await _awaiting_confirmation(jobs, submitted.job_id)
        if decision == "approve":
            await jobs.approve(submitted.job_id, pending.confirmation.call_digest)
            assert (await _terminal(jobs, submitted.job_id)).status is JobStatus.COMPLETED
        elif decision == "cancel":
            assert (await jobs.cancel(submitted.job_id)).status is JobStatus.CANCELLED
        else:
            await jobs.close()
            assert jobs._lifecycle.snapshot(submitted.job_id).status is JobStatus.CANCELLED
        await asyncio.sleep(0.05)
        assert not jobs._confirmation_expiries and not jobs._confirmation_expiry_tasks
    finally:
        await jobs.close()
