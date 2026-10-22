import asyncio
import base64
import time
from typing import Any
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from test_jobs import _awaiting_confirmation

from aegis_core.approval_signing import PinnedApprovalSigner, approval_signature_message
from aegis_core.computer_app_grants import AppGrantError, ComputerAppGrantStore
from aegis_core.contracts import AgentResult, AgentRole, InputModality, ToolCall, UserRequest
from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.job_authorization import AuthorizationConsumptionError
from aegis_core.job_contracts import JobConfirmationError, JobStatus
from aegis_core.job_ipc import SwarmIpcService
from aegis_core.jobs import SwarmJobManager
from aegis_core.tools.audit import HashChainAuditLog
from aegis_core.tools.broker import PolicyContext
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker
from aegis_core.tools.execution import ReadOnlyToolExecutor


class ComputerGraph:
    def __init__(self, broker, context, bundle: str) -> None:
        self.broker, self.context = broker, context
        self.call = ToolCall(call_id="call-computer", tool_name="computer_use",
                             requested_by=AgentRole.PLANNER, arguments={
                                 "objective": "Escribe hola en una nota nueva",
                                 "application_bundle_identifier": bundle})

    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]:
        del input
        specialist = AgentResult(role=AgentRole.PLANNER, model_id="fake", content="nota",
                                 tool_calls=(self.call,))
        return {"specialist_result": specialist,
                "tool_authorizations": (self.broker.authorize(self.call, self.context),)}


def _jobs(tmp_path, bundle="com.apple.Notes", **kwargs):
    tmp_path.chmod(0o700)
    broker = build_default_tool_broker()
    confirmations = OneTimeConfirmationStore()
    context = PolicyContext(workspace_root=tmp_path, confirmation_store=confirmations)
    audit = HashChainAuditLog(tmp_path / "audit.jsonl")
    store = ComputerAppGrantStore(tmp_path / "computer_app_grants.json")
    jobs = SwarmJobManager(ComputerGraph(broker, context, bundle), tool_broker=broker,
                           policy_context=context, confirmation_store=confirmations,
                           tool_executor=ReadOnlyToolExecutor(), audit_sink=audit,
                           app_grants=store, **kwargs)
    return jobs, audit, store


async def _left_confirmation(jobs, job_id) -> set:
    import asyncio

    observed = set()
    for _ in range(400):
        snapshot = await jobs.status(job_id)
        observed.add(snapshot.status)
        if snapshot.status in {JobStatus.COMPLETED, JobStatus.FAILED}:
            break
        await asyncio.sleep(0.001)
    return observed


@pytest.mark.asyncio
async def test_allowing_an_app_always_skips_later_approvals_in_it(tmp_path):
    jobs, audit, store = _jobs(tmp_path)
    try:
        first = await jobs.submit(UserRequest(text="Escribe hola en Notas"))
        pending = await _awaiting_confirmation(jobs, first.job_id)
        assert pending.confirmation.app_grant_bundle == "com.apple.Notes"
        await jobs.approve_app(first.job_id, pending.confirmation.call_digest, always=True)
        assert [g.bundle_identifier for g in store.entries()] == ["com.apple.notes"]
        second = await jobs.submit(UserRequest(text="Escribe adiós en Notas"))
        assert JobStatus.AWAITING_CONFIRMATION not in await _left_confirmation(jobs, second.job_id)
        events = [record.event_type for record in audit.verify()]
        assert events.count("computer_app_grant_created") == 1
        assert events.count("computer_app_grant_used") == 1
        assert jobs.revoke_app_grant("com.apple.Notes") is True
        third = await jobs.submit(UserRequest(text="Otra nota"))
        await _awaiting_confirmation(jobs, third.job_id)
    finally:
        await jobs.close()


@pytest.mark.asyncio
async def test_a_conversation_grant_needs_a_conversation_and_voice_still_asks(tmp_path):
    jobs, _, store = _jobs(tmp_path)
    try:
        queued = await jobs.submit(UserRequest(text="Escribe hola en Notas"))
        pending = await _awaiting_confirmation(jobs, queued.job_id)
        with pytest.raises(JobConfirmationError):  # This job is not in a conversation.
            await jobs.approve_app(queued.job_id, pending.confirmation.call_digest, always=False)
        store.grant("com.apple.Notes", conversation_id=None)
        spoken = await jobs.submit(UserRequest(
            text="Escribe hola en Notas",
            modalities=frozenset({InputModality.TEXT, InputModality.AUDIO})))
        assert (await _awaiting_confirmation(jobs, spoken.job_id)).status is (
            JobStatus.AWAITING_CONFIRMATION)
    finally:
        await jobs.close()


