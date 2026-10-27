"""Real synthetic restore/host/authority paths; no owner data or SDK accounts."""
import asyncio
import fcntl
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

import pytest
from test_activation import prepared as prepared
from test_activation import snapshot
from test_maintenance_integration import allow_synthetic_owner, finish
from test_recovery import bundle as bundle
from test_recovery import ctx
from test_service import ModelPeer

import jarvis_core.activation as core
import jarvis_core.activation_management as module
from jarvis_core.activation import activate_prepared_profile, review_prepared_profile
from jarvis_core.host_profile import ExpectedRestore, HostProfileError, ProfileLease
from jarvis_core.protocol import RpcError
from jarvis_core.service import JarvisService


@pytest.fixture
async def host(tmp_path):
    root = tmp_path / "host"
    root.mkdir(mode=0o700)
    service = JarvisService(root, ModelPeer())
    service.store.create_session("Fixture ajeno al perfil preparado")
    try:
        yield service, service.maintenance_coordinator
    finally:
        await service.aclose()


async def reviewed(coordinator, prepared):
    job = await coordinator.review_activation({"profile_path": str(prepared)})
    row = await finish(coordinator, job["operation_id"])
    assert row["phase"] == "complete", row
    return row["result"]["review"]


async def admitted(coordinator, prepared):
    review = await reviewed(coordinator, prepared)
    operation = await coordinator.activate({key: review[key] for key in
                                             ("review_id", "review_digest")})
    await allow_synthetic_owner(coordinator)
    return review, await finish(coordinator, operation["operation_id"])


async def test_real_nonempty_activation_review_is_readonly_and_receipt_is_distinct(host, prepared):
    service, coordinator = host
    before = snapshot(prepared)
    review = await reviewed(coordinator, prepared)
    assert snapshot(prepared) == before
    assert review["metadata"]["root"] == str(prepared)
    assert review["metadata"]["workspace_policy"] == "new_private_empty"
    assert review["metadata"]["credentials_included"] is False
    assert review["metadata"]["authenticity"] == "not_established"
    assert 0 < review["expires_in_seconds"] <= 120
    assert any(row["role"] == "state" and row["counts"]
               for row in review["metadata"]["resources"])
    params = {key: review[key] for key in ("review_id", "review_digest")}
    first = await coordinator.activate(params)
    await allow_synthetic_owner(coordinator)
    result = await finish(coordinator, first["operation_id"])
    assert result["phase"] == "activated_not_opened", result
    assert result["receipt_durable"] and not result["cleanup_owner_pending"]
    assert result["receipt"]["effect_status"] == "local_admission_published"
    assert result["receipt"]["services_started"] is False
    assert result["receipt"]["grants_restored"] is False
    assert "target_name" not in result["receipt"]
    assert (await coordinator.activate(params))["operation_id"] == first["operation_id"]
    assert not (prepared / "service.sock").exists()
    assert not (prepared / "provider").exists()
    assert not service.provider.calls
    assert (prepared / "restore_record.json").read_bytes() == before["restore_record.json"][-1]
    history = coordinator.journal.document.read()["operations"]
    assert history[-1]["receipt"] == result["receipt"]


@pytest.mark.parametrize("decision", ["deny", "cancel", "revoke"])
async def test_owner_decision_and_revocation_leave_prepared_namespace_unchanged(
        host, prepared, decision):
    service, coordinator = host
    before = snapshot(prepared)
    review = await reviewed(coordinator, prepared)
    operation = await coordinator.activate({key: review[key] for key in
                                             ("review_id", "review_digest")})
    async with asyncio.timeout(2):
        while not (pending := (await coordinator.pending({}))["pending"]):
            await asyncio.sleep(0)
    row = pending[0]
    if decision == "deny":
        await coordinator.decide({"approval_id": row["approval_id"], "digest": row["digest"],
                                  "allow": False, **row["scope"]})
    elif decision == "cancel":
        await coordinator.cancel({"operation_id": operation["operation_id"]})
    else:
        service.authority.revoke_host(service.profile_lease.scope)
    result = await finish(coordinator, operation["operation_id"])
    assert result["phase"] in {"denied", "cancelled"}
    assert snapshot(prepared) == before
    assert not (await coordinator.pending({}))["pending"]


