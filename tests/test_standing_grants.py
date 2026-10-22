import asyncio
import base64
import json
import threading
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from test_jobs import PendingNetworkGraph, _awaiting_confirmation, _terminal

from aegis_core.approval_signing import PinnedApprovalSigner, approval_signature_message
from aegis_core.contracts import Capability, InputModality, PolicyDecision, UserRequest
from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.job_contracts import JobConfirmationError, JobStatus
from aegis_core.job_ipc import SwarmIpcService
from aegis_core.jobs import SwarmJobManager
from aegis_core.standing_grants import StandingGrantError, StandingGrantStore
from aegis_core.tools.audit import HashChainAuditLog
from aegis_core.tools.broker import PolicyContext
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context
from aegis_core.tools.execution import ReadOnlyToolExecutor

TOOL = "network_discover_hosts"


def _store(tmp_path: Path, capabilities: dict[str, Capability]) -> StandingGrantStore:
    tmp_path.chmod(0o700)
    return StandingGrantStore(tmp_path / "standing_grants.json", capability_of=capabilities.get)


def _jobs(tmp_path: Path, capabilities: dict[str, Capability], **kwargs):
    broker = build_default_tool_broker()
    base = default_policy_context(tmp_path)
    confirmations = OneTimeConfirmationStore()
    context = PolicyContext(
        workspace_root=tmp_path, network_scopes=base.network_scopes,
        confirmation_store=confirmations,
    )
    audit = HashChainAuditLog(tmp_path / "audit.jsonl")
    store = _store(tmp_path, capabilities)
    jobs = SwarmJobManager(
        PendingNetworkGraph(broker, context),
        tool_broker=broker, policy_context=context, confirmation_store=confirmations,
        tool_executor=ReadOnlyToolExecutor(tcp_connector=lambda *_: "closed"),
        audit_sink=audit, standing_grants=store, **kwargs,
    )
    return jobs, audit, store


def _events(audit: HashChainAuditLog) -> list[str]:
    return [record.event_type for record in audit.verify()]


@pytest.mark.asyncio
async def test_write_capable_tool_is_never_offered_or_granted(tmp_path):
    jobs, _, store = _jobs(tmp_path, {TOOL: Capability.NETWORK_DISCOVERY})
    try:
        queued = await jobs.submit(UserRequest(text="Escanea loopback"))
        pending = await _awaiting_confirmation(jobs, queued.job_id)
        assert pending.confirmation.standing_grant_available is False
        with pytest.raises(JobConfirmationError):
            await jobs.approve_standing(queued.job_id, pending.confirmation.call_digest)
        assert store.entries() == ()
    finally:
        await jobs.close()


@pytest.mark.asyncio
async def test_always_allow_skips_later_confirmations_and_is_audited(tmp_path):
    jobs, audit, store = _jobs(tmp_path, {TOOL: Capability.MAIL_READ})
    try:
        first = await jobs.submit(UserRequest(text="Escanea loopback"))
        pending = await _awaiting_confirmation(jobs, first.job_id)
        assert pending.confirmation.standing_grant_available is True
        await jobs.approve_standing(first.job_id, pending.confirmation.call_digest)
        assert (await _terminal(jobs, first.job_id)).status is JobStatus.COMPLETED
        assert [grant.tool_name for grant in store.entries()] == [TOOL]

        second = await jobs.submit(UserRequest(text="Escanea loopback otra vez"))
        observed = set()
        for _ in range(400):
            snapshot = await jobs.status(second.job_id)
            observed.add(snapshot.status)
            if snapshot.status is JobStatus.COMPLETED:
                break
            await _sleep()
        assert JobStatus.COMPLETED in observed
        assert JobStatus.AWAITING_CONFIRMATION not in observed
        events = _events(audit)
        assert events.count("standing_grant_created") == 1
        assert events.count("standing_grant_used") == 1
    finally:
        await jobs.close()


@pytest.mark.asyncio
async def test_unverified_voice_still_needs_a_fresh_approval(tmp_path):
    jobs, _, store = _jobs(tmp_path, {TOOL: Capability.MAIL_READ})
    try:
        store.grant(TOOL)
        queued = await jobs.submit(UserRequest(
            text="Escanea loopback",
            modalities=frozenset({InputModality.TEXT, InputModality.AUDIO}),
        ))
        pending = await _awaiting_confirmation(jobs, queued.job_id)
        assert pending.status is JobStatus.AWAITING_CONFIRMATION
    finally:
        await jobs.close()


@pytest.mark.asyncio
async def test_revoked_grant_asks_again(tmp_path):
    jobs, audit, store = _jobs(tmp_path, {TOOL: Capability.MAIL_READ})
    try:
        store.grant(TOOL)
        assert jobs.revoke_standing_grant(TOOL) is True
        assert jobs.revoke_standing_grant(TOOL) is False
        queued = await jobs.submit(UserRequest(text="Escanea loopback"))
        await _awaiting_confirmation(jobs, queued.job_id)
        assert "standing_grant_revoked" in _events(audit)
    finally:
        await jobs.close()


