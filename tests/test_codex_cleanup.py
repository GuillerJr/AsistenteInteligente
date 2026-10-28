"""Owned sleeping subprocesses, synthetic denied signals, no Codex/account/network."""

import asyncio
import contextlib
import errno
import signal
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from test_service_review import _owned_sleeping_process

import jarvis_core.codex as module

CodexError = module.CodexError


class SyntheticStdin:
    def __init__(self):
        self.frames = []

    def write(self, frame):
        self.frames.append(frame)

    async def drain(self):
        return


@dataclass
class Owned:
    client: module.CodexClient
    process: asyncio.subprocess.Process
    reader: asyncio.Task
    stderr: asyncio.Task
    stdin: SyntheticStdin
    killpg: object
    wait: object


@pytest.fixture
async def owned(tmp_path):
    client = module.CodexClient(Path(sys.executable), tmp_path.resolve())
    process = await _owned_sleeping_process()
    reader = asyncio.create_task(asyncio.Event().wait())
    stderr = asyncio.create_task(asyncio.Event().wait())
    stdin = SyntheticStdin()
    # The real fixture intentionally has /dev/null streams. This host-side fake
    # records a would-be RPC without sending bytes to any program or provider.
    process.stdin = stdin
    client._process, client._reader, client._stderr = process, reader, stderr
    client._threads.add("synthetic-owned-thread")
    value = Owned(client, process, reader, stderr, stdin, module.os.killpg, process.wait)
    try:
        yield value
    finally:
        # Tests own this no-fork sleeper. Never let a deliberately failed client
        # cleanup leak it, and never signal an unrelated PID or process group.
        if process.returncode is None:
            process.kill()
        await asyncio.wait_for(value.wait(), 3)
        for task in (reader, stderr, client._reader, client._stderr):
            if task is not None:
                task.cancel()
        await asyncio.gather(reader, stderr, return_exceptions=True)


async def assert_blocked(value):
    assert value.client._process is value.process and value.client._cleanup_pending
    assert value.client._reader is value.reader and value.client._stderr is value.stderr
    assert value.client._threads == {"synthetic-owned-thread"}
    with pytest.raises(CodexError, match=r"^codex_cleanup_incomplete$"):
        await value.client.start()
    with pytest.raises(CodexError, match=r"^codex_cleanup_incomplete$"):
        await value.client._write({"method": "turn/start", "params": {}})
    with pytest.raises(CodexError, match=r"^codex_cleanup_incomplete$"):
        await value.client.chat("Solicitud sintética")
    assert value.stdin.frames == []


async def assert_finished(value):
    await value.client.aclose()
    assert value.process.returncode is not None
    assert value.reader.done() and value.stderr.done()
    assert value.client._process is value.client._reader is value.client._stderr is None
    assert not value.client._cleanup_pending and not value.client._threads
    with pytest.raises(CodexError, match=r"^codex_closed$"):
        await value.client.start()


async def test_denied_signal_retains_pid_readers_and_blocks_start_write_and_inference(
    owned, monkeypatch
):
    calls = []

    def denied(pid, sig):
        assert pid == owned.process.pid
        calls.append(sig)
        raise PermissionError("synthetic denied signal; no environment diagnosis")

    monkeypatch.setattr(module.os, "killpg", denied)
    with pytest.raises(CodexError, match=r"^codex_cleanup_incomplete$"):
        await owned.client._stop_process()
    assert calls == [signal.SIGTERM] and owned.process.returncode is None
    assert not owned.reader.done() and not owned.stderr.done()
    await assert_blocked(owned)
    # No internal retry is allowed to silently signal a retained uncertain PID.
    with pytest.raises(CodexError, match=r"^codex_cleanup_incomplete$"):
        await owned.client._stop_process()
    assert calls == [signal.SIGTERM]
    monkeypatch.setattr(module.os, "killpg", owned.killpg)
    await assert_finished(owned)


