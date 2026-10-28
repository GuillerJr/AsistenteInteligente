"""Backup/restore of extension bytes remains disarmed and legacy compatible."""

import base64
import json
import threading
from dataclasses import replace

import pytest
from test_host_profile import service as service
from test_plugins import change, install, package
from test_recovery import ctx, target
from test_skill_versions import text

from jarvis_core.activation import ActivationError, review_prepared_profile
from jarvis_core.doctor import DoctorContext, DoctorInventory, inspect_report
from jarvis_core.host_profile import freeze_sources
from jarvis_core.plugins import PluginsStore
from jarvis_core.profiles import ProfilesStore
from jarvis_core.recovery import (
    JsonSource,
    SourceBundle,
    canonical,
    create_backup,
    restore_new,
    verify_backup,
)
from jarvis_core.recovery_fs import FileIdentity
from jarvis_core.skills import SkillsStore


def bundle(tmp_path):
    root = tmp_path / "original"
    root.mkdir(mode=0o700)
    agents = ProfilesStore(root / "profiles")
    skills = SkillsStore(root / "profiles")
    extensions = PluginsStore(root / "profiles")
    row = install(extensions, package(tmp_path))
    change(extensions, row, "enable")
    source = tmp_path / "skill"
    (source / "references").mkdir(parents=True, mode=0o700)
    (source / "SKILL.md").write_text(text())
    (source / "references/data.txt").write_text("SYNTHETIC VERSIONED RESOURCE")
    library = skills.inspect_library(source)
    skill = skills.import_library("jarvis", source, expected_sha256=library.manifest.sha256,
                                  expected_library_sha256=library.sha256, enabled=True)
    skills.update("jarvis", skill.skill_id, text=text("2.0.0"),
                   expected_revision=skill.revision, expected_sha256=skill.sha256)
    documents = tuple(JsonSource(role, canonical(store.document.read()), lambda: None)
                      for role, store in (("agents", agents), ("skills", skills),
                                          ("plugins", extensions)))
    return SourceBundle("fixture_profile", 1, root, FileIdentity.of(root.stat()), (), documents,
                        ("state", "memory", "schedules", "telegram"))


def test_plugin_and_skill_resource_history_roundtrip_is_exact_and_disarmed(tmp_path):
    source = bundle(tmp_path)
    original = {row.role: row.data for row in source.json}
    receipt = create_backup(source, target(tmp_path), ctx())
    assert receipt.status == "completed"
    checked = verify_backup(tmp_path / "backup", ctx())
    restore_new(tmp_path / "backup", checked, target(tmp_path, "restored"), ctx())
    root = tmp_path / "restored"
    restored = PluginsStore(root / "profiles")
    skill_store = SkillsStore(root / "profiles")
    row = restored.list("jarvis")[0]
    assert not row.enabled and restored.context("jarvis")["text"] == ""
    assert restored.contributions("jarvis")["plugins"] == []
    skill = skill_store.list("jarvis")[0]
    assert not skill.enabled and len(skill_store.versions("jarvis", skill.skill_id)) == 2
    resource = skill_store.resource("jarvis", skill.skill_id, "references/data.txt",
                                    expected_revision=skill.revision, expected_sha256=skill.sha256)
    assert base64.b64decode(resource["content_base64"]) == b"SYNTHETIC VERSIONED RESOURCE"
    assert json.loads(original["plugins"])["plugins"][row.plugin_id]["enabled"]
    assert (source.root / "profiles/plugins.json").read_bytes() == original["plugins"]
    activation = review_prepared_profile(root, ctx())
    assert any(item.role == "plugins" and item.present for item in activation.resources)


def test_activation_rejects_a_restored_plugin_that_was_reenabled(tmp_path):
    source = bundle(tmp_path)
    create_backup(source, target(tmp_path), ctx())
    checked = verify_backup(tmp_path / "backup", ctx())
    restore_new(tmp_path / "backup", checked, target(tmp_path, "restored"), ctx())
    path = tmp_path / "restored/profiles/plugins.json"
    value = json.loads(path.read_bytes())
    next(iter(value["plugins"].values()))["enabled"] = True
    path.write_bytes(canonical(value))
    with pytest.raises(ActivationError, match="activation_profile_not_disarmed"):
        review_prepared_profile(tmp_path / "restored", ctx())


def test_legacy_backup_without_any_plugin_role_remains_readable(tmp_path):
    source = bundle(tmp_path)
    legacy = replace(source, json=tuple(row for row in source.json if row.role != "plugins"))
    create_backup(legacy, target(tmp_path), ctx())
    manifest = json.loads((tmp_path / "backup/manifest.json").read_bytes())
    assert "plugins" not in manifest["absent"]
    assert all(row["role"] != "plugins" for row in manifest["resources"])
    checked = verify_backup(tmp_path / "backup", ctx())
    restore_new(tmp_path / "backup", checked, target(tmp_path, "legacy"), ctx())
    assert not (tmp_path / "legacy/profiles/plugins.json").exists()


async def test_real_host_cut_captures_plugins_and_doctor_checks_the_same_role(service, tmp_path):
    service.agent_profiles.update("jarvis", expected_revision=1, name="Jarvis de prueba")
    row = install(service.plugins, package(tmp_path))
    change(service.plugins, row, "enable")
    with service.profile_gate.exclusive() as cut:
        frozen = freeze_sources(service, cut)
        captured = frozen.bind(cut)
        plugin = next(item for item in captured.json if item.role == "plugins")
        assert row.plugin_id in json.loads(plugin.data)["plugins"]
    inventory = DoctorInventory("fixture_profile", 1, service.directory,
                                 (service.profile_lease.identity.device,
                                  service.profile_lease.identity.inode),
                                 "coherent_snapshot", roles=("agents", "plugins"))
    report = inspect_report(inventory, DoctorContext(lambda *args, **kwargs: None,
                                                     threading.Event()))
    check = next(item for item in report.checks if item.role == "plugins")
    assert check.status == "ok" and dict(check.counts)["enabled_plugins"] == 1
