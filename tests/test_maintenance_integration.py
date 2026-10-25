import asyncio
import threading

import pytest

from jarvis_core import maintenance
from jarvis_core.host_profile import HostProfileError
from jarvis_core.recovery import create_backup
from jarvis_core.recovery_fs import no_replace
from jarvis_core.service import JarvisService


class _OwnedFixturePeer:
    async def aclose(self):
        pass


@pytest.fixture
async def host(tmp_path):
    root = tmp_path / "profile"
    root.mkdir(mode=0o700)
    service = JarvisService(root, _OwnedFixturePeer())
    service.store.create_session("Sólo fixture de revisión")
    coordinator = service.maintenance_coordinator
    try:
        yield coordinator, tmp_path
    finally:
        await service.aclose()


async def allow_synthetic_owner(coordinator):
    for _ in range(100):
        pending = (await coordinator.pending({}))["pending"]
        if pending:
            break
        await asyncio.sleep(0)
    assert len(pending) == 1
    row = pending[0]
    result = await coordinator.decide(
        {
            "approval_id": row["approval_id"],
            "digest": row["digest"],
            "allow": True,
            "profile_id": row["scope"]["profile_id"],
            "generation": row["scope"]["generation"],
        }
    )
    assert result["accepted"]


async def new_backup(coordinator, parent, name="backup"):
    review = await coordinator.review_backup({"destination_parent": str(parent), "name": name})
    return review, await coordinator.backup(
        {"review_id": review["review_id"], "review_digest": review["review_digest"]}
    )


async def finish(coordinator, operation_id):
    task = coordinator._operations[operation_id].task
    await asyncio.wait_for(asyncio.shield(task), 5)
    return await coordinator.status({"operation_id": operation_id})


@pytest.mark.asyncio
async def test_real_backup_roundtrip_review_approval_core_and_durable_receipt(host):
    coordinator, parent = host
    _, operation = await new_backup(coordinator, parent)
    await allow_synthetic_owner(coordinator)
    result = await finish(coordinator, operation["operation_id"])
    assert result["phase"] == "complete", result
    assert result["receipt"]["effect_status"] == "published_verified_bytes"
    assert result["receipt_durable"]
    assert (parent / "backup" / "manifest.json").is_file()


@pytest.mark.asyncio
async def test_initial_journal_failure_never_leaves_unstartable_awaiting_owner(host, monkeypatch):
    coordinator, parent = host
    review = await coordinator.review_backup({"destination_parent": str(parent), "name": "backup"})
    original = coordinator.journal.record

    def fail(*args, **kwargs):
        raise OSError("synthetic_journal_failure")

    monkeypatch.setattr(coordinator.journal, "record", fail)
    with pytest.raises(OSError, match="synthetic_journal_failure"):
        await coordinator.backup(
            {"review_id": review["review_id"], "review_digest": review["review_digest"]}
        )
    rows = (await coordinator.status({}))["operations"]
    assert not any(row["phase"] == "awaiting_owner" and not row["worker_pending"] for row in rows)
    monkeypatch.setattr(coordinator.journal, "record", original)
    assert not (parent / "backup").exists()


@pytest.mark.asyncio
async def test_unknown_publication_is_not_reported_as_failed(host, monkeypatch):
    coordinator, parent = host

    def lost_ack(directory, old, new):
        no_replace(directory, old, new)
        raise OSError("synthetic_lost_ack")

    def injected(bundle, target, context):
        return create_backup(bundle, target, context, publish=lost_ack)

    monkeypatch.setattr(maintenance, "create_backup", injected)
    _, operation = await new_backup(coordinator, parent)
    await allow_synthetic_owner(coordinator)
    result = await finish(coordinator, operation["operation_id"])
    assert result["receipt"]["status"] == "unknown"
    assert result["phase"] in {"publication_unknown", "cleanup_pending"}
    assert (parent / "backup" / ".pending").is_file()


@pytest.mark.asyncio
async def test_restore_calls_real_signature_and_reports_only_staged_disarmed(host):
    coordinator, parent = host
    _, operation = await new_backup(coordinator, parent)
    await allow_synthetic_owner(coordinator)
    result = await finish(coordinator, operation["operation_id"])
    assert result["phase"] == "complete", result
    verification = await coordinator.verify({"backup_path": str(parent / "backup")})
    verified = await finish(coordinator, verification["operation_id"])
    assert verified["phase"] == "complete", verified
    review = await coordinator.review_restore(
        {"verification_id": verified["result"]["verification_id"],
         "destination_parent": str(parent), "name": "restored"}
    )
    operation = await coordinator.restore(
        {"review_id": review["review_id"], "review_digest": review["review_digest"]}
    )
    await allow_synthetic_owner(coordinator)
    result = await finish(coordinator, operation["operation_id"])
    assert result["receipt"] is not None
    assert result["phase"] == "staged_disarmed"
    assert result["receipt"]["effect_status"] == "new_disarmed_profile"
    assert (parent / "restored" / "restore_record.json").is_file()


