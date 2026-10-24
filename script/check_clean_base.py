#!/usr/bin/env python3
"""Qualify the active stdlib base and its own archives, without loading legacy code.

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
    "src/aegis_core/runtime/daemon.py",
    "src/aegis_core/orchestration/graph.py",
    "src/aegis_core/brain/nvidia_client.py",
    "native/AegisAudio/Package.swift",
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
STDLIB = sys.stdlib_module_names | {"__future__", "aegis_core"}
SDIST_REQUIRED = {
    "PKG-INFO", "pyproject.toml", "README.md", "LICENSE", "uv.lock", "MANIFEST.in",
    "script/check_clean_base.py", "script/research_inventory.py", "script/setup_git_gates.sh",
    ".githooks/pre-commit", ".githooks/pre-push",
}


class CleanBaseError(ValueError):
    """An active dependency, source or archive violates the base contract."""


def _check_imports(data: bytes, name: str) -> None:
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
            if module.split(".")[0] not in STDLIB:
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
    if project.get("name") != "aegis-swarm":
        raise CleanBaseError("debe conservarse la distribución aegis-swarm")
    if project.get("dependencies") != [] or project.get("optional-dependencies"):
        raise CleanBaseError("la base no admite dependencias productivas ni extras heredados")
    if project.get("scripts"):
        raise CleanBaseError("la base todavía no declara un CLI de producto")
    return project


def check_tree(root: Path) -> dict:
    project = _project(root)
    for relative in RETIRED_FILES:
        if (root / relative).exists():
            raise CleanBaseError(f"resto productivo retirado: {relative}")
    source_root = root / "src"
    package = source_root / "aegis_core"
    if source_root.is_symlink() or package.is_symlink() or not (package / "__init__.py").is_file():
        raise CleanBaseError("falta el paquete propio ordinario aegis_core")
    files = []
    for path in sorted(source_root.rglob("*")):
        relative = path.relative_to(source_root)
        if "__pycache__" in relative.parts or any(part.endswith(".egg-info")
                                                for part in relative.parts):
            continue
        if " 2." in path.name:
            continue
        if path.is_symlink():
            raise CleanBaseError(f"fuente enlazada fuera del árbol: {relative}")
        if not path.is_file():
            continue
        if relative.parts[0] != "aegis_core" or path.suffix != ".py":
            raise CleanBaseError(f"recurso o paquete ajeno en Sources: {relative}")
        if any(part.casefold() in EXTERNAL_RUNTIMES | {"legacy", "vendor", "rebuild"}
               for part in relative.parts):
            raise CleanBaseError(f"árbol externo o retirado: {relative}")
        _check_imports(path.read_bytes(), str(relative))
        files.append(relative.as_posix())
    if not files:
        raise CleanBaseError("no hay fuentes propias")
    return {"name": project["name"], "version": project["version"], "source_files": files}


def _safe_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
        raise CleanBaseError("ruta de archivo insegura")
    return path


def _metadata(data: bytes) -> dict:
    message = email.parser.BytesParser().parsebytes(data)
    if message.get("Name") != "aegis-swarm" or not message.get("Version"):
        raise CleanBaseError("identidad de distribución incorrecta")
    if message.get_all("Requires-Dist"):
        raise CleanBaseError("el artefacto declara dependencias productivas")
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
            if top == "aegis_core" and name.suffix == ".py":
                data = archive.read(entry)
                _check_imports(data, entry.filename)
                files[entry.filename] = data
            elif top.startswith("aegis_swarm-") and top.endswith(".dist-info"):
                metadata_name = PurePosixPath(*name.parts[1:]).as_posix()
                if metadata_name not in {
                    "METADATA", "WHEEL", "RECORD", "top_level.txt", "licenses/LICENSE",
                }:
                    raise CleanBaseError(f"recurso ajeno en metadata del wheel: {entry.filename}")
                if name.name == "METADATA":
                    metadata.append(archive.read(entry))
            else:
                raise CleanBaseError(f"paquete o recurso ajeno en wheel: {entry.filename}")
    if "aegis_core/__init__.py" not in files or len(metadata) != 1:
        raise CleanBaseError("wheel propio incompleto")
    identity = _metadata(metadata[0])
    if root is not None:
        project = _project(root)
        if identity != {"name": project["name"], "version": project["version"]}:
            raise CleanBaseError("la metadata del wheel no corresponde al proyecto")
        expected = {f"aegis_core/{p.relative_to(root / 'src/aegis_core').as_posix()}":
                    p.read_bytes() for p in (root / "src/aegis_core").rglob("*.py")
                    if " 2." not in p.name}
        if files != expected:
            raise CleanBaseError("el wheel no corresponde al árbol cualificado")
    return {"wheel": path.name, "python_files": len(files), "runtime_dependencies": 0}


def check_sdist(path: Path, root: Path | None = None) -> dict:
    metadata = []
    manifest = None
    sources = {}
    included = set()
    count = 0
    with tarfile.open(path, "r:gz") as archive:
        names = set()
        for member in archive:
            name = _safe_name(member.name)
            if member.name in names:
                raise CleanBaseError("miembros duplicados en sdist")
            names.add(member.name)
            if not name.parts or not name.parts[0].startswith("aegis_swarm-"):
                raise CleanBaseError("raíz de sdist incorrecta")
            if member.isdir():
                continue
            if not member.isfile():
                raise CleanBaseError("enlace o miembro especial en sdist")
            relative = PurePosixPath(*name.parts[1:])
            included.add(relative.as_posix())
            if (relative.parts[0] not in {"src", "tests", "script", "docs"}
                and relative.as_posix() not in {
                    *SDIST_REQUIRED, "setup.cfg",
                }):
                raise CleanBaseError(f"miembro ajeno en sdist: {relative}")
            if relative.as_posix() in RETIRED_FILES or relative.suffix in {
                ".so", ".dylib", ".dll", ".js", ".mjs", ".cjs", ".ts", ".swift",
                ".asar", ".onnx",
            }:
                raise CleanBaseError(f"recurso retirado o ajeno en sdist: {relative}")
            data = archive.extractfile(member)
            if data is None:
                raise CleanBaseError("no se pudo leer miembro de sdist")
            content = data.read()
            if relative.as_posix() == "PKG-INFO":
                metadata.append(content)
            elif relative.as_posix() == "pyproject.toml":
                manifest = tomllib.loads(content.decode())["project"]
            elif relative.parts[0] == "src" and not any(
                part.endswith(".egg-info") for part in relative.parts
            ):
                if len(relative.parts) < 3 or relative.parts[1] != "aegis_core" \
                        or relative.suffix != ".py":
                    raise CleanBaseError(f"fuente ajena en sdist: {relative}")
                _check_imports(content, relative.as_posix())
                sources[relative.as_posix()] = content
            count += 1
    if len(metadata) != 1:
        raise CleanBaseError("falta metadata única del sdist")
    if not SDIST_REQUIRED <= included:
        raise CleanBaseError("el sdist omite archivos necesarios para repetir la cualificación")
    identity = _metadata(metadata[0])
    if (manifest is None or manifest.get("dependencies") != []
        or manifest.get("optional-dependencies") or manifest.get("scripts")
        or identity != {"name": manifest.get("name"), "version": manifest.get("version")}):
        raise CleanBaseError("el manifiesto del sdist no coincide con una base sin dependencias")
    if root is not None:
        if identity != {key: _project(root)[key] for key in ("name", "version")}:
            raise CleanBaseError("la identidad del sdist no corresponde al proyecto")
        expected = {p.relative_to(root).as_posix(): p.read_bytes()
                    for p in (root / "src/aegis_core").rglob("*.py") if " 2." not in p.name}
        if sources != expected:
            raise CleanBaseError("el sdist no corresponde al árbol cualificado")
    return {"sdist": path.name, "files": count}


def smoke_wheel(wheel: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise CleanBaseError("uv es necesario para instalar el wheel sin red")
    with tempfile.TemporaryDirectory(prefix="aegis-base-smoke-") as temporary:
        sandbox = Path(temporary).resolve()
        environment = {"PATH": os.defpath, "LANG": "C.UTF-8",
                       "UV_CACHE_DIR": str(sandbox / "cache"), "UV_PYTHON_DOWNLOADS": "never"}
        target = sandbox / "venv"
        venv.EnvBuilder(with_pip=False).create(target)
        python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run([
            uv, "--no-config", "pip", "install", "--offline", "--no-index", "--no-deps",
            "--python", str(python), str(wheel.resolve()),
        ], check=True, cwd=sandbox, env=environment, capture_output=True, timeout=60)
        code = (
            "from pathlib import Path; import aegis_core; "
            "from aegis_core import AgentRuntime; "
            f"assert Path(aegis_core.__file__).resolve().is_relative_to({str(target)!r}); "
            "assert callable(AgentRuntime); print('installed_base_import=ok')"
        )
        result = subprocess.run([str(python), "-I", "-c", code], check=True,
                                cwd=sandbox, env=environment, capture_output=True,
                                text=True, timeout=10)
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
    print("status=ok clean_base runtime_dependencies=0 native_app=pending")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
