from __future__ import annotations

import json
import plistlib
from datetime import timedelta
from functools import partial
from pathlib import Path

import httpx
import pytest
from test_secure_update import NEW_REVISION, OLD_REVISION, PUBLISHED, _app_info, _signed_fixture

from aegis_core import bundle_paths
from aegis_core import secure_update_feed as feed
from aegis_core.secure_update import (
    UPDATE_PUBLIC_KEY_NAME,
    SecureUpdateError,
    UpdateSignatureError,
    verify_signed_update,
    verify_update_manifest,
)


@pytest.fixture
def channel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manifest, archive, release, public, _ = _signed_fixture(tmp_path / "release")
    app = tmp_path / "Applications/Jarvis.app"
    (app / "Contents/Resources").mkdir(parents=True)
    (app / "Contents/Info.plist").write_bytes(_app_info(build=1, revision=OLD_REVISION))
    key = app / "Contents/Resources" / UPDATE_PUBLIC_KEY_NAME
    key.write_bytes(public.read_bytes())
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    now = PUBLISHED + timedelta(hours=1)
    monkeypatch.setattr(feed, "verify_update_manifest", partial(verify_update_manifest, now=now))
    monkeypatch.setattr(feed, "verify_signed_update", partial(verify_signed_update, now=now))
    blobs = {path.name: path.read_bytes() for path in (manifest, archive, release)}
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, content=blobs[request.url.path.rsplit("/", 1)[-1]])

    return app, key, state, blobs, requests, httpx.MockTransport(respond)


@pytest.mark.asyncio
async def test_check_only_downloads_signed_manifest_and_prepare_verifies_archive(channel) -> None:
    app, key, state, _, requests, transport = channel
    async with httpx.AsyncClient(transport=transport) as client:
        checked = await feed.check_or_prepare(app=app, public_key=key, state=state, client=client)
        assert checked["status"] == "available"
        assert checked["release"]["revision"] == NEW_REVISION
        assert requests == [feed.STABLE_FEED + "Jarvis.update.json"]
        prepared = await feed.check_or_prepare(
            app=app, public_key=key, state=state, client=client,
            confirmation_revision=NEW_REVISION,
        )
    assert prepared["status"] == "prepared"
    assert (state / "Updates" / NEW_REVISION / "Jarvis.zip").stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_channel_change_after_confirmation_and_tampering_fail_closed(channel) -> None:
    app, key, state, blobs, _, transport = channel
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(SecureUpdateError, match="channel changed"):
            await feed.check_or_prepare(
                app=app, public_key=key, state=state, client=client,
                confirmation_revision="c" * 40,
            )
        manifest = json.loads(blobs["Jarvis.update.json"])
        manifest["payload"]["version"] = "9.9.9"
        blobs["Jarvis.update.json"] = json.dumps(manifest).encode()
        with pytest.raises(UpdateSignatureError):
            await feed.check_or_prepare(app=app, public_key=key, state=state, client=client)


@pytest.mark.asyncio
async def test_prepare_rejects_corrupted_archive_and_cleans_partial_downloads(channel) -> None:
    app, key, state, blobs, _, transport = channel
    blobs["Jarvis.zip"] = b"altered"
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(SecureUpdateError):
            await feed.check_or_prepare(
                app=app, public_key=key, state=state, client=client,
                confirmation_revision=NEW_REVISION,
            )
    assert list((state / "Updates" / NEW_REVISION).iterdir()) == []


@pytest.mark.asyncio
async def test_no_update_never_prepares_a_downgrade(channel) -> None:
    app, key, state, _, _, transport = channel
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    info["CFBundleVersion"] = "3"
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
    async with httpx.AsyncClient(transport=transport) as client:
        assert (await feed.check_or_prepare(
            app=app, public_key=key, state=state, client=client
        ))["status"] == "current"
        with pytest.raises(SecureUpdateError, match="not newer"):
            await feed.check_or_prepare(
                app=app, public_key=key, state=state, client=client,
                confirmation_revision=NEW_REVISION,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["http://github.com/file", "https://evil.example/file"])
async def test_downloader_rejects_redirects_outside_pinned_https_hosts(tmp_path, location) -> None:
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": location})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(SecureUpdateError, match="trusted HTTPS"):
            await feed.download(client, "Jarvis.zip", tmp_path / "archive", maximum_bytes=8)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_downloader_enforces_stream_limit_and_unpublished_state(tmp_path) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=b"0123456789")
    )) as client:
        with pytest.raises(SecureUpdateError, match="byte budget"):
            await feed.download(client, "Jarvis.zip", tmp_path / "archive", maximum_bytes=4)
    assert not (tmp_path / "archive").exists()
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(404)
    )) as client:
        with pytest.raises(feed.FeedUnpublished):
            await feed.download(client, "Jarvis.zip", tmp_path / "archive", maximum_bytes=4)


def test_frozen_bundle_paths_follow_relocated_executable(monkeypatch) -> None:
    monkeypatch.setattr(bundle_paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(bundle_paths.sys, "executable",
                        "/Applications/Jarvis.app/Contents/Resources/Daemon/jarvis-daemon")
    assert bundle_paths.installed_app_path() == Path("/Applications/Jarvis.app")


@pytest.mark.asyncio
async def test_apply_requires_explicit_confirmation_before_reading_state(capsys) -> None:
    assert await feed.run(["secure-update-apply"]) == 1
    assert capsys.readouterr().out.strip() == '{"status":"error"}'
