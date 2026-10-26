"""Real private fixtures only: no owner profiles, accounts, HOME scans or UI."""

import hashlib
import json
import os
import shutil
import stat
from dataclasses import replace
from uuid import uuid4

import pytest

from jarvis_core import workspace_recovery as wr
from jarvis_core.recovery_fs import FileIdentity, RecoveryFsError, no_replace


def identity(path):
    return FileIdentity.of(path.stat())


def write(path, data=b"PRUEBA SELECCIONADA", mode=0o644):
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)
    return path


def selection(root, *paths, forbidden=()):
    return wr.WorkspaceSelection(root, identity(root), tuple(
        path if isinstance(path, wr.SelectedWorkspaceEntry) else wr.SelectedWorkspaceEntry(path)
        for path in paths
    ), forbidden)


def target(parent, root, name="candidate"):
    return wr.WorkspaceTarget(parent, identity(parent), name, (root,))


def snapshot(root):
    write(root / "informes" / "uno.txt", b"UNO")
    write(root / "informes" / "dos.bin", bytes(range(256)))
    (root / "vacio").mkdir(mode=0o755)
    return wr.capture_workspace(selection(
        root, "informes/uno.txt", wr.SelectedWorkspaceEntry("informes/dos.bin", "artifact"),
        wr.SelectedWorkspaceEntry("vacio", kind="directory"),
    ))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode()


def mutate_manifest(snap, mutate):
    value = json.loads(snap.manifest)
    mutate(value)
    return replace(snap, manifest=canonical(value))


def test_exact_roundtrip_binary_artifact_and_empty_directory_are_portable(tmp_path):
    root = tmp_path / "source"
    root.mkdir(mode=0o755)
    snap = snapshot(root)
    summary = wr.validate_workspace_snapshot(snap)
    manifest = json.loads(snap.manifest)
    assert summary.file_count == 2 and summary.artifact_count == 1
    assert summary.directory_count == 2 and summary.total_bytes == 259
    assert manifest["format"] == "jarvis-workspace/1"
    assert str(root).encode() not in snap.manifest
    assert not {"inode", "device", "source_binding"}.intersection(manifest)
    assert snap.manifest == canonical(manifest)
    assert summary.manifest_sha256 == hashlib.sha256(snap.manifest).hexdigest()
    portable = wr.WorkspaceSnapshot(snap.manifest, snap.files)  # RAM identities not exported.
    result = wr.restore_workspace_new(portable, target(tmp_path, root), uuid4().hex)
    restored = tmp_path / "candidate"
    assert result.effect_status == "new_unselected_workspace"
    assert result.activation_required and not result.replay_allowed
    assert result.target_identity == identity(restored)
    assert result.manifest_sha256 == summary.manifest_sha256
    assert (restored / "informes" / "uno.txt").read_bytes() == b"UNO"
    assert (restored / "informes" / "dos.bin").read_bytes() == bytes(range(256))
    assert list((restored / "vacio").iterdir()) == []
    for directory in (restored, restored / "informes", restored / "vacio"):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((restored / "informes" / "uno.txt").stat().st_mode) == 0o600
    assert (root / "informes" / "uno.txt").read_bytes() == b"UNO"
    assert not list(tmp_path.glob(".jarvis-stage-*"))


def test_directory_selection_never_discovers_children_or_private_neighbors(tmp_path, monkeypatch):
    root = tmp_path / "source"
    write(root / "selected" / "unselected.txt", b"NO LEER")
    write(root / "auth.json", b"NO LEER")
    actual_open, reads = os.open, []

    def opened(name, *args, **kwargs):
        reads.append(os.fspath(name))
        assert os.fspath(name) not in {"auth.json", "unselected.txt"}
        return actual_open(name, *args, **kwargs)

    def no_enumeration(*args, **kwargs):
        raise AssertionError("capture must not enumerate even the selected directory")

    monkeypatch.setattr(os, "open", opened)
    monkeypatch.setattr(os, "scandir", no_enumeration)
    snap = wr.capture_workspace(selection(
        root, wr.SelectedWorkspaceEntry("selected", kind="directory")))
    assert snap.files == () and wr.validate_workspace_snapshot(snap).directory_count == 1
    assert "selected" in reads


@pytest.mark.parametrize("path", [
    "", "/absolute", "../parent", "x/../parent", "x//y", "x/", "./file", "C:drive",
    "x\\file", "bad\x00name", "bad\nname", "dot.", "trailing ", "NUL.txt", "COM1",
    "cafe\u0301.txt", "\u202ehidden", "\uff47it", "x/\ud800", 'bad"name', "bad?name",
])
def test_nonportable_or_ambiguous_paths_fail_before_source_io(tmp_path, monkeypatch, path):
    root = tmp_path / "source"
    root.mkdir()
    selected = selection(root, path)
    monkeypatch.setattr(os, "open", lambda *a, **k: pytest.fail("invalid path opened source"))
    with pytest.raises(wr.WorkspaceRecoveryError, match="workspace_recovery_path_invalid"):
        wr.capture_workspace(selected)


