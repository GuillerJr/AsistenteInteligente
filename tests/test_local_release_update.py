"""Own local release fixtures only; no installed app, account or live approval."""

import fcntl
import hashlib
import json
import os
import stat
import threading
import time
from dataclasses import asdict, replace
from uuid import uuid4

import pytest

from jarvis_core import local_release_update as release
from jarvis_core.recovery import RecoveryContext, canonical
from jarvis_core.recovery_fs import FileIdentity
from jarvis_core.tools import HostPurpose


def context(check=None):
    return RecoveryContext(
        uuid4().hex,
        "synthetic-profile",
        3,
        check or (lambda *_: None),
        threading.Event(),
        time.monotonic() + 60,
    )


def profile_pin():
    return release.ProfilePin(
        "synthetic-profile",
        3,
        FileIdentity(10, 20),
        "b" * 64,
        (("state", 2), ("memory", 2), ("schedules", 1)),
        "c" * 64,
    )


def artifact(root, version="v1", schemas=None):
    root.mkdir(mode=0o700)
    purposes = sorted(purpose.value for purpose in HostPurpose)
    resources = {
        "python/jarvis_core/__init__.py": (b"# own synthetic package\n", False),
        "python/jarvis_core/service.py": (b"raise RuntimeError('MUST NEVER EXECUTE')\n", False),
        "python/jarvis_core/maintenance_journal.py": (
            ("_PURPOSES = {" + ", ".join(repr(v) for v in purposes) + "}\n").encode(),
            False,
        ),
        "app/Jarvis.app/Contents/MacOS/Jarvis": (("SYNTHETIC NATIVE " + version).encode(), True),
        "app/Jarvis.app/Contents/Info.plist": (b"SYNTHETIC PLIST", False),
    }
    rows = []
    for name, (data, executable) in resources.items():
        path = root / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o700 if executable else 0o600)
        rows.append(
            {
                "path": name,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "executable": executable,
            }
        )
    manifest = {
        "format": release.FORMAT,
        "product": "Jarvis",
        "channel": "development",
        "version_key": version,
        "entrypoints": {
            "python": "python/jarvis_core/service.py",
            "native": "app/Jarvis.app/Contents/MacOS/Jarvis",
        },
        "external_dependencies": {
            "python": "external_required",
            "codex": "external_official_required",
        },
        "schemas": schemas or {"state": [1, 2], "memory": [1, 2], "schedules": [1, 1]},
        "maintenance_purposes": purposes,
        "files": rows,
    }
    write_manifest(root, manifest)
    return root


def write_manifest(root, value):
    (root / "release.json").write_bytes(canonical(value))
    (root / "release.json").chmod(0o600)


def manifest(root):
    return json.loads((root / "release.json").read_bytes())


def review(source, target, **kwargs):
    return release.review_release(source, target, context(), profile_pin(), **kwargs)


def install(source, target):
    return release.apply_release(review(source, target), context())


@pytest.fixture
def setup(tmp_path):
    target = tmp_path / "dedicated-install"
    target.mkdir(mode=0o700)
    return artifact(tmp_path / "own-artifact"), target


def test_install_update_rollback_select_coherent_source_and_native_without_launch(setup, tmp_path):
    source, target = setup
    first = install(source, target)
    assert first.status == "completed" and first.version_key == "v1"
    assert first.previous_key is None and not first.launch_performed
    assert first.profile_data_modified is False and first.automatic_replay is False
    second = install(artifact(tmp_path / "second", "v2"), target)
    assert second.previous_key == "v1" and second.current_sha256 != first.current_sha256
    back = release.review_rollback(target, context(), profile_pin())
    assert back.kind == "release.rollback" and back.version_key == "v1"
    receipt = release.apply_release(back, context())
    state = release.inspect_installation(target, context())
    assert state["current"]["release_key"] == "v1"
    assert state["current"]["previous"]["release_key"] == "v2"
    assert receipt.previous_key == "v2" and receipt.operation_id != first.operation_id
    for version in ("v1", "v2"):
        installed = target / "releases" / version
        value = manifest(installed)
        for row in value["files"]:
            path = installed / row["path"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]
            assert stat.S_IMODE(path.stat().st_mode) == (0o500 if row["executable"] else 0o400)
        assert stat.S_IMODE(installed.stat().st_mode) == 0o500
    assert state["pending"] is None and not state["mutation_performed"]
    assert len(list((target / "receipts").iterdir())) == 3
    assert receipt.release_authenticity == "not_established"
    assert receipt.restored_profile_activation_requires_owner


