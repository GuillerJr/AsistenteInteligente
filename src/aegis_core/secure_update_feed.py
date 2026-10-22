"""Fixed, signed release channel used by the app; never accepts a client-supplied URL."""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx

from aegis_core.secure_update import (
    MAX_MANIFEST_BYTES,
    MAX_RELEASE_MANIFEST_BYTES,
    UPDATE_MANIFEST_NAME,
    SecureUpdateError,
    SignedUpdateManifest,
    UpdateRollbackError,
    _require_private_directory,
    default_secure_update_installer,
    default_update_paths_from_frozen_executable,
    read_installed_identity,
    verify_signed_update,
    verify_update_manifest,
)

STABLE_FEED = "https://github.com/GuillerJr/AsistenteInteligente/releases/latest/download/"
RELEASE_MANIFEST_NAME = "Jarvis.release.json"
ALLOWED_HOSTS = frozenset(
    {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
)


class FeedUnpublished(SecureUpdateError):
    """The owner has not published a stable channel yet."""


def _allowed_url(url: httpx.URL) -> bool:
    return (
        url.scheme == "https"
        and url.host in ALLOWED_HOSTS
        and url.port in (None, 443)
        and not url.userinfo
    )


async def download(
    client: httpx.AsyncClient, name: str, destination: Path, *, maximum_bytes: int
) -> None:
    if name not in {UPDATE_MANIFEST_NAME, RELEASE_MANIFEST_NAME, "Jarvis.zip"}:
        raise SecureUpdateError("unexpected channel artifact")
    url = httpx.URL(STABLE_FEED + name)
    for _ in range(6):
        if not _allowed_url(url):
            raise SecureUpdateError("update redirect left the trusted HTTPS channel")
        async with client.stream("GET", url, headers={"Accept-Encoding": "identity"}) as response:
            if response.status_code == 404:
                raise FeedUnpublished("stable update has not been published")
            if response.is_redirect:
                location = response.headers.get("location", "")
                if not location:
                    raise SecureUpdateError("update redirect has no destination")
                url = response.url.join(location)
                continue
            response.raise_for_status()
            if response.status_code != 200:
                raise SecureUpdateError("update server did not return a complete artifact")
            length = response.headers.get("content-length")
            if length is not None and (not length.isdigit() or int(length) > maximum_bytes):
                raise SecureUpdateError("update download exceeds its byte budget")
            descriptor, temporary = tempfile.mkstemp(prefix=".download-", dir=destination.parent)
            try:
                size = 0
                with os.fdopen(descriptor, "wb") as output:
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        size += len(chunk)
                        if size > maximum_bytes:
                            raise SecureUpdateError("update download exceeds its byte budget")
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if size == 0:
                    raise SecureUpdateError("empty update artifact")
                os.replace(temporary, destination)
                return
            finally:
                Path(temporary).unlink(missing_ok=True)
    raise SecureUpdateError("too many update redirects")


def _release(envelope: SignedUpdateManifest) -> dict[str, Any]:
    return {
        "version": envelope.payload.version,
        "build": envelope.payload.build,
        "revision": envelope.payload.build_revision,
    }


async def check_or_prepare(
    *, app: Path, public_key: Path, state: Path, client: httpx.AsyncClient,
    confirmation_revision: str | None = None,
) -> dict[str, Any]:
    installed = read_installed_identity(app)
    updates = state / "Updates"
    _require_private_directory(updates)
    with tempfile.TemporaryDirectory(prefix="check-", dir=updates) as temporary:
        manifest = Path(temporary) / UPDATE_MANIFEST_NAME
        try:
            await download(client, UPDATE_MANIFEST_NAME, manifest, maximum_bytes=MAX_MANIFEST_BYTES)
        except FeedUnpublished:
            return {"status": "unpublished"}
        try:
            envelope = verify_update_manifest(manifest, public_key, current_build=installed.build)
        except UpdateRollbackError:
            # The signature and lifetime were validated before this comparison.
            if confirmation_revision is None:
                return {"status": "current"}
            raise
        if confirmation_revision is None:
            return {"status": "available", "release": _release(envelope)}
        if confirmation_revision != envelope.payload.build_revision:
            raise SecureUpdateError("channel changed after the owner's confirmation")
        target = updates / confirmation_revision
        _require_private_directory(target)
        final_manifest = target / UPDATE_MANIFEST_NAME
        os.replace(manifest, final_manifest)
        release = target / RELEASE_MANIFEST_NAME
        archive = target / "Jarvis.zip"
        try:
            await download(client, RELEASE_MANIFEST_NAME, release,
                           maximum_bytes=MAX_RELEASE_MANIFEST_BYTES)
            await download(client, "Jarvis.zip", archive,
                           maximum_bytes=envelope.payload.artifact.size)
            verified = await asyncio.to_thread(
                verify_signed_update, final_manifest, archive, release, public_key,
                current_build=installed.build,
            )
        except BaseException:
            for path in (final_manifest, release, archive):
                path.unlink(missing_ok=True)
            raise
        return {"status": "prepared", "release": _release(verified.manifest)}


async def run(arguments: Sequence[str]) -> int:
    try:
        command = arguments[0]
        confirmation: str | None = None
        if command in {"secure-update-prepare", "secure-update-apply"}:
            if (len(arguments) != 3 or arguments[1] != "--confirm-install"
                    or re.fullmatch(r"[0-9a-f]{40}", arguments[2]) is None):
                raise SecureUpdateError("explicit revision confirmation is required")
            confirmation = arguments[2]
        elif command != "secure-update-check" or len(arguments) != 1:
            raise SecureUpdateError("unsupported channel command")
        app, public_key, state = default_update_paths_from_frozen_executable()
        if command == "secure-update-apply":
            assert confirmation is not None
            target = state / "Updates" / confirmation
            verified = await default_secure_update_installer().install(
                target / UPDATE_MANIFEST_NAME, target / "Jarvis.zip",
                target / RELEASE_MANIFEST_NAME, public_key, confirmation_revision=confirmation,
            )
            result = {"status": "installed", "release": _release(verified.manifest)}
            for name in (UPDATE_MANIFEST_NAME, "Jarvis.zip", RELEASE_MANIFEST_NAME):
                (target / name).unlink(missing_ok=True)
        else:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30, connect=10), follow_redirects=False, trust_env=False
            ) as client:
                async with asyncio.timeout(600 if confirmation else 35):
                    result = await check_or_prepare(
                        app=app, public_key=public_key, state=state, client=client,
                        confirmation_revision=confirmation,
                    )
        print(json.dumps(result, separators=(",", ":")), flush=True)
        return 0
    except (OSError, ValueError, SecureUpdateError, httpx.HTTPError, TimeoutError):
        print('{"status":"error"}', flush=True)
        return 1
