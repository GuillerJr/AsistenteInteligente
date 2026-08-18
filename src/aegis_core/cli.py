from __future__ import annotations

import argparse
import asyncio
import platform
import subprocess
import sys
from pathlib import Path

from aegis_core.config import Settings
from aegis_core.contracts import AgentRole
from aegis_core.providers.nvidia import NvidiaNimClient, NvidiaNimError
from aegis_core.secrets import (
    InvalidSecretError,
    MacOSKeychain,
    SecretNotFoundError,
    import_nvidia_key_from_clipboard,
    import_nvidia_key_from_file,
)
from aegis_core.tools.audit import AuditIntegrityError, HashChainAuditLog


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


def import_nvidia_key() -> int:
    settings = Settings()
    keychain = MacOSKeychain(
        service=settings.nvidia_keychain_service,
        account=settings.nvidia_keychain_account,
    )
    try:
        import_nvidia_key_from_clipboard(keychain)
    except (InvalidSecretError, SecretNotFoundError, subprocess.SubprocessError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    print("nvidia_api_key=stored clipboard=cleared")
    return 0


def import_nvidia_key_file(source: Path) -> int:
    settings = Settings()
    keychain = MacOSKeychain(
        service=settings.nvidia_keychain_service,
        account=settings.nvidia_keychain_account,
    )
    try:
        import_nvidia_key_from_file(keychain, source)
    except (InvalidSecretError, SecretNotFoundError, OSError, subprocess.SubprocessError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    print("nvidia_api_key=stored temporary_file=destroyed")
    return 0


async def probe_nvidia() -> int:
    settings = Settings()
    keychain = MacOSKeychain(
        service=settings.nvidia_keychain_service,
        account=settings.nvidia_keychain_account,
    )
    try:
        async with NvidiaNimClient(settings, keychain.get) as client:
            result = await client.complete(
                role=AgentRole.ROUTER,
                messages=[
                    {
                        "role": "user",
                        "content": "Reply with exactly: OK",
                    }
                ],
                max_tokens=8,
                temperature=0.0,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
    except (SecretNotFoundError, NvidiaNimError, OSError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    print(f"status=ok model={result.model_id} credential=keychain")
    return 0


def verify_audit(path: Path) -> int:
    try:
        records = HashChainAuditLog(path).verify()
    except (AuditIntegrityError, OSError):
        print("status=error reason=audit_integrity_failure")
        return 1
    head_hash = records[-1].record_hash if records else "empty"
    print(f"status=ok records={len(records)} head_hash={head_hash}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="aegis")
    parser.add_argument(
        "command",
        choices=[
            "doctor",
            "import-nvidia-key",
            "import-nvidia-key-file",
            "probe-nvidia",
            "verify-audit",
        ],
    )
    parser.add_argument("resource_path", nargs="?", type=Path)
    args = parser.parse_args()
    if args.command == "doctor":
        raise SystemExit(doctor())
    if args.command == "import-nvidia-key":
        raise SystemExit(import_nvidia_key())
    if args.command == "import-nvidia-key-file":
        if args.resource_path is None:
            parser.error("import-nvidia-key-file requires credential_path")
        raise SystemExit(import_nvidia_key_file(args.resource_path))
    if args.command == "probe-nvidia":
        raise SystemExit(asyncio.run(probe_nvidia()))
    if args.command == "verify-audit":
        if args.resource_path is None:
            parser.error("verify-audit requires audit_path")
        raise SystemExit(verify_audit(args.resource_path))
    raise SystemExit(1)


if __name__ == "__main__":
    sys.exit(main())