async def test_target_is_current_profile_rejected_before_target_lock_or_card(host):
    service, coordinator = host
    before = snapshot(service.directory)
    job = await coordinator.review_activation({"profile_path": str(service.directory)})
    result = await finish(coordinator, job["operation_id"])
    assert result["error_code"] == "activation_target_is_current_profile"
    assert snapshot(service.directory) == before
    assert not (await coordinator.pending({}))["pending"]


async def test_edited_review_target_does_not_publish_after_approval(host, prepared):
    _, coordinator = host
    review = await reviewed(coordinator, prepared)
    operation = await coordinator.activate({key: review[key] for key in
                                             ("review_id", "review_digest")})
    (prepared / "configuration.json").write_bytes(b"{}")
    await allow_synthetic_owner(coordinator)
    result = await finish(coordinator, operation["operation_id"])
    assert result["phase"] == "failed"
    assert not (prepared / "restore_admission.json").exists()


async def test_initial_activation_journal_failure_has_no_card_or_effect(
        host, prepared, monkeypatch):
    _, coordinator = host
    review = await reviewed(coordinator, prepared)
    before = snapshot(prepared)
    def denied(*args, **kwargs):
        raise OSError("fixture journal")
    monkeypatch.setattr(coordinator.journal, "record", denied)
    with pytest.raises(OSError):
        await coordinator.activate({key: review[key] for key in ("review_id", "review_digest")})
    assert snapshot(prepared) == before
    assert not (await coordinator.pending({}))["pending"]


async def test_lost_publication_ack_never_replays_or_relabels_unknown(host, prepared, monkeypatch):
    _, coordinator = host
    calls = []
    def injected(review, context):
        def lost_ack(directory, old, new):
            core.no_replace(directory, old, new)
            raise OSError("fixture lost publication acknowledgement")
        calls.append(context.operation_id)
        return activate_prepared_profile(review, context, publish=lost_ack)
    monkeypatch.setattr(module, "activate_prepared_profile", injected)
    review, result = await admitted(coordinator, prepared)
    assert result["phase"] == "cleanup_pending", result
    assert result["cleanup_owner_pending"]
    original = result["receipt"]
    assert original["status"] == "unknown"
    assert original["effect_status"] == "publication_unknown"
    again = await coordinator.activate({key: review[key] for key in
                                         ("review_id", "review_digest")})
    assert again["receipt"] == original and len(calls) == 1
    await coordinator.cleanup({"operation_id": result["operation_id"]})
    cleaned = await finish(coordinator, result["operation_id"])
    assert not cleaned["cleanup_owner_pending"]
    job = await coordinator.inspect_activation({"profile_path": str(prepared)})
    readback = await finish(coordinator, job["operation_id"])
    assert readback["result"]["admission"]["state"] == "local_present_valid"
    assert readback["result"]["admission"]["open_available"] is True
    status = await coordinator.status({"operation_id": result["operation_id"]})
    assert status["receipt"] == original


async def test_cleanup_owner_retains_lock_blocks_new_jobs_and_cleanup_preserves_receipt(
        host, prepared, monkeypatch):
    _, coordinator = host
    original_fsync = os.fsync
    target_inode = prepared.stat().st_ino
    refuse = True
    def uncertain(fd):
        if refuse and os.fstat(fd).st_ino == target_inode and (
                prepared / "restore_admission.json").exists():
            raise OSError("fixture directory sync unknown")
        return original_fsync(fd)
    with patch.object(core.os, "fsync", uncertain):
        _, result = await admitted(coordinator, prepared)
        assert result["phase"] == "cleanup_pending", result
        assert result["cleanup_owner_pending"]
        original = result["receipt"]
        with pytest.raises(HostProfileError, match="profile_busy"):
            ProfileLease.acquire(prepared)
        with pytest.raises(RpcError):
            await coordinator.review_activation({"profile_path": str(prepared)})
        job = await coordinator.inspect_activation({"profile_path": str(prepared)})
        readback = await finish(coordinator, job["operation_id"])
        assert not readback["result"]["admission"]["open_available"]
        await coordinator.cleanup({"operation_id": result["operation_id"]})
        failed = await finish(coordinator, result["operation_id"])
        assert failed["cleanup_owner_pending"]
        assert failed["receipt"] == original
        refuse = False
        await coordinator.cleanup({"operation_id": result["operation_id"]})
        finished = await finish(coordinator, result["operation_id"])
        assert not finished["cleanup_owner_pending"]
        assert finished["receipt"] == original
        assert finished["phase"] == "publication_unknown"
        lease = ProfileLease.acquire(prepared)
        lease.close()


