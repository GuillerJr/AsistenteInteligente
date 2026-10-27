"""Small supervisor oracles: no Swift build, disk image or full-suite launch."""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "jarvis_test_resource_guard", Path(__file__).resolve().parents[1]
    / "script/test_resource_guard.py")
guard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = guard
SPEC.loader.exec_module(guard)


def options(**changes):
    values = dict(profile="local", kind="python", selection="tests/test_own.py", full=False,
                  budget_mib=64, deadline=2, command=[sys.executable, "-c", "pass"])
    return SimpleNamespace(**(values | changes))


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "repo"
    root.mkdir(mode=0o700)
    (root / "own.py").write_text("VALUE = 1\n")
    for arguments in (["init", "-b", "main"], ["add", "own.py"],
                      ["-c", "user.name=Jarvis Fixture", "-c", "user.email=fixture@example.invalid",
                       "commit", "-m", "own synthetic source"]):
        subprocess.run(["git", "-C", str(root), *arguments], capture_output=True,
                       check=True, timeout=10)
    return root


def test_fingerprint_binds_head_index_worktree_and_nonignored_untracked(source):
    initial = guard.fingerprint(source)
    assert guard.fingerprint(source) == initial
    (source / "new.py").write_text("NEW = True\n")
    untracked = guard.fingerprint(source)
    assert untracked["fingerprint"] != initial["fingerprint"]
    subprocess.run(["git", "-C", str(source), "add", "new.py"], check=True, timeout=10)
    staged = guard.fingerprint(source)
    assert staged["index_sha256"] != untracked["index_sha256"]
    assert staged["worktree_sha256"] == untracked["worktree_sha256"]
    (source / "own.py").write_text("VALUE = 2\n")
    assert guard.fingerprint(source)["worktree_sha256"] != staged["worktree_sha256"]


def test_fingerprint_handles_deleted_directory_and_never_follows_link(source, tmp_path):
    folder = source / "folder"
    folder.mkdir()
    (folder / "a.py").write_text("A=1\n")
    subprocess.run(["git", "-C", str(source), "add", "folder/a.py"], check=True, timeout=10)
    (folder / "a.py").unlink()
    folder.rmdir()
    deleted = guard.fingerprint(source)
    target = tmp_path / "outside"
    target.mkdir()
    folder.symlink_to(target, target_is_directory=True)
    with pytest.raises(guard.GuardError, match="ancestro"):
        guard.fingerprint(source)
    assert deleted["files"] == 2


@pytest.mark.parametrize("name", ["auth.json", ".env", "identity.key", "secrets.json"])
def test_fingerprint_refuses_private_presence_before_reading(source, monkeypatch, name):
    (source / name).write_bytes(b"SYNTHETIC_DO_NOT_READ")
    original = os.open

    def reject_private(path, *args, **kwargs):
        assert Path(path).name != name
        return original(path, *args, **kwargs)

    monkeypatch.setattr(guard.os, "open", reject_private)
    with pytest.raises(guard.GuardError, match="privado"):
        guard.fingerprint(source)


def test_tracked_private_local_configuration_is_rejected_before_open(source, monkeypatch):
    directory = source / ".claude"
    directory.mkdir()
    (directory / "settings.local.json").write_bytes(b"OWN_SYNTHETIC_DO_NOT_READ")
    subprocess.run(["git", "-C", str(source), "add", ".claude/settings.local.json"],
                   check=True, timeout=10)
    original = os.open

    def reject_private(path, *args, **kwargs):
        assert Path(path).name != "settings.local.json"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(guard.os, "open", reject_private)
    with pytest.raises(guard.GuardError, match="configuración local privada"):
        guard.fingerprint(source)


@pytest.mark.parametrize("changes", [{"full": True}, {"selection": "tests"},
    {"selection": ""}, {"budget_mib": 769}, {"deadline": 181}, {"command": []}])
def test_local_configuration_never_falls_back_to_full_or_raises_budget(tmp_path, changes):
    with pytest.raises(guard.GuardError):
        guard.validate_options(options(**changes), {}, {"head": "a" * 40}, tmp_path)


