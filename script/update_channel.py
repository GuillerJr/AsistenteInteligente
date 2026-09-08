#!/usr/bin/env python3
"""Manage Jarvis's Ed25519 release channel without exposing the private key."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from aegis_core.secure_update import (  # noqa: E402
    SecureUpdateError,
    create_signed_update_manifest,
    initialize_update_signing_key,
    public_key_id,
    read_public_key,
    verify_signed_update,
)

DEFAULT_PUBLIC_KEY = PROJECT_ROOT / "packaging/JarvisUpdatePublicKey.ed25519"
DEFAULT_ARCHIVE = PROJECT_ROOT / "dist/Jarvis.zip"
DEFAULT_RELEASE_MANIFEST = PROJECT_ROOT / "dist/Jarvis.release.json"
DEFAULT_UPDATE_MANIFEST = PROJECT_ROOT / "dist/Jarvis.update.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "status", "sign", "verify"))
    parser.add_argument("--public-key", type=Path, default=DEFAULT_PUBLIC_KEY)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--release-manifest", type=Path, default=DEFAULT_RELEASE_MANIFEST)
    parser.add_argument("--update-manifest", type=Path, default=DEFAULT_UPDATE_MANIFEST)
    parser.add_argument("--current-build", type=int, default=0)
    return parser


def _require_distribution_path(path: Path, expected_name: str) -> Path:
    candidate = path if path.is_absolute() else PROJECT_ROOT / path
    if candidate.name != expected_name or candidate.parent.resolve() != (
        PROJECT_ROOT / "dist"
    ).resolve():
        raise SecureUpdateError("update evidence path is outside dist")
    return candidate


def _require_public_key_path(path: Path) -> Path:
    candidate = path if path.is_absolute() else PROJECT_ROOT / path
    expected = (PROJECT_ROOT / "packaging/JarvisUpdatePublicKey.ed25519").resolve()
    if candidate.resolve() != expected:
        raise SecureUpdateError("update public key path is not the repository pin")
    return candidate


def main(arguments: list[str] | None = None) -> int:
    options = _parser().parse_args(arguments)
    try:
        public_key = _require_public_key_path(options.public_key)
        if options.action == "init":
            key_id = initialize_update_signing_key(public_key)
            print(f"status=ok action=initialized key_id={key_id}")
            return 0
        if options.action == "status":
            key_id = public_key_id(read_public_key(public_key))
            print(f"status=ok action=status key_id={key_id}")
            return 0
        archive = _require_distribution_path(options.archive, "Jarvis.zip")
        release_manifest = _require_distribution_path(
            options.release_manifest, "Jarvis.release.json"
        )
        update_manifest = _require_distribution_path(
            options.update_manifest, "Jarvis.update.json"
        )
        if options.action == "sign":
            envelope = create_signed_update_manifest(
                release_manifest,
                archive,
                update_manifest,
                public_key,
            )
            print(
                "status=ok action=signed "
                f"build={envelope.payload.build} revision={envelope.payload.build_revision} "
                f"key_id={envelope.key_id}"
            )
            return 0
        verified = verify_signed_update(
            update_manifest,
            archive,
            release_manifest,
            public_key,
            current_build=options.current_build,
        )
        print(
            "status=ok action=verified "
            f"build={verified.manifest.payload.build} "
            f"revision={verified.manifest.payload.build_revision}"
        )
        return 0
    except (OSError, SecureUpdateError, ValueError):
        print("status=error reason=secure_update_channel_validation_failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
