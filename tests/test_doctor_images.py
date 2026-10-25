"""Actual WAL/FD fixtures; no account, live application or owner profiles."""

import hashlib
import json
import os
import sqlite3
import threading
from dataclasses import replace

import pytest
from test_doctor import (
    context,
    fingerprint,
    inventory,
    session,
    sqlite_file,
    write_json,
)

from jarvis_core import state
from jarvis_core.doctor import (
    DoctorError,
    DoctorLimits,
    DoctorSqliteImage,
    DoctorSqliteImages,
    inspect_report,
)


@pytest.fixture
def root(tmp_path):
    path = tmp_path.resolve() / "profile"
    path.mkdir(mode=0o700)
    return path


def selected(root, *, roles=("state", "agents"), **kwargs):
    return replace(
        inventory(root, roles=roles),
        coherence="coherent_snapshot",
        cut_id="synthetic_cut",
        **kwargs,
    )


def image(root, role="state", *, data=None):
    path = (
        root
        / {
            "state": "jarvis.sqlite3",
            "memory": "memory.sqlite3",
            "schedules": "schedules.sqlite3",
        }[role]
    )
    info = path.stat()
    data = path.read_bytes() if data is None else data
    return DoctorSqliteImage(
        role,
        "image",
        (info.st_dev, info.st_ino),
        data,
        hashlib.sha256(data).hexdigest(),
    )


def bundle(inv, *images, **kwargs):
    return replace(
        DoctorSqliteImages(
            inv.profile_id,
            inv.generation,
            inv.profile_identity,
            inv.cut_id,
            tuple(images),
        ),
        **kwargs,
    )


def test_active_wal_uses_host_ram_snapshot_without_source_or_sidecar_reads(
    root, monkeypatch
):
    path = root / "jarvis.sqlite3"
    source = sqlite3.connect(path)
    try:
        source.execute("PRAGMA journal_mode=WAL")
        source.executescript(state._SCHEMA)
        source.execute(f"PRAGMA application_id={state._APPLICATION_ID}")
        source.execute(f"PRAGMA user_version={state._SCHEMA_VERSION}")
        session(source)
        source.commit()
        for file in (
            path,
            path.with_name(path.name + "-wal"),
            path.with_name(path.name + "-shm"),
        ):
            file.chmod(0o600)
        destination = sqlite3.connect(":memory:")
        try:
            source.backup(destination)
            data = bytearray(destination.serialize())
        finally:
            destination.close()
        # This is the host's own standalone RAM result, not a source header
        # modification by doctor. Production capture belongs to recovery core.
        data[18:20] = b"\x01\x01"
        payload = image(root, data=bytes(data))
        write_json(root)
        inv = selected(root)
        before = fingerprint(root)
        source_id = payload.source_identity
        real_read, real_stat, real_connect = os.read, os.stat, sqlite3.connect

        def read(descriptor, count):
            info = os.fstat(descriptor)
            assert (info.st_dev, info.st_ino) != source_id
            return real_read(descriptor, count)

        def stat(path, *args, **kwargs):
            assert not str(path).endswith(("-wal", "-shm", "-journal"))
            return real_stat(path, *args, **kwargs)

        def connect(path, *args, **kwargs):
            assert path == ":memory:"
            return real_connect(path, *args, **kwargs)

        monkeypatch.setattr(os, "read", read)
        monkeypatch.setattr(os, "stat", stat)
        monkeypatch.setattr(sqlite3, "connect", connect)
        report = inspect_report(inv, context(), sqlite_images=bundle(inv, payload))
        assert report.status == "ok"
        check = report.checks[0]
        assert check.integrity == "ok" and dict(check.counts)["sessions"] == 1
        assert check.inspection_source == "host_ram_snapshot"
        assert check.snapshot_sha256 == payload.sha256
        assert check.snapshot_byte_length == len(payload.data)
        assert "PRIVATE" not in json.dumps(report.to_dict())
        monkeypatch.setattr(os, "stat", real_stat)
        assert fingerprint(root) == before
    finally:
        source.close()


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"profile_id": "other_profile"}, "doctor_snapshot_scope_mismatch"),
        ({"generation": 8}, "doctor_snapshot_scope_mismatch"),
        ({"profile_identity": (1, 2)}, "doctor_snapshot_scope_mismatch"),
        ({"cut_id": "former_cut"}, "doctor_snapshot_scope_mismatch"),
        ({"images": ()}, "doctor_snapshot_roles_mismatch"),
    ],
)
def test_scope_generation_cut_and_missing_roles_reject_before_any_body_read(
    root, monkeypatch, changes, code
):
    sqlite_file(root)
    inv = selected(root, roles=("state",))
    supplied = bundle(inv, image(root), **changes)

    def forbidden(*args, **kwargs):
        pytest.fail("fallback source body read")

    monkeypatch.setattr(os, "read", forbidden)
    ctx = context()
    report = inspect_report(inv, ctx, sqlite_images=supplied)
    assert report.status == "blocked" and code in report.blockers
    assert ctx.bytes_read == 0


