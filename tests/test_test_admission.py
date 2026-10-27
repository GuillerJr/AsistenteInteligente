"""Small admission checks; synthetic metadata never launches an admitted suite."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import conftest as admission
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def permit(tmp_path):
    control = tmp_path / "control"
    run_root = tmp_path / "run"
    control.mkdir(mode=0o700)
    run_root.mkdir(mode=0o700)
    path = control / "admission.json"
    value = {
        "format": "jarvis-test-guard/1", "profile": "local", "kind": "python",
        "selection": "tests/test_test_admission.py", "full": False,
        "nonce": "a" * 48, "source_fingerprint": "b" * 64,
        "run_root": str(run_root), "guard_pid": os.getppid(),
    }
    environment = {
        "JARVIS_TEST_GUARD_PROFILE": "local",
        "JARVIS_TEST_GUARD_SELECTION": value["selection"],
        "JARVIS_TEST_GUARD_RUN_ROOT": str(run_root),
        "JARVIS_TEST_GUARD_ADMISSION": str(path),
        "JARVIS_TEST_GUARD_NONCE": value["nonce"],
    }

    def publish():
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    publish()
    return SimpleNamespace(path=path, value=value, environment=environment, publish=publish,
                           root=run_root, control=control)


@pytest.fixture
def fixed_source(monkeypatch):
    # Unit-only metadata dependency: this does not run pytest or admit fixtures.
    identity = {"head": "c" * 40, "fingerprint": "b" * 64}
    monkeypatch.setattr(admission._GUARD, "fingerprint", lambda root: identity)
    return identity


def test_environment_alone_cannot_admit_pytest(permit):
    permit.path.unlink()
    with pytest.raises(OSError):
        admission.validate_guard_admission(ROOT, permit.environment)


def test_direct_pytest_without_guard_is_rejected_before_source_access(monkeypatch):
    monkeypatch.setattr(admission._GUARD, "fingerprint",
                        lambda root: pytest.fail("an unguarded run inspected sources"))
    with pytest.raises(ValueError, match="pytest directo"):
        admission.validate_guard_admission(ROOT, {})


def test_coherent_local_metadata_is_validated_without_starting_tests(permit, fixed_source):
    assert admission.validate_guard_admission(ROOT, permit.environment) == permit.value


@pytest.mark.parametrize(("field", "value"), [
    ("format", "jarvis-test-guard/0"), ("profile", "ci"), ("kind", "swift"),
    ("full", True), ("full", 1), ("nonce", "a" * 47), ("guard_pid", True),
    ("guard_pid", -1), ("source_fingerprint", "d" * 64),
    ("selection", "tests"), ("run_root", "/"),
])
def test_incoherent_or_full_local_admission_fails_closed(permit, fixed_source, field, value):
    permit.value[field] = value
    permit.publish()
    with pytest.raises(ValueError):
        admission.validate_guard_admission(ROOT, permit.environment)


@pytest.mark.parametrize("mode", [0o644, 0o660, 0o400])
def test_admission_permissions_are_exact(permit, mode):
    permit.path.chmod(mode)
    with pytest.raises(ValueError, match="privada"):
        admission.validate_guard_admission(ROOT, permit.environment)


def test_hardlinked_admission_is_rejected(permit):
    os.link(permit.path, permit.control / "other")
    with pytest.raises(ValueError, match="único"):
        admission.validate_guard_admission(ROOT, permit.environment)


def test_symlink_admission_is_rejected(permit):
    target = permit.control / "original"
    permit.path.rename(target)
    permit.path.symlink_to(target)
    with pytest.raises(ValueError, match="físicas"):
        admission.validate_guard_admission(ROOT, permit.environment)


@pytest.mark.parametrize("problem", ["extra_field", "missing_field", "oversized", "invalid_json"])
def test_admission_is_a_small_exact_json_record(permit, fixed_source, problem):
    if problem == "extra_field":
        permit.value["bootstrap_override"] = True
    elif problem == "missing_field":
        permit.value.pop("nonce")
    permit.publish()
    if problem == "oversized":
        permit.path.write_bytes(b" " * (admission._MAX_ADMISSION_BYTES + 1))
    elif problem == "invalid_json":
        permit.path.write_text("{")
    with pytest.raises(ValueError):
        admission.validate_guard_admission(ROOT, permit.environment)


def test_run_directory_must_be_private(permit):
    permit.root.chmod(0o755)
    with pytest.raises(ValueError, match="privada"):
        admission.validate_guard_admission(ROOT, permit.environment)


def test_guard_pid_cannot_be_replayed_by_a_different_parent(permit, fixed_source):
    permit.value["guard_pid"] = os.getpid()
    permit.publish()
    with pytest.raises(ValueError, match="hijo directo"):
        admission.validate_guard_admission(ROOT, permit.environment)


def test_guard_must_still_be_alive(permit, fixed_source, monkeypatch):
    parents = iter((permit.value["guard_pid"], 1))
    monkeypatch.setattr(admission.os, "getppid", lambda: next(parents))
    with pytest.raises(ValueError, match="se cerró"):
        admission.validate_guard_admission(ROOT, permit.environment)


def test_guard_liveness_does_not_signal_its_unsandboxed_parent(permit, fixed_source, monkeypatch):
    monkeypatch.setattr(admission.os, "kill",
                        lambda *args: pytest.fail("must not signal the guard outside Seatbelt"))
    assert admission.validate_guard_admission(ROOT, permit.environment) == permit.value


def test_ci_profile_needs_full_and_exact_workspace_sha(permit, fixed_source):
    permit.value.update(profile="ci", full=True, selection="tests")
    permit.environment.update(JARVIS_TEST_GUARD_PROFILE="ci", JARVIS_TEST_GUARD_SELECTION="tests")
    permit.publish()
    with pytest.raises(ValueError, match="GitHub Actions"):
        admission.validate_guard_admission(ROOT, permit.environment)
    permit.environment.update(GITHUB_ACTIONS="true", GITHUB_SHA=fixed_source["head"],
                              GITHUB_WORKSPACE=str(ROOT))
    assert admission.validate_guard_admission(ROOT, permit.environment)["profile"] == "ci"
    permit.environment["GITHUB_SHA"] = "d" * 40
    with pytest.raises(ValueError, match="GitHub Actions"):
        admission.validate_guard_admission(ROOT, permit.environment)


@pytest.mark.parametrize("selection", ["tests", "tests/../outside.py", "all", "/tmp/test.py"])
def test_local_selection_cannot_expand_to_a_suite_or_escape(permit, fixed_source, selection):
    permit.value["selection"] = permit.environment["JARVIS_TEST_GUARD_SELECTION"] = selection
    permit.publish()
    with pytest.raises(ValueError, match="focales"):
        admission.validate_guard_admission(ROOT, permit.environment)


def test_configure_binds_exact_pytest_arguments(permit, fixed_source, monkeypatch):
    monkeypatch.setattr(admission.os, "environ", permit.environment)
    config = SimpleNamespace(args=["tests/test_other.py"])
    with pytest.raises(pytest.UsageError, match="selección de pytest"):
        admission.pytest_configure(config)


def test_collection_limit_precedes_any_fixture(permit, fixed_source, monkeypatch):
    monkeypatch.setattr(admission.os, "environ", permit.environment)
    config = SimpleNamespace(args=["tests/test_test_admission.py"])
    admission.pytest_configure(config)
    admission.pytest_collection_modifyitems(config, [object()] * 100)
    with pytest.raises(pytest.UsageError, match="100 casos"):
        admission.pytest_collection_modifyitems(config, [object()] * 101)


def test_unguarded_nested_pytest_only_collects_a_synthetic_rejection(tmp_path):
    # No real project fixtures or inherited guard/CI metadata enter this child.
    project = tmp_path / "nested"
    (project / "tests").mkdir(parents=True)
    (project / "script").mkdir()
    shutil.copyfile(ROOT / "tests/conftest.py", project / "tests/conftest.py")
    shutil.copyfile(ROOT / "script/test_resource_guard.py",
                    project / "script/test_resource_guard.py")
    (project / "pytest.ini").write_text("[pytest]\naddopts = -p no:cacheprovider\n")
    (project / "tests/test_canary.py").write_text(
        "from pathlib import Path\nimport pytest\n"
        "@pytest.fixture(autouse=True)\ndef forbidden():\n"
        "    Path('FIXTURE_RAN').write_text('unexpected')\n"
        "def test_canary():\n    assert False\n",
    )
    result = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-q", "tests/test_canary.py"], cwd=project,
        env={"PATH": os.defpath, "HOME": str(tmp_path), "TMPDIR": str(tmp_path) + "/",
             "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
             "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 4 and "pytest directo" in result.stderr
    assert not (project / "FIXTURE_RAN").exists()
    assert not list(project.rglob("__pycache__"))


def test_static_hooks_do_not_launch_tests_builds_or_bytecode_caches():
    for name in ("pre-commit", "pre-push"):
        source = (ROOT / ".githooks" / name).read_text()
        assert "fingerprint" in source and "--no-cache" in source
        assert "validación completa pendiente de CI remota" in source
        assert all(command not in source for command in
                   ("-m pytest", "compileall", "uv build", "test_desktop.sh"))


def test_syntax_validation_uses_ram_and_rejects_invalid_test_source(tmp_path):
    spec = importlib.util.spec_from_file_location("admission_clean_base",
                                                ROOT / "script/check_clean_base.py")
    assert spec is not None and spec.loader is not None
    clean = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(clean)
    folder = tmp_path / "tests"
    folder.mkdir()
    source = folder / "test_marker.py"
    source.write_text("MARKER = 1\n")
    assert clean.check_python_syntax(tmp_path) == 1
    assert not list(tmp_path.rglob("__pycache__"))
    source.write_text("def broken(:\n")
    with pytest.raises(SyntaxError):
        clean.check_python_syntax(tmp_path)