async def test_review_expiry_includes_analysis_time_and_does_not_renew_owner_wait(host, prepared):
    _, coordinator = host
    now = [100.0]
    coordinator.clock = lambda: now[0]
    review = await reviewed(coordinator, prepared)
    assert review["expires_in_seconds"] == 120
    row = coordinator._reviews[review["review_id"]]
    coordinator._reviews[review["review_id"]] = replace(row, expires=100.03)
    operation = await coordinator.activate({key: review[key] for key in
                                             ("review_id", "review_digest")})
    await asyncio.sleep(.06)
    now[0] = 101
    result = await finish(coordinator, operation["operation_id"])
    assert result["phase"] in {"denied", "failed"}
    assert not (await coordinator.pending({}))["pending"]
    assert not (prepared / "restore_admission.json").exists()


def test_valid_local_admission_bootstraps_and_expected_restrictions_are_exact(prepared):
    review = review_prepared_profile(prepared, ctx())
    receipt = activate_prepared_profile(review, ctx())
    expected = ExpectedRestore(receipt.target_identity, receipt.marker_sha256,
                               receipt.admission_sha256, receipt.operation_id)
    lease = ProfileLease.acquire(prepared, expected_restore=expected)
    assert lease.admission.operation_id == receipt.operation_id
    lease.close()
    before = snapshot(prepared)
    with pytest.raises(HostProfileError, match="profile_expected_restore_mismatch"):
        ProfileLease.acquire(prepared, expected_restore=replace(expected, operation_id="e"*32))
    assert snapshot(prepared) == before


def test_bootstrap_revalidates_admission_before_store_constructors(prepared):
    receipt = activate_prepared_profile(review_prepared_profile(prepared, ctx()), ctx())
    lease = ProfileLease.acquire(prepared)
    (prepared / "restore_admission.json").unlink()
    with pytest.raises(HostProfileError, match="profile_activation_required"):
        JarvisService(prepared, ModelPeer(), profile_lease=lease)
    assert receipt.status == "completed"


async def test_service_shutdown_cleans_retained_owner_without_replaying_publish(
        tmp_path, prepared, monkeypatch):
    root = tmp_path / "shutdown-host"
    root.mkdir(mode=0o700)
    service = JarvisService(root, ModelPeer())
    coordinator = service.maintenance_coordinator
    calls = []
    def uncertain(review, context):
        def publish(directory, old, new):
            calls.append(context.operation_id)
            core.no_replace(directory, old, new)
            raise OSError("fixture lost acknowledgement")
        return activate_prepared_profile(review, context, publish=publish)
    monkeypatch.setattr(module, "activate_prepared_profile", uncertain)
    _, result = await admitted(coordinator, prepared)
    assert result["cleanup_owner_pending"]
    original = result["receipt"]
    await service.aclose()
    assert len(calls) == 1
    assert not coordinator._operations[result["operation_id"]].cleanup_owner
    assert coordinator._operations[result["operation_id"]].snapshot()["receipt"] == original
    lease = ProfileLease.acquire(prepared)
    lease.close()


