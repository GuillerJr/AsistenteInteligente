"""Recovery extension boundaries with only owned profiles and synthetic grants."""

import copy
import hashlib
import json
import threading

import pytest
from test_cold_repair import memory_fixture
from test_recovery_extensions import decision, params
from test_wal_host_boundaries import NoAccount, finish
from wal_fixture import oracle

from jarvis_core import cold_repair, cold_wal, repair_maintenance, workspace_recovery
from jarvis_core.protocol import RpcError
from jarvis_core.recovery import create_backup
from jarvis_core.recovery_fs import no_replace
from jarvis_core.service import JarvisService
from jarvis_core.state import StateStore


@pytest.fixture
async def repair_host(tmp_path):
    root = tmp_path / "profile"
    memory_fixture(root, "missing")
    with StateStore(root / "jarvis.sqlite3") as store:
        store.create_session("SYNTHETIC OTHER ROLE")
    peer = NoAccount()
    service = JarvisService(root, peer)
    try:
        yield service, tmp_path, peer
    finally:
        await service.aclose()


async def repair_review(service, parent):
    coordinator = service.maintenance_coordinator
    read = await coordinator.repair_review({"role": "memory", "repair_kind": "memory_fts_rebuild",
        "destination_parent": str(parent), "name": "candidate"})
    result = await finish(coordinator, read)
    assert result["phase"] == "complete", result
    return result["result"]["review"]


@pytest.mark.parametrize("mode", ["deny", "cancel", "wrong_digest", "changed_after_wait"])
async def test_repair_never_publishes_without_current_exact_owner_decision(repair_host, mode):
    service, parent, peer = repair_host
    coordinator = service.maintenance_coordinator
    before = oracle(service.directory)
    review = await repair_review(service, parent)
    if mode == "wrong_digest":
        with pytest.raises(RpcError):
            await coordinator.repair_apply({**params(review), "review_digest": "a" * 64})
        assert not (await coordinator.pending({}))["pending"]
    else:
        operation = await coordinator.repair_apply(params(review))
        if mode == "cancel":
            await coordinator.cancel({"operation_id": operation["operation_id"]})
        else:
            if mode == "changed_after_wait":
                path = service.directory / "memory.sqlite3"
                path.write_bytes(path.read_bytes() + b"synthetic change")
                before = oracle(service.directory)
            await decision(coordinator, mode != "deny")
        result = await finish(coordinator, operation)
        assert result["phase"] == {"deny": "denied", "cancel": "cancelled",
                                    "changed_after_wait": "failed"}[mode], result
        assert not result["cleanup_owner_pending"]
    assert not (parent / "candidate").exists()
    assert service.profile_gate._cut is None
    assert service.store._disk is service.memory._disk is None
    assert oracle(service.directory) == before and peer.calls == 0


async def test_repair_rejects_other_role_change_without_reusing_stale_snapshot(repair_host):
    service, parent, peer = repair_host
    coordinator = service.maintenance_coordinator
    review = await repair_review(service, parent)
    path = service.directory / "jarvis.sqlite3"
    path.write_bytes(path.read_bytes() + b"synthetic other role change")
    before = oracle(service.directory)
    with pytest.raises(RpcError):
        await service.handlers()["doctor.repair.apply"](params(review))
    assert not (parent / "candidate").exists()
    assert not (await coordinator.pending({}))["pending"]
    assert oracle(service.directory) == before and peer.calls == 0
    assert service.profile_gate._cut is None


