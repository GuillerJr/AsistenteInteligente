"""Own bounded process supervisor with a fail-closed operating-system sandbox.

The host supplies the workspace and conversation identity; the model supplies
only a reviewed argv. macOS denies forking, network and files outside the selected
workspace/system runtime. Linux requires bubblewrap namespaces. This is not a
defence against malicious separate same-uid host processes racing workspace paths.
Exit zero attests a process outcome, never independent verification of its effects.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from .runtime import ToolDefinition, ToolResult
from .tools import ToolCancelled, ToolError, ToolSpec, check_tool_authority
from .workspace import _PRIVATE, WorkspaceError, WorkspaceTools

_ID = re.compile(r"[a-f0-9]{32}")
_WRAPPER = 'set -e; ulimit -c 0; ulimit -n 64; ulimit -t 30; ulimit -f 16384; exec "$@"'
_SYSTEM_READS = (
    "/System/Library",
    "/System/Cryptexes",
    "/System/Volumes/Preboot/Cryptexes",
    "/bin",
    "/sbin",
    "/usr/bin",
    "/usr/sbin",
    "/usr/lib",
    "/usr/libexec",
    "/usr/share",
)


class ProcessError(ToolError):
    """Stable public codes do not expose argv, environment or private paths."""


def _private_name(name: str) -> bool:
    name = name.casefold()
    return (
        name in _PRIVATE
        or name.startswith((".env.", ".jarvis-tmp-", ".configuration-"))
        or name.endswith((".p12", ".pfx", ".key"))
    )


def _regex_name(name: str) -> str:
    # SBPL's #"" literal preserves backslashes. Fixed character classes avoid
    # double-escaping through JSON and enforce case folding on case-sensitive disks.
    return "".join(
        "[.]" if char == "." else f"[{char.lower()}{char.upper()}]" if char.isalpha() else char
        for char in name
    )


def _framework_runtime_root(executable: Path) -> Path | None:
    # python.org and macOS CI install the host interpreter outside Homebrew.
    # Admit only that exact resolved interpreter and its matching version, never
    # a neighbouring executable or a broad /Library/Frameworks read grant.
    if sys.platform != "darwin" or executable != Path(sys.executable).resolve():
        return None
    major, minor = sys.version_info[:2]
    version = f"{major}.{minor}"
    root = Path("/Library/Frameworks/Python.framework/Versions") / version
    try:
        relative = executable.relative_to(root)
    except ValueError:
        return None
    if relative.parts not in {
        ("bin", "python"),
        ("bin", f"python{major}"),
        ("bin", f"python{version}"),
        ("Resources", "Python.app", "Contents", "MacOS", "Python"),
    }:
        return None
    return root


def _runtime_roots(executable: Path) -> tuple[Path, ...]:
    roots = set()
    for candidate in (executable, Path(sys.executable).resolve()):
        parts = candidate.parts
        if len(parts) > 5 and parts[:4] == ("/", "opt", "homebrew", "Cellar"):
            roots.add(Path(*parts[:6]))
        elif len(parts) > 5 and parts[:4] == ("/", "usr", "local", "Cellar"):
            roots.add(Path(*parts[:6]))
    framework = _framework_runtime_root(executable)
    if framework is not None:
        roots.add(framework)
    return tuple(sorted(roots))


def _mac_profile(root: Path, executable: Path) -> str:
    readable = list(_SYSTEM_READS)
    readable.extend(str(item) for item in _runtime_roots(executable))
    filters = " ".join(f"(subpath {json.dumps(path, ensure_ascii=False)})" for path in readable)
    private = [".*/(" + _regex_name(name) + ")(/.*)?$" for name in sorted(_PRIVATE)]
    extra = [
        ".*/" + _regex_name(prefix) + "[^/]*(/.*)?$"
        for prefix in (".env.", ".jarvis-tmp-", ".configuration-")
    ] + [
        r".*[.]([pP]12|[pP][fF][xX]|[kK][eE][yY])$",
    ]
    deny_filters = " ".join("#" + json.dumps(value) for value in (*private, *extra))
    return (
        "(version 1)(deny default)(allow process-exec)(deny process-fork)"
        "(allow sysctl-read)(allow file-read-metadata)"
        # dyld needs the root directory itself, not an unbounded subpath '/'.
        f'(allow file-read* (literal "/") {filters} '
        '(literal "/dev/null") (literal "/dev/random") (literal "/dev/urandom") '
        '(literal "/private/etc/localtime"))'
        f"(allow file-read* file-write* (subpath {json.dumps(str(root), ensure_ascii=False)}))"
        f"(deny file-read* file-write* (regex {deny_filters}))"
    )


@dataclass
class _Entry:
    process_id: str
    owner: str
    process: asyncio.subprocess.Process
    started_ns: int
    argv: tuple[str, ...]
    output: bytearray = field(default_factory=bytearray)
    output_seen: int = 0
    digest: object = field(default_factory=hashlib.sha256)
    reason: str | None = None
    stdin_closed: bool = False
    output_truncated: bool = False
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    pump_tasks: tuple[asyncio.Task, ...] = ()
    watcher: asyncio.Task | None = None
    timer: asyncio.Task | None = None


class ProcessTools:
    def __init__(
        self,
        workspace: WorkspaceTools,
        scope: Callable[[], str],
        *,
        max_processes: int = 8,
        max_output_bytes: int = 65536,
        max_wall_seconds: float = 120,
    ) -> None:
        if (
            not isinstance(workspace, WorkspaceTools)
            or not callable(scope)
            or type(max_processes) is not int
            or not 1 <= max_processes <= 32
            or type(max_output_bytes) is not int
            or not 1024 <= max_output_bytes <= 65536
            or type(max_wall_seconds) not in {int, float}
            or not 0.05 <= max_wall_seconds <= 300
        ):
            raise ProcessError("process_configuration_invalid")
        self.workspace, self.scope = workspace, scope
        self.max_processes, self.max_output_bytes = max_processes, max_output_bytes
        self.max_wall_seconds = max_wall_seconds
        self._entries: dict[str, _Entry] = {}
        self._starting = 0
        self._calls: dict[asyncio.Task, str] = {}
        self._closing_scopes: set[str] = set()
        self._closed = False
        self._close_task: asyncio.Task | None = None
        self.child_custody = None
        if sys.platform == "darwin":
            self._backend = "macos_seatbelt"
            self._helper = (
                "/usr/bin/sandbox-exec" if Path("/usr/bin/sandbox-exec").is_file() else None
            )
        elif sys.platform.startswith("linux"):
            self._backend = "linux_bubblewrap"
            helper = shutil.which("bwrap")
            self._helper = helper if helper and self._linux_supported(helper) else None
        else:
            self._backend, self._helper = "unsupported", None

    @staticmethod
    def _linux_supported(helper: str) -> bool:
        # A PATH entry is not proof of confinement. Only an unprivileged,
        # root-owned immutable-to-users helper may probe the kernel with a fixed
        # /bin/true in an empty private namespace; no owner path or model argv.
        try:
            path = Path(helper).resolve(strict=True)
            if str(path) not in {"/usr/bin/bwrap", "/bin/bwrap"}:
                return False
            info = path.stat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != 0
                or info.st_mode & (0o022 | stat.S_ISUID | stat.S_ISGID)
            ):
                return False
            command = [helper, "--unshare-all", "--die-with-parent", "--new-session", "--clearenv"]
            for path in ("/usr", "/bin", "/lib", "/lib64"):
                if Path(path).exists():
                    command.extend(("--ro-bind", path, path))
            command.extend(("--chdir", "/", "--", "/bin/true"))
            return (
                subprocess.run(
                    command,
                    cwd="/",
                    env={"PATH": "/usr/bin:/bin"},
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                    start_new_session=True,
                    check=False,
                ).returncode
                == 0
            )
        except (OSError, subprocess.SubprocessError):
            return False

    def status(self) -> dict:
        return {
            "available": self._helper is not None and not self._closed,
            "backend": self._backend,
            "network": "denied",
            "confinement": "workspace_and_readonly_runtime",
            "fork": "denied" if self._backend == "macos_seatbelt" else "pid_namespace",
            "effects_independently_verified": False,
            "max_wall_seconds": self.max_wall_seconds,
            "max_processes": self.max_processes,
            "max_output_bytes": self.max_output_bytes,
        }

    @property
    def has_running(self) -> bool:
        return bool(
            self._starting or any(not item.finished.is_set() for item in self._entries.values())
        )

    @staticmethod
    def _owner(value: str) -> str:
        if not isinstance(value, str) or not 1 <= len(value.encode()) <= 256:
            raise ProcessError("process_scope_required")
        return value

    def _current_owner(self) -> str:
        return self._owner(self.scope())

    def _lookup(self, owner: str, process_id: str) -> _Entry:
        self._owner(owner)
        if not isinstance(process_id, str) or _ID.fullmatch(process_id) is None:
            raise ProcessError("process_id_invalid")
        entry = self._entries.get(process_id)
        if entry is None or entry.owner != owner:
            raise ProcessError("process_not_found")
        return entry

    def _check_workspace(self) -> None:
        # Reopen the physical root rather than trusting a cached pathname.
        current = WorkspaceTools(self.workspace.root)
        if current.identity != self.workspace.identity:
            raise ProcessError("process_workspace_changed")
        if any(ord(char) < 32 for char in str(self.workspace.root)):
            raise ProcessError("process_workspace_path_invalid")
        count = 0
        stack = [(self.workspace.root, 0)]
        while stack:
            directory, depth = stack.pop()
            if depth > 32:
                raise ProcessError("process_workspace_limit")
            with os.scandir(directory) as children:
                for child in children:
                    count += 1
                    if count > 4096:
                        raise ProcessError("process_workspace_limit")
                    if _private_name(child.name):
                        continue  # Explicitly denied by the sandbox, never opened.
                    info = child.stat(follow_symlinks=False)
                    if (
                        stat.S_ISLNK(info.st_mode)
                        or info.st_uid != os.getuid()
                        or info.st_mode & 0o022
                        or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)
                    ):
                        raise ProcessError("process_workspace_unsafe_entry")
                    if stat.S_ISDIR(info.st_mode):
                        stack.append((Path(child.path), depth + 1))
                    elif not stat.S_ISREG(info.st_mode):
                        raise ProcessError("process_workspace_unsafe_entry")

    @staticmethod
    def _validate(name: str, arguments: Mapping) -> None:
        if not isinstance(arguments, Mapping):
            raise ProcessError("process_arguments_invalid")
        allowed = {
            "process_run": {"argv", "wait_ms", "timeout_seconds"},
            "process_poll": {"process_id", "cursor"},
            "process_send": {"process_id", "text", "close_stdin"},
            "process_stop": {"process_id"},
        }[name]
        required = {"argv"} if name == "process_run" else {"process_id"}
        if name == "process_send":
            required.add("text")
        if set(arguments) - allowed or required - set(arguments):
            raise ProcessError("process_arguments_invalid")
        if name == "process_run":
            argv = arguments["argv"]
            if (
                not isinstance(argv, (list, tuple))
                or not 1 <= len(argv) <= 64
                or any(
                    not isinstance(value, str) or "\0" in value or len(value.encode()) > 8192
                    for value in argv
                )
                or not argv[0]
                or not Path(argv[0]).is_absolute()
                or sum(len(value.encode()) for value in argv) > 32768
            ):
                raise ProcessError("process_argv_invalid")
            wait = arguments.get("wait_ms", 1000)
            timeout = arguments.get("timeout_seconds", 120)
            if (
                type(wait) is not int
                or not 0 <= wait <= 10000
                or type(timeout) not in {int, float}
                or not 0.05 <= timeout <= 300
            ):
                raise ProcessError("process_deadline_invalid")
        else:
            if (
                not isinstance(arguments["process_id"], str)
                or _ID.fullmatch(arguments["process_id"]) is None
            ):
                raise ProcessError("process_id_invalid")
        if "cursor" in arguments and (
            type(arguments["cursor"]) is not int or not 0 <= arguments["cursor"] <= 65536
        ):
            raise ProcessError("process_cursor_invalid")
        if name == "process_send" and (
            not isinstance(arguments["text"], str)
            or len(arguments["text"].encode()) > 4096
            or type(arguments.get("close_stdin", False)) is not bool
        ):
            raise ProcessError("process_stdin_invalid")

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(
            ToolDefinition(
                name,
                lambda arguments, name=name: self._validate(name, arguments),
                lambda arguments, cancellation, name=name: self._execute(
                    name, arguments, cancellation
                ),
                requires_confirmation=name != "process_poll",
            )
            for name in ("process_run", "process_poll", "process_send", "process_stop")
        )

    def specs(self) -> tuple[ToolSpec, ...]:
        fields = {
            "argv": {
                "type": "array",
                "minItems": 1,
                "maxItems": 64,
                "items": {"type": "string", "maxLength": 8192},
            },
            "wait_ms": {"type": "integer", "minimum": 0, "maximum": 10000},
            "timeout_seconds": {"type": "number", "minimum": 0.05, "maximum": 300},
            "process_id": {"type": "string", "pattern": "^[a-f0-9]{32}$"},
            "cursor": {"type": "integer", "minimum": 0, "maximum": 65536},
            "text": {"type": "string", "maxLength": 4096},
            "close_stdin": {"type": "boolean"},
        }
        entries = (
            (
                "process_run",
                "Ejecuta argv exacto en la carpeta con sandbox sin red. "
                "Requiere aprobación. Devuelve identidad propia si sigue activo; "
                "exit 0 no verifica efectos.",
                ("argv", "wait_ms", "timeout_seconds"),
                ("argv",),
            ),
            (
                "process_poll",
                "Consulta estado y hasta 4096 bytes de salida de un proceso de este chat.",
                ("process_id", "cursor"),
                ("process_id",),
            ),
            (
                "process_send",
                "Envía texto exacto a stdin de un proceso de este chat con aprobación. "
                "close_stdin cierra su entrada después de enviar; no reintenta entregas inciertas.",
                ("process_id", "text", "close_stdin"),
                ("process_id", "text"),
            ),
            (
                "process_stop",
                "Detiene y recoge un proceso propio con aprobación. "
                "No prueba ni revierte sus cambios anteriores.",
                ("process_id",),
                ("process_id",),
            ),
        )
        return tuple(
            ToolSpec(
                name,
                description,
                {
                    "type": "object",
                    "properties": {key: fields[key] for key in keys},
                    "required": list(required),
                    "additionalProperties": False,
                },
            )
            for name, description, keys, required in entries
        )

    def _command(self, argv: tuple[str, ...]) -> tuple[str, ...]:
        if self._helper is None:
            raise ProcessError("process_sandbox_unavailable")
        executable = Path(argv[0]).resolve(strict=True)
        info = executable.stat()
        if (
            not stat.S_ISREG(info.st_mode)
            or not os.access(executable, os.X_OK)
            or any(_private_name(part) for part in executable.parts)
            or info.st_mode & (stat.S_ISUID | stat.S_ISGID)
        ):
            raise ProcessError("process_executable_invalid")
        trusted = any(
            executable.is_relative_to(Path(path))
            for path in (
                "/bin",
                "/sbin",
                "/usr/bin",
                "/usr/sbin",
                "/System/Library",
                "/System/Cryptexes",
                "/System/Volumes/Preboot/Cryptexes",
                "/opt/homebrew/Cellar",
                "/usr/local/Cellar",
            )
        )
        if self._backend == "macos_seatbelt" and _framework_runtime_root(executable) is not None:
            trusted = True
        if not trusted and not executable.is_relative_to(self.workspace.root):
            raise ProcessError("process_executable_outside_scope")
        actual = (str(executable), *argv[1:])
        if self._backend == "macos_seatbelt":
            return (
                self._helper,
                "-p",
                _mac_profile(self.workspace.root, executable),
                "/bin/sh",
                "-c",
                _WRAPPER,
                "jarvis-process",
                *actual,
            )
        command = [
            self._helper,
            "--unshare-all",
            "--new-session",
            "--die-with-parent",
            "--clearenv",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--dir",
            "/tmp",
        ]
        for path in ("/usr", "/bin", "/sbin", "/lib", "/lib64"):
            if Path(path).exists():
                command.extend(("--ro-bind", path, path))
        command.extend(
            (
                "--bind",
                str(self.workspace.root),
                "/workspace",
                "--chdir",
                "/workspace",
                "--setenv",
                "HOME",
                "/workspace",
                "--setenv",
                "TMPDIR",
                "/workspace",
                "--setenv",
                "PATH",
                "/usr/bin:/bin:/usr/sbin:/sbin",
            )
        )
        # Linux's mount namespace cannot express Seatbelt's arbitrary name filters.
        # Reject private names rather than silently grant them inside the bind.
        for _directory, names, files in os.walk(self.workspace.root):
            if any(_private_name(name) for name in (*names, *files)):
                raise ProcessError("process_workspace_private_entry")
        if executable.is_relative_to(self.workspace.root):
            actual = ("/workspace/" + str(executable.relative_to(self.workspace.root)), *actual[1:])
        elif not str(executable).startswith(("/usr/", "/bin/", "/sbin/")):
            raise ProcessError("process_runtime_unavailable")
        return (*command, "--", "/bin/sh", "-c", _WRAPPER, "jarvis-process", *actual)

    async def _pump(self, entry: _Entry, reader: asyncio.StreamReader) -> None:
        while chunk := await reader.read(4096):
            entry.output_seen += len(chunk)
            entry.digest.update(chunk)
            room = self.max_output_bytes - len(entry.output)
            entry.output.extend(chunk[: max(room, 0)])
            if len(chunk) > room:
                entry.output_truncated = True
                entry.reason = entry.reason or "output_limit"
                self._signal(entry)
            await asyncio.sleep(0)

    @staticmethod
    def _signal(entry: _Entry) -> None:
        if entry.process.returncode is None:
            try:
                # Darwin forbids fork in the selected sandbox, so the owned PID
                # is the whole workload. On Linux, bubblewrap owns the PID
                # namespace and --die-with-parent kills its namespace init.
                # A negative-PID group signal can be rejected by macOS even for
                # an owned sandbox child; never broaden a signal to host PIDs.
                entry.process.kill()
            except ProcessLookupError:
                pass

    async def _watch(self, entry: _Entry) -> None:
        try:
            await entry.process.wait()
            await asyncio.gather(*entry.pump_tasks)
        except Exception:
            entry.reason = entry.reason or "output_unconfirmed"
            try:
                self._signal(entry)
            except OSError:
                entry.reason = "termination_unconfirmed"
            await entry.process.wait()
            for task in entry.pump_tasks:
                task.cancel()
            await asyncio.gather(*entry.pump_tasks, return_exceptions=True)
        finally:
            if entry.timer and entry.process.returncode is not None:
                entry.timer.cancel()
            if (
                entry.process.stdin
                and not entry.stdin_closed
                and entry.process.returncode is not None
            ):
                entry.stdin_closed = True
                entry.process.stdin.close()
            if entry.process.returncode is not None:
                entry.finished.set()

    async def _deadline(self, entry: _Entry, seconds: float) -> None:
        await asyncio.sleep(seconds)
        if not entry.finished.is_set():
            entry.reason = entry.reason or "timed_out"
            try:
                self._signal(entry)
            except OSError:
                entry.reason = "termination_unconfirmed"

    def poll(self, scope: str, process_id: str, cursor: int = 0) -> dict:
        entry = self._lookup(scope, process_id)
        if type(cursor) is not int or not 0 <= cursor <= len(entry.output):
            raise ProcessError("process_cursor_invalid")
        end = min(cursor + 4096, len(entry.output))
        state = (
            "running"
            if not entry.finished.is_set()
            else entry.reason or ("completed" if entry.process.returncode == 0 else "failed")
        )
        return {
            "process_id": process_id,
            "state": state,
            "running": not entry.finished.is_set(),
            "exit_code": entry.process.returncode,
            "termination_reason": entry.reason,
            "started_ns": entry.started_ns,
            "output": bytes(entry.output[cursor:end]).decode("utf-8", errors="replace"),
            "cursor": cursor,
            "next_cursor": end,
            "has_more": end < len(entry.output),
            "output_bytes": entry.output_seen,
            "retained_bytes": len(entry.output),
            "output_sha256": entry.digest.hexdigest(),
            "output_truncated": entry.output_truncated,
            "backend": self._backend,
            "verification": "process_state_only",
            "effects_independently_verified": False,
        }

    def list_processes(self, scope: str) -> tuple[dict, ...]:
        self._owner(scope)
        results = []
        for key, entry in self._entries.items():
            if entry.owner == scope:
                data = self.poll(scope, key)
                end = min(256, len(entry.output))
                data.update(
                    output=bytes(entry.output[:end]).decode("utf-8", errors="replace"),
                    next_cursor=end,
                    has_more=end < len(entry.output),
                )
                results.append(data)
        return tuple(results)

    async def _stop(self, entry: _Entry, reason: str) -> None:
        if not entry.finished.is_set():
            entry.reason = entry.reason or reason
            self._signal(entry)
            await asyncio.wait_for(entry.finished.wait(), 3)
        await asyncio.gather(
            *entry.pump_tasks, *filter(None, (entry.watcher, entry.timer)), return_exceptions=True
        )

    @staticmethod
    async def _settled(task: asyncio.Task):
        # Once spawning begins, even repeated cancellation must wait for the
        # transport and owned child to become visible to cleanup.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        return task.result()

    async def _spawn(
        self, args: Mapping, command: tuple[str, ...], owner: str, cancellation: asyncio.Event
    ) -> _Entry:
        if self._closed or owner in self._closing_scopes or cancellation.is_set():
            raise ProcessError("process_scope_closed")
        self._check_workspace()
        # This runs in the inherited authority context immediately before Popen.
        # The caller shields it until the new process has an owned cleanup entry.
        check_tool_authority("process_run", args, commit=True)
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=self.workspace.root,
            env={
                "HOME": str(self.workspace.root),
                "TMPDIR": str(self.workspace.root),
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                "LANG": "C",
                "PYTHONUTF8": "1",
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            pass_fds=self.child_custody() if self.child_custody else (),
            limit=16384,
        )
        entry = _Entry(uuid4().hex, owner, process, time.time_ns(), tuple(args["argv"]))
        self._entries[entry.process_id] = entry
        assert process.stdout is not None and process.stderr is not None
        entry.pump_tasks = tuple(
            asyncio.create_task(self._pump(entry, stream))
            for stream in (process.stdout, process.stderr)
        )
        entry.timer = asyncio.create_task(
            self._deadline(
                entry,
                min(args.get("timeout_seconds", self.max_wall_seconds), self.max_wall_seconds),
            )
        )
        entry.watcher = asyncio.create_task(self._watch(entry))
        if self._closed or owner in self._closing_scopes or cancellation.is_set():
            await self._stop(entry, "cancelled")
        return entry

    async def _run(self, args: Mapping, cancellation: asyncio.Event, owner: str) -> ToolResult:
        if self._closed or owner in self._closing_scopes:
            raise ProcessError("process_manager_closed")
        # Retain finite result history; only settled entries can be evicted.
        if len(self._entries) >= 64:
            oldest = next(
                (key for key, item in self._entries.items() if item.finished.is_set()), None
            )
            if oldest is None:
                raise ProcessError("process_capacity")
            self._entries.pop(oldest)
        if (
            self._starting + sum(not item.finished.is_set() for item in self._entries.values())
            >= self.max_processes
        ):
            raise ProcessError("process_capacity")
        self._check_workspace()
        command = self._command(tuple(args["argv"]))
        self._starting += 1
        task = asyncio.current_task()
        assert task is not None
        self._calls[task] = owner
        entry = None
        spawning = None
        try:
            if cancellation.is_set():
                raise asyncio.CancelledError
            spawning = asyncio.create_task(self._spawn(args, command, owner, cancellation))
            entry = await asyncio.shield(spawning)
            cancelled = asyncio.create_task(cancellation.wait())
            finished = asyncio.create_task(entry.finished.wait())
            try:
                await asyncio.wait(
                    (cancelled, finished),
                    timeout=args.get("wait_ms", 1000) / 1000,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if cancellation.is_set():
                    await self._stop(entry, "cancelled")
                    raise asyncio.CancelledError
            finally:
                for task in (cancelled, finished):
                    task.cancel()
                await asyncio.gather(cancelled, finished, return_exceptions=True)
            return self._receipt("process_run", entry)
        except asyncio.CancelledError:
            if spawning is not None and entry is None:
                try:
                    entry = await self._settled(spawning)
                except (ToolError, OSError):
                    pass
            if entry:
                await self._settled(asyncio.create_task(self._stop(entry, "cancelled")))
            raise ToolCancelled(
                ToolResult(
                    "process_run", False, False, error="process_cancelled", effect_status="unknown"
                )
            ) from None
        finally:
            self._calls.pop(task, None)
            self._starting -= 1

    def _receipt(self, name: str, entry: _Entry) -> ToolResult:
        data = self.poll(entry.owner, entry.process_id)
        uncertain = entry.reason in {
            "timed_out",
            "cancelled",
            "output_limit",
            "stdin_unconfirmed",
            "output_unconfirmed",
            "termination_unconfirmed",
        }
        okay = (entry.process.returncode in (None, 0) or name == "process_stop") and not uncertain
        return ToolResult(
            name,
            okay,
            False,
            output=json.dumps(data, ensure_ascii=False),
            error=None if okay else "process_" + (entry.reason or "exit_nonzero"),
            effect_status="unknown" if uncertain else "known",
        )

    async def _execute(self, name: str, args: Mapping, cancellation: asyncio.Event) -> ToolResult:
        self._validate(name, args)
        owner = self._current_owner()
        dispatched = False
        try:
            if name == "process_run":
                return await self._run(args, cancellation, owner)
            if name == "process_poll":
                return ToolResult(
                    name,
                    True,
                    False,
                    output=json.dumps(
                        self.poll(owner, args["process_id"], args.get("cursor", 0)),
                        ensure_ascii=False,
                    ),
                )
            entry = self._lookup(owner, args["process_id"])
            if cancellation.is_set():
                raise asyncio.CancelledError
            check_tool_authority(name, args)
            if name == "process_stop":
                if not entry.finished.is_set():
                    check_tool_authority(name, args, commit=True)
                    dispatched = True
                    await self._stop(entry, "stopped")
                return self._receipt(name, entry)
            if entry.finished.is_set() or entry.stdin_closed or not entry.process.stdin:
                raise ProcessError("process_stdin_closed")
            check_tool_authority(name, args, commit=True)
            dispatched = True
            entry.process.stdin.write(args["text"].encode())
            try:
                await asyncio.wait_for(entry.process.stdin.drain(), 2)
                if args.get("close_stdin", False):
                    entry.stdin_closed = True
                    entry.process.stdin.close()
                if cancellation.is_set():
                    raise asyncio.CancelledError
            except (OSError, TimeoutError, asyncio.CancelledError):
                entry.reason = "stdin_unconfirmed"
                await self._stop(entry, "stdin_unconfirmed")
                return self._receipt(name, entry)
            return self._receipt(name, entry)
        except (ProcessError, WorkspaceError, OSError) as error:
            code = (
                str(error)
                if isinstance(error, (ProcessError, WorkspaceError))
                else "process_unavailable"
            )
            return ToolResult(
                name, False, False, error=code, effect_status="unknown" if dispatched else "known"
            )

    async def close_scope(self, scope: str) -> None:
        self._owner(scope)
        self._closing_scopes.add(scope)
        active = tuple(task for task, owner in self._calls.items() if owner == scope)
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)
        for key, entry in tuple(self._entries.items()):
            if entry.owner == scope:
                await self._stop(entry, "scope_closed")
                self._entries.pop(key, None)

    async def aclose(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        active = tuple(self._calls)
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)
        for entry in tuple(self._entries.values()):
            await self._stop(entry, "manager_closed")
        self._entries.clear()
