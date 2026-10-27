#!/usr/bin/env python3
"""Finite test jobs, source identity and private bounded diagnostics.

Local jobs use a new, capacity-limited APFS image and Seatbelt. CI is an explicit
remote profile, not proof against a hostile process running as the same user.
An owned process group is not universal custody of detached descendants. An
interruption fails the job. Cleanup additionally requires its process group to
exit, no pending-owner marker and a successful non-forced volume detachment.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import plistlib
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

FORMAT = "jarvis-test-guard/1"
MIB = 1024 * 1024
GIB = 1024 * MIB
LOG_LIMIT = 256 * 1024
RECEIPT_LIMIT = 64 * 1024
MAX_FILES = 10_000
MAX_FILE_BYTES = 64 * MIB
MAX_SOURCE_BYTES = 256 * MIB
REPO = Path(__file__).resolve().parents[1]
SENSITIVE = {"auth.json", "credentials.json", "secrets.json", "id_rsa", "id_ed25519"}
SENSITIVE_RELATIVE = {".claude/settings.local.json"}


class GuardError(RuntimeError):
    pass


class PreparationError(GuardError):
    def __init__(self, pending):
        super().__init__("No se preparó la cuota APFS; no se ejecutó el comando.")
        self.pending = pending


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def private_directory(path: Path):
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o777 != 0o700):
        raise GuardError("El directorio del supervisor no es privado.")
    return info.st_dev, info.st_ino


def write_json(path: Path, value):
    data = canonical(value) + b"\n"
    if len(data) > RECEIPT_LIMIT:
        raise GuardError("El recibo supera su límite de 64 KiB.")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def git(root, *arguments):
    executable = os.environ.get("JARVIS_TEST_GUARD_GIT", "git")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update({"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
                        "GIT_CONFIG_NOSYSTEM": "1"})
    result = subprocess.run([executable, "-c", "core.excludesFile=/dev/null", "-C", str(root),
                             *arguments], capture_output=True, timeout=10, check=False,
                            env=environment)
    if result.returncode:
        raise GuardError("No se pudo confirmar la identidad Git del árbol.")
    return result.stdout


def safe_source_bytes(root: Path, relative: str, remaining=MAX_SOURCE_BYTES):
    parts = Path(relative).parts
    if (not parts or len(parts) > 64 or relative.startswith("/")
            or any(p in {".", ".."} for p in parts)):
        raise GuardError("La lista Git contiene una ruta no admitida.")
    if relative.casefold() in SENSITIVE_RELATIVE:
        raise GuardError("Hay una configuración local privada en Git; no se leyó.")
    if any(p.lower() in SENSITIVE or p.lower().startswith(".env")
           or p.lower().endswith((".key", ".pem", ".p12", ".pfx")) for p in parts):
        raise GuardError("Hay un archivo posiblemente privado en el árbol; no se leyó.")
    descriptors = [os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)]
    bindings = []
    root_identity = os.fstat(descriptors[0])
    try:
        for name in parts[:-1]:
            try:
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=descriptors[-1])
            except FileNotFoundError:
                return {"path": relative, "kind": "deleted"}, 0
            except OSError as error:
                raise GuardError("Un ancestro de fuente no es un directorio físico.") from error
            bindings.append((descriptors[-1], name, os.fstat(child)))
            descriptors.append(child)
        parent, name = descriptors[-1], parts[-1]
        try:
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return {"path": relative, "kind": "deleted"}, 0
        if stat.S_ISLNK(before.st_mode):
            payload = os.fsencode(os.readlink(name, dir_fd=parent))
            total, digest, kind = len(payload), hashlib.sha256(payload), "symlink"
        elif stat.S_ISREG(before.st_mode):
            if before.st_size > min(MAX_FILE_BYTES, remaining):
                raise GuardError("La fuente supera el presupuesto de lectura restante.")
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=parent)
            try:
                pinned = os.fstat(descriptor)
                if (pinned.st_dev, pinned.st_ino) != (before.st_dev, before.st_ino):
                    raise GuardError("La fuente cambió antes de leerla.")
                total, digest = 0, hashlib.sha256()
                while block := os.read(descriptor, min(MIB, remaining - total + 1)):
                    total += len(block)
                    if total > min(MAX_FILE_BYTES, remaining):
                        raise GuardError("La fuente creció durante su lectura.")
                    digest.update(block)
            finally:
                os.close(descriptor)
            kind = "file"
        else:
            raise GuardError("Git referencia una fuente especial no admitida.")
        after = os.stat(name, dir_fd=parent, follow_symlinks=False)
        keys = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode")
        if any(getattr(before, key) != getattr(after, key) for key in keys):
            raise GuardError("La fuente cambió durante su lectura.")
        for descriptor, component, pinned in bindings:
            current = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (pinned.st_dev, pinned.st_ino):
                raise GuardError("Un ancestro de fuente cambió durante la lectura.")
        current_root = root.lstat()
        if (current_root.st_dev, current_root.st_ino) != (
                root_identity.st_dev, root_identity.st_ino):
            raise GuardError("La raíz de fuentes cambió durante la lectura.")
        return {"path": relative, "kind": kind, "bytes": total,
                "executable": bool(before.st_mode & 0o111), "sha256": digest.hexdigest()}, total
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def fingerprint(root: Path):
    root = root.resolve(strict=True)
    top = os.fsdecode(git(root, "rev-parse", "--show-toplevel")).strip()
    if Path(top).resolve() != root:
        raise GuardError("La raíz solicitada no es la raíz Git.")
    head = git(root, "rev-parse", "HEAD").decode().strip()
    index = git(root, "ls-files", "--stage", "-z")
    names = sorted(set(os.fsdecode(name) for name in git(
        root, "ls-files", "--cached", "--others", "--exclude-standard", "-z"
    ).split(b"\0") if name))
    if len(names) > MAX_FILES:
        raise GuardError("El árbol supera el límite de 10.000 archivos.")
    rows, total = [], 0
    for name in names:
        row, count = safe_source_bytes(root, name, MAX_SOURCE_BYTES - total)
        rows.append(row)
        total += count
        if total > MAX_SOURCE_BYTES:
            raise GuardError("El árbol supera el límite de lectura de 256 MiB.")
    identity = {"head": head, "index_sha256": hashlib.sha256(index).hexdigest(),
                "worktree_sha256": hashlib.sha256(canonical(rows)).hexdigest(),
                "files": len(rows), "source_bytes": total}
    identity["fingerprint"] = hashlib.sha256(canonical(identity)).hexdigest()
    return identity


def validate_options(options, environment, identity, root):
    if not options.command:
        raise GuardError("Falta el comando explícito que se debe comprobar.")
    if not options.selection.strip() or len(options.selection.encode()) > 4096:
        raise GuardError("Indica una selección focal explícita.")
    if options.profile == "local":
        if options.full or options.selection.strip().lower() in {"all", "full", "todos", "tests"}:
            raise GuardError("Las suites completas se ejecutan en CI; indica pruebas focales.")
        maximum = 180 if options.kind == "python" else 600
        budget = 768 if options.kind == "python" else 2048
    else:
        if (not options.full or environment.get("GITHUB_ACTIONS") != "true"
                or environment.get("GITHUB_SHA") != identity["head"]
                or Path(environment.get("GITHUB_WORKSPACE", "/")).resolve() != root):
            raise GuardError("CI exige --full, workspace y SHA exactos de GitHub Actions.")
        maximum = 1800 if options.kind == "python" else 1200
        budget = 16 * 1024
    if not 1 <= options.budget_mib <= budget or not 0 < options.deadline <= maximum:
        raise GuardError("El presupuesto o plazo supera el máximo del perfil.")


def allocated_bytes(root: Path, maximum_entries=100_000):
    """Conservative inode-accounted allocation; never follows links or reads data."""
    total, seen, pending, count = 0, set(), [root], 0
    while pending:
        path = pending.pop()
        info = path.lstat()
        count += 1
        if count > maximum_entries:
            raise GuardError("El directorio de pruebas supera su límite de entradas.")
        key = info.st_dev, info.st_ino
        if key in seen:
            continue
        seen.add(key)
        total += info.st_blocks * 512
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(path) as entries:
                pending.extend(Path(entry.path) for entry in entries)
    return total


def available_bytes(path: Path):
    value = os.statvfs(path)
    return value.f_bavail * value.f_frsize


@dataclass
class Volume:
    root: Path
    image: Path | None
    device: int
    inode: int
    total_bytes: int


def create_volume(control: Path, budget_mib: int, kind: str):
    if sys.platform != "darwin":
        raise GuardError("El perfil local exige la cuota APFS de macOS; usa CI en otros sistemas.")
    capacity = 1024 if kind == "python" else max(1024, budget_mib)
    image = control / "quota.sparseimage"
    mount = Path(tempfile.mkdtemp(prefix="jq-", dir="/private/tmp"))
    mount.chmod(0o700)
    write_json(control / "preparation.json", {"image": str(image), "mount": str(mount),
               "capacity_mib": capacity, "command_dispatched": False})
    commands = (["/usr/bin/hdiutil", "create", "-size", f"{capacity}m", "-type", "SPARSE",
                 "-fs", "APFS", "-volname", "JarvisTestQuota", str(image)],
                ["/usr/bin/hdiutil", "attach", "-nobrowse", "-owners", "on",
                 "-mountpoint", str(mount), "-plist", str(image)])
    attached = False
    try:
        for command in commands:
            result = subprocess.run(command, capture_output=True, timeout=35)
            if result.returncode:
                raise GuardError("No se pudo preparar la cuota APFS; no se ejecutó el comando.")
            if command[1] == "attach":
                parsed = plistlib.loads(result.stdout)
                attached = any(entry.get("mount-point") == str(mount)
                               for entry in parsed.get("system-entities", []))
        info, fs = mount.lstat(), os.statvfs(mount)
        total = fs.f_blocks * fs.f_frsize
        if not attached or not os.path.ismount(mount) or total > capacity * MIB:
            raise GuardError("No se confirmó la capacidad del volumen propio.")
        mount.chmod(0o700)
        return Volume(mount, image, info.st_dev, info.st_ino, total)
    except BaseException as error:
        # No command was dispatched. Never force an unmount or delete a mount.
        pending = isinstance(error, (subprocess.TimeoutExpired, KeyboardInterrupt, SystemExit))
        if attached or os.path.ismount(mount):
            try:
                result = subprocess.run(["/usr/bin/hdiutil", "detach", str(mount)],
                                        capture_output=True, timeout=20)
                pending = result.returncode != 0
            except subprocess.SubprocessError:
                pending = True
        if not pending:
            mount.chmod(0)
            if image.exists() and stat.S_ISREG(image.lstat().st_mode):
                image.unlink()
        raise PreparationError(pending) from error


def detach_volume(volume: Volume):
    info = volume.root.lstat()
    if ((info.st_dev, info.st_ino) != (volume.device, volume.inode)
            or not os.path.ismount(volume.root) or volume.root.is_symlink()):
        return False
    result = subprocess.run(["/usr/bin/hdiutil", "detach", str(volume.root)],
                            capture_output=True, timeout=20)
    if result.returncode:
        return False
    # A remaining child cannot use the underlying mount directory after detach.
    volume.root.chmod(0)
    return True


def sandbox_policy(source: Path, control: Path, mount: Path):
    reads = [source, control, mount, Path("/System"), Path("/usr"), Path("/bin"),
             Path("/sbin"), Path("/opt/homebrew"), Path("/Library/Frameworks/Python.framework"),
             Path("/Library/Developer"), Path("/Applications/Xcode.app"),
             Path("/private/var/db/dyld"), Path("/private/etc"), Path("/dev")]
    filters = " ".join(f"(subpath {json.dumps(str(path))})" for path in reads)
    quote = json.dumps(str(mount))
    return "\n".join([
        "(version 1)", "(deny default)",
        "(allow process-exec process-fork sysctl-read file-read-metadata)",
        "(allow signal (target same-sandbox))",
        f'(allow file-read* (literal "/") {filters})',
        f"(allow file-read* file-write* (subpath {quote}))",
        f"(deny file-write-mode file-write-owner file-write-unlink (literal {quote}))",
        '(allow file-write-data (literal "/dev/null"))',
        f'(allow network-bind (local ip "localhost:*") (local unix-socket (subpath {quote})))',
        f'(allow network-inbound (local ip "localhost:*") (local unix-socket (subpath {quote})))',
        f'(allow network-outbound (remote ip "localhost:*") '
        f'(remote unix-socket (subpath {quote})))',
    ]) + "\n"


def child_environment(root: Path, control: Path, admission, original, source: Path):
    environment = {key: original[key] for key in ("PATH", "LANG", "LC_ALL", "SDKROOT",
        "DEVELOPER_DIR", "GITHUB_ACTIONS", "GITHUB_SHA", "GITHUB_WORKSPACE", "RUNNER_OS")
                   if key in original}
    environment.update({"HOME": str(root / "home"), "TMPDIR": str(root / "tmp") + "/",
        "UV_CACHE_DIR": str(root / "uv"), "XDG_CACHE_HOME": str(root / "xdg"),
        "UV_PYTHON_DOWNLOADS": "never", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1", "PYTHONPATH": str(source / "src"),
        "PYTEST_ADDOPTS": "-p no:cacheprovider --basetemp=" + str(root / "p"),
        "JARVIS_TEST_GUARD_PROFILE": admission["profile"],
        "JARVIS_TEST_GUARD_SELECTION": admission["selection"],
        "JARVIS_TEST_GUARD_RUN_ROOT": str(root),
        "JARVIS_TEST_GUARD_ADMISSION": str(control / "admission.json"),
        "JARVIS_TEST_GUARD_NONCE": admission["nonce"],
        "JARVIS_TEST_GUARD_CLEANUP_UNCERTAIN": str(root / "cleanup-uncertain.json"),
        "JARVIS_DESKTOP_PYTHON": str(source / ".venv/bin/python"),
        "JARVIS_DESKTOP_SOURCE": str(source / "src"),
        "JARVIS_RPC_CLIENT_KEY_FILE": str(root / "synthetic-client.key"),
        "CLANG_MODULE_CACHE_PATH": str(root / "clang-cache")})
    # Values are explicit code/dependency locations, never account state.
    for key in ("JARVIS_DESKTOP_DEPENDENCIES", "JARVIS_RPC_BOOTSTRAP_PUBLIC_KEY"):
        if key in original:
            environment[key] = original[key]
    return environment


def resolve_local_git(environment):
    """Resolve the installed developer binary; the /usr/bin shim needs services.

    This runs before Seatbelt and never accepts a licence, changes settings or
    broadens child permissions. A non-system developer installation fails shut.
    """
    result = subprocess.run(["/usr/bin/xcrun", "--find", "git"], capture_output=True,
                            timeout=10, check=False)
    if result.returncode:
        raise GuardError("No se pudo resolver el Git instalado para el focal aislado.")
    executable = Path(os.fsdecode(result.stdout).strip()).resolve(strict=True)
    allowed = (Path("/Applications/Xcode.app"), Path("/Library/Developer"))
    if (not any(executable.is_relative_to(base) for base in allowed)
            or not executable.is_file() or not os.access(executable, os.X_OK)):
        raise GuardError("El Git instalado queda fuera de las rutas de desarrollo admitidas.")
    environment["JARVIS_TEST_GUARD_GIT"] = str(executable)
    environment["PATH"] = str(executable.parent) + ":" + environment.get("PATH", os.defpath)


def group_alive(pid):
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False


def supervise(command, *, environment, cwd: Path, budget_root: Path, budget_bytes: int,
              deadline: float, data_root: Path, free_floor=10 * GIB,
              max_data_drop=GIB, clock=time.monotonic, poll_seconds=0.2, on_dispatch=None):
    initial_free = available_bytes(data_root)
    if initial_free < free_floor:
        raise GuardError("No se inició la prueba: quedan menos de 10 GiB libres.")
    root_identity = private_directory(budget_root)
    initial_used = allocated_bytes(budget_root)
    tail, peak, reason = bytearray(), initial_used, None
    process = subprocess.Popen(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    if on_dispatch is not None:
        on_dispatch(process.pid)
    assert process.stdout is not None
    requested_signal, previous_handlers = [], {}
    for number in (signal.SIGINT, signal.SIGTERM):
        try:
            previous_handlers[number] = signal.signal(
                number, lambda received, _frame: requested_signal.append(received))
        except ValueError:  # A component caller may be outside the main thread.
            break
    os.set_blocking(process.stdout.fileno(), False)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    started, next_measure, termination = clock(), clock(), None
    try:
        while process.poll() is None or selector.get_map():
            for key, _ in selector.select(timeout=poll_seconds):
                data = os.read(key.fd, 65536)
                if data:
                    tail.extend(data)
                    if len(tail) > LOG_LIMIT:
                        del tail[:-LOG_LIMIT]
                else:
                    selector.unregister(key.fileobj)
            now = clock()
            if requested_signal and reason is None:
                reason = "owner_interrupt"
            if now >= next_measure and reason is None:
                next_measure = now + 0.25
                try:
                    if private_directory(budget_root) != root_identity:
                        reason = "resource_root_changed"
                    used = allocated_bytes(budget_root)
                    peak = max(peak, used)
                    remaining = available_bytes(data_root)
                    if used > budget_bytes:
                        reason = "temporary_budget_exceeded"
                    elif remaining < free_floor or initial_free - remaining > max_data_drop:
                        reason = "global_storage_guard"
                    elif now - started > deadline:
                        reason = "deadline"
                except (OSError, GuardError):
                    reason = "resource_measurement_unconfirmed"
            if reason is not None and termination is None:
                termination = now
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
            if termination is not None and now - termination > 5:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                if now - termination > 8:
                    break
        code = process.wait(timeout=2)
        confirmed = not group_alive(process.pid)
    except BaseException:
        # Preserve the volume on interruption; a group is not universal custody.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=2)
        raise
    finally:
        for number, handler in previous_handlers.items():
            signal.signal(number, handler)
        selector.close()
        process.stdout.close()
    return {"exit_code": code, "stop_reason": reason,
            "elapsed_seconds": round(clock() - started, 3), "peak_temporary_bytes": peak,
            "process_group_exited": confirmed, "custody_scope": "owned_process_group_only",
            "interrupted": reason is not None}, bytes(tail)


def export_artifact(source: Path, destination: Path):
    if (not destination.is_absolute() or destination.parent != Path("/private/tmp")
            or destination.exists() or destination.is_symlink()):
        raise GuardError("La exportación exige un directorio nuevo directamente en /private/tmp.")
    total, entries = 0, []
    pending = [source]
    while pending:
        path = pending.pop()
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) and not stat.S_ISREG(info.st_mode):
            raise GuardError("El artefacto contiene una entrada especial.")
        total += info.st_size if stat.S_ISREG(info.st_mode) else 0
        if total > 256 * MIB or len(entries) > 10_000:
            raise GuardError("El artefacto supera su límite de exportación.")
        entries.append(path)
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(path) as children:
                for child in children:
                    if len(entries) + len(pending) >= 10_000:
                        raise GuardError("El artefacto supera su límite de entradas.")
                    pending.append(Path(child.path))
    destination.mkdir(mode=0o700)
    for path in entries:
        target = destination / source.name / path.relative_to(source)
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            target.mkdir(mode=0o700, parents=True, exist_ok=True)
        else:
            # No source symlink is followed, including a replacement during copy.
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as incoming, target.open("xb") as outgoing:
                if os.fstat(incoming.fileno()).st_size != info.st_size:
                    raise GuardError("El artefacto cambió durante la exportación.")
                copied = 0
                while block := incoming.read(min(MIB, info.st_size - copied + 1)):
                    copied += len(block)
                    if copied > info.st_size:
                        raise GuardError("El artefacto creció durante la exportación.")
                    outgoing.write(block)
                if copied != info.st_size:
                    raise GuardError("El artefacto cambió durante la exportación.")
            target.chmod(0o700 if info.st_mode & 0o111 else 0o600)
    return str(destination / source.name)


def registry_root(repo: Path):
    prefix = Path("/private/tmp" if sys.platform == "darwin" else tempfile.gettempdir())
    path = prefix / ("jarvis-test-resource-" + str(os.getuid()) + "-" +
                     hashlib.sha256(os.fsencode(repo)).hexdigest()[:12])
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    private_directory(path)
    return path


def prune_evidence(registry: Path, *, keep=3, max_age=7 * 86400):
    """Only our closed, unpinned compact records; ambiguous entries are retained."""
    candidates = []
    for path in registry.iterdir():
        if not path.name.startswith("jarvis-test-evidence-") or path.is_symlink():
            continue
        try:
            private_directory(path)
            if (path / "pinned").exists():
                continue
            value = read_private_json(path / "receipt.json")
            if (value.get("format") != FORMAT or value.get("cleanup_status") != "confirmed"
                    or (path / "quota.sparseimage").exists()
                    or any(p.name not in {"admission.json", "receipt.json", "quota.sb", "tail.log",
                                         "preparation.json"}
                           for p in path.iterdir())):
                continue
            if type(value.get("created_ns")) is int:
                candidates.append((value["created_ns"], path))
        except (OSError, ValueError, KeyError, GuardError):
            continue
    for index, (created, path) in enumerate(sorted(candidates, reverse=True)):
        if index >= keep or (time.time_ns() - created) / 1e9 > max_age:
            # Contains only the four bounded metadata files checked above.
            for child in path.iterdir():
                if not stat.S_ISREG(child.lstat().st_mode):
                    break
            else:
                shutil.rmtree(path)


def reject_orphaned_evidence(registry: Path):
    """A killed supervisor cannot silently allocate another quota on restart."""
    for path in registry.iterdir():
        if not path.name.startswith("jarvis-test-evidence-"):
            continue
        try:
            private_directory(path)
            receipt = read_private_json(path / "receipt.json")
        except (OSError, ValueError, GuardError) as error:
            raise GuardError("Cierre sin confirmar; revisa inspect-pending.") from error
        if receipt.get("cleanup_status") != "confirmed" or (path / "quota.sparseimage").exists():
            raise GuardError("Una cuota anterior conserva recursos; no se inicia otra ejecución.")


def run(options):
    root = options.repo_root.resolve(strict=True)
    before = fingerprint(root)
    validate_options(options, os.environ, before, root)
    data_root = Path("/System/Volumes/Data" if sys.platform == "darwin" else "/")
    free_floor = (10 if options.profile == "local" else 2) * GIB
    if available_bytes(data_root) < free_floor:
        raise GuardError("No se preparó la cuota: quedan menos de 10 GiB libres.")
    registry = registry_root(root)
    lock_fd = os.open(registry / "lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        os.close(lock_fd)
        raise GuardError("Otra comprobación conserva el presupuesto del repositorio.") from error
    try:
        if (registry / "cleanup-uncertain.json").exists():
            raise GuardError("Hay una limpieza de pruebas pendiente; no se inicia otra ejecución.")
        reject_orphaned_evidence(registry)
        prune_evidence(registry)
        control = Path(tempfile.mkdtemp(prefix="jarvis-test-evidence-", dir=str(registry)))
        control.chmod(0o700)
        pending_path = registry / "cleanup-uncertain.json"
        write_json(pending_path, {"format": FORMAT, "evidence": str(control),
                   "guard_pid": os.getpid(), "reason": "job_not_yet_confirmed_closed"})
        pending_identity = pending_path.lstat()
        volume = None
        closed = False
        dispatched = []
        result = {"exit_code": None, "stop_reason": "preparation_failed"}
        tail = b""
        try:
            if options.profile == "local":
                volume = create_volume(control, options.budget_mib, options.kind)
                run_root = volume.root
            else:
                # A short physical root preserves Unix-socket path budgets.
                temporary = "/private/tmp" if sys.platform == "darwin" else "/tmp"
                run_root = Path(tempfile.mkdtemp(prefix="jq-", dir=temporary))
                run_root.chmod(0o700)
            for name in ("home", "tmp", "uv", "xdg", "work"):
                (run_root / name).mkdir(mode=0o700)
            admission = {"format": FORMAT, "profile": options.profile, "kind": options.kind,
                "selection": options.selection, "full": options.full,
                "nonce": os.urandom(24).hex(), "source_fingerprint": before["fingerprint"],
                "run_root": str(run_root), "guard_pid": os.getpid()}
            write_json(control / "admission.json", admission)
            command = [part.replace("@RUN_ROOT@", str(run_root)) for part in options.command]
            environment = child_environment(run_root, control, admission, os.environ, root)
            if options.profile == "local":
                resolve_local_git(environment)
                policy = control / "quota.sb"
                policy.write_text(sandbox_policy(root, control, run_root))
                policy.chmod(0o600)
                command = ["/usr/bin/sandbox-exec", "-f", str(policy), *command]
            result, tail = supervise(command, environment=environment, cwd=root,
                budget_root=run_root, budget_bytes=options.budget_mib * MIB,
                deadline=options.deadline, data_root=data_root, free_floor=free_floor,
                max_data_drop=GIB if options.profile == "local" else options.budget_mib * MIB,
                on_dispatch=dispatched.append)
            marker = run_root / "cleanup-uncertain.json"
            closed = result["process_group_exited"] and not marker.exists()
            if closed and result["exit_code"] == 0 and options.export_relative:
                relative = Path(options.export_relative)
                if relative.is_absolute() or ".." in relative.parts:
                    raise GuardError("La exportación no admite rutas relativas ascendentes.")
                result["exported_artifact"] = export_artifact(
                    run_root / relative, options.export_destination)
        except BaseException as error:
            if not dispatched and (not isinstance(error, PreparationError) or not error.pending):
                closed = True
            if result.get("exit_code") == 0:
                result["exit_code"] = 1
                result["stop_reason"] = "artifact_export_failed"
            raise
        finally:
            try:
                after = fingerprint(root)
            except (GuardError, OSError, subprocess.SubprocessError):
                after = {"fingerprint_error": "source_identity_unconfirmed"}
            source_same = before == after
            if volume is not None and closed:
                try:
                    closed = detach_volume(volume)
                except (OSError, subprocess.SubprocessError):
                    closed = False
                if closed:
                    volume.image.unlink()
            elif volume is None and closed and "run_root" in locals():
                shutil.rmtree(run_root)
            log = control / "tail.log"
            descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(tail[-LOG_LIMIT:])
            receipt = {"format": FORMAT, "profile": options.profile, "kind": options.kind,
                "selection": options.selection, "full": options.full, "source_before": before,
                "source_after": after, "source_unchanged": source_same,
                "budget_bytes": options.budget_mib * MIB, "deadline_seconds": options.deadline,
                "quota_mode": "apfs_capacity" if volume is not None else
                    "ci_watchdog_only" if options.profile == "ci" else "not_prepared",
                "quota_capacity_bytes": volume.total_bytes if volume is not None else None,
                "cleanup_status": "confirmed" if closed else "uncertain",
                "log_sha256": hashlib.sha256(tail[-LOG_LIMIT:]).hexdigest(),
                "full_qualification": "pending_remote_ci" if options.profile == "local" else
                    "ci_job_result_requires_workflow_completion", "result": result,
                "created_ns": time.time_ns()}
            write_json(control / "receipt.json", receipt)
            if closed:
                current = pending_path.lstat()
                if (current.st_dev, current.st_ino) != (
                        pending_identity.st_dev, pending_identity.st_ino):
                    raise GuardError("El registro de custodia cambió; no se retiró.")
                pending_path.unlink()
            print(json.dumps({"receipt": str(control / "receipt.json"),
                "cleanup_status": receipt["cleanup_status"], "result": result,
                "source_unchanged": source_same,
                "full_qualification": receipt["full_qualification"]}))
            if options.profile == "ci":
                # CI runs only the synthetic corpus in a fresh checkout. Keep
                # failures diagnosable after its private work directory closes;
                # never dump local corpus/logs automatically to the terminal.
                print(tail[-LOG_LIMIT:].decode("utf-8", errors="replace"), end="")
        passed = closed and source_same and result["exit_code"] == 0
        return 0 if passed and not result.get("interrupted", False) else 1
    finally:
        os.close(lock_fd)


def read_private_json(path: Path):
    if path.name not in {"admission.json", "receipt.json", "cleanup-uncertain.json"}:
        raise GuardError("Sólo se admiten los metadatos propios del supervisor.")
    private_directory(path.parent)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_mode & 0o777 != 0o600
                or info.st_size > RECEIPT_LIMIT):
            raise GuardError("El recibo no es un archivo privado acotado.")
        raw = os.read(descriptor, RECEIPT_LIMIT + 1)
        after = os.fstat(descriptor)
        named = path.lstat()
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if (any(getattr(info, key) != getattr(after, key) for key in fields)
                or (named.st_dev, named.st_ino) != (info.st_dev, info.st_ino)
                or len(raw) != info.st_size):
            raise GuardError("Los metadatos cambiaron durante la lectura.")
    finally:
        os.close(descriptor)
    return json.loads(raw)


def verify_receipt(path: Path, root: Path):
    value = read_private_json(path)
    if (value.get("format") != FORMAT or value.get("cleanup_status") != "confirmed"
            or not value.get("source_unchanged") or value.get("result", {}).get("exit_code") != 0
            or value.get("result", {}).get("interrupted", False)
            or value.get("source_after") != fingerprint(root)):
        raise GuardError("El recibo no acredita un focal cerrado del árbol actual.")
    log = path.parent / "tail.log"
    descriptor = os.open(log, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_size > LOG_LIMIT):
            raise GuardError("El diagnóstico no es un archivo propio acotado.")
        data = os.read(descriptor, LOG_LIMIT + 1)
    finally:
        os.close(descriptor)
    if hashlib.sha256(data).hexdigest() != value.get("log_sha256"):
        raise GuardError("El diagnóstico cambió después de la comprobación.")
    return value


def verify_admission(root: Path, kind: str):
    environment = os.environ
    value = read_private_json(Path(environment.get("JARVIS_TEST_GUARD_ADMISSION", "")))
    expected = {"profile": "JARVIS_TEST_GUARD_PROFILE", "selection": "JARVIS_TEST_GUARD_SELECTION",
                "nonce": "JARVIS_TEST_GUARD_NONCE", "run_root": "JARVIS_TEST_GUARD_RUN_ROOT"}
    if (value.get("format") != FORMAT or value.get("kind") != kind
            or any(value.get(key) != environment.get(env) for key, env in expected.items())
            or value.get("source_fingerprint") != fingerprint(root)["fingerprint"]
            or type(value.get("guard_pid")) is not int or value["guard_pid"] <= 1
            or value["guard_pid"] != os.getppid()):
        raise GuardError("La admisión del comando no corresponde al supervisor actual.")
    private_directory(Path(value["run_root"]))
    if value["guard_pid"] != os.getppid():
        raise GuardError("El supervisor dejó de ser el padre del comando.")
    return value


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="operation", required=True)
    identity = commands.add_parser("fingerprint")
    identity.add_argument("--repo-root", "--root", type=Path, default=REPO)
    verify = commands.add_parser("verify-receipt")
    verify.add_argument("--receipt", type=Path, required=True)
    verify.add_argument("--repo-root", type=Path, default=REPO)
    pending = commands.add_parser("inspect-pending")
    pending.add_argument("--repo-root", type=Path, default=REPO)
    admission = commands.add_parser("verify-admission")
    admission.add_argument("--repo-root", type=Path, default=REPO)
    admission.add_argument("--kind", choices=("python", "swift", "build"), required=True)
    admitted = commands.add_parser("admitted-exec")
    admitted.add_argument("--repo-root", type=Path, default=REPO)
    admitted.add_argument("--kind", choices=("swift", "build"), required=True)
    admitted.add_argument("command", nargs=argparse.REMAINDER)
    execute = commands.add_parser("run")
    execute.add_argument("--repo-root", type=Path, default=REPO)
    execute.add_argument("--profile", choices=("local", "ci"), required=True)
    execute.add_argument("--kind", choices=("python", "swift", "build"), required=True)
    execute.add_argument("--selection", required=True)
    execute.add_argument("--budget-mib", type=int, required=True)
    execute.add_argument("--deadline", type=float, required=True)
    execute.add_argument("--full", action="store_true")
    execute.add_argument("--export-relative")
    execute.add_argument("--export-destination", type=Path)
    execute.add_argument("command", nargs=argparse.REMAINDER)
    return result


def main():
    options = parser().parse_args()
    try:
        if options.operation == "fingerprint":
            print(json.dumps(fingerprint(options.repo_root)))
            return 0
        if options.operation == "verify-receipt":
            value = verify_receipt(options.receipt, options.repo_root)
            print(json.dumps({"verified": True, "profile": value["profile"],
                              "selection": value["selection"],
                              "full_qualification": value["full_qualification"]}))
            return 0
        if options.operation == "verify-admission":
            verify_admission(options.repo_root, options.kind)
            return 0
        if options.operation == "admitted-exec":
            value = verify_admission(options.repo_root, options.kind)
            command = options.command[1:] if options.command[:1] == ["--"] else options.command
            if not command:
                raise GuardError("Falta el comando interno admitido.")
            environment = dict(os.environ)
            environment["JARVIS_TEST_GUARD_CHILD_CHECKED"] = value["nonce"]
            os.execvpe(command[0], command, environment)
        if options.operation == "inspect-pending":
            path = registry_root(options.repo_root.resolve()) / "cleanup-uncertain.json"
            value = read_private_json(path) if path.exists() else {"pending": False}
            print(json.dumps(value))
            return 0
        if options.command[:1] == ["--"]:
            options.command = options.command[1:]
        if bool(options.export_relative) != bool(options.export_destination):
            raise GuardError("La exportación exige origen y destino explícitos.")
        return run(options)
    except (GuardError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"[jarvis-test-guard] {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