def test_review_is_readonly_and_binds_artifact_profile_copy_and_platform_evidence(setup):
    source, target = setup
    original = {
        p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob("*") if p.is_file()
    }
    result = review(
        source,
        target,
        authenticity_verifier=lambda *_: {
            "native_signature": "ad_hoc_valid",
            "release_authenticity": "not_established",
        },
    )
    assert not list(target.iterdir())
    assert result.metadata()["profile_pin"]["backup_manifest_sha256"] == "c" * 64
    assert result.metadata()["release_authenticity"] == "not_established"
    assert result.metadata()["external_dependencies"]["codex"] == "external_official_required"
    assert original == {
        p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob("*") if p.is_file()
    }
    with pytest.raises(release.ReleaseError, match="release_platform_evidence_changed"):
        release.apply_release(result, context())
    assert not (target / "CURRENT").exists()


@pytest.mark.parametrize(
    "bad",
    [
        "../outside",
        "/tmp/escape",
        "python/../auth.json",
        "python/auth.json",
        "python/.git/config",
        "python/home/a",
        "python/x\\evil",
        "app//x",
        "python/./x",
    ],
)
def test_manifest_rejects_traversal_credentials_and_noncanonical_paths(setup, bad):
    source, target = setup
    value = manifest(source)
    value["files"][0]["path"] = bad
    write_manifest(source, value)
    with pytest.raises(release.ReleaseError, match="release_resource_path_invalid"):
        review(source, target)
    assert not list(target.iterdir())


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "extra", "writable"])
def test_unsafe_resource_never_reaches_publication(setup, tmp_path, kind):
    source, target = setup
    path = source / "python/jarvis_core/service.py"
    if kind == "symlink":
        outside = tmp_path / "outside"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
    elif kind == "hardlink":
        os.link(path, tmp_path / "linked")
    elif kind == "fifo":
        path.unlink()
        os.mkfifo(path, 0o600)
    elif kind == "extra":
        (source / "extra.py").write_text("NEVER EXECUTE")
    else:
        path.chmod(0o666)
    with pytest.raises(release.ReleaseError):
        review(source, target)
    assert not list(target.iterdir())


def test_artifact_changed_after_review_rejected_before_any_selection(setup):
    source, target = setup
    result = review(source, target)
    (source / "python/jarvis_core/service.py").write_text("MUTATED")
    with pytest.raises(release.ReleaseError, match="release_resource_changed") as caught:
        release.apply_release(result, context())
    assert caught.value.receipt.effect_status == "no_current_change"
    assert caught.value.owner is None
    assert not (target / "CURRENT").exists()


def test_stale_current_cas_does_not_override_another_update(setup, tmp_path):
    source, target = setup
    install(source, target)
    second = artifact(tmp_path / "second", "v2")
    stale = review(second, target)
    install(artifact(tmp_path / "third", "v3"), target)
    before = (target / "CURRENT").read_bytes()
    with pytest.raises(release.ReleaseError, match="release_current_changed"):
        release.apply_release(stale, context())
    assert (target / "CURRENT").read_bytes() == before


def test_version_keys_cannot_be_reused_but_exact_previous_is_rollback(setup, tmp_path):
    source, target = setup
    install(source, target)
    with pytest.raises(release.ReleaseError, match="release_version_already_current"):
        review(source, target)
    install(artifact(tmp_path / "second", "v2"), target)
    with pytest.raises(release.ReleaseError, match="release_version_key_reused"):
        review(source, target)
    assert release.review_rollback(target, context(), profile_pin()).version_key == "v1"


def test_rollback_requires_previous_and_fresh_compatible_schema_pin(setup, tmp_path):
    source, target = setup
    with pytest.raises(release.ReleaseError, match="release_no_previous_version"):
        release.review_rollback(target, context(), profile_pin())
    install(source, target)
    install(
        artifact(
            tmp_path / "second", "v2", {"state": [1, 3], "memory": [1, 3], "schedules": [1, 1]}
        ),
        target,
    )
    pin = replace(profile_pin(), schemas=(("state", 3), ("memory", 2), ("schedules", 1)))
    with pytest.raises(release.ReleaseError, match="release_profile_schema_incompatible"):
        release.review_rollback(target, context(), pin)
    assert json.loads((target / "CURRENT").read_bytes())["release_key"] == "v2"


@pytest.mark.parametrize("change", ["generation", "backup", "schemas"])
def test_invalid_profile_pin_and_scope_rejected(setup, change):
    source, target = setup
    if change == "generation":
        pin = replace(profile_pin(), generation=4)
        with pytest.raises(release.ReleaseError, match="release_profile_pin_changed"):
            release.review_release(source, target, context(), pin)
    elif change == "backup":
        with pytest.raises(release.ReleaseError, match="release_profile_pin_invalid"):
            replace(profile_pin(), backup_manifest_sha256="")
    else:
        with pytest.raises(release.ReleaseError, match="release_profile_pin_invalid"):
            replace(profile_pin(), schemas=(("state", 2), ("state", 2), ("memory", 2)))