async def test_repair_lost_publication_ack_preserves_purpose_receipt_and_never_replays(
    repair_host, monkeypatch,
):
    service, parent, peer = repair_host
    coordinator = service.maintenance_coordinator
    before = oracle(service.directory)
    review = await repair_review(service, parent)
    calls = []

    def lost_ack(directory, old, new):
        calls.append(new)
        no_replace(directory, old, new)
        raise OSError("synthetic publication acknowledgement loss")

    def backup(bundle, target, context):
        return create_backup(bundle, target, context, publish=lost_ack)

    monkeypatch.setattr(repair_maintenance, "create_backup", backup)
    operation = await coordinator.repair_apply(params(review))
    await decision(coordinator, True)
    result = await finish(coordinator, operation)
    assert result["receipt_durable"] and result["receipt"]["status"] == "unknown", result
    assert result["receipt"]["purpose"] == "doctor.repair.apply"
    assert result["receipt"]["operation_id"] == operation["operation_id"]
    again = await coordinator.repair_apply(params(review))
    assert again["operation_id"] == operation["operation_id"] and len(calls) == 1
    assert (parent / "candidate/.pending").is_file()
    assert oracle(service.directory) == before and peer.calls == 0


async def test_repair_cleanup_retains_cut_and_retires_without_recapturing_or_publishing(
    repair_host, monkeypatch,
):
    service, parent, peer = repair_host
    coordinator = service.maintenance_coordinator
    before = oracle(service.directory)
    review = await repair_review(service, parent)
    original = cold_wal.WalCaptureOwner.cleanup
    control, seen = {"close": False}, []

    def cleanup(owner):
        seen.append(owner)
        if not control["close"]:
            raise OSError("synthetic retained private reader")
        return original(owner)

    def no_recapture(*_args, **_kwargs):
        pytest.fail("repair must use the reviewed candidate, not rebuild after owner decision")

    monkeypatch.setattr(cold_wal.WalCaptureOwner, "cleanup", cleanup)
    monkeypatch.setattr(cold_repair, "repair_cold_image", no_recapture)
    try:
        operation = await coordinator.repair_apply(params(review))
        await decision(coordinator, True)
        result = await finish(coordinator, operation)
        assert result["phase"] == "cleanup_pending" and result["cleanup_owner_pending"], result
        row = coordinator._operations[operation["operation_id"]]
        held, owner = row.capture_cut, row.capture_cut.owner
        assert row.cleanup_owner is held and service.profile_gate._cut is held.cut
        assert seen and all(value is owner for value in seen)
        assert result["receipt"]["purpose"] == "doctor.repair.apply"
        assert result["receipt"]["effect_status"] == "no_published_effect"
        historical = copy.deepcopy(result["receipt"])
        with pytest.raises(RpcError):
            await coordinator.doctor({})
        control["close"] = True
        await coordinator.cleanup({"operation_id": operation["operation_id"]})
        settled = await finish(coordinator, operation)
        assert not settled["cleanup_owner_pending"] and held.released and owner.closed
        assert settled["receipt"] == historical
        assert service.profile_gate._cut is None
        assert not (parent / "candidate").exists()
        assert oracle(service.directory) == before and peer.calls == 0
    finally:
        control["close"] = True


async def test_workspace_restore_rechecks_cached_archive_before_owner_effect(tmp_path):
    root = tmp_path / "profile"
    root.mkdir(mode=0o700)
    peer = NoAccount()
    service = JarvisService(root, peer)
    try:
        coordinator = service.maintenance_coordinator
        source = service._workspace_path / "selected.txt"
        source.write_text("JARVIS ARCHIVE SYNTHETIC")
        operation = await coordinator.workspace_backup_review({"entries": [
            {"path": "selected.txt", "role": "workspace_file", "kind": "file"}],
            "destination_parent": str(tmp_path), "name": "archive"})
        result = await finish(coordinator, operation)
        review = result["result"]["review"]
        operation = await coordinator.workspace_backup_create(params(review))
        await decision(coordinator, True)
        result = await finish(coordinator, operation)
        assert result["phase"] == "complete", result
        operation = await coordinator.workspace_restore_review({
            "archive_path": str(tmp_path / "archive"),
            "expected_manifest_sha256": result["receipt"]["manifest_sha256"],
            "destination_parent": str(tmp_path), "name": "workspace-candidate"})
        result = await finish(coordinator, operation)
        review = result["result"]["review"]
        operation = await coordinator.workspace_restore_stage(params(review))
        path = tmp_path / "archive/data/00000000.bin"
        path.write_bytes(b"X" * path.stat().st_size)
        await decision(coordinator, True)
        result = await finish(coordinator, operation)
        assert result["phase"] == "failed" and result["receipt_durable"], result
        assert result["receipt"]["purpose"] == "workspace.restore.stage"
        assert not (tmp_path / "workspace-candidate").exists()
        assert source.read_text() == "JARVIS ARCHIVE SYNTHETIC"
        assert not result["cleanup_owner_pending"] and service.profile_gate._cut is None
        assert peer.calls == 0
    finally:
        await service.aclose()


