"""Versioned skill data, explicit argument invocation and snapshotted resources."""

import base64
import hashlib
import json
import os

import pytest

from jarvis_core.skills import SkillError, SkillsStore, skill_metadata


def text(version="1.0.0", *, invocable=True):
    return (
        "---\nname: Ejemplo\ndescription: Skill propia de prueba\nversion: " + version
        + '\nresources: ["references/data.txt"]\nargument-hint: "tema"\nuser-invocable: '
        + ("true" if invocable else "false")
        + '\narguments: [{"name":"topic","type":"string","required":true,'
        '"description":"Tema de la respuesta"}]\nmetadata: {"license":"MIT","tags":["prueba"]}'
        "\n---\nUsa un ejemplo para explicar el tema.\n"
    )


@pytest.fixture
def context(tmp_path):
    store = SkillsStore(tmp_path.resolve() / "profile")
    source = tmp_path.resolve() / "source"
    (source / "references").mkdir(parents=True, mode=0o700)
    (source / "SKILL.md").write_text(text())
    (source / "SKILL.md").chmod(0o600)
    (source / "references/data.txt").write_text("RECURSO SINTÉTICO")
    (source / "references/data.txt").chmod(0o600)
    return store, source


def install(store, source, *, library=False):
    preview = store.inspect_directory(source)
    if library:
        archive = store.inspect_library(source)
        return store.import_library("jarvis", source, expected_sha256=preview.sha256,
                                    expected_library_sha256=archive.sha256)
    return store.import_directory("jarvis", source, expected_sha256=preview.sha256)


def test_metadata_arguments_and_explicit_invocation_are_data_not_execution(context):
    store, source = context
    row = install(store, source)
    metadata = store.metadata("jarvis", row.skill_id)
    assert metadata["version"] == "1.0.0" and metadata["user_invocable"]
    assert metadata["metadata"]["license"] == "MIT"
    assert store.context("jarvis").text == ""
    invocation = store.invoke("jarvis", row.skill_id, {"topic": "área $(never execute)"},
                               expected_revision=row.revision, expected_sha256=row.sha256)
    assert invocation["executed_code"] is False
    assert "$(never execute)" in invocation["context"]
    assert invocation["text"].startswith("Usa un ejemplo")
    assert "nunca permisos" in invocation["context"]
    assert not list(source.glob("*.sh"))


@pytest.mark.parametrize("arguments", [{}, {"topic": 42}, {"topic": "ok", "extra": "no"},
                                      {"topic": "a" * 2049}, [], None])
def test_invalid_arguments_never_create_a_model_request_or_effect(context, arguments):
    store, source = context
    row = install(store, source)
    before = store.document.path.read_bytes()
    with pytest.raises(SkillError, match="skill_"):
        store.invoke("jarvis", row.skill_id, arguments,
                      expected_revision=row.revision, expected_sha256=row.sha256)
    assert store.document.path.read_bytes() == before


def test_library_import_is_explicit_hash_bound_and_survives_original_changes(context):
    store, source = context
    plain = install(store, source)
    with pytest.raises(SkillError, match="skill_resource_not_imported"):
        store.resource("jarvis", plain.skill_id, "references/data.txt",
                        expected_revision=plain.revision, expected_sha256=plain.sha256)
    store.remove("jarvis", plain.skill_id, expected_revision=plain.revision,
                  expected_sha256=plain.sha256)
    preview = store.inspect_library(source)
    assert preview.public()["files"][0]["bytes"] == len("RECURSO SINTÉTICO".encode())
    row = install(store, source, library=True)
    (source / "references/data.txt").write_text("AHORA OTRO RECURSO")
    resource = store.resource("jarvis", row.skill_id, "references/data.txt",
                              expected_revision=row.revision, expected_sha256=row.sha256)
    assert base64.b64decode(resource["content_base64"]).decode() == "RECURSO SINTÉTICO"
    assert resource["sha256"] == hashlib.sha256("RECURSO SINTÉTICO".encode()).hexdigest()
    with pytest.raises(SkillError, match="skill_source_changed"):
        store.import_library("other", source, expected_sha256=row.sha256,
                              expected_library_sha256=preview.sha256)


@pytest.mark.parametrize("alias", ["symlink", "hardlink", "fifo", "ancestor"])
def test_library_rejects_unsafe_resource_identity_before_import(context, tmp_path, alias):
    store, source = context
    resource = source / "references/data.txt"
    outside = tmp_path / "outside.txt"
    outside.write_text("PROPIO PERO FUERA DEL RECURSO")
    if alias == "symlink":
        resource.unlink()
        resource.symlink_to(outside)
    elif alias == "hardlink":
        os.link(resource, source / "linked.txt")
    elif alias == "fifo":
        resource.unlink()
        os.mkfifo(resource)
    else:
        resource.unlink()
        resource.parent.rmdir()
        resource.parent.symlink_to(tmp_path)
    with pytest.raises(SkillError):
        store.inspect_library(source)
    assert not store.document.path.exists()


