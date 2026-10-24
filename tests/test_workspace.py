"""Real temporary files; approval choices here are synthetic test decisions."""

import asyncio
import hashlib
import json
import os
import stat

import pytest

from jarvis_core.tools import ApprovalAuthority, ToolCatalog
from jarvis_core.workspace import WorkspaceError, WorkspaceTools


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path.resolve() / "workspace"
    root.mkdir(mode=0o700)
    tools = WorkspaceTools(root)
    authority = ApprovalAuthority(ttl=1)
    catalog = ToolCatalog(tools.definitions(), tools.specs(), authority=authority)
    return root, tools, authority, catalog


async def invoke(workspace, name, arguments, *, allow=True, before_decide=None):
    _, _, authority, catalog = workspace
    task = asyncio.create_task(catalog.execute(name, arguments, session_id="s", run_id="r"))
    async with asyncio.timeout(2):
        while not task.done():
            tickets = authority.list_pending()
            if tickets:
                if before_decide:
                    before_decide()
                authority.decide(tickets[0].approval_id, allow)
                break
            await asyncio.sleep(0.001)
        return await task


async def test_create_read_edit_verify_and_no_silent_overwrite(workspace):
    root, _, _, _ = workspace
    result = await invoke(workspace, "workspace_write", {"path": "nota.txt", "text": "uno\ndos"})
    assert result.succeeded and result.verified
    written = json.loads(result.output)
    assert written["sha256"] == hashlib.sha256(b"uno\ndos").hexdigest()
    assert stat.S_IMODE((root / "nota.txt").stat().st_mode) == 0o600
    read = await invoke(workspace, "workspace_read", {"path": "nota.txt"})
    assert json.loads(read.output)["text"] == "uno\ndos"
    conflict = await invoke(workspace, "workspace_write", {"path": "nota.txt", "text": "NO"})
    assert not conflict.succeeded and (root / "nota.txt").read_text() == "uno\ndos"
    edited = await invoke(workspace, "workspace_edit", {
        "path": "nota.txt", "old_text": "dos", "new_text": "tres",
        "expected_sha256": written["sha256"],
    })
    assert edited.verified and (root / "nota.txt").read_text() == "uno\ntres"
    assert not list(root.glob(".jarvis-tmp-*"))


async def test_denial_leaves_files_untouched(workspace):
    root, _, _, _ = workspace
    result = await invoke(workspace, "workspace_write", {"path": "no.txt", "text": "no"},
                          allow=False)
    assert result.error == "approval_denied" and not (root / "no.txt").exists()
    assert not list(root.iterdir())


async def test_mutator_callback_cannot_bypass_authority(workspace):
    root, tools, _, _ = workspace
    write = next(tool for tool in tools.definitions() if tool.name == "workspace_write")
    result = await write.execute({"path": "no.txt", "text": "no"}, asyncio.Event())
    assert result.error == "tool_authority_required" and not (root / "no.txt").exists()


@pytest.mark.parametrize("path", ["../outside", "/outside", "a/../outside", "a//b", "a\\b",
                                  "auth.json", ".ssh/key", ".codex/sessions/data", ".env",
                                  ".env.local", "x/history.json", "a/.git/config",
                                  "transport.token", "jarvis.sqlite3", "jarvis.sqlite3-wal"])
async def test_private_and_escaping_paths_rejected_without_reading(workspace, path):
    result = await invoke(workspace, "workspace_read", {"path": path})
    assert not result.succeeded and result.effect_status == "known"


async def test_symlinks_hardlinks_and_fifos_never_read(workspace, tmp_path):
    root, _, _, _ = workspace
    private = tmp_path / "outside"
    private.write_text("SECRET")
    (root / "linked").symlink_to(private)
    os.link(private, root / "hardlink")
    os.mkfifo(root / "fifo")
    for name in ("linked", "hardlink", "fifo"):
        result = await asyncio.wait_for(invoke(workspace, "workspace_read", {"path": name}), 1)
        assert not result.succeeded and "SECRET" not in result.output
    result = await invoke(workspace, "workspace_list", {})
    assert json.loads(result.output)["entries"] == []


async def test_directory_symlink_cannot_retarget_write(workspace, tmp_path):
    root, _, _, _ = workspace
    target = tmp_path / "outside"
    target.mkdir()
    (root / "dir").symlink_to(target, target_is_directory=True)
    result = await invoke(workspace, "workspace_write", {"path": "dir/no.txt", "text": "no"})
    assert not result.succeeded and not list(target.iterdir())


