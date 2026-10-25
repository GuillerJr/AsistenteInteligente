"""Local skill snapshots with disposable manifests; never run a skill or script."""

import hashlib
import json
import os
import stat

import pytest

from jarvis_core.private_json import PrivateJsonError
from jarvis_core.skills import SkillError, SkillsStore


def manifest(
    body="Escribe una respuesta clara.\n",
    *,
    name="Ejemplo",
    description="Prueba local",
    resources=None,
):
    resource_line = "" if resources is None else "resources: " + json.dumps(resources) + "\n"
    return f"---\nname: {name}\ndescription: {description}\n{resource_line}---\n{body}"


@pytest.fixture
def store(tmp_path):
    return SkillsStore(tmp_path.resolve() / "private")


@pytest.fixture
def source(tmp_path):
    directory = tmp_path.resolve() / "source"
    directory.mkdir(mode=0o700)
    (directory / "SKILL.md").write_text(manifest())
    (directory / "SKILL.md").chmod(0o600)
    return directory


def imported(store, source, *, agent="jarvis", enabled=False):
    preview = store.inspect_directory(source)
    return store.import_directory(agent, source, expected_sha256=preview.sha256, enabled=enabled)


def test_preview_import_is_disabled_private_snapshot_and_survives_source_removal(store, source):
    preview = store.inspect_directory(source)
    raw = (source / "SKILL.md").read_bytes()
    assert preview.sha256 == hashlib.sha256(raw).hexdigest()
    assert preview.name == "Ejemplo" and preview.body == "Escribe una respuesta clara.\n"
    assert not store.document.path.exists()
    row = imported(store, source)
    assert not row.enabled and row.revision == 1 and not row.source_verified
    assert row.provenance == "imported_snapshot" and row.content_bytes == len(raw)
    assert stat.S_IMODE(store.document.path.stat().st_mode) == 0o600
    assert store.context("jarvis").text == "" and not store.context("jarvis").truncated
    (source / "SKILL.md").unlink()
    content = SkillsStore(store.document.directory).get("jarvis", row.skill_id)
    assert content.text.encode() == raw and content.record == row


def test_enabled_context_contains_bounded_provenance_without_absolute_source_path(store, source):
    row = imported(store, source)
    before = store.context("jarvis")
    enabled = store.set_enabled(
        "jarvis", row.skill_id, True, expected_revision=1, expected_sha256=row.sha256
    )
    assert enabled.enabled and enabled.revision == 2
    context = store.context("jarvis")
    assert context.fingerprint != before.fingerprint
    assert context.records == (enabled,) and not context.truncated
    assert enabled.sha256 in context.text and enabled.skill_id in context.text
    assert "Escribe una respuesta clara." in context.text
    assert "no permisos" in context.text and str(source) not in context.text
    assert store.context("other_agent").text == ""
    assert (
        store.set_enabled(
            "jarvis", row.skill_id, True, expected_revision=2, expected_sha256=row.sha256
        )
        == enabled
    )


def test_update_is_cas_snapshot_only_with_edited_provenance(store, source):
    row = imported(store, source, enabled=True)
    original = (source / "SKILL.md").read_text()
    edited = manifest("Ahora incluye ejemplos.\n", name="Editada")
    changed = store.update(
        "jarvis", row.skill_id, text=edited, expected_revision=1, expected_sha256=row.sha256
    )
    assert changed.name == "Editada" and changed.revision == 2 and changed.enabled
    assert changed.sha256 == hashlib.sha256(edited.encode()).hexdigest()
    assert changed.provenance == "edited_snapshot" and not changed.source_verified
    assert (source / "SKILL.md").read_text() == original
    assert store.get("jarvis", row.skill_id).text == edited
    assert "Ahora incluye ejemplos." in store.context("jarvis").text
    assert (
        store.update(
            "jarvis", row.skill_id, text=edited, expected_revision=2, expected_sha256=changed.sha256
        )
        == changed
    )
    for method in (
        lambda: store.update(
            "jarvis", row.skill_id, text=original, expected_revision=1, expected_sha256=row.sha256
        ),
        lambda: store.set_enabled(
            "jarvis", row.skill_id, False, expected_revision=1, expected_sha256=row.sha256
        ),
        lambda: store.remove(
            "jarvis", row.skill_id, expected_revision=1, expected_sha256=row.sha256
        ),
        lambda: store.remove(
            "jarvis", row.skill_id, expected_revision=2, expected_sha256=row.sha256
        ),
    ):
        with pytest.raises(SkillError, match="skill_revision_conflict"):
            method()
    assert store.get("jarvis", row.skill_id).record == changed
    store.remove("jarvis", row.skill_id, expected_revision=2, expected_sha256=changed.sha256)
    assert store.list("jarvis") == () and store.context("jarvis").text == ""


