"""Cold source fixtures are closed before the host acquires their profile.

No live source is opened with SQLite by the exporter; all validation is RAM-only.
No account, provider credentials, owner profile or UI participates.
"""

import asyncio
import hashlib
import os
import sqlite3
import time
from pathlib import Path
from uuid import uuid4

import pytest

from jarvis_core.cold_sqlite import ColdSqliteError
from jarvis_core.doctor import DoctorContext
from jarvis_core.host_profile import HostProfileError, freeze_sources
from jarvis_core.memory import MemoryScope, MemoryStore
from jarvis_core.recovery import (
    NewTarget,
    RecoveryContext,
    RecoveryLimits,
    SqliteImageSource,
    create_backup,
    verify_backup,
)
from jarvis_core.recovery_fs import FileIdentity
from jarvis_core.scheduler import ScheduleSpec, ScheduleStore
from jarvis_core.service import JarvisService
from jarvis_core.state import StateStore
from jarvis_core.tools import HostCancellation

NAMES = {"state": "jarvis.sqlite3", "memory": "memory.sqlite3", "schedules": "schedules.sqlite3"}
SUFFIXES = ("", "-wal", "-shm", "-journal")


class NoProvider:
    async def aclose(self):
        pass


def make_cold(root, roles=("state", "memory", "schedules")):
    root.mkdir(mode=0o700)
    if "state" in roles:
        with StateStore(root / NAMES["state"]) as store:
            store.create_session("COLD STATE MARKER")
    if "memory" in roles:
        store = MemoryStore(root / NAMES["memory"])
        try:
            store.upsert(MemoryScope("jarvis", "f" * 64), "fixture", "COLD MEMORY MARKER",
                         source_uri="jarvis://synthetic-cold")
        finally:
            store.close()
    if "schedules" in roles:
        store = ScheduleStore(root / NAMES["schedules"])
        try:
            store.create("COLD SCHEDULE MARKER", "synthetic prompt", "jarvis", "f" * 64,
                         ScheduleSpec("once", at=4_000_000_000), enabled=False)
        finally:
            store.close()


def oracle(root):
    result = {}
    for name in NAMES.values():
        for suffix in SUFFIXES:
            path = root / (name + suffix)
            if path.exists():
                info = path.stat()
                result[path.name] = (info.st_ino, info.st_mode, info.st_nlink, info.st_size,
                                     info.st_mtime_ns, info.st_ctime_ns,
                                     hashlib.sha256(path.read_bytes()).hexdigest())
    return result


def context(cut, frozen, cancel=None, *, deadline=None):
    cancel = cancel or HostCancellation()
    return RecoveryContext(uuid4().hex, cut.scope.profile_id, cut.scope.generation,
                           lambda *_: frozen.assert_current(cut), cancel,
                           deadline if deadline is not None else time.monotonic() + 20)


async def bind(service, cut, frozen, *, limits=None, cancel=None, ctx=None):
    cancel = cancel or HostCancellation()
    ctx = ctx or context(cut, frozen, cancel)
    outcome = await service.profile_gate.run_worker(
        cut, lambda: frozen.bind(cut, context=ctx, limits=limits), cancel,
    )
    if outcome.error is not None:
        raise outcome.error
    return outcome.value


@pytest.mark.parametrize("role", NAMES)
async def test_cold_role_is_captured_without_initializing_own_store(tmp_path, monkeypatch, role):
    root = tmp_path / "profile"
    make_cold(root, (role,))
    service = JarvisService(root, NoProvider())
    before = oracle(root)
    connect = sqlite3.connect
    opened = []

    def ram_only(path, *args, **kwargs):
        assert path == ":memory:"
        opened.append(path)
        return connect(path, *args, **kwargs)

    def no_open(*_args, **_kwargs):
        pytest.fail("store initializer used by cold exporter")

    monkeypatch.setattr(sqlite3, "connect", ram_only)
    monkeypatch.setattr(StateStore, "_open_disk", no_open)
    monkeypatch.setattr(MemoryStore, "_open_disk", no_open)
    monkeypatch.setattr(ScheduleStore, "_open", no_open)
    try:
        with service.profile_gate.exclusive() as cut:
            frozen = freeze_sources(service, cut)
            assert next(row for row in frozen.metadata if row["role"] == role)[
                "disposition"
            ] == "present"
            assert not opened  # Review only inspected metadata and <=100 bytes.
            bundle = await bind(service, cut, frozen)
            source, = bundle.sqlite
            assert isinstance(source, SqliteImageSource) and source.role == role
            assert source.sha256 == hashlib.sha256(source.data).hexdigest()
            assert source.data == (root / NAMES[role]).read_bytes()
            assert service.store._disk is None and service.memory._disk is None
            assert service.schedule_store._connection is None
        assert opened and oracle(root) == before
    finally:
        await service.aclose()