async def test_workspace_widened_owner_retains_cut_after_confirmed_publish_close_failure(
    tmp_path, monkeypatch,
):
    root = tmp_path / "profile"
    root.mkdir(mode=0o700)
    service = JarvisService(root, NoAccount())
    coordinator = service.maintenance_coordinator
    source = service._workspace_path / "selected.txt"
    source.write_text("JARVIS WORKSPACE OWNER")
    operation = await coordinator.workspace_backup_review({"entries": [
        {"path": "selected.txt", "role": "workspace_file", "kind": "file"}],
        "destination_parent": str(tmp_path), "name": "archive"})
    result = await finish(coordinator, operation)
    review = result["result"]["review"]
    original = workspace_recovery.WorkspaceRecoveryOwner.cleanup
    control = {"close": False}
    seen = []

    def close(owner):
        if owner.parent is None:
            return original(owner)
        seen.append(owner)
        if not control["close"]:
            raise OSError("synthetic workspace private close refusal")
        return original(owner)

    monkeypatch.setattr(workspace_recovery.WorkspaceRecoveryOwner, "cleanup", close)
    try:
        operation = await coordinator.workspace_backup_create(params(review))
        await decision(coordinator, True)
        result = await finish(coordinator, operation)
        assert result["cleanup_owner_pending"] and result["phase"] == "cleanup_pending", result
        row = coordinator._operations[operation["operation_id"]]
        held = row.capture_cut
        assert isinstance(held.owner, workspace_recovery.WorkspaceRecoveryOwner)
        assert (tmp_path / "archive/manifest.json").is_file()
        assert held.owner.stage is None
        assert held is row.cleanup_owner and service.profile_gate._cut is held.cut
        assert result["receipt"]["purpose"] == "workspace.backup.create"
        with pytest.raises(RpcError):
            await coordinator.doctor({})
        historical = copy.deepcopy(result["receipt"])
        control["close"] = True
        await coordinator.cleanup({"operation_id": operation["operation_id"]})
        settled = await finish(coordinator, operation)
        assert not settled["cleanup_owner_pending"] and held.released and held.owner.closed
        assert settled["receipt"] == historical
        assert service.profile_gate._cut is None and source.read_text() == "JARVIS WORKSPACE OWNER"
        assert seen and all(owner is held.owner for owner in seen)
    finally:
        control["close"] = True
        await service.aclose()


@pytest.mark.parametrize("timing", ["before_apply", "awaiting_owner"])
async def test_expired_repair_review_never_publishes_cached_candidate(repair_host, timing):
    service, parent, peer = repair_host
    coordinator = service.maintenance_coordinator
    before = oracle(service.directory)
    review = await repair_review(service, parent)
    clock = coordinator.clock
    if timing == "before_apply":
        coordinator.clock = lambda: clock() + 121
        with pytest.raises(RpcError):
            await coordinator.repair_apply(params(review))
    else:
        operation = await coordinator.repair_apply(params(review))
        coordinator.clock = lambda: clock() + 121
        await decision(coordinator, True)
        result = await finish(coordinator, operation)
        assert result["phase"] in {"failed", "cancelled"}, result
        assert result["error_code"] == "recovery_deadline_exceeded"
        assert result["receipt"]["effect_status"] == "no_published_effect"
        assert result["receipt"]["purpose"] == "doctor.repair.apply"
    assert not (parent / "candidate").exists()
    assert service.profile_gate._cut is None
    assert oracle(service.directory) == before and peer.calls == 0


