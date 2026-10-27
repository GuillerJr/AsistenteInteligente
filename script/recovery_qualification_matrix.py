#!/usr/bin/env python3
"""Matriz finita propia para cualificar WAL/journals sin datos ni cuentas reales.

prepare crea perfiles nuevos exclusivamente bajo /private/tmp (o /tmp en Linux).
verify lee sólo un fixture marcado y su destino fijo; nunca abre SQLite original.
No inicia la app, no conecta proveedor y no toma decisiones reales por el dueño.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import signal
import sqlite3
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO / "tests"))

from journal_fixture import make_journal  # noqa: E402
from wal_fixture import NAMES, SUFFIXES, make_wal  # noqa: E402

from jarvis_core.recovery_fs import Directory  # noqa: E402

_TEMP = Path("/private/tmp" if sys.platform == "darwin" else "/tmp")
_FORMAT = "jarvis-recovery-qualification/1"
_ROLES = tuple(NAMES)
_TABLES = {"state": ("sessions", "runs", "messages", "events"),
           "memory": ("scopes", "documents", "chunks", "metadata"),
           "schedules": ("schedules", "occurrences")}
_MODES = ("wal_absent", "wal_empty", "wal_header", "journal_hot_delete",
          "journal_hot_persist", "journal_inactive_persist", "journal_inactive_truncate")
_TAILS = ("truncated", "checksum", "old_generation")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def cases():
    rows = [{"id": mode + "_all", "variants": {role: mode for role in _ROLES},
             "action": "backup.create", "repair_role": None, "tail": None}
            for mode in _MODES]
    rows += [{"id": "prefix_" + tail + "_" + role,
              "variants": {key: "wal_prefix_" + tail if key == role else "cold_delete"
                           for key in _ROLES},
              "action": "doctor.repair.apply", "repair_role": role, "tail": tail}
             for tail in _TAILS for role in _ROLES]
    return rows


def _metadata(info):
    return [info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode), info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def read_private(path, maximum=64 * 1024 * 1024):
    """Own files only, with no-follow descriptors and stable full metadata."""
    path = Path(path)
    with Directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent.fd)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                    or info.st_size > maximum):
                raise ValueError("fixture_private_file_invalid")
            data, offset = bytearray(), 0
            while offset < info.st_size:
                chunk = os.pread(fd, min(65536, info.st_size - offset), offset)
                if not chunk:
                    raise ValueError("fixture_file_changed")
                data.extend(chunk)
                offset += len(chunk)
            named = os.stat(path.name, dir_fd=parent.fd, follow_symlinks=False)
            if _metadata(info) != _metadata(os.fstat(fd)) or _metadata(named) != _metadata(info):
                raise ValueError("fixture_file_changed")
            parent.check()
            return bytes(data), _metadata(info)
        finally:
            os.close(fd)


def source_oracle(profile):
    result = {}
    for name in NAMES.values():
        for suffix in SUFFIXES:
            path = profile / (name + suffix)
            try:
                data, metadata = read_private(path)
            except FileNotFoundError:
                result[path.name] = None
            else:
                result[path.name] = {"metadata": metadata, "sha256": digest(data)}
    return result


def _projection(connection, role):
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute("PRAGMA query_only=ON")
    if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise ValueError("fixture_projection_integrity_failed")
    tables = {}
    for table in _TABLES[role]:
        rows = connection.execute("SELECT * FROM " + table).fetchall()
        tables[table] = sorted([list(row) for row in rows], key=canonical)
    if role == "memory" and connection.execute(
        "SELECT 1 FROM sqlite_schema WHERE name='chunk_fts'"
    ).fetchone():
        tables["chunk_fts"] = sorted([list(row) for row in connection.execute(
            "SELECT rowid,text FROM chunk_fts")], key=canonical)
    return {"application_id": connection.execute("PRAGMA application_id").fetchone()[0],
            "user_version": connection.execute("PRAGMA user_version").fetchone()[0],
            "tables": tables}


def private_reference(profile, trims=None):
    """Independent native SQLite oracle; only copies are ever SQLite paths.

    SHM is not copied. For a known truncated commit fixture, a complete valid WAL
    is trimmed ONLY in this private oracle at its preceding commit boundary.
    """
    before, result = source_oracle(profile), {}
    with tempfile.TemporaryDirectory(prefix=".jarvis-oracle-", dir=profile.parent) as scratch:
        stage = Path(scratch)
        for role, name in NAMES.items():
            for suffix in ("", "-wal", "-journal"):
                if before[name + suffix] is None:
                    continue
                raw, _ = read_private(profile / (name + suffix))
                if suffix == "-wal" and role in (trims or {}):
                    raw = raw[:trims[role]]
                destination = stage / (name + suffix)
                with destination.open("xb") as stream:
                    stream.write(raw)
                destination.chmod(0o600)
            connection = sqlite3.connect((stage / name).as_uri() + "?mode=rw", uri=True)
            try:
                result[role] = _projection(connection, role)
            finally:
                connection.close()
    if source_oracle(profile) != before:
        raise ValueError("fixture_reference_changed_source")
    return result


def _checkpoint(profile, role, *, delete=False):
    # Fixture construction, before any host lease or GUI. No personal database.
    db = sqlite3.connect(profile / NAMES[role], isolation_level=None)
    try:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if delete:
            assert db.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
    finally:
        db.close()
    for suffix in ("-wal", "-shm"):
        (profile / (NAMES[role] + suffix)).unlink(missing_ok=True)


def _prefix(profile, role, tail):
    path = profile / (NAMES[role] + "-wal")
    raw, _ = read_private(path)
    stride = 24 + int.from_bytes(raw[8:12], "big")
    commits = [offset + stride for offset in range(32, len(raw), stride)
               if int.from_bytes(raw[offset + 4:offset + 8], "big")]
    assert len(commits) == 3
    if tail == "truncated":
        retained, damaged = commits[-2], raw[:-1]
    else:
        retained = commits[-1]
        frame = bytearray(raw[-stride:])
        frame[16 if tail == "checksum" else 8] ^= 1
        damaged = raw + frame
    expected = private_reference(profile, {role: retained})
    path.write_bytes(damaged)
    return expected, {"retained_bytes": retained, "discarded_bytes": len(damaged) - retained,
                      "discarded_frame_slots": (len(damaged) - retained + stride - 1) // stride,
                      "failure": {"truncated": "partial_frame", "checksum": "checksum",
                                  "old_generation": "page_or_generation"}[tail],
                      "lost_committed_transactions": "unknown", "data_loss_possible": True}


def prepare_case(parent, specification):
    parent = Path(parent)
    known = {row["id"]: row for row in cases()}
    if (parent.parent != _TEMP or not parent.name.startswith(("jarvis-", "jr-"))
            or known.get(specification.get("id")) != specification):
        raise ValueError("fixture_known_temporary_case_required")
    with Directory(parent):
        pass
    case_root = parent / specification["id"]
    case_root.mkdir(mode=0o700)
    profile = case_root / "profile"
    mode = next(iter(specification["variants"].values()))
    if specification["repair_role"] is not None:
        make_wal(profile)
        for role in _ROLES:
            if role != specification["repair_role"]:
                _checkpoint(profile, role, delete=True)
        expected, loss = _prefix(profile, specification["repair_role"], specification["tail"])
    elif mode.startswith("journal"):
        make_journal(profile, mode=mode.rsplit("_", 1)[-1].upper(),
                     hot="inactive" not in mode, duplicates=64)
        expected, loss = private_reference(profile), None
    else:
        make_wal(profile)
        headers = {role: read_private(profile / (name + "-wal"))[0][:32]
                   for role, name in NAMES.items()}
        for role in _ROLES:
            _checkpoint(profile, role)
            if mode != "wal_absent":
                path = profile / (NAMES[role] + "-wal")
                path.write_bytes(headers[role] if mode == "wal_header" else b"")
                path.chmod(0o600)
        expected, loss = private_reference(profile), None
    value = {"format": _FORMAT, "case": specification, "profile": str(profile),
             "profile_identity": _metadata(profile.stat())[:2],
             "synthetic_data": True, "owner_decisions_simulated": False,
             "source_members": source_oracle(profile), "expected": expected,
             "expected_projection_sha256": {role: digest(canonical(image))
                                             for role, image in expected.items()},
             "expected_loss": loss,
             "targets": {"backup": str(case_root / "respaldo"),
                         "repaired": str(case_root / "reparado"),
                         "restored": str(case_root / "restaurado")}}
    manifest = case_root / "fixture.json"
    manifest.write_bytes(canonical(value))
    manifest.chmod(0o600)
    return {"case_id": specification["id"], "fixture": str(manifest),
            "profile": str(profile), "sha256": digest(manifest.read_bytes()),
            "action": specification["action"], "repair_role": specification["repair_role"],
            "expected_loss": loss}


def prepare(root, selected=None):
    root = Path(root)
    if root.parent != _TEMP or not root.name.startswith(("jarvis-", "jr-")) or root.exists():
        raise ValueError("fixture_new_private_temp_root_required")
    ids = selected or [row["id"] for row in cases()]
    matrix = {row["id"]: row for row in cases()}
    if len(ids) != len(set(ids)) or set(ids) - matrix.keys():
        raise ValueError("fixture_case_invalid")
    if any(len(str(root / name / "profile/service.sock").encode()) >= 104 for name in ids):
        raise ValueError("fixture_socket_path_too_long")
    root.mkdir(mode=0o700)
    records = [prepare_case(root, matrix[identity]) for identity in ids]
    value = {"format": _FORMAT, "case_count": len(records), "cases": records,
             "qualification_boundary": "Synthetic preparation; no GUI or owner decision",
             "native_gui_plan": [
                 "Abrir el perfil con --recovery-only y cuenta desconectada; no enumerar chats.",
                 "Modos: Mantenimiento → Diagnosticar y revisar copia; descartar sin publicar.",
                 "Prefijos: revisar wal_valid_prefix del rol indicado y comprobar pérdida.",
                 "El dueño aprueba un caso representativo; registrar estado y verificar destino.",
                 "Cerrar antes de cambiar de perfil; conservar restaurados sin activar.",
             ]}
    path = root / "matrix.json"
    path.write_bytes(canonical(value))
    path.chmod(0o600)
    return value


def verify(fixture, artifact=None, status=None):
    fixture = Path(fixture)
    case_root = fixture.parent
    if (fixture.name != "fixture.json" or case_root.parent.parent != _TEMP
            or not case_root.parent.name.startswith(("jarvis-", "jr-"))
            or case_root.name not in {row["id"] for row in cases()}):
        raise ValueError("fixture_known_temporary_case_required")
    raw, _ = read_private(fixture, 8 * 1024 * 1024)
    value = json.loads(raw)
    if value.get("format") != _FORMAT or value.get("synthetic_data") is not True:
        raise ValueError("fixture_marker_invalid")
    profile = Path(value["profile"])
    if (profile != case_root / "profile"
            or _metadata(profile.stat())[:2] != value["profile_identity"]):
        raise ValueError("fixture_profile_identity_changed")
    if source_oracle(profile) != value["source_members"]:
        raise ValueError("fixture_original_changed")
    result = {"case_id": value["case"]["id"], "source_unchanged": True,
              "fixture_sha256": digest(raw), "owner_decision_verified_by_this_script": False}
    if artifact is None:
        return result
    artifact = Path(artifact)
    if str(artifact) not in value["targets"].values():
        raise ValueError("fixture_target_not_reviewed")
    receipt = None
    if status is not None:
        status = Path(status)
        if (status.parent not in {case_root, case_root.parent}
                or not status.name.endswith("status.json")):
            raise ValueError("fixture_known_status_required")
        encoded, _ = read_private(status, 2 * 1024 * 1024)
        row = json.loads(encoded)
        row = row.get("operation", row)
        receipt = row.get("receipt")
        if (row.get("phase") not in {"complete", "staged_disarmed"}
                or row.get("worker_pending") is not False
                or row.get("cleanup_owner_pending") is not False
                or not row.get("receipt_durable") or receipt is None
                or receipt.get("status") != "completed" or receipt.get("cleanup_pending")):
            raise ValueError("fixture_effect_not_settled")
        expected_purpose = {value["targets"]["backup"]: "backup.create",
                            value["targets"]["repaired"]: "doctor.repair.apply",
                            value["targets"]["restored"]: "restore.stage"}[str(artifact)]
        with Directory(artifact) as pinned:
            actual_identity = [pinned.identity.device, pinned.identity.inode]
        identity = receipt.get("target_identity") or {}
        if (row.get("kind") != expected_purpose or receipt.get("purpose") != expected_purpose
                or row.get("operation_id") != receipt.get("operation_id")
                or receipt.get("target_name") != artifact.name
                or [identity.get("device"), identity.get("inode")]
                != [str(number) for number in actual_identity]):
            raise ValueError("fixture_receipt_identity_mismatch")
    if artifact == Path(value["targets"]["restored"]):
        return _verify_restored(value, profile, artifact, receipt, result)
    manifest, _ = read_private(artifact / "manifest.json")
    document = json.loads(manifest)
    if receipt is not None and receipt.get("manifest_sha256") != digest(manifest):
        raise ValueError("fixture_receipt_manifest_mismatch")
    resources = document["resources"]
    roles = [row["role"] for row in resources]
    if (document.get("format") != "jarvis-backup/1" or len(roles) != len(set(roles))
            or set(roles) != {*_ROLES, "agents", "configuration_portable"}):
        raise ValueError("fixture_artifact_roles_invalid")
    expected_files = {"manifest.json", "data"}
    with Directory(artifact) as pinned:
        if set(os.listdir(pinned.fd)) != expected_files:
            raise ValueError("fixture_artifact_inventory_invalid")
    with Directory(artifact / "data") as pinned:
        if set(os.listdir(pinned.fd)) != {Path(row["path"]).name for row in resources}:
            raise ValueError("fixture_artifact_inventory_invalid")
    observed = {}
    for row in resources:
        role = row["role"]
        expected_path = "data/" + role + (".sqlite3" if role in _ROLES else ".json")
        if row["path"] != expected_path:
            raise ValueError("fixture_artifact_path_invalid")
        data, _ = read_private(artifact / expected_path)
        if len(data) != row["size"] or digest(data) != row["sha256"]:
            raise ValueError("fixture_artifact_hash_mismatch")
        if role in _ROLES:
            if data[18:20] != b"\1\1":
                raise ValueError("fixture_artifact_not_standalone")
            connection = sqlite3.connect(":memory:")
            try:
                connection.deserialize(data)
                observed[role] = _projection(connection, role)
            finally:
                connection.close()
    if observed != value["expected"]:
        raise ValueError("fixture_logical_projection_changed")
    if source_oracle(profile) != value["source_members"]:
        raise ValueError("fixture_original_changed")
    return {**result, "artifact_sha256": digest(manifest), "logical_roles": sorted(observed),
            "artifact_integrity": True}


def _verify_restored(value, profile, artifact, receipt, result):
    if receipt is None or receipt.get("purpose") != "restore.stage":
        raise ValueError("fixture_restore_receipt_required")
    expected = {*NAMES.values(), "configuration.json", "profiles", "restore_record.json"}
    with Directory(artifact) as pinned:
        if set(os.listdir(pinned.fd)) != expected:
            raise ValueError("fixture_restore_inventory_invalid")
    with Directory(artifact / "profiles") as pinned:
        if set(os.listdir(pinned.fd)) != {"agents.json"}:
            raise ValueError("fixture_restore_inventory_invalid")
    marker = json.loads(read_private(artifact / "restore_record.json")[0])
    if (marker.get("format") != "jarvis-restore-record/1"
            or marker.get("activation_required") is not True
            or marker.get("replay_allowed") is not False
            or marker.get("source_manifest_sha256") != receipt.get("manifest_sha256")
            or marker.get("profile_id") != receipt.get("new_profile_id")):
        raise ValueError("fixture_restore_marker_invalid")
    configuration = json.loads(read_private(artifact / "configuration.json")[0])
    if configuration != {"version": 1, "workspace": None, "workspace_identity": None,
                         "bindings": {}, "mcp_servers": {}}:
        raise ValueError("fixture_restore_not_disarmed")
    observed, hashes = {}, {}
    for role, name in NAMES.items():
        data, _ = read_private(artifact / name)
        if data[18:20] != b"\1\1":
            raise ValueError("fixture_artifact_not_standalone")
        db = sqlite3.connect(":memory:")
        try:
            db.deserialize(data)
            observed[role] = _projection(db, role)
        finally:
            db.close()
        hashes[role] = digest(data)
    expected_projection = json.loads(canonical(value["expected"]))
    # Every generated schedule starts disabled but can retain its next_at. The
    # documented restore disarm clears that timestamp; all other cells remain
    # exact. These fixtures have no unfinished occurrences or conversation runs.
    if (expected_projection["state"]["tables"]["runs"]
            or expected_projection["schedules"]["tables"]["occurrences"]):
        raise ValueError("fixture_unexpected_unsettled_rows")
    for row in expected_projection["schedules"]["tables"]["schedules"]:
        if len(row) != 11:
            raise ValueError("fixture_schedule_shape_changed")
        row[6], row[8] = 0, None  # Own schema v1: enabled, next_at.
    if observed != expected_projection or source_oracle(profile) != value["source_members"]:
        raise ValueError("fixture_logical_projection_changed")
    return {**result, "restored_disarmed": True, "logical_roles": sorted(observed),
            "restored_sqlite_sha256": hashes,
            "source_manifest_sha256": marker["source_manifest_sha256"]}


def rejection_fixture(fixture, kind):
    """Controlled corruption before starting a synthetic service; never SQLite."""
    if kind not in {"no_commit", "ambiguous", "journal_checksum"}:
        raise ValueError("fixture_negative_invalid")
    verify(fixture)
    raw, _ = read_private(Path(fixture), 8 * 1024 * 1024)
    value = json.loads(raw)
    profile = Path(value["profile"])
    if kind == "no_commit":
        path = profile / "memory.sqlite3-wal"
        data = bytearray(read_private(path)[0])
        data[48] ^= 1  # First frame checksum: no verified commit remains.
    elif kind == "ambiguous":
        path = profile / "memory.sqlite3-journal"
        data = b"JARVIS SYNTHETIC MIXED FAMILY"
    else:
        path = profile / "memory.sqlite3-journal"
        data = bytearray(read_private(path)[0])
        sector = int.from_bytes(data[20:24], "big")
        page_size = int.from_bytes(data[24:28], "big")
        data[sector + page_size + 4] ^= 1
    path.write_bytes(data)
    path.chmod(0o600)
    value["negative_kind"] = kind
    value["source_members"] = source_oracle(profile)
    Path(fixture).write_bytes(canonical(value))
    return verify(fixture)


def socket_binding(profile):
    with Directory(profile) as directory:
        info = os.stat("service.sock", dir_fd=directory.fd, follow_symlinks=False)
        if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ValueError("fixture_socket_identity_invalid")
        return {"metadata": [str(value) for value in _metadata(info)], "type": "socket"}


def retire_fixture_socket(fixture, expected, *, child_exit_confirmed=False):
    """Explicit TEST intervention after its owner confirmed the child's exit.

    Historical fixture intervention, independent of productive stale-socket
    recovery. It acquires the profile's actual exclusive lease and removes
    exactly one previously pinned socket; it never guesses ownership.
    """
    from jarvis_core.host_profile import ProfileLease
    if child_exit_confirmed is not True or expected.get("type") != "socket":
        raise ValueError("fixture_confirmed_child_exit_required")
    verify(fixture)
    data = json.loads(read_private(Path(fixture), 8 * 1024 * 1024)[0])
    profile = Path(data["profile"])
    lease = ProfileLease.acquire(profile, create=False)
    try:
        if [lease.identity.device, lease.identity.inode] != data["profile_identity"]:
            raise ValueError("fixture_profile_identity_changed")
        lease.assert_current()
        if socket_binding(profile) != expected:
            raise ValueError("fixture_socket_identity_changed")
        with Directory(profile) as directory:
            if socket_binding(profile) != expected:
                raise ValueError("fixture_socket_identity_changed")
            os.unlink("service.sock", dir_fd=directory.fd)
            os.fsync(directory.fd)
        lease.assert_current()
    finally:
        lease.close()
    verify(fixture)
    return {"removed_only": "service.sock", "identity_matched": True,
            "profile_lease_held": True, "confirmed_child_exit": True,
            "explicit_synthetic_intervention": True, "production_auto_recovery": False}


async def serve_fixture(fixture, *, negative=None):
    """Fixture IPC host only. Synthetic clients decide; no actual owner UI."""
    from jarvis_core.service import JarvisService

    if negative is not None:
        rejection_fixture(fixture, negative)
    initial = verify(fixture)
    raw, _ = read_private(Path(fixture), 8 * 1024 * 1024)
    specification = json.loads(raw)
    profile = Path(specification["profile"])

    class NoAccount:
        calls = 0

        async def account_status(self):
            self.calls += 1
            raise AssertionError("No account in recovery fixture")

        async def chat(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("No inference in recovery fixture")

        async def aclose(self):
            pass

    peer = NoAccount()
    service = JarvisService(profile, peer)
    barrier, reached = threading.Event(), threading.Event()
    controls = {"block": False}
    from jarvis_core.recovery import RecoveryContext
    old_checkpoint = RecoveryContext.checkpoint

    def checkpoint(context, stage, write=False):
        if controls["block"] and stage == "cut_validate":
            reached.set()
            if not barrier.wait(30):
                raise ValueError("fixture_barrier_timeout")
        return old_checkpoint(context, stage, write)

    RecoveryContext.checkpoint = checkpoint

    async def control(params):
        if set(params) != {"action"}:
            raise ValueError("fixture_params_invalid")
        action = params["action"]
        if action == "advance_clock":
            service.maintenance_coordinator.clock = lambda: time.monotonic() + 121
        elif action == "short_ttl":
            service.authority.ttl = .08  # Bounded real timer in this synthetic child only.
        elif action == "block_before_publish":
            controls["block"] = True
        elif action == "blocked":
            pass
        elif action == "crash":
            os._exit(23)  # Only this explicitly marked child fixture process.
        else:
            raise ValueError("fixture_params_invalid")
        return {"blocked": reached.is_set(), "synthetic_control": True}

    async def trace(params):
        if params:
            raise ValueError("fixture_params_invalid")
        result = verify(fixture)
        return {**result, "provider_calls": peer.calls,
                "stores_closed": service.store._disk is None and service.memory._disk is None
                and service.schedule_store._connection is None,
                "expected_loss": specification["expected_loss"],
                "repair_role": specification["case"]["repair_role"],
                "expected_projection_sha256": specification["expected_projection_sha256"],
                "initial_fixture_sha256": initial["fixture_sha256"],
                "previous_operations": list(service.maintenance_coordinator.journal.previous),
                "decision_origin": "synthetic_fixture_client",
                "socket_binding": socket_binding(profile),
                "production_socket_recovered": service.server.recovered_stale_socket}

    async def check_artifact(params):
        if set(params) != {"target", "operation_id"} or params["target"] not in {
                "backup", "repaired", "restored"}:
            raise ValueError("fixture_params_invalid")
        status = await service.maintenance_coordinator.status({
            "operation_id": params["operation_id"]})
        path = Path(fixture).parent / (params["target"] + "-status.json")
        with path.open("xb") as stream:
            stream.write(canonical(status))
        path.chmod(0o600)
        return verify(fixture, specification["targets"][params["target"]], path)

    service.server._handlers["fixture.matrix.control"] = control
    service.server._handlers["fixture.matrix.trace"] = trace
    service.server._handlers["fixture.matrix.verify"] = check_artifact
    done = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, done.set)
    try:
        await service.start()
        # Service IPC bootstrap itself must preserve ALL three cold families.
        verify(fixture)
        await done.wait()
    finally:
        for operation in service.maintenance_coordinator._operations.values():
            operation.cancellation.set()
        barrier.set()
        await service.aclose()
        RecoveryContext.checkpoint = old_checkpoint


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    create = commands.add_parser("prepare")
    create.add_argument("--root", type=Path, required=True)
    create.add_argument("--case", action="append")
    check = commands.add_parser("verify")
    check.add_argument("--fixture", type=Path, required=True)
    check.add_argument("--artifact", type=Path)
    check.add_argument("--status", type=Path)
    server = commands.add_parser("serve")
    server.add_argument("--fixture", type=Path, required=True)
    server.add_argument("--negative", choices=("no_commit", "ambiguous", "journal_checksum"))
    args = parser.parse_args(argv)
    if args.command == "list":
        result = {"cases": cases(), "count": len(cases())}
    elif args.command == "prepare":
        result = prepare(args.root, args.case)
    elif args.command == "verify":
        result = verify(args.fixture, args.artifact, args.status)
    else:
        asyncio.run(serve_fixture(args.fixture, negative=args.negative))
        return 0
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
