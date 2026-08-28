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


class InvalidPluginSecretError(RuntimeError):
    """Raised when a plugin credential is malformed."""


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
