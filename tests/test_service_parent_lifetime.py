"""Admission and closure of the actual service seam, without account state."""

import argparse
import asyncio
from pathlib import Path

import pytest

import jarvis_core.service as module
from jarvis_core.bundled_runtime import BundledRuntimeError


def arguments(tmp_path, **overrides):
    return argparse.Namespace(state=tmp_path / "absent", codex=Path("/synthetic/codex"),
                              provider_state=None, **overrides)


async def test_parent_already_gone_never_acquires_profile(tmp_path, monkeypatch):
    observed = []

    class Gone:
        ended = True

        def close(self):
            observed.append("closed")

        def attach(self, *_):
            pytest.fail("an ended initial pipe must not start a service")

    monkeypatch.setattr(module, "ParentLifetime", Gone)
    await module._serve(arguments(tmp_path, parent_watch_stdin=True))
    assert observed == ["closed"]
    assert not (tmp_path / "absent").exists()


async def test_parent_exits_during_admission_before_stores(tmp_path, monkeypatch):
    observed = []

    class EndsAtAttach:
        ended = False

        def check(self):
            return True

        def attach(self, loop, callback):
            loop.call_soon(callback)

        def close(self):
            observed.append("closed")

    monkeypatch.setattr(module, "ParentLifetime", EndsAtAttach)
    await module._serve(arguments(tmp_path, parent_watch_stdin=True))
    assert observed == ["closed"]
    assert not (tmp_path / "absent").exists()


async def test_fresh_parent_checkpoint_rejects_eof_not_yet_dispatched(tmp_path, monkeypatch):
    observed = []

    class PendingEOF:
        ended = False

        def attach(self, *_):
            pass  # reader callback has not run

        def check(self):
            observed.append("fresh_check")
            return False

        def close(self):
            observed.append("closed")

    monkeypatch.setattr(module, "ParentLifetime", PendingEOF)
    await module._serve(arguments(tmp_path, parent_watch_stdin=True))
    assert observed == ["fresh_check", "closed"]
    assert not (tmp_path / "absent").exists()


async def test_live_parent_eof_closes_owned_service_once(tmp_path, monkeypatch):
    observed = []
    callback = None

    class Live:
        ended = False

        def check(self):
            return True

        def attach(self, _loop, notify):
            nonlocal callback
            callback = notify

        def close(self):
            observed.append("watch_closed")

    class Lease:
        admission = None

        def close(self):
            pytest.fail("the constructed service owns its lease")

    class Service:
        def __init__(self, *_args, **_kwargs):
            observed.append("constructed")

        async def start(self):
            observed.append("started")
            asyncio.get_running_loop().call_soon(callback)

        async def aclose(self):
            observed.append("closed")

    monkeypatch.setattr(module, "ParentLifetime", Live)
    monkeypatch.setattr(module.ProfileLease, "acquire", lambda *_args, **_kw: Lease())
    monkeypatch.setattr(module, "CodexClient", lambda *_args, **_kw: object())
    monkeypatch.setattr(module, "JarvisService", Service)
    await asyncio.wait_for(module._serve(arguments(tmp_path, parent_watch_stdin=True)), 2)
    assert observed == ["constructed", "started", "closed", "watch_closed"]


@pytest.mark.parametrize("fields", [
    {"bundled_runtime_root": Path("/synthetic/bundle")},
    {"bundled_runtime_sha256": "0" * 64},
    {"bundled_runtime_root": Path("/synthetic/bundle"),
     "bundled_runtime_sha256": "not-a-pin"},
])
async def test_bad_bundle_fails_before_any_profile_or_fallback(tmp_path, fields):
    with pytest.raises(BundledRuntimeError):
        await module._serve(arguments(tmp_path, **fields))
    assert not (tmp_path / "absent").exists()


async def test_watch_is_closed_even_if_bundle_admission_raises(tmp_path, monkeypatch):
    observed = []

    class Live:
        ended = False

        def check(self):
            return True

        def attach(self, *_):
            pass

        def close(self):
            observed.append("closed")

    monkeypatch.setattr(module, "ParentLifetime", Live)
    with pytest.raises(BundledRuntimeError):
        await module._serve(arguments(tmp_path, parent_watch_stdin=True,
                                     bundled_runtime_sha256="0" * 64))
    assert observed == ["closed"]
    assert not (tmp_path / "absent").exists()