async def test_failed_explicit_close_can_only_retry_cleanup_not_reopen_the_client(
    owned, monkeypatch
):
    def denied(_pid, _sig):
        raise PermissionError("synthetic denial")

    monkeypatch.setattr(module.os, "killpg", denied)
    with pytest.raises(CodexError, match=r"^codex_cleanup_incomplete$"):
        await owned.client.aclose()
    assert owned.client._closed and owned.process.returncode is None
    await assert_blocked(owned)
    monkeypatch.setattr(module.os, "killpg", owned.killpg)
    await assert_finished(owned)
    await owned.client.aclose()


async def test_probe_denied_after_reaping_does_not_certify_group_absence(owned, monkeypatch):
    def denied_probe(pid, sig):
        assert pid == owned.process.pid
        if sig == 0:
            raise PermissionError("synthetic probe denial")
        return owned.killpg(pid, sig)

    monkeypatch.setattr(module.os, "killpg", denied_probe)
    with pytest.raises(CodexError, match=r"^codex_cleanup_incomplete$"):
        await owned.client._stop_process()
    assert owned.process.returncode is not None
    assert not owned.reader.done() and not owned.stderr.done()
    await assert_blocked(owned)
    monkeypatch.setattr(module.os, "killpg", owned.killpg)
    await assert_finished(owned)


async def test_leader_reaped_but_group_still_present_never_releases_ownership(owned, monkeypatch):
    # No descendant is created. Model a still-present group explicitly after the
    # real owned leader is reaped; signal zero must be the absence evidence.
    owned.process.kill()
    await owned.wait()
    signals = []

    def group_present(pid, sig):
        assert pid == owned.process.pid
        if sig != 0:
            signals.append(sig)

    monkeypatch.setattr(module.os, "killpg", group_present)
    monkeypatch.setattr(owned.client, "_shutdown_timeout", 0.01)
    with pytest.raises(CodexError, match=r"^codex_cleanup_incomplete$"):
        await owned.client._stop_process()
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    await assert_blocked(owned)
    monkeypatch.setattr(module.os, "killpg", owned.killpg)
    monkeypatch.setattr(owned.client, "_shutdown_timeout", 2.0)
    await assert_finished(owned)


async def test_both_post_signal_waits_are_finite_and_keep_unsettled_child(owned, monkeypatch):
    calls = []

    def no_effect(pid, sig):
        assert pid == owned.process.pid
        calls.append(sig)
        if sig == 0:
            return owned.killpg(pid, sig)

    monkeypatch.setattr(module.os, "killpg", no_effect)
    monkeypatch.setattr(owned.client, "_shutdown_timeout", 0.01)
    async with asyncio.timeout(1):
        with pytest.raises(CodexError, match=r"^codex_cleanup_incomplete$"):
            await owned.client._stop_process()
    assert calls == [signal.SIGTERM, signal.SIGKILL] and owned.process.returncode is None
    await assert_blocked(owned)
    monkeypatch.setattr(module.os, "killpg", owned.killpg)
    monkeypatch.setattr(owned.client, "_shutdown_timeout", 2.0)
    await assert_finished(owned)


async def test_concurrent_start_waits_for_owner_and_write_is_blocked_during_cleanup(
    owned, monkeypatch
):
    entered, release = asyncio.Event(), asyncio.Event()

    def no_effect(pid, sig):
        assert pid == owned.process.pid and sig in {signal.SIGTERM, signal.SIGKILL}

    async def denied_wait():
        entered.set()
        await release.wait()
        raise TimeoutError("synthetic unsettled wait")

    monkeypatch.setattr(module.os, "killpg", no_effect)
    monkeypatch.setattr(owned.process, "wait", denied_wait)
    closing = asyncio.create_task(owned.client._stop_process())
    starting = None
    try:
        await asyncio.wait_for(entered.wait(), 3)
        starting = asyncio.create_task(owned.client.start())
        await asyncio.sleep(0)
        assert not starting.done() and owned.client._start_lock.locked()
        with pytest.raises(CodexError, match=r"^codex_cleanup_incomplete$"):
            await owned.client._write({"method": "turn/start", "params": {}})
        assert owned.stdin.frames == []
        release.set()
        for task in (closing, starting):
            with pytest.raises(CodexError, match=r"^codex_cleanup_incomplete$"):
                await task
        await assert_blocked(owned)
    finally:
        release.set()
        for task in (closing, starting):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*filter(None, (closing, starting)), return_exceptions=True)
        monkeypatch.setattr(module.os, "killpg", owned.killpg)
        monkeypatch.setattr(owned.process, "wait", owned.wait)
    await assert_finished(owned)


