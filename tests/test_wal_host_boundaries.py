"""Cold WAL host/authority/custody fixtures, never an owner profile or account.

Every approval is synthetic. Crash fixtures finish before profile admission;
uncertain cleanup is injected only into operation-owned private staging.
"""

import asyncio
import copy
import json
import os
import threading
import time
from uuid import uuid4

import pytest
from wal_fixture import make_wal, oracle

from jarvis_core import cold_wal
from jarvis_core.host_profile import HostProfileError, freeze_sources
from jarvis_core.memory import MemoryStore
from jarvis_core.memory_host_read import read_memory_image
from jarvis_core.protocol import RpcError
from jarvis_core.recovery import RecoveryContext, RecoveryLimits
from jarvis_core.scheduler import ScheduleStore
from jarvis_core.service import JarvisService
from jarvis_core.state import StateStore
from jarvis_core.tools import HostCancellation, ToolError


class NoAccount:
    def __init__(self):
        self.calls = 0

    async def account_status(self):
        self.calls += 1
        raise AssertionError("No provider or owner account belongs to WAL recovery")

    login = account_status
    chat = account_status

    async def aclose(self):
        pass


@pytest.fixture
async def host(tmp_path):
    root = tmp_path / "wal-profile"
    make_wal(root, roles=("memory",))
    provider = NoAccount()
    service = JarvisService(root, provider)
    control = {"allow_cleanup": True}
    try:
        yield service, tmp_path, control, provider
    finally:
        control["allow_cleanup"] = True
        await asyncio.wait_for(service.aclose(), 8)


async def finish(coordinator, operation):
    identity = operation if isinstance(operation, str) else operation["operation_id"]
    async with asyncio.timeout(8):
        while True:
            result = await coordinator.status({"operation_id": identity})
            if not result["worker_pending"]:
                return result
            await asyncio.sleep(.001)


async def decide(coordinator, allow=True):
    async with asyncio.timeout(2):
        while not (rows := (await coordinator.pending({}))["pending"]):
            await asyncio.sleep(.001)
    assert len(rows) == 1
    ticket = rows[0]
    reply = await coordinator.decide({"approval_id": ticket["approval_id"],
                                      "digest": ticket["digest"], "allow": allow,
                                      **ticket["scope"]})
    assert reply["accepted"]


async def start(coordinator, parent, kind, *, allow=True):
    if kind == "doctor":
        return await coordinator.doctor({})
    review = await coordinator.review_backup({"destination_parent": str(parent),
                                              "name": "wal-backup"})
    operation = await coordinator.backup({key: review[key] for key in
                                          ("review_id", "review_digest")})
    await decide(coordinator, allow)
    return operation


def hold_cleanup(monkeypatch, service, control):
    original = cold_wal.WalCaptureOwner.cleanup
    seen = []

    def cleanup(owner):
        seen.append((owner, service.profile_gate._cut))
        if not control["allow_cleanup"]:
            raise OSError("SYNTHETIC-WAL-PRIVATE-CLOSE-MESSAGE")
        return original(owner)

    monkeypatch.setattr(cold_wal.WalCaptureOwner, "cleanup", cleanup)
    control["allow_cleanup"] = False
    return seen, original


async def assert_pending_owner(service, result, seen):
    coordinator = service.maintenance_coordinator
    operation = coordinator._operations[result["operation_id"]]
    assert result["phase"] == "cleanup_pending", result
    assert result["cleanup_owner_pending"] and not result["worker_pending"]
    assert operation.capture_cut is operation.cleanup_owner
    held = operation.capture_cut
    owner = held.owner
    assert seen and all(row[0] is owner and row[1] is held.cut for row in seen)
    assert not owner.closed and not held.released
    assert service.profile_gate._cut is held.cut and held.cut.active
    assert held.cut.worker_count == 0 and service.profile_lease._fd >= 0
    assert "SYNTHETIC-WAL-PRIVATE-CLOSE-MESSAGE" not in json.dumps(result)
    assert "profile_maintenance_active" in (await coordinator.status({}))["blockers"]
    assert not (await coordinator.pending({}))["pending"]
    with pytest.raises(RpcError):
        await coordinator.doctor({})
    with pytest.raises(RpcError):
        await coordinator.review_backup({"destination_parent": str(service.directory.parent),
                                         "name": "another-backup"})
    with pytest.raises(RpcError, match="profile_maintenance_busy"):
        await service.handlers()["sessions.list"]({})
    with pytest.raises(HostProfileError, match="profile_maintenance_busy"):
        service.store.list_sessions()
    return operation, held, owner