@pytest.mark.parametrize("path", [
    ".git/config", "AUTH.JSON", ".ssh/id_rsa", ".aws/config", ".codex/config.toml",
    ".env.production", "keys/client.p12", "keychains/login", "memory.sqlite3-wal",
    "jarvis.sqlite3", "sub/configuration.json", ".jarvis-tmp-abcd", ".pending",
])
def test_private_state_credentials_and_temporary_names_are_not_importable(tmp_path, path):
    root = tmp_path / "source"
    root.mkdir()
    with pytest.raises(wr.WorkspaceRecoveryError, match="workspace_recovery_private_path"):
        wr.capture_workspace(selection(root, path))


@pytest.mark.parametrize("paths", [
    ("A/x.txt", "a/y.txt"), ("É.txt", "é.txt"), ("x", "x/y"), ("x", "x"),
])
def test_case_collisions_file_parents_and_duplicates_rejected_without_read(tmp_path, paths):
    root = tmp_path / "source"
    root.mkdir()
    with pytest.raises(wr.WorkspaceRecoveryError, match="workspace_recovery_path_collision"):
        wr.capture_workspace(selection(root, *paths))


def test_explicit_parent_directory_and_child_file_are_order_independent(tmp_path):
    root = tmp_path / "source"
    write(root / "sub" / "file.txt")
    entries = (wr.SelectedWorkspaceEntry("sub/file.txt"),
               wr.SelectedWorkspaceEntry("sub", kind="directory"))
    first = wr.capture_workspace(selection(root, *entries))
    second = wr.capture_workspace(selection(root, *reversed(entries)))
    assert first.manifest == second.manifest and first.files == second.files


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "directory", "writable"])
def test_selected_nonregular_links_and_shared_writes_fail_closed(tmp_path, kind):
    root = tmp_path / "source"
    root.mkdir(mode=0o755)
    original = write(root / "original.txt")
    chosen = root / "chosen"
    if kind == "symlink":
        chosen.symlink_to(original)
    elif kind == "hardlink":
        os.link(original, chosen)
    elif kind == "fifo":
        os.mkfifo(chosen, 0o600)
    elif kind == "directory":
        chosen.mkdir(mode=0o700)
    else:
        write(chosen, mode=0o666)
    with pytest.raises(wr.WorkspaceRecoveryError):
        wr.capture_workspace(selection(root, "chosen"))
    assert chosen.lstat() and original.read_bytes() == b"PRUEBA SELECCIONADA"


@pytest.mark.parametrize("kind", ["root_link", "ancestor_link", "writable_directory", "missing"])
def test_source_pin_rejects_symlink_ancestors_shared_directories_and_missing(tmp_path, kind):
    root = tmp_path / "source"
    write(root / "dir" / "file.txt")
    chosen = root
    if kind == "root_link":
        chosen = tmp_path / "alias"
        chosen.symlink_to(root, target_is_directory=True)
    elif kind == "ancestor_link":
        (root / "dir").rename(root / "original-dir")
        (root / "dir").symlink_to(root / "original-dir", target_is_directory=True)
    elif kind == "writable_directory":
        (root / "dir").chmod(0o777)
    else:
        (root / "dir" / "file.txt").unlink()
    with pytest.raises(wr.WorkspaceRecoveryError):
        wr.capture_workspace(selection(chosen, "dir/file.txt"))


def test_source_and_target_physical_identity_and_forbidden_roots_are_bound(tmp_path):
    root = tmp_path / "source"
    write(root / "file.txt")
    selected = selection(root, "file.txt")
    root.rename(tmp_path / "original")
    write(root / "file.txt", b"REEMPLAZO")
    with pytest.raises(wr.WorkspaceRecoveryError, match="source_changed"):
        wr.capture_workspace(selected)
    with pytest.raises(wr.WorkspaceRecoveryError, match="source_forbidden"):
        wr.capture_workspace(selection(root, "file.txt", forbidden=(root,)))
    snap = wr.capture_workspace(selection(root, "file.txt"))
    with pytest.raises(wr.WorkspaceRecoveryError, match="destination_overlaps_source"):
        wr.restore_workspace_new(snap, target(root, root, "nested"), uuid4().hex)


@pytest.mark.parametrize("change", ["same_size", "replace", "delete", "parent_replace"])
def test_double_read_and_final_identity_detect_real_source_races(tmp_path, change):
    root = tmp_path / "source"
    file = write(root / "sub" / "file.txt", b"ORIGINAL")
    changed = False

    def checkpoint(stage, write=False):
        nonlocal changed
        if stage == "workspace_capture_second_pass" and not changed:
            changed = True
            if change == "same_size":
                file.write_bytes(b"CAMBIADO")
            elif change == "replace":
                file.rename(file.with_name("old.txt"))
                globals()["write"](file, b"ORIGINAL")
            elif change == "delete":
                file.unlink()
            else:
                (root / "sub").rename(root / "old-sub")
                globals()["write"](file, b"ORIGINAL")

    with pytest.raises(wr.WorkspaceRecoveryError):
        wr.capture_workspace(selection(root, "sub/file.txt"), checkpoint)
    assert changed