@pytest.mark.parametrize("stage", ["pending_durable", "release_staged"])
def test_pre_current_failure_never_selects_partial_release(setup, stage):
    source, target = setup

    def fail(where):
        if where == stage:
            raise OSError("synthetic fault")

    with pytest.raises(release.ReleaseError) as caught:
        release.apply_release(review(source, target), context(), fault=fail)
    assert caught.value.receipt.effect_status == "no_current_change"
    assert not (target / "CURRENT").exists()
    assert caught.value.owner is None
    assert not (target / "PENDING").exists()


@pytest.mark.parametrize(
    "stage", ["before_current_replace", "after_current_replace", "receipt_durable"]
)
def test_publication_ack_uncertain_retains_owner_blocks_retry_and_inspects_readonly(setup, stage):
    source, target = setup

    def fail(where):
        if where == stage:
            raise OSError("synthetic lost ACK")

    with pytest.raises(release.ReleaseError) as caught:
        release.apply_release(review(source, target), context(), fault=fail)
    error = caught.value
    assert error.receipt.effect_status == "publication_unknown"
    assert error.owner is not None and not error.owner.closed
    pending = (target / "PENDING").read_bytes()
    state = release.inspect_installation(target, context())
    assert state["pending"]["target_key"] == "v1" and not state["automatic_replay"]
    assert state["prior_publication_durability"] == "not_established"
    assert (target / "PENDING").read_bytes() == pending
    error.owner.cleanup()
    assert error.owner.closed and (target / "PENDING").read_bytes() == pending
    with pytest.raises(release.ReleaseError, match="release_pending_requires_reconciliation"):
        review(source, target)


def test_cancel_before_effect_and_context_reuse_never_publish(setup):
    source, target = setup
    ctx = context()
    ctx.cancellation.set()
    with pytest.raises(Exception, match="recovery_cancelled"):
        release.apply_release(review(source, target), ctx)
    assert not list(target.iterdir())
    done = context()
    release.review_release(source, target, done, profile_pin())
    with pytest.raises(Exception, match="recovery_operation_already_used"):
        release.review_release(source, target, done, profile_pin())


def test_limits_checked_without_executing_artifact(setup):
    source, target = setup
    with pytest.raises(release.ReleaseError):
        review(source, target, limits=release.ReleaseLimits(max_file_bytes=8))
    with pytest.raises(release.ReleaseError, match="release_payload_limit"):
        review(source, target, limits=release.ReleaseLimits(max_total_bytes=8))
    assert not list(target.iterdir())


def test_readonly_reconciliation_never_claims_durable_receipt(setup):
    source, target = setup
    completed = install(source, target)
    snapshot = {
        p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()
    }
    result = release.inspect_installation(target, context())
    assert result["current"]["operation_id"] == completed.operation_id
    assert result["prior_publication_durability"] == "not_established"
    assert snapshot == {
        p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()
    }
    assert json.loads((target / "receipts" / (completed.operation_id + ".json")).read_bytes()) == (
        asdict(completed)
    )


def test_profile_identity_cannot_be_used_as_installation_or_artifact(setup):
    source, target = setup
    pin = replace(profile_pin(), root_identity=FileIdentity.of(target.stat()))
    with pytest.raises(release.ReleaseError, match="release_install_root_is_profile"):
        release.review_release(source, target, context(), pin)
    pin = replace(profile_pin(), root_identity=FileIdentity.of(source.stat()))
    with pytest.raises(release.ReleaseError, match="release_artifact_is_profile"):
        release.review_release(source, target, context(), pin)


def test_central_authority_is_rechecked_after_staging_before_selection(setup):
    source, target = setup
    from jarvis_core.recovery_fs import RecoveryFsError

    def guard(stage, write):
        if stage == "release.current_publish":
            raise RecoveryFsError("synthetic_authority_revoked")

    with pytest.raises(release.ReleaseError, match="synthetic_authority_revoked") as caught:
        release.apply_release(review(source, target), context(guard))
    assert caught.value.receipt.effect_status == "no_current_change"
    assert not (target / "CURRENT").exists() and not (target / "PENDING").exists()


def test_cas_changed_at_final_authority_boundary_is_preserved(setup, tmp_path):
    source, target = setup
    install(source, target)
    plan = review(artifact(tmp_path / "v2", "v2"), target)
    original = (target / "CURRENT").read_bytes()
    changed = json.loads(original)
    changed["revision"] = uuid4().hex
    changed = canonical(changed)

    def guard(stage, write):
        if stage == "release.current_publish":
            (target / "CURRENT").write_bytes(changed)

    with pytest.raises(release.ReleaseError, match="release_current_changed"):
        release.apply_release(plan, context(guard))
    assert (target / "CURRENT").read_bytes() == changed


