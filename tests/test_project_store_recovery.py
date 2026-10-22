import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from aegis_core.projects import ProjectIpcService, ProjectStore, ProjectStoreUnavailable


def make_store(tmp_path):
    path = tmp_path / "projects.json"
    store = ProjectStore(path)
    project = store.save(project_id=None, name="Proyecto sintético", color="clay", icon="book",
                         instructions="Conserva estas instrucciones.")
    store.add_file(project.project_id, "guía.txt", "Referencia sintética.")
    return store, path, project.project_id


def corrupt(payload, kind):
    if kind == "broken_json":
        return b'{"projects":['
    if kind == "invalid_utf8":
        return b'{"projects":\xff}'
    if kind == "missing_list":
        return b'{}'
    if kind == "wrong_root":
        return b'[]'
    if kind == "wrong_list":
        return b'{"projects":{}}'
    if kind == "duplicate_key":
        return b'{"projects":[],"projects":[]}'
    if kind == "invalid_entry":
        payload["projects"].append({"name": "Entrada dañada"})
    elif kind == "duplicate_project":
        payload["projects"].append(payload["projects"][0])
    elif kind == "duplicate_file":
        payload["projects"][0]["files"] *= 2
    return json.dumps(payload).encode()


@pytest.mark.parametrize("kind", [
    "broken_json", "invalid_utf8", "missing_list", "wrong_root", "wrong_list",
    "duplicate_key", "invalid_entry", "duplicate_project", "duplicate_file",
])
def test_damaged_store_is_never_treated_as_empty_or_overwritten(tmp_path, kind):
    store, path, project_id = make_store(tmp_path)
    damaged = corrupt(json.loads(path.read_text()), kind)
    path.write_bytes(damaged)
    files = {p: p.read_bytes() for p in (tmp_path / "projects").rglob("*.txt")}
    operations = [
        store.projects,
        lambda: store.get(project_id),
        lambda: store.context(project_id),
        lambda: store.save(project_id=None, name="Nuevo", color="clay", icon="book",
                           instructions=""),
        lambda: store.delete(project_id),
        lambda: store.add_file(project_id, "otro.txt", "Otro contenido."),
        lambda: store.remove_file(project_id, uuid4()),
    ]
    for operation in operations:
        with pytest.raises(ProjectStoreUnavailable, match="Conservé los datos"):
            operation()
        assert path.read_bytes() == damaged
        assert {p: p.read_bytes() for p in (tmp_path / "projects").rglob("*.txt")} == files


def test_missing_store_can_be_created_but_unreadable_existing_store_is_preserved(
    tmp_path, monkeypatch,
):
    store = ProjectStore(tmp_path / "projects.json")
    assert store.projects() == ()
    store, path, _ = make_store(tmp_path)
    original = path.read_bytes()
    read_text = Path.read_text

    def unreadable(self, *args, **kwargs):
        if self == path:
            raise PermissionError("synthetic inaccessible file")
        return read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    with pytest.raises(ProjectStoreUnavailable):
        store.save(project_id=None, name="Nuevo", color="clay", icon="book", instructions="")
    assert path.read_bytes() == original


@pytest.mark.parametrize("dangling", [False, True])
def test_project_store_symlink_cannot_be_read_or_replaced(tmp_path, dangling):
    target = tmp_path / "original.json"
    if not dangling:
        target.write_bytes(b'{"projects":[]}')
    path = tmp_path / "projects.json"
    path.symlink_to(target)
    with pytest.raises(ProjectStoreUnavailable):
        ProjectStore(path).save(project_id=None, name="Nuevo", color="clay", icon="book",
                                instructions="")
    assert path.is_symlink()
    if not dangling:
        assert target.read_bytes() == b'{"projects":[]}'


@pytest.mark.asyncio
@pytest.mark.parametrize("method", [
    "projects.list", "projects.save", "projects.file.add", "projects.delete", "projects.assign",
])
async def test_ipc_returns_safe_error_without_a_false_empty_listing(tmp_path, method):
    store, path, project_id = make_store(tmp_path)
    path.write_bytes(b'{"projects":[')

    class Conversations:
        namespace = "user.synthetic"

        async def all_records(self):
            return [SimpleNamespace(
                conversation_id=uuid4(), context_checkpoints={"project_id": str(project_id)},
            )]

        async def set_project(self, *_):
            pytest.fail("a failed project mutation must not change chat membership")

    class Request:
        payload = ({"name": "Nuevo"} if method == "projects.save" else
                   {"project_id": str(project_id), "name": "otro", "text": "contenido"}
                   if method == "projects.file.add" else
                   {"project_id": str(project_id)} if method == "projects.delete" else
                   {"conversation_id": str(uuid4()), "project_id": None}
                   if method == "projects.assign" else {})

    request = Request()
    request.method = method
    result = await ProjectIpcService(store, Conversations()).handle(request)
    assert result.ok
    assert result.payload == {
        "error": "No pude leer los proyectos guardados. Conservé los datos sin cambiarlos."
    }
    assert path.read_bytes() == b'{"projects":['
