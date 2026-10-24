"""Adversarial ownership review with synthetic sessions and owned local processes.

No account, external network, owner history, or installed application is used.
"""

import asyncio
import contextlib
import signal
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from jarvis_core.codex import CodexClient, CodexError
from jarvis_core.service import JarvisService


class CleanupPeer:
    def __init__(self):
        self.entered = asyncio.Event()
        self.cleaning = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    async def chat(self, message, *, thread_id, temporary, on_thread, on_event, **_options):
        on_thread("owned-" + uuid4().hex)
        self.entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cleaning.set()
            await self.release.wait()

    async def aclose(self):
        self.closed = True


async def _start_waiting(service):
    ref = (await service.create_session({"temporary": True}))["session"]
    params = {"session_id": ref["session_id"], "temporary": True}
    result = await service.start_run({**params, "message": "Solicitud sintética"})
    await asyncio.wait_for(service.provider.entered.wait(), 1)
    return service._handles[result["run_id"]]


async def test_cancelled_shutdown_retains_resources_until_runtime_cleanup_settles(tmp_path):
    peer = CleanupPeer()
    service = JarvisService(tmp_path.resolve(), peer)
    handle = await _start_waiting(service)
    closing = asyncio.create_task(service.aclose())
    try:
        await asyncio.wait_for(peer.cleaning.wait(), 1)
        closing.cancel()
        await asyncio.sleep(0.02)
        assert not peer.closed, "Se cerró el proveedor antes de terminar su limpieza"
        assert not service.store._closed, "Se cerró SQLite mientras el run aún tiene dueño"
        peer.release.set()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(closing, 1)
        await asyncio.wait_for(handle.await_settled(), 1)
    finally:
        peer.release.set()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(closing, 2)
        await asyncio.wait_for(handle.await_settled(), 2)
        await service.aclose()


async def test_transport_shutdown_error_does_not_skip_runtime_cancellation(tmp_path, monkeypatch):
    peer = CleanupPeer()
    service = JarvisService(tmp_path.resolve(), peer)
    handle = await _start_waiting(service)
    close_transport = service.server.aclose

    async def failing_close():
        raise TimeoutError("Synthetic transport cleanup failure")

    monkeypatch.setattr(service.server, "aclose", failing_close)
    closing = asyncio.create_task(service.aclose())
    try:
        await asyncio.wait_for(peer.cleaning.wait(), 0.2)
        assert not peer.closed
        peer.release.set()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(closing, 1)
        await asyncio.wait_for(handle.await_settled(), 1)
        assert peer.closed
    finally:
        handle.cancel()
        peer.release.set()
        await asyncio.wait_for(handle.await_settled(), 1)
        with contextlib.suppress(TimeoutError):
            await closing
        monkeypatch.setattr(service.server, "aclose", close_transport)
        with contextlib.suppress(TimeoutError):
            await service.aclose()
        await peer.aclose()
        service.store.close()


async def _owned_sleeping_process():
    return await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(30)", start_new_session=True,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL, env={},
    )


async def test_concurrent_provider_stops_cannot_forget_a_replacement_process(tmp_path, monkeypatch):
    client = CodexClient(Path(sys.executable), tmp_path.resolve())
    original = await _owned_sleeping_process()
    client._process = original
    waited, release = asyncio.Event(), asyncio.Event()
    original_wait = original.wait
    replacement = None

    async def delayed_wait():
        code = await original_wait()
        waited.set()
        await release.wait()
        return code

    monkeypatch.setattr(original, "wait", delayed_wait)
    slow = asyncio.create_task(client._stop_process())
    second = None
    try:
        await asyncio.wait_for(waited.wait(), 1)
        second = asyncio.create_task(client._stop_process())
        await asyncio.sleep(0.02)
        if not second.done():
            # Serializing lifecycle changes is also a valid solution: another
            # start/stop cannot publish a replacement while the owner is closing.
            release.set()
            await asyncio.wait_for(asyncio.gather(slow, second), 1)
        else:
            await second
            replacement = await _owned_sleeping_process()
            client._process = replacement
            release.set()
            await asyncio.wait_for(slow, 1)
            assert client._process is replacement, "Un cierre antiguo perdió el proceso nuevo"
    finally:
        release.set()
        for task in (slow, second):
            if task is not None:
                await asyncio.wait_for(task, 2)
        for process in (original, replacement):
            if process is not None and process.returncode is None:
                process.send_signal(signal.SIGTERM)
                await asyncio.wait_for(process.wait(), 2)
        await client.aclose()


@pytest.mark.parametrize("url", [
    "https://auth.openai.com:not-a-port/authorize",
    "https://auth.openai.com:99999/authorize",
    "https://[invalid/authorize",
])
async def test_malformed_login_urls_return_a_public_provider_error(tmp_path, monkeypatch, url):
    client = CodexClient(Path(sys.executable), tmp_path.resolve())

    async def started():
        pass

    async def request(method, params):
        return {"authUrl": url}

    monkeypatch.setattr(client, "start", started)
    monkeypatch.setattr(client, "request", request)
    with pytest.raises(CodexError, match=r"^codex_login_url_invalid$"):
        await client.login()
