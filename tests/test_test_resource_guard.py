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


def test_invalid_export_is_rejected_before_volume_preparation(tmp_path):
    with pytest.raises(guard.GuardError, match="directamente"):
        guard.validate_options(options(export_destination=tmp_path / "nested"), {}, {}, tmp_path)


def test_qa_launcher_build_flag_is_opt_in_without_forwarding_other_values(tmp_path):
    admission = {"profile": "ci", "selection": "Jarvis", "nonce": "synthetic"}
    for flag in (None, "0", "unexpected", "1"):
        original = {"OPENAI_API_KEY": "SYNTHETIC_NEVER_FORWARD"}
        if flag is not None:
            original["JARVIS_BUILD_QA_LAUNCHER"] = flag
        value = guard.child_environment(tmp_path, tmp_path, admission, original, tmp_path)
        assert (value.get("JARVIS_BUILD_QA_LAUNCHER") == "1") is (flag == "1")
        assert "OPENAI_API_KEY" not in value


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


def test_real_small_budget_cancels_only_owned_job_and_does_not_claim_cleanup(tmp_path, monkeypatch):
    # APFS space accounting may batch tiny writes. An independent stat of our
    # authored payload makes the component watchdog boundary deterministic.
    def payload_size(descriptor):
        try:
            return os.stat("bytes", dir_fd=descriptor, follow_symlinks=False).st_size
        except FileNotFoundError:
            return 0
    monkeypatch.setattr(guard, "volume_used", payload_size)
    code = "import pathlib,time;pathlib.Path('bytes').write_bytes(b'X'*196608);time.sleep(30)"
    result, _ = supervise(tmp_path, code, budget_bytes=128 * 1024)
    assert result["stop_reason"] == "temporary_budget_exceeded"
    assert result["interrupted"]
    assert result["peak_temporary_bytes"] < 512 * 1024
    assert result["custody_scope"] == "owned_process_group_only"


