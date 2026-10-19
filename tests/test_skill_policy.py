"""Synthetic owner choices protect complete skill bodies without granting execution."""

from __future__ import annotations

import json
import stat

import pytest

from aegis_core.contracts import AgentRole
from aegis_core.settings_store import write_private_json
from aegis_core.skills import SkillDraft, SkillError, SkillPolicyStore, SkillRegistry, SkillStore
from aegis_core.skills.contracts import SkillManifest, SkillOrigin
from aegis_core.skills.policy import MAX_SKILL_PREVIEW_BYTES, skill_content_sha256
from aegis_core.tools.defaults import build_default_tool_broker


def draft(skill_id="owner-procedure", **updates):
    values = {
        "skill_id": skill_id,
        "name": "Procedimiento de prueba",
        "description": "Procedimiento declarativo para pruebas sintéticas sin datos reales.",
        "role": AgentRole.PLANNER,
        "trigger_phrases": ("procedimiento atlas",),
        "instructions": ("Compara unidades y revisiones antes de presentar una conclusión.",),
        "resources": ("Referencia privada sintética ZEPHYR-147, sin autoridad de ejecución.",),
    }
    values.update(updates)
    return SkillDraft(**values)


def registry(tmp_path, **kwargs):
    return SkillRegistry(
        build_default_tool_broker(),
        SkillStore(tmp_path / "skills"),
        policy=SkillPolicyStore(tmp_path / "policy.json"),
        **kwargs,
    )


def approve(instance, manifest):
    instance.set_remote_consent(manifest.skill_id, skill_content_sha256(manifest), True)


def test_content_consent_is_closed_until_reviewed_and_survives_restart(tmp_path):
    instance = registry(tmp_path)
    manifest = instance.learn(draft())
    activation = instance.select("procedimiento atlas")
    assert activation is not None
    assert instance.remote_context((activation,)) == []
    preview = instance.preview(manifest.skill_id, skill_content_sha256(manifest))
    assert preview["instructions"] == list(manifest.instructions)
    assert preview["resources"] == list(manifest.resources)
    approve(instance, manifest)
    persisted = (tmp_path / "policy.json").read_text()
    assert manifest.resources[0] not in persisted
    assert stat.S_IMODE((tmp_path / "policy.json").stat().st_mode) == 0o600
    restarted = registry(tmp_path)
    assert restarted.remote_context((activation,))[0]["resources"] == list(manifest.resources)
    restarted.set_remote_consent(manifest.skill_id, skill_content_sha256(manifest), False)
    assert instance.remote_context((activation,)) == []


@pytest.mark.parametrize(
    "change",
    [
        {"instructions": ("Mantén cada cifra asociada a su revisión original verificable.",)},
        {"resources": ("La referencia revisada usa unidades diferentes y sigue siendo privada.",)},
        {"allowed_tools": frozenset({"web_research"})},
        {"role": AgentRole.CODE_SECURITY},
    ],
)
def test_every_content_update_revokes_old_consent_and_activation(tmp_path, change):
    instance = registry(tmp_path, builtins=())
    original = instance.learn(draft())
    activation = instance.select("procedimiento atlas")
    approve(instance, original)
    revised = instance.learn(draft(**change))
    assert skill_content_sha256(original) != skill_content_sha256(revised)
    assert instance.remote_context((activation,)) == []
    current = instance.select("procedimiento atlas")
    assert instance.remote_context((current,)) == []
    with pytest.raises(ValueError, match="content changed"):
        instance.set_remote_consent(revised.skill_id, skill_content_sha256(original), True)
    approve(instance, revised)
    assert instance.remote_context((current,))[0]["instructions"] == list(revised.instructions)


def test_plugin_version_changes_revoke_sharing_and_runtime_unavailability_is_live(tmp_path):
    base = SkillManifest(**draft().model_dump(), origin=SkillOrigin.PLUGIN, source_version="1.0")
    available = {"value": True}
    first = registry(
        tmp_path,
        builtins=(),
        plugin_skills=(base,),
        plugin_available=lambda _skill_id: available["value"],
    )
    approve(first, base)
    active = first.select("procedimiento atlas")
    assert first.remote_context((active,))
    second = registry(
        tmp_path, builtins=(), plugin_skills=(base.model_copy(update={"source_version": "2.0"}),)
    )
    assert not second.policy.remote_allowed(second.all()[0])
    available["value"] = False
    assert first.select("procedimiento atlas") is None
    assert first.remote_context((active,)) == []
    assert first.semantic_catalogue() == []


