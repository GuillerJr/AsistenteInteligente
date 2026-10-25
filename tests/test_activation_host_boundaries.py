"""Typed journal/capability/RPC boundaries on synthetic physical profiles."""
import asyncio
import json
import shutil
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from test_activation import prepared as prepared
from test_activation import snapshot
from test_recovery import bundle as bundle
from test_service import ModelPeer

from jarvis_core.activation import ActivationReceipt
from jarvis_core.host_profile import ExpectedRestore, ProfileLease
from jarvis_core.maintenance_journal import MaintenanceJournal
from jarvis_core.private_json import PrivateJsonError
from jarvis_core.protocol import LocalRpcClient
from jarvis_core.recovery_fs import FileIdentity
from jarvis_core.service import JarvisService
from jarvis_core.tools import (
    ApprovalAuthority,
    HostApprovalRequest,
    HostCancellation,
    HostPurpose,
    HostScope,
    canonical_host_arguments,
    host_operation_digest,
)


def request(scope, purpose=HostPurpose.PROFILE_RESTORE_ACTIVATE):
    value = HostApprovalRequest(scope, purpose, uuid4().hex,
                                canonical_host_arguments({"review_digest": "d"*64}), "")
    return replace(value, digest=host_operation_digest(value))


async def test_restrictive_deadline_expires_ticket_grant_and_does_not_use_whole_ttl():
    authority = ApprovalAuthority(ttl=2)
    scope = HostScope("fixture", 0)
    authority.bind_host_scope(scope)
    operation, cancellation = request(scope), HostCancellation()
    begin = time.monotonic()
    task = asyncio.create_task(authority.request_host(operation, cancellation, deadline=begin+.05))
    async with asyncio.timeout(1):
        while not authority.pending_host(scope):
            await asyncio.sleep(0)
    pending = authority.pending_host(scope)[0]
    assert int(pending.expires_at_ns) <= time.time_ns() + 60_000_000
    assert await asyncio.wait_for(task, .5) is None
    assert time.monotonic() - begin < .5
    assert not authority.decide_host(pending.approval_id, scope=scope,
                                      digest=operation.digest, allow=True)
    await authority.close()


async def test_purpose_domain_does_not_lend_backup_grant_to_activation():
    authority = ApprovalAuthority()
    scope = HostScope("fixture", 0)
    authority.bind_host_scope(scope)
    original = request(scope, HostPurpose.BACKUP_CREATE)
    task = asyncio.create_task(authority.request_host(original, HostCancellation()))
    async with asyncio.timeout(1):
        while not (rows := authority.pending_host(scope)):
            await asyncio.sleep(0)
    authority.decide_host(rows[0].approval_id, scope=scope, digest=original.digest, allow=True)
    grant = await task
    changed = replace(original, purpose=HostPurpose.PROFILE_RESTORE_ACTIVATE)
    changed = replace(changed, digest=host_operation_digest(changed))
    with pytest.raises(ValueError):
        with authority.execution_host(changed, grant):
            authority.check_host(changed, commit=True)
    await authority.close()


def receipt():
    return ActivationReceipt("a"*32, "profile.restore.activate", "completed",
                             "local_admission_published", FileIdentity(1,2), "b"*64, "c"*64)


def test_activation_journal_roundtrip_and_restart_never_replays(tmp_path):
    journal = MaintenanceJournal(tmp_path)
    journal.record("a"*32, "profile.restore.activate", "d"*64,
                   "activated_not_opened", receipt())
    restored = MaintenanceJournal(tmp_path)
    assert restored.previous[0]["phase"] == "activated_not_opened"
    assert restored.previous[0]["receipt"]["target_identity"] == {"device":"1", "inode":"2"}
    journal.record("e"*32, "profile.restore.activate", "f"*64, "working")
    assert MaintenanceJournal(tmp_path).previous[-1]["phase"] == "interrupted_unknown"
    assert MaintenanceJournal(tmp_path).previous[-1]["receipt"] is None