@pytest.mark.asyncio
async def test_cancelled_coordinator_retains_actual_unknown_worker_receipt(host, monkeypatch):
    coordinator, parent = host
    dispatched, release = threading.Event(), threading.Event()

    def gated_publish(directory, old, new):
        no_replace(directory, old, new)
        dispatched.set()
        assert release.wait(3), "synthetic publication gate not released"

    def injected(bundle, target, context):
        return create_backup(bundle, target, context, publish=gated_publish)

    monkeypatch.setattr(maintenance, "create_backup", injected)
    _, operation = await new_backup(coordinator, parent)
    await allow_synthetic_owner(coordinator)
    row = coordinator._operations[operation["operation_id"]]
    try:
        assert await asyncio.to_thread(dispatched.wait, 2)
        row.task.cancel()
        for _ in range(100):
            if row.cancellation.is_set():
                break
            await asyncio.sleep(0)
        assert row.cancellation.is_set()
    finally:
        release.set()
    result = await finish(coordinator, operation["operation_id"])
    assert result["receipt"] is not None
    assert result["receipt"]["effect_status"] == "unknown"
    assert result["phase"] in {"publication_unknown", "cleanup_pending"}
    assert (parent / "backup" / ".pending").exists()


@pytest.mark.asyncio
async def test_final_journal_failure_keeps_published_receipt_without_replay(host, monkeypatch):
    coordinator, parent = host
    original = coordinator.journal.record

    def fail_terminal(op, purpose, digest, phase, receipt=None):
        if receipt is not None:
            raise OSError("synthetic_final_journal_failure")
        return original(op, purpose, digest, phase, receipt)

    monkeypatch.setattr(coordinator.journal, "record", fail_terminal)
    review, operation = await new_backup(coordinator, parent)
    await allow_synthetic_owner(coordinator)
    result = await finish(coordinator, operation["operation_id"])
    assert result["phase"] == "publication_unknown" and not result["receipt_durable"]
    assert result["receipt"]["effect_status"] == "published_verified_bytes"
    repeated = await coordinator.backup(
        {"review_id": review["review_id"], "review_digest": review["review_digest"]}
    )
    assert repeated["operation_id"] == operation["operation_id"]
    assert repeated["receipt"] == result["receipt"]
    assert not (parent / "backup" / ".pending").exists()


@pytest.mark.asyncio
async def test_review_exact_expiry_is_rejected_before_ticket_or_journal(host):
    coordinator, parent = host
    now = [coordinator.clock()]
    coordinator.clock = lambda: now[0]
    review = await coordinator.review_backup({"destination_parent": str(parent), "name": "backup"})
    now[0] += 120
    with pytest.raises(maintenance.RpcError, match="caducó"):
        await coordinator.backup(
            {"review_id": review["review_id"], "review_digest": review["review_digest"]}
        )
    assert not (await coordinator.status({}))["operations"]
    assert not (await coordinator.pending({}))["pending"]
    assert not (parent / "backup").exists()


@pytest.mark.asyncio
async def test_review_expiry_while_awaiting_owner_cannot_renew_deadline(host):
    coordinator, parent = host
    now = [coordinator.clock()]
    coordinator.clock = lambda: now[0]
    _review, operation = await new_backup(coordinator, parent)
    now[0] += 121
    await allow_synthetic_owner(coordinator)
    result = await finish(coordinator, operation["operation_id"])
    assert result["phase"] == "cancelled"
    assert result["receipt"]["error_code"] == "recovery_deadline_exceeded"
    assert result["receipt"]["effect_status"] == "no_published_effect"
    assert not (parent / "backup").exists()
    assert not list(parent.glob(".jarvis-stage-*"))


