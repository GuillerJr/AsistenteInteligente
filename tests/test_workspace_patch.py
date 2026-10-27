"""Own unified patches exercise exact authority and real disposable filesystem effects."""
import asyncio
import difflib
import hashlib
import json
import os
from uuid import uuid4

import pytest

from jarvis_core.tools import ApprovalAuthority, ToolCatalog, ToolDefinition
from jarvis_core.workspace import WorkspaceTools
from jarvis_core.workspace_patch import parse_patch


def sha(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def delta(before, after, source, destination=None):
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile="/dev/null" if source is None else "a/" + source,
        tofile="/dev/null" if destination is None and not after else "b/" + (destination or source),
    ))


@pytest.fixture
async def patch_bundle(tmp_path):
    root = tmp_path.resolve() / "workspace"
    root.mkdir(mode=0o700)
    workspace = WorkspaceTools(root)
    authority = ApprovalAuthority(ttl=4)
    catalog = ToolCatalog(workspace.definitions(), workspace.specs(), authority=authority)
    try:
        yield root, workspace, authority, catalog
    finally:
        await authority.close()


async def invoke(bundle, arguments, *, allow=True, before=None, cancellation=None):
    _root, _workspace, authority, catalog = bundle
    task = asyncio.create_task(catalog.execute(
        "workspace_patch", arguments, session_id="patch-chat", run_id=uuid4().hex,
        cancellation=cancellation,
    ))
    async with asyncio.timeout(3):
        while not task.done():
            pending = authority.list_pending()
            if pending:
                if before:
                    before()
                authority.decide(pending[0].approval_id, allow)
                break
            await asyncio.sleep(0)
        return await task


async def test_one_review_add_edit_delete_move_and_exact_hashes(patch_bundle):
    root, _, _, _ = patch_bundle
    (root / "edit.txt").write_text("one\ntwo\nthree\n")
    (root / "delete.txt").write_text("delete me\n")
    (root / "move.txt").write_text("move me\n")
    patch = delta("", "brand new\n", None, "add.txt")
    patch += delta("one\ntwo\nthree\n", "one\nchanged\nthree\n", "edit.txt")
    patch += delta("delete me\n", "", "delete.txt")
    patch += "--- a/move.txt\n+++ b/moved.txt\n"
    result = await invoke(patch_bundle, {"patch": patch, "expected_sha256": {
        "add.txt": None, "edit.txt": sha("one\ntwo\nthree\n"),
        "delete.txt": sha("delete me\n"), "move.txt": sha("move me\n"), "moved.txt": None,
    }})
    assert result.succeeded and result.verified and result.effect_status == "known"
    assert (root / "add.txt").read_bytes() == b"brand new\n"
    assert (root / "edit.txt").read_bytes() == b"one\nchanged\nthree\n"
    assert not (root / "delete.txt").exists() and not (root / "move.txt").exists()
    assert (root / "moved.txt").read_bytes() == b"move me\n"
    receipt = json.loads(result.output)
    assert len(receipt["files"]) == 4 and receipt["permits_replay"] is False
    for row in receipt["files"]:
        if row["destination"]:
            assert sha((root / row["destination"]).read_bytes()) == row["sha256"]
    assert not list(root.glob(".jarvis-tmp-*"))


async def test_multiple_hunks_have_exact_coordinates_not_fuzzy_search(patch_bundle):
    root, _, _, _ = patch_bundle
    before = "".join(f"line {i}\n" for i in range(30))
    after = before.replace("line 2\n", "replacement two\n").replace("line 28\n", "tail\nextra\n")
    (root / "multi.txt").write_text(before)
    patch = delta(before, after, "multi.txt")
    assert patch.count("@@ ") == 2
    result = await invoke(patch_bundle, {
        "patch": patch, "expected_sha256": {"multi.txt": sha(before)},
    })
    assert result.verified and (root / "multi.txt").read_text() == after


async def test_no_newline_marker_preserves_exact_bytes(patch_bundle):
    root, _, _, _ = patch_bundle
    (root / "end.txt").write_bytes(b"old")
    patch = ("--- a/end.txt\n+++ b/end.txt\n@@ -1 +1 @@\n-old\n"
             "\\ No newline at end of file\n+new\n\\ No newline at end of file\n")
    result = await invoke(patch_bundle, {
        "patch": patch, "expected_sha256": {"end.txt": sha(b"old")},
    })
    assert result.verified and (root / "end.txt").read_bytes() == b"new"


@pytest.mark.parametrize("path", ["../out", "/absolute", ".codex/auth.json", ".git/config",
                                  "auth.json", "x.key", "a//b", "a/../b"])
async def test_patch_cannot_escape_or_name_private_paths(patch_bundle, path):
    patch = f"--- /dev/null\n+++ {path}\n@@ -0,0 +1 @@\n+NO\n"
    result = await invoke(patch_bundle, {"patch": patch, "expected_sha256": {path: None}})
    assert not result.succeeded and result.effect_status == "known"
    assert not list(patch_bundle[0].iterdir())


@pytest.mark.parametrize("kind", ["source_changed", "destination_exists", "context", "missing_hash",
                                   "source_symlink", "source_hardlink", "parent_symlink"])