def test_ci_requires_explicit_full_and_exact_workspace_and_head(tmp_path):
    identity = {"head": "a" * 40}
    environment = {"GITHUB_ACTIONS": "true", "GITHUB_SHA": identity["head"],
                   "GITHUB_WORKSPACE": str(tmp_path)}
    configuration = options(profile="ci", full=True, selection="tests", deadline=1200)
    guard.validate_options(configuration, environment, identity, tmp_path)
    for key in environment:
        bad = dict(environment)
        bad.pop(key)
        with pytest.raises(guard.GuardError):
            guard.validate_options(configuration, bad, identity, tmp_path)


def test_allocation_counts_hardlinks_once_and_does_not_follow_symlinks(tmp_path):
    own = tmp_path / "own"
    own.mkdir(mode=0o700)
    path = own / "file"
    path.write_bytes(b"x" * 8192)
    first = guard.allocated_bytes(own)
    os.link(path, own / "same-inode")
    after = guard.allocated_bytes(own)
    assert after - first < 8192
    (own / "outside").symlink_to(Path("/"), target_is_directory=True)
    assert guard.allocated_bytes(own, maximum_entries=10) < 64 * 1024


def supervise(tmp_path, code, **changes):
    root = Path(guard.tempfile.mkdtemp(prefix="resource-", dir=tmp_path))
    arguments = dict(environment={"PATH": os.defpath}, cwd=root, budget_root=root,
        budget_bytes=1024 * 1024, deadline=3, data_root=tmp_path,
        free_floor=0, max_data_drop=guard.GIB, poll_seconds=0.02)
    return guard.supervise([sys.executable, "-I", "-B", "-c", code],
                           **(arguments | changes))


def test_real_small_child_output_is_drained_and_only_finite_tail_retained(tmp_path):
    result, log = supervise(tmp_path, "import os;os.write(1,b'X'*600000)")
    assert result["exit_code"] == 0 and result["process_group_exited"]
    assert not result["interrupted"]
    assert len(log) == guard.LOG_LIMIT and log == b"X" * guard.LOG_LIMIT


def test_real_small_budget_cancels_only_owned_job_and_does_not_claim_cleanup(tmp_path):
    code = "import pathlib,time;pathlib.Path('bytes').write_bytes(b'X'*196608);time.sleep(30)"
    result, _ = supervise(tmp_path, code, budget_bytes=128 * 1024)
    assert result["stop_reason"] == "temporary_budget_exceeded"
    assert result["interrupted"]
    assert result["peak_temporary_bytes"] < 512 * 1024
    assert result["custody_scope"] == "owned_process_group_only"


def test_real_deadline_has_finite_termination_and_failure_retains_exit_code(tmp_path):
    result, _ = supervise(tmp_path, "import time;time.sleep(30)", deadline=0.08)
    assert result["stop_reason"] == "deadline" and result["elapsed_seconds"] < 3
    failed, _ = supervise(tmp_path, "raise SystemExit(7)")
    assert failed["exit_code"] == 7 and failed["stop_reason"] is None


def test_low_storage_prevents_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "available_bytes", lambda _path: 1)
    monkeypatch.setattr(guard.subprocess, "Popen", lambda *a, **k: pytest.fail("dispatched"))
    with pytest.raises(guard.GuardError, match="No se inició"):
        supervise(tmp_path, "pass", free_floor=2)


