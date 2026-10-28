"""Private, bounded, descriptor-confined settings for one Jarvis profile.

No HOME discovery, credential loading or imported owner state. Concurrent writers
merge under a short nonblocking lock; invalid or retargeted paths fail closed.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from jarvis_core.state import ensure_private_directory


class ConfigError(ValueError):
    pass


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ConfigError("duplicate_configuration_key")
        value[key] = item
    return value


class ProfileConfig:
    def __init__(self, directory: Path) -> None:
        self.directory = ensure_private_directory(directory)
        self.path = directory / "configuration.json"
        info = self.directory.stat()
        self._identity = info.st_dev, info.st_ino
        with self._parent() as parent:
            self._data = self._load_at(parent)

    @staticmethod
    def _validate(value: dict) -> None:
        required = {"version", "workspace", "bindings"}
        if (
            not isinstance(value, dict)
            or required - set(value)
            or set(value) - required - {"workspace_identity", "mcp_servers"}
            or type(value["version"]) is not int
            or value["version"] != 1
            or not isinstance(value["bindings"], dict)
            or len(value["bindings"]) > 1000
        ):
            raise ConfigError("invalid_profile_configuration")
        workspace = value["workspace"]
        if workspace is not None and (
            not isinstance(workspace, str)
            or not Path(workspace).is_absolute()
            or ".." in Path(workspace).parts
            or "\0" in workspace
            or len(workspace.encode()) > 4096
        ):
            raise ConfigError("invalid_workspace_configuration")
        identity = value.get("workspace_identity")
        if identity is not None and (
            not isinstance(identity, list)
            or len(identity) != 2
            or any(type(item) is not int or item < 0 for item in identity)
        ):
            raise ConfigError("invalid_workspace_identity")
        for key, digest in value["bindings"].items():
            if (
                not isinstance(key, str)
                or re.fullmatch(r"[a-f0-9]{32}", key) is None
                or not isinstance(digest, str)
                or re.fullmatch(r"[a-f0-9]{64}", digest) is None
            ):
                raise ConfigError("invalid_provider_binding")
        servers = value.get("mcp_servers", {})
        if not isinstance(servers, dict) or len(servers) > 8:
            raise ConfigError("invalid_mcp_configuration")
        for server_id, server in servers.items():
            if (
                not isinstance(server_id, str)
                or re.fullmatch(r"[a-z][a-z0-9_]{0,23}", server_id) is None
                or not isinstance(server, dict)
                or not {"command", "protocol_version"} <= set(server)
                or set(server) - {"command", "protocol_version", "transport", "endpoint",
                                  "oauth", "allow_loopback"}
                or not isinstance(server["protocol_version"], str)
                or server["protocol_version"] not in {"2024-11-05", "2025-11-25", "2026-07-28"}
            ):
                raise ConfigError("invalid_mcp_configuration")
            command = server["command"]
            if not isinstance(command, list):
                raise ConfigError("invalid_mcp_configuration")
            if server.get("transport", "stdio") != "stdio":
                try:
                    from jarvis_core.mcp_registry import ServerSpec
                    ServerSpec(server_id, tuple(command), server["protocol_version"],
                               transport=server.get("transport", "stdio"),
                               endpoint=server.get("endpoint"), oauth=server.get("oauth"),
                               allow_loopback=server.get("allow_loopback", False))
                except (TypeError, ValueError):
                    raise ConfigError("invalid_mcp_configuration") from None
                continue
            if (server["protocol_version"] not in {"2025-11-25", "2026-07-28"}
                    or server.get("endpoint") is not None or server.get("oauth") is not None
                    or server.get("allow_loopback", False)):
                raise ConfigError("invalid_mcp_configuration")
            if (
                not isinstance(command, list)
                or not 1 <= len(command) <= 64
                or any(
                    not isinstance(arg, str) or not arg or "\0" in arg or len(arg.encode()) > 8192
                    for arg in command
                )
                or sum(len(arg.encode()) for arg in command) > 32768
                or not Path(command[0]).is_absolute()
            ):
                raise ConfigError("invalid_mcp_configuration")

    def _check_parent(self, parent: int) -> None:
        ensure_private_directory(self.directory)
        info, opened = self.directory.lstat(), os.fstat(parent)
        if (info.st_dev, info.st_ino) != self._identity or (
            opened.st_dev,
            opened.st_ino,
        ) != self._identity:
            raise ConfigError("profile_configuration_retargeted")

    @contextmanager
    def _parent(self) -> Iterator[int]:
        ensure_private_directory(self.directory)
        parent = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            self._check_parent(parent)
            yield parent
        finally:
            os.close(parent)

    @staticmethod
    def _private_file(descriptor: int, *, limit: int) -> None:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or info.st_size > limit
        ):
            raise ConfigError("unsafe_profile_configuration")

    def _load_at(self, parent: int) -> dict:
        self._check_parent(parent)
        try:
            descriptor = os.open(
                "configuration.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
            )
        except FileNotFoundError:
            return {"version": 1, "workspace": None, "workspace_identity": None, "bindings": {}}
        except OSError:
            raise ConfigError("unsafe_profile_configuration") from None
        try:
            self._private_file(descriptor, limit=131072)
            data = bytearray()
            while chunk := os.read(descriptor, min(8192, 131073 - len(data))):
                data.extend(chunk)
                if len(data) > 131072:
                    raise ConfigError("profile_configuration_limit")
            value = json.loads(data, object_pairs_hook=_object)
            self._validate(value)
            return {
                **value,
                "workspace_identity": value.get("workspace_identity"),
                "mcp_servers": value.get("mcp_servers", {}),
            }
        except (ValueError, UnicodeError):
            raise ConfigError("invalid_profile_configuration") from None
        finally:
            os.close(descriptor)

    def _write_temporary(self, parent: int, data: bytes) -> str:
        name = ".configuration-" + uuid4().hex
        descriptor = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
        )
        try:
            os.fchmod(descriptor, 0o600)
            remaining = memoryview(data)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OSError("configuration_write_failed")
                remaining = remaining[written:]
            os.fsync(descriptor)
        except BaseException:
            os.unlink(name, dir_fd=parent)
            raise
        finally:
            os.close(descriptor)
        return name

    def _update(self, change: Callable[[dict], dict]) -> None:
        with self._parent() as parent:
            try:
                lock = os.open(
                    "configuration.lock",
                    os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                    0o600,
                    dir_fd=parent,
                )
            except OSError:
                raise ConfigError("unsafe_profile_configuration") from None
            try:
                self._private_file(lock, limit=0)
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ConfigError("profile_configuration_busy") from None
                current = self._load_at(parent)
                value = change(current)
                self._validate(value)
                if value == current:
                    self._data = current
                    return
                data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
                if len(data) > 131072:
                    raise ConfigError("profile_configuration_limit")
                name = self._write_temporary(parent, data)
                try:
                    self._load_at(parent)
                    self._check_parent(parent)
                    os.replace(name, "configuration.json", src_dir_fd=parent, dst_dir_fd=parent)
                    try:
                        os.fsync(parent)
                    except OSError:
                        raise ConfigError("profile_configuration_durability_uncertain") from None
                finally:
                    try:
                        os.unlink(name, dir_fd=parent)
                    except FileNotFoundError:
                        pass
                self._data = value
            except (UnicodeError, OSError) as error:
                raise ConfigError("profile_configuration_unavailable") from error
            finally:
                os.close(lock)

    def _read(self) -> dict:
        with self._parent() as parent:
            self._data = self._load_at(parent)
            return self._data

    @property
    def workspace(self) -> str | None:
        return self._read()["workspace"]

    @property
    def workspace_identity(self) -> tuple[int, int] | None:
        value = self._read()["workspace_identity"]
        return tuple(value) if value is not None else None

    def set_workspace(self, path: Path, identity: tuple[int, int] | None = None) -> None:
        self._update(
            lambda current: {
                **current,
                "workspace": str(path),
                "workspace_identity": list(identity) if identity else None,
                "bindings": {},
            }
        )

    def binding_matches(self, session_id: str, fingerprint: str) -> bool:
        return self._read()["bindings"].get(session_id) == fingerprint

    def bind(self, session_id: str, fingerprint: str) -> None:
        self._update(
            lambda current: {
                **current,
                "bindings": {
                    **current["bindings"],
                    session_id: fingerprint,
                },
            }
        )

    def forget(self, session_id: str) -> None:
        self._update(
            lambda current: {
                **current,
                "bindings": {
                    key: value for key, value in current["bindings"].items() if key != session_id
                },
            }
        )

    @property
    def server_specs(self) -> tuple[dict, ...]:
        return tuple(
            {
                "server_id": key,
                "command": tuple(value["command"]),
                "protocol_version": value["protocol_version"],
                **{name: value[name] for name in
                   ("transport", "endpoint", "oauth", "allow_loopback")
                   if name in value},
            }
            for key, value in self._read().get("mcp_servers", {}).items()
        )

    def set_server(self, server_id: str, command: tuple[str, ...], protocol_version: str, *,
                   transport: str = "stdio", endpoint: str | None = None,
                   oauth: dict | None = None, allow_loopback: bool = False) -> None:
        from jarvis_core.mcp import McpError
        from jarvis_core.mcp_registry import McpRegistryError, ServerSpec
        try:
            spec = ServerSpec(server_id, command, protocol_version, transport, endpoint, oauth,
                              allow_loopback)
        except (McpRegistryError, McpError):
            # Keep the settings API's public error boundary. Validation happens
            # before _update can lock, create or replace configuration.json.
            raise ConfigError("invalid_mcp_configuration") from None
        record = spec.public()
        record.pop("server_id")
        self._update(
            lambda current: {
                **current,
                "mcp_servers": {
                    **current.get("mcp_servers", {}),
                    server_id: record,
                },
            }
        )

    def remove_server(self, server_id: str) -> None:
        self._update(
            lambda current: {
                **current,
                "mcp_servers": {
                    key: value
                    for key, value in current.get("mcp_servers", {}).items()
                    if key != server_id
                },
            }
        )
