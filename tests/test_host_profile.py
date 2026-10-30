"""Real profile leases, service handlers, SQLite and coordinated synthetic workers."""

import asyncio
import hashlib
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

import pytest
from test_service import ModelPeer

import jarvis_core.host_profile as module
from jarvis_core.host_profile import (
    HostProfileError,
    ProfileLease,
    ServiceProfileGate,
    freeze_sources,
)
from jarvis_core.protocol import RpcError
from jarvis_core.recovery import RecoveryContext, RecoveryError, RecoveryLimits, RecoveryReceipt
from jarvis_core.service import JarvisService
from jarvis_core.state import StateStore
from jarvis_core.tools import HostCancellation


@pytest.fixture
async def service(tmp_path):
    peer = ModelPeer()
    value = JarvisService(tmp_path / "profile", peer)
    try:
        yield value
    finally:
        peer.release.set()
        await value.aclose()


async def call(service, name, params=None):
    return await service.handlers()[name]({} if params is None else params)


async def wait_until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0)


def test_profile_lifetime_lock_excludes_another_owner_and_does_not_overwrite(tmp_path):
    root = tmp_path / "profile"
    first = ProfileLease.acquire(root)
    before = (root / "profile.lock").stat()
    try:
        with pytest.raises(HostProfileError, match=r"^profile_busy$"):
            ProfileLease.acquire(root)
        assert (root / "profile.lock").stat().st_ino == before.st_ino
        assert first.scope.generation == 0
    finally:
        first.close()
    next_owner = ProfileLease.acquire(root)
    try:
        assert next_owner.scope.profile_id != first.scope.profile_id
    finally:
        next_owner.close()


async def test_profile_lock_excludes_real_second_process(tmp_path):
    root = tmp_path / "profile"
    lease = ProfileLease.acquire(root)
    source = str(Path(module.__file__).resolve().parents[1])
    code = """
from pathlib import Path
import sys
from jarvis_core.host_profile import ProfileLease, HostProfileError
try:
    ProfileLease.acquire(Path(sys.argv[1]))
except HostProfileError as error:
    print(error.code)
    raise SystemExit(0)
raise SystemExit(9)
"""
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", code, str(root), env={"PYTHONPATH": source},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        output, error = await asyncio.wait_for(process.communicate(), 5)
        assert process.returncode == 0 and output == b"profile_busy\n" and not error
    finally:
        lease.close()


def test_lock_replacement_and_profile_rename_are_detected(tmp_path):
    root = tmp_path / "profile"
    lease = ProfileLease.acquire(root)
    saved = tmp_path / "saved-lock"
    try:
        (root / "profile.lock").rename(saved)
        (root / "profile.lock").touch(mode=0o600)
        with pytest.raises(HostProfileError, match=r"^profile_lock_changed$"):
            lease.assert_current()
        (root / "profile.lock").unlink()
        saved.rename(root / "profile.lock")
        renamed = tmp_path / "renamed"
        root.rename(renamed)
        try:
            with pytest.raises(HostProfileError, match=r"^profile_identity_changed$"):
                lease.assert_current()
        finally:
            renamed.rename(root)
    finally:
        lease.close()


async def test_all_registered_rpc_handlers_are_denied_before_validation_during_cut(service):
    gate = service.profile_gate
    controls = {
        "maintenance.status", "maintenance.pending", "maintenance.decide", "maintenance.cancel",
        "backup.review", "backup.create", "backup.verify", "restore.review", "restore.stage",
        "doctor.report", "profile.restore.review", "profile.restore.activate",
        "profile.restore.inspect", "maintenance.cleanup",
        "memory.restore.inventory", "memory.restore.review", "memory.restore.copy_scope",
        "memory.restore.receipt",
        "telegram.restore.review", "telegram.restore.reconcile", "telegram.restore.inspect",
        "workspace.backup.review", "workspace.backup.create",
        "workspace.restore.review", "workspace.restore.stage",
        "doctor.repair.review", "doctor.repair.apply",
        "maintenance.release.inspect", "maintenance.release.review", "maintenance.release.apply",
        "maintenance.release.rollback.review", "maintenance.release.rollback.apply",
        "maintenance.release.resolve_launch",
    }
    assert set(service.maintenance_coordinator.handlers()) == controls
    with gate.exclusive():
        for name, handler in service.handlers().items():
            if name in controls:
                continue
            with pytest.raises(RpcError, match=r"^profile_maintenance_busy$") as raised:
                await handler({})
            assert raised.value.code == -32037, name
        # Only coordinator metadata remains available over a real cut; it never
        # starts a provider or initializes a store to manufacture health.
        assert not (await service.handlers()["maintenance.status"]({}))["operations"]
        assert not (await service.handlers()["maintenance.pending"]({}))["pending"]
    assert not service.provider.calls
    assert not service.store.path.exists()


