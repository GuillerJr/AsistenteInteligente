#!/usr/bin/env python3
"""Qualify the independent base and its own archives, without loading legacy code.

This is a bounded source/metadata check, not proof of arbitrary program behavior or
of a complete application. Research documents may reference external projects.
"""

from __future__ import annotations

import argparse
import ast
import email.parser
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import venv
import zipfile
from pathlib import Path, PurePosixPath

RETIRED_FILES = (
    "src/jarvis_core/runtime/daemon.py",
    "src/jarvis_core/orchestration/graph.py",
    "src/jarvis_core/brain/nvidia_client.py",
    "native/JarvisAudio/Package.swift",
    "integrations/mcp/package.json",
    "packaging/jarvis_daemon.spec",
    "script/build_and_run.sh",
    "script/menu_bar_service.sh",
    "script/test_native.sh",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
)
EXTERNAL_RUNTIMES = {"openclaw", "opencode"}
STDLIB = sys.stdlib_module_names | {"__future__", "jarvis_core"}
# A reviewed signature primitive, never a model, agent framework or external runtime.
# The exact release and its transitive artifacts are hash-bound by uv.lock.
REVIEWED_DEPENDENCIES = ("cryptography==50.0.2",)
SDIST_REQUIRED = {
    "PKG-INFO", "pyproject.toml", "README.md", "LICENSE", "uv.lock", "MANIFEST.in",
    "script/check_clean_base.py", "script/research_inventory.py", "script/setup_git_gates.sh",
    "script/test_resource_guard.py",
    ".githooks/pre-commit", ".githooks/pre-push",
}
NATIVE_ROOT = PurePosixPath("native/JarvisDesktop")


def _native_source(path: PurePosixPath) -> bool:
    if not path.is_relative_to(NATIVE_ROOT):
        return False
    inside = path.relative_to(NATIVE_ROOT)
    if not inside.parts or any(part in {".build", ".swiftpm"} for part in inside.parts):
        return False
    return inside.suffix in {".swift", ".plist", ".md"}


def _native_files(root: Path) -> dict[str, bytes]:
    result = {}
    folder = root / NATIVE_ROOT
    if (root / "native").is_symlink():
        raise CleanBaseError("el proyecto nativo propio no admite enlaces")
    native_root = root / "native"
    if native_root.exists():
        if not native_root.is_dir() or any(
            child.name not in {"JarvisDesktop", ".DS_Store"} for child in native_root.iterdir()
        ):
            raise CleanBaseError("árbol nativo ajeno al proyecto JarvisDesktop")
    if not folder.exists():
        return result
    if folder.is_symlink():
        raise CleanBaseError("el proyecto nativo propio no admite enlaces")
    for parent, directories, files in os.walk(folder, followlinks=False):
        directories[:] = [name for name in directories if name not in {".build", ".swiftpm"}]
        for name in [*directories, *files]:
            path = Path(parent) / name
            if path.is_symlink():
                raise CleanBaseError("fuente nativa enlazada fuera del árbol")
        for name in files:
            path = Path(parent) / name
            relative = PurePosixPath(path.relative_to(root).as_posix())
            if " 2." not in name and _native_source(relative):
                result[relative.as_posix()] = path.read_bytes()
    return result


def _test_files(root: Path) -> dict[str, bytes]:
    """All ordinary Python test sources, including non-test-named helpers."""
    folder = root / "tests"
    if folder.is_symlink():
        raise CleanBaseError("las fuentes de pruebas no admiten enlaces")
    if not folder.exists():
        return {}
    if not folder.is_dir():
        raise CleanBaseError("la raíz de pruebas debe ser un directorio ordinario")
    result = {}
    for parent, directories, files in os.walk(folder, followlinks=False):
        directories[:] = [name for name in directories if name != "__pycache__"]
        for name in [*directories, *files]:
            if (Path(parent) / name).is_symlink():
                raise CleanBaseError("fuente de pruebas enlazada fuera del árbol")
        for name in files:
            path = Path(parent) / name
            if path.suffix == ".py" and " 2." not in name:
                result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result


