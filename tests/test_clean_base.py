"""Adversarial source/archive checks for the independent foundation."""

import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import threading
import venv
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("clean_base", ROOT / "script/check_clean_base.py")
assert SPEC is not None and SPEC.loader is not None
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def fixture_tree(root):
    (root / "src/jarvis_core").mkdir(parents=True)
    (root / "tests/support").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname="jarvis-core"\nversion="1.0.0"\ndependencies=[]\n',
    )
    (root / "src/jarvis_core/__init__.py").write_text("import asyncio\n")
    (root / "tests/support/fixture_values.py").write_text("MARKER = 'qualified fixture'\n")
    (root / "tests/test_fixture.py").write_text(
        "from support.fixture_values import MARKER\n\n"
        "def test_marker():\n    assert MARKER == 'qualified fixture'\n",
    )
    return root


def wheel(path, *, requires="", extra=None, source="import asyncio\n"):
    members = {
        "jarvis_core/__init__.py": source,
        "jarvis_core-1.0.0.dist-info/METADATA":
            "Metadata-Version: 2.3\nName: jarvis-core\nVersion: 1.0.0\n" + requires,
        "jarvis_core-1.0.0.dist-info/WHEEL":
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    members.update(extra or {})
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


def test_current_tree_is_own_stdlib_base():
    report = guard.check_tree(ROOT)
    assert report["name"] == "jarvis-core" and report["source_files"]


@pytest.mark.parametrize("source", [
    "import httpx\n", "from langgraph.graph import StateGraph\n",
    "import nvidia.riva.client\n", "from old_backend import Engine\n",
    "import subprocess\nsubprocess.run(['openclaw', 'gateway'])\n",
    "__import__('httpx')\n", "exec('import httpx')\n",
    "from importlib import import_module as load\nload('httpx')\n",
])
def test_external_imports_and_runtime_launches_fail_closed(tmp_path, source):
    root = fixture_tree(tmp_path)
    (root / "src/jarvis_core/runtime.py").write_text(source)
    with pytest.raises(guard.CleanBaseError):
        guard.check_tree(root)


def test_research_documentation_does_not_become_runtime(tmp_path):
    root = fixture_tree(tmp_path)
    (root / "docs").mkdir()
    (root / "docs/reference.md").write_text("OpenClaw is a research reference, not a runtime.")
    assert guard.check_tree(root)["source_files"] == ["jarvis_core/__init__.py"]


def test_finder_metadata_does_not_become_package_source(tmp_path):
    root = fixture_tree(tmp_path)
    (root / "src/.DS_Store").write_bytes(b"finder-metadata")
    assert guard.check_tree(root)["source_files"] == ["jarvis_core/__init__.py"]


@pytest.mark.parametrize("legacy", [
    "native/JarvisAudio/Package.swift", "src/jarvis_core/runtime/daemon.py",
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
                        + '\n[project.scripts]\njarvis="jarvis_core.cli:main"\n')
    with pytest.raises(guard.CleanBaseError, match="CLI"):
        guard.check_tree(root)


def test_clean_wheel_matches_the_qualified_source(tmp_path):
    root = fixture_tree(tmp_path / "project")
    path = wheel(tmp_path / "own.whl")
    assert guard.check_wheel(path, root)["runtime_dependencies"] == 0
    (root / "src/jarvis_core/__init__.py").write_text("import json\n")
    with pytest.raises(guard.CleanBaseError, match="corresponde"):
        guard.check_wheel(path, root)


@pytest.mark.parametrize(("requires", "extra", "source"), [
    ("Requires-Dist: httpx\n", {}, "import asyncio\n"),
    ("", {"openclaw/gateway.js": "foreign"}, "import asyncio\n"),
    ("", {"jarvis_core/model.onnx": "foreign"}, "import asyncio\n"),
    ("", {"jarvis_core-1.0.0.dist-info/old_runtime.mjs": "foreign"}, "import asyncio\n"),
    ("", {"jarvis_core-1.0.0.dist-info/legacy.so": "foreign"}, "import asyncio\n"),
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
        member = tarfile.TarInfo(f"jarvis_core-1.0.0/docs/reference/runtime.{suffix}")
        member.size = 4
        archive.addfile(member, io.BytesIO(b"code"))
    with pytest.raises(guard.CleanBaseError, match="ajeno"):
        guard.check_sdist(path)
    assert not (tmp_path / "openclaw").exists()


def test_sdist_requires_the_helpers_its_tests_need(tmp_path):
    path = tmp_path / "incomplete.tar.gz"
    content = b"Metadata-Version: 2.3\nName: jarvis-core\nVersion: 1.0.0\n"
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo("jarvis_core-1.0.0/PKG-INFO")
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
    with pytest.raises(guard.CleanBaseError, match="repetir"):
        guard.check_sdist(path)


def native_sdist(path, root, *, omit=None, overrides=None):
    entries = {name: b"fixture" for name in guard.SDIST_REQUIRED}
    entries["PKG-INFO"] = b"Metadata-Version: 2.3\nName: jarvis-core\nVersion: 1.0.0\n"
    entries["pyproject.toml"] = (root / "pyproject.toml").read_bytes()
    entries.update({p.relative_to(root).as_posix(): p.read_bytes()
                    for p in root.rglob("*.swift")})
    entries.update({p.relative_to(root).as_posix(): p.read_bytes()
                    for p in (root / "tests").rglob("*.py")})
    entries["src/jarvis_core/__init__.py"] = (root / "src/jarvis_core/__init__.py").read_bytes()
    entries.update(overrides or {})
    if omit:
        entries.pop(omit)
    with tarfile.open(path, "w:gz") as archive:
        for name, content in entries.items():
            item = tarfile.TarInfo("jarvis_core-1.0.0/" + name)
            item.size = len(content)
            archive.addfile(item, io.BytesIO(content))
    return path


def test_sdist_rejects_missing_python_helper_required_by_packaged_tests(tmp_path):
    root = fixture_tree(tmp_path / "project")
    helper = "tests/support/fixture_values.py"
    assert guard.check_sdist(native_sdist(tmp_path / "complete.tar.gz", root), root)["files"]
    incomplete = native_sdist(tmp_path / "missing-helper.tar.gz", root, omit=helper)
    with pytest.raises(guard.CleanBaseError, match="pruebas"):
        guard.check_sdist(incomplete, root)


def test_sdist_rejects_stale_python_test_helper_instead_of_qualifying_other_bytes(tmp_path):
    root = fixture_tree(tmp_path / "project")
    helper = "tests/support/fixture_values.py"
    stale = native_sdist(tmp_path / "stale-helper.tar.gz", root,
                         overrides={helper: b"MARKER = 'previous fixture'\n"})
    with pytest.raises(guard.CleanBaseError, match="pruebas"):
        guard.check_sdist(stale, root)


def test_sdist_carries_exact_own_native_source_and_still_rejects_retired_swift(tmp_path):
    root = fixture_tree(tmp_path / "project")
    relative = "native/JarvisDesktop/Package.swift"
    file = root / relative
    file.parent.mkdir(parents=True)
    file.write_text("// Original native package fixture\n")
    assert guard.check_sdist(native_sdist(tmp_path / "ok.tar.gz", root), root)["files"]
    with pytest.raises(guard.CleanBaseError, match="nativas"):
        guard.check_sdist(native_sdist(tmp_path / "omitted.tar.gz", root, omit=relative), root)
    with pytest.raises(guard.CleanBaseError, match="nativas"):
        guard.check_sdist(native_sdist(tmp_path / "stale.tar.gz", root,
                                      overrides={relative: b"stale"}), root)
    with pytest.raises(guard.CleanBaseError, match="ajeno"):
        guard.check_sdist(native_sdist(tmp_path / "retired.tar.gz", root,
                                      overrides={"native/JarvisAudio/Package.swift": b"old"}))


def test_native_parent_symlink_is_rejected_before_reading_sources(tmp_path):
    root = fixture_tree(tmp_path / "project")
    outside = tmp_path / "outside"
    (outside / "JarvisDesktop").mkdir(parents=True)
    (root / "native").symlink_to(outside, target_is_directory=True)
    with pytest.raises(guard.CleanBaseError, match="enlaces"):
        guard.check_tree(root)


def test_unregistered_native_tree_is_not_treated_as_own_source(tmp_path):
    root = fixture_tree(tmp_path)
    (root / "native/foreign-product").mkdir(parents=True)
    with pytest.raises(guard.CleanBaseError, match="nativo ajeno"):
        guard.check_tree(root)


def test_sdist_rejects_file_with_only_root_component(tmp_path):
    path = tmp_path / "root-file.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo("jarvis_core-1.0.0")
        archive.addfile(member, io.BytesIO())
    with pytest.raises(guard.CleanBaseError, match="ruta relativa"):
        guard.check_sdist(path)


def test_gate_setup_reports_static_checks_and_remote_full_qualification(tmp_path):
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
    assert "profile=jarvis_base" in installed.stdout and "native=swiftpm" in installed.stdout
    assert "pre_commit=static pre_push=static full_qualification=remote_ci" in installed.stdout
    active = subprocess.run([str(script), "--check"], cwd=repository, env=environment,
                            capture_output=True, text=True, check=True)
    assert active.stdout == installed.stdout


def test_clean_base_cli_does_not_claim_native_app():
    completed = subprocess.run([sys.executable, str(ROOT / "script/check_clean_base.py")],
                               capture_output=True, text=True, check=True)
    assert "runtime_dependencies=7" in completed.stdout
    assert "native_app=development" in completed.stdout


def test_reviewed_crypto_import_requires_exact_declared_dependency(tmp_path):
    root = fixture_tree(tmp_path)
    source = root / "src/jarvis_core/runtime.py"
    source.write_text("from cryptography.hazmat.primitives.asymmetric.ed25519 "
                      "import Ed25519PublicKey\n")
    with pytest.raises(guard.CleanBaseError, match="import externo"):
        guard.check_tree(root)
    manifest = root / "pyproject.toml"
    manifest.write_text(manifest.read_text().replace(
        "dependencies=[]", 'dependencies=["cryptography==50.0.2"]'))
    assert guard.check_tree(root)["source_files"]
    # A transitive dependency is not an authorization to use arbitrary cffi APIs.
    source.write_text("import cffi\n")
    with pytest.raises(guard.CleanBaseError, match="import externo"):
        guard.check_tree(root)


@pytest.mark.parametrize("requirement", [
    "cryptography", "cryptography>=50", "cryptography==50.0.1",
    "cryptography[ssh]==50.0.2", "cryptography @ https://example.com/package.whl",
    'cryptography==50.0.2; python_version>="3.11"',
])
def test_crypto_dependency_is_not_a_general_dependency_escape(tmp_path, requirement):
    root = fixture_tree(tmp_path)
    manifest = root / "pyproject.toml"
    import json
    manifest.write_text(manifest.read_text().replace(
        "dependencies=[]", "dependencies=" + json.dumps([requirement])))
    with pytest.raises(guard.CleanBaseError, match="dependencias"):
        guard.check_tree(root)
    with pytest.raises(guard.CleanBaseError, match="dependencias"):
        guard.check_wheel(wheel(tmp_path / "bad.whl", requires=f"Requires-Dist: {requirement}\n"))


def test_crypto_wheel_metadata_must_match_declared_project(tmp_path):
    root = fixture_tree(tmp_path / "project")
    metadata = "Requires-Dist: cryptography==50.0.2\n"
    artifact = wheel(tmp_path / "reviewed.whl", requires=metadata)
    with pytest.raises(guard.CleanBaseError, match="dependencias del wheel"):
        guard.check_wheel(artifact, root)
    manifest = root / "pyproject.toml"
    manifest.write_text(manifest.read_text().replace(
        "dependencies=[]", 'dependencies=["cryptography==50.0.2"]'))
    assert guard.check_wheel(artifact, root)["runtime_dependencies"] == 1
    with pytest.raises(guard.CleanBaseError, match="dependencias del wheel"):
        guard.check_wheel(wheel(tmp_path / "missing.whl"), root)


def test_crypto_sdist_metadata_must_match_its_manifest(tmp_path):
    root = fixture_tree(tmp_path / "project")
    manifest = root / "pyproject.toml"
    manifest.write_text(manifest.read_text().replace(
        "dependencies=[]", 'dependencies=["cryptography==50.0.2"]'))
    with pytest.raises(guard.CleanBaseError, match="dependencias del sdist"):
        guard.check_sdist(native_sdist(tmp_path / "missing.tar.gz", root), root)
    metadata = ("Metadata-Version: 2.3\nName: jarvis-core\nVersion: 1.0.0\n"
                "Requires-Dist: cryptography==50.0.2\n")
    own = native_sdist(tmp_path / "ok.tar.gz", root, overrides={"PKG-INFO": metadata.encode()})
    assert guard.check_sdist(own, root)["files"]


@pytest.fixture
def locked_smoke_cache(tmp_path):
    """One synthetic wheel URL, with no package-index metadata or external network."""
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required for the offline package boundary")
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname="jarvis-smoke-root"\nversion="1.0.0"\n'
        'requires-python=">=3.11"\ndependencies=["jarvis-smoke-dependency==1.0.0"]\n')
    artifact = io.BytesIO()
    with zipfile.ZipFile(artifact, "w") as archive:
        for name, data in {
            "jarvis_smoke_dependency/__init__.py": "MARKER = 'locked cache fixture'\n",
            "jarvis_smoke_dependency-1.0.0.dist-info/METADATA":
                "Metadata-Version: 2.3\nName: jarvis-smoke-dependency\nVersion: 1.0.0\n",
            "jarvis_smoke_dependency-1.0.0.dist-info/WHEEL":
                "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            "jarvis_smoke_dependency-1.0.0.dist-info/RECORD": "",
        }.items():
            archive.writestr(name, data)
    payload = artifact.getvalue()
    requests = []
    name = "/jarvis_smoke_dependency-1.0.0-py3-none-any.whl"

    class WheelOnly(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if self.path != name:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), WheelOnly)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}{name}"
        (root / "uv.lock").write_text(
            'version=1\nrevision=3\nrequires-python=">=3.11"\n'
            '[[package]]\nname="jarvis-smoke-dependency"\nversion="1.0.0"\n'
            'source={registry="https://pypi.org/simple"}\n'
            f'wheels=[{{url={json.dumps(url)},hash="sha256:'
            f'{hashlib.sha256(payload).hexdigest()}",size={len(payload)}}}]\n'
            '[[package]]\nname="jarvis-smoke-root"\nversion="1.0.0"\n'
            'source={virtual="."}\ndependencies=[{name="jarvis-smoke-dependency"}]\n'
            '[package.metadata]\n'
            'requires-dist=[{name="jarvis-smoke-dependency",specifier="==1.0.0"}]\n')
        environment = {"PATH": os.defpath, "LANG": "C.UTF-8",
                       "UV_CACHE_DIR": str(tmp_path / "cache"), "UV_PYTHON_DOWNLOADS": "never"}
        subprocess.run([
            uv, "--no-config", "sync", "--locked", "--no-dev", "--no-build",
            "--no-install-project", "--project", str(root), "--python", sys.executable,
        ], env={**environment, "UV_PROJECT_ENVIRONMENT": str(tmp_path / "prepared")},
            check=True, capture_output=True, text=True, timeout=30)
        assert requests == [name]  # The lock did not request a simple index.
        target = tmp_path / "isolated"
        venv.EnvBuilder(with_pip=False).create(target)
        yield uv, root, target, environment, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_offline_smoke_uses_lock_artifacts_without_index_metadata(locked_smoke_cache, tmp_path):
    uv, root, target, environment, requests = locked_smoke_cache
    original = {path.name: path.read_bytes() for path in root.iterdir()}
    requirements = tmp_path / "requirements.txt"
    exported = subprocess.run([
        uv, "--no-config", "export", "--offline", "--locked", "--no-dev",
        "--no-emit-project", "--format", "requirements.txt", "--project", str(root),
        "--python", sys.executable,
    ], env=environment, check=True, capture_output=True, timeout=30)
    requirements.write_bytes(exported.stdout)
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    # Causal reproduction: hashes alone do not supply a missing index response.
    old = subprocess.run([
        uv, "--no-config", "pip", "install", "--offline", "--require-hashes",
        "--python", str(python), "--requirements", str(requirements),
    ], env=environment, capture_output=True, text=True, timeout=30)
    assert old.returncode != 0 and "No solution found" in old.stderr
    guard._sync_smoke_dependencies(uv, root, target, tmp_path, environment)
    check = subprocess.run([
        str(python), "-I", "-c",
        "import importlib.util, jarvis_smoke_dependency as dependency; "
        "assert dependency.MARKER == 'locked cache fixture'; "
        "assert importlib.util.find_spec('jarvis_smoke_root') is None",
    ], check=True, env=environment, capture_output=True, timeout=10)
    assert check.returncode == 0
    assert {path.name: path.read_bytes() for path in root.iterdir()} == original
    assert len(requests) == 1  # Both attempts after cache preparation stayed offline.
    assert "UV_PROJECT_ENVIRONMENT" not in environment


@pytest.mark.parametrize("damage", ["hash", "cache", "lock"])
def test_offline_smoke_fails_closed_with_diagnostic(locked_smoke_cache, tmp_path, damage):
    uv, root, target, environment, requests = locked_smoke_cache
    if damage == "hash":
        lock = root / "uv.lock"
        content = lock.read_text()
        start = content.index('hash="sha256:') + len('hash="sha256:')
        lock.write_text(content[:start] + "0" * 64 + content[start + 64:])
    elif damage == "cache":
        environment["UV_CACHE_DIR"] = str(tmp_path / "empty-cache")
    else:
        project = root / "pyproject.toml"
        project.write_text(project.read_text().replace("==1.0.0", "==1.0.1"))
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    with pytest.raises(guard.CleanBaseError, match="falló el control offline:") as error:
        guard._sync_smoke_dependencies(uv, root, target, tmp_path, environment)
    assert str(error.value) != "falló el control offline: sin diagnóstico"
    if damage == "hash":
        assert "Hash mismatch" in str(error.value)
    assert {path.name: path.read_bytes() for path in root.iterdir()} == before
    assert len(requests) == 1