def test_agent_scope_is_checked_before_all_mutations(store, source):
    row = imported(store, source, agent="first_agent", enabled=True)
    assert store.list("jarvis") == () and store.list("second_agent") == ()
    for method in (
        lambda: store.get("second_agent", row.skill_id),
        lambda: store.set_enabled(
            "second_agent", row.skill_id, False, expected_revision=1, expected_sha256=row.sha256
        ),
        lambda: store.update(
            "second_agent",
            row.skill_id,
            text=manifest("Mal\n"),
            expected_revision=1,
            expected_sha256=row.sha256,
        ),
        lambda: store.remove(
            "second_agent", row.skill_id, expected_revision=1, expected_sha256=row.sha256
        ),
    ):
        with pytest.raises(SkillError, match="skill_not_found"):
            method()
    assert store.get("first_agent", row.skill_id).record == row


def test_resources_are_only_declared_metadata_and_never_opened(store, source, monkeypatch):
    resources = ["references/missing.md", "scripts/never_run.py"]
    (source / "SKILL.md").write_text(manifest(resources=resources))
    original = os.open
    opened = []

    def guarded(path, flags, *args, **kwargs):
        opened.append(str(path))
        assert "missing.md" not in str(path) and "never_run.py" not in str(path)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", guarded)
    row = imported(store, source, enabled=True)
    assert row.resources == tuple(resources)
    assert "SKILL.md" in opened
    assert "scripts/never_run.py" in store.context("jarvis").text
    assert not (source / "scripts").exists()


def test_duplicate_content_is_scoped_and_update_cannot_duplicate_another_skill(store, source):
    one = imported(store, source)
    with pytest.raises(SkillError, match="skill_already_imported"):
        imported(store, source)
    assert imported(store, source, agent="other").sha256 == one.sha256
    (source / "SKILL.md").write_text(manifest("Segundo\n"))
    two = imported(store, source)
    with pytest.raises(SkillError, match="skill_already_imported"):
        store.update(
            "jarvis",
            two.skill_id,
            text=store.get("jarvis", one.skill_id).text,
            expected_revision=1,
            expected_sha256=two.sha256,
        )
    assert len(store.list("jarvis")) == 2


def test_source_changed_after_preview_is_not_imported(store, source):
    preview = store.inspect_directory(source)
    (source / "SKILL.md").write_text(manifest("Cambio posterior\n"))
    with pytest.raises(SkillError, match="skill_source_changed"):
        store.import_directory("jarvis", source, expected_sha256=preview.sha256)
    assert store.list("jarvis") == ()


@pytest.mark.parametrize(
    "kind",
    [
        "manifest_symlink",
        "manifest_hardlink",
        "manifest_fifo",
        "manifest_public_write",
        "directory_symlink",
        "ancestor_symlink",
        "directory_public_write",
    ],
)
def test_unsafe_sources_are_rejected_before_import(store, source, tmp_path, kind):
    outside = tmp_path.resolve() / "outside.md"
    outside.write_text(manifest("No leer por alias\n"))
    outside.chmod(0o600)
    path = source / "SKILL.md"
    if kind == "manifest_symlink":
        path.unlink()
        path.symlink_to(outside)
    elif kind == "manifest_hardlink":
        path.unlink()
        os.link(outside, path)
    elif kind == "manifest_fifo":
        path.unlink()
        os.mkfifo(path)
    elif kind == "manifest_public_write":
        path.chmod(0o666)
    elif kind in {"directory_symlink", "ancestor_symlink"}:
        link = tmp_path.resolve() / "link"
        link.symlink_to(
            source if kind == "directory_symlink" else source.parent, target_is_directory=True
        )
        source = link if kind == "directory_symlink" else link / source.name
    else:
        source.chmod(0o777)
    with pytest.raises(SkillError):
        store.inspect_directory(source)
    assert store.list("jarvis") == ()


