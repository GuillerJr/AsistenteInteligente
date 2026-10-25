"""Agent registry and shared private transactions with disposable data only."""

import fcntl
import json
import os
import stat

import pytest

from jarvis_core.private_json import PrivateJsonDocument, PrivateJsonError
from jarvis_core.profiles import ProfileError, ProfilesStore
from jarvis_core.state import StateError


@pytest.fixture
def store(tmp_path):
    return ProfilesStore(tmp_path.resolve() / "private")


def test_default_profile_no_implicit_files_and_roundtrip(store):
    assert [x.agent_id for x in store.list()] == ["jarvis"]
    assert store.get("jarvis").name == "Jarvis"
    assert not store.document.path.exists()
    row = store.create("Investigación", "Responde con citas explícitas.", agent_id="investigacion")
    assert row.revision == 1 and row.created_ns == row.updated_ns
    assert ProfilesStore(store.document.directory).get(row.agent_id) == row
    assert stat.S_IMODE(store.document.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.document.directory.stat().st_mode) == 0o700


def test_generated_id_stable_revision_updates_and_fingerprint(store):
    row = store.create("Editor")
    assert row.agent_id.startswith("agent_")
    changed = store.update(
        row.agent_id, expected_revision=1, name="Redactor", instructions="Usa un lenguaje sencillo."
    )
    assert changed.revision == 2 and changed.created_ns == row.created_ns
    assert changed.fingerprint != row.fingerprint
    assert store.update(row.agent_id, expected_revision=2) == changed
    assert (
        store.update(row.agent_id, expected_revision=2, instructions=changed.instructions)
        == changed
    )
    with pytest.raises(ProfileError, match="agent_revision_conflict"):
        store.update(row.agent_id, expected_revision=1, name="Stale")
    with pytest.raises(ProfileError, match="agent_revision_conflict"):
        store.delete(row.agent_id, expected_revision=1)
    assert store.get(row.agent_id) == changed
    store.delete(row.agent_id, expected_revision=2)
    with pytest.raises(ProfileError, match="agent_not_found"):
        store.get(row.agent_id)


def test_instances_merge_latest_changes_without_lost_agents(store):
    second = ProfilesStore(store.document.directory)
    first = store.create("Uno", agent_id="one")
    second.create("Dos", agent_id="two")
    store.update("one", expected_revision=1, instructions="Primero")
    assert second.get("one").revision == 2 and second.get("two").name == "Dos"
    assert store.get("two").name == "Dos"
    assert store.get("one").created_ns == first.created_ns


def test_default_cannot_disappear_and_duplicate_id_is_rejected(store):
    with pytest.raises(ProfileError, match="default_agent_required"):
        store.delete("jarvis", expected_revision=1)
    with pytest.raises(ProfileError, match="agent_already_exists"):
        store.create("Otro", agent_id="jarvis")
    assert [x.agent_id for x in store.list()] == ["jarvis"]


@pytest.mark.parametrize("agent_id", ["", "../owner", "UPPER", "a" * 65, [], None, "a\0b"])
def test_invalid_agent_id_never_selects_another_profile(store, agent_id):
    with pytest.raises(ProfileError, match="agent_id_invalid"):
        store.get(agent_id)


@pytest.mark.parametrize(
    "name,instructions",
    [
        ("", ""),
        (" ", ""),
        ("x" * 257, ""),
        ("Editor", "x" * 8193),
        ("Editor", "\0"),
        ("\ud800", ""),
        ("Editor", "\ud800"),
        (None, ""),
    ],
)
def test_invalid_text_does_not_persist(store, name, instructions):
    with pytest.raises(ProfileError):
        store.create(name, instructions)
    assert not store.document.path.exists()


@pytest.mark.parametrize("revision", [True, 0, -1, "1", None])
def test_expected_revision_must_be_positive_integer(store, revision):
    with pytest.raises(ProfileError, match="agent_revision_invalid"):
        store.update("jarvis", expected_revision=revision, name="Otro")


def test_agent_capacity_does_not_overwrite_existing_data(store):
    for index in range(31):
        store.create(f"Agente {index}", agent_id=f"agent{index}")
    before = store.document.path.read_bytes()
    with pytest.raises(ProfileError, match="agent_limit"):
        store.create("Exceso")
    assert store.document.path.read_bytes() == before


@pytest.mark.parametrize(
    "content",
    [
        '{"version":1,"version":1,"agents":{}}',
        '{"version":1,"agents":{}}',
        '{"version":true,"agents":{}}',
        "[1,2,3]",
        "{broken",
        '{"version":1,"agents":{},"provider":"fallback"}',
    ],
)
def test_corrupt_or_unsupported_registry_never_becomes_profile(store, content):
    path = store.document.path
    path.write_text(content)
    path.chmod(0o600)
    with pytest.raises((PrivateJsonError, ProfileError)):
        store.list()
    with pytest.raises((PrivateJsonError, ProfileError)):
        ProfilesStore(path.parent)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "public"])