def test_extra_role_is_rejected_not_silently_ignored(root):
    sqlite_file(root)
    sqlite_file(root, "memory")
    inv = selected(root, roles=("state",))
    report = inspect_report(
        inv, context(), sqlite_images=bundle(inv, image(root), image(root, "memory"))
    )
    assert (
        report.status == "blocked"
        and "doctor_snapshot_roles_mismatch" in report.blockers
    )


@pytest.mark.parametrize(
    "coherence,cut",
    [
        ("unverified", "synthetic_cut"),
        ("offline_quiescent", "synthetic_cut"),
        ("coherent_snapshot", None),
    ],
)
def test_images_are_not_implicit_permission_to_bypass_coherence(root, coherence, cut):
    sqlite_file(root)
    inv = selected(root, roles=("state",))
    supplied = bundle(inv, image(root))
    report = inspect_report(
        replace(inv, coherence=coherence, cut_id=cut), context(), sqlite_images=supplied
    )
    assert (
        report.status == "blocked" and "doctor_snapshot_mode_invalid" in report.blockers
    )


@pytest.mark.parametrize(
    "kind,expected,code",
    [
        ("hash", "corrupt", "doctor_snapshot_hash_mismatch"),
        ("schema", "unsafe", "doctor_schema_invalid"),
        ("role", "unsafe", "doctor_sqlite_identity_invalid"),
        ("bytes", "corrupt", "doctor_sqlite_corrupt"),
        ("wal_header", "blocked", "doctor_needs_quiescent_snapshot"),
    ],
)
def test_image_bytes_hash_schema_and_role_validated_without_fallback(
    root, monkeypatch, kind, expected, code
):
    sqlite_file(root)
    data = (root / "jarvis.sqlite3").read_bytes()
    if kind == "schema":
        path = sqlite_file(
            root, change=lambda c: c.execute("CREATE TABLE unknown_object(x)")
        )
        data = path.read_bytes()
    elif kind == "role":
        data = sqlite_file(root, "memory").read_bytes()
    elif kind == "bytes":
        data = b"PRIVATE INVALID DATABASE"
    elif kind == "wal_header":
        raw = bytearray(data)
        raw[18:20] = b"\x02\x02"
        data = bytes(raw)
    payload = image(root, data=data)
    if kind == "hash":
        payload = replace(payload, sha256="0" * 64)
    inv = selected(root, roles=("state",))
    before = fingerprint(root)
    monkeypatch.setattr(os, "read", lambda *a, **k: pytest.fail("source fallback"))
    report = inspect_report(inv, context(), sqlite_images=bundle(inv, payload))
    assert report.status == expected and report.checks[0].code == code
    assert "PRIVATE" not in json.dumps(report.to_dict())
    assert fingerprint(root) == before


def test_absent_unavailable_and_missing_snapshot_are_not_conflated(root, monkeypatch):
    inv = selected(root, roles=("state",))
    absent = DoctorSqliteImage("state", "absent")
    report = inspect_report(inv, context(), sqlite_images=bundle(inv, absent))
    assert report.checks[0].status == "absent"
    sqlite_file(root)
    identity = image(root).source_identity
    inv = selected(root, roles=("state",))
    unavailable = DoctorSqliteImage("state", "unavailable", identity)
    monkeypatch.setattr(os, "read", lambda *a, **k: pytest.fail("lazy store body read"))
    report = inspect_report(inv, context(), sqlite_images=bundle(inv, unavailable))
    assert (
        report.status == "blocked"
        and report.checks[0].code == "doctor_snapshot_source_unavailable"
    )
    report = inspect_report(inv, context(), sqlite_images=bundle(inv, absent))
    assert (
        report.status == "unknown"
        and "doctor_snapshot_source_changed" in report.blockers
    )


def test_missing_or_replaced_physical_source_does_not_validate_unrelated_ram(root):
    sqlite_file(root)
    inv = selected(root, roles=("state",))
    payload = image(root)
    supplied = bundle(inv, payload)
    path = root / "jarvis.sqlite3"
    path.rename(root / "prior.sqlite3")
    sqlite_file(root)
    report = inspect_report(inv, context(), sqlite_images=supplied)
    assert (
        report.status == "unknown"
        and "doctor_snapshot_source_changed" in report.blockers
    )
    path.unlink()
    report = inspect_report(inv, context(), sqlite_images=supplied)
    assert (
        report.status == "unknown"
        and "doctor_snapshot_source_changed" in report.blockers
    )


def test_source_permission_and_hardlink_checks_still_apply_to_images(root):
    path = sqlite_file(root)
    inv = selected(root, roles=("state",))
    supplied = bundle(inv, image(root))
    path.chmod(0o644)
    report = inspect_report(inv, context(), sqlite_images=supplied)
    assert report.status == "unsafe" and report.checks[0].code == "doctor_file_unsafe"
    path.chmod(0o600)
    os.link(path, root / "extra-link")
    report = inspect_report(inv, context(), sqlite_images=supplied)
    assert report.status == "unsafe" and report.checks[0].code == "doctor_file_unsafe"