async def test_inflight_status_blocks_cut_without_cancelling_or_starting_jobs(service):
    entered, release = asyncio.Event(), asyncio.Event()

    async def status():
        entered.set()
        await release.wait()
        return {"connected": True, "mode": "chatgpt"}

    service.provider.account_status = status
    task = asyncio.create_task(call(service, "service.status"))
    try:
        await entered.wait()
        with pytest.raises(HostProfileError, match=r"^profile_not_quiescent$") as raised:
            with service.profile_gate.exclusive():
                pytest.fail("cut admitted an in-flight read/provider initialization")
        assert "profile_handlers_active" in raised.value.blockers
        assert not task.done() and not service.scheduler.running
    finally:
        release.set()
        await task
    with service.profile_gate.exclusive():
        pass


async def test_read_polling_advances_only_lazy_initialization_not_every_request(service):
    initial = service.profile_lease.scope
    await call(service, "sessions.list")
    initialized = service.profile_lease.scope
    assert initialized.generation == initial.generation + 1
    await call(service, "sessions.list")
    await call(service, "service.status")
    assert service.profile_lease.scope == initialized
    await call(service, "schedules.list")
    warmed = service.profile_lease.scope
    assert warmed.generation == initialized.generation + 1
    await call(service, "schedules.list")
    assert service.profile_lease.scope == warmed


async def test_review_binds_to_new_cut_after_unchanged_polls_but_not_old_callbacks(service):
    await call(service, "sessions.list")
    gate = service.profile_gate
    with gate.exclusive() as first:
        frozen = freeze_sources(service, first)
        old_bundle = frozen.bind(first)
    await call(service, "sessions.list")
    with gate.exclusive(expected_scope=frozen.scope,
                        expected_fingerprint=frozen.fingerprint) as second:
        bundle = frozen.bind(second)
        assert second.cut_id != first.cut_id
        assert bundle.json[0].data == old_bundle.json[0].data
        bundle.json[0].check_current()
        with pytest.raises(HostProfileError, match=r"^profile_cut_expired$"):
            old_bundle.json[0].check_current()


async def test_review_rejects_explicit_mutation_and_external_json_change(service):
    gate = service.profile_gate
    with gate.exclusive() as cut:
        frozen = freeze_sources(service, cut)
    await call(service, "agents.create", {"name": "Agente sintético"})
    with pytest.raises(HostProfileError, match=r"^profile_generation_stale$"):
        with gate.exclusive(expected_scope=frozen.scope):
            pytest.fail("old generation")
    with gate.exclusive() as cut:
        frozen = freeze_sources(service, cut)
    path = service.agent_profiles.document.path
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(HostProfileError, match=r"^profile_sources_changed$"):
        with gate.exclusive(expected_scope=frozen.scope,
                            expected_fingerprint=frozen.fingerprint):
            pytest.fail("changed JSON")


