from __future__ import annotations

import os
import platform
import re
import secrets as pysecrets
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path


class SecretNotFoundError(RuntimeError):
    """Raised when a required secret is unavailable."""


class InvalidSecretError(RuntimeError):
    """Raised when a value does not look like a NVIDIA API key."""


class InvalidIpcSecretError(RuntimeError):
    """Raised when a local IPC authentication secret is malformed."""


class InvalidMemorySecretError(RuntimeError):
    """Raised when the memory AEAD root secret is malformed."""


class InvalidAuditAnchorError(RuntimeError):
    """Raised when the durable audit anchor is not a canonical SHA-256 digest."""


class InvalidPluginSecretError(RuntimeError):
    """Raised when a plugin credential is malformed."""


class InvalidGenericSecretError(RuntimeError):
    """Raised when a scoped local-device credential is malformed."""


def contains_likely_secret_material(content: str) -> bool:
    normalized = content.casefold()
    markers = (
        "-----begin private key-----",
        "-----begin rsa private key-----",
        "-----begin openssh private key-----",
        "nvapi-",
        "sk-proj-",
    )
    return any(marker in normalized for marker in markers)


@dataclass(frozen=True, slots=True)
class MacOSKeychain:
    service: str
    account: str

    def get(self) -> str:
        environment_value = os.getenv("NVIDIA_API_KEY")
        if environment_value:
            if not self._is_valid_nvidia_key(environment_value):
                raise InvalidSecretError("Value does not contain a valid NVIDIA API key")
            return environment_value

        if platform.system() != "Darwin":
            raise SecretNotFoundError("macOS Keychain is only available on Darwin")

        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                self.account,
                "-s",
                self.service,
                "-w",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        secret = result.stdout.strip()
        if result.returncode != 0 or not secret:
            raise SecretNotFoundError(
                f"No credential found for service={self.service!r}, account={self.account!r}"
            )
        if not self._is_valid_nvidia_key(secret):
            raise InvalidSecretError("Value does not contain a valid NVIDIA API key")
        return secret

    def is_configured(self) -> bool:
        environment_value = os.getenv("NVIDIA_API_KEY")
        if environment_value:
            return self._is_valid_nvidia_key(environment_value)
        if platform.system() != "Darwin":
            raise SecretNotFoundError("macOS Keychain is only available on Darwin")
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                self.account,
                "-s",
                self.service,
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return result.returncode == 0

    def set(self, secret: str) -> None:
        if not self._is_valid_nvidia_key(secret):
            raise InvalidSecretError("Value does not contain a valid NVIDIA API key")
        if platform.system() != "Darwin":
            raise SecretNotFoundError("macOS Keychain is only available on Darwin")

        subprocess.run(
            [
                "/usr/bin/security",
                "add-generic-password",
                "-U",
                "-a",
                self.account,
                "-s",
                self.service,
                "-w",
                secret,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )

    @staticmethod
    def _is_valid_nvidia_key(secret: str) -> bool:
        return (
            secret.startswith("nvapi-")
            and len(secret) >= 32
            and not any(char.isspace() for char in secret)
        )


@dataclass(frozen=True, slots=True)
class MacOSIpcSecret:
    service: str
    account: str

    def get(self) -> str:
        if platform.system() != "Darwin":
            raise SecretNotFoundError("macOS Keychain is only available on Darwin")
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                self.account,
                "-s",
                self.service,
                "-w",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        secret = result.stdout.strip()
        if result.returncode != 0 or not secret:
            raise SecretNotFoundError("No local IPC credential found")
        self._validate(secret)
        return secret

    def get_or_create(self) -> str:
        try:
            return self.get()
        except SecretNotFoundError:
            candidate = pysecrets.token_hex(32)

        result = subprocess.run(
            [
                "/usr/bin/security",
                "add-generic-password",
                "-a",
                self.account,
                "-s",
                self.service,
                "-w",
                candidate,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return candidate
        return self.get()

    @staticmethod
    def _validate(secret: str) -> None:
        if len(secret) != 64 or any(char not in "0123456789abcdef" for char in secret):
            raise InvalidIpcSecretError("IPC secret must be 32-byte lowercase hex")


@dataclass(frozen=True, slots=True)
class MacOSGenericSecret:
    """A narrowly scoped Keychain secret for local device protocols."""

    service: str
    account: str = "default"

    def __post_init__(self) -> None:
        if (
            re.fullmatch(r"^ai\.aegis\.[a-z0-9][a-z0-9.-]{2,95}$", self.service) is None
            or re.fullmatch(r"^[a-z0-9][a-z0-9._-]{0,63}$", self.account) is None
        ):
            raise InvalidGenericSecretError("generic Keychain scope is invalid")

    def get(self) -> str:
        if platform.system() != "Darwin":
            raise SecretNotFoundError("macOS Keychain is only available on Darwin")
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                self.account,
                "-s",
                self.service,
                "-w",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        secret = result.stdout.strip()
        if result.returncode != 0 or not secret:
            raise SecretNotFoundError("No scoped local-device credential found")
        self._validate(secret)
        return secret

    def set(self, secret: str) -> None:
        self._validate(secret)
        if platform.system() != "Darwin":
            raise SecretNotFoundError("macOS Keychain is only available on Darwin")
        subprocess.run(
            [
                "/usr/bin/security",
                "add-generic-password",
                "-U",
                "-a",
                self.account,
                "-s",
                self.service,
                "-w",
                secret,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )

    @staticmethod
    def _validate(secret: str) -> None:
        if (
            not 1 <= len(secret.encode("utf-8")) <= 8_192
            or any(character in "\r\n\0" for character in secret)
        ):
            raise InvalidGenericSecretError("generic Keychain credential is invalid")


@dataclass(frozen=True, slots=True)
class MacOSMemorySecret:
    service: str
    account: str

    def get(self) -> bytes:
        secret = self._read()
        self._validate(secret)
        return bytes.fromhex(secret)

    def get_or_create(self) -> bytes:
        try:
            return self.get()
        except SecretNotFoundError:
            candidate = pysecrets.token_hex(32)

        result = subprocess.run(
            [
                "/usr/bin/security",
                "add-generic-password",
                "-a",
                self.account,
                "-s",
                self.service,
                "-w",
                candidate,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return bytes.fromhex(candidate)
        return self.get()

    def _read(self) -> str:
        if platform.system() != "Darwin":
            raise SecretNotFoundError("macOS Keychain is only available on Darwin")
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                self.account,
                "-s",
                self.service,
                "-w",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        secret = result.stdout.strip()
        if result.returncode != 0 or not secret:
            raise SecretNotFoundError("No memory encryption credential found")
        return secret

    @staticmethod
    def _validate(secret: str) -> None:
        if len(secret) != 64 or any(char not in "0123456789abcdef" for char in secret):
            raise InvalidMemorySecretError("memory secret must be 32-byte lowercase hex")


@dataclass(frozen=True, slots=True)
class MacOSAuditAnchor:
    service: str = "ai.aegis.audit-anchor"
    account: str = "default"

    def get(self) -> str:
        if platform.system() != "Darwin":
            raise SecretNotFoundError("macOS Keychain is only available on Darwin")
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                self.account,
                "-s",
                self.service,
                "-w",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        anchor = result.stdout.strip()
        if result.returncode != 0 or not anchor:
            raise SecretNotFoundError("No durable audit anchor found")
        self._validate(anchor)
        return anchor

    def set(self, anchor: str) -> None:
        self._validate(anchor)
        if platform.system() != "Darwin":
            raise SecretNotFoundError("macOS Keychain is only available on Darwin")
        subprocess.run(
            [
                "/usr/bin/security",
                "add-generic-password",
                "-U",
                "-a",
                self.account,
                "-s",
                self.service,
                "-w",
                anchor,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )

    @staticmethod
    def _validate(anchor: str) -> None:
        if len(anchor) != 64 or any(char not in "0123456789abcdef" for char in anchor):
            raise InvalidAuditAnchorError("audit anchor must be lowercase SHA-256 hex")


@dataclass(frozen=True, slots=True)
class MacOSPluginSecret:
    plugin_id: str
    connector_id: str

    def __post_init__(self) -> None:
        pattern = r"^[a-z][a-z0-9-]{2,31}$"
        if (
            re.fullmatch(pattern, self.plugin_id) is None
            or re.fullmatch(pattern, self.connector_id) is None
        ):
            raise InvalidPluginSecretError("plugin credential scope is invalid")

    @property
    def service(self) -> str:
        return f"ai.jarvis.plugin.{self.plugin_id}.{self.connector_id}"

    def get(self) -> str:
        if platform.system() != "Darwin":
            raise SecretNotFoundError("macOS Keychain is only available on Darwin")
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                "default",
                "-s",
                self.service,
                "-w",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        secret = result.stdout.strip()
        if result.returncode != 0 or not secret:
            raise SecretNotFoundError("No plugin credential found")
        self._validate(secret)
        return secret

    def set(self, secret: str) -> None:
        self._validate(secret)
        if platform.system() != "Darwin":
            raise SecretNotFoundError("macOS Keychain is only available on Darwin")
        subprocess.run(
            [
                "/usr/bin/security",
                "add-generic-password",
                "-U",
                "-a",
                "default",
                "-s",
                self.service,
                "-w",
                secret,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def delete(self) -> None:
        if platform.system() != "Darwin":
            raise SecretNotFoundError("macOS Keychain is only available on Darwin")
        subprocess.run(
            [
                "/usr/bin/security",
                "delete-generic-password",
                "-a",
                "default",
                "-s",
                self.service,
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )

    @staticmethod
    def _validate(secret: str) -> None:
        if not 8 <= len(secret) <= 8_192 or any(character in "\r\n\0" for character in secret):
            raise InvalidPluginSecretError("plugin credential is invalid")


def import_plugin_secret_from_file(secret_store: MacOSPluginSecret, source: Path) -> None:
    if stat.S_ISLNK(source.lstat().st_mode):
        raise InvalidPluginSecretError("Credential file cannot be a symbolic link")
    source = source.resolve(strict=True)
    file_info = source.lstat()
    mode = stat.S_IMODE(file_info.st_mode)
    if not stat.S_ISREG(file_info.st_mode) or file_info.st_uid != os.getuid() or mode & 0o077:
        raise InvalidPluginSecretError("Credential file must be owner-only")
    secret = source.read_text(encoding="utf-8").strip()
    try:
        secret_store.set(secret)
    finally:
        if source.exists():
            size = source.stat().st_size
            with source.open("r+b", buffering=0) as handle:
                handle.write(b"\0" * size)
                handle.flush()
                os.fsync(handle.fileno())
            source.unlink()


def import_generic_secret_from_file(
    secret_store: MacOSGenericSecret,
    source: Path,
) -> None:
    file_info = source.lstat()
    if (
        stat.S_ISLNK(file_info.st_mode)
        or not stat.S_ISREG(file_info.st_mode)
        or file_info.st_uid != os.getuid()
        or stat.S_IMODE(file_info.st_mode) & 0o077
        or not 1 <= file_info.st_size <= 8_192
    ):
        raise InvalidGenericSecretError("Credential file must be private and bounded")
    descriptor = os.open(
        source,
        os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (file_info.st_dev, file_info.st_ino):
            raise InvalidGenericSecretError("Credential file identity changed while opening")
        payload = bytearray()
        while len(payload) <= 8_192:
            chunk = os.read(descriptor, min(1_024, 8_193 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        if not 1 <= len(payload) <= 8_192:
            raise InvalidGenericSecretError("Credential file exceeds the safety ceiling")
        try:
            secret = payload.decode("utf-8").strip()
            secret_store.set(secret)
        except UnicodeDecodeError as error:
            raise InvalidGenericSecretError("Credential file is not UTF-8") from error
        finally:
            payload[:] = b"\0" * len(payload)
    finally:
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            remaining = file_info.st_size
            zeroes = b"\0" * min(4_096, remaining)
            while remaining:
                written = os.write(descriptor, zeroes[:remaining])
                if written <= 0:
                    break
                remaining -= written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            current = source.lstat()
        except FileNotFoundError:
            current = None
        if current is not None and (current.st_dev, current.st_ino) == (
            file_info.st_dev,
            file_info.st_ino,
        ):
            source.unlink()


def import_nvidia_key_from_clipboard(keychain: MacOSKeychain) -> None:
    clipboard = subprocess.run(
        ["/usr/bin/pbpaste"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout.strip()
    try:
        keychain.set(clipboard)
    finally:
        subprocess.run(
            ["/usr/bin/pbcopy"],
            input="",
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )


def import_nvidia_key_from_file(keychain: MacOSKeychain, source: Path) -> None:
    if stat.S_ISLNK(source.lstat().st_mode):
        raise InvalidSecretError("Credential file cannot be a symbolic link")
    source = source.resolve(strict=True)
    file_info = source.lstat()
    mode = stat.S_IMODE(file_info.st_mode)
    if not stat.S_ISREG(file_info.st_mode) or file_info.st_uid != os.getuid() or mode & 0o077:
        raise InvalidSecretError("Credential file must be an owner-only regular file")

    secret = source.read_text(encoding="utf-8").strip()
    try:
        keychain.set(secret)
    finally:
        if source.exists():
            size = source.stat().st_size
            with source.open("r+b", buffering=0) as handle:
                handle.write(b"\0" * size)
                handle.flush()
                os.fsync(handle.fileno())
            source.unlink()