def test_existing_installation_lock_blocks_effect_and_current_is_untouched(setup, tmp_path):
    source, target = setup
    install(source, target)
    plan = review(artifact(tmp_path / "v2", "v2"), target)
    fd = os.open(target / "INSTALL.lock", os.O_RDWR)
    before = (target / "CURRENT").read_bytes()
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(release.ReleaseError, match="release_installation_busy"):
            release.apply_release(plan, context())
        assert (target / "CURRENT").read_bytes() == before
    finally:
        os.close(fd)


def test_rollback_rejects_tampered_previous_payload(setup, tmp_path):
    source, target = setup
    install(source, target)
    install(artifact(tmp_path / "v2", "v2"), target)
    previous = target / "releases/v1/python/jarvis_core/service.py"
    previous.chmod(0o600)
    previous.write_bytes(b"TAMPERED PREVIOUS")
    current = (target / "CURRENT").read_bytes()
    with pytest.raises(release.ReleaseError, match="release_resource_changed"):
        release.review_rollback(target, context(), profile_pin())
    assert (target / "CURRENT").read_bytes() == current


def test_unknown_mkdir_ack_never_adopts_or_removes_present_name(setup, monkeypatch):
    source, target = setup
    plan, ctx = review(source, target), context()
    original = os.mkdir

    def lost_ack(path, mode=0o777, *, dir_fd=None):
        original(path, mode, dir_fd=dir_fd)
        if str(path).startswith(".stage-"):
            raise OSError("synthetic mkdir ACK lost")

    monkeypatch.setattr(release.os, "mkdir", lost_ack)
    with pytest.raises(release.ReleaseError) as caught:
        release.apply_release(plan, ctx)
    owner = caught.value.owner
    assert owner is not None and not owner.closed
    stage = target / "releases" / (".stage-" + ctx.operation_id)
    assert stage.is_dir() and (target / "PENDING").exists()
    with pytest.raises(release.ReleaseError, match="release_creation_uncertain"):
        owner.cleanup()
    assert stage.is_dir()
    # Only independent confirmed absence can settle the unknown creation.
    stage.rmdir()
    owner.cleanup()
    assert owner.closed and not (target / "PENDING").exists()


def test_unknown_release_rename_ack_retains_pending_without_selecting_current(setup, monkeypatch):
    from jarvis_core import recovery_fs

    source, target = setup
    original = recovery_fs.no_replace

    def lost_ack(*args):
        original(*args)
        raise OSError("synthetic exclusive rename ACK lost")

    monkeypatch.setattr(recovery_fs, "no_replace", lost_ack)
    with pytest.raises(release.ReleaseError) as caught:
        release.apply_release(review(source, target), context())
    assert caught.value.owner is not None
    assert (target / "releases/v1").is_dir() and not (target / "CURRENT").exists()
    state = release.inspect_installation(target, context())
    assert state["pending"] is not None and not state["automatic_replay"]


def test_lost_close_ack_does_not_close_reused_foreign_descriptor(setup, tmp_path, monkeypatch):
    source, target = setup
    plan = review(source, target)
    original_close = os.close
    lost = []

    def close_loses_ack(fd):
        identity = FileIdentity.of(os.fstat(fd))
        lock = target / "INSTALL.lock"
        if not lost and lock.exists() and identity == FileIdentity.of(lock.stat()):
            lost.append(fd)
            original_close(fd)
            raise OSError("synthetic close ACK lost")
        original_close(fd)

    monkeypatch.setattr(release.os, "close", close_loses_ack)
    with pytest.raises(release.ReleaseError) as caught:
        release.apply_release(plan, context())
    owner = caught.value.owner
    assert lost and owner is not None and not owner.closed
    foreign = tmp_path / "foreign-own-test"
    foreign.write_bytes(b"do not close reused descriptor")
    opened = []
    try:
        while lost[0] not in opened:
            opened.append(os.open(foreign, os.O_RDONLY))
        with pytest.raises(release.ReleaseError, match="release_close_uncertain"):
            owner.cleanup()
        assert os.pread(lost[0], 100, 0) == b"do not close reused descriptor"
    finally:
        for fd in opened:
            original_close(fd)
    owner.cleanup()
    assert owner.closed


def test_manifest_duplicate_keys_and_duplicate_resources_fail_closed(setup):
    source, target = setup
    raw = (source / "release.json").read_bytes()
    (source / "release.json").write_bytes(raw[:-1] + b',"product":"Jarvis"}')
    with pytest.raises(release.ReleaseError, match="release_json_invalid"):
        review(source, target)
    value = json.loads(raw)
    value["files"].append(value["files"][0])
    write_manifest(source, value)
    with pytest.raises(release.ReleaseError, match="release_manifest_invalid"):
        review(source, target)