def test_explicit_enable_overrides_source_default_but_not_adaptation_required(tmp_path):
    instance = registry(tmp_path, builtins=())
    disabled = instance.learn(draft(enabled=False))
    digest = skill_content_sha256(disabled)
    assert instance.select("procedimiento atlas") is None
    approve(instance, disabled)
    assert instance.remote_context(()) == []
    instance.set_enabled(disabled.skill_id, digest, True)
    assert instance.select("procedimiento atlas") is not None
    assert instance.policy.remote_allowed(disabled)
    adapted = instance.learn(draft(enabled=False, adaptation_required=True))
    with pytest.raises(SkillError, match="adaptation"):
        instance.set_enabled(adapted.skill_id, skill_content_sha256(adapted), True)
    assert instance.select("procedimiento atlas") is None
    assert "adaptation_required" not in disabled.model_dump()
    assert adapted.model_dump()["adaptation_required"] is True


@pytest.mark.parametrize("failure", ["invalid_json", "public_permissions", "symlink", "deep_json"])
def test_invalid_private_policy_fails_closed_and_cannot_be_overwritten(tmp_path, failure):
    instance = registry(tmp_path)
    manifest = instance.learn(draft())
    approve(instance, manifest)
    path = tmp_path / "policy.json"
    if failure == "invalid_json":
        path.write_text("{broken", encoding="utf-8")
    elif failure == "public_permissions":
        path.chmod(0o644)
    elif failure == "deep_json":
        path.write_text('{"choices":' + "[" * 1000 + "0" + "]" * 1000 + "}")
    else:
        target = tmp_path / "target.json"
        path.rename(target)
        path.symlink_to(target)
    assert instance.policy.valid is False
    assert instance.select("procedimiento atlas") is None
    assert instance.semantic_catalogue() == []
    with pytest.raises(ValueError, match="policy is invalid"):
        instance.set_enabled(manifest.skill_id, skill_content_sha256(manifest), True)


def test_builtin_public_default_and_explicit_owner_denial_are_persistent(tmp_path):
    instance = registry(tmp_path)
    selected = instance.select("Controla mi computadora y usa Safari")
    assert selected is not None
    assert instance.remote_context((selected,))
    manifest = selected.manifest
    instance.set_remote_consent(manifest.skill_id, skill_content_sha256(manifest), False)
    assert registry(tmp_path).remote_context((selected,)) == []
    instance.set_enabled(manifest.skill_id, skill_content_sha256(manifest), False)
    assert registry(tmp_path).select("Controla mi computadora y usa Safari") is None


def test_preview_and_multiple_guidance_never_truncate_a_body_or_exceed_budget(tmp_path):
    instance = registry(tmp_path, builtins=())
    activations = []
    for index in range(3):
        manifest = instance.learn(draft(f"body-{index}", resources=(str(index) + "á" * 2450,)))
        preview = instance.preview(manifest.skill_id, skill_content_sha256(manifest))
        assert len(json.dumps(preview, ensure_ascii=False).encode()) <= MAX_SKILL_PREVIEW_BYTES
        assert preview["resources"] == list(manifest.resources)
        approve(instance, manifest)
        activations.append(
            next(
                item
                for item in instance.select_many("procedimiento atlas")
                if item.manifest.skill_id == manifest.skill_id
            )
        )
    contexts = instance.remote_context(activations)
    assert 1 <= len(contexts) < 3
    assert len(json.dumps(contexts, ensure_ascii=False).encode()) <= MAX_SKILL_PREVIEW_BYTES
    by_id = {item.manifest.skill_id: item.manifest.resources for item in activations}
    assert all(item["resources"] == list(by_id[item["skill_id"]]) for item in contexts)
    large = instance.learn(
        draft(
            "body-large", resources=("x" * 8192,), instructions=tuple("I" * 399 for _ in range(12))
        )
    )
    assert not instance.describe_manifest(large)["preview_available"]
    with pytest.raises(SkillError, match="preview"):
        instance.set_remote_consent(large.skill_id, skill_content_sha256(large), True)
    assert not instance.policy.remote_allowed(large)


def test_fingerprint_is_stable_across_unordered_sets_and_covers_origin(tmp_path):
    first = SkillManifest(
        **draft(allowed_tools=frozenset({"web_fetch", "web_research"})).model_dump(),
        origin=SkillOrigin.LEARNED,
    )
    second = first.model_copy(update={"allowed_tools": frozenset(["web_research", "web_fetch"])})
    assert skill_content_sha256(first) == skill_content_sha256(second)
    assert skill_content_sha256(first) != skill_content_sha256(
        first.model_copy(update={"origin": SkillOrigin.PLUGIN})
    )