@pytest.mark.parametrize(
    "text",
    [
        "Sin cabecera",
        "---\nname: Prueba\n---\nTexto\n",
        "---\nname: Prueba\nname: Duplicada\ndescription: Texto\n---\nTexto\n",
        "---\nname: Prueba\ndescription: Texto\nallowed-tools: all\n---\nTexto\n",
        "---\nname: Prueba\ndescription: |\n  Multilínea\n---\nTexto\n",
        "---\nname: Prueba\ndescription: *alias\n---\nTexto\n",
        "---\nname: Prueba\ndescription: Texto\n---\n   \n",
        "---\nname: Prueba\ndescription: Texto\n" + "# c\n" * 150 + "---\nTexto\n",
        manifest("NUL\0Texto"),
        manifest("\ud800"),
        manifest("x" * 32769),
    ],
)
def test_unsupported_or_unbounded_manifest_is_not_accepted(store, source, text):
    path = source / "SKILL.md"
    if "\ud800" in text:
        path.write_bytes(b"\xff\xfe")
    else:
        path.write_text(text)
    with pytest.raises(SkillError):
        store.inspect_directory(source)
    assert not store.document.path.exists()


@pytest.mark.parametrize(
    "resource",
    [
        "/absolute",
        "../outside",
        "a/../../outside",
        "a//b",
        "a/./b",
        "C:/owner",
        "https://example.com/a",
        "a\\b",
        "auth.json",
        "references/.env",
        ".ssh/key",
        "a\nb",
        123,
    ],
)
def test_resource_metadata_cannot_suggest_arbitrary_paths(store, source, resource):
    (source / "SKILL.md").write_text(manifest(resources=[resource]))
    with pytest.raises(SkillError):
        store.inspect_directory(source)


def test_scalar_quotes_crlf_and_exact_hash_are_preserved(store, source):
    text = manifest(name='"Habilidad con ñ"', description="'Una ''prueba'' local'")
    raw = text.replace("\n", "\r\n").encode()
    (source / "SKILL.md").write_bytes(raw)
    preview = store.inspect_directory(source)
    assert preview.name == "Habilidad con ñ" and preview.description == "Una 'prueba' local"
    assert preview.sha256 == hashlib.sha256(raw).hexdigest()
    row = imported(store, source)
    assert store.get("jarvis", row.skill_id).text.encode() == raw


def test_context_budget_skips_whole_snapshot_and_fingerprint_tracks_omissions(store, source):
    (source / "SKILL.md").write_text(manifest("x" * 2000))
    large = imported(store, source, enabled=True)
    small_context = store.context("jarvis", max_bytes=256)
    assert small_context.truncated and small_context.records == ()
    assert small_context.omitted_skill_ids == (large.skill_id,)
    assert len(small_context.text.encode()) <= 256
    full = store.context("jarvis", max_bytes=4096)
    assert full.records == (large,) and not full.truncated
    assert full.fingerprint != small_context.fingerprint
    changed = store.update(
        "jarvis",
        large.skill_id,
        text=manifest("z" * 2000),
        expected_revision=1,
        expected_sha256=large.sha256,
    )
    assert store.context("jarvis", max_bytes=256).fingerprint != small_context.fingerprint
    assert changed.enabled


@pytest.mark.parametrize("budget", [True, 0, 255, 32769, "16000", None])
def test_context_budget_requires_bounded_integer(store, budget):
    with pytest.raises(SkillError, match="skill_context_budget_invalid"):
        store.context("jarvis", max_bytes=budget)