def test_real_deadline_has_finite_termination_and_failure_retains_exit_code(tmp_path, monkeypatch):
    # This component fixture lives on the CI host volume, not a dedicated APFS
    # quota. Unrelated writes must not masquerade as the child's deadline.
    monkeypatch.setattr(guard, "available_bytes", lambda _path: 32 * guard.GIB)
    result, _ = supervise(tmp_path, "import time;time.sleep(30)", deadline=0.08,
                          measurement_mode="ci_available_delta")
    assert result["stop_reason"] == "deadline" and result["elapsed_seconds"] < 3
    failed, _ = supervise(tmp_path, "raise SystemExit(7)", measurement_mode="ci_available_delta")
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
         "GITHUB_TOKEN": "SYNTHETIC_NEVER_FORWARD",
         "JARVIS_MINILM_TEST_ASSETS": "/not/an/admitted/input",
         "JARVIS_MINILM_REQUIRE_REAL": "1"}, tmp_path)
    assert "OPENAI_API_KEY" not in value and "GITHUB_TOKEN" not in value
    assert "JARVIS_MINILM_TEST_ASSETS" not in value and "JARVIS_MINILM_REQUIRE_REAL" not in value
    assert Path(value["HOME"]).is_relative_to(tmp_path)
    assert value["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "--basetemp=" + str(tmp_path / "p") in value["PYTEST_ADDOPTS"]


def test_qualification_asset_path_cannot_admit_a_symlink_or_arbitrary_directory(tmp_path):
    other = tmp_path / "public"
    other.mkdir()
    link = tmp_path / "alias"
    link.symlink_to(other, target_is_directory=True)
    with pytest.raises(guard.GuardError, match="física"):
        guard.qualify_minilm_assets(link)
    with pytest.raises(guard.GuardError, match="pins"):
        guard.qualify_minilm_assets(other)


def test_policy_blocks_writes_and_chmod_outside_private_volume(tmp_path):
    value = guard.sandbox_policy(tmp_path / "source", tmp_path / "control", tmp_path / "volume")
    assert '(allow file-read* file-write* (subpath ' + json.dumps(str(tmp_path / "volume")) in value
    assert '(deny file-write-mode file-write-owner file-write-unlink (literal ' in value
    assert '(subpath "/private/tmp")' not in value
    for ancestor in (tmp_path / "volume").parents:
        assert '(literal ' + json.dumps(str(ancestor)) + ')' in value
    assert '(subpath "/private")' not in value
    assert '(remote ip "localhost:*")' in value


@pytest.mark.parametrize("chosen,accepted", [
    ("/Applications/Xcode.app/Contents/Developer/usr/bin/git", True),
    ("/private/tmp/not-an-admitted-developer/git", False),
])
@pytest.mark.parametrize("resolver,tool", [(guard.resolve_local_git, "git"),
                                          (guard.resolve_local_swift, "swift")])
def test_tool_resolution_is_explicit_and_does_not_broaden_sandbox(
    monkeypatch, chosen, accepted, resolver, tool,
):
    chosen = str(Path(chosen).with_name(tool))
    sdk = "/Applications/Xcode.app/Contents/Developer/SDKs/MacOSX.sdk"
    monkeypatch.setattr(guard.subprocess, "run", lambda arguments, **k:
                        SimpleNamespace(returncode=0, stdout=(
                            sdk if "--show-sdk-path" in arguments else chosen).encode()))
    monkeypatch.setattr(guard.Path, "resolve", lambda self, **_kwargs: self)
    monkeypatch.setattr(guard.Path, "is_file", lambda _self: True)
    monkeypatch.setattr(guard.Path, "is_dir", lambda _self: True)
    monkeypatch.setattr(guard.os, "access", lambda *_args: True)
    environment = {"PATH": "/usr/bin:/bin"}
    if accepted:
        resolver(environment)
        if tool == "git":
            assert environment["JARVIS_TEST_GUARD_GIT"] == chosen
        else:
            assert environment["SDKROOT"] == sdk
        assert environment["PATH"].startswith(str(Path(chosen).parent) + ":")
    else:
        with pytest.raises(guard.GuardError, match="fuera de las rutas"):
            resolver(environment)


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


@pytest.mark.parametrize("wrapper,arguments,full", [
    ("test_desktop.sh", ["--filter", "OwnFixtureTests", "--budget-mib", "32"], False),
    ("test_desktop.sh", ["--full"], True),
    ("build_desktop.sh", ["--budget-mib", "32"], False),
    ("build_desktop.sh", ["--full"], True),
    ("build_desktop.sh", ["--full", "--self-contained"], True),
])
def test_wrappers_forward_empty_optional_arrays_on_system_bash(
        tmp_path, monkeypatch, wrapper, arguments, full):
    """macOS /bin/bash 3.2 rejects an empty array expansion under nounset."""
    repository = tmp_path / "own-wrapper-repo"
    scripts, binaries = repository / "script", repository / ".venv/bin"
    scripts.mkdir(parents=True)
    binaries.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "script" / wrapper
    selected = scripts / wrapper
    selected.write_bytes(source.read_bytes())
    python = binaries / "python"
    python.write_text("#!" + sys.executable + "\nimport json,sys\n"
                      "print(json.dumps(sys.argv[1:]))\n")
    python.chmod(0o700)
    if "--self-contained" in arguments:
        monkeypatch.setenv("JARVIS_MINILM_TEST_ASSETS", str(tmp_path / "public-pinned-model"))
    result = subprocess.run(["/bin/bash", str(selected), *arguments],
        capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
    forwarded = json.loads(result.stdout)
    assert "" not in forwarded
    assert ("--full" in forwarded) is full
    assert forwarded[forwarded.index("--profile") + 1] == ("ci" if full else "local")
    assert "admitted-exec" in forwarded and "--guard-child" in forwarded
    if "--self-contained" in arguments:
        assert forwarded[-1] == "--self-contained"
        assert forwarded[forwarded.index("--minilm-test-assets") + 1] == str(
            tmp_path / "public-pinned-model")


@pytest.mark.parametrize("mode", ["apfs_volume_used", "ci_available_delta"])
def test_resource_measurement_never_traverses_mode_zero_fixtures(tmp_path, monkeypatch, mode):
    root = tmp_path / "own-resource"
    root.mkdir(mode=0o700)
    hidden = root / "before"
    hidden.mkdir()
    hidden.chmod(0)
    identity = root.stat().st_dev, root.stat().st_ino
    monkeypatch.setattr(guard, "allocated_bytes", lambda *_a, **_k: pytest.fail("fixture walk"))
    # The APFS-used branch needs metadata for a private quota, whereas this
    # fixture is an ordinary directory on a shared CI host filesystem. Stub
    # only filesystem metadata; the child, permission failures and reap are real.
    metadata_reads = []
    def quota_metadata(descriptor):
        info = os.fstat(descriptor)
        assert (info.st_dev, info.st_ino) == identity
        metadata_reads.append(descriptor)
        return SimpleNamespace(f_blocks=262144, f_bfree=261888, f_frsize=4096)
    monkeypatch.setattr(guard.os, "fstatvfs", quota_metadata)
    monkeypatch.setattr(guard, "available_bytes", lambda _path: 32 * guard.GIB)
    code = ("import os,time;os.mkdir('during');os.chmod('during',0);"
            "print('OWN MODE ZERO',flush=True);time.sleep(.4)")
    try:
        result, tail = guard.supervise([sys.executable, "-I", "-B", "-c", code],
            environment={"PATH": os.defpath}, cwd=root, budget_root=root,
            budget_bytes=64 * guard.MIB, deadline=3, data_root=tmp_path,
            free_floor=0, measurement_mode=mode, poll_seconds=.02)
        assert result["exit_code"] == 0 and result["stop_reason"] is None
        assert result["measurement_mode"] == mode and b"OWN MODE ZERO" in tail
        assert metadata_reads
        if mode == "apfs_volume_used":
            assert len(metadata_reads) >= 2 and result["peak_temporary_bytes"] == guard.MIB
        else:
            assert result["peak_temporary_bytes"] == 0
        assert hidden.stat().st_mode & 0o777 == 0
        assert (root / "during").stat().st_mode & 0o777 == 0
    finally:
        guard.cleanup_owned_tree(root, identity, resources_closed=True)


def test_closed_cleanup_restores_only_pinned_directories_not_hardlinks_or_symlinks(
        tmp_path, monkeypatch):
    root = tmp_path / "corpus"
    root.mkdir(mode=0o700)
    protected = root / "Jarvis"
    protected.mkdir()
    (protected / "own").write_bytes(b"OWN")
    outside = tmp_path / "retained-file"
    outside.write_bytes(b"SYNTHETIC PRESERVE")
    outside.chmod(0o400)
    os.link(outside, root / "hardlink")
    (root / "alias").symlink_to(outside)
    protected.chmod(0)
    identity = root.stat().st_dev, root.stat().st_ino
    chmod = guard.os.fchmod
    def directories_only(descriptor, mode):
        assert guard.stat.S_ISDIR(os.fstat(descriptor).st_mode)
        return chmod(descriptor, mode)
    monkeypatch.setattr(guard.os, "fchmod", directories_only)
    with pytest.raises(guard.GuardError, match="recursos pendientes"):
        guard.cleanup_owned_tree(root, identity, resources_closed=False)
    assert protected.stat().st_mode & 0o777 == 0
    guard.cleanup_owned_tree(root, identity, resources_closed=True)
    assert not root.exists()
    assert outside.read_bytes() == b"SYNTHETIC PRESERVE"
    assert outside.stat().st_mode & 0o777 == 0o400


def test_closed_cleanup_rejects_replaced_root_before_changing_permissions(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir(mode=0o700)
    identity = root.stat().st_dev, root.stat().st_ino
    original = tmp_path / "original"
    root.rename(original)
    root.mkdir(mode=0o700)
    (root / "unrelated").write_bytes(b"OWN REPLACEMENT")
    with pytest.raises(guard.GuardError, match="cambió antes"):
        guard.cleanup_owned_tree(root, identity, resources_closed=True)
    assert (root / "unrelated").read_bytes() == b"OWN REPLACEMENT"
    assert original.is_dir()


def outcome_fixture(tmp_path):
    control = tmp_path / "jarvis-test-evidence-own"
    control.mkdir(mode=0o700)
    pending = tmp_path / "cleanup-uncertain.json"
    guard.write_json(pending, {"format": guard.FORMAT})
    root = tmp_path / "own-work"
    root.mkdir(mode=0o700)
    return dict(control=control, pending_path=pending, pending_identity=pending.stat(),
        options=options(profile="ci", full=True), before={"own": 1}, after={"own": 1},
        volume=None, run_root=root, run_identity=(root.stat().st_dev, root.stat().st_ino),
        closed=True, result={"exit_code": 7, "measurement_mode": "ci_available_delta"},
        tail=b"OWN ASSERTION DIAGNOSTIC\n")


def test_cleanup_error_never_masks_child_tail_receipt_or_pending_intent(tmp_path, monkeypatch):
    arguments = outcome_fixture(tmp_path)
    def denied(*_args, **_kwargs):
        raise PermissionError(13, "OWN synthetic Jarvis")
    monkeypatch.setattr(guard, "cleanup_owned_tree", denied)
    assert not guard.finalize_evidence(**arguments)
    receipt = guard.read_private_json(arguments["control"] / "receipt.json")
    assert receipt["cleanup_status"] == "uncertain" and receipt["cleanup_error"]
    assert receipt["result"]["exit_code"] == 7
    assert (arguments["control"] / "tail.log").read_bytes() == arguments["tail"]
    assert arguments["pending_path"].is_file() and arguments["run_root"].is_dir()


def test_closed_mode_zero_cleanup_has_durable_child_failure_receipt(tmp_path):
    arguments = outcome_fixture(tmp_path)
    folder = arguments["run_root"] / "Jarvis"
    folder.mkdir()
    (folder / "fixture").write_bytes(b"OWN")
    folder.chmod(0)
    assert guard.finalize_evidence(**arguments)
    receipt = guard.read_private_json(arguments["control"] / "receipt.json")
    assert receipt["cleanup_status"] == "confirmed" and receipt["result"]["exit_code"] == 7
    assert not arguments["pending_path"].exists() and not arguments["run_root"].exists()


def test_receipt_io_failure_still_emits_ci_tail_and_orphan_blocks_reuse(
        tmp_path, monkeypatch, capsys):
    arguments = outcome_fixture(tmp_path)
    def no_receipt(*_args, **_kwargs):
        raise OSError(28, "OWN synthetic full diagnostic device")
    monkeypatch.setattr(guard, "write_json", no_receipt)
    with pytest.raises(OSError):
        guard.finalize_evidence(**arguments)
    assert arguments["tail"].decode() in capsys.readouterr().err
    assert (arguments["control"] / "tail.log").read_bytes() == arguments["tail"]
    with pytest.raises(guard.GuardError, match="Cierre sin confirmar"):
        guard.reject_orphaned_evidence(tmp_path)