def test_environment_is_private_and_does_not_inherit_account_secrets(tmp_path):
    admission = {"profile": "local", "selection": "tests/test_own.py", "nonce": "a" * 48}
    value = guard.child_environment(tmp_path, tmp_path, admission,
        {"PATH": os.defpath, "OPENAI_API_KEY": "SYNTHETIC_NEVER_FORWARD",
         "GITHUB_TOKEN": "SYNTHETIC_NEVER_FORWARD"}, tmp_path)
    assert "OPENAI_API_KEY" not in value and "GITHUB_TOKEN" not in value
    assert Path(value["HOME"]).is_relative_to(tmp_path)
    assert value["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "--basetemp=" + str(tmp_path / "p") in value["PYTEST_ADDOPTS"]


def test_policy_blocks_writes_and_chmod_outside_private_volume(tmp_path):
    value = guard.sandbox_policy(tmp_path / "source", tmp_path / "control", tmp_path / "volume")
    assert '(allow file-read* file-write* (subpath ' + json.dumps(str(tmp_path / "volume")) in value
    assert '(deny file-write-mode file-write-owner file-write-unlink (literal ' in value
    assert '(subpath "/private/tmp")' not in value
    assert '(remote ip "localhost:*")' in value


@pytest.mark.parametrize("chosen,accepted", [
    ("/Applications/Xcode.app/Contents/Developer/usr/bin/git", True),
    ("/private/tmp/not-an-admitted-developer/git", False),
])
def test_git_resolution_is_explicit_and_does_not_broaden_sandbox(monkeypatch, chosen, accepted):
    monkeypatch.setattr(guard.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout=(chosen + "\n").encode()))
    monkeypatch.setattr(guard.Path, "resolve", lambda self, **_kwargs: self)
    monkeypatch.setattr(guard.Path, "is_file", lambda _self: True)
    monkeypatch.setattr(guard.os, "access", lambda *_args: True)
    environment = {"PATH": "/usr/bin:/bin"}
    if accepted:
        guard.resolve_local_git(environment)
        assert environment["JARVIS_TEST_GUARD_GIT"] == chosen
        assert environment["PATH"].startswith(str(Path(chosen).parent) + ":")
    else:
        with pytest.raises(guard.GuardError, match="fuera de las rutas"):
            guard.resolve_local_git(environment)


def test_receipt_requires_same_tree_success_cleanup_and_log_identity(source, tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700)
    (evidence / "tail.log").write_bytes(b"OWN DIAGNOSTIC")
    (evidence / "tail.log").chmod(0o600)
    value = {"format": guard.FORMAT, "cleanup_status": "confirmed", "source_unchanged": True,
        "source_after": guard.fingerprint(source), "result": {"exit_code": 0},
        "log_sha256": hashlib.sha256(b"OWN DIAGNOSTIC").hexdigest()}
    guard.write_json(evidence / "receipt.json", value)
    assert guard.verify_receipt(evidence / "receipt.json", source) == value
    (evidence / "tail.log").write_bytes(b"CHANGED")
    with pytest.raises(guard.GuardError, match="diagnóstico cambió"):
        guard.verify_receipt(evidence / "receipt.json", source)


def test_pruning_preserves_uncertain_unique_pins_and_unknown_files(tmp_path):
    for name, status, pinned, extra in (("closed", "confirmed", False, False),
            ("pending", "uncertain", False, False), ("unique", "confirmed", True, False),
            ("unknown", "confirmed", False, True)):
        path = tmp_path / ("jarvis-test-evidence-" + name)
        path.mkdir(mode=0o700)
        guard.write_json(path / "receipt.json", {"format": guard.FORMAT,
            "cleanup_status": status, "created_ns": 1})
        if pinned:
            (path / "pinned").touch()
        if extra:
            (path / "unique-corpus").write_bytes(b"OWN")
    guard.prune_evidence(tmp_path, keep=0)
    assert not (tmp_path / "jarvis-test-evidence-closed").exists()
    assert all((tmp_path / ("jarvis-test-evidence-" + name)).is_dir()
               for name in ("pending", "unique", "unknown"))


def test_orphaned_admission_blocks_another_run_without_dispatch(tmp_path, monkeypatch):
    evidence = tmp_path / "jarvis-test-evidence-orphan"
    evidence.mkdir(mode=0o700)
    guard.write_json(evidence / "admission.json", {"format": guard.FORMAT})
    monkeypatch.setattr(guard.subprocess, "Popen", lambda *a, **k: pytest.fail("dispatch"))
    with pytest.raises(guard.GuardError, match="Cierre sin confirmar"):
        guard.reject_orphaned_evidence(tmp_path)
    guard.write_json(evidence / "receipt.json", {"cleanup_status": "confirmed"})
    guard.reject_orphaned_evidence(tmp_path)
    (evidence / "quota.sparseimage").write_bytes(b"OWN_UNCONFIRMED_IMAGE")
    with pytest.raises(guard.GuardError, match="conserva recursos"):
        guard.reject_orphaned_evidence(tmp_path)
