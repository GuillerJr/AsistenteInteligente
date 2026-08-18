from __future__ import annotations

import os
import platform
import subprocess
from dataclasses import dataclass


class SecretNotFoundError(RuntimeError):
    """Raised when a required secret is unavailable."""


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