async def test_preflight_rejects_whole_patch_before_any_publication(patch_bundle, tmp_path, kind):
    root, _, _, _ = patch_bundle
    (root / "source.txt").write_text("original\n")
    patch = delta("", "first\n", None, "first.txt")
    patch += delta("original\n", "new\n", "source.txt")
    expected = {"first.txt": None, "source.txt": sha("original\n")}
    before = None
    if kind == "source_changed":
        def before():
            (root / "source.txt").write_text("later\n")
    elif kind == "destination_exists":
        (root / "first.txt").write_text("keep\n")
    elif kind == "context":
        patch = patch.replace("-original\n", "-different\n")
    elif kind == "missing_hash":
        expected.pop("source.txt")
    elif kind == "source_symlink":
        (root / "source.txt").unlink()
        (root / "source.txt").symlink_to(tmp_path)
    elif kind == "source_hardlink":
        os.link(root / "source.txt", root / "linked.txt")
    else:
        target = tmp_path / "elsewhere"
        target.mkdir()
        (root / "link").symlink_to(target)
        patch = delta("", "first\n", None, "link/first.txt")
        expected = {"link/first.txt": None}
    result = await invoke(patch_bundle, {"patch": patch, "expected_sha256": expected},
                          before=before)
    assert not result.succeeded and result.effect_status == "known"
    assert not list(root.glob(".jarvis-tmp-*"))
    if kind == "destination_exists":
        assert (root / "first.txt").read_text() == "keep\n"
    else:
        assert not (root / "first.txt").exists()


async def test_owner_denial_and_direct_callback_do_not_mutate(patch_bundle):
    root, workspace, _, _ = patch_bundle
    args = {"patch": delta("", "no\n", None, "no.txt"), "expected_sha256": {"no.txt": None}}
    denied = await invoke(patch_bundle, args, allow=False)
    assert denied.error == "approval_denied"
    tool = next(row for row in workspace.definitions() if row.name == "workspace_patch")
    bypass = await tool.execute(args, asyncio.Event())
    assert bypass.error == "tool_authority_required" and not list(root.iterdir())


@pytest.mark.parametrize("interrupt", ["cancel", "revoke", "os_error"])
async def test_partial_effect_never_reports_success_or_replays(
    patch_bundle, monkeypatch, interrupt,
):
    root, _, authority, _ = patch_bundle
    for name in ["one.txt", "two.txt"]:
        (root / name).write_text("old\n")
    patch = delta("old\n", "new\n", "one.txt") + delta("old\n", "new\n", "two.txt")
    cancellation = asyncio.Event()
    original = os.replace
    seen = []
    def replace(*args, **kwargs):
        seen.append(args[1])
        if len(seen) == 2 and interrupt == "os_error":
            raise OSError("synthetic publication fault")
        result = original(*args, **kwargs)
        if interrupt == "cancel":
            cancellation.set()
        elif interrupt == "revoke":
            authority.revoke("patch-chat")
        return result
    monkeypatch.setattr(os, "replace", replace)
    result = await invoke(patch_bundle, {"patch": patch, "expected_sha256": {
        "one.txt": sha("old\n"), "two.txt": sha("old\n"),
    }}, cancellation=cancellation)
    assert not result.succeeded and result.effect_status == "unknown"
    assert (root / "one.txt").read_text() == "new\n"
    assert (root / "two.txt").read_text() == "old\n"
    assert seen.count("one.txt") == 1 and not list(root.glob(".jarvis-tmp-*"))


@pytest.mark.parametrize("patch", [
    "", "--- a/x\n+++ b/x\n", "--- /dev/null\n+++ b/x\n@@ -0,0 +1,2 @@\n+one\n",
    "--- a/x\n+++ b/x\n@@ malformed @@\n-old\n+new\n",
    "--- /dev/null\n+++ b/x\n@@ -0,0 +1 @@\n+one\n--- /dev/null\n+++ b/x\n",
])
def test_invalid_or_ambiguous_patch_is_not_silently_repaired(patch):
    with pytest.raises(ValueError):
        parse_patch(patch)


async def test_browser_download_requires_its_original_committed_lease(patch_bundle):
    root, workspace, authority, _ = patch_bundle
    payload = b"x" * 65536
    args = {"path": "download.bin"}
    called = []
    async def execute(arguments, _cancellation):
        from jarvis_core.tools import check_tool_authority
        check_tool_authority("browser_download", arguments, commit=True)
        result = workspace.publish_download(arguments["path"], payload,
                                            arguments=arguments, guard=lambda: called.append(True))
        from jarvis_core.runtime import ToolResult
        return ToolResult("browser_download", True, True, output=json.dumps(result))
    catalog = ToolCatalog([ToolDefinition("browser_download", lambda _: None, execute,
                                          requires_confirmation=True)],
                          [workspace.specs()[0].__class__("browser_download", "Prueba sintética",
                           {"type": "object"})], authority=authority)
    task = asyncio.create_task(catalog.execute("browser_download", args,
                                              session_id="download", run_id=uuid4().hex))
    async with asyncio.timeout(3):
        while not authority.list_pending():
            await asyncio.sleep(0)
        authority.decide(authority.list_pending()[0].approval_id, True)
        result = await task
    assert result.verified and (root / args["path"]).read_bytes() == payload
    assert json.loads(result.output)["sha256"] == sha(payload)
    assert len(called) == 2 and os.stat(root / args["path"]).st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="authority_required"):
        workspace.publish_download("bypass.bin", b"NO", arguments={"path": "bypass.bin"},
                                   guard=lambda: None)
    assert not (root / "bypass.bin").exists()


