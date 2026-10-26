"""Host integration with real private bytes and synthetic owner decisions only."""
import asyncio
import json

import pytest
from test_cold_repair import memory_fixture
from test_maintenance_integration import allow_synthetic_owner, finish
from wal_fixture import NAMES, make_wal, oracle

from jarvis_core import workspace_recovery
from jarvis_core.protocol import RpcError
from jarvis_core.recovery_fs import RecoveryFsError, no_replace
from jarvis_core.service import JarvisService


class Peer:
    async def aclose(self):
        pass


@pytest.fixture
async def host(tmp_path):
    root = tmp_path / "profile"
    root.mkdir(mode=0o700)
    service = JarvisService(root, Peer())
    service.store.create_session("RECOVERY FIXTURE")
    workspace = service._workspace_path
    (workspace / "selected.txt").write_text("JARVIS ARCHIVO 8432")
    (workspace / "artifact.bin").write_bytes(bytes(range(256)))
    (workspace / "unselected.txt").write_text("NO SELECCIONADO")
    try:
        yield service.maintenance_coordinator, tmp_path
    finally:
        await service.aclose()


async def reviewed(coordinator, kind, parent, name="archive"):
    read = await coordinator.workspace_backup_review({
        "entries": [{"path": "selected.txt", "role": "workspace_file", "kind": "file"},
                    {"path": "artifact.bin", "role": "artifact", "kind": "file"}],
        "destination_parent": str(parent), "name": name})
    result = await finish(coordinator, read["operation_id"])
    assert result["phase"] == "complete", result
    review = result["result"]["review"]
    assert review["metadata"]["kind"] == kind
    return review


def params(review):
    return {"review_id": review["review_id"], "review_digest": review["review_digest"]}


async def decision(coordinator, allow):
    for _ in range(100):
        pending = (await coordinator.pending({}))["pending"]
        if pending:
            break
        await asyncio.sleep(0)
    row = pending[0]
    assert (await coordinator.decide({"approval_id": row["approval_id"],
                                     "digest": row["digest"], "allow": allow,
                                     **row["scope"]}))["accepted"]


@pytest.mark.asyncio
async def test_selected_workspace_archive_restore_authority_and_durable_receipts(host):
    coordinator, parent = host
    review = await reviewed(coordinator, "workspace.backup.create", parent)
    assert review["metadata"]["summary"]["artifact_count"] == 1
    assert "JARVIS ARCHIVO" not in json.dumps(review)
    operation = await coordinator.workspace_backup_create(params(review))
    assert not (parent / "archive").exists()
    await allow_synthetic_owner(coordinator)
    result = await finish(coordinator, operation["operation_id"])
    assert result["phase"] == "complete" and result["receipt_durable"], result
    assert result["receipt"]["purpose"] == "workspace.backup.create"
    assert result["result"]["association_changed"] is False
    digest = result["receipt"]["manifest_sha256"]
    read = await coordinator.workspace_restore_review({
        "archive_path": str(parent / "archive"), "expected_manifest_sha256": digest,
        "destination_parent": str(parent), "name": "restored-files"})
    parsed = await finish(coordinator, read["operation_id"])
    assert parsed["phase"] == "complete", parsed
    restore_review = parsed["result"]["review"]
    operation = await coordinator.workspace_restore_stage(params(restore_review))
    assert not (parent / "restored-files").exists()
    await allow_synthetic_owner(coordinator)
    result = await finish(coordinator, operation["operation_id"])
    assert result["phase"] == "complete" and result["receipt_durable"], result
    assert (parent / "restored-files/selected.txt").read_text() == "JARVIS ARCHIVO 8432"
    assert (parent / "restored-files/artifact.bin").read_bytes() == bytes(range(256))
    assert not (parent / "restored-files/unselected.txt").exists()
    assert coordinator.service._workspace_path != parent / "restored-files"
    assert coordinator.gate.blockers() == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["deny", "cancel", "source_changed", "digest_changed"])
async def test_workspace_exact_review_refuses_unapproved_or_changed_content(host, mode):
    coordinator, parent = host
    review = await reviewed(coordinator, "workspace.backup.create", parent)
    if mode == "digest_changed":
        with pytest.raises(RpcError):
            await coordinator.workspace_backup_create({**params(review), "review_digest": "a" * 64})
    else:
        operation = await coordinator.workspace_backup_create(params(review))
        if mode == "cancel":
            await coordinator.cancel({"operation_id": operation["operation_id"]})
        elif mode == "source_changed":
            (coordinator.service._workspace_path / "selected.txt").write_text("CAMBIADO")
            await decision(coordinator, True)
        else:
            await decision(coordinator, False)
        result = await finish(coordinator, operation["operation_id"])
        assert result["phase"] in {"denied", "cancelled", "failed"}, result
    assert not (parent / "archive").exists()


