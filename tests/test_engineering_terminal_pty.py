from __future__ import annotations

import asyncio
import fcntl
import os
import pty
import signal
import struct
import sys
import tempfile
import termios
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from aegis_core.ipc.protocol import IpcAuthenticator, IpcRequest
from aegis_core.ipc.server import AegisDaemon, IpcHandlerResult
from aegis_core.job_contracts import JobSnapshot, JobStatus

_CLIENT = """
import asyncio, sys
from pathlib import Path
from aegis_core.engineering_cli import EngineeringCLI
from aegis_core.ipc.client import IpcClient
from aegis_core.ipc.protocol import IpcAuthenticator
client = IpcClient(Path(sys.argv[1]), IpcAuthenticator.from_hex('a' * 64))
raise SystemExit(asyncio.run(EngineeringCLI(client, workspace=Path(sys.argv[2])).run_interactive()))
"""


@pytest.mark.asyncio
async def test_real_terminal_paste_interrupt_history_and_eof_over_signed_ipc() -> None:
    """Real PTY + child process + signed UDS, without Keychain, models or owner audio."""
    with tempfile.TemporaryDirectory(prefix="aegis-cli-", dir="/private/tmp") as directory:
        root = Path(directory)
        socket_path = root / "cli.sock"
        submitted: list[str] = []
        waiting, cancelled = asyncio.Event(), asyncio.Event()
        job: JobSnapshot | None = None

        async def handler(request: IpcRequest) -> IpcHandlerResult:
            nonlocal job
            if request.method == "engineering.preflight":
                return IpcHandlerResult(ok=True, payload={"workspace_path": str(root)})
            if request.method == "engineering.submit":
                submitted.append(request.payload["text"])
                now = datetime.now(UTC)
                done = len(submitted) > 1
                job = JobSnapshot(
                    job_id=uuid4(),
                    request_id=uuid4(),
                    created_at=now,
                    updated_at=now,
                    status=JobStatus.COMPLETED if done else JobStatus.RUNNING,
                    result="Respuesta local de prueba" if done else None,
                )
            elif request.method == "jobs.wait":
                waiting.set()
                await cancelled.wait()
            elif request.method == "jobs.cancel":
                assert job is not None
                job = job.model_copy(update={"status": JobStatus.CANCELLED})
                cancelled.set()
            assert job is not None
            return IpcHandlerResult(ok=True, payload=job.model_dump(mode="json"))

        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 90, 0, 0))
        os.set_blocking(master, False)
        transcript = bytearray()
        changed = asyncio.Event()
        loop = asyncio.get_running_loop()

        def read_output() -> None:
            try:
                data = os.read(master, 16_384)
            except OSError:
                loop.remove_reader(master)
                return
            if data:
                transcript.extend(data)
                if b"\x1b[6n" in data:
                    os.write(master, b"\x1b[1;1R")
                changed.set()
            else:
                loop.remove_reader(master)

        async def expect(text: str, *, after: int = 0) -> None:
            async with asyncio.timeout(10):
                while text.encode() not in transcript[after:]:
                    changed.clear()
                    await changed.wait()

        async with AegisDaemon(
            socket_path,
            IpcAuthenticator.from_hex("a" * 64),
            handlers={
                name: handler
                for name in (
                    "engineering.preflight",
                    "engineering.submit",
                    "jobs.wait",
                    "jobs.cancel",
                )
            },
        ):
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                _CLIENT,
                str(socket_path),
                str(root),
                stdin=slave,
                stdout=slave,
                stderr=slave,
                start_new_session=True,
                env={
                    **os.environ,
                    "TERM": "xterm-256color",
                    "NO_COLOR": "1",
                    "PROMPT_TOOLKIT_NO_CPR": "1",
                    "PYTHONPATH": "src",
                },
            )
            os.close(slave)
            loop.add_reader(master, read_output)
            try:
                await expect("ENGINEERING CLI")
                await expect("\x1b[?2004h")
                os.write(master, b"borrador\x03")
                await expect("Borrador descartado")
                assert submitted == []
                os.write(master, b"\x1b[200~primera linea\nsegunda linea\x1b[201~")
                await expect("segunda linea")
                assert submitted == []  # Paste inserts, never auto-submits individual lines.
                os.write(master, b"\r")
                await asyncio.wait_for(waiting.wait(), timeout=5)
                assert submitted == ["primera linea\nsegunda linea"]
                process.send_signal(signal.SIGINT)
                await expect("Cancelación confirmada")
                assert cancelled.is_set()
                marker = len(transcript)
                os.write(master, b"Hola\r")
                await expect("Respuesta recibida", after=marker)
                assert submitted[-1] == "Hola"
                marker = len(transcript)
                os.write(master, b"\x1b[A\r")
                await expect("Respuesta recibida", after=marker)
                assert submitted[-2:] == ["Hola", "Hola"]
                marker = len(transcript)
                os.write(master, b"/new\r")
                await expect("historial del editor limpio", after=marker)
                os.write(master, b"\x04")
                assert await asyncio.wait_for(process.wait(), timeout=5) == 0
                assert b"Traceback" not in transcript
                assert len(transcript) < 65_536
            finally:
                loop.remove_reader(master)
                os.close(master)
                if process.returncode is None:
                    process.kill()
                    await process.wait()