def test_update_rollback_retire_and_restart_keep_exact_resource_versions(context):
    store, source = context
    first = install(store, source, library=True)
    row = first
    for major in (2, 3, 4):
        row = store.update("jarvis", row.skill_id, text=text(f"{major}.0.0"),
                            expected_revision=row.revision, expected_sha256=row.sha256)
    assert len(store.versions("jarvis", row.skill_id)) == 4
    with pytest.raises(SkillError, match="skill_history_limit"):
        store.update("jarvis", row.skill_id, text=text("5.0.0"),
                      expected_revision=row.revision, expected_sha256=row.sha256)
    row = store.retire_version("jarvis", row.skill_id, target_revision=2,
                                expected_revision=row.revision, expected_sha256=row.sha256)
    restored = store.rollback("jarvis", row.skill_id, target_revision=first.revision,
                              expected_revision=row.revision, expected_sha256=row.sha256)
    assert restored.sha256 == first.sha256 and not restored.enabled
    assert restored.revision > row.revision
    restarted = SkillsStore(store.document.directory)
    assert restarted.metadata("jarvis", row.skill_id)["version"] == "1.0.0"
    assert restarted.resource("jarvis", row.skill_id, "references/data.txt",
                               expected_revision=restored.revision,
                               expected_sha256=restored.sha256)["content_base64"]


def test_enabled_library_read_keeps_imported_resource_after_source_changes_and_restart(context):
    store, source = context
    imported = install(store, source, library=True)
    original = "RECURSO SINTÉTICO".encode()
    (source / "references/data.txt").write_bytes(b"DISTINTO DEL SNAPSHOT")
    current = imported
    for enabled in (True, False):
        current = store.set_enabled("jarvis", current.skill_id, enabled,
            expected_revision=current.revision, expected_sha256=current.sha256)
        store = SkillsStore(store.document.directory)
        resource = store.resource("jarvis", current.skill_id, "references/data.txt",
            expected_revision=current.revision, expected_sha256=current.sha256)
        assert base64.b64decode(resource["content_base64"]) == original
        assert resource["bytes"] == len(original)
        assert resource["sha256"] == hashlib.sha256(original).hexdigest()
    with pytest.raises(SkillError, match="skill_revision_conflict"):
        store.resource("jarvis", imported.skill_id, "references/data.txt",
            expected_revision=imported.revision, expected_sha256=imported.sha256)


def test_final_publication_guard_and_stale_revision_preserve_original(context):
    store, source = context
    row = install(store, source)
    original = store.document.path.read_bytes()
    calls = 0

    def revoke():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SkillError("skill_revoked")

    with pytest.raises(SkillError, match="skill_revoked"):
        store.update("jarvis", row.skill_id, text=text("2.0.0"),
                      expected_revision=row.revision, expected_sha256=row.sha256,
                      assert_current=revoke)
    assert store.document.path.read_bytes() == original
    with pytest.raises(SkillError, match="skill_revision_conflict"):
        store.invoke("jarvis", row.skill_id, {"topic": "ok"},
                      expected_revision=2, expected_sha256=row.sha256)


def test_non_user_invocable_skill_and_corrupt_history_fail_closed(context):
    store, source = context
    (source / "SKILL.md").write_text(text(invocable=False))
    row = install(store, source)
    with pytest.raises(SkillError, match="skill_not_user_invocable"):
        store.invoke("jarvis", row.skill_id, {"topic": "ok"},
                      expected_revision=row.revision, expected_sha256=row.sha256)
    row = store.update("jarvis", row.skill_id, text=text("2.0.0"),
                        expected_revision=row.revision, expected_sha256=row.sha256)
    value = json.loads(store.document.path.read_text())
    value["skills"][row.skill_id]["history"][0]["sha256"] = "0" * 64
    store.document.path.write_text(json.dumps(value))
    with pytest.raises(SkillError, match="skill_history_invalid"):
        store.versions("jarvis", row.skill_id)


@pytest.mark.parametrize("line", ["version: nope", "user-invocable: yes",
                                  'arguments: [{"name":"x","type":"shell"}]',
                                  'metadata: {"permissions":"all"}'])
def test_extended_frontmatter_remains_strict(line):
    with pytest.raises(SkillError):
        skill_metadata("---\nname: Ejemplo\ndescription: Prueba\n" + line + "\n---\nTexto\n")
