"""Adversarial source/archive checks for the independent stdlib foundation."""

import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("clean_base", ROOT / "script/check_clean_base.py")
assert SPEC is not None and SPEC.loader is not None
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def fixture_tree(root):
    (root / "src/aegis_core").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname="aegis-swarm"\nversion="1.0.0"\ndependencies=[]\n',
    )
    (root / "src/aegis_core/__init__.py").write_text("import asyncio\n")
    return root


def wheel(path, *, requires="", extra=None, source="import asyncio\n"):
    members = {
        "aegis_core/__init__.py": source,
        "aegis_swarm-1.0.0.dist-info/METADATA":
            "Metadata-Version: 2.3\nName: aegis-swarm\nVersion: 1.0.0\n" + requires,
        "aegis_swarm-1.0.0.dist-info/WHEEL":
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    members.update(extra or {})
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


def test_current_tree_is_own_stdlib_base():
    report = guard.check_tree(ROOT)
    assert report["name"] == "aegis-swarm" and report["source_files"]


@pytest.mark.parametrize("source", [
    "import httpx\n", "from langgraph.graph import StateGraph\n",
    "import nvidia.riva.client\n", "from old_backend import Engine\n",
    "import subprocess\nsubprocess.run(['openclaw', 'gateway'])\n",
    "__import__('httpx')\n", "exec('import httpx')\n",
    "from importlib import import_module as load\nload('httpx')\n",
])
def test_external_imports_and_runtime_launches_fail_closed(tmp_path, source):
    root = fixture_tree(tmp_path)
    (root / "src/aegis_core/runtime.py").write_text(source)
    with pytest.raises(guard.CleanBaseError):
        guard.check_tree(root)


def test_research_documentation_does_not_become_runtime(tmp_path):
    root = fixture_tree(tmp_path)
    (root / "docs").mkdir()
    (root / "docs/reference.md").write_text("OpenClaw is a research reference, not a runtime.")
    assert guard.check_tree(root)["source_files"] == ["aegis_core/__init__.py"]


@pytest.mark.parametrize("legacy", [
    "native/AegisAudio/Package.swift", "src/aegis_core/runtime/daemon.py",
    "integrations/mcp/package.json", "script/build_and_run.sh",
])
def test_retired_product_files_are_rejected(tmp_path, legacy):
    root = fixture_tree(tmp_path)
    path = root / legacy
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("retired")
    with pytest.raises(guard.CleanBaseError, match="retirado"):
        guard.check_tree(root)


def test_product_dependencies_and_fake_entrypoints_are_rejected(tmp_path):
    root = fixture_tree(tmp_path)
    manifest = root / "pyproject.toml"
    manifest.write_text(manifest.read_text().replace("dependencies=[]", 'dependencies=["mlx"]'))
    with pytest.raises(guard.CleanBaseError, match="dependencias"):
        guard.check_tree(root)
    manifest.write_text(manifest.read_text().replace('dependencies=["mlx"]', "dependencies=[]")
                        + '\n[project.scripts]\njarvis="aegis_core.cli:main"\n')
    with pytest.raises(guard.CleanBaseError, match="CLI"):
        guard.check_tree(root)


def test_clean_wheel_matches_the_qualified_source(tmp_path):
    root = fixture_tree(tmp_path / "project")
    path = wheel(tmp_path / "own.whl")
    assert guard.check_wheel(path, root)["runtime_dependencies"] == 0
    (root / "src/aegis_core/__init__.py").write_text("import json\n")
    with pytest.raises(guard.CleanBaseError, match="corresponde"):
        guard.check_wheel(path, root)


@pytest.mark.parametrize(("requires", "extra", "source"), [
    ("Requires-Dist: httpx\n", {}, "import asyncio\n"),
    ("", {"openclaw/gateway.js": "foreign"}, "import asyncio\n"),
    ("", {"aegis_core/model.onnx": "foreign"}, "import asyncio\n"),
    ("", {"aegis_swarm-1.0.0.dist-info/old_runtime.mjs": "foreign"}, "import asyncio\n"),
    ("", {"aegis_swarm-1.0.0.dist-info/legacy.so": "foreign"}, "import asyncio\n"),
    ("", {"../outside.py": "foreign"}, "import asyncio\n"),
    ("", {}, "import httpx\n"),
])
def test_wheel_rejects_dependencies_foreign_resources_and_traversal(
    tmp_path, requires, extra, source,
):
    with pytest.raises(guard.CleanBaseError):
        guard.check_wheel(wheel(tmp_path / "bad.whl", requires=requires,
                                extra=extra, source=source))


@pytest.mark.parametrize("suffix", ["js", "mjs", "cjs"])
def test_sdist_rejects_external_runtime_without_extracting_it(tmp_path, suffix):
    path = tmp_path / "source.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo(f"aegis_swarm-1.0.0/docs/reference/runtime.{suffix}")
        member.size = 4
        archive.addfile(member, io.BytesIO(b"code"))
    with pytest.raises(guard.CleanBaseError, match="ajeno"):
        guard.check_sdist(path)
    assert not (tmp_path / "openclaw").exists()


def test_sdist_requires_the_helpers_its_tests_need(tmp_path):
    path = tmp_path / "incomplete.tar.gz"
    content = b"Metadata-Version: 2.3\nName: aegis-swarm\nVersion: 1.0.0\n"
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo("aegis_swarm-1.0.0/PKG-INFO")
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
    with pytest.raises(guard.CleanBaseError, match="repetir"):
        guard.check_sdist(path)


def test_gate_setup_reports_only_the_python_profile(tmp_path):
    repository = tmp_path / "repo"
    (repository / "script").mkdir(parents=True)
    (repository / ".githooks").mkdir()
    for relative in ("script/setup_git_gates.sh", ".githooks/pre-commit", ".githooks/pre-push"):
        target = repository / relative
        shutil.copyfile(ROOT / relative, target)
        target.chmod(0o755)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("GIT_")}
    subprocess.run(["git", "init", "-b", "main", str(repository)], check=True,
                   env=environment, capture_output=True)
    script = repository / "script/setup_git_gates.sh"
    inactive = subprocess.run([str(script), "--check"], cwd=repository, env=environment,
                              capture_output=True, text=True)
    assert inactive.returncode == 2
    installed = subprocess.run([str(script), "--install"], cwd=repository, env=environment,
                               capture_output=True, text=True, check=True)
    assert "profile=jarvis_base" in installed.stdout and "native=pending" in installed.stdout
    active = subprocess.run([str(script), "--check"], cwd=repository, env=environment,
                            capture_output=True, text=True, check=True)
    assert active.stdout == installed.stdout


def test_clean_base_cli_does_not_claim_native_app():
    completed = subprocess.run([sys.executable, str(ROOT / "script/check_clean_base.py")],
                               capture_output=True, text=True, check=True)
    assert "runtime_dependencies=0" in completed.stdout and "native_app=pending" in completed.stdout