def test_snapshot_and_json_share_total_budget_once(root):
    path = sqlite_file(root)
    write_json(root)
    inv = selected(root)
    supplied = bundle(inv, image(root))
    size = path.stat().st_size
    ctx = context(limits=DoctorLimits(total_bytes=size))
    report = inspect_report(inv, ctx, sqlite_images=supplied)
    assert report.status == "blocked" and report.checks[1].code == "doctor_byte_budget"
    assert ctx.bytes_read == size
    ctx = context(limits=DoctorLimits(sqlite_bytes=size - 1))
    report = inspect_report(inv, ctx, sqlite_images=supplied)
    assert report.status == "blocked" and ctx.bytes_read < size


@pytest.mark.parametrize(
    "stage", ["image_hash", "image_hashed", "sqlite_validation", "resource_revalidated"]
)
def test_cancellation_during_host_image_validation_is_latched_and_no_mutation(
    root, stage
):
    sqlite_file(root)
    inv = selected(root, roles=("state",))
    supplied = bundle(inv, image(root))
    event = threading.Event()

    def guard(current, *, write):
        if current == stage:
            event.set()

    before = fingerprint(root)
    ctx = context(guard, event)
    report = inspect_report(inv, ctx, sqlite_images=supplied)
    assert report.status == "cancelled" and "doctor_cancelled" in report.blockers
    assert fingerprint(root) == before
    event.clear()
    report = inspect_report(inv, ctx, sqlite_images=supplied)
    assert report.status == "cancelled"


def test_guard_rejects_generation_changed_after_snapshot_was_selected(root):
    sqlite_file(root)
    inv = selected(root, roles=("state",))
    supplied = bundle(inv, image(root))

    def guard(stage, *, write):
        if stage == "image_hashed":
            raise RuntimeError("PRIVATE GENERATION DIAGNOSTIC")

    report = inspect_report(inv, context(guard), sqlite_images=supplied)
    assert report.status == "unknown" and "doctor_barrier_changed" in report.blockers
    assert all(not check.counts for check in report.checks)
    assert "PRIVATE" not in json.dumps(report.to_dict())


@pytest.mark.parametrize(
    "changes",
    [
        {"data": bytearray(b"mutable")},
        {"data": "/arbitrary/file"},
        {"role": "auth.json"},
        {"source_identity": (True, 1)},
        {"sha256": "PRIVATE RAW DIAGNOSTIC"},
        {"disposition": "absent", "data": b"body"},
    ],
)
def test_images_are_typed_ram_only_and_repr_does_not_expose_bytes(root, changes):
    sqlite_file(root)
    payload = image(root, data=b"PRIVATE BYTES CANARY")
    assert "PRIVATE" not in repr(payload)
    with pytest.raises(DoctorError):
        replace(payload, **changes)


def test_multiple_ram_images_share_aggregate_budget_before_next_hash(root):
    first = sqlite_file(root)
    second = sqlite_file(root, "memory")
    inv = selected(root, roles=("state", "memory"))
    supplied = bundle(inv, image(root), image(root, "memory"))
    size = first.stat().st_size + second.stat().st_size
    ctx = context(limits=DoctorLimits(total_bytes=size - 1))
    report = inspect_report(inv, ctx, sqlite_images=supplied)
    assert report.status == "blocked"
    assert report.checks[0].integrity == "ok"
    assert report.checks[1].code == "doctor_byte_budget"
    assert ctx.bytes_read == first.stat().st_size


def test_duplicate_image_roles_and_nonfinite_generation_are_rejected(root):
    sqlite_file(root)
    inv = selected(root)
    payload = image(root)
    with pytest.raises(DoctorError, match="doctor_images_invalid"):
        bundle(inv, payload, payload)
    with pytest.raises(DoctorError, match="doctor_images_invalid"):
        bundle(inv, payload, generation=float("nan"))
    with pytest.raises(DoctorError, match="doctor_images_invalid"):
        bundle(inv, payload, generation=True)


def test_source_changed_during_ram_validation_invalidates_cut(root):
    path = sqlite_file(root)
    inv = selected(root, roles=("state",))
    supplied = bundle(inv, image(root))

    def guard(stage, *, write):
        if stage == "image_hashed":
            path.write_bytes(b"changed synthetic fixture")

    report = inspect_report(inv, context(guard), sqlite_images=supplied)
    assert report.status == "unknown" and "doctor_resource_changed" in report.blockers
    assert all(not row.counts for row in report.checks)


def test_host_blocker_still_prevents_hashing_and_reading_images(root):
    sqlite_file(root)
    inv = selected(root, roles=("state",), blockers=("runs_active",))
    supplied = bundle(inv, image(root))
    ctx = context()
    report = inspect_report(inv, ctx, sqlite_images=supplied)
    assert report.status == "blocked" and ctx.bytes_read == 0
    assert "runs_active" in report.blockers