@pytest.mark.parametrize("worker_threads", [None, 1], ids=["default-pool", "single-worker"])
async def test_cleanup_worker_survives_repeated_awaiter_cancel_without_dropping_host_lease(
        host, prepared, monkeypatch, worker_threads):
    _, coordinator = host
    loop = asyncio.get_running_loop()
    if worker_threads is not None:
        # The event loop retains this executor until host teardown is complete.
        loop.set_default_executor(ThreadPoolExecutor(max_workers=worker_threads))
    def uncertain(review, context):
        def publish(directory, old, new):
            core.no_replace(directory, old, new)
            raise OSError("fixture lost acknowledgement")
        return activate_prepared_profile(review, context, publish=publish)
    monkeypatch.setattr(module, "activate_prepared_profile", uncertain)
    _, result = await admitted(coordinator, prepared)
    operation = coordinator._operations[result["operation_id"]]
    owner = operation.cleanup_owner
    entered, release = asyncio.Event(), threading.Event()
    cleanup_budget = 10
    original_cleanup = owner.cleanup
    def gated():
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(cleanup_budget), "fixture cleanup gate not released"
        original_cleanup()
    monkeypatch.setattr(owner, "cleanup", gated)
    await coordinator.cleanup({"operation_id": result["operation_id"]})
    entered_waiter = asyncio.create_task(entered.wait())
    try:
        # Receiving the worker's marker must not occupy the executor slot that
        # owner.cleanup needs in order to emit it.
        await asyncio.wait({entered_waiter, operation.task}, timeout=cleanup_budget,
                           return_when=asyncio.FIRST_COMPLETED)
        assert entered.is_set(), operation.snapshot()
        operation.task.cancel()
        await asyncio.sleep(0)
        operation.task.cancel()
        await asyncio.sleep(0)
        assert operation.cleanup_owner is owner
        assert coordinator.gate._admitted
        assert not operation.task.done()
        with pytest.raises(RpcError):
            await coordinator.inspect_activation({"profile_path": str(prepared)})
    finally:
        release.set()
        entered_waiter.cancel()
        await asyncio.gather(entered_waiter, return_exceptions=True)
    completed = await finish(coordinator, result["operation_id"])
    assert not completed["cleanup_owner_pending"]
    assert completed["receipt"] == result["receipt"]
    assert not coordinator.gate._admitted


async def test_final_journal_failure_preserves_physical_receipt_and_prevents_ack_claim(
        host, prepared, monkeypatch):
    _, coordinator = host
    record = coordinator.journal.record
    def fail_final(operation_id, purpose, digest, phase, receipt=None):
        if purpose == "profile.restore.activate" and receipt is not None:
            raise OSError("fixture final receipt write failed")
        return record(operation_id, purpose, digest, phase, receipt)
    monkeypatch.setattr(coordinator.journal, "record", fail_final)
    _, result = await admitted(coordinator, prepared)
    assert result["phase"] == "publication_unknown"
    assert not result["receipt_durable"]
    assert result["receipt"]["status"] == "completed"
    assert result["error_code"] == "maintenance_receipt_unconfirmed"
    assert (prepared / "restore_admission.json").is_file()


async def test_historical_unknown_does_not_become_completed_or_permanently_block_fresh_inspection(
        host, prepared, monkeypatch):
    service, coordinator = host
    def uncertain(review, context):
        def publish(directory, old, new):
            core.no_replace(directory, old, new)
            raise OSError("fixture lost acknowledgement")
        return activate_prepared_profile(review, context, publish=publish)
    monkeypatch.setattr(module, "activate_prepared_profile", uncertain)
    _, original = await admitted(coordinator, prepared)
    assert original["receipt"]["status"] == "unknown"
    await service.aclose()  # Real retained Owner cleanup is confirmed before shutdown.
    restarted = JarvisService(service.directory, ModelPeer())
    try:
        coordinator = restarted.maintenance_coordinator
        previous = coordinator.journal.previous[-1]
        assert previous["receipt"] == original["receipt"]
        assert previous["receipt"]["status"] == "unknown"
        job = await coordinator.inspect_activation({"profile_path": str(prepared)})
        result = await finish(coordinator, job["operation_id"])
        assert result["result"]["admission"]["open_available"]
        assert coordinator.journal.previous[-1] == previous
        assert not restarted.provider.calls
    finally:
        await restarted.aclose()


