"""Owned nonblocking macOS PTY, without preexec_fn or ambient terminal access."""
from __future__ import annotations

import asyncio
import errno
import os
import termios

# Constant own bootstrap. argv remains separate data, never interpolated code.
# start_new_session is performed by subprocess before this bootstrap runs.
PTY_BOOTSTRAP = (
    "import fcntl,os,sys,termios; "
    "fcntl.ioctl(0,termios.TIOCSCTTY,0); os.execv(sys.argv[1],sys.argv[1:])"
)


class PTYChannel:
    def __init__(self, rows: int, cols: int) -> None:
        self.master, self.slave = os.openpty()
        self.path = os.ttyname(self.slave)
        self.rows, self.cols = rows, cols
        try:
            termios.tcsetwinsize(self.slave, (rows, cols))
            os.set_blocking(self.master, False)
        except BaseException:
            self.close()
            raise

    def close_slave(self) -> None:
        if self.slave >= 0:
            os.close(self.slave)
            self.slave = -1

    def close(self) -> None:
        self.close_slave()
        if self.master >= 0:
            os.close(self.master)
            self.master = -1

    async def _ready(self, *, write: bool) -> None:
        if self.master < 0:
            raise OSError(errno.EBADF, "closed owned PTY")
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        fd = self.master
        def ready():
            if not future.done():
                future.set_result(None)
        register = loop.add_writer if write else loop.add_reader
        remove = loop.remove_writer if write else loop.remove_reader
        register(fd, ready)
        try:
            await future
        finally:
            remove(fd)

    async def read(self, size: int) -> bytes:
        while self.master >= 0:
            try:
                return os.read(self.master, size)
            except BlockingIOError:
                await self._ready(write=False)
            except OSError as error:
                # Linux reports EIO when the last slave closes; macOS returns EOF.
                if error.errno == errno.EIO:
                    return b""
                raise
        return b""

    async def write(self, payload: bytes, check) -> None:
        view = memoryview(payload)
        while view:
            check()
            try:
                count = os.write(self.master, view)
                if count <= 0:
                    raise OSError("short terminal write")
                view = view[count:]
            except BlockingIOError:
                await self._ready(write=True)

    def eof(self) -> bytes:
        settings = termios.tcgetattr(self.master)
        value = settings[6][termios.VEOF]
        value = bytes([value]) if isinstance(value, int) else value
        if not settings[3] & termios.ICANON or not value or value == b"\0":
            raise ValueError("process_pty_eof_requires_canonical_mode")
        # Flush a pending partial line, then deliver the empty canonical read.
        return value * 2

    def resize(self, rows: int, cols: int) -> None:
        termios.tcsetwinsize(self.master, (rows, cols))
        actual = termios.tcgetwinsize(self.master)
        if actual != (rows, cols):
            raise OSError("terminal size did not match")
        self.rows, self.cols = actual
