from __future__ import annotations

import os
import platform
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


@dataclass(frozen=True, slots=True)
class MacOSKeychain:
    service: str
    account: str

    def get(self) -> str:
        environment_value = os.getenv("NVIDIA_API_KEY")
        if environment_value:
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
        return secret

    def set(self, secret: str) -> None:
        if (
            not secret.startswith("nvapi-")
            or len(secret) < 32
            or any(char.isspace() for char in secret)
        ):
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