async def _approve_standing(jobs, job_id, digest, *, telegram):
    if telegram:
        return await jobs.approve_standing_from_telegram(job_id, digest, owner_id=123)
    return await jobs.approve_standing(job_id, digest)


@pytest.mark.asyncio
@pytest.mark.parametrize("telegram", [False, True])
async def test_reauthorization_refusal_does_not_leave_a_permanent_grant(
    tmp_path, monkeypatch, telegram,
):
    jobs, audit, store = _jobs(tmp_path, {TOOL: Capability.MAIL_READ})
    try:
        queued = await jobs.submit(UserRequest(text="Escanea loopback"))
        pending = await _awaiting_confirmation(jobs, queued.job_id)
        authorization = jobs._lifecycle.confirmation_context(queued.job_id).authorization
        monkeypatch.setattr(jobs._workflow_broker, "authorize", lambda *args, **kwargs:
                            authorization.model_copy(update={
                                "decision": PolicyDecision.DENY,
                                "reason_code": "policy_changed",
                            }))
        with pytest.raises(JobConfirmationError):
            await _approve_standing(
                jobs, queued.job_id, pending.confirmation.call_digest, telegram=telegram,
            )
        assert store.entries() == ()
        assert _store(tmp_path, {TOOL: Capability.MAIL_READ}).entries() == ()
        assert (await jobs.status(queued.job_id)).status is JobStatus.FAILED
        assert not {"standing_grant_created", "tool_execution"}.intersection(_events(audit))
    finally:
        await jobs.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("telegram", [False, True])
async def test_failed_standing_grant_write_seals_approval_without_execution(
    tmp_path, monkeypatch, telegram,
):
    jobs, audit, store = _jobs(tmp_path, {TOOL: Capability.MAIL_READ})
    try:
        queued = await jobs.submit(UserRequest(text="Escanea loopback"))
        pending = await _awaiting_confirmation(jobs, queued.job_id)

        def fail_write(grants):
            raise OSError("synthetic storage failure")

        monkeypatch.setattr(store, "_save", fail_write)
        with pytest.raises(JobConfirmationError):
            await _approve_standing(
                jobs, queued.job_id, pending.confirmation.call_digest, telegram=telegram,
            )
        assert store.entries() == ()
        snapshot = await jobs.status(queued.job_id)
        assert snapshot.status is JobStatus.FAILED
        assert snapshot.confirmation is None
        assert snapshot.error_code == "confirmation_consumption_failed"
        assert not {"standing_grant_created", "tool_execution"}.intersection(_events(audit))
    finally:
        await jobs.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("telegram", [False, True])
async def test_expiry_racing_standing_grant_cannot_leave_a_rejected_permission(
    tmp_path, monkeypatch, telegram,
):
    monotonic = [100.0]
    jobs, _, store = _jobs(
        tmp_path, {TOOL: Capability.MAIL_READ}, monotonic_clock=lambda: monotonic[0],
    )
    loop = asyncio.get_running_loop()
    original_grant = store.grant

    def grant_with_expiry(tool):
        result = original_grant(tool)
        # The timeout event is ready as soon as the storage operation completes.
        loop.call_soon_threadsafe(monotonic.__setitem__, 0, 221.0)
        return result

    try:
        queued = await jobs.submit(UserRequest(text="Escanea loopback"))
        pending = await _awaiting_confirmation(jobs, queued.job_id)
        monkeypatch.setattr(store, "grant", grant_with_expiry)
        try:
            accepted = await _approve_standing(
                jobs, queued.job_id, pending.confirmation.call_digest, telegram=telegram,
            )
        except JobConfirmationError:
            assert store.entries() == ()
        else:
            # Either consent commits before expiry, or expiry rejects both parts.
            assert accepted.status is JobStatus.RUNNING and accepted.confirmation is None
            assert store.allows(TOOL)
            assert jobs.revoke_standing_grant(TOOL)
        await asyncio.sleep(0)
        assert store.entries() == ()
    finally:
        await jobs.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("telegram", [False, True])