class CleanBaseError(ValueError):
    """An active dependency, source or archive violates the base contract."""


def check_python_syntax(root: Path) -> int:
    """Parse own sources in memory: never create compileall bytecode caches."""
    count = 0
    for name in ("src", "tests", "script"):
        folder = root / name
        if folder.is_symlink():
            raise CleanBaseError("las fuentes de Python no admiten enlaces")
        if not folder.exists():
            continue
        for parent, directories, files in os.walk(folder, followlinks=False):
            directories[:] = [part for part in directories
                              if part != "__pycache__" and not part.endswith(".egg-info")]
            for item in [*directories, *files]:
                if (Path(parent) / item).is_symlink():
                    raise CleanBaseError("fuente de Python enlazada fuera del árbol")
            for item in files:
                path = Path(parent) / item
                if path.suffix == ".py" and " 2." not in item:
                    ast.parse(path.read_bytes(), filename=str(path.relative_to(root)))
                    count += 1
    return count


def _dependencies(values) -> tuple[str, ...]:
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise CleanBaseError("lista de dependencias productivas inválida")
    requirements = tuple(value.replace(" ", "").lower() for value in values)
    if requirements not in {(), REVIEWED_DEPENDENCIES}:
        raise CleanBaseError("dependencias productivas fuera de la lista revisada")
    return requirements