async def test_fast_guard_does_not_reread_json_and_detects_metadata_changes(service, monkeypatch):
    await call(service, "agents.create", {"name": "Agente sintético"})
    with service.profile_gate.exclusive() as cut:
        frozen = freeze_sources(service, cut)
        bundle = frozen.bind(cut)
        monkeypatch.setattr(module, "_json_state", lambda *_: pytest.fail("JSON reread"))
        for _ in range(20):
            frozen.assert_current(cut)
            for row in bundle.json:
                row.check_current()
        path = service.agent_profiles.document.path
        path.write_bytes(path.read_bytes() + b" ")
        with pytest.raises(HostProfileError, match=r"^profile_sources_changed$"):
            frozen.assert_current(cut)


async def test_absent_sources_and_exclusions_are_never_created_or_opened(service, monkeypatch):
    root = service.directory
    (root / "provider").mkdir(mode=0o700)
    secret = root / "provider" / "auth.json"
    secret.write_text("SYNTHETIC EXCLUDED CREDENTIAL")
    real_open = os.open

    def guarded_open(path, *args, **kwargs):
        assert os.fspath(path) not in {"provider", "auth.json", str(secret)}
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", guarded_open)
    with service.profile_gate.exclusive() as cut:
        frozen = freeze_sources(service, cut)
        bundle = frozen.bind(cut)
        expected = {"state", "memory", "schedules", "skills", "telegram", "product"}
        if getattr(service, "plugins", None) is not None:
            expected.add("plugins")
        if getattr(service, "automation_flows", None) is not None:
            expected.add("automations")
        assert set(bundle.absent) == expected
        assert bundle.json[0].role == "agents"
        assert not bundle.sqlite
        assert next(row for row in frozen.metadata if row["role"] == "agents")[
            "disposition"
        ] == "generated_builtin"
        assert "SYNTHETIC" not in repr(frozen.metadata)
    assert not service.store.path.exists()
    assert not service.memory.path.exists()
    assert not service.schedule_store.path.exists()


async def test_unknown_and_restore_resources_block_without_reading_them(service):
    root = service.directory
    unknown = root / "unsupported.bin"
    unknown.write_bytes(b"synthetic")
    with service.profile_gate.exclusive() as cut:
        with pytest.raises(HostProfileError, match=r"^profile_inventory_unclassified$"):
            freeze_sources(service, cut)
    unknown.unlink()
    (root / "restore_record.json").write_bytes(b"not parsed")
    with service.profile_gate.exclusive() as cut:
        with pytest.raises(HostProfileError, match=r"^profile_activation_required$"):
            freeze_sources(service, cut)


async def test_maintenance_journal_is_explicitly_excluded_from_source_fingerprint(service):
    with service.profile_gate.exclusive() as cut:
        frozen = freeze_sources(service, cut)
    path = service.directory / "maintenance.json"
    path.write_bytes(b"synthetic host journal, not source data")
    with service.profile_gate.exclusive(expected_scope=frozen.scope,
                                        expected_fingerprint=frozen.fingerprint) as cut:
        bundle = frozen.bind(cut)
        assert all(row.role != "maintenance" for row in bundle.json)


async def test_cold_existing_sqlite_is_captured_without_lazy_recovery(tmp_path):
    root = tmp_path / "cold"
    with StateStore(root / "jarvis.sqlite3") as store:
        store.create_session("Sintética")
    path = root / "jarvis.sqlite3"
    original = path.read_bytes(), path.stat().st_mtime_ns
    value = JarvisService(root, ModelPeer())
    try:
        assert value.store._disk is None
        with value.profile_gate.exclusive() as cut:
            frozen = freeze_sources(value, cut)
            metadata = next(row for row in frozen.metadata if row["role"] == "state")
            assert metadata["disposition"] == "present" and metadata["bytes"] > 0
            context = RecoveryContext(
                uuid4().hex, cut.scope.profile_id, cut.scope.generation,
                lambda *_: frozen.assert_current(cut), HostCancellation(), time.monotonic() + 20,
            )
            outcome = await value.profile_gate.run_worker(
                cut, lambda: frozen.bind(cut, context=context), HostCancellation(),
            )
            assert outcome.error is None and outcome.value.sqlite[0].data == original[0]
        assert value.store._disk is None
        assert (path.read_bytes(), path.stat().st_mtime_ns) == original
    finally:
        await value.aclose()