async def test_changed_root_identity_invalidates_selected_workspace(workspace, tmp_path):
    root, _, _, _ = workspace
    root.rename(tmp_path / "retired")
    root.mkdir(mode=0o700)
    result = await invoke(workspace, "workspace_write", {"path": "no.txt", "text": "no"})
    assert not result.succeeded and result.error == "workspace_identity_changed"
    assert not list(root.iterdir())


async def test_changed_file_during_approval_causes_conflict(workspace):
    root, _, _, _ = workspace
    path = root / "nota.txt"
    path.write_text("antes")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    result = await invoke(workspace, "workspace_write", {
        "path": "nota.txt", "text": "modelo", "expected_sha256": digest,
    }, before_decide=lambda: path.write_text("cambio externo"))
    assert not result.succeeded and path.read_text() == "cambio externo"


async def test_edit_requires_single_match_and_preserves_other_text(workspace):
    root, _, _, _ = workspace
    path = root / "nota.txt"
    path.write_text("x x")
    result = await invoke(workspace, "workspace_edit", {
        "path": "nota.txt", "old_text": "x", "new_text": "y",
        "expected_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    })
    assert result.error == "workspace_edit_not_unique" and path.read_text() == "x x"


async def test_revoke_at_precommit_cleans_temporary_and_keeps_target(workspace, monkeypatch):
    root, _, authority, _ = workspace
    original = os.fsync
    called = False

    def fsync(descriptor):
        nonlocal called
        original(descriptor)
        if not called:
            called = True
            authority.revoke("s")

    monkeypatch.setattr(os, "fsync", fsync)
    result = await invoke(workspace, "workspace_write", {"path": "no.txt", "text": "no"})
    assert not result.succeeded and result.error == "tool_authority_revoked"
    assert not list(root.iterdir())


async def test_verification_failure_is_uncertain_and_never_retried(workspace, monkeypatch):
    root, tools, _, _ = workspace
    original = tools._read

    def read(parent, name):
        data, info = original(parent, name)
        return (b"different", info) if name == "nota.txt" else (data, info)

    monkeypatch.setattr(tools, "_read", read)
    result = await invoke(workspace, "workspace_write", {"path": "nota.txt", "text": "uno"})
    assert not result.verified and result.effect_status == "unknown"
    assert (root / "nota.txt").read_text() == "uno"


async def test_mkdir_and_bounded_directory_pages(workspace):
    root, _, _, _ = workspace
    result = await invoke(workspace, "workspace_mkdir", {"path": "carpeta"})
    assert result.verified and (root / "carpeta").is_dir()
    assert stat.S_IMODE((root / "carpeta").stat().st_mode) == 0o700
    for index in range(140):
        (root / f"file-{index:03}").touch()
    first = json.loads((await invoke(workspace, "workspace_list", {})).output)
    second = json.loads((await invoke(workspace, "workspace_list", {
        "offset": first["next_offset"],
    })).output)
    assert first["has_more"] and not second["has_more"]
    assert len(first["entries"]) + len(second["entries"]) == 141


async def test_file_budget_and_binary_content_fail_without_partial_claim(workspace):
    root, _, _, _ = workspace
    (root / "large.txt").write_bytes(b"a" * 32001)
    (root / "binary").write_bytes(b"\xff\xfe")
    for name in ("large.txt", "binary"):
        result = await invoke(workspace, "workspace_read", {"path": name})
        assert not result.succeeded and not result.verified and not result.output


def test_workspace_rejects_unsafe_existing_root(tmp_path):
    path = tmp_path.resolve() / "unsafe"
    path.mkdir(mode=0o777)
    path.chmod(0o777)
    with pytest.raises(WorkspaceError, match="permissions"):
        WorkspaceTools(path)
    link = tmp_path / "link"
    link.symlink_to(path, target_is_directory=True)
    with pytest.raises(WorkspaceError):
        WorkspaceTools(link)


def test_selected_root_is_read_only_and_identity_is_exposed(workspace):
    root, tools, _, _ = workspace
    assert tools.root == root
    assert tools.identity == (root.stat().st_dev, root.stat().st_ino)
    with pytest.raises(AttributeError):
        tools.root = root.parent


@pytest.mark.parametrize("private", [".ssh", ".codex", ".aws", ".jarvis"])
def test_private_directory_cannot_be_selected_as_workspace(tmp_path, private):
    path = tmp_path.resolve() / private / "subdirectory"
    path.mkdir(mode=0o700, parents=True)
    with pytest.raises(WorkspaceError, match="private_path"):
        WorkspaceTools(path)