async def test_wal_review_is_metadata_only_and_explains_confirmed_transaction_scope(
    host, monkeypatch,
):
    service, parent, _control, provider = host
    before = oracle(service.directory)

    def forbidden(*_args, **_kwargs):
        pytest.fail("review initialized a store or captured WAL payload")

    monkeypatch.setattr(cold_wal, "read_wal_image", forbidden)
    monkeypatch.setattr(StateStore, "_open_disk", forbidden)
    monkeypatch.setattr(MemoryStore, "_open_disk", forbidden)
    monkeypatch.setattr(ScheduleStore, "_open", forbidden)
    coordinator = service.maintenance_coordinator
    review = await coordinator.review_backup({"destination_parent": str(parent),
                                              "name": "review-only"})
    metadata = review["metadata"]
    assert metadata["sqlite_recovery"] == [{"role": "memory", "mode": "cold_wal",
                                             "semantics": "confirmed_transactions_only"}]
    memory = next(row for row in metadata["source"] if row["role"] == "memory")
    assert memory["disposition"] == "present"
    assert not (await coordinator.pending({}))["pending"]
    assert service.store._disk is service.memory._disk is None
    assert service.schedule_store._connection is None
    assert oracle(service.directory) == before and provider.calls == 0
    assert not (parent / "review-only").exists()


@pytest.mark.parametrize("kind", ["backup", "doctor"])
async def test_wal_capture_completes_through_host_without_initializing_source_stores(host, kind):
    service, parent, _control, provider = host
    before = oracle(service.directory)
    coordinator = service.maintenance_coordinator
    result = await finish(coordinator, await start(coordinator, parent, kind))
    assert result["phase"] == "complete", result
    assert not result["cleanup_owner_pending"]
    assert service.profile_gate._cut is None
    if kind == "backup":
        assert result["receipt_durable"]
        assert result["receipt"]["effect_status"] == "published_verified_bytes"
        verification = await finish(coordinator, await coordinator.verify({
            "backup_path": str(parent / "wal-backup")}))
        assert verification["phase"] == "complete", verification
    else:
        report = result["result"]["report"]
        memory = next(row for row in report["checks"] if row["role"] == "memory")
        assert memory["integrity"] == "ok"
        assert memory["inspection_source"] == "host_ram_snapshot"
        assert result["receipt"] is None and not result["receipt_durable"]
    assert service.store._disk is service.memory._disk is None
    assert service.schedule_store._connection is None
    assert oracle(service.directory) == before and provider.calls == 0


@pytest.mark.parametrize("kind", ["backup", "doctor"])
async def test_uncertain_wal_cleanup_retains_exact_cut_blocks_new_effects_and_never_recaptures(
    host, monkeypatch, kind,
):
    service, parent, control, provider = host
    before = oracle(service.directory)
    coordinator = service.maintenance_coordinator
    seen, _ = hold_cleanup(monkeypatch, service, control)
    result = await finish(coordinator, await start(coordinator, parent, kind))
    operation, held, owner = await assert_pending_owner(service, result, seen)
    original_receipt = copy.deepcopy(result["receipt"])
    if kind == "backup":
        assert original_receipt["cleanup_pending"]
        assert original_receipt["effect_status"] == "no_published_effect"
        assert result["receipt_durable"]
    else:
        assert result["receipt"] is None and not result["receipt_durable"]
    assert not (parent / "wal-backup").exists()
    assert oracle(service.directory) == before
    await coordinator.cleanup({"operation_id": operation.operation_id})
    failed_cleanup = await finish(coordinator, operation.operation_id)
    assert failed_cleanup["cleanup_owner_pending"]
    assert failed_cleanup["receipt"] == original_receipt
    assert operation.capture_cut is held and service.profile_gate._cut is held.cut
    control["allow_cleanup"] = True
    await coordinator.cleanup({"operation_id": operation.operation_id})
    settled = await finish(coordinator, operation.operation_id)
    assert settled["phase"] in {"failed", "cancelled"}, settled
    assert not settled["cleanup_owner_pending"]
    assert settled["receipt"] == original_receipt
    assert owner.closed and held.released and operation.capture_cut is None
    assert service.profile_gate._cut is None
    assert all(row[0] is owner for row in seen)
    assert oracle(service.directory) == before and provider.calls == 0
    assert not (parent / "wal-backup").exists()