@pytest.mark.asyncio
async def test_restricted_apps_never_reach_an_approval_or_a_grant(tmp_path):
    jobs, _, store = _jobs(tmp_path, bundle="com.apple.Terminal")
    try:
        queued = await jobs.submit(UserRequest(text="Abre Terminal"))
        assert JobStatus.AWAITING_CONFIRMATION not in await _left_confirmation(jobs, queued.job_id)
        with pytest.raises(AppGrantError):
            store.grant("com.apple.Terminal", conversation_id=None)
        assert not store.allows("com.apple.Terminal", None)
    finally:
        await jobs.close()


def test_store_scopes_conversation_grants_and_persists_only_always(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / "computer_app_grants.json"
    store = ComputerAppGrantStore(path)
    chat, other = uuid4(), uuid4()
    store.grant("com.apple.Notes", conversation_id=chat)
    assert store.allows("com.apple.Notes", chat) and not store.allows("com.apple.Notes", other)
    store.grant("com.apple.TextEdit", conversation_id=None)
    reopened = ComputerAppGrantStore(path)
    assert reopened.allows("com.apple.TextEdit", None)
    assert not reopened.allows("com.apple.Notes", chat)  # Conversation grants end on restart.


@pytest.mark.asyncio
async def test_ipc_app_approvals_require_their_own_signature(tmp_path):
    jobs, _, store = _jobs(tmp_path)
    key = ec.generate_private_key(ec.SECP256R1())
    signer = PinnedApprovalSigner(tmp_path / "approval_signer.pub")
    signer.register(key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint))
    service = SwarmIpcService(jobs, approval_signer=signer)
    authenticator = IpcAuthenticator(bytes.fromhex("6d" * 32))
    try:
        queued = await jobs.submit(UserRequest(text="Escribe hola en Notas"))
        digest = (await _awaiting_confirmation(jobs, queued.job_id)).confirmation.call_digest

        def signed(method, nonce):
            issued = time.time_ns() // 1_000_000
            message = approval_signature_message(method=method, job_id=queued.job_id,
                                                 call_digest=digest, approval_nonce=nonce,
                                                 issued_at_milliseconds=issued)
            return {"job_id": str(queued.job_id), "call_digest": digest, "approval_nonce": nonce,
                    "issued_at_milliseconds": issued, "signature": base64.b64encode(
                        key.sign(message, ec.ECDSA(hashes.SHA256()))).decode()}

        replayed = await service.handle(authenticator.create_request(
            "jobs.approve.app.always", signed("jobs.approve", "0a" * 16)))
        assert replayed.error_code == "approval_signature_invalid"
        accepted = await service.handle(authenticator.create_request(
            "jobs.approve.app.always", signed("jobs.approve.app.always", "0b" * 16)))
        assert accepted.ok is True
        listed = await service.handle(authenticator.create_request("jobs.app_grants.list", {}))
        assert [g["bundle_identifier"] for g in listed.payload["grants"]] == ["com.apple.notes"]
        revoked = await service.handle(authenticator.create_request(
            "jobs.app_grants.revoke", {"bundle_identifier": "com.apple.Notes"}))
        assert revoked.payload == {"revoked": True} and store.entries() == ()
    finally:
        await jobs.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["digest", "consume"])
async def test_invalid_app_approval_never_publishes_a_grant(tmp_path, monkeypatch, invalid):
    jobs, audit, store = _jobs(tmp_path)
    try:
        queued = await jobs.submit(UserRequest(text="Escribe hola en Notas"))
        digest = (await _awaiting_confirmation(jobs, queued.job_id)).confirmation.call_digest
        store.grant("com.apple.TextEdit", conversation_id=None)
        before = store.entries()
        if invalid == "consume":
            def reject(*args, **kwargs):
                raise AuthorizationConsumptionError("synthetic invalid confirmation")
            monkeypatch.setattr(jobs._authorization, "consume", reject)
        with pytest.raises(JobConfirmationError):
            await jobs.approve_app(
                queued.job_id, "f" * 64 if invalid == "digest" else digest, always=True,
            )
        assert store.entries() == before
        assert not store.allows("com.apple.Notes", None)
        assert not any(record.event_type in {"tool_execution", "computer_app_grant_created"}
                       for record in audit.verify())
    finally:
        await jobs.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_action", ["cancel", "expire", "cancel_waiter"])