def test_multiple_store_instances_merge_without_lost_skills(store, source):
    second = SkillsStore(store.document.directory)
    one = imported(store, source, agent="one")
    (source / "SKILL.md").write_text(manifest("Segundo\n"))
    two = imported(second, source, agent="two")
    second.set_enabled("one", one.skill_id, True, expected_revision=1, expected_sha256=one.sha256)
    assert store.get("two", two.skill_id).record == two
    assert store.get("one", one.skill_id).record.enabled


def test_content_tampering_cannot_silently_change_loaded_context(store, source):
    row = imported(store, source, enabled=True)
    value = json.loads(store.document.path.read_text())
    value["skills"][row.skill_id]["text"] = manifest("Inserción no validada\n")
    store.document.path.write_text(json.dumps(value))
    with pytest.raises(SkillError, match="skill_content_corrupt"):
        store.context("jarvis")
    with pytest.raises(SkillError):
        store.get("jarvis", row.skill_id)


@pytest.mark.parametrize(
    "field,value",
    [
        ("enabled", "true"),
        ("revision", True),
        ("provenance", []),
        ("source_identity", [True, 1]),
        ("source_path", "../bad"),
        ("agent_id", "../bad"),
    ],
)
def test_corrupt_metadata_fails_closed(store, source, field, value):
    row = imported(store, source, enabled=True)
    data = json.loads(store.document.path.read_text())
    data["skills"][row.skill_id][field] = value
    store.document.path.write_text(json.dumps(data))
    with pytest.raises((SkillError, PrivateJsonError)):
        store.list("jarvis")


def test_skill_capacity_is_per_agent_and_does_not_evict_existing_data(store, source):
    for index in range(32):
        (source / "SKILL.md").write_text(manifest(f"Nota {index}\n"))
        imported(store, source)
    previous = store.document.path.read_bytes()
    (source / "SKILL.md").write_text(manifest("Otra\n"))
    with pytest.raises(SkillError, match="skill_agent_limit"):
        imported(store, source)
    assert store.document.path.read_bytes() == previous
    assert len(store.list("jarvis")) == 32
    assert imported(store, source, agent="different").agent_id == "different"


@pytest.mark.parametrize("agent", ["", "../bad", [], None])
def test_invalid_agent_never_selects_default_or_other_scope(store, agent):
    with pytest.raises(SkillError, match="skill_agent_invalid"):
        store.list(agent)


def test_invalid_digest_and_identity_never_mutate(store, source):
    preview = store.inspect_directory(source)
    with pytest.raises(SkillError, match="skill_digest_invalid"):
        store.import_directory("jarvis", source, expected_sha256="bad")
    with pytest.raises(SkillError, match="skill_enabled_invalid"):
        store.import_directory("jarvis", source, expected_sha256=preview.sha256, enabled="true")
    with pytest.raises(SkillError, match="skill_id_invalid"):
        store.get("jarvis", "../other")
    for path in ("relative", None):
        with pytest.raises(SkillError, match="skill_source_path_invalid"):
            store.inspect_directory(path)


@pytest.mark.parametrize("replace_directory", [False, True])
def test_source_replaced_while_reading_is_not_imported(
    store, source, monkeypatch, replace_directory
):
    original = os.read
    target_info = (source / "SKILL.md").stat()
    changed = False

    def replace_during_read(fd, amount):
        nonlocal changed
        raw = original(fd, amount)
        current = os.fstat(fd)
        if not changed and current.st_ino == target_info.st_ino and raw:
            changed = True
            if replace_directory:
                source.rename(source.with_name("moved-source"))
                source.mkdir(mode=0o700)
            else:
                (source / "SKILL.md").unlink()
            (source / "SKILL.md").write_text(manifest("Contenido sustituto\n"))
            (source / "SKILL.md").chmod(0o600)
        return raw

    monkeypatch.setattr(os, "read", replace_during_read)
    with pytest.raises(SkillError, match="skill_source_changed"):
        store.inspect_directory(source)
    assert changed and not store.document.path.exists()