async def test_cancelled_cleanup_keeps_pending_ownership_for_explicit_close(owned, monkeypatch):
    entered = asyncio.Event()

    async def waiting():
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(owned.process, "wait", waiting)
    closing = asyncio.create_task(owned.client._stop_process())
    await asyncio.wait_for(entered.wait(), 3)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    await assert_blocked(owned)
    monkeypatch.setattr(owned.process, "wait", owned.wait)
    await assert_finished(owned)


async def test_reader_cleanup_timeout_retains_references_until_explicit_finish(owned, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_reader():
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    owned.reader.cancel()
    await asyncio.gather(owned.reader, return_exceptions=True)
    reader = asyncio.create_task(delayed_reader())
    owned.client._reader = owned.reader = reader
    await entered.wait()
    # The real process is reaped before testing only the reader shutdown budget.
    owned.process.kill()
    await owned.wait()
    monkeypatch.setattr(owned.client, "_shutdown_timeout", 0.01)
    try:
        with pytest.raises(CodexError, match=r"^codex_cleanup_incomplete$"):
            await owned.client._stop_process()
        assert owned.client._reader is reader and not reader.done()
        await assert_blocked(owned)
    finally:
        release.set()
        monkeypatch.setattr(owned.client, "_shutdown_timeout", 2.0)
        with contextlib.suppress(CodexError):
            await owned.client.aclose()
    assert owned.client._process is None and reader.done()


async def test_successful_stop_reaps_and_releases_only_its_original_group(owned):
    await owned.client._stop_process()
    assert owned.process.returncode is not None and owned.reader.done() and owned.stderr.done()
    assert owned.client._process is None and not owned.client._cleanup_pending
    assert not owned.client._threads
    await owned.client.aclose()


async def test_group_permission_race_needs_reaped_child_and_fresh_absence(owned, monkeypatch):
    calls = []

    def exited_before_child_watcher(pid, sig):
        assert pid == owned.process.pid
        calls.append(sig)
        if sig == signal.SIGTERM:
            # Deterministic kernel/asyncio ordering: the owned child exits, but
            # its watcher has not published returncode when killpg reports EPERM.
            owned.process.kill()
            raise PermissionError(errno.EPERM, "SYNTHETIC-PRIVATE-OS-MESSAGE")
        assert sig == 0
        return owned.killpg(pid, sig)

    monkeypatch.setattr(module.os, "killpg", exited_before_child_watcher)
    await owned.client._stop_process()
    assert calls == [signal.SIGTERM, 0]
    assert owned.process.returncode is not None
    assert owned.client._process is None and not owned.client._cleanup_pending
    assert owned.reader.done() and owned.stderr.done()
    await owned.client.aclose()


async def test_permission_race_with_present_or_reused_group_never_signals_again(
    owned, monkeypatch, caplog
):
    calls = []

    def group_still_present(pid, sig):
        assert pid == owned.process.pid
        calls.append(sig)
        if sig == signal.SIGTERM:
            owned.process.kill()
            raise PermissionError(errno.EPERM, "SYNTHETIC-PRIVATE-OS-MESSAGE")
        assert sig == 0
        # The observed group could contain descendants or have been reused.
        # Reaping our child alone cannot authorize signalling that group again.

    monkeypatch.setattr(module.os, "killpg", group_still_present)
    with pytest.raises(CodexError, match=r"^codex_cleanup_incomplete$") as caught:
        await owned.client._stop_process()
    assert calls == [signal.SIGTERM, 0] and owned.process.returncode is not None
    assert caught.value.cleanup_stage == "permission_group_unsettled"
    assert caught.value.os_errno == errno.EPERM
    assert "SYNTHETIC-PRIVATE" not in str(caught.value) + caplog.text
    assert "stage=permission_group_unsettled errno=1" in caplog.text
    await assert_blocked(owned)
    monkeypatch.setattr(module.os, "killpg", owned.killpg)
    await assert_finished(owned)


async def test_permission_denial_on_live_child_times_out_and_keeps_custody(owned, monkeypatch):
    calls = []

    def denied(pid, sig):
        assert pid == owned.process.pid
        calls.append(sig)
        raise PermissionError(errno.EPERM, "SYNTHETIC-PRIVATE-OS-MESSAGE")

    monkeypatch.setattr(module.os, "killpg", denied)
    monkeypatch.setattr(owned.client, "_shutdown_timeout", 0.02)
    async with asyncio.timeout(1):
        with pytest.raises(CodexError, match=r"^codex_cleanup_incomplete$") as caught:
            await owned.client._stop_process()
    assert calls == [signal.SIGTERM] and owned.process.returncode is None
    assert caught.value.cleanup_stage == "permission_reap_timeout"
    assert caught.value.os_errno == errno.EPERM
    await assert_blocked(owned)
    monkeypatch.setattr(module.os, "killpg", owned.killpg)
    monkeypatch.setattr(owned.client, "_shutdown_timeout", 2.0)
    await assert_finished(owned)


async def test_denied_probe_after_permission_race_is_not_absence(owned, monkeypatch, caplog):
    calls = []

    def denied_probe(pid, sig):
        assert pid == owned.process.pid
        calls.append(sig)
        if sig == signal.SIGTERM:
            owned.process.kill()
        else:
            assert sig == 0
        raise PermissionError(errno.EPERM, "SYNTHETIC-PRIVATE-OS-MESSAGE")

    monkeypatch.setattr(module.os, "killpg", denied_probe)
    with pytest.raises(CodexError, match=r"^codex_cleanup_incomplete$") as caught:
        await owned.client._stop_process()
    assert calls == [signal.SIGTERM, 0] and owned.process.returncode is not None
    assert caught.value.cleanup_stage == "probe_group" and caught.value.os_errno == errno.EPERM
    assert "SYNTHETIC-PRIVATE" not in str(caught.value) + caplog.text
    assert "stage=probe_group errno=1" in caplog.text
    await assert_blocked(owned)
    monkeypatch.setattr(module.os, "killpg", owned.killpg)
    await assert_finished(owned)


async def test_other_signal_error_is_preserved_without_permission_recovery(
    owned, monkeypatch, caplog
):
    calls = []

    def invalid_signal(pid, sig):
        assert pid == owned.process.pid
        calls.append(sig)
        raise OSError(errno.EINVAL, "SYNTHETIC-PRIVATE-OS-MESSAGE")

    monkeypatch.setattr(module.os, "killpg", invalid_signal)
    with pytest.raises(CodexError, match=r"^codex_cleanup_incomplete$") as caught:
        await owned.client._stop_process()
    assert calls == [signal.SIGTERM] and owned.process.returncode is None
    assert caught.value.cleanup_stage == "signal_group" and caught.value.os_errno == errno.EINVAL
    assert "SYNTHETIC-PRIVATE" not in str(caught.value) + caplog.text
    assert f"stage=signal_group errno={errno.EINVAL}" in caplog.text
    await assert_blocked(owned)
    monkeypatch.setattr(module.os, "killpg", owned.killpg)
    await assert_finished(owned)