def test_source_binding_revalidation_does_not_recopy_or_accept_stale_bytes(tmp_path, monkeypatch):
    root = tmp_path / "source"
    file = write(root / "file.txt", b"ANTES")
    snap = wr.capture_workspace(selection(root, "file.txt"))
    wr.check_workspace_source(snap)
    file.write_bytes(b"DESPUES")
    monkeypatch.setattr(os, "pread", lambda *a: pytest.fail("revalidation copied payload"))
    with pytest.raises(wr.WorkspaceRecoveryError, match="source_changed"):
        wr.check_workspace_source(snap)
    with pytest.raises(wr.WorkspaceRecoveryError, match="source_binding_required"):
        wr.check_workspace_source(replace(snap, source_binding=None))


@pytest.mark.parametrize("limits", [
    wr.WorkspaceLimits(max_files=1), wr.WorkspaceLimits(max_file_bytes=2),
    wr.WorkspaceLimits(max_total_bytes=4), wr.WorkspaceLimits(max_directories=1),
    wr.WorkspaceLimits(max_depth=1), wr.WorkspaceLimits(max_manifest_bytes=20),
    wr.WorkspaceLimits(max_work_bytes=4), wr.WorkspaceLimits(max_path_bytes=3),
])
def test_independent_snapshot_limits_reject_whole_selection(tmp_path, limits):
    root = tmp_path / "source"
    write(root / "sub" / "deep" / "file.txt", b"123")
    write(root / "second.txt", b"456")
    with pytest.raises(wr.WorkspaceRecoveryError):
        wr.capture_workspace(selection(root, "sub/deep/file.txt", "second.txt"), limits=limits)
    assert (root / "second.txt").read_bytes() == b"456"


@pytest.mark.parametrize("stage", [
    "workspace_capture_begin", "workspace_source_directory", "workspace_source_open",
    "workspace_source_read", "workspace_source_read_done", "workspace_capture_second_pass",
    "workspace_capture_final_check",
])
def test_cancellation_checkpoints_return_no_data_and_close_owned_source_handles(tmp_path, stage):
    root = tmp_path / "source"
    file = write(root / "sub" / "file.txt")
    reached = []

    def stop(current, write=False):
        reached.append(current)
        if current == stage:
            raise RecoveryFsError("recovery_cancelled")

    with pytest.raises(wr.WorkspaceRecoveryError) as error:
        wr.capture_workspace(selection(root, "sub/file.txt"), stop)
    assert error.value.code == "recovery_cancelled" and error.value.owner is None
    assert stage in reached and file.read_bytes() == b"PRUEBA SELECCIONADA"


@pytest.mark.parametrize("change", [
    "wrong_hash", "extra_field", "traversal", "collision", "role", "bool_size",
    "missing_directory", "wrong_total", "format", "exclusions", "order",
])
def test_manifest_and_payload_import_validation_rejects_tampering(tmp_path, change):
    root = tmp_path / "source"
    root.mkdir()
    snap = snapshot(root)

    def mutate(value):
        files = [row for row in value["entries"] if row["kind"] == "file"]
        if change == "wrong_hash":
            files[0]["sha256"] = "0" * 64
        elif change == "extra_field":
            value["permission"] = "approved"
        elif change == "traversal":
            files[0]["path"] = "../escape"
        elif change == "collision":
            files[1]["path"] = files[0]["path"].upper()
        elif change == "role":
            files[0]["role"] = "provider_credentials"
        elif change == "bool_size":
            files[0]["size"] = True
        elif change == "missing_directory":
            value["entries"] = [row for row in value["entries"] if row["kind"] == "file"]
        elif change == "wrong_total":
            value["total_bytes"] += 1
        elif change == "format":
            value["format"] = "jarvis-workspace/999"
        elif change == "exclusions":
            value["exclusions"] = []
        else:
            value["entries"].reverse()

    corrupt = mutate_manifest(snap, mutate)
    with pytest.raises(wr.WorkspaceRecoveryError):
        wr.restore_workspace_new(corrupt, target(tmp_path, root), uuid4().hex)
    assert not (tmp_path / "candidate").exists()
    assert not list(tmp_path.glob(".jarvis-stage-*"))


@pytest.mark.parametrize("kind", [
    "duplicate_keys", "noncanonical", "extra_payload", "changed_payload",
])
def test_portable_bytes_are_exact_and_duplicate_json_keys_are_rejected(tmp_path, kind):
    root = tmp_path / "source"
    root.mkdir()
    snap = snapshot(root)
    if kind == "duplicate_keys":
        snap = replace(snap, manifest=snap.manifest.replace(b'"format":', b'"format":0,"format":'))
    elif kind == "noncanonical":
        snap = replace(snap, manifest=snap.manifest + b"\n")
    elif kind == "extra_payload":
        snap = replace(snap, files=(*snap.files, wr.WorkspaceFile("extra.txt", b"extra")))
    else:
        snap = replace(snap, files=(replace(snap.files[0], data=b"CAMBIADO"), *snap.files[1:]))
    with pytest.raises(wr.WorkspaceRecoveryError):
        wr.validate_workspace_snapshot(snap)