@pytest.mark.parametrize("kind", ["backup", "doctor"])
async def test_repeated_cleanup_awaiter_cancellation_keeps_worker_and_same_cut_until_close(
    host, monkeypatch, kind,
):
    service, parent, control, _provider = host
    coordinator = service.maintenance_coordinator
    seen, original_cleanup = hold_cleanup(monkeypatch, service, control)
    result = await finish(coordinator, await start(coordinator, parent, kind))
    operation, held, owner = await assert_pending_owner(service, result, seen)
    entered, release = threading.Event(), threading.Event()

    def gated_cleanup(value):
        assert value is owner
        assert service.profile_gate._cut is held.cut
        entered.set()
        assert release.wait(4), "synthetic cleanup gate was not released"
        return original_cleanup(value)

    monkeypatch.setattr(cold_wal.WalCaptureOwner, "cleanup", gated_cleanup)
    control["allow_cleanup"] = True
    await coordinator.cleanup({"operation_id": operation.operation_id})
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        operation.task.cancel()
        await asyncio.sleep(0)
        operation.task.cancel()
        await asyncio.sleep(0)
        assert not operation.task.done()
        assert operation.cleanup_owner is held
        assert service.profile_gate._cut is held.cut and held.cut.worker_count == 1
        assert service.profile_lease._fd >= 0
        with pytest.raises(RpcError):
            await coordinator.doctor({})
    finally:
        release.set()
    settled = await finish(coordinator, operation.operation_id)
    assert not settled["cleanup_owner_pending"]
    assert settled["receipt"] == result["receipt"]
    assert service.profile_gate._cut is None and held.released and owner.closed


@pytest.mark.parametrize("kind", ["backup", "doctor"])
@pytest.mark.parametrize("fail_first_shutdown", [False, True])
async def test_shutdown_finishes_only_same_wal_owner_in_another_task_without_another_grant(
    host, monkeypatch, kind, fail_first_shutdown,
):
    service, parent, control, provider = host
    before = oracle(service.directory)
    coordinator = service.maintenance_coordinator
    captured_requests = []
    request_host = coordinator.authority.request_host

    async def record_request(request, *args, **kwargs):
        captured_requests.append(request)
        return await request_host(request, *args, **kwargs)

    monkeypatch.setattr(coordinator.authority, "request_host", record_request)
    seen, original_cleanup = hold_cleanup(monkeypatch, service, control)
    result = await finish(coordinator, await start(coordinator, parent, kind))
    operation, held, owner = await assert_pending_owner(service, result, seen)
    if fail_first_shutdown:
        with pytest.raises(TimeoutError, match="maintenance_cleanup_incomplete"):
            await service.aclose()
        assert operation.cleanup_owner is held and not held.released
        assert service.profile_gate._cut is held.cut and service.profile_lease._fd >= 0
    cleanup_calls = []

    def cleanup(value):
        assert value is owner and service.profile_gate._cut is held.cut
        for request in captured_requests:
            with pytest.raises(ToolError, match="host_authority_required"):
                coordinator.authority.check_host(request)
        cleanup_calls.append(value)
        return original_cleanup(value)

    monkeypatch.setattr(cold_wal.WalCaptureOwner, "cleanup", cleanup)
    control["allow_cleanup"] = True
    await service.aclose()
    assert cleanup_calls == [owner]
    assert operation.cleanup_owner is None and operation.capture_cut is None
    assert operation.snapshot()["receipt"] == result["receipt"]
    assert held.released and owner.closed and service.profile_gate._cut is None
    assert service.profile_lease._fd < 0
    assert len(captured_requests) == (1 if kind == "backup" else 0)
    assert oracle(service.directory) == before and provider.calls == 0


