"""Synthetic native payloads cross the real IPC, graph, approval and chat boundaries."""

from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from aegis_core.approval_signing import PinnedApprovalSigner, approval_signature_message
from aegis_core.computer_app_grants import ComputerAppGrantStore
from aegis_core.contracts import AgentResult, AgentRole, ToolCall, ToolExecutionResult
from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.job_contracts import TERMINAL_STATUSES, JobSnapshot, JobStatus
from aegis_core.job_ipc import SwarmIpcService
from aegis_core.jobs import SwarmJobManager
from aegis_core.memory.conversations import ConversationCoordinator, ConversationIpcService
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.orchestration.graph import build_nvidia_swarm_graph
from aegis_core.tools.audit import HashChainAuditLog
from aegis_core.tools.broker import PolicyContext
from aegis_core.tools.computer import ComputerUseReport
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker
from aegis_core.tools.execution import ReadOnlyToolExecutor

BUNDLE = "com.apple.TextEdit"
REQUEST = (
    "Controla la app TextEdit para escribir únicamente «PRUEBA JARVIS A» "
    "en el campo «First Text View» del documento nuevo vacío abierto. "
    "No abras otros documentos ni guardes."
)


class SyntheticPlanner:
    def __init__(self):
        self.literal = "PRUEBA JARVIS A"

    async def complete_nvidia_only(self, **kwargs):
        assert kwargs["role"] is AgentRole.PLANNER
        return AgentResult(
            role=AgentRole.PLANNER, model_id="synthetic/nvidia", content="Pendiente de aprobación",
            tool_calls=(ToolCall(
                call_id=str(uuid4()), tool_name="computer_use", requested_by=AgentRole.PLANNER,
                arguments={
                    "application_bundle_identifier": BUNDLE,
                    "objective": REQUEST.replace("PRUEBA JARVIS A", self.literal), "max_steps": 4,
                    "postconditions": [{"kind": "field_contains", "target": "First Text View",
                                        "value": self.literal}],
                },
            ),),
        )


class NoUnapprovedExecution(ReadOnlyToolExecutor):
    async def execute_async(self, authorization, context):
        pytest.fail("The graph must not execute computer_use before owner approval")


class SyntheticComputerExecutor:
    def __init__(self):
        self.started = asyncio.Queue()
        self.release = asyncio.Event()
        self.release.set()
        self.calls = 0
        self.cancelled = False

    async def execute_async(self, authorization, context):
        assert authorization.reason_code == "confirmation_consumed"
        self.calls += 1
        self.started.put_nowait(authorization.call_id)
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        report = ComputerUseReport(status="blocked", steps=0,
                                   application_bundle_identifier=BUNDLE,
                                   reason_code="sensitive_action")
        return ToolExecutionResult(call_id=authorization.call_id, tool_name="computer_use",
                                   success=True, output=report.model_dump_json())


@dataclass
class AppGrantIPC:
    conversations: ConversationIpcService
    coordinator: ConversationCoordinator
    service: SwarmIpcService
    jobs: SwarmJobManager
    authenticator: IpcAuthenticator
    signing_key: ec.EllipticCurvePrivateKey
    executor: SyntheticComputerExecutor
    planner: SyntheticPlanner
    audit: HashChainAuditLog
    grants: ComputerAppGrantStore

    async def call(self, method, payload):
        service = self.conversations if method.startswith("conversations.") else self.service
        return await service.handle(self.authenticator.create_request(method, payload))

    async def create_temporary(self):
        response = await self.call("conversations.create", {"title": "Chat temporal",
                                                           "temporary": True})
        assert response.ok
        return UUID(response.payload["conversation_id"])

    async def submit(self, conversation, text=REQUEST):
        # Same text-only shape emitted by ChatSubmission, with no client identity metadata.
        response = await self.call("swarm.submit", {"text": text,
                                                    "conversation_id": str(conversation)})
        assert response.ok
        return UUID(response.payload["job_id"])

    async def status_until(self, job_id, expected):
        async with asyncio.timeout(5):
            while True:
                response = await self.call("jobs.status", {"job_id": str(job_id)})
                assert response.ok
                snapshot = JobSnapshot.model_validate(response.payload)
                if snapshot.status is expected:
                    return snapshot
                assert snapshot.status not in TERMINAL_STATUSES, snapshot
                await asyncio.sleep(0.01)

    async def approve(self, job_id, digest, *, scope, signed_method=None):
        method = "jobs.approve.app." + scope
        nonce, issued = uuid4().hex, time.time_ns() // 1_000_000
        message = approval_signature_message(
            method=signed_method or method, job_id=job_id, call_digest=digest,
            approval_nonce=nonce, issued_at_milliseconds=issued,
        )
        return await self.call(method, {
            "job_id": str(job_id), "call_digest": digest, "approval_nonce": nonce,
            "issued_at_milliseconds": issued,
            "signature": base64.b64encode(
                self.signing_key.sign(message, ec.ECDSA(hashes.SHA256()))).decode(),
        })