def test_in_place_source_edit_revalidates_hash_without_restart(tmp_path):
    instance = registry(tmp_path, builtins=())
    original = instance.learn(draft())
    approve(instance, original)
    active = instance.select("procedimiento atlas")
    revised = draft(resources=("Referencia privada revisada ZEPHYR-148, no aprobada.",))
    path = instance.store.directory / f"{original.skill_id}.json"
    before = instance.store.directory.stat().st_mtime_ns
    path.write_text(revised.model_dump_json(), encoding="utf-8")
    assert instance.store.directory.stat().st_mtime_ns == before
    current = instance.all()[0]
    assert current.resources == revised.resources
    assert not instance.policy.remote_allowed(current)
    assert instance.remote_context((active,)) == []


def test_approved_multi_skill_selection_fits_complete_guidance_budget(tmp_path):
    instance = registry(tmp_path, builtins=())
    for index in range(3):
        manifest = instance.learn(draft(f"big-{index}", resources=(str(index) + "x" * 7000,)))
        approve(instance, manifest)
    active = instance.select_many("procedimiento atlas")
    contexts = instance.remote_context(active)
    assert len(active) == len(contexts) == 1
    with pytest.raises(SkillError, match="budget"):
        instance.resolve_semantic(["big-0", "big-1"], instance.semantic_catalogue())


def test_public_only_context_cannot_forward_preselected_private_activation(tmp_path):
    instance = registry(tmp_path, builtins=())
    manifest = instance.learn(draft())
    approve(instance, manifest)
    active = instance.select_many("procedimiento atlas")
    assert instance.remote_context(active)
    assert instance.remote_context(active, public_only=True) == []
    assert instance.select_many("procedimiento atlas", public_only=True) == ()
    assert instance.semantic_catalogue(public_only=True) == []
    with pytest.raises(SkillError, match="unapproved"):
        instance.resolve_semantic(
            [manifest.skill_id], instance.semantic_catalogue(), public_only=True
        )


def test_consent_can_be_revoked_while_disabled_and_does_not_reappear_on_enable(tmp_path):
    instance = registry(tmp_path, builtins=())
    manifest = instance.learn(draft())
    digest = skill_content_sha256(manifest)
    approve(instance, manifest)
    instance.set_enabled(manifest.skill_id, digest, False)
    record = instance.catalogue()[0]
    assert record["remote_authorized"] and not record["remote_allowed"]
    revoked = instance.set_remote_consent(manifest.skill_id, digest, False)
    assert not revoked["remote_authorized"] and not revoked["remote_allowed"]
    instance.set_enabled(manifest.skill_id, digest, True)
    restarted = registry(tmp_path, builtins=())
    assert restarted.catalogue()[0]["enabled"]
    assert not restarted.catalogue()[0]["remote_authorized"]
    assert restarted.remote_context(restarted.select_many("procedimiento atlas")) == []


def test_policy_preflight_checks_capacity_without_writing_and_allows_existing_choice(tmp_path):
    path = tmp_path / "policy.json"
    policy = SkillPolicyStore(path)
    policy.preflight_choice("new-procedure")
    assert not path.exists()
    choices = {f"choice-{index}": {"enabled": False} for index in range(512)}
    write_private_json(path, {"schema_version": 1, "choices": choices})
    before = path.read_bytes()
    policy.preflight_choice("choice-0")
    with pytest.raises(ValueError, match="capacity"):
        policy.preflight_choice("new-procedure")
    assert path.read_bytes() == before


@pytest.mark.parametrize("failure", ["corrupt", "symlink_parent", "symlink_loop"])
def test_preflight_rejects_invalid_or_aliased_private_location_before_import(tmp_path, failure):
    path = tmp_path / "policy.json"
    if failure == "corrupt":
        write_private_json(path, {"schema_version": 1, "unexpected": True})
    elif failure == "symlink_parent":
        parent = tmp_path / "actual"
        parent.mkdir()
        alias = tmp_path / "alias"
        alias.symlink_to(parent, target_is_directory=True)
        path = alias / "policy.json"
    else:
        alias = tmp_path / "loop"
        alias.symlink_to(alias, target_is_directory=True)
        path = alias / "policy.json"
    policy = SkillPolicyStore(path)
    with pytest.raises(ValueError, match="policy is invalid"):
        policy.preflight_choice("new-procedure")
    assert not policy.valid