@pytest.mark.parametrize("changes", [
    {"services_started": True}, {"grants_restored": True}, {"status": "known"},
    {"effect_status": "new_disarmed_profile"}, {"cleanup_pending": True},
    {"target_identity": {"device":2**64,"inode":2}}, {"admission_sha256": None},
    {"error_code": "peer body with private path"}, {"owner": "not serializable"},
])
def test_activation_journal_rejects_loose_or_contradictory_receipts(tmp_path, changes):
    from jarvis_core.maintenance_journal import wire
    journal = MaintenanceJournal(tmp_path)
    with pytest.raises(PrivateJsonError):
        journal.record("a"*32, "profile.restore.activate", "d"*64, "activated_not_opened",
                       {**wire(receipt()), **changes})
    assert journal.document.read()["operations"] == []


@pytest.mark.parametrize("phase", ["publication_unknown", "cleanup_pending"])
def test_unknown_activation_records_are_not_disposable_at_journal_capacity(tmp_path, phase):
    journal = MaintenanceJournal(tmp_path)
    unknown = replace(receipt(), status="unknown", effect_status="publication_unknown",
                      cleanup_pending=phase == "cleanup_pending")
    for index in range(64):
        operation_id = f"{index:032x}"
        journal.record(operation_id, "profile.restore.activate", "d"*64, phase,
                       replace(unknown, operation_id=operation_id))
    with pytest.raises(PrivateJsonError, match="maintenance_journal_capacity"):
        journal.record("f"*32, "profile.restore.activate", "d"*64, "awaiting_owner")
    assert len(journal.document.read()["operations"]) == 64


async def test_activation_rpc_real_socket_and_new_host_local_status_without_sdk(prepared):
    directory = Path(tempfile.mkdtemp(prefix="jvs-act-",
                                     dir=tempfile.gettempdir())).resolve()
    host = directory / "host"
    host.mkdir(mode=0o700)
    peer = ModelPeer()
    service = JarvisService(host, peer)
    client = LocalRpcClient(host, max_frame_bytes=1024*1024)
    new_service = None
    try:
        await service.start()
        await client.connect()
        before = snapshot(prepared)
        job = await client.call("profile.restore.review", {"profile_path": str(prepared)})
        async def wait(operation):
            async with asyncio.timeout(5):
                while True:
                    row = await client.call("maintenance.status", {"operation_id": operation})
                    if not row["worker_pending"]:
                        return row
                    await asyncio.sleep(.005)
        review_job = await wait(job["operation_id"])
        assert review_job["phase"] == "complete", review_job
        assert snapshot(prepared) == before
        review = review_job["result"]["review"]
        job = await client.call("profile.restore.activate", {key: review[key] for key in
                                                            ("review_id", "review_digest")})
        async with asyncio.timeout(2):
            while not (pending := (await client.call("maintenance.pending"))["pending"]):
                await asyncio.sleep(.005)
        card = pending[0]
        arguments = json.loads(card["arguments_json"])
        assert arguments["review"] == review["metadata"]
        assert card["purpose"] == "profile.restore.activate"
        decision = {"approval_id":card["approval_id"],"digest":card["digest"],
                    "allow":True, **card["scope"]}
        assert (await client.call("maintenance.decide", decision))["accepted"]
        result = await wait(job["operation_id"])
        assert result["phase"] == "activated_not_opened", result
        assert result["receipt_durable"] and not result["cleanup_owner_pending"]
        local = await client.call("service.local_status")
        assert local["restore_admission"] is None
        assert not peer.calls
        physical = result["receipt"]["target_identity"]
        expected = ExpectedRestore(FileIdentity(int(physical["device"]),int(physical["inode"])),
                                   result["receipt"]["marker_sha256"],
                                   result["receipt"]["admission_sha256"],
                                   result["operation_id"])
        class NoSdk:
            async def account_status(self):
                raise AssertionError("provider was accessed without local choice")
            async def aclose(self):
                pass
        lease = ProfileLease.acquire(prepared, expected_restore=expected)
        new_service = JarvisService(prepared, NoSdk(), profile_lease=lease)
        local = await new_service.local_status({})
        assert local["restore_admission"]["admission_sha256"] == expected.admission_sha256
        assert not local["provider_choice"]["selected"]
        assert (await new_service.status({}))["provider"]["connected"] is False
    finally:
        if new_service is not None:
            await new_service.aclose()
        await client.aclose()
        await service.aclose()
        shutil.rmtree(directory)
