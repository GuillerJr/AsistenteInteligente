"""Causal pipe lifetime contracts; no installed services, PID adoption or signals."""

import asyncio
import errno
import fcntl
import json
import os
import pty
import sys
from pathlib import Path

import pytest

import jarvis_core.parent_lifetime as module
from jarvis_core.parent_lifetime import ParentLifetime, ParentLifetimeError


@pytest.fixture
def pipe():
    descriptors = os.pipe()
    pins = {
        fd: (os.fstat(fd).st_dev, os.fstat(fd).st_ino,
             fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE)
        for fd in descriptors
    }
    try:
        yield descriptors
    finally:
        for descriptor in descriptors:
            try:
                info = os.fstat(descriptor)
                current = (info.st_dev, info.st_ino,
                           fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE)
                if current == pins[descriptor]:
                    os.close(descriptor)
            except OSError as error:
                assert error.errno == errno.EBADF


def test_initial_eof_is_terminal_before_any_store_or_event_loop(pipe):
    read, write = pipe
    os.close(write)
    watch = ParentLifetime(read)
    assert watch.ended and not watch.violation
    assert watch.reason == "parent_watch_parent_eof"
    watch.close()
    os.fstat(read)  # The launcher's stdin descriptor remains its own property.


def test_initial_protocol_byte_is_terminal_and_not_retained_or_logged(pipe):
    read, write = pipe
    os.write(write, b"DO_NOT_RETAIN_THIS_PAYLOAD")
    watch = ParentLifetime(read)
    assert watch.ended and watch.violation
    assert watch.reason == "parent_watch_protocol_violation"
    assert "DO_NOT_RETAIN" not in repr(vars(watch))
    watch.close()


def test_empty_pipe_with_live_writer_is_not_eof_and_owned_duplicate_is_cloexec(pipe):
    read, _ = pipe
    flags = fcntl.fcntl(read, fcntl.F_GETFL)
    watch = ParentLifetime(read)
    assert not watch.ended and not watch.violation and watch.reason is None
    assert watch._fd != read and not os.get_inheritable(watch._fd)
    assert fcntl.fcntl(read, fcntl.F_GETFL) & os.O_NONBLOCK
    watch.close()
    assert fcntl.fcntl(read, fcntl.F_GETFL) == flags
    assert not watch.ended  # Retiring a watcher is not evidence of parent death.


def test_fresh_admission_check_observes_eof_without_attaching_or_scheduling_reader(pipe):
    read, write = pipe
    watch = ParentLifetime(read)
    admitted = []
    try:
        assert watch.check()
        os.close(write)
        if watch.check():
            admitted.append("would-open-store")
        assert admitted == [] and watch.ended
        assert watch.reason == "parent_watch_parent_eof"
        assert watch._loop is None and watch._notification is None
    finally:
        watch.close()
    assert not watch.check()


async def test_fresh_check_does_not_depend_on_pending_event_loop_notification(pipe):
    read, write = pipe
    watch = ParentLifetime(read)
    calls, admitted = [], []
    try:
        watch.attach(asyncio.get_running_loop(), lambda: calls.append("parent-ended"))
        os.close(write)
        # No await: neither the EOF reader nor its queued callback has run yet.
        if watch.check():
            admitted.append("would-open-store")
        assert admitted == [] and calls == [] and watch.ended
        await asyncio.sleep(0)
        assert calls == ["parent-ended"]
        assert not watch.check()
    finally:
        watch.close()


@pytest.mark.parametrize("descriptor", [-1, True, "0"])
def test_invalid_descriptor_is_rejected(descriptor):
    with pytest.raises(ParentLifetimeError, match=r"^parent_watch_descriptor_invalid$"):
        ParentLifetime(descriptor)


def test_regular_file_is_rejected_without_taking_ownership(tmp_path):
    with (tmp_path / "not-a-pipe").open("wb+") as handle:
        before = fcntl.fcntl(handle.fileno(), fcntl.F_GETFL)
        with pytest.raises(ParentLifetimeError, match=r"^parent_watch_pipe_required$"):
            ParentLifetime(handle.fileno())
        assert fcntl.fcntl(handle.fileno(), fcntl.F_GETFL) == before


