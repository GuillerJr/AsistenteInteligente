from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aegis_core.plugins.contracts import SHA256_PATTERN, PluginPackage

MAX_INSTALLED_PLUGINS = 32
MAX_PLUGIN_PACKAGE_BYTES = 1_048_576
_PLUGIN_SEAL_KEY_DOMAIN = b"jarvis-plugin-store-seal-v1"


class PluginError(ValueError):
    """Raised when a plugin package or installed record violates policy."""


class InstalledPlugin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    package: PluginPackage
    installed_at: datetime
    enabled: bool = True
    local_seal_sha256: str = Field(pattern=SHA256_PATTERN)


def _read_regular_file(path: Path, maximum_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PluginError("plugin source is unavailable") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= maximum_bytes:
            raise PluginError("plugin source size or type is invalid")
        data = os.read(descriptor, maximum_bytes + 1)
    finally:
        os.close(descriptor)
    if not data or len(data) > maximum_bytes:
        raise PluginError("plugin source size is invalid")
    return data


def load_plugin_package(path: Path) -> PluginPackage:
    if path.is_symlink() or not path.is_file():
        raise PluginError("plugin source must be a regular file")
    try:
        return PluginPackage.model_validate_json(_read_regular_file(path, MAX_PLUGIN_PACKAGE_BYTES))
    except (UnicodeError, ValidationError, ValueError) as error:
        raise PluginError("plugin package is invalid") from error


def read_plugin_source(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise PluginError("plugin source must be a regular file")
    return _read_regular_file(path, MAX_PLUGIN_PACKAGE_BYTES)


class PluginStore:
    def __init__(self, directory: Path, seal_key_loader: Callable[[], bytes | str]) -> None:
        self.directory = directory
        self._seal_key_loader = seal_key_loader
        self._lock = threading.RLock()

    def install(self, package: PluginPackage) -> InstalledPlugin:
        with self._lock:
            self._ensure_private_directory()
            existing = self.load_all()
            existing_ids = {item.package.manifest.plugin_id for item in existing}
            previous = next(
                (
                    item
                    for item in existing
                    if item.package.manifest.plugin_id == package.manifest.plugin_id
                ),
                None,
            )
            if previous is not None and self._version(package) < self._version(previous.package):
                raise PluginError("plugin downgrade is not allowed")
            if (
                package.manifest.plugin_id not in existing_ids
                and len(existing) >= MAX_INSTALLED_PLUGINS
            ):
                raise PluginError("installed plugin capacity reached")
            installed = self._sealed_record(package, enabled=True)
            self._write(installed)
            return installed

    def set_enabled(self, plugin_id: str, enabled: bool) -> InstalledPlugin:
        self._validate_id(plugin_id)
        with self._lock:
            current = self.get(plugin_id)
            if current is None:
                raise PluginError("plugin is not installed")
            updated = self._sealed_record(
                current.package,
                enabled=enabled,
                installed_at=current.installed_at,
            )
            self._write(updated)
            return updated

    def uninstall(self, plugin_id: str) -> bool:
        self._validate_id(plugin_id)
        with self._lock:
            if not self.directory.exists():
                return False
            if self.directory.is_symlink() or not self.directory.is_dir():
                raise PluginError("plugin directory is unsafe")
            target = self.directory / f"{plugin_id}.json"
            if target.is_symlink():
                raise PluginError("plugin record is unsafe")
            try:
                target.unlink()
            except FileNotFoundError:
                return False
            self._sync_directory()
            return True

    def get(self, plugin_id: str) -> InstalledPlugin | None:
        self._validate_id(plugin_id)
        for installed in self.load_all():
            if installed.package.manifest.plugin_id == plugin_id:
                return installed
        return None

    def load_all(self) -> tuple[InstalledPlugin, ...]:
        if self.directory.is_symlink():
            return ()
        try:
            entries = sorted(self.directory.glob("*.json"))
        except OSError:
            return ()
        records: list[InstalledPlugin] = []
        for path in entries[: MAX_INSTALLED_PLUGINS + 1]:
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                record = InstalledPlugin.model_validate_json(
                    _read_regular_file(path, MAX_PLUGIN_PACKAGE_BYTES)
                )
                if path.stem != record.package.manifest.plugin_id:
                    continue
                if not self._seal_matches(record):
                    continue
                records.append(record)
            except (OSError, PluginError, UnicodeError, ValidationError, ValueError):
                continue
        return tuple(records)

    def enabled_packages(self) -> tuple[PluginPackage, ...]:
        return tuple(item.package for item in self.load_all() if item.enabled)

    def verify(self) -> dict[str, bool]:
        if self.directory.is_symlink():
            return {}
        try:
            entries = sorted(self.directory.glob("*.json"))
        except OSError:
            return {}
        status: dict[str, bool] = {}
        for path in entries[: MAX_INSTALLED_PLUGINS + 1]:
            plugin_id = path.stem
            if re.fullmatch(r"^[a-z][a-z0-9-]{2,31}$", plugin_id) is None:
                continue
            try:
                record = InstalledPlugin.model_validate_json(
                    _read_regular_file(path, MAX_PLUGIN_PACKAGE_BYTES)
                )
                status[plugin_id] = (
                    record.package.manifest.plugin_id == plugin_id and self._seal_matches(record)
                )
            except (OSError, PluginError, UnicodeError, ValidationError, ValueError):
                status[plugin_id] = False
        return status

    def signature(self) -> tuple[int, int]:
        if self.directory.is_symlink():
            return (0, 0)
        try:
            metadata = self.directory.stat()
            count = sum(1 for _ in self.directory.glob("*.json"))
        except OSError:
            return (0, 0)
        return (metadata.st_mtime_ns, min(count, MAX_INSTALLED_PLUGINS + 1))

    def _sealed_record(
        self,
        package: PluginPackage,
        *,
        enabled: bool,
        installed_at: datetime | None = None,
    ) -> InstalledPlugin:
        timestamp = installed_at or datetime.now(UTC)
        payload = self._seal_payload(package, timestamp, enabled)
        seal = hmac.new(self._seal_key(), payload, hashlib.sha256).hexdigest()
        return InstalledPlugin(
            package=package,
            installed_at=timestamp,
            enabled=enabled,
            local_seal_sha256=seal,
        )

    def _seal_matches(self, record: InstalledPlugin) -> bool:
        expected = hmac.new(
            self._seal_key(),
            self._seal_payload(record.package, record.installed_at, record.enabled),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(record.local_seal_sha256, expected)

    @staticmethod
    def _seal_payload(package: PluginPackage, installed_at: datetime, enabled: bool) -> bytes:
        return json.dumps(
            {
                "enabled": enabled,
                "installed_at": installed_at.isoformat(),
                "plugin_id": package.manifest.plugin_id,
                "version": package.manifest.version,
                "checksum_sha256": package.checksum_sha256,
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    def _seal_key(self) -> bytes:
        value = self._seal_key_loader()
        if isinstance(value, str):
            try:
                value = bytes.fromhex(value)
            except ValueError as error:
                raise PluginError("plugin seal key is invalid") from error
        if not isinstance(value, bytes) or len(value) < 32:
            raise PluginError("plugin seal key is invalid")
        return hmac.new(value, _PLUGIN_SEAL_KEY_DOMAIN, hashlib.sha256).digest()

    def _write(self, record: InstalledPlugin) -> None:
        plugin_id = record.package.manifest.plugin_id
        target = self.directory / f"{plugin_id}.json"
        temporary = self.directory / f".{plugin_id}.{uuid4().hex}.tmp"
        payload = record.model_dump_json(indent=2).encode("utf-8") + b"\n"
        if len(payload) > MAX_PLUGIN_PACKAGE_BYTES:
            raise PluginError("installed plugin exceeds size limit")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            os.chmod(target, 0o600)
            self._sync_directory()
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _ensure_private_directory(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink() or not self.directory.is_dir():
            raise PluginError("plugin directory is unsafe")
        os.chmod(self.directory, 0o700)

    def _sync_directory(self) -> None:
        descriptor = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _validate_id(plugin_id: str) -> None:
        if re.fullmatch(r"^[a-z][a-z0-9-]{2,31}$", plugin_id) is None:
            raise PluginError("plugin identifier is invalid")

    @staticmethod
    def _version(package: PluginPackage) -> tuple[int, int, int]:
        major, minor, patch = package.manifest.version.split(".")
        return (int(major), int(minor), int(patch))