@pytest.mark.parametrize("worker_error", [False, True])
async def test_repeated_awaiter_cancel_retains_cut_and_worker_receipt(tmp_path, worker_error):
    lease = ProfileLease.acquire(tmp_path / "profile")
    gate = ServiceProfileGate(lease, blockers=lambda: ())
    cancellation = HostCancellation()
    entered, release = threading.Event(), threading.Event()
    receipt = RecoveryReceipt(
        uuid4().hex, "backup.create", "publication_unknown", "unknown", "copy",
    )
    error = RecoveryError("recovery_publication_unknown", receipt)

    def worker():
        entered.set()
        assert release.wait(3)
        if worker_error:
            raise error
        return receipt

    async def operation():
        with gate.exclusive() as cut:
            return await gate.run_worker(cut, worker, cancellation)

    task = asyncio.create_task(operation())
    try:
        await wait_until(entered.is_set)
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert cancellation.is_set()
            with pytest.raises(HostProfileError, match=r"^profile_maintenance_busy$"):
                with gate.admit("sessions.create", mutating=True):
                    pytest.fail("writer passed while worker alive")
        release.set()
        outcome = await asyncio.wait_for(task, 2)
        assert outcome.awaiter_cancelled
        assert outcome.error is error if worker_error else outcome.value is receipt
        if worker_error:
            assert outcome.error.receipt is receipt
        with gate.exclusive():
            pass
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        lease.close()


async def test_running_run_and_cleanup_block_until_durable_settlement(service):
    row = (await call(service, "sessions.create"))["session"]
    params = {"session_id": row["session_id"]}
    run = await call(service, "runs.start", {**params, "message": "espera"})
    await service.provider.entered.wait()
    with pytest.raises(HostProfileError, match=r"^profile_not_quiescent$"):
        with service.profile_gate.exclusive():
            pytest.fail("running turn")
    await call(service, "runs.cancel", {**params, "run_id": run["run_id"]})
    await service.provider.cleaning.wait()
    with pytest.raises(HostProfileError, match=r"^profile_not_quiescent$"):
        with service.profile_gate.exclusive():
            pytest.fail("cleanup not settled")
    assert service._run_writers
    service.provider.release.set()
    await wait_until(lambda: not service._handles and not service._observers)
    record = service.store.get_run(module_session(row), run["run_id"])
    assert record.receipt_json is not None and record.outcome == "unknown"
    with service.profile_gate.exclusive() as cut:
        frozen = freeze_sources(service, cut)
        frozen.bind(cut)


def module_session(row):
    from jarvis_core.state import SessionRef
    return SessionRef(row["session_id"])


async def test_scheduler_running_requires_explicit_stop_not_automatic_pause(service):
    from jarvis_core.client_identity import key_id
    from jarvis_core.protocol import authenticated_client

    snapshot = await call(service, "schedules.list")
    owner = key_id(os.environ["JARVIS_RPC_BOOTSTRAP_PUBLIC_KEY"])
    token = authenticated_client.set(owner)
    try:
        await call(service, "scheduler.start", {
            "configuration_hash": snapshot["configuration_hash"]})
    finally:
        authenticated_client.reset(token)
    owned = service.scheduler._lease
    with pytest.raises(HostProfileError, match=r"^profile_not_quiescent$") as raised:
        with service.profile_gate.exclusive():
            pytest.fail("active scheduler")
    assert "scheduler_active_or_cleanup" in raised.value.blockers
    assert service.scheduler.running and service.scheduler._lease == owned
    await call(service, "scheduler.stop")
    with service.profile_gate.exclusive():
        pass


