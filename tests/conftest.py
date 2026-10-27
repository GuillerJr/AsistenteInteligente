"""Explicit test-only desktop launch admission; productive IPC stays signed.

Each test owns a fresh synthetic key, and passes only its public launch pin to
JarvisService. No owner key, account, history or installed app is consulted.
Generic transport parsing tests deliberately omit ClientAuthority and continue
to exercise their separately declared bearer-only protocol fixture.
"""

import importlib.util
import json
import os
import re
import shlex
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "jarvis_test_resource_guard", ROOT / "script/test_resource_guard.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_GUARD = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _GUARD
_SPEC.loader.exec_module(_GUARD)
_PREFIX = "JARVIS_TEST_GUARD_"
_FIELDS = {"format", "profile", "kind", "selection", "full", "nonce",
           "source_fingerprint", "run_root", "guard_pid"}
_MAX_ADMISSION_BYTES = 16 * 1024


def _private_path(path: Path, *, directory: bool) -> os.stat_result:
    try:
        physical = path.resolve(strict=True)
    except RuntimeError as error:
        raise ValueError("La admisión contiene un ciclo de enlaces.") from error
    if not path.is_absolute() or physical != path:
        raise ValueError("La admisión exige rutas físicas absolutas.")
    info = path.lstat()
    ordinary = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    mode = 0o700 if directory else 0o600
    if not ordinary or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != mode:
        raise ValueError("La admisión del supervisor debe ser privada.")
    if not directory and (info.st_nlink != 1 or info.st_size > _MAX_ADMISSION_BYTES):
        raise ValueError("La admisión debe ser un archivo único acotado.")
    return info


def _guard_alive(value: dict) -> None:
    pid = value.get("guard_pid")
    if type(pid) is not int or pid <= 1 or pid != os.getppid():
        raise ValueError("Pytest debe ser hijo directo de su supervisor activo.")
    # The kernel reparents an orphan. Two exact PPID reads confirm the direct
    # parent without granting a sandboxed child permission to signal its guard.
    if os.getppid() != pid:
        raise ValueError("El supervisor se cerró durante la admisión.")


def validate_guard_admission(root: Path, environment: dict[str, str]) -> dict:
    """Fail before fixtures; environment alone never admits a local or CI suite."""
    required = ("PROFILE", "SELECTION", "RUN_ROOT", "ADMISSION", "NONCE")
    if any(not environment.get(_PREFIX + key) for key in required):
        raise ValueError("Usa script/test_resource_guard.py; pytest directo no está admitido.")
    profile = environment[_PREFIX + "PROFILE"]
    if profile not in {"local", "ci"}:
        raise ValueError("Perfil de pruebas no admitido.")
    run_root = Path(environment[_PREFIX + "RUN_ROOT"])
    admission = Path(environment[_PREFIX + "ADMISSION"])
    run_info = _private_path(run_root, directory=True)
    parent_info = _private_path(admission.parent, directory=True)
    admission_info = _private_path(admission, directory=False)
    descriptor = os.open(admission, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        raw = os.read(descriptor, _MAX_ADMISSION_BYTES + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if ((before.st_dev, before.st_ino) != (admission_info.st_dev, admission_info.st_ino)
            or len(raw) > _MAX_ADMISSION_BYTES or len(raw) != before.st_size
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                before.st_ctime_ns, before.st_mode, before.st_uid, before.st_nlink)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                after.st_ctime_ns, after.st_mode, after.st_uid, after.st_nlink)
            or not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1):
        raise ValueError("La admisión cambió o no es un archivo privado acotado.")
    for path, info in ((run_root, run_info), (admission.parent, parent_info),
                       (admission, before)):
        after_path = path.lstat()
        if (after_path.st_dev, after_path.st_ino, after_path.st_mode) != (
            info.st_dev, info.st_ino, info.st_mode,
        ):
            raise ValueError("Las rutas de admisión cambiaron durante la lectura.")
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise ValueError("La admisión tiene campos inesperados o incompletos.")
    if (value["format"] != "jarvis-test-guard/1" or value["kind"] != "python"
            or value["profile"] != profile or value["run_root"] != str(run_root)
            or value["selection"] != environment[_PREFIX + "SELECTION"]
            or value["nonce"] != environment[_PREFIX + "NONCE"]
            or not isinstance(value["nonce"], str)
            or re.fullmatch(r"[0-9a-f]{48}", value["nonce"]) is None
            or type(value["full"]) is not bool):
        raise ValueError("El entorno y la admisión no describen la misma ejecución Python.")
    _guard_alive(value)
    identity = _GUARD.fingerprint(root)
    if value["source_fingerprint"] != identity["fingerprint"]:
        raise ValueError("El código cambió desde la admisión de pruebas.")
    if profile == "ci":
        if (not value["full"] or environment.get("GITHUB_ACTIONS") != "true"
                or environment.get("GITHUB_SHA") != identity["head"]
                or Path(environment.get("GITHUB_WORKSPACE", "/")).resolve() != root):
            raise ValueError("La suite completa exige metadatos coherentes de GitHub Actions.")
    elif value["full"]:
        raise ValueError("La suite completa está reservada a CI.")
    selection = value["selection"]
    if not isinstance(selection, str) or not selection.strip() or len(selection.encode()) > 4096:
        raise ValueError("La selección de pruebas debe ser explícita y acotada.")
    paths = shlex.split(selection)
    if profile == "local" and (not paths or len(paths) > 100 or any(
        not re.fullmatch(r"tests/[A-Za-z0-9_./-]+\.py(?:::.*)?", path)
        or ".." in Path(path.split("::", 1)[0]).parts for path in paths
    )):
        raise ValueError("Selecciona archivos o casos focales dentro de tests.")
    if profile == "ci" and paths != ["tests"]:
        raise ValueError("La cualificación CI debe declarar la suite tests completa.")
    _guard_alive(value)
    return value


def pytest_configure(config):
    try:
        value = validate_guard_admission(ROOT, dict(os.environ))
        if shlex.split(value["selection"]) != config.args:
            raise ValueError("La selección de pytest difiere de la admitida por el supervisor.")
        config._jarvis_test_admission = value
    except (ValueError, OSError, _GUARD.GuardError) as error:
        raise pytest.UsageError(f"[jarvis-test-admission] {error}") from error


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    try:
        value = validate_guard_admission(ROOT, dict(os.environ))
        if value != config._jarvis_test_admission:
            raise ValueError("La admisión cambió durante la colección.")
        if value["profile"] == "local" and len(items) > 100:
            raise ValueError("Un focal local admite como máximo 100 casos; divide la selección.")
    except (ValueError, OSError, _GUARD.GuardError) as error:
        raise pytest.UsageError(f"[jarvis-test-admission] {error}") from error

@pytest.fixture(autouse=True)
def signed_fixture_client(monkeypatch, tmp_path_factory):
    from jarvis_core.client_identity import ClientIdentity

    identity = ClientIdentity.generate()
    # Credentials belong to the test harness, never the corpus a test may
    # inventory, package or add to its own synthetic Git repository.
    path = tmp_path_factory.mktemp("jarvis-synthetic-client") / "synthetic-client.key"
    path.write_bytes(identity.private_key.private_bytes_raw())
    path.chmod(0o600)
    monkeypatch.setenv("JARVIS_RPC_BOOTSTRAP_PUBLIC_KEY", identity.public_key)
    monkeypatch.setenv("JARVIS_RPC_CLIENT_KEY_FILE", str(path))