async def test_empty_add_and_empty_delete_are_explicit_real_operations(patch_bundle):
    root, _, _, _ = patch_bundle
    added = await invoke(patch_bundle, {
        "patch": "--- /dev/null\n+++ b/empty.txt\n", "expected_sha256": {"empty.txt": None},
    })
    assert added.verified and (root / "empty.txt").read_bytes() == b""
    removed = await invoke(patch_bundle, {
        "patch": "--- a/empty.txt\n+++ /dev/null\n", "expected_sha256": {"empty.txt": sha(b"")},
    })
    assert removed.verified and not (root / "empty.txt").exists()


@pytest.mark.parametrize("fault", ["exists", "symlink", "oversize", "path_mismatch",
                                  "revoked_after_stage", "parent_changed"])
async def test_download_boundary_fails_without_publishing_new_bytes(
    patch_bundle, tmp_path, fault,
):
    root, workspace, authority, _ = patch_bundle
    args = {"path": "received.bin"}
    payload = b"safe fixture"
    outside = tmp_path / "outside-fixture"
    outside.write_bytes(b"outside must remain")
    if fault == "exists":
        (root / "received.bin").write_bytes(b"existing must remain")
    elif fault == "symlink":
        (root / "received.bin").symlink_to(outside)
    elif fault == "oversize":
        payload = b"x" * 65537
    calls = []
    def guard():
        calls.append(True)
        if len(calls) == 2 and fault == "revoked_after_stage":
            authority.revoke("download-fault")
    async def execute(arguments, _cancellation):
        from jarvis_core.tools import check_tool_authority
        check_tool_authority("browser_download", arguments, commit=True)
        if fault == "parent_changed":
            root.rename(root.with_name("displaced-workspace"))
            root.mkdir(mode=0o700)
        value = workspace.publish_download(
            "different.bin" if fault == "path_mismatch" else arguments["path"], payload,
            arguments=arguments, guard=guard,
        )
        from jarvis_core.runtime import ToolResult
        return ToolResult("browser_download", True, True, output=json.dumps(value))
    catalog = ToolCatalog([ToolDefinition("browser_download", lambda _: None, execute,
                                          requires_confirmation=True)],
                          [workspace.specs()[0].__class__("browser_download", "Prueba sintética",
                           {"type": "object"})], authority=authority)
    task = asyncio.create_task(catalog.execute("browser_download", args,
                        session_id="download-fault", run_id=uuid4().hex))
    async with asyncio.timeout(3):
        while not authority.list_pending():
            await asyncio.sleep(0)
        authority.decide(authority.list_pending()[0].approval_id, True)
        reply = await task
    assert not reply.succeeded
    assert outside.read_bytes() == b"outside must remain"
    if fault == "exists":
        assert (root / "received.bin").read_bytes() == b"existing must remain"
    elif fault != "symlink":
        assert not (root / "received.bin").exists()
    assert not (root / "different.bin").exists()
    assert not list(root.glob(".jarvis-tmp-*"))


def test_patch_paths_are_reviewable_and_receipts_fit_their_byte_budget():
    with pytest.raises(ValueError, match="path_invalid"):
        parse_patch("--- /dev/null\n+++ b/control\x01.txt\n")
    paths = ["/".join([str(i) + "x" * 240] * 6) for i in range(6)]
    with pytest.raises(ValueError, match="patch_limit"):
        parse_patch("".join(f"--- /dev/null\n+++ b/{path}\n" for path in paths))


async def test_move_failure_identifies_the_verified_destination_and_uncertain_removal(
    patch_bundle, monkeypatch,
):
    root, _, _, _ = patch_bundle
    (root / "move.txt").write_text("preserved\n")
    original = os.unlink
    def fail_source(name, *args, **kwargs):
        if name == "move.txt":
            raise OSError("synthetic source removal failure")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(os, "unlink", fail_source)
    reply = await invoke(patch_bundle, {
        "patch": "--- a/move.txt\n+++ b/moved.txt\n", "expected_sha256": {
            "move.txt": sha("preserved\n"), "moved.txt": None,
        },
    })
    assert not reply.succeeded and reply.effect_status == "unknown"
    assert (root / "move.txt").read_bytes() == (root / "moved.txt").read_bytes() == b"preserved\n"
    receipt = json.loads(reply.output)
    assert receipt["completed_files"] == []
    assert receipt["uncertain_file"] == {
        "source": "move.txt", "destination": "moved.txt", "phase": "source_removal_started",
    }
    assert receipt["permits_replay"] is False and not list(root.glob(".jarvis-tmp-*"))