async def test_live_images_use_all_three_owned_connections_and_do_not_change_sources(service):
    row = (await call(service, "sessions.create"))["session"]
    await call(service, "memory.list", {"session_id": row["session_id"]})
    await call(service, "schedules.list")
    paths = [service.store.path, service.memory.path, service.schedule_store.path]
    before = [(path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
              for path in paths]
    with service.profile_gate.exclusive() as cut:
        frozen = freeze_sources(service, cut)
        cancellation = HostCancellation()
        context = RecoveryContext(uuid4().hex, cut.scope.profile_id, cut.scope.generation,
                                  lambda *_: frozen.assert_current(cut), cancellation,
                                  time.monotonic() + 20)
        outcome = await service.profile_gate.run_worker(
            cut, lambda: frozen.doctor_images(cut, context, RecoveryLimits()), cancellation,
        )
        assert outcome.error is None
        assert {item.role for item in outcome.value.images} == {"state", "memory", "schedules"}
        assert all(item.disposition == "image" and item.data[18:20] == b"\x01\x01"
                   for item in outcome.value.images)
    assert [(path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in paths] == before


async def test_readonly_cas_detects_external_sqlite_commit_between_review_and_execute(service):
    await call(service, "sessions.list")
    with service.profile_gate.exclusive() as cut:
        frozen = freeze_sources(service, cut)
    source = sqlite3.connect(service.store.path)
    try:
        # Valid external commit without touching this connection's total_changes.
        source.execute("PRAGMA user_version=2")
        source.commit()
    finally:
        source.close()
    with pytest.raises(HostProfileError, match=r"^profile_sources_changed$"):
        with service.profile_gate.exclusive(expected_scope=frozen.scope,
                                            expected_fingerprint=frozen.fingerprint):
            pytest.fail("external data_version change")


async def test_captured_store_methods_and_late_callbacks_cannot_bypass_exclusive_cut(service):
    from jarvis_core.runtime import RunContext

    captured = service.store.create_session
    context = RunContext("absent_run", "absent_session", "synthetic", ())
    with service.profile_gate.exclusive():
        for invoke in (
            lambda: captured("Must not be created"),
            lambda: service.store.list_sessions(),
            lambda: service._event(context, "late", {}),
            lambda: service._bind_thread(context, "late_thread"),
            lambda: service._prepare_turn(context),
        ):
            with pytest.raises(HostProfileError, match=r"^profile_maintenance_busy$"):
                invoke()
        with pytest.raises(HostProfileError, match=r"^profile_maintenance_busy$"):
            await service._settle(None, None)
    assert not service.store.path.exists()


async def test_source_summary_is_detached_from_frozen_authority_metadata(service):
    with service.profile_gate.exclusive() as cut:
        frozen = freeze_sources(service, cut)
        summary = frozen.metadata
        summary[0]["role"] = "arbitrary"
        configuration = frozen.configuration_metadata
        configuration["mcp_server_count"] = 1000
        assert frozen.metadata[0]["role"] == "state"
        assert frozen.configuration_metadata["mcp_server_count"] == 0
        frozen.bind(cut)



async def test_captured_document_update_rechecks_after_await_and_root_retarget(service):
    root = service.directory
    document = service.agent_profiles.document
    update = document.update
    await asyncio.sleep(0)
    with service.profile_gate.exclusive():
        with pytest.raises(HostProfileError, match=r"^profile_maintenance_busy$"):
            update(lambda value: value)
    old = root.parent / "old_profile"
    root.rename(old)
    root.mkdir(mode=0o700)
    try:
        await asyncio.sleep(0)
        with pytest.raises(HostProfileError, match=r"^profile_identity_changed$"):
            update(lambda value: value)
        assert not (old / "profiles" / "agents.json").exists()
        assert not (root / "profiles").exists()
    finally:
        root.rmdir()
        old.rename(root)


def test_constructor_failure_releases_lease_before_another_explicit_start(tmp_path):
    root = tmp_path / "profile"
    (root / "profiles").mkdir(mode=0o700, parents=True)
    root.chmod(0o700)
    bad = root / "profiles" / "agents.json"
    bad.write_bytes(b"not valid json")
    bad.chmod(0o600)
    with pytest.raises(ValueError):
        JarvisService(root, ModelPeer())
    next_owner = ProfileLease.acquire(root)
    next_owner.close()