def test_tty_is_rejected_without_reading_input():
    master, slave = pty.openpty()
    try:
        with pytest.raises(ParentLifetimeError, match=r"^parent_watch_pipe_required$"):
            ParentLifetime(slave)
        os.fstat(slave)
    finally:
        os.close(slave)
        os.close(master)


def test_write_only_pipe_is_rejected_without_closing_it(pipe):
    _, write = pipe
    with pytest.raises(ParentLifetimeError, match=r"^parent_watch_readonly_required$"):
        ParentLifetime(write)
    os.fstat(write)


def test_read_write_fifo_is_rejected(tmp_path):
    fifo = tmp_path / "duplex-fifo"
    os.mkfifo(fifo, 0o600)
    descriptor = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
    try:
        with pytest.raises(ParentLifetimeError, match=r"^parent_watch_readonly_required$"):
            ParentLifetime(descriptor)
    finally:
        os.close(descriptor)


async def test_reader_waits_for_actual_eof_and_notifies_exactly_once(pipe):
    read, write = pipe
    watch = ParentLifetime(read)
    notified, calls = asyncio.Event(), []

    def ended():
        calls.append(watch.reason)
        notified.set()

    try:
        watch.attach(asyncio.get_running_loop(), ended)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(notified.wait(), 0.02)
        assert calls == [] and not watch.ended
        os.close(write)
        await asyncio.wait_for(notified.wait(), 2)
        watch._on_readable()
        await asyncio.sleep(0)
        assert calls == ["parent_watch_parent_eof"] and not watch.violation
    finally:
        watch.close()


async def test_a_byte_after_attach_stops_observation_and_notifies_once(pipe):
    read, write = pipe
    watch = ParentLifetime(read)
    notified = asyncio.Event()
    try:
        watch.attach(asyncio.get_running_loop(), notified.set)
        os.write(write, b"unexpected")
        await asyncio.wait_for(notified.wait(), 2)
        assert watch.ended and watch.violation
        assert watch.reason == "parent_watch_protocol_violation"
        assert not watch._reader_installed and watch._fd is None
        os.fstat(read)
    finally:
        watch.close()


async def test_terminal_initial_peek_notifies_only_when_attached(pipe):
    read, write = pipe
    os.close(write)
    watch = ParentLifetime(read)
    calls = []
    watch.attach(asyncio.get_running_loop(), lambda: calls.append(watch.reason))
    assert calls == []
    await asyncio.sleep(0)
    assert calls == ["parent_watch_parent_eof"]
    watch.close()


async def test_close_retires_reader_and_cancels_queued_terminal_notification(pipe):
    read, write = pipe
    os.close(write)
    watch = ParentLifetime(read)
    calls = []
    watch.attach(asyncio.get_running_loop(), lambda: calls.append("wrong"))
    watch.close()
    watch.close()
    await asyncio.sleep(0)
    assert calls == []
    with pytest.raises(ParentLifetimeError, match=r"^parent_watch_attach_invalid$"):
        watch.attach(asyncio.get_running_loop(), lambda: None)


async def test_descriptor_replacement_is_terminal_and_foreign_fd_is_not_closed(pipe):
    read, _ = pipe
    other_read, other_write = os.pipe()
    watch = ParentLifetime(read)
    own = watch._fd
    calls = []
    try:
        watch.attach(asyncio.get_running_loop(), lambda: calls.append(watch.reason))
        os.close(own)
        os.dup2(other_read, own)
        watch._on_readable()
        await asyncio.sleep(0)
        assert watch.ended and watch.reason == "parent_watch_descriptor_changed"
        assert calls == ["parent_watch_descriptor_changed"]
        watch.close()
        os.write(other_write, b"R")
        assert os.read(own, 1) == b"R"
    finally:
        watch.close()
        os.close(own)
        os.close(other_read)
        os.close(other_write)


def test_repeated_close_cannot_close_a_later_reused_descriptor(pipe):
    read, _ = pipe
    watch = ParentLifetime(read)
    own = watch._fd
    watch.close()
    os.dup2(read, own)
    try:
        watch.close()
        os.fstat(own)
    finally:
        os.close(own)


