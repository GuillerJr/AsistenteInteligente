"""Own descriptor-based tools for one explicitly selected local workspace.

No shell, recursive discovery, credential/history import or path outside the
selected directory is provided. Descriptor walks reject symlinks and hardlinked
files. Replacement is atomic and its result independently reread and hashed.
The optimistic version check detects changes observed before replacement; an OS
sandbox is still required before claiming protection from hostile same-uid code.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from .runtime import ToolDefinition, ToolResult
from .tools import ToolError, ToolSpec, check_tool_authority, revalidate_committed_tool_authority

_PRIVATE = {
    "auth.json", "history", "history.json", "chat_history.json", "conversations.json",
    ".ssh", ".codex", ".aws", ".gnupg", ".git", ".jarvis", ".venv", ".venv.nosync",
    "node_modules", ".env", ".zsh_history", ".bash_history", ".python_history",
    ".git-credentials", ".npmrc", ".netrc", "credentials.json", "cookies", "login data",
    "keychains", "id_rsa", "id_ed25519",
    ".ds_store", ".trash", ".fseventsd", ".spotlight-v100", ".documentrevisions-v100",
    "transport.token", "jarvis.sqlite3", "jarvis.sqlite3-wal", "jarvis.sqlite3-shm",
    "jarvis.sqlite3-journal",
}
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


class WorkspaceError(ValueError):
    pass


def _parts(path: str, *, root_allowed: bool = False) -> tuple[str, ...]:
    if (not isinstance(path, str) or len(path.encode()) > 2048 or "\0" in path
            or "\\" in path or path.startswith("/") or (path.endswith("/") and path)):
        raise WorkspaceError("invalid_workspace_path")
    if not path and root_allowed:
        return ()
    parts = tuple(path.split("/"))
    if any(not part or part in {".", ".."} or len(part.encode()) > 255 for part in parts):
        raise WorkspaceError("invalid_workspace_path")
    for part in parts:
        name = part.casefold()
        if (name in _PRIVATE or name.startswith((".env.", ".jarvis-tmp-", ".configuration-"))
                or name.endswith((".p12", ".pfx", ".key"))):
            raise WorkspaceError("workspace_private_path")
    return parts


def _json(value: Any) -> str:
    output = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(output.encode()) > 24000:
        raise WorkspaceError("workspace_output_limit")
    return output


def _fingerprint(info: os.stat_result) -> tuple:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


class WorkspaceTools:
    def __init__(self, root: Path, *, max_file_bytes: int = 32000) -> None:
        root = Path(root)
        if (not root.is_absolute() or ".." in root.parts or type(max_file_bytes) is not int
                or not 1 <= max_file_bytes <= 32000):
            raise WorkspaceError("invalid_workspace")
        if any(component.casefold() in _PRIVATE for component in root.parts):
            raise WorkspaceError("workspace_private_path")
        self._root, self.max_file_bytes = root, max_file_bytes
        try:
            descriptor = self._open_absolute()
            info = os.fstat(descriptor)
            os.close(descriptor)
        except OSError:
            raise WorkspaceError("workspace_unavailable") from None
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise WorkspaceError("unsafe_workspace_permissions")
        self._identity = info.st_dev, info.st_ino
        self._definitions = tuple(ToolDefinition(
            name, lambda arguments, name=name: self._validate(name, arguments),
            lambda arguments, cancellation, name=name: self._execute(name, arguments, cancellation),
            requires_confirmation=name in {"workspace_write", "workspace_edit", "workspace_mkdir",
                                           "workspace_patch"},
        ) for name in ("workspace_list", "workspace_read", "workspace_write", "workspace_edit",
                       "workspace_mkdir", "workspace_patch"))

    @property
    def root(self) -> Path:
        return self._root

    @property
    def identity(self) -> tuple[int, int]:
        return self._identity

    def _open_absolute(self) -> int:
        descriptor = os.open(self.root.anchor, _DIRECTORY)
        try:
            for component in self.root.parts[1:]:
                next_descriptor = os.open(component, _DIRECTORY, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    @contextmanager
    def _parent(self, parts: tuple[str, ...]):
        descriptor = self._open_absolute()
        try:
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != self._identity:
                raise WorkspaceError("workspace_identity_changed")
            if info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise WorkspaceError("unsafe_workspace_permissions")
            for component in parts:
                next_descriptor = os.open(component, _DIRECTORY, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
                info = os.fstat(descriptor)
                if info.st_uid != os.getuid() or info.st_mode & 0o022:
                    raise WorkspaceError("unsafe_workspace_permissions")
            yield descriptor
        finally:
            os.close(descriptor)

    def _same_parent(self, parts: tuple[str, ...], descriptor: int) -> None:
        with self._parent(parts) as reopened:
            original, current = os.fstat(descriptor), os.fstat(reopened)
            if (original.st_dev, original.st_ino) != (current.st_dev, current.st_ino):
                raise WorkspaceError("workspace_parent_changed")

    def _read(self, parent: int, name: str, *, limit: int | None = None
              ) -> tuple[bytes, os.stat_result]:
        limit = self.max_file_bytes if limit is None else limit
        if type(limit) is not int or not 1 <= limit <= 65536:
            raise WorkspaceError("workspace_file_limit")
        descriptor = os.open(name, _FILE, dir_fd=parent)
        try:
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                    or before.st_nlink != 1):
                raise WorkspaceError("workspace_unsafe_file")
            if before.st_size > limit:
                raise WorkspaceError("workspace_file_limit")
            chunks, size = [], 0
            while True:
                chunk = os.read(descriptor, min(4096, limit + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise WorkspaceError("workspace_file_limit")
                chunks.append(chunk)
            if _fingerprint(before) != _fingerprint(os.fstat(descriptor)):
                raise WorkspaceError("workspace_file_changed")
            return b"".join(chunks), before
        finally:
            os.close(descriptor)

    def _target(self, parent: int, name: str, expected: str | None) -> None:
        try:
            data, info = self._read(parent, name)
        except FileNotFoundError:
            if expected is not None:
                raise WorkspaceError("workspace_version_conflict") from None
            return
        if (expected is None or hashlib.sha256(data).hexdigest() != expected
                or not info.st_mode & stat.S_IWUSR):
            raise WorkspaceError("workspace_version_conflict")

    def publish_download(self, path: str, payload: bytes, *, arguments: Mapping,
                         guard: Callable[[], None]) -> dict:
        """Publish one reviewed browser download; never overwrite or infer a path.

        The browser tool already committed its exact authority before its native
        fetch. Continuation must retain that same lease, plus the host's fresh
        profile/target/cancellation guard; no second tool permission is created.
        """
        parts = _parts(path)
        if (not callable(guard) or not isinstance(arguments, Mapping)
                or arguments.get("path") != path or type(payload) is not bytes
                or len(payload) > 65536):
            raise WorkspaceError("workspace_download_invalid")
        def check():
            revalidate_committed_tool_authority("browser_download", arguments)
            guard()
            revalidate_committed_tool_authority("browser_download", arguments)
        check()
        with self._parent(parts[:-1]) as parent:
            def absent():
                try:
                    os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    return
                raise WorkspaceError("workspace_download_destination_exists")
            absent()
            temporary = ".jarvis-tmp-" + uuid4().hex
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=parent)
            published, stage_exists = False, True
            try:
                view = memoryview(payload)
                while view:
                    count = os.write(fd, view)
                    if count <= 0:
                        raise OSError("short download write")
                    view = view[count:]
                os.fsync(fd)
                original = os.fstat(fd)
                self._same_parent(parts[:-1], parent)
                absent()
                check()
                os.link(temporary, parts[-1], src_dir_fd=parent, dst_dir_fd=parent,
                        follow_symlinks=False)
                published = True
                os.unlink(temporary, dir_fd=parent)
                stage_exists = False
                os.fsync(parent)
                self._same_parent(parts[:-1], parent)
                actual, current = self._read(parent, parts[-1], limit=65536)
                if (actual != payload or (current.st_dev, current.st_ino)
                        != (original.st_dev, original.st_ino)
                        or stat.S_IMODE(current.st_mode) != 0o600):
                    raise WorkspaceError("workspace_download_verification_failed")
                return {"path": path, "bytes": len(actual),
                        "sha256": hashlib.sha256(actual).hexdigest()}
            except Exception as error:
                if published:
                    raise _Uncertain("workspace_download_uncertain") from error
                raise
            finally:
                os.close(fd)
                if stage_exists:
                    try:
                        os.unlink(temporary, dir_fd=parent)
                    except FileNotFoundError:
                        pass
                    except OSError as error:
                        raise _Uncertain("workspace_download_cleanup_uncertain") from error

    def _validate(self, name: str, arguments: Mapping[str, Any]) -> None:
        if name == "workspace_patch":
            from .workspace_patch import validate_patch
            validate_patch(arguments)
            return
        if not isinstance(arguments, Mapping):
            raise WorkspaceError("invalid_workspace_arguments")
        allowed = {
            "workspace_list": {"path", "offset"}, "workspace_read": {"path"},
            "workspace_write": {"path", "text", "expected_sha256"},
            "workspace_edit": {"path", "old_text", "new_text", "expected_sha256"},
            "workspace_mkdir": {"path"},
        }[name]
        required = {
            "workspace_list": set(), "workspace_read": {"path"},
            "workspace_write": {"path", "text"},
            "workspace_edit": {"path", "old_text", "new_text", "expected_sha256"},
            "workspace_mkdir": {"path"},
        }[name]
        if set(arguments) - allowed or required - set(arguments):
            raise WorkspaceError("invalid_workspace_arguments")
        _parts(arguments.get("path", ""), root_allowed=name == "workspace_list")
        if name == "workspace_list":
            offset = arguments.get("offset", 0)
            if type(offset) is not int or not 0 <= offset <= 2048:
                raise WorkspaceError("invalid_workspace_arguments")
        for key in {"text", "old_text", "new_text"} & arguments.keys():
            value = arguments[key]
            if not isinstance(value, str) or len(value.encode()) > self.max_file_bytes:
                raise WorkspaceError("workspace_file_limit")
        if "old_text" in arguments and not arguments["old_text"]:
            raise WorkspaceError("invalid_workspace_arguments")
        expected = arguments.get("expected_sha256")
        if expected is not None and (
            not isinstance(expected, str) or re.fullmatch(r"[a-f0-9]{64}", expected) is None
        ):
            raise WorkspaceError("invalid_workspace_arguments")
        if name == "workspace_edit" and expected is None:
            raise WorkspaceError("invalid_workspace_arguments")

    def _list(self, arguments: Mapping[str, Any]) -> str:
        parts = _parts(arguments.get("path", ""), root_allowed=True)
        with self._parent(parts) as parent:
            entries = []
            with os.scandir(parent) as directory:
                for index, entry in enumerate(directory):
                    if index >= 2048:
                        raise WorkspaceError("workspace_directory_limit")
                    try:
                        _parts(entry.name)
                    except WorkspaceError:
                        continue
                    info = entry.stat(follow_symlinks=False)
                    if info.st_uid != os.getuid() or stat.S_ISLNK(info.st_mode):
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        entries.append({"name": entry.name, "type": "directory"})
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        entries.append({"name": entry.name, "type": "file", "bytes": info.st_size})
            entries.sort(key=lambda item: item["name"])
            offset = arguments.get("offset", 0)
            page = entries[offset:offset + 128]
            while page:
                try:
                    return _json({"entries": page, "next_offset": offset + len(page),
                                  "has_more": offset + len(page) < len(entries)})
                except WorkspaceError:
                    page.pop()
            return _json({"entries": [], "next_offset": offset, "has_more": False})

    def _write(self, name: str, arguments: Mapping[str, Any], cancellation: asyncio.Event) -> str:
        parts = _parts(arguments["path"])
        with self._parent(parts[:-1]) as parent:
            expected = arguments.get("expected_sha256")
            self._target(parent, parts[-1], expected)
            if name == "workspace_edit":
                data, _ = self._read(parent, parts[-1])
                text = data.decode("utf-8")
                if text.count(arguments["old_text"]) != 1:
                    raise WorkspaceError("workspace_edit_not_unique")
                text = text.replace(arguments["old_text"], arguments["new_text"], 1)
            else:
                text = arguments["text"]
            output = text.encode()
            if len(output) > self.max_file_bytes:
                raise WorkspaceError("workspace_file_limit")
            check_tool_authority(name, arguments)
            if cancellation.is_set():
                raise WorkspaceError("workspace_cancelled")
            temporary = ".jarvis-tmp-" + uuid4().hex
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=parent)
            committed = False
            try:
                view = memoryview(output)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("short write")
                    view = view[written:]
                os.fsync(descriptor)
                identity = os.fstat(descriptor)
                self._same_parent(parts[:-1], parent)
                self._target(parent, parts[-1], expected)
                if cancellation.is_set():
                    raise WorkspaceError("workspace_cancelled")
                check_tool_authority(name, arguments, commit=True)
                os.replace(temporary, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
                committed = True
                os.fsync(parent)
                actual, info = self._read(parent, parts[-1])
                if (actual != output or (identity.st_dev, identity.st_ino)
                        != (info.st_dev, info.st_ino)):
                    raise WorkspaceError("workspace_verification_failed")
                return _json({"path": arguments["path"], "bytes": len(output),
                              "sha256": hashlib.sha256(actual).hexdigest()})
            except Exception as error:
                if committed:
                    raise _Uncertain("workspace_write_uncertain") from error
                raise
            finally:
                os.close(descriptor)
                if not committed:
                    try:
                        os.unlink(temporary, dir_fd=parent)
                    except FileNotFoundError:
                        pass
                    except OSError as error:
                        raise _Uncertain("workspace_cleanup_uncertain") from error

    async def _execute(self, name: str, arguments: Mapping[str, Any],
                       cancellation: asyncio.Event) -> ToolResult:
        if name == "workspace_patch":
            from .workspace_patch import execute_patch
            return execute_patch(self, arguments, cancellation)
        try:
            self._validate(name, arguments)
            if cancellation.is_set():
                raise WorkspaceError("workspace_cancelled")
            if name in {"workspace_write", "workspace_edit", "workspace_mkdir"}:
                check_tool_authority(name, arguments)
            if name == "workspace_list":
                output = self._list(arguments)
            elif name == "workspace_read":
                parts = _parts(arguments["path"])
                with self._parent(parts[:-1]) as parent:
                    data, _ = self._read(parent, parts[-1])
                    output = _json({"path": arguments["path"], "text": data.decode("utf-8"),
                                    "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
            elif name == "workspace_mkdir":
                parts = _parts(arguments["path"])
                check_tool_authority(name, arguments)
                with self._parent(parts[:-1]) as parent:
                    self._same_parent(parts[:-1], parent)
                    check_tool_authority(name, arguments, commit=True)
                    os.mkdir(parts[-1], mode=0o700, dir_fd=parent)
                    try:
                        os.fsync(parent)
                        info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
                        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                                or stat.S_IMODE(info.st_mode) != 0o700):
                            raise _Uncertain("workspace_mkdir_uncertain")
                    except OSError as error:
                        raise _Uncertain("workspace_mkdir_uncertain") from error
                output = _json({"path": arguments["path"], "created": True})
            else:
                output = self._write(name, arguments, cancellation)
            return ToolResult(name, True, True, output=output)
        except _Uncertain as error:
            return ToolResult(name, False, False, error=str(error), effect_status="unknown")
        except (WorkspaceError, ToolError) as error:
            return ToolResult(name, False, False, error=str(error))
        except (OSError, UnicodeError):
            return ToolResult(name, False, False, error="workspace_unavailable")

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return self._definitions

    def specs(self) -> tuple[ToolSpec, ...]:
        path = {"type": "string", "maxLength": 2048}
        text = {"type": "string", "maxLength": self.max_file_bytes}
        digest = {"type": "string", "minLength": 64, "maxLength": 64}
        values = [
            ("workspace_list", "Lista archivos permitidos dentro del espacio de trabajo elegido.",
             {"path": path, "offset": {"type": "integer", "minimum": 0, "maximum": 2048}}, []),
            ("workspace_read", "Lee texto UTF-8 y su hash dentro del espacio de trabajo elegido.",
             {"path": path}, ["path"]),
            ("workspace_write", "Crea texto; para sobrescribir exige el hash actual y aprobación.",
             {"path": path, "text": text, "expected_sha256": digest}, ["path", "text"]),
            ("workspace_edit", "Reemplaza una coincidencia única tras comprobar hash y aprobación.",
             {"path": path, "old_text": text, "new_text": text, "expected_sha256": digest},
             ["path", "old_text", "new_text", "expected_sha256"]),
            ("workspace_mkdir", "Crea un directorio explícito tras la aprobación del dueño.",
             {"path": path}, ["path"]),
            ("workspace_patch", "Aplica diff unificado UTF-8 exacto sobre hasta 16 archivos. "
             "---/+++ y @@ con conteos; admite crear, editar, borrar o mover. "
             "expected_sha256 debe incluir cada ruta: hash de origen y null para destino nuevo. "
             "Publicación atómica por archivo; una operación parcial es incierta y no se repite.",
             {"patch": {"type": "string", "maxLength": 96000},
              "expected_sha256": {"type": "object", "maxProperties": 32,
                                  "additionalProperties": {"type": ["string", "null"]}}},
             ["patch", "expected_sha256"]),
        ]
        return tuple(ToolSpec(name, description, {"type": "object", "properties": properties,
                                                  "required": required,
                                                  "additionalProperties": False})
                     for name, description, properties, required in values)


class _Uncertain(WorkspaceError):
    pass