async def test_three_cold_roles_backup_and_doctor_preserve_source_family(tmp_path, monkeypatch):
    root = tmp_path / "profile"
    make_cold(root)
    service = JarvisService(root, NoProvider())
    before = oracle(root)
    connection = sqlite3.connect

    def ram_only(path, *args, **kwargs):
        assert path == ":memory:"
        return connection(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", ram_only)
    try:
        with service.profile_gate.exclusive() as cut:
            frozen = freeze_sources(service, cut)
            target = NewTarget(tmp_path, FileIdentity.of(tmp_path.stat()), "backup", (root,))
            ctx = context(cut, frozen)

            def backup():
                bundle = frozen.bind(cut, context=ctx)
                return create_backup(bundle, target, ctx)

            outcome = await service.profile_gate.run_worker(cut, backup, HostCancellation())
            assert outcome.error is None and outcome.value.status == "completed"
            assert verify_backup(tmp_path / "backup", context(cut, frozen)).artifact_id
            doc_context = DoctorContext(lambda *_args, **_kw: frozen.assert_current(cut),
                                        HostCancellation())
            outcome = await service.profile_gate.run_worker(
                cut, lambda: frozen.doctor_images(cut, doc_context, RecoveryLimits()),
                HostCancellation(),
            )
            assert outcome.error is None
            assert {row.role for row in outcome.value.images} == set(NAMES)
            assert all(row.disposition == "image" for row in outcome.value.images)
        assert oracle(root) == before
        assert service.store._disk is None and service.memory._disk is None
        assert service.schedule_store._connection is None
    finally:
        await service.aclose()


@pytest.mark.parametrize("suffix", SUFFIXES[1:])
@pytest.mark.parametrize("has_main", [True, False])
async def test_new_sidecar_invalidates_fast_guard_and_review_cas(tmp_path, suffix, has_main):
    root = tmp_path / "profile"
    make_cold(root, ("state",) if has_main else ())
    service = JarvisService(root, NoProvider())
    try:
        with service.profile_gate.exclusive() as cut:
            frozen = freeze_sources(service, cut)
            before_main = oracle(root)
            (root / (NAMES["state"] + suffix)).touch(mode=0o600)
            with pytest.raises(ColdSqliteError, match="profile_sqlite_family_changed"):
                frozen.assert_current(cut)
            if has_main:
                assert oracle(root)[NAMES["state"]] == before_main[NAMES["state"]]
        with pytest.raises((ColdSqliteError, HostProfileError)):
            with service.profile_gate.exclusive(expected_scope=frozen.scope,
                                                expected_fingerprint=frozen.fingerprint):
                pytest.fail("changed family accepted")
    finally:
        await service.aclose()


@pytest.mark.parametrize("suffix", SUFFIXES[1:])
@pytest.mark.parametrize("action", ["replace", "delete"])
async def test_existing_sidecar_identity_and_absence_are_part_of_review(tmp_path, suffix, action):
    root = tmp_path / "profile"
    make_cold(root, ("state",))
    path = root / (NAMES["state"] + suffix)
    path.touch(mode=0o600)
    service = JarvisService(root, NoProvider())
    try:
        with service.profile_gate.exclusive() as cut:
            frozen = freeze_sources(service, cut)
            path.unlink()
            if action == "replace":
                path.touch(mode=0o600)
            with pytest.raises(ColdSqliteError, match="profile_sqlite_family_changed"):
                frozen.assert_current(cut)
    finally:
        await service.aclose()


@pytest.mark.parametrize("suffix", SUFFIXES[1:])
async def test_even_empty_cold_sidecars_are_unavailable_not_ignored(tmp_path, suffix):
    root = tmp_path / "profile"
    make_cold(root, ("state",))
    (root / (NAMES["state"] + suffix)).touch(mode=0o600)
    service = JarvisService(root, NoProvider())
    before = oracle(root)
    try:
        with service.profile_gate.exclusive() as cut:
            frozen = freeze_sources(service, cut)
            if suffix == "-journal":
                assert frozen.metadata[0]["disposition"] == "present"
                bundle = await bind(service, cut, frozen)
                assert bundle.sqlite[0].data[18:20] == b"\1\1"
                assert frozen.sqlite_recovery[0]["mode"] == "cold_journal_inactive"
            else:
                assert frozen.metadata[0]["disposition"] == "unavailable"
                with pytest.raises(HostProfileError, match="profile_lazy_source_unavailable"):
                    await bind(service, cut, frozen)
        assert oracle(root) == before
    finally:
        await service.aclose()


async def test_wal_header_without_sidecar_is_never_normalized_to_delete(tmp_path):
    root = tmp_path / "profile"
    make_cold(root, ("state",))
    path = root / NAMES["state"]
    raw = path.read_bytes()
    path.write_bytes(raw[:18] + b"\x02\x02" + raw[20:])
    service = JarvisService(root, NoProvider())
    before = oracle(root)
    try:
        with service.profile_gate.exclusive() as cut:
            frozen = freeze_sources(service, cut)
            assert frozen.metadata[0]["disposition"] == "present"
            bundle = await bind(service, cut, frozen)
            assert bundle.sqlite[0].data[18:20] == b"\1\1"
            assert frozen.sqlite_recovery[0]["mode"] == "cold_wal_main_only"
            assert path.read_bytes()[18:20] == b"\2\2"
        assert oracle(root) == before
    finally:
        await service.aclose()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "mode"])