@pytest.mark.parametrize("ambiguity", ["temporary", "unsafe_lock"])
async def test_current_namespace_ambiguity_blocks_inspection_and_bootstrap_without_cleanup(
        host, prepared, ambiguity):
    _, coordinator = host
    await admitted(coordinator, prepared)
    if ambiguity == "temporary":
        path = prepared / (".jarvis-activation-" + "f"*32 + ".pending")
        path.write_bytes(b"UNKNOWN OWNERSHIP FIXTURE")
        path.chmod(0o600)
    else:
        path = prepared / "profile.lock"
        path.write_bytes(b"UNKNOWN LOCK FIXTURE")
    before = snapshot(prepared)
    job = await coordinator.inspect_activation({"profile_path": str(prepared)})
    result = await finish(coordinator, job["operation_id"])
    assert result["phase"] == "failed", result
    with pytest.raises(HostProfileError):
        ProfileLease.acquire(prepared)
    assert snapshot(prepared) == before


async def test_current_valid_admission_does_not_bypass_bootstrap_busy_flock(host, prepared):
    _, coordinator = host
    await admitted(coordinator, prepared)
    owner = ProfileLease.acquire(prepared)
    try:
        job = await coordinator.inspect_activation({"profile_path": str(prepared)})
        result = await finish(coordinator, job["operation_id"])
        assert result["result"]["admission"]["state"] == "local_present_valid"
        with pytest.raises(HostProfileError, match="profile_busy"):
            JarvisService(prepared, ModelPeer())
    finally:
        owner.close()


@pytest.mark.parametrize("stop", ["cancel", "deadline", "barrier"])
def test_doctor_admission_callback_stop_keeps_cancel_deadline_and_barrier_semantics(
        prepared, stop):
    import threading

    from jarvis_core.doctor import DoctorContext, DoctorInventory, inspect_report
    activate_prepared_profile(review_prepared_profile(prepared, ctx()), ctx())
    cancellation = threading.Event()
    now = [0.0]
    calls = []
    def guard(stage, write=False):
        calls.append(stage)
        if stage == "restore_admission" and calls.count(stage) >= 2:
            if stop == "cancel":
                cancellation.set()
            elif stop == "deadline":
                now[0] = 1000
            else:
                raise ValueError("fixture cut replaced")
    context = DoctorContext(guard, cancellation, clock=lambda: now[0])
    info = prepared.stat()
    inventory = DoctorInventory("fixture", 0, prepared, (info.st_dev,info.st_ino),
                                "offline_quiescent", roles=("restore_record",))
    report = inspect_report(inventory, context)
    assert report.status == ("cancelled" if stop == "cancel" else "unknown")
    assert {"cancel":"doctor_cancelled", "deadline":"doctor_deadline",
            "barrier":"doctor_barrier_changed"}[stop] in report.blockers
    assert all(check.code != "doctor_restore_admission_invalid" for check in report.checks)


async def test_restored_fast_source_guard_never_rereads_admission_bodies_and_detects_change(
        prepared, monkeypatch):
    import jarvis_core.host_profile as profile_module
    from jarvis_core.host_profile import freeze_sources
    activate_prepared_profile(review_prepared_profile(prepared, ctx()), ctx())
    service = JarvisService(prepared, ModelPeer())
    original = profile_module.validate_bootstrap_admission
    calls = []
    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(profile_module, "validate_bootstrap_admission", counted)
    try:
        with service.profile_gate.exclusive() as cut:
            frozen = freeze_sources(service, cut)
            assert calls
            before = len(calls)
            for _ in range(50):
                frozen.assert_current(cut)
            assert len(calls) == before
            path = prepared / "restore_admission.json"
            info = path.stat()
            path.write_bytes(path.read_bytes())
            os.utime(path, ns=(info.st_atime_ns,info.st_mtime_ns))
            with pytest.raises(HostProfileError, match="profile_bootstrap_changed"):
                frozen.assert_current(cut)
    finally:
        await service.aclose()