async def test_read_error_is_terminal_without_signalling_or_restart(pipe, monkeypatch):
    read, _ = pipe
    watch = ParentLifetime(read)
    own, original_read = watch._fd, os.read
    notified = asyncio.Event()

    def failed(fd, count):
        if fd == own:
            raise OSError(errno.EIO, "synthetic-read-failure")
        return original_read(fd, count)

    try:
        monkeypatch.setattr(module.os, "read", failed)
        watch.attach(asyncio.get_running_loop(), notified.set)
        await asyncio.wait_for(notified.wait(), 2)
        assert watch.ended and not watch.violation
        assert watch.reason == "parent_watch_read_failed"
    finally:
        watch.close()


async def test_removing_nonblocking_flag_is_terminal_instead_of_blocking_loop(pipe):
    read, _ = pipe
    watch = ParentLifetime(read)
    notified = asyncio.Event()
    flags = fcntl.fcntl(read, fcntl.F_GETFL)
    try:
        watch.attach(asyncio.get_running_loop(), notified.set)
        fcntl.fcntl(read, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
        watch._on_readable()
        await asyncio.wait_for(notified.wait(), 2)
        assert watch.ended and watch.reason == "parent_watch_descriptor_changed"
    finally:
        watch.close()


async def test_reader_registration_failure_is_fail_closed(pipe, monkeypatch):
    read, _ = pipe
    watch = ParentLifetime(read)
    loop, notified = asyncio.get_running_loop(), asyncio.Event()

    def unavailable(*_):
        raise NotImplementedError("synthetic-loop")

    try:
        monkeypatch.setattr(loop, "add_reader", unavailable)
        watch.attach(loop, notified.set)
        await asyncio.wait_for(notified.wait(), 2)
        assert watch.ended and watch.reason == "parent_watch_reader_unavailable"
        assert watch._fd is None
    finally:
        watch.close()


_PARENT_DEATH_DRIVER = r"""
import os, subprocess, sys
source, control = sys.argv[1], int(sys.argv[2])
read, write = os.pipe()
code = r'''
import asyncio, json, os, sys
sys.path.insert(0, sys.argv[1])
from jarvis_core.parent_lifetime import ParentLifetime
async def main():
    control = int(sys.argv[2])
    loop, done = asyncio.get_running_loop(), asyncio.Event()
    watch = ParentLifetime()
    fallback = []
    def cleanup():
        if not os.read(control, 1):
            fallback.append(True)
            done.set()
    loop.add_reader(control, cleanup)
    watch.attach(loop, done.set)
    print('READY', flush=True)
    try:
        await asyncio.wait_for(done.wait(), 3)
        print(json.dumps({'ended': watch.ended, 'reason': watch.reason,
                          'violation': watch.violation, 'fallback_used': bool(fallback)}),
              flush=True)
    finally:
        loop.remove_reader(control)
        os.close(control)
        watch.close()
asyncio.run(main())
'''
subprocess.Popen([sys.executable, '-I', '-S', '-B', '-u', '-c', code, source, str(control)],
                 stdin=read, close_fds=True, pass_fds=(control,))
os.close(read)
os.close(control)
# Only this process owns the lifetime writer. Death, not a protocol message or
# explicit writer close, must therefore produce the child's EOF.
os.read(0, 1)
os._exit(0)
"""


@pytest.mark.skipif(os.environ.get("CI") != "true", reason="real parent death is CI-only")
async def test_real_parent_death_closes_the_only_writer_and_child_exits(tmp_path):
    control_read, control_write = os.pipe()
    source = str(Path(module.__file__).resolve().parents[1])
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-S", "-B", "-u", "-c", _PARENT_DEATH_DRIVER,
            source, str(control_read), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            pass_fds=(control_read,), cwd=tmp_path,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        )
        os.close(control_read)
        control_read = None
        assert await asyncio.wait_for(process.stdout.readline(), 5) == b"READY\n"
        output, error = await asyncio.wait_for(process.communicate(b"X"), 5)
        assert process.returncode == 0 and not error
        assert json.loads(output) == {
            "ended": True, "reason": "parent_watch_parent_eof",
            "violation": False, "fallback_used": False,
        }
    finally:
        # A separate fixture-only channel plus the child's three-second deadline
        # bounds cleanup even if the lifetime assertion fails. Neither is the
        # parent-lifetime pipe, and neither is credited as the passing cause.
        os.close(control_write)
        if control_read is not None:
            os.close(control_read)
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            await asyncio.wait_for(process.communicate(), 5)