async def test_unsafe_main_is_rejected_before_any_image_read(tmp_path, kind):
    root = tmp_path / "profile"
    make_cold(root, ("state",))
    path = root / NAMES["state"]
    if kind == "mode":
        path.chmod(0o644)
    else:
        target = tmp_path / "synthetic-target"
        path.rename(target)
        if kind == "symlink":
            path.symlink_to(target)
        elif kind == "hardlink":
            os.link(target, path)
        else:
            os.mkfifo(path, 0o600)
    service = JarvisService(root, NoProvider())
    try:
        with service.profile_gate.exclusive() as cut:
            with pytest.raises(OSError):
                freeze_sources(service, cut)
    finally:
        await service.aclose()


@pytest.mark.parametrize("stop", ["cancel", "deadline", "size", "total"])
async def test_cancel_deadline_and_limits_stop_before_full_buffer(tmp_path, monkeypatch, stop):
    root = tmp_path / "profile"
    make_cold(root)
    service = JarvisService(root, NoProvider())
    full_reads = []
    pread = os.pread

    def watch(fd, count, offset):
        if count > 100:
            full_reads.append(count)
        return pread(fd, count, offset)

    monkeypatch.setattr(os, "pread", watch)
    try:
        with service.profile_gate.exclusive() as cut:
            frozen = freeze_sources(service, cut)
            cancellation = HostCancellation()
            if stop == "cancel":
                cancellation.set()
            ctx = context(cut, frozen, cancellation,
                          deadline=time.monotonic() - 1 if stop == "deadline" else None)
            limits = RecoveryLimits(max_sqlite_bytes=1) if stop == "size" else (
                RecoveryLimits(max_total_bytes=1) if stop == "total" else RecoveryLimits()
            )
            with pytest.raises((OSError, HostProfileError)):
                await bind(service, cut, frozen, limits=limits, cancel=cancellation, ctx=ctx)
        assert full_reads == []
    finally:
        await service.aclose()