def test_private_registry_rejects_unsafe_file(store, tmp_path, kind):
    store.create("Uno")
    path = store.document.path
    outside = tmp_path / "outside"
    outside.write_bytes(path.read_bytes())
    outside.chmod(0o600)
    path.unlink()
    if kind == "symlink":
        path.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, path)
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.write_bytes(outside.read_bytes())
        path.chmod(0o644)
    with pytest.raises(PrivateJsonError):
        store.list()
    assert outside.stat().st_size > 0


def test_oversized_registry_and_deleted_observed_file_fail_closed(store):
    store.create("Uno")
    path = store.document.path
    original = path.read_bytes()
    path.write_bytes(b" " * 524289)
    with pytest.raises(PrivateJsonError):
        store.list()
    path.write_bytes(original)
    path.unlink()
    with pytest.raises(PrivateJsonError, match="registry_file_missing"):
        store.create("No recuperar por defecto")


def test_parent_symlink_and_replacement_are_rejected(tmp_path, store):
    real = tmp_path.resolve() / "real"
    real.mkdir(mode=0o700)
    link = tmp_path.resolve() / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises((StateError, PrivateJsonError)):
        ProfilesStore(link / "private")
    store.create("Uno")
    old = store.document.directory
    moved = old.with_name("moved")
    old.rename(moved)
    old.mkdir(mode=0o700)
    with pytest.raises(PrivateJsonError, match="registry_directory_changed"):
        store.list()
    assert not (old / "agents.json").exists()


def test_lock_is_nonblocking_and_does_not_drop_other_write(store):
    store.create("Uno")
    previous = store.document.path.read_bytes()
    fd = os.open(store.document.directory / store.document.lock_name, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(PrivateJsonError, match="registry_busy"):
            store.create("Dos")
    finally:
        os.close(fd)
    assert store.document.path.read_bytes() == previous


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "public", "nonempty"])
def test_unsafe_lock_never_authorizes_registry_write(store, tmp_path, kind):
    store.create("Uno")
    previous = store.document.path.read_bytes()
    path = store.document.directory / store.document.lock_name
    path.unlink()
    outside = tmp_path / "outside"
    outside.touch(mode=0o600)
    if kind == "symlink":
        path.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, path)
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.touch(mode=0o600)
        if kind == "public":
            path.chmod(0o644)
        else:
            path.write_text("not a lock")
    with pytest.raises(PrivateJsonError):
        store.create("Dos")
    assert store.document.path.read_bytes() == previous


def test_parent_swap_during_transaction_cannot_write_replacement(store, monkeypatch):
    store.create("Uno")
    original = store.document._write_temporary
    parent = store.document.directory
    moved = parent.with_name("moved")

    def replace_parent(fd, data):
        name = original(fd, data)
        parent.rename(moved)
        parent.mkdir(mode=0o700)
        return name

    monkeypatch.setattr(store.document, "_write_temporary", replace_parent)
    with pytest.raises(PrivateJsonError):
        store.create("Dos")
    assert not (parent / "agents.json").exists()
    assert len(ProfilesStore(moved).list()) == 2


def test_external_noncooperating_edit_is_not_overwritten(store, monkeypatch):
    store.create("Uno", agent_id="one")
    original = store.document._write_temporary

    def external_edit(fd, data):
        name = original(fd, data)
        value = json.loads(store.document.path.read_text())
        value["agents"]["one"]["name"] = "Modificado fuera"
        store.document.path.write_text(json.dumps(value))
        return name

    monkeypatch.setattr(store.document, "_write_temporary", external_edit)
    with pytest.raises(PrivateJsonError, match="registry_changed"):
        store.create("Dos")
    assert store.get("one").name == "Modificado fuera"
    assert len(store.list()) == 2


def test_durability_failure_does_not_claim_no_commit(store, monkeypatch):
    original = os.fsync

    def fail_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("synthetic fsync failure")
        original(fd)

    monkeypatch.setattr(os, "fsync", fail_directory)
    with pytest.raises(PrivateJsonError, match="registry_durability_uncertain"):
        store.create("Uno", agent_id="one")
    assert ProfilesStore(store.document.directory).get("one").name == "Uno"


def test_private_document_fixed_name_and_limits(tmp_path):
    for name in ("../file.json", "auth.json/private", "", "UPPER.json"):
        with pytest.raises(PrivateJsonError, match="registry_configuration_invalid"):
            PrivateJsonDocument(tmp_path.resolve(), name, initial=dict, validate=lambda row: None)
    with pytest.raises(PrivateJsonError):
        PrivateJsonDocument(
            tmp_path.resolve(), "test.json", initial=dict, validate=lambda row: None, max_bytes=True
        )


def test_replaced_lock_cannot_commit_under_detached_lock(store, monkeypatch):
    store.create("Uno")
    previous = store.document.path.read_bytes()
    original = store.document._write_temporary

    def swap_lock(fd, data):
        name = original(fd, data)
        lock = store.document.directory / store.document.lock_name
        lock.unlink()
        lock.touch(mode=0o600)
        return name

    monkeypatch.setattr(store.document, "_write_temporary", swap_lock)
    with pytest.raises(PrivateJsonError, match="registry_lock_changed"):
        store.create("Dos")
    assert store.document.path.read_bytes() == previous