@pytest.mark.parametrize("kind", ["file", "directory", "symlink"])
def test_existing_destination_is_never_overwritten_or_cleaned(tmp_path, kind):
    root = tmp_path / "source"
    write(root / "file.txt")
    snap = wr.capture_workspace(selection(root, "file.txt"))
    destination = tmp_path / "candidate"
    if kind == "file":
        write(destination, b"NO TOCAR")
    elif kind == "directory":
        write(destination / "original.txt", b"NO TOCAR")
    else:
        destination.symlink_to(root, target_is_directory=True)
    before = destination.lstat()
    with pytest.raises(wr.WorkspaceRecoveryError, match="destination_exists"):
        wr.restore_workspace_new(snap, target(tmp_path, root), uuid4().hex)
    assert destination.lstat().st_ino == before.st_ino
    assert not list(tmp_path.glob(".jarvis-stage-*"))


@pytest.mark.parametrize("stage", [
    "workspace_restore_begin", "workspace_restore_stage", "workspace_restore_mkdir",
    "workspace_restore_write", "workspace_restore_write_chunk",
    "workspace_restore_verify_directory", "workspace_restore_before_publish",
])
def test_restore_cancellation_before_publication_only_cleans_owned_staging(tmp_path, stage):
    root = tmp_path / "source"
    root.mkdir()
    snap = snapshot(root)

    def stop(current, write=False):
        if current == stage:
            raise RecoveryFsError("recovery_cancelled")

    with pytest.raises(wr.WorkspaceRecoveryError) as error:
        wr.restore_workspace_new(snap, target(tmp_path, root), uuid4().hex, stop)
    assert error.value.code == "recovery_cancelled"
    assert error.value.owner is None, error.value.cleanup_error_code
    assert not (tmp_path / "candidate").exists()
    assert not list(tmp_path.glob(".jarvis-stage-*"))
    assert (root / "informes" / "uno.txt").read_bytes() == b"UNO"


def test_destination_created_at_publish_cannot_be_replaced(tmp_path):
    root = tmp_path / "source"
    write(root / "file.txt")
    snap = wr.capture_workspace(selection(root, "file.txt"))

    def race(parent, old_name, new_name):
        write(parent.path / new_name / "foreign.txt", b"AJENO")
        no_replace(parent, old_name, new_name)

    with pytest.raises(wr.WorkspaceRecoveryError) as error:
        wr.restore_workspace_new(snap, target(tmp_path, root), uuid4().hex, publish=race)
    assert error.value.owner is None
    assert (tmp_path / "candidate" / "foreign.txt").read_bytes() == b"AJENO"
    assert not list(tmp_path.glob(".jarvis-stage-*"))


def test_lost_publish_ack_retains_owner_never_replays_or_deletes_candidate(tmp_path):
    root = tmp_path / "source"
    write(root / "file.txt")
    snap = wr.capture_workspace(selection(root, "file.txt"))
    calls = []

    def lost(parent, old_name, new_name):
        calls.append(new_name)
        no_replace(parent, old_name, new_name)
        raise OSError("ack lost")

    with pytest.raises(wr.WorkspaceRecoveryError) as error:
        wr.restore_workspace_new(snap, target(tmp_path, root), uuid4().hex, publish=lost)
    assert error.value.owner is None and error.value.effect_status == "unknown"
    assert error.value.publication_unknown
    assert calls == ["candidate"]
    assert (tmp_path / "candidate" / "file.txt").read_bytes() == b"PRUEBA SELECCIONADA"


def test_independent_published_tree_verification_detects_tamper(tmp_path):
    root = tmp_path / "source"
    write(root / "file.txt")
    snap = wr.capture_workspace(selection(root, "file.txt"))

    def tamper(parent, old_name, new_name):
        no_replace(parent, old_name, new_name)
        write(parent.path / new_name / "file.txt", b"ALTERADO", mode=0o600)

    with pytest.raises(wr.WorkspaceRecoveryError) as error:
        wr.restore_workspace_new(snap, target(tmp_path, root), uuid4().hex, publish=tamper)
    assert error.value.code == "workspace_recovery_verification_failed"
    assert error.value.owner is None and error.value.effect_status == "unknown"
    assert (tmp_path / "candidate" / "file.txt").read_bytes() == b"ALTERADO"


