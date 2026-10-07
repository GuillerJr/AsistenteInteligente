"""Boot the real daemon and authenticated IPC with isolated, synthetic secrets."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from aegis_core.config import Settings
from aegis_core.ipc.client import IpcClient
from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.runtime import daemon as runtime
from aegis_core.secrets import SecretNotFoundError
from aegis_core.telegram.configuration import TelegramConfiguration, TelegramConfigurationStore


async def corruption_scenario(root: Path, *, nested: bool = False) -> None:
    defaults = Settings(_env_file=None)
    paths = {
        name: root / name
        for name in type(defaults).model_fields
        if isinstance(getattr(defaults, name), Path)
    }
    settings = Settings(
        _env_file=None,
        **{**paths, "workspace_root": root},
        biometric_training_enabled=False,
        spotlight_graph_index_enabled=False,
    )
    TelegramConfigurationStore(settings.telegram_configuration_path).save(
        TelegramConfiguration(
            enabled=True,
            bot_id=99999,
            bot_username="SyntheticJarvis",
            owner_id=12345,
            owner_name="Synthetic owner",
        )
    )
    journal_path = settings.telegram_configuration_path.with_name("telegram-transport.json")
    content = b"[" * 1000 + b"0" + b"]" * 1000 if nested else b"corrupt encrypted transport fixture"
    journal_path.write_bytes(content)
    journal_path.chmod(0o600)

    class IPCSecret:
        def __init__(self, **kwargs):
            pass

        def get_or_create(self):
            return "ab" * 32

    class MemorySecret(IPCSecret):
        def get_or_create(self):
            return b"x" * 32

        get = get_or_create

    class ProviderSecret(IPCSecret):
        def get(self):
            raise AssertionError("This boot test must not request a remote credential")

        def is_configured(self):
            return True

    class Anchor:
        def get(self):
            raise SecretNotFoundError("No synthetic cold-boot anchor")

        def set(self, value):
            pass

    shutdown = asyncio.Event()
    with patch.multiple(
        runtime,
        Settings=lambda: settings,
        MacOSIpcSecret=IPCSecret,
        MacOSMemorySecret=MemorySecret,
        MacOSKeychain=ProviderSecret,
        MacOSGenericSecret=ProviderSecret,
        MacOSAuditAnchor=Anchor,
    ):
        task = asyncio.create_task(runtime._run_daemon(shutdown))
        try:
            for _ in range(300):
                if settings.ipc_socket_path.exists():
                    break
                if task.done():
                    raise AssertionError(f"daemon exited before IPC: {await task}")
                await asyncio.sleep(0.01)
            client = IpcClient(settings.ipc_socket_path, IpcAuthenticator.from_hex("ab" * 32))
            telegram = await client.call("telegram.status")
            assert telegram.ok and telegram.payload["state"] == "blocked"
            assert telegram.payload["error"] == "telegram_journal_invalid"
            created = await client.call("conversations.create", {"title": "Synthetic healthy chat"})
            assert created.ok, created.error_code
            listed = await client.call("conversations.list")
            assert listed.ok, listed.error_code
            assert journal_path.read_bytes() == content
        finally:
            shutdown.set()
            assert await asyncio.wait_for(task, timeout=10) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
async def test_corrupt_telegram_journal_does_not_block_real_daemon_or_chat_ipc(nested):
    # _run_daemon owns shutdown_default_executor, so give it its own process/event loop.
    with tempfile.TemporaryDirectory(prefix="jarvis-tg-boot-", dir="/private/tmp") as directory:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(Path(__file__).resolve()),
            directory,
            str(int(nested)),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        assert process.returncode == 0, (stdout.decode(), stderr.decode())


if __name__ == "__main__":
    asyncio.run(corruption_scenario(Path(sys.argv[1]).resolve(), nested=bool(int(sys.argv[2]))))