async def test_journal_appearing_during_chunk_read_aborts_before_destination(tmp_path, monkeypatch):
    root = tmp_path / "profile"
    make_cold(root, ("memory",))
    service = JarvisService(root, NoProvider())
    original = (root / NAMES["memory"]).read_bytes()
    pread = os.pread
    inserted = []

    def race(fd, count, offset):
        result = pread(fd, count, offset)
        if count > 100 and not inserted:
            inserted.append(True)
            (root / (NAMES["memory"] + "-journal")).touch(mode=0o600)
        return result

    monkeypatch.setattr(os, "pread", race)
    try:
        with service.profile_gate.exclusive() as cut:
            frozen = freeze_sources(service, cut)
            target = NewTarget(tmp_path, FileIdentity.of(tmp_path.stat()), "forbidden", (root,))
            ctx = context(cut, frozen)

            def work():
                bundle = frozen.bind(cut, context=ctx)
                return create_backup(bundle, target, ctx)

            outcome = await service.profile_gate.run_worker(cut, work, HostCancellation())
            assert isinstance(outcome.error, ColdSqliteError)
            assert outcome.error.code == "profile_sqlite_family_changed"
        assert inserted and not (tmp_path / "forbidden").exists()
        assert (root / NAMES["memory"]).read_bytes() == original
    finally:
        await service.aclose()


@pytest.mark.parametrize("stop", ["cancel", "deadline"])
async def test_coordinator_capture_interruption_has_durable_cancelled_no_output(
    tmp_path, monkeypatch, stop,
):
    from jarvis_core import maintenance

    root = tmp_path / "profile"
    make_cold(root, ("state",))
    service = JarvisService(root, NoProvider())
    coordinator = service.maintenance_coordinator
    original_context = maintenance.RecoveryContext
    body_reads = []
    operation = None
    original_pread = os.pread

    class ExpiringContext(original_context):
        def checkpoint(self, stage, write=False):
            if body_reads and stop == "deadline":
                self.deadline = self.clock() - 1
            super().checkpoint(stage, write)

    monkeypatch.setattr(maintenance, "RecoveryContext", ExpiringContext)
    try:
        review = await coordinator.review_backup(
            {"destination_parent": str(tmp_path), "name": "backup"},
        )
        accepted = await coordinator.backup(
            {"review_id": review["review_id"], "review_digest": review["review_digest"]},
        )
        operation = coordinator._operations[accepted["operation_id"]]
        inode = (root / NAMES["state"]).stat().st_ino

        def interrupt(fd, count, offset):
            data = original_pread(fd, count, offset)
            if count > 100 and os.fstat(fd).st_ino == inode:
                body_reads.append(count)
                if stop == "cancel":
                    operation.cancellation.set()
            return data

        monkeypatch.setattr(os, "pread", interrupt)
        async with asyncio.timeout(3):
            while not (pending := (await coordinator.pending({}))["pending"]):
                await asyncio.sleep(0)
        ticket = pending[0]
        await coordinator.decide({
            "approval_id": ticket["approval_id"], "digest": ticket["digest"], "allow": True,
            "profile_id": ticket["scope"]["profile_id"],
            "generation": ticket["scope"]["generation"],
        })
        await asyncio.wait_for(asyncio.shield(operation.task), 3)
        result = await coordinator.status({"operation_id": operation.operation_id})
        assert body_reads and result["phase"] == "cancelled"
        assert result["receipt"]["effect_status"] == "no_published_effect"
        assert result["receipt"]["cleanup_pending"] is False
        assert result["receipt_durable"] and not result["worker_pending"]
        assert result["cancellation_requested"] == (stop == "cancel")
        assert service.store._disk is None and not (tmp_path / "backup").exists()
        assert not list(tmp_path.glob(".jarvis-stage-*"))
    finally:
        if operation is not None:
            operation.cancellation.set()
        await service.aclose()


async def test_cold_to_owned_invalidates_before_raw_fd_open(tmp_path, monkeypatch):
    root = tmp_path / "profile"
    make_cold(root, ("state",))
    service = JarvisService(root, NoProvider())
    try:
        with service.profile_gate.exclusive() as cut:
            frozen = freeze_sources(service, cut)
        await service.handlers()["sessions.list"]({})
        assert service.store._disk is not None
        original_open = os.open

        def reject_raw_db(path, *args, **kwargs):
            assert Path(path).name != NAMES["state"]
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(os, "open", reject_raw_db)
        with service.profile_gate.exclusive() as cut:
            with pytest.raises(HostProfileError, match="profile_generation_stale"):
                frozen.bind(cut, context=context(cut, frozen))
    finally:
        await service.aclose()