def _check_imports(data: bytes, name: str, dependencies: tuple[str, ...] = ()) -> None:
    allowed = STDLIB | ({"cryptography"} if dependencies else set())
    tree = ast.parse(data, filename=name)
    dynamic_names = {"eval", "exec", "__import__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "importlib":
            dynamic_names.update(item.asname or item.name for item in node.names
                                 if item.name == "import_module")
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            modules = [node.module]
        for module in modules:
            if module.split(".")[0] not in allowed:
                raise CleanBaseError(f"import externo en {name}: {module}")
        if isinstance(node, ast.Call):
            function = node.func
            if ((isinstance(function, ast.Name) and function.id in dynamic_names)
                or (isinstance(function, ast.Attribute) and function.attr == "import_module")):
                raise CleanBaseError(f"carga dinámica no revisable en {name}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            command = PurePosixPath(node.value).name.casefold()
            if command in EXTERNAL_RUNTIMES:
                raise CleanBaseError(f"runtime externo en {name}: {command}")


def _project(root: Path) -> dict:
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    if project.get("name") != "jarvis-core":
        raise CleanBaseError("debe conservarse la distribución jarvis-core")
    _dependencies(project.get("dependencies"))
    if project.get("optional-dependencies"):
        raise CleanBaseError("la base no admite extras heredados")
    if project.get("scripts"):
        raise CleanBaseError("la base todavía no declara un CLI de producto")
    return project


def check_tree(root: Path) -> dict:
    project = _project(root)
    for relative in RETIRED_FILES:
        if (root / relative).exists():
            raise CleanBaseError(f"resto productivo retirado: {relative}")
    source_root = root / "src"
    package = source_root / "jarvis_core"
    if source_root.is_symlink() or package.is_symlink() or not (package / "__init__.py").is_file():
        raise CleanBaseError("falta el paquete propio ordinario jarvis_core")
    files = []
    for path in sorted(source_root.rglob("*")):
        relative = path.relative_to(source_root)
        if "__pycache__" in relative.parts or any(part.endswith(".egg-info")
                                                for part in relative.parts):
            continue
        if " 2." in path.name:
            continue
        if path.name == ".DS_Store":
            continue  # Finder metadata is ignored and never packaged or opened.
        if path.is_symlink():
            raise CleanBaseError(f"fuente enlazada fuera del árbol: {relative}")
        if not path.is_file():
            continue
        if relative.parts[0] != "jarvis_core" or path.suffix != ".py":
            raise CleanBaseError(f"recurso o paquete ajeno en Sources: {relative}")
        if any(part.casefold() in EXTERNAL_RUNTIMES | {"legacy", "vendor", "rebuild"}
               for part in relative.parts):
            raise CleanBaseError(f"árbol externo o retirado: {relative}")
        _check_imports(path.read_bytes(), str(relative), _dependencies(project["dependencies"]))
        files.append(relative.as_posix())
    if not files:
        raise CleanBaseError("no hay fuentes propias")
    _native_files(root)
    _test_files(root)
    return {"name": project["name"], "version": project["version"], "source_files": files}


def _safe_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
        raise CleanBaseError("ruta de archivo insegura")
    return path


def _metadata_dependencies(data: bytes) -> tuple[str, ...]:
    return _dependencies(email.parser.BytesParser().parsebytes(data).get_all("Requires-Dist") or [])


def _metadata(data: bytes) -> dict:
    message = email.parser.BytesParser().parsebytes(data)
    if message.get("Name") != "jarvis-core" or not message.get("Version"):
        raise CleanBaseError("identidad de distribución incorrecta")
    _metadata_dependencies(data)
    return {"name": message["Name"], "version": message["Version"]}


def check_wheel(path: Path, root: Path | None = None) -> dict:
    files: dict[str, bytes] = {}
    metadata = []
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(set(names)) != len(names):
            raise CleanBaseError("miembros duplicados en wheel")
        for entry in archive.infolist():
            name = _safe_name(entry.filename)
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise CleanBaseError("enlace no permitido en wheel")
            if entry.is_dir():
                continue
            top = name.parts[0]
            if top == "jarvis_core" and name.suffix == ".py":
                data = archive.read(entry)
                files[entry.filename] = data
            elif top.startswith("jarvis_core-") and top.endswith(".dist-info"):
                metadata_name = PurePosixPath(*name.parts[1:]).as_posix()
                if metadata_name not in {
                    "METADATA", "WHEEL", "RECORD", "top_level.txt", "licenses/LICENSE",
                }:
                    raise CleanBaseError(f"recurso ajeno en metadata del wheel: {entry.filename}")
                if name.name == "METADATA":
                    metadata.append(archive.read(entry))
            else:
                raise CleanBaseError(f"paquete o recurso ajeno en wheel: {entry.filename}")
    if "jarvis_core/__init__.py" not in files or len(metadata) != 1:
        raise CleanBaseError("wheel propio incompleto")
    identity = _metadata(metadata[0])
    dependencies = _metadata_dependencies(metadata[0])
    for name, data in files.items():
        _check_imports(data, name, dependencies)
    if root is not None:
        project = _project(root)
        if dependencies != _dependencies(project["dependencies"]):
            raise CleanBaseError("las dependencias del wheel no corresponden al proyecto")
        if identity != {"name": project["name"], "version": project["version"]}:
            raise CleanBaseError("la metadata del wheel no corresponde al proyecto")
        expected = {f"jarvis_core/{p.relative_to(root / 'src/jarvis_core').as_posix()}":
                    p.read_bytes() for p in (root / "src/jarvis_core").rglob("*.py")
                    if " 2." not in p.name}
        if files != expected:
            raise CleanBaseError("el wheel no corresponde al árbol cualificado")
    return {"wheel": path.name, "python_files": len(files),
            "runtime_dependencies": len(dependencies)}


def check_sdist(path: Path, root: Path | None = None) -> dict:
    metadata = []
    manifest = None
    sources = {}
    native_sources = {}
    test_sources = {}
    included = set()
    count = 0
    with tarfile.open(path, "r:gz") as archive:
        names = set()
        for member in archive:
            name = _safe_name(member.name)
            if member.name in names:
                raise CleanBaseError("miembros duplicados en sdist")
            names.add(member.name)
            if not name.parts or not name.parts[0].startswith("jarvis_core-"):
                raise CleanBaseError("raíz de sdist incorrecta")
            if member.isdir():
                continue
            if len(name.parts) < 2:
                raise CleanBaseError("miembro de sdist sin ruta relativa")
            if not member.isfile():
                raise CleanBaseError("enlace o miembro especial en sdist")
            relative = PurePosixPath(*name.parts[1:])
            included.add(relative.as_posix())
            native = _native_source(relative)
            if (relative.parts[0] not in {"src", "tests", "script", "docs"} and not native
                and relative.as_posix() not in {
                    *SDIST_REQUIRED, "setup.cfg",
                }):
                raise CleanBaseError(f"miembro ajeno en sdist: {relative}")
            if relative.as_posix() in RETIRED_FILES or (relative.suffix in {
                ".so", ".dylib", ".dll", ".js", ".mjs", ".cjs", ".ts", ".swift",
                ".asar", ".onnx",
            } and not native):
                raise CleanBaseError(f"recurso retirado o ajeno en sdist: {relative}")
            data = archive.extractfile(member)
            if data is None:
                raise CleanBaseError("no se pudo leer miembro de sdist")
            content = data.read()
            if native:
                native_sources[relative.as_posix()] = content
            if relative.as_posix() == "PKG-INFO":
                metadata.append(content)
            elif relative.as_posix() == "pyproject.toml":
                manifest = tomllib.loads(content.decode())["project"]
            elif relative.parts[0] == "src" and not any(
                part.endswith(".egg-info") for part in relative.parts
            ):
                if len(relative.parts) < 3 or relative.parts[1] != "jarvis_core" \
                        or relative.suffix != ".py":
                    raise CleanBaseError(f"fuente ajena en sdist: {relative}")
                sources[relative.as_posix()] = content
            elif relative.parts[0] == "tests" and relative.suffix == ".py":
                test_sources[relative.as_posix()] = content
            count += 1
    if len(metadata) != 1:
        raise CleanBaseError("falta metadata única del sdist")
    if not SDIST_REQUIRED <= included:
        raise CleanBaseError("el sdist omite archivos necesarios para repetir la cualificación")
    identity = _metadata(metadata[0])
    if (manifest is None
        or manifest.get("optional-dependencies") or manifest.get("scripts")
        or identity != {"name": manifest.get("name"), "version": manifest.get("version")}):
        raise CleanBaseError("el manifiesto del sdist no coincide con la base propia")
    dependencies = _dependencies(manifest.get("dependencies"))
    if dependencies != _metadata_dependencies(metadata[0]):
        raise CleanBaseError("las dependencias del sdist no coinciden con su metadata")
    for name, data in sources.items():
        _check_imports(data, name, dependencies)
    if root is not None:
        if dependencies != _dependencies(_project(root)["dependencies"]):
            raise CleanBaseError("las dependencias del sdist no corresponden al proyecto")
        if identity != {key: _project(root)[key] for key in ("name", "version")}:
            raise CleanBaseError("la identidad del sdist no corresponde al proyecto")
        expected = {p.relative_to(root).as_posix(): p.read_bytes()
                    for p in (root / "src/jarvis_core").rglob("*.py") if " 2." not in p.name}
        if sources != expected:
            raise CleanBaseError("el sdist no corresponde al árbol cualificado")
        if native_sources != _native_files(root):
            raise CleanBaseError("el sdist no corresponde a las fuentes nativas propias")
        if test_sources != _test_files(root):
            raise CleanBaseError("el sdist no corresponde a las fuentes Python de pruebas")
    return {"sdist": path.name, "files": count}


def _smoke_command(command: list[str], *, cwd: Path, environment: dict[str, str],
                   timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, check=True, cwd=cwd, env=environment,
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.CalledProcessError as error:
        diagnostic = (error.stderr or error.stdout or "sin diagnóstico").strip()
        raise CleanBaseError(f"falló el control offline: {diagnostic[-8000:]}") from error


def _sync_smoke_dependencies(uv: str, root: Path, target: Path, sandbox: Path,
                             environment: dict[str, str]) -> None:
    # A fresh sync cache has URL artifacts from uv.lock, but need not have the
    # simple-index metadata required to resolve exported name==version entries.
    # Consume that same hash-bound lock directly; never build/install the project.
    _smoke_command([
        uv, "--no-config", "sync", "--offline", "--locked", "--no-dev", "--no-build",
        "--no-default-groups", "--no-install-project", "--project", str(root),
        "--python", str(target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")),
    ], cwd=sandbox, environment={**environment, "UV_PROJECT_ENVIRONMENT": str(target)}, timeout=60)


def smoke_wheel(wheel: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise CleanBaseError("uv es necesario para instalar el wheel sin red")
    with tempfile.TemporaryDirectory(prefix="jarvis-base-smoke-") as temporary:
        sandbox = Path(temporary).resolve()
        # Cache preparation belongs to uv sync, outside this offline check. Every
        # dependency is still installed in a new venv and checked against lock hashes.
        cache = subprocess.run([uv, "--no-config", "cache", "dir"], check=True,
                               capture_output=True, text=True, timeout=10).stdout.strip()
        environment = {"PATH": os.defpath, "LANG": "C.UTF-8",
                       "UV_CACHE_DIR": cache, "UV_PYTHON_DOWNLOADS": "never"}
        target = sandbox / "venv"
        venv.EnvBuilder(with_pip=False).create(target)
        python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        root = Path(__file__).resolve().parents[1]
        dependencies = _dependencies(_project(root)["dependencies"])
        if dependencies:
            _sync_smoke_dependencies(uv, root, target, sandbox, environment)
        _smoke_command([
            uv, "--no-config", "pip", "install", "--offline", "--no-index", "--no-deps",
            "--python", str(python), str(wheel.resolve()),
        ], cwd=sandbox, environment=environment, timeout=60)
        code = (
            "from pathlib import Path; import jarvis_core; "
            "from jarvis_core import AgentRuntime; "
            f"assert Path(jarvis_core.__file__).resolve().is_relative_to({str(target)!r}); "
            "assert callable(AgentRuntime); "
            + ("from cryptography.hazmat.primitives.asymmetric.ed25519 "
               "import Ed25519PrivateKey; "
               "key=Ed25519PrivateKey.generate(); "
               "key.public_key().verify(key.sign(b'jarvis offline smoke'),"
               "b'jarvis offline smoke'); "
               if dependencies else "")
            + "print('installed_base_import=ok')"
        )
        result = _smoke_command([str(python), "-I", "-c", code], cwd=sandbox,
                                environment=environment, timeout=10)
        if result.stdout.strip() != "installed_base_import=ok":
            raise CleanBaseError("el paquete aislado no confirmó su importación")


def main() -> int:
    parser = argparse.ArgumentParser(description="Comprueba la base propia de Jarvis.")
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--sdist", type=Path)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if args.smoke and args.wheel is None:
        parser.error("--smoke requiere --wheel")
    root = Path(__file__).resolve().parents[1]
    try:
        check_tree(root)
        syntax_count = check_python_syntax(root)
        if args.wheel is not None:
            check_wheel(args.wheel, root)
        if args.sdist is not None:
            check_sdist(args.sdist, root)
        if args.smoke:
            smoke_wheel(args.wheel)
    except (CleanBaseError, OSError, SyntaxError, ValueError, KeyError,
            subprocess.SubprocessError, zipfile.BadZipFile, tarfile.TarError) as error:
        print(f"status=failed clean_base reason={error}", file=sys.stderr)
        return 1
    count = len(_dependencies(_project(root)["dependencies"]))
    print(f"status=ok clean_base runtime_dependencies={count} native_app=development "
          f"python_syntax_files={syntax_count} bytecode_written=no")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
