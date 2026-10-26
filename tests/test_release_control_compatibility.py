"""Maintenance-reader compatibility, own synthetic packages and closed profiles.

The legacy literal was read from our 8bd1641 runtime snapshot; source SHA256
8cbd2d9d59d5a55ac937205313e32ae8679937f64c33f69858ffec9eef50dd47.
Only that literal is reproduced here. No legacy module, runtime or app executes.
"""

import hashlib
import importlib.util
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from test_local_release_update import artifact, context, profile_pin, write_manifest
from test_recovery_extensions import decision, params
from test_release_maintenance import verification
from test_wal_host_boundaries import NoAccount, finish

from jarvis_core import local_release_update as core
from jarvis_core import release_maintenance
from jarvis_core.maintenance_journal import MaintenanceJournal
from jarvis_core.recovery import canonical
from jarvis_core.recovery_fs import FileIdentity
from jarvis_core.service import JarvisService

_LEGACY_PURPOSES = ("backup.create", "memory.restore.copy_scope", "profile.restore.activate",
                    "restore.stage")
_EFFECT_PURPOSES = {"release.update.apply", "release.rollback.apply"}
_REPO = Path(__file__).resolve().parents[1]


def declared_artifact(root, purposes, version="controlled"):
    result = artifact(root, version,
                      {"state": [0, 2], "memory": [0, 2], "schedules": [0, 1]})
    value = json.loads((result / "release.json").read_bytes())
    declaration = "_PURPOSES = {" + ",".join(repr(item) for item in sorted(purposes)) + "}\n"
    module = result / "python/jarvis_core/maintenance_journal.py"
    module.write_text(declaration + "raise RuntimeError('SYNTHETIC SOURCE MUST NEVER RUN')\n")
    module.chmod(0o600)
    path = module.relative_to(result).as_posix()
    value["files"] = [row for row in value["files"] if row["path"] != path]
    value["files"].append({"path": path, "size": module.stat().st_size,
                           "sha256": hashlib.sha256(module.read_bytes()).hexdigest(),
                           "executable": False})
    value["maintenance_purposes"] = sorted(purposes)
    write_manifest(result, value)
    return result


def record(journal, purpose):
    journal.record(uuid4().hex, purpose, "f" * 64, "failed")


@pytest.mark.parametrize("required", [(), ("doctor.repair.apply",)])
def test_legacy_four_purpose_reader_cannot_be_selected_for_new_release_effect(
    tmp_path, required,
):
    source = declared_artifact(tmp_path / "legacy-reader", _LEGACY_PURPOSES)
    target = tmp_path / "installation"
    target.mkdir(mode=0o700)
    pin = replace(profile_pin(), maintenance_purposes=required)
    with pytest.raises(core.ReleaseError, match="release_maintenance_purposes_incompatible"):
        core.review_release(source, target, context(), pin)
    assert list(target.iterdir()) == []