async def test_denied_wal_backup_does_not_capture_or_create_private_owner(host, monkeypatch):
    service, parent, _control, provider = host
    before = oracle(service.directory)

    def forbidden(*_args, **_kwargs):
        pytest.fail("denied operation tried to capture WAL")

    monkeypatch.setattr(cold_wal, "read_wal_image", forbidden)
    coordinator = service.maintenance_coordinator
    result = await finish(coordinator, await start(coordinator, parent, "backup", allow=False))
    assert result["phase"] == "denied" and result["receipt_durable"]
    assert not result["cleanup_owner_pending"]
    assert oracle(service.directory) == before and provider.calls == 0
    assert not (parent / "wal-backup").exists()


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm"])
async def test_changed_wal_family_while_owner_decides_invalidates_before_capture(
    host, monkeypatch, suffix,
):
    service, parent, _control, provider = host
    coordinator = service.maintenance_coordinator
    review = await coordinator.review_backup({"destination_parent": str(parent),
                                              "name": "wal-backup"})
    operation = await coordinator.backup({key: review[key] for key in
                                          ("review_id", "review_digest")})
    path = service.directory / ("memory.sqlite3" + suffix)
    data = path.read_bytes()
    path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    after_fixture_change = oracle(service.directory)

    def forbidden(*_args, **_kwargs):
        pytest.fail("changed review tried to capture WAL")

    monkeypatch.setattr(cold_wal, "read_wal_image", forbidden)
    await decide(coordinator)
    result = await finish(coordinator, operation)
    assert result["phase"] == "failed", result
    assert not result["cleanup_owner_pending"]
    assert service.profile_gate._cut is None
    assert oracle(service.directory) == after_fixture_change and provider.calls == 0
    assert not (parent / "wal-backup").exists()


async def test_backup_queue_delay_does_not_renew_wal_capture_budget(host, monkeypatch):
    service, parent, _control, provider = host
    coordinator = service.maintenance_coordinator
    now = [coordinator.clock()]
    coordinator.clock = lambda: now[0]
    original = service.profile_gate.run_worker

    async def delayed(cut, callback, cancellation):
        now[0] += 21
        return await original(cut, callback, cancellation)

    def forbidden(*_args, **_kwargs):
        pytest.fail("expired operation tried to capture WAL")

    monkeypatch.setattr(service.profile_gate, "run_worker", delayed)
    monkeypatch.setattr(cold_wal, "read_wal_image", forbidden)
    before = oracle(service.directory)
    result = await finish(coordinator, await start(coordinator, parent, "backup"))
    assert result["phase"] == "cancelled", result
    assert result["receipt"]["effect_status"] == "no_published_effect"
    assert result["receipt"]["error_code"] == "recovery_deadline_exceeded"
    assert not result["cleanup_owner_pending"]
    assert oracle(service.directory) == before and provider.calls == 0
    assert not (parent / "wal-backup").exists()


async def test_wal_input_budget_is_checked_before_capture_not_only_main_size(host, monkeypatch):
    service, _parent, _control, provider = host
    before = oracle(service.directory)
    root = service.directory
    family_bytes = sum((root / ("memory.sqlite3" + suffix)).stat().st_size
                       for suffix in ("", "-wal", "-shm"))

    def forbidden(*_args, **_kwargs):
        pytest.fail("oversized WAL family reached the capturer")

    monkeypatch.setattr(cold_wal, "read_wal_image", forbidden)
    cancellation = HostCancellation()
    with service.profile_gate.exclusive() as cut:
        frozen = freeze_sources(service, cut)
        context = RecoveryContext(uuid4().hex, cut.scope.profile_id, cut.scope.generation,
                                  lambda *_args: frozen.assert_current(cut), cancellation,
                                  time.monotonic() + 20)
        outcome = await service.profile_gate.run_worker(
            cut, lambda: frozen.bind(cut, context=context, limits=RecoveryLimits(
                max_total_bytes=family_bytes - 1)), cancellation)
        assert outcome.error is not None
        assert getattr(outcome.error, "owner", None) is None
        assert "limit" in getattr(outcome.error, "code", "")
    assert oracle(service.directory) == before and provider.calls == 0


