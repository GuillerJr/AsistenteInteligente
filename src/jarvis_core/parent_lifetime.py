"""Read-only pipe lifetime of the process which launched the service.

The descriptor is an explicit launch capability, never a parent PID to signal
or a reason to restart. The caller checks ``ended`` before opening its stores.
Only this object's duplicate is owned; the supplied stdin descriptor is not.
"""

from __future__ import annotations

import asyncio
import errno
import fcntl
import os
import stat
from collections.abc import Callable


class ParentLifetimeError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode), info.st_uid


class ParentLifetime:
    """Observe one validated pipe without blocking or inheriting its duplicate.

    Bytes are never a command. Any byte ends this lifetime as a protocol
    violation, without retaining or logging its content. EOF, read failure or
    replacement ends it too. Event-loop observation uses one reader callback;
    there is no polling task, PID lookup, signal or automatic restart.
    """

    def __init__(self, fd: int = 0):
        self.ended = False
        self.violation = False
        self.reason: str | None = None
        self._fd: int | None = None
        self._pin: tuple[int, int, int, int] | None = None
        self._original_flags: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._callback: Callable[[], None] | None = None
        self._reader_installed = False
        self._notification: asyncio.Handle | None = None
        self._notified = False
        self._attached = False
        self._closed = False
        if type(fd) is not int or fd < 0:
            raise ParentLifetimeError("parent_watch_descriptor_invalid")
        try:
            info = os.fstat(fd)
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        except OSError:
            raise ParentLifetimeError("parent_watch_descriptor_invalid") from None
        if not stat.S_ISFIFO(info.st_mode):
            raise ParentLifetimeError("parent_watch_pipe_required")
        if flags & os.O_ACCMODE != os.O_RDONLY:
            raise ParentLifetimeError("parent_watch_readonly_required")
        if info.st_uid != os.geteuid():
            raise ParentLifetimeError("parent_watch_owner_invalid")
        try:
            self._fd = os.dup(fd)
            self._pin = _identity(info)
            self._original_flags = flags
            os.set_inheritable(self._fd, False)
            if not self._current():
                raise ParentLifetimeError("parent_watch_descriptor_changed")
            fcntl.fcntl(self._fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            if not self._current():
                raise ParentLifetimeError("parent_watch_descriptor_changed")
        except (OSError, ParentLifetimeError):
            self._release()
            raise ParentLifetimeError("parent_watch_descriptor_changed") from None
        self._read_once()

    def attach(self, loop: asyncio.AbstractEventLoop, on_parent_exit: Callable[[], None]) -> None:
        """Attach once; a terminal initial peek notifies on the next loop turn."""
        if self._closed or self._attached or loop.is_closed() or not callable(on_parent_exit):
            raise ParentLifetimeError("parent_watch_attach_invalid")
        self._attached = True
        self._loop = loop
        self._callback = on_parent_exit
        if not self.ended:
            self._read_once()
        if self.ended:
            self._schedule_notification()
            return
        try:
            loop.add_reader(self._fd, self._on_readable)
            self._reader_installed = True
        except (OSError, RuntimeError, NotImplementedError):
            self._finish("parent_watch_reader_unavailable")

    def check(self) -> bool:
        """Synchronously recheck admission without waiting for a reader callback.

        This is a fresh, nonblocking observation, not atomic ownership of the
        parent or a guarantee that it cannot exit after this function returns.
        """
        if not self._closed and not self.ended:
            self._read_once()
        return not self.ended and not self._closed

    def _current(self) -> bool:
        if self._fd is None or self._pin is None:
            return False
        try:
            return (
                _identity(os.fstat(self._fd)) == self._pin
                and fcntl.fcntl(self._fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY
            )
        except OSError:
            return False

    def _on_readable(self) -> None:
        if not self._closed and not self.ended:
            self._read_once()

    def _read_once(self) -> None:
        if not self._current():
            self._finish("parent_watch_descriptor_changed")
            return
        try:
            nonblocking = fcntl.fcntl(self._fd, fcntl.F_GETFL) & os.O_NONBLOCK
        except OSError:
            nonblocking = False
        if not nonblocking:
            self._finish("parent_watch_descriptor_changed")
            return
        for _ in range(4):
            try:
                data = os.read(self._fd, 1)
                break
            except InterruptedError:
                continue
            except OSError as error:
                if error.errno in {errno.EAGAIN, errno.EWOULDBLOCK}:
                    return
                self._finish("parent_watch_read_failed")
                return
        else:
            self._finish("parent_watch_read_failed")
            return
        if data:
            self._finish("parent_watch_protocol_violation", violation=True)
        else:
            self._finish("parent_watch_parent_eof")

    def _finish(self, reason: str, *, violation: bool = False) -> None:
        if self.ended or self._closed:
            return
        self.ended, self.violation, self.reason = True, violation, reason
        self._remove_reader()
        self._release()
        self._schedule_notification()

    def _schedule_notification(self) -> None:
        if (
            self._loop is not None
            and not self._loop.is_closed()
            and self._callback is not None
            and self._notification is None
            and not self._notified
            and not self._closed
        ):
            self._notification = self._loop.call_soon(self._notify)

    def _notify(self) -> None:
        self._notification = None
        if self._closed or self._notified or not self.ended:
            return
        self._notified = True
        if self._callback is not None:
            self._callback()

    def _remove_reader(self) -> None:
        if self._reader_installed and self._loop is not None and self._fd is not None:
            try:
                self._loop.remove_reader(self._fd)
            except (OSError, RuntimeError):
                pass
        self._reader_installed = False

    def _release(self) -> None:
        # External descriptor replacement is a loss of authority, not ownership
        # of the new descriptor. The supplied stdin is never closed here.
        if self._current():
            try:
                if (
                    self._original_flags is not None
                    and fcntl.fcntl(self._fd, fcntl.F_GETFL)
                    == self._original_flags | os.O_NONBLOCK
                ):
                    fcntl.fcntl(self._fd, fcntl.F_SETFL, self._original_flags)
                if self._current():
                    os.close(self._fd)
            except OSError:
                pass
        self._fd = None

    def close(self) -> None:
        """Retire our reader and duplicate; explicit close never fakes parent EOF."""
        if self._closed:
            return
        self._closed = True
        if self._notification is not None:
            self._notification.cancel()
            self._notification = None
        self._remove_reader()
        self._release()
        self._callback = None
        self._loop = None
