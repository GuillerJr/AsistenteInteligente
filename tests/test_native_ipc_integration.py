import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from aegis_core.contracts import AgentResult, AgentRole
from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.ipc.server import AegisDaemon
from aegis_core.jobs import SwarmIpcService, SwarmJobManager


class _ImmediateGraph:
    async def ainvoke(self, input: dict[str, object]) -> dict[str, object]:
        del input
        return {
            "final_result": AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id="fake/native-ipc-integration",
                content="respuesta:nativa",
            )
        }


@pytest.mark.asyncio
async def test_native_helper_authenticates_and_submits_voice_job() -> None:
    helper_value = os.getenv("AEGIS_NATIVE_HELPER")
    if not helper_value:
        pytest.skip("set AEGIS_NATIVE_HELPER to run the native IPC contract test")
    helper = Path(helper_value).resolve(strict=True)
    service = f"ai.aegis.ipc-integration.{uuid4()}"
    account = "integration-test"
    secret = "44" * 32
    subprocess.run(
        [
            "/usr/bin/security",
            "add-generic-password",
            "-A",
            "-a",
            account,
            "-s",
            service,
            "-w",
            secret,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    ipc_root = tempfile.TemporaryDirectory(prefix="ag-", dir="/private/tmp")
    Path(ipc_root.name).chmod(0o700)
    socket_path = Path(ipc_root.name) / "aegis.sock"
    jobs = SwarmJobManager(_ImmediateGraph())
    daemon = AegisDaemon(
        socket_path,
        IpcAuthenticator.from_hex(secret),
        handlers=SwarmIpcService(jobs).handlers(),
    )
    transcript = {
        "schema_version": "1.0",
        "type": "speech.transcript",
        "capture_id": "01234567-89ab-cdef-0123-456789abcdef",
        "sequence": 1,
        "text": "Revisa el sistema",
        "locale_identifier": "es-US",
        "duration_milliseconds": 900,
        "is_final": True,
        "on_device": True,
        "confidence": 0.9,
    }
    try:
        async with daemon:
            process = await asyncio.create_subprocess_exec(
                str(helper),
                "submit-transcript",
                "--socket-path",
                str(socket_path),
                "--keychain-service",
                service,
                "--keychain-account",
                account,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(json.dumps(transcript).encode("utf-8")),
                    timeout=10,
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                raise
    finally:
        await jobs.close()
        ipc_root.cleanup()
        subprocess.run(
            [
                "/usr/bin/security",
                "delete-generic-password",
                "-a",
                account,
                "-s",
                service,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

    assert process.returncode == 0, (
        stdout.decode("utf-8", errors="replace")
        + stderr.decode("utf-8", errors="replace")
    )
    event = json.loads(stdout)
    assert event["type"] == "ipc.voice_submitted"
    assert event["status"] in {"queued", "running", "completed"}
    assert set(event) == {"schema_version", "type", "job_id", "status"}