async def test_wal_read_support_does_not_enable_mutable_memory_reconciliation(host, monkeypatch):
    service, _parent, _control, provider = host
    before = oracle(service.directory)

    def forbidden(*_args, **_kwargs):
        pytest.fail("memory reconciliation borrowed the new WAL reader")

    monkeypatch.setattr(cold_wal, "read_wal_image", forbidden)
    cancellation = HostCancellation()
    with service.profile_gate.exclusive() as cut:
        frozen = freeze_sources(service, cut)
        context = RecoveryContext(uuid4().hex, cut.scope.profile_id, cut.scope.generation,
                                  lambda *_args: frozen.assert_current(cut), cancellation,
                                  time.monotonic() + 20)
        outcome = await service.profile_gate.run_worker(
            cut, lambda: read_memory_image(frozen, cut, context, for_copy=False), cancellation)
        assert isinstance(outcome.error, HostProfileError)
        assert outcome.error.code == "profile_memory_source_unavailable"
    assert service.memory._disk is None
    assert oracle(service.directory) == before and provider.calls == 0


@pytest.mark.parametrize("physical_bytes", [64 * 1024 * 1024 + 1, 128 * 1024 * 1024 + 1])
async def test_review_rejects_oversized_sparse_wal_before_payload_capture_or_native_dto(
    host, monkeypatch, physical_bytes,
):
    service, parent, _control, provider = host
    path = service.directory / "memory.sqlite3-wal"
    # This is a deliberately prohibited synthetic input, not a valid WAL to
    # recover. Extending sparsely lets the metadata-only review reject it
    # without allocating or reading the claimed payload.
    os.truncate(path, physical_bytes)
    info = path.stat()
    expected = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    read = os.pread

    def no_wal_payload(fd, length, offset):
        current = os.fstat(fd)
        assert (current.st_dev, current.st_ino) != expected[:2], "review read WAL payload"
        return read(fd, length, offset)

    def forbidden(*_args, **_kwargs):
        pytest.fail("oversized review invoked native WAL capture")

    monkeypatch.setattr(os, "pread", no_wal_payload)
    monkeypatch.setattr(cold_wal, "read_wal_image", forbidden)
    coordinator = service.maintenance_coordinator
    with pytest.raises(HostProfileError, match="profile_sqlite_image_limit"):
        await coordinator.review_backup({"destination_parent": str(parent),
                                         "name": "too-large"})
    after = path.stat()
    assert (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
            after.st_ctime_ns) == expected
    assert not (await coordinator.pending({}))["pending"]
    assert not (await coordinator.status({}))["operations"]
    assert service.memory._disk is None and service.profile_gate._cut is None
    assert not (parent / "too-large").exists() and provider.calls == 0


async def test_doctor_queue_delay_consumes_original_wal_deadline_before_capture(host, monkeypatch):
    service, _parent, _control, provider = host
    coordinator = service.maintenance_coordinator
    now = [coordinator.clock()]
    coordinator.clock = lambda: now[0]
    original = service.profile_gate.run_worker

    async def delayed(cut, callback, cancellation):
        now[0] += 21
        return await original(cut, callback, cancellation)

    def forbidden(*_args, **_kwargs):
        pytest.fail("expired doctor request invoked WAL capture")

    monkeypatch.setattr(service.profile_gate, "run_worker", delayed)
    monkeypatch.setattr(cold_wal, "read_wal_image", forbidden)
    before = oracle(service.directory)
    result = await finish(coordinator, await coordinator.doctor({}))
    assert result["phase"] in {"failed", "cancelled"}, result
    assert result["error_code"] == "doctor_deadline"
    assert result["receipt"] is None and not result["receipt_durable"]
    assert not result["cleanup_owner_pending"]
    assert service.profile_gate._cut is None
    assert oracle(service.directory) == before and provider.calls == 0