@pytest.mark.parametrize("interruption", ["cancel_waiter", "revoke"])
async def test_standing_grant_has_no_orphaned_writer_after_cancellation_or_revocation(
    tmp_path, monkeypatch, telegram, interruption,
):
    jobs, _, store = _jobs(tmp_path, {TOOL: Capability.MAIL_READ})
    loop = asyncio.get_running_loop()
    event_loop_thread = threading.get_ident()
    release_worker, finished = threading.Event(), threading.Event()
    owner_action_done = asyncio.Event()
    original_grant = store.grant
    approval = None

    def owner_action():
        if interruption == "cancel_waiter":
            approval.cancel()
        else:
            assert jobs.revoke_standing_grant(TOOL)
        release_worker.set()
        owner_action_done.set()

    def grant_during_owner_action(tool):
        try:
            if threading.get_ident() != event_loop_thread:
                # An outstanding worker must not recreate a grant after its caller
                # disconnects or after the owner removes an existing permission.
                loop.call_soon_threadsafe(owner_action)
                assert release_worker.wait(timeout=2)
                return original_grant(tool)
            result = original_grant(tool)
            # A synchronous commit linearizes first; the later revoke still wins.
            loop.call_soon(owner_action)
            return result
        finally:
            finished.set()

    try:
        queued = await jobs.submit(UserRequest(text="Escanea loopback"))
        pending = await _awaiting_confirmation(jobs, queued.job_id)
        if interruption == "revoke":
            # Another valid approval has already granted the same read-only tool.
            store.grant(TOOL)
        monkeypatch.setattr(store, "grant", grant_during_owner_action)
        approval = asyncio.create_task(_approve_standing(
            jobs, queued.job_id, pending.confirmation.call_digest, telegram=telegram,
        ))
        accepted = None
        try:
            accepted = await approval
        except asyncio.CancelledError:
            pass
        await asyncio.wait_for(owner_action_done.wait(), 2)
        assert await asyncio.to_thread(finished.wait, 2)
        if accepted is not None and interruption == "cancel_waiter":
            assert accepted.status is JobStatus.RUNNING
            assert jobs.revoke_standing_grant(TOOL)
        assert store.entries() == ()
        assert _store(tmp_path, {TOOL: Capability.MAIL_READ}).entries() == ()
    finally:
        release_worker.set()
        if approval is not None:
            await asyncio.gather(approval, return_exceptions=True)
        await jobs.close()


def test_store_persists_and_rechecks_capability_on_every_use(tmp_path):
    capabilities = {TOOL: Capability.MAIL_READ}
    store = _store(tmp_path, capabilities)
    store.grant(TOOL)
    reopened = _store(tmp_path, capabilities)
    assert reopened.allows(TOOL)
    # The peer was reconfigured and the tool now writes: the grant stops applying.
    capabilities[TOOL] = Capability.MAIL_WRITE
    assert not reopened.allows(TOOL)
    with pytest.raises(StandingGrantError):
        reopened.grant("unknown_tool")


def test_unreadable_or_loose_file_grants_nothing(tmp_path):
    store = _store(tmp_path, {TOOL: Capability.MAIL_READ})
    path = tmp_path / "standing_grants.json"
    path.write_text(json.dumps({"version": 1, "grants": [
        {"tool_name": TOOL, "granted_at": "2026-10-20T00:00:00+00:00"},
    ]}))
    path.chmod(0o644)
    assert not store.allows(TOOL)


@pytest.mark.asyncio
async def test_ipc_standing_approval_requires_its_own_signature(tmp_path):
    jobs, _, store = _jobs(tmp_path, {TOOL: Capability.MAIL_READ})
    signing_key = ec.generate_private_key(ec.SECP256R1())
    signer = PinnedApprovalSigner(tmp_path / "approval_signer.pub")
    signer.register(
        signing_key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    )
    service = SwarmIpcService(jobs, approval_signer=signer)
    authenticator = IpcAuthenticator(bytes.fromhex("6d" * 32))
    try:
        queued = await jobs.submit(UserRequest(text="Escanea loopback"))
        pending = await _awaiting_confirmation(jobs, queued.job_id)
        digest = pending.confirmation.call_digest

        def signed(signed_method: str, nonce: str) -> dict:
            issued_at = time.time_ns() // 1_000_000
            message = approval_signature_message(
                method=signed_method, job_id=queued.job_id, call_digest=digest,
                approval_nonce=nonce, issued_at_milliseconds=issued_at,
            )
            return {
                "job_id": str(queued.job_id), "call_digest": digest, "approval_nonce": nonce,
                "issued_at_milliseconds": issued_at,
                "signature": base64.b64encode(
                    signing_key.sign(message, ec.ECDSA(hashes.SHA256()))
                ).decode(),
            }

        # A one-time approval signature cannot be replayed as "always allow".
        replayed = await service.handle(authenticator.create_request(
            "jobs.approve.standing", signed("jobs.approve", "0a" * 16),
        ))
        assert replayed.error_code == "approval_signature_invalid"
        accepted = await service.handle(authenticator.create_request(
            "jobs.approve.standing", signed("jobs.approve.standing", "0b" * 16),
        ))
        assert accepted.ok is True
        assert (await _terminal(jobs, queued.job_id)).status is JobStatus.COMPLETED

        listed = await service.handle(authenticator.create_request("jobs.grants.list", {}))
        assert [item["tool_name"] for item in listed.payload["grants"]] == [TOOL]
        revoked = await service.handle(authenticator.create_request(
            "jobs.grants.revoke", {"tool_name": TOOL},
        ))
        assert revoked.payload == {"revoked": True}
        assert store.entries() == ()
    finally:
        await jobs.close()


async def _sleep():
    import asyncio

    await asyncio.sleep(0.001)