@pytest.mark.asyncio
async def test_worker_queue_delay_consumes_existing_effect_budget(host, monkeypatch):
    coordinator, parent = host
    now = [coordinator.clock()]
    coordinator.clock = lambda: now[0]
    original = coordinator.gate.run_worker

    async def delayed(cut, callback, cancellation):
        now[0] += 21
        return await original(cut, callback, cancellation)

    monkeypatch.setattr(coordinator.gate, "run_worker", delayed)
    _, operation = await new_backup(coordinator, parent)
    await allow_synthetic_owner(coordinator)
    result = await finish(coordinator, operation["operation_id"])
    assert result["phase"] == "cancelled"
    assert result["receipt"]["error_code"] == "recovery_deadline_exceeded"
    assert result["receipt"]["effect_status"] == "no_published_effect"
    assert not (parent / "backup").exists()


@pytest.mark.asyncio
async def test_owner_denial_never_publishes_or_replays_same_review(host):
    coordinator, parent = host
    review, operation = await new_backup(coordinator, parent)
    for _ in range(100):
        rows = (await coordinator.pending({}))["pending"]
        if rows:
            break
        await asyncio.sleep(0)
    pending = rows[0]
    assert (await coordinator.decide({
        "approval_id": pending["approval_id"], "digest": pending["digest"], "allow": False,
        "profile_id": pending["scope"]["profile_id"],
        "generation": pending["scope"]["generation"],
    }))["accepted"]
    settled = await finish(coordinator, operation["operation_id"])
    assert settled["phase"] == "denied" and settled["receipt_durable"]
    assert not (parent / "backup").exists()
    again = await coordinator.backup({
        "review_id": review["review_id"], "review_digest": review["review_digest"]})
    assert again["operation_id"] == operation["operation_id"] and again["phase"] == "denied"
    assert not (await coordinator.pending({}))["pending"]


@pytest.mark.asyncio
async def test_returned_review_metadata_cannot_retarget_the_frozen_approval(host):
    coordinator, parent = host
    review = await coordinator.review_backup({"destination_parent": str(parent), "name": "backup"})
    review["metadata"]["destination_name"] = "other"
    review["metadata"]["source"][0]["bytes"] = 999999
    operation = await coordinator.backup({
        "review_id": review["review_id"], "review_digest": review["review_digest"]})
    await allow_synthetic_owner(coordinator)
    result = await finish(coordinator, operation["operation_id"])
    assert result["phase"] == "complete"
    assert (parent / "backup" / "manifest.json").exists()
    assert not (parent / "other").exists()


@pytest.mark.asyncio
async def test_stale_sources_fail_before_ticket_or_journal(host):
    coordinator, parent = host
    review = await coordinator.review_backup({"destination_parent": str(parent), "name": "backup"})
    # An independent committed change is not assumed to advance the host epoch.
    coordinator.service.store.create_session("Cambio externo sintético al corte")
    with pytest.raises(HostProfileError, match="profile_sources_changed"):
        await coordinator.backup({
            "review_id": review["review_id"], "review_digest": review["review_digest"]})
    assert not (await coordinator.status({}))["operations"]
    assert not (await coordinator.pending({}))["pending"]
    assert not (parent / "backup").exists()


@pytest.mark.asyncio
async def test_appearing_destination_is_preserved_before_requesting_approval(host):
    coordinator, parent = host
    review = await coordinator.review_backup({"destination_parent": str(parent), "name": "backup"})
    path = parent / "backup"
    path.write_bytes(b"OTRO ARCHIVO SINTETICO")
    with pytest.raises(OSError, match="recovery_destination_exists"):
        await coordinator.backup({
            "review_id": review["review_id"], "review_digest": review["review_digest"]})
    assert path.read_bytes() == b"OTRO ARCHIVO SINTETICO"
    assert not (await coordinator.status({}))["operations"]
    assert not (await coordinator.pending({}))["pending"]


@pytest.mark.asyncio
async def test_readonly_diagnostic_uses_host_snapshots_without_creating_absent_stores(host):
    coordinator, _parent = host
    root = coordinator.service.directory
    before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns)
              for path in root.iterdir() if path.is_file()}
    operation = await coordinator.doctor({})
    settled = await finish(coordinator, operation["operation_id"])
    assert settled["phase"] == "complete", settled
    report = settled["result"]["report"]
    state = next(check for check in report["checks"] if check["role"] == "state")
    assert state["integrity"] == "ok" and state["inspection_source"] == "host_ram_snapshot"
    after = {path.name: (path.read_bytes(), path.stat().st_mtime_ns)
             for path in root.iterdir() if path.is_file()}
    assert after == before
    assert not (root / "memory.sqlite3").exists()
    assert not (root / "schedules.sqlite3").exists()
    assert not (await coordinator.pending({}))["pending"]