@pytest.mark.asyncio
async def test_workspace_unknown_ack_never_replays_and_cleanup_keeps_historical_unknown(
    host, monkeypatch,
):
    coordinator, parent = host
    review = await reviewed(coordinator, "workspace.backup.create", parent)
    original = workspace_recovery.write_workspace_archive_new
    calls = []

    def lost_ack(directory, old, new):
        calls.append(new)
        no_replace(directory, old, new)
        raise OSError("synthetic ACK loss")

    def write(*args, **kwargs):
        return original(*args, **kwargs, publish=lost_ack)
    monkeypatch.setattr(workspace_recovery, "write_workspace_archive_new", write)
    operation = await coordinator.workspace_backup_create(params(review))
    await decision(coordinator, True)
    result = await finish(coordinator, operation["operation_id"])
    assert result["phase"] == "publication_unknown", result
    assert result["receipt_durable"] and result["receipt"]["status"] == "unknown"
    again = await coordinator.workspace_backup_create(params(review))
    assert again["operation_id"] == operation["operation_id"] and len(calls) == 1
    assert (parent / "archive/manifest.json").is_file()


@pytest.mark.asyncio
async def test_repair_memory_index_publishes_restorable_backup_without_editing_original(tmp_path):
    root = tmp_path / "profile"
    memory_fixture(root, "missing")
    before = oracle(root)
    service = JarvisService(root, Peer())
    try:
        coordinator = service.maintenance_coordinator
        read = await coordinator.repair_review({"role": "memory",
                "repair_kind": "memory_fts_rebuild", "destination_parent": str(tmp_path),
                "name": "repaired-backup"})
        result = await finish(coordinator, read["operation_id"])
        assert result["phase"] == "complete", result
        review = result["result"]["review"]
        assert review["metadata"]["candidate"]["base_data_loss"] is False
        assert "PRUEBA MEMORIA BASE" not in json.dumps(review)
        operation = await coordinator.repair_apply(params(review))
        await decision(coordinator, True)
        result = await finish(coordinator, operation["operation_id"])
        assert result["phase"] == "complete" and result["receipt_durable"], result
        assert result["receipt"]["purpose"] == "doctor.repair.apply"
        assert oracle(root) == before
        verified = await coordinator.verify({"backup_path": str(tmp_path / "repaired-backup")})
        verification = await finish(coordinator, verified["operation_id"])
        assert verification["phase"] == "complete", verification
        restore = await coordinator.review_restore({
            "verification_id": verification["result"]["verification_id"],
            "destination_parent": str(tmp_path), "name": "repaired-profile"})
        operation = await coordinator.restore(params(restore))
        await decision(coordinator, True)
        result = await finish(coordinator, operation["operation_id"])
        assert result["phase"] == "staged_disarmed", result
        assert oracle(root) == before and not service.memory._disk
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_repair_wal_tail_is_explicit_lost_transactions_unknown(tmp_path):
    root = tmp_path / "profile"
    make_wal(root, ("memory",))
    wal = root / (NAMES["memory"] + "-wal")
    wal.write_bytes(wal.read_bytes() + b"truncated tail")
    before = oracle(root)
    service = JarvisService(root, Peer())
    try:
        coordinator = service.maintenance_coordinator
        read = await coordinator.repair_review({"role": "memory", "repair_kind": "wal_valid_prefix",
                "destination_parent": str(tmp_path), "name": "repaired-wal"})
        result = await finish(coordinator, read["operation_id"])
        assert result["phase"] == "complete", result
        review = result["result"]["review"]
        assert review["metadata"]["candidate"]["data_loss_possible"]
        assert review["metadata"]["candidate"]["lost_committed_transactions"] == "unknown"
        operation = await coordinator.repair_apply(params(review))
        await decision(coordinator, True)
        result = await finish(coordinator, operation["operation_id"])
        assert result["phase"] == "complete" and result["receipt_durable"], result
        assert oracle(root) == before
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_repair_does_not_accept_arbitrary_kind_or_open_store(host):
    coordinator, parent = host
    with pytest.raises(RecoveryFsError):
        await coordinator.repair_review({"role": "state", "repair_kind": "arbitrary_sql",
                "destination_parent": str(parent), "name": "no"})
    read = await coordinator.repair_review({"role": "state", "repair_kind": "wal_valid_prefix",
            "destination_parent": str(parent), "name": "no"})
    result = await finish(coordinator, read["operation_id"])
    assert result["phase"] == "failed"
    assert result["error_code"] == "profile_repair_closed_source_required"
    assert not (parent / "no").exists()


@pytest.mark.asyncio
async def test_workspace_source_changed_during_staging_cannot_be_published(host, monkeypatch):
    coordinator, parent = host
    review = await reviewed(coordinator, "workspace.backup.create", parent)
    original = workspace_recovery.write_workspace_archive_new

    def write(snapshot, target, operation_id, checkpoint, **kwargs):
        def interleave(stage, write=False):
            if stage == "workspace_restore_before_publish":
                (coordinator.service._workspace_path / "selected.txt").write_text("CAMBIÓ")
            checkpoint(stage, write)
        return original(snapshot, target, operation_id, interleave, **kwargs)
    monkeypatch.setattr(workspace_recovery, "write_workspace_archive_new", write)
    operation = await coordinator.workspace_backup_create(params(review))
    await decision(coordinator, True)
    result = await finish(coordinator, operation["operation_id"])
    assert result["phase"] == "failed" and result["receipt_durable"], result
    assert not (parent / "archive").exists()
    assert not result["cleanup_owner_pending"]