async def test_target_becomes_kernel_busy_while_owner_card_waits_keeps_exact_originals(
        host, prepared):
    """The review is not a lease on the selected target's future availability."""
    _, coordinator = host
    descriptor = os.open(prepared / "profile.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        review = await reviewed(coordinator, prepared)
        before = snapshot(prepared)
        operation = await coordinator.activate({key: review[key] for key in
                                                ("review_id", "review_digest")})
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        await allow_synthetic_owner(coordinator)
        result = await finish(coordinator, operation["operation_id"])
        assert result["phase"] == "failed", result
        assert result["error_code"] == "activation_profile_busy"
        assert result["receipt"]["effect_status"] == "not_activated"
        assert not result["cleanup_owner_pending"]
        assert snapshot(prepared) == before
        assert not (await coordinator.pending({}))["pending"]
        # The failed attempt neither releases our lock nor retargets another root.
        with pytest.raises(BlockingIOError):
            with open(prepared / "profile.lock", "r+b") as other:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(descriptor)


async def test_selected_physical_target_replaced_during_owner_wait_is_not_followed(
        host, prepared):
    _, coordinator = host
    review = await reviewed(coordinator, prepared)
    operation = await coordinator.activate({key: review[key] for key in
                                            ("review_id", "review_digest")})
    moved = prepared.with_name("original-prepared-fixture")
    prepared.rename(moved)
    prepared.mkdir(mode=0o700)
    sentinel = prepared / "unknown.fixture"
    sentinel.write_bytes(b"THIS REPLACEMENT IS NOT THE REVIEWED TARGET")
    sentinel.chmod(0o600)
    replacement_before, original_before = snapshot(prepared), snapshot(moved)
    await allow_synthetic_owner(coordinator)
    result = await finish(coordinator, operation["operation_id"])
    assert result["phase"] == "failed", result
    assert not result["cleanup_owner_pending"]
    assert snapshot(prepared) == replacement_before
    assert snapshot(moved) == original_before
    assert not (await coordinator.pending({}))["pending"]


@pytest.mark.parametrize("worker_threads", [None, 1], ids=["default-pool", "single-worker"])
async def test_cancelled_activation_awaiter_waits_for_real_worker_and_keeps_unknown_receipt(
        host, prepared, monkeypatch, worker_threads):
    """Cancelling after dispatch cannot erase the actual admission publication."""
    _, coordinator = host
    loop = asyncio.get_running_loop()
    if worker_threads is not None:
        # This loop owns the executor through the host fixture's teardown. A
        # blocking notification wait must never consume its only worker.
        loop.set_default_executor(ThreadPoolExecutor(max_workers=worker_threads))
    published, release = asyncio.Event(), threading.Event()
    publication_budget = 10
    calls = []

    def injected(review, context):
        def publish(directory, old, new):
            calls.append(context.operation_id)
            core.no_replace(directory, old, new)
            loop.call_soon_threadsafe(published.set)
            assert release.wait(publication_budget), "fixture publication gate not released"
        return activate_prepared_profile(review, context, publish=publish)

    monkeypatch.setattr(module, "activate_prepared_profile", injected)
    review = await reviewed(coordinator, prepared)
    operation = await coordinator.activate({key: review[key] for key in
                                            ("review_id", "review_digest")})
    await allow_synthetic_owner(coordinator)
    row = coordinator._operations[operation["operation_id"]]
    publication_waiter = asyncio.create_task(published.wait())
    try:
        # The read-side handshake stays on the event loop. Waiting in to_thread
        # can occupy the same executor slot the real publisher still needs.
        await asyncio.wait({publication_waiter, row.task}, timeout=publication_budget,
                           return_when=asyncio.FIRST_COMPLETED)
        assert published.is_set(), row.snapshot()
        row.task.cancel()
        await asyncio.sleep(0)
        row.task.cancel()
        await asyncio.sleep(0)
        assert not row.task.done() and coordinator.gate._cut is not None
        assert row.cancellation.is_set()
    finally:
        release.set()
        publication_waiter.cancel()
        await asyncio.gather(publication_waiter, return_exceptions=True)
    result = await finish(coordinator, operation["operation_id"])
    assert result["phase"] == "cleanup_pending", result
    assert result["receipt"]["status"] == "unknown"
    assert result["receipt"]["effect_status"] == "publication_unknown"
    assert (prepared / "restore_admission.json").is_file()
    historical_receipt = result["receipt"]
    await coordinator.cleanup({"operation_id": row.operation_id})
    result = await finish(coordinator, row.operation_id)
    assert result["receipt"] == historical_receipt
    assert result["phase"] == "publication_unknown" and not result["cleanup_owner_pending"]
    again = await coordinator.activate({key: review[key] for key in
                                        ("review_id", "review_digest")})
    assert again["receipt"] == historical_receipt and calls == [row.operation_id]