async def test_app_approval_waiting_for_lock_rechecks_cancellation_and_expiry(
    tmp_path, terminal_action,
):
    monotonic = [100.0]
    jobs, audit, store = _jobs(tmp_path, monotonic_clock=lambda: monotonic[0])
    approval = cancellation = None
    try:
        queued = await jobs.submit(UserRequest(text="Escribe hola en Notas"))
        digest = (await _awaiting_confirmation(jobs, queued.job_id)).confirmation.call_digest
        async with jobs._lock:
            if terminal_action == "cancel":
                cancellation = asyncio.create_task(jobs.cancel(queued.job_id))
                await asyncio.sleep(0)  # Cancellation owns the first position in the lock queue.
            approval = asyncio.create_task(jobs.approve_app(queued.job_id, digest, always=True))
            await asyncio.sleep(0)
            assert not approval.done()
            if terminal_action == "expire":
                monotonic[0] += 121
            elif terminal_action == "cancel_waiter":
                approval.cancel()
        with pytest.raises(
            asyncio.CancelledError if terminal_action == "cancel_waiter" else JobConfirmationError,
        ):
            await asyncio.wait_for(approval, 1)
        if cancellation is not None:
            assert (await cancellation).status is JobStatus.CANCELLED
        assert store.entries() == ()
        assert not any(record.event_type in {"tool_execution", "computer_app_grant_created"}
                       for record in audit.verify())
    finally:
        for task in (approval, cancellation):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        await jobs.close()


@pytest.mark.asyncio
async def test_failed_app_grant_commit_closes_job_without_execution_or_rollback(
    tmp_path, monkeypatch,
):
    jobs, audit, store = _jobs(tmp_path)
    try:
        queued = await jobs.submit(UserRequest(text="Escribe hola en Notas"))
        digest = (await _awaiting_confirmation(jobs, queued.job_id)).confirmation.call_digest
        store.grant("com.apple.TextEdit", conversation_id=None)
        before = store.entries()
        original_save = store._save

        def fail_new_grant(grants):
            if "com.apple.notes" in grants:
                raise OSError("synthetic write failure")
            original_save(grants)

        monkeypatch.setattr(store, "_save", fail_new_grant)
        with pytest.raises(JobConfirmationError, match="application cannot be allowed"):
            await jobs.approve_app(queued.job_id, digest, always=True)
        snapshot = await jobs.status(queued.job_id)
        assert snapshot.status is JobStatus.FAILED and snapshot.confirmation is None
        assert snapshot.error_code == "confirmation_consumption_failed"
        assert store.entries() == before
        assert not any(record.event_type in {"tool_execution", "computer_app_grant_created"}
                       for record in audit.verify())
        # A later owner revoke must not be undone by asynchronous grant cleanup.
        assert jobs.revoke_app_grant("com.apple.TextEdit")
        await asyncio.sleep(0)
        assert store.entries() == ()
    finally:
        await jobs.close()


@pytest.mark.asyncio
async def test_app_grant_commit_follows_consumption_and_queued_cancel_cannot_republish_it(
    tmp_path, monkeypatch,
):
    jobs, audit, store = _jobs(tmp_path)
    cancellations = []
    consumed = False
    original_consume = jobs._authorization.consume
    original_grant = store.grant

    def consume(*args, **kwargs):
        nonlocal consumed
        result = original_consume(*args, **kwargs)
        consumed = True
        return result

    try:
        queued = await jobs.submit(UserRequest(text="Escribe hola en Notas"))
        digest = (await _awaiting_confirmation(jobs, queued.job_id)).confirmation.call_digest

        def grant(bundle, *, conversation_id):
            assert consumed and jobs._lock.locked()
            original_grant(bundle, conversation_id=conversation_id)
            # These run only after the valid approval commits and leaves its critical section.
            cancellations.append(asyncio.create_task(jobs.cancel(queued.job_id)))
            asyncio.get_running_loop().call_soon(jobs.revoke_app_grant, bundle)

        monkeypatch.setattr(jobs._authorization, "consume", consume)
        monkeypatch.setattr(store, "grant", grant)
        accepted = await jobs.approve_app(queued.job_id, digest, always=True)
        assert accepted.status is JobStatus.RUNNING and accepted.confirmation is None
        assert (await cancellations[0]).status is JobStatus.CANCELLED
        assert store.entries() == ()
        events = [record.event_type for record in audit.verify()]
        assert events.count("computer_app_grant_created") == 1
        assert events.count("computer_app_grant_revoked") == 1
        assert "tool_execution" not in events
    finally:
        await asyncio.gather(*cancellations, return_exceptions=True)
        await jobs.close()