def test_failed_cleanup_retains_exact_owner_then_only_settles_its_files(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    snap = snapshot(root)
    original_unlink, stopped = os.unlink, False

    def denied(name, *args, **kwargs):
        nonlocal stopped
        if not stopped:
            stopped = True
            raise PermissionError("synthetic cleanup failure")
        return original_unlink(name, *args, **kwargs)

    def stop(stage, write=False):
        if stage == "workspace_restore_before_publish":
            raise RecoveryFsError("recovery_cancelled")

    monkeypatch.setattr(os, "unlink", denied)
    with pytest.raises(wr.WorkspaceRecoveryError) as error:
        wr.restore_workspace_new(snap, target(tmp_path, root), uuid4().hex, stop)
    owner = error.value.owner
    assert owner is not None and error.value.code == "recovery_cancelled"
    assert error.value.effect_status == "no_published_effect"
    assert list(tmp_path.glob(".jarvis-stage-*"))
    owner.cleanup()
    owner.cleanup()  # Idempotent settlement, not another operation.
    assert owner.closed and not list(tmp_path.glob(".jarvis-stage-*"))
    assert not (tmp_path / "candidate").exists()


def test_replaced_staging_file_is_never_deleted_as_owned(tmp_path):
    root = tmp_path / "source"
    write(root / "file.txt")
    snap = wr.capture_workspace(selection(root, "file.txt"))
    replaced = []

    def replace_then_stop(stage, write=False):
        if stage == "workspace_restore_before_publish":
            staged = next(tmp_path.glob(".jarvis-stage-*")) / "file.txt"
            staged.rename(staged.with_name("original.txt"))
            globals()["write"](staged, b"AJENO", mode=0o600)
            replaced.append(staged)
            raise RecoveryFsError("recovery_cancelled")

    with pytest.raises(wr.WorkspaceRecoveryError) as error:
        wr.restore_workspace_new(snap, target(tmp_path, root), uuid4().hex, replace_then_stop)
    assert error.value.owner is not None and replaced[0].read_bytes() == b"AJENO"
    with pytest.raises(wr.WorkspaceRecoveryError):
        error.value.owner.cleanup()
    assert replaced[0].read_bytes() == b"AJENO"


def test_unknown_stage_creation_ack_retains_custody_without_adopting_by_name(tmp_path, monkeypatch):
    root = tmp_path / "source"
    write(root / "file.txt")
    snap = wr.capture_workspace(selection(root, "file.txt"))
    mkdir = os.mkdir

    def lost(name, *args, **kwargs):
        mkdir(name, *args, **kwargs)
        if os.fspath(name).startswith(".jarvis-stage-"):
            raise OSError("lost mkdir acknowledgement")

    monkeypatch.setattr(os, "mkdir", lost)
    with pytest.raises(wr.WorkspaceRecoveryError) as error:
        wr.restore_workspace_new(snap, target(tmp_path, root), uuid4().hex)
    assert error.value.owner is not None and error.value.owner.creation_unknown
    staged = next(tmp_path.glob(".jarvis-stage-*"))
    with pytest.raises(wr.WorkspaceRecoveryError):
        error.value.owner.cleanup()
    assert staged.is_dir() and not (tmp_path / "candidate").exists()


def test_lost_close_ack_never_closes_reused_foreign_descriptor(tmp_path, monkeypatch):
    root = tmp_path / "source"
    file = write(root / "file.txt")
    foreign = write(tmp_path / "foreign.txt", b"AJENO")
    actual_close, actual_open, reused = os.close, os.open, []

    def lost(fd):
        if not reused and stat.S_ISREG(os.fstat(fd).st_mode):
            actual_close(fd)
            foreign_fd = actual_open(foreign, os.O_RDONLY)
            assert foreign_fd == fd
            reused.append(foreign_fd)
            raise OSError("lost close acknowledgement")
        return actual_close(fd)

    monkeypatch.setattr(os, "close", lost)
    with pytest.raises(wr.WorkspaceRecoveryError) as error:
        wr.capture_workspace(selection(root, file.name))
    assert error.value.owner is not None
    with pytest.raises(wr.WorkspaceRecoveryError):
        error.value.owner.cleanup()
    assert os.pread(reused[0], 5, 0) == b"AJENO"
    actual_close(reused[0])
    error.value.owner.cleanup()


def test_total_payload_budget_is_admitted_before_over_budget_file_is_read(tmp_path, monkeypatch):
    root = tmp_path / "source"
    file = write(root / "large.txt", b"123456")
    actual_pread, seen = os.pread, []

    def read(fd, *args):
        seen.append(identity(file) == FileIdentity.of(os.fstat(fd)))
        return actual_pread(fd, *args)

    monkeypatch.setattr(os, "pread", read)
    with pytest.raises(wr.WorkspaceRecoveryError, match="total_limit"):
        wr.capture_workspace(selection(root, file.name),
                             limits=wr.WorkspaceLimits(max_total_bytes=5))
    assert not seen


@pytest.mark.parametrize("when,change", [
    ("workspace_restore_confirm", "bytes"),
    ("workspace_restore_verify_final", "bytes"),
    ("workspace_restore_verify_final", "extra"),
    ("workspace_restore_verify_final", "replace_same_bytes"),
])
def test_final_boundary_cannot_certify_tamper_after_earlier_reads(tmp_path, when, change):
    root = tmp_path / "source"
    write(root / "file.txt", b"ORIGINAL")
    snap = wr.capture_workspace(selection(root, "file.txt"))
    published, attacked = False, False

    def monitor(stage, write=False):
        nonlocal published, attacked
        if stage == "workspace_restore_confirm":
            published = True
        if stage == when and published and not attacked:
            attacked = True
            candidate = tmp_path / "candidate"
            if change == "bytes":
                (candidate / "file.txt").write_bytes(b"CAMBIADO")
            elif change == "extra":
                globals()["write"](candidate / "extra.txt", b"AJENO", mode=0o600)
            else:
                (candidate / "file.txt").rename(candidate / "previous.txt")
                globals()["write"](candidate / "file.txt", b"ORIGINAL", mode=0o600)

    with pytest.raises(wr.WorkspaceRecoveryError) as error:
        wr.restore_workspace_new(snap, target(tmp_path, root), uuid4().hex, monitor)
    assert attacked and error.value.owner is None
    assert error.value.effect_status == "unknown"


@pytest.mark.parametrize("kind", ["escape_directory", "extra_file_binding", "wrong_fingerprint"])
def test_ram_binding_has_no_path_escape_or_unbounded_private_metadata_read(tmp_path, kind):
    root = tmp_path / "source"
    write(root / "sub" / "file.txt")
    snap = wr.capture_workspace(selection(root, "sub/file.txt"))
    binding = snap.source_binding
    if kind == "escape_directory":
        binding = replace(binding, directories=(("../escape", binding.root_identity),))
    elif kind == "extra_file_binding":
        binding = replace(binding, files=(*binding.files, ("auth.json", (1,) * 8)))
    else:
        binding = replace(binding, files=(("sub/file.txt", (True,) * 8),))
    with pytest.raises(wr.WorkspaceRecoveryError, match="source_binding_invalid"):
        wr.check_workspace_source(replace(snap, source_binding=binding))


@pytest.mark.parametrize("field", ["role", "kind"])
def test_malformed_container_types_get_content_free_manifest_errors(tmp_path, field):
    root = tmp_path / "source"
    write(root / "file.txt")
    snap = wr.capture_workspace(selection(root, "file.txt"))
    broken = mutate_manifest(snap, lambda value: value["entries"][0].update({field: []}))
    with pytest.raises(wr.WorkspaceRecoveryError):
        wr.validate_workspace_snapshot(broken)


def test_failed_cleanup_after_some_unlinks_is_idempotent_and_keeps_foreign_sibling(tmp_path,
                                                                               monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    snap = snapshot(root)
    actual_unlink, count = os.unlink, 0

    def fail_second(name, *args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise PermissionError("fixture second unlink failed")
        return actual_unlink(name, *args, **kwargs)

    def stop(stage, write=False):
        if stage == "workspace_restore_before_publish":
            raise RecoveryFsError("recovery_cancelled")

    unrelated = write(tmp_path / "unrelated.txt", b"CONSERVAR")
    monkeypatch.setattr(os, "unlink", fail_second)
    with pytest.raises(wr.WorkspaceRecoveryError) as error:
        wr.restore_workspace_new(snap, target(tmp_path, root), uuid4().hex, stop)
    assert count == 2 and error.value.owner is not None
    error.value.owner.cleanup()
    assert unrelated.read_bytes() == b"CONSERVAR"
    assert not list(tmp_path.glob(".jarvis-stage-*"))


def test_forbidden_root_guard_is_conservative_across_case_insensitive_hosts(tmp_path):
    root = tmp_path / "Selection"
    write(root / "file.txt")
    with pytest.raises(wr.WorkspaceRecoveryError, match="source_forbidden"):
        wr.capture_workspace(selection(root, "file.txt", forbidden=(tmp_path / "selection",)))
    snap = wr.capture_workspace(selection(root, "file.txt"))
    with pytest.raises(wr.WorkspaceRecoveryError, match="destination_overlaps_source"):
        wr.restore_workspace_new(snap, wr.WorkspaceTarget(
            tmp_path, identity(tmp_path), "SELECTION", (root,)), uuid4().hex)


def test_retargeted_destination_parent_does_not_receive_staging(tmp_path):
    root = tmp_path / "source"
    write(root / "file.txt")
    snap = wr.capture_workspace(selection(root, "file.txt"))
    parent = tmp_path / "parent"
    parent.mkdir(mode=0o700)
    reviewed = target(parent, root)
    parent.rename(tmp_path / "old-parent")
    parent.mkdir(mode=0o700)
    with pytest.raises(wr.WorkspaceRecoveryError, match="source_changed"):
        wr.restore_workspace_new(snap, reviewed, uuid4().hex)
    assert list(parent.iterdir()) == [] and list((tmp_path / "old-parent").iterdir()) == []


def test_unselected_temporary_neighbor_and_deleted_selection_are_never_resurrected(tmp_path):
    root = tmp_path / "source"
    write(root / "keep.txt", b"CONSERVAR")
    temporary = write(root / ".jarvis-tmp-neighbor", b"NO COPIAR")
    removed = write(root / "deleted.txt", b"NO RESUCITAR")
    removed.unlink()
    snap = wr.capture_workspace(selection(root, "keep.txt"))
    wr.restore_workspace_new(snap, target(tmp_path, root), uuid4().hex)
    assert list((tmp_path / "candidate").iterdir()) == [tmp_path / "candidate" / "keep.txt"]
    assert temporary.read_bytes() == b"NO COPIAR" and not removed.exists()
    with pytest.raises(wr.WorkspaceRecoveryError):
        wr.capture_workspace(selection(root, "keep.txt", "deleted.txt"))


def test_cancellation_during_ram_revalidation_stops_between_entries(tmp_path):
    root = tmp_path / "source"
    write(root / "a.txt")
    write(root / "b.txt")
    snap = wr.capture_workspace(selection(root, "a.txt", "b.txt"))
    visited = []

    def stop(stage, write=False):
        if stage == "workspace_source_revalidate_file":
            visited.append(stage)
            raise RecoveryFsError("recovery_cancelled")

    with pytest.raises(wr.WorkspaceRecoveryError) as error:
        wr.check_workspace_source(snap, stop)
    assert visited == ["workspace_source_revalidate_file"] and error.value.owner is None


def make_archive(tmp_path):
    root = tmp_path / "source"
    root.mkdir(mode=0o755)
    snap = snapshot(root)
    result = wr.write_workspace_archive_new(snap, target(tmp_path, root, "portable"), uuid4().hex)
    return root, snap, result, tmp_path / "portable"


def test_separate_archive_exact_layout_roundtrips_and_binds_reviewed_source(tmp_path):
    root, original, result, archive = make_archive(tmp_path)
    summary = wr.validate_workspace_snapshot(original)
    assert result.effect_status == "new_workspace_archive" and not result.activation_required
    assert result.file_count == 2 and result.directory_count == 2
    assert result.manifest_sha256 == summary.manifest_sha256
    assert {path.name for path in archive.iterdir()} == {"manifest.json", "data"}
    assert {path.name for path in (archive / "data").iterdir()} == {"00000000.bin", "00000001.bin"}
    assert (archive / "manifest.json").read_bytes() == original.manifest
    assert (archive / "data" / "00000000.bin").read_bytes() == bytes(range(256))
    loaded = wr.read_workspace_archive(archive, result.manifest_sha256,
                                       expected_identity=result.target_identity)
    assert loaded.manifest == original.manifest and loaded.files == original.files
    assert loaded.source_binding is None
    assert loaded.archive_binding.root == archive
    assert loaded.archive_binding.root_identity == result.target_identity
    wr.check_workspace_archive_source(loaded)
    staged = wr.restore_workspace_new(loaded, target(tmp_path, root), uuid4().hex)
    assert staged.manifest_sha256 == summary.manifest_sha256
    assert (tmp_path / "candidate" / "informes" / "uno.txt").read_bytes() == b"UNO"
    assert archive.is_dir() and (root / "informes" / "uno.txt").read_bytes() == b"UNO"


def test_empty_exact_selection_is_valid_without_claiming_whole_workspace(tmp_path):
    root = tmp_path / "source"
    write(root / "private-unselected.txt", b"NO LEER")
    snap = wr.capture_workspace(selection(root))
    result = wr.write_workspace_archive_new(snap, target(tmp_path, root, "portable"), uuid4().hex)
    loaded = wr.read_workspace_archive(tmp_path / "portable", result.manifest_sha256)
    assert loaded.files == () and wr.validate_workspace_snapshot(loaded).total_bytes == 0
    assert list((tmp_path / "portable" / "data").iterdir()) == []


@pytest.mark.parametrize("kind", [
    "extra_root", "extra_payload", "missing_payload", "symlink_payload", "hardlink_payload",
    "fifo_payload", "wrong_bytes", "writable_data", "writable_payload", "manifest_tamper",
    "data_link", "archive_link",
])
def test_archive_reader_never_accepts_noncanonical_inventory_or_special_files(tmp_path, kind):
    root, snap, result, archive = make_archive(tmp_path)
    payload = archive / "data" / "00000000.bin"
    if kind == "extra_root":
        write(archive / "auth.json", b"NO LEER", mode=0o600)
    elif kind == "extra_payload":
        write(archive / "data" / "extra.bin", b"EXTRA", mode=0o600)
    elif kind == "missing_payload":
        payload.unlink()
    elif kind == "symlink_payload":
        payload.unlink()
        payload.symlink_to(root / "informes" / "dos.bin")
    elif kind == "hardlink_payload":
        payload.unlink()
        os.link(root / "informes" / "dos.bin", payload)
    elif kind == "fifo_payload":
        payload.unlink()
        os.mkfifo(payload, 0o600)
    elif kind == "wrong_bytes":
        payload.write_bytes(b"CORRUPTO")
    elif kind == "writable_data":
        (archive / "data").chmod(0o777)
    elif kind == "writable_payload":
        payload.chmod(0o666)
    elif kind == "manifest_tamper":
        (archive / "manifest.json").write_bytes(snap.manifest + b"\n")
    elif kind == "data_link":
        (archive / "data").rename(archive / "old-data")
        (archive / "data").symlink_to(archive / "old-data", target_is_directory=True)
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(archive, target_is_directory=True)
        archive = alias
    with pytest.raises(wr.WorkspaceRecoveryError):
        wr.read_workspace_archive(archive, result.manifest_sha256)


@pytest.mark.parametrize("change", ["root", "data", "file", "manifest"])
def test_archive_cached_review_rejects_same_bytes_identity_replacement(tmp_path, change):
    _, _, result, archive = make_archive(tmp_path)
    loaded = wr.read_workspace_archive(archive, result.manifest_sha256)
    if change == "root":
        archive.rename(tmp_path / "previous-portable")
        shutil.copytree(tmp_path / "previous-portable", archive)
    elif change == "data":
        (archive / "data").rename(archive / "previous-data")
        shutil.copytree(archive / "previous-data", archive / "data")
    else:
        chosen = archive / "manifest.json" if change == "manifest" else (
            archive / "data" / "00000000.bin")
        data = chosen.read_bytes()
        chosen.rename(chosen.with_name("previous.bin"))
        write(chosen, data, mode=0o600)
    with pytest.raises(wr.WorkspaceRecoveryError):
        wr.check_workspace_archive_source(loaded)
    if change == "root":
        with pytest.raises(wr.WorkspaceRecoveryError, match="source_changed"):
            wr.read_workspace_archive(archive, result.manifest_sha256,
                                      expected_identity=result.target_identity)


def test_archive_review_hash_mismatch_fails_before_reading_any_payload(tmp_path, monkeypatch):
    _, _, _, archive = make_archive(tmp_path)
    actual_open = os.open

    def opened(name, *args, **kwargs):
        assert not os.fspath(name).endswith(".bin")
        return actual_open(name, *args, **kwargs)

    monkeypatch.setattr(os, "open", opened)
    with pytest.raises(wr.WorkspaceRecoveryError, match="archive_review_changed"):
        wr.read_workspace_archive(archive, "0" * 64)


@pytest.mark.parametrize("stage", [
    "workspace_archive_read_begin", "workspace_archive_inventory", "workspace_archive_second_pass",
    "workspace_archive_final_check", "workspace_source_read",
])
def test_archive_read_cancellation_keeps_artifact_unchanged(tmp_path, stage):
    _, _, result, archive = make_archive(tmp_path)
    before = {path.relative_to(archive): path.read_bytes() for path in archive.rglob("*")
              if path.is_file()}

    def stop(current, write=False):
        if current == stage:
            raise RecoveryFsError("recovery_cancelled")

    with pytest.raises(wr.WorkspaceRecoveryError) as error:
        wr.read_workspace_archive(archive, result.manifest_sha256, stop)
    assert error.value.owner is None and error.value.code == "recovery_cancelled"
    assert before == {path.relative_to(archive): path.read_bytes() for path in archive.rglob("*")
                      if path.is_file()}


@pytest.mark.parametrize("change", ["bytes", "extra", "manifest"])
def test_archive_final_checkpoint_cannot_hide_later_tamper(tmp_path, change):
    _, _, result, archive = make_archive(tmp_path)

    def mutate(stage, write=False):
        if stage != "workspace_archive_final_check":
            return
        if change == "extra":
            globals()["write"](archive / "data" / "extra.bin", b"EXTRA", mode=0o600)
        else:
            chosen = archive / "manifest.json" if change == "manifest" else (
                archive / "data" / "00000000.bin")
            chosen.write_bytes(b"CAMBIADO")

    with pytest.raises(wr.WorkspaceRecoveryError):
        wr.read_workspace_archive(archive, result.manifest_sha256, mutate)


def test_archive_writer_keeps_existing_selected_artifact_unchanged(tmp_path):
    root, snap, _, archive = make_archive(tmp_path)
    original = (archive / "manifest.json").read_bytes()
    with pytest.raises(wr.WorkspaceRecoveryError, match="destination_exists"):
        wr.write_workspace_archive_new(snap, target(tmp_path, root, "portable"), uuid4().hex)
    assert original == (archive / "manifest.json").read_bytes()


def test_cached_archive_revalidation_is_not_a_call_to_the_archive_reader(tmp_path, monkeypatch):
    _, _, result, archive = make_archive(tmp_path)
    loaded = wr.read_workspace_archive(archive, result.manifest_sha256)
    monkeypatch.setattr(wr, "read_workspace_archive",
                        lambda *a, **k: pytest.fail("recaptured archive"))
    wr.check_workspace_archive_source(loaded)
    with pytest.raises(wr.WorkspaceRecoveryError, match="destination_overlaps_source"):
        wr.restore_workspace_new(loaded, target(archive, archive, "nested"), uuid4().hex)