async def test_reviewed_repair_bytes_rebind_to_new_cut_without_recapture(repair_host, monkeypatch):
    service, parent, peer = repair_host
    coordinator = service.maintenance_coordinator
    before = oracle(service.directory)
    review = await repair_review(service, parent)
    assert service.profile_gate._cut is None

    def forbidden(*_args, **_kwargs):
        pytest.fail("owner effect must not recapture the reviewed repair")

    monkeypatch.setattr(cold_repair, "repair_cold_image", forbidden)
    original = repair_maintenance.create_backup
    received = []

    def exact_candidate(bundle, target, context):
        source = next(row for row in bundle.sqlite if row.role == "memory")
        expected = review["metadata"]["candidate"]["image_sha256"]
        assert source.sha256 == hashlib.sha256(source.data).hexdigest() == expected
        received.append(source.sha256)
        return original(bundle, target, context)

    monkeypatch.setattr(repair_maintenance, "create_backup", exact_candidate)
    operation = await coordinator.repair_apply(params(review))
    await decision(coordinator, True)
    result = await finish(coordinator, operation)
    assert result["phase"] == "complete", result
    manifest = json.loads((parent / "candidate/manifest.json").read_bytes())
    memory_row = next(row for row in manifest["resources"] if row["role"] == "memory")
    assert received == [review["metadata"]["candidate"]["image_sha256"]]
    portable = parent / "candidate" / memory_row["path"]
    assert memory_row["sha256"] == hashlib.sha256(portable.read_bytes()).hexdigest()
    assert service.profile_gate._cut is None
    assert oracle(service.directory) == before and peer.calls == 0


async def test_workspace_worker_cancellation_before_publish_cleans_then_reopens_admission(
    tmp_path, monkeypatch,
):
    root = tmp_path / "profile"
    root.mkdir(mode=0o700)
    service = JarvisService(root, NoAccount())
    coordinator = service.maintenance_coordinator
    (service._workspace_path / "selected.txt").write_text("JARVIS CANCEL WORKSPACE")
    operation = await coordinator.workspace_backup_review({"entries": [
        {"path": "selected.txt", "role": "workspace_file", "kind": "file"}],
        "destination_parent": str(tmp_path), "name": "archive"})
    result = await finish(coordinator, operation)
    review = result["result"]["review"]
    blocked, release = threading.Event(), threading.Event()
    original = workspace_recovery.write_workspace_archive_new

    def archive(snapshot, target, operation_id, checkpoint):
        def guard(stage, write=False):
            if stage == "workspace_restore_before_publish":
                blocked.set()
                assert release.wait(5)
            checkpoint(stage, write)
        return original(snapshot, target, operation_id, guard)

    monkeypatch.setattr(workspace_recovery, "write_workspace_archive_new", archive)
    try:
        operation = await coordinator.workspace_backup_create(params(review))
        await decision(coordinator, True)
        import asyncio
        assert await asyncio.to_thread(blocked.wait, 3)
        row = coordinator._operations[operation["operation_id"]]
        held = row.capture_cut
        row.task.cancel()
        for _ in range(100):
            if row.cancellation.is_set():
                break
            await asyncio.sleep(.001)
        assert row.cancellation.is_set() and service.profile_gate._cut is held.cut
        with pytest.raises(RpcError):
            await coordinator.doctor({})
        release.set()
        result = await finish(coordinator, operation)
        assert result["phase"] == "cancelled" and result["receipt_durable"], result
        assert result["receipt"]["purpose"] == "workspace.backup.create"
        assert not result["cleanup_owner_pending"] and service.profile_gate._cut is None
        assert not (tmp_path / "archive").exists()
        assert not any(path.name.startswith(".jarvis-") for path in tmp_path.iterdir())
    finally:
        release.set()
        await service.aclose()