@pytest.fixture
async def host(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    peer = NoAccount()
    service = JarvisService(profile, peer)
    service.store.create_session("SYNTHETIC CONTROL COMPATIBILITY")
    try:
        yield service, tmp_path, peer
    finally:
        await service.aclose()


@pytest.mark.parametrize("when", ["awaiting_owner", "after_pin", "before_current_cas"])
async def test_new_journal_purpose_invalidates_review_before_selecting_incompatible_reader(
    host, monkeypatch, when,
):
    service, parent, peer = host
    coordinator = service.maintenance_coordinator
    identity = await verification(coordinator, parent)
    supported = _EFFECT_PURPOSES | {"backup.create"}
    source = declared_artifact(parent / "limited-reader", supported)
    target = parent / "installation"
    target.mkdir(mode=0o700)
    read = await coordinator.release_review({"artifact_path": str(source),
        "install_root": str(target), "verification_id": identity})
    result = await finish(coordinator, read)
    assert result["phase"] == "complete", result
    review = result["result"]["review"]
    required = set(review["metadata"]["profile_pin"]["maintenance_purposes"])
    assert required == supported
    original = release_maintenance.apply_release
    changed = []

    def mutate():
        if not changed:
            record(coordinator.journal, "doctor.repair.apply")
            changed.append(True)

    def effect(core_review, effect_context, **kwargs):
        if when == "after_pin":
            mutate()
        if when == "before_current_cas":
            guard = effect_context.check_current

            def check(stage, write=False):
                if stage == "release.current_cas":
                    mutate()
                guard(stage, write)
            effect_context.check_current = check
        return original(core_review, effect_context, **kwargs)

    monkeypatch.setattr(release_maintenance, "apply_release", effect)
    operation = await coordinator.release_apply(params(review))
    if when == "awaiting_owner":
        mutate()
    await decision(coordinator, True)
    result = await finish(coordinator, operation)
    assert changed
    assert result["phase"] == "failed", result
    assert result["error_code"] == "release_profile_pin_changed", result
    assert not (target / "CURRENT").exists()
    assert result["receipt"]["purpose"] == "release.update.apply"
    assert not result["cleanup_owner_pending"] and service.profile_gate._cut is None
    assert peer.calls == 0


def launch_fixture(tmp_path):
    supported = _EFFECT_PURPOSES | {"backup.create"}
    source = declared_artifact(tmp_path / "limited-reader", supported)
    install = tmp_path / "installation"
    install.mkdir(mode=0o700)
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    (profile / "profile.lock").touch(mode=0o600)
    pin = replace(profile_pin(), root_identity=FileIdentity.of(profile.stat()),
                  schemas=(("state", 0), ("memory", 0), ("schedules", 0)),
                  maintenance_purposes=tuple(sorted(supported)))
    review = core.review_release(source, install, context(), pin)
    receipt = core.apply_release(review, context())
    exported = tmp_path / "pin.json"
    exported.write_bytes(canonical(pin.metadata()))
    exported.chmod(0o600)
    arguments = ["--install-root", str(install), "--profile-pin", str(exported),
                 "--expected-current-sha256", receipt.current_sha256,
                 "--python", os.path.realpath(sys.executable),
                 "--codex", os.path.realpath(sys.executable), "--state", str(profile), "--launch"]
    spec = importlib.util.spec_from_file_location(
        "jarvis_control_launch_fixture", _REPO / "script/launch_local_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, arguments, profile


def test_closed_launcher_rejects_actual_journal_purpose_missing_from_pin_and_reader(
    tmp_path, monkeypatch,
):
    module, arguments, profile = launch_fixture(tmp_path)
    record(MaintenanceJournal(profile), "doctor.repair.apply")

    def forbidden(*_args):
        pytest.fail("incompatible closed profile must never reach exec")

    monkeypatch.setattr(module.os, "execve", forbidden)
    before = (profile / "maintenance.json").read_bytes()
    with pytest.raises(core.ReleaseError, match="release_launch_maintenance_purposes_changed"):
        module.main(arguments)
    assert (profile / "maintenance.json").read_bytes() == before


def test_closed_launcher_rechecks_journal_identity_and_bytes_at_final_handoff(
    tmp_path, monkeypatch,
):
    module, arguments, profile = launch_fixture(tmp_path)
    journal = MaintenanceJournal(profile)
    record(journal, "backup.create")
    validate = module.validate_profile_for_launch
    changed = []

    def after_validation(*args, **kwargs):
        families = validate(*args, **kwargs)
        record(journal, "doctor.repair.apply")
        changed.append(True)
        return families

    def forbidden(*_args):
        pytest.fail("changed journal must never reach exec")

    monkeypatch.setattr(module, "validate_profile_for_launch", after_validation)
    monkeypatch.setattr(module.os, "execve", forbidden)
    with pytest.raises(core.ReleaseError, match="release_launch_journal_changed"):
        module.main(arguments)
    assert changed and (profile / "maintenance.json").is_file()