@pytest.fixture
async def app_grant_ipc(tmp_path):
    tmp_path.chmod(0o700)
    memory = SQLiteMemoryStore(tmp_path / "memory.db", encryption_secret=b"x" * 32)
    memory.initialize()
    coordinator = ConversationCoordinator(memory, namespace="synthetic")
    conversations = ConversationIpcService(memory, coordinator)
    broker, confirmations = build_default_tool_broker(), OneTimeConfirmationStore()
    context = PolicyContext(workspace_root=tmp_path, confirmation_store=confirmations)
    audit = HashChainAuditLog(tmp_path / "audit.jsonl")
    grants = ComputerAppGrantStore(tmp_path / "app_grants.json")
    executor = SyntheticComputerExecutor()
    planner = SyntheticPlanner()
    graph = build_nvidia_swarm_graph(
        planner, tool_broker=broker, policy_context=context,
        tool_executor=NoUnapprovedExecution(),
    )
    jobs = SwarmJobManager(graph, conversations=coordinator, tool_broker=broker,
                           policy_context=context, confirmation_store=confirmations,
                           tool_executor=executor, audit_sink=audit, app_grants=grants)
    key = ec.generate_private_key(ec.SECP256R1())
    signer = PinnedApprovalSigner(tmp_path / "signer.pub")
    signer.register(key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint))
    try:
        yield AppGrantIPC(conversations, coordinator, SwarmIpcService(jobs, approval_signer=signer),
                          jobs, IpcAuthenticator(b"z" * 32), key, executor, planner, audit, grants)
    finally:
        await jobs.close()


@pytest.mark.parametrize("first_outcome", ["blocked", "cancelled"])
async def test_signed_temporary_chat_grant_survives_stopped_action_but_not_another_chat(
    app_grant_ipc, first_outcome,
):
    ipc = app_grant_ipc
    conversation = await ipc.create_temporary()
    first = await ipc.submit(conversation)
    pending = await ipc.status_until(first, JobStatus.AWAITING_CONFIRMATION)
    assert pending.confirmation.app_grant_bundle == BUNDLE
    digest = pending.confirmation.call_digest
    forged_scope = await ipc.approve(first, digest, scope="conversation",
                                     signed_method="jobs.approve")
    assert not forged_scope.ok and forged_scope.error_code == "approval_signature_invalid"
    assert ipc.executor.calls == 0 and not ipc.grants.allows(BUNDLE, conversation)
    if first_outcome == "cancelled":
        ipc.executor.release.clear()
    approved = await ipc.approve(first, digest, scope="conversation")
    assert approved.ok
    await asyncio.wait_for(ipc.executor.started.get(), timeout=5)
    if first_outcome == "cancelled":
        cancelled = await ipc.call("jobs.cancel", {"job_id": str(first)})
        assert cancelled.ok and cancelled.payload["status"] == "cancelled"
        assert ipc.executor.cancelled
        ipc.executor.release.set()
    else:
        completed = await ipc.status_until(first, JobStatus.COMPLETED)
        assert "acción era sensible" in completed.result
    assert ipc.grants.allows(BUNDLE, conversation)
    assert ipc.grants.entries() == ()  # Conversation scope never becomes an always grant.

    next_request = REQUEST
    if first_outcome == "cancelled":
        # Grant reuse must not bypass recovery's guard against repeating an action
        # whose execution was interrupted before a verified result was recorded.
        repeated = await ipc.submit(conversation)
        stopped = await ipc.status_until(repeated, JobStatus.COMPLETED)
        assert "interrumpió" in stopped.result and ipc.executor.calls == 1
        ipc.planner.literal = "PRUEBA JARVIS B"
        next_request = REQUEST.replace("PRUEBA JARVIS A", ipc.planner.literal)
    second = await ipc.submit(conversation, next_request)
    completed = await ipc.status_until(second, JobStatus.COMPLETED)
    assert "acción era sensible" in completed.result and ipc.executor.calls == 2
    assert await ipc.coordinator.is_temporary(conversation)
    other = await ipc.create_temporary()
    assert other != conversation
    third = await ipc.submit(other, next_request)
    await ipc.status_until(third, JobStatus.AWAITING_CONFIRMATION)
    assert ipc.executor.calls == 2
    events = [event.event_type for event in ipc.audit.verify()]
    assert events.count("computer_app_grant_created") == 1
    assert events.count("computer_app_grant_used") == 1


async def test_signed_always_grant_crosses_temporary_chats_and_revocation_restores_approval(
    app_grant_ipc,
):
    ipc = app_grant_ipc
    first_chat = await ipc.create_temporary()
    first = await ipc.submit(first_chat)
    pending = await ipc.status_until(first, JobStatus.AWAITING_CONFIRMATION)
    assert (await ipc.approve(first, pending.confirmation.call_digest, scope="always")).ok
    await ipc.status_until(first, JobStatus.COMPLETED)
    listed = await ipc.call("jobs.app_grants.list", {})
    assert listed.ok
    assert [grant["bundle_identifier"] for grant in listed.payload["grants"]] == [BUNDLE.lower()]

    second_chat = await ipc.create_temporary()
    assert second_chat != first_chat
    second = await ipc.submit(second_chat)
    await ipc.status_until(second, JobStatus.COMPLETED)
    assert ipc.executor.calls == 2
    revoked = await ipc.call("jobs.app_grants.revoke", {"bundle_identifier": BUNDLE})
    assert revoked.ok and revoked.payload == {"revoked": True}
    third = await ipc.submit(second_chat)
    await ipc.status_until(third, JobStatus.AWAITING_CONFIRMATION)
    assert ipc.executor.calls == 2
    events = [event.event_type for event in ipc.audit.verify()]
    assert events.count("computer_app_grant_created") == 1
    assert events.count("computer_app_grant_used") == 1
    assert events.count("computer_app_grant_revoked") == 1
