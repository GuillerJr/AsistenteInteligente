from __future__ import annotations

import argparse
import platform
import sys

from aegis_core.config import Settings
from aegis_core.secrets import MacOSKeychain, SecretNotFoundError


def doctor() -> int:
    settings = Settings()
    architecture = platform.machine()
    print(f"architecture={architecture}")
    print(f"python={platform.python_version()}")
    print(f"nvidia_base_url={settings.nvidia_base_url}")

    if architecture != "arm64":
        print("status=error reason=non_arm64_runtime")
        return 1

    keychain = MacOSKeychain(
        service=settings.nvidia_keychain_service,
        account=settings.nvidia_keychain_account,
    )
    try:
        keychain.get()
    except SecretNotFoundError:
        print("nvidia_api_key=missing")
        return 2
    print("nvidia_api_key=available")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="aegis")
    parser.add_argument("command", choices=["doctor"])
    args = parser.parse_args()
    if args.command == "doctor":
        raise SystemExit(doctor())
    raise SystemExit(1)


if __name__ == "__main__":
    sys.exit(main())
