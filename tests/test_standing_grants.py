import base64
import json
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from aegis_core.approval_signing import PinnedApprovalSigner, approval_signature_message
from aegis_core.contracts import Capability, InputModality, UserRequest
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
from tests.test_jobs import PendingNetworkGraph, _awaiting_confirmation, _terminal

TOOL = "network_discover_hosts"


def _store(tmp_path: Path, capabilities: dict[str, Capability]) -> StandingGrantStore:
    tmp_path.chmod(0o700)
    return StandingGrantStore(tmp_path / "standing_grants.json", capability_of=capabilities.get)


def _jobs(tmp_path: Path, capabilities: dict[str, Capability]):
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
        audit_sink=audit, standing_grants=store,
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
