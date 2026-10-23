"""Boot the real daemon and authenticated IPC with isolated, synthetic secrets."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from aegis_core.config import Settings
from aegis_core.contracts import AgentResult, ToolCall
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
        # The test client stands in for the signed app, which this boot test cannot be.
        ipc_require_signed_peers=False,
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

    class SyntheticCodex:
        provider_id = "codex"
        status_delay = 0

        def __init__(self, **kwargs):
            self.requests = []
            assert kwargs["working_directory"].is_relative_to(root)
            assert kwargs["state_directory"].is_relative_to(root)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            await self.aclose()

        async def aclose(self):
            pass

        async def status(self):
            if self.status_delay:
                await asyncio.sleep(self.status_delay)
            return {"state": "ready", "installed": True, "model_id": "synthetic-codex",
                    "models": [{"id": "synthetic-codex", "display_name": "Sintético",
                                "images": True, "is_default": True}]}

        async def complete(self, *, role, messages, extra_body=None, **kwargs):
            self.requests.append(messages)
            tools = (extra_body or {}).get("tools")
            if tools:
                return AgentResult(role=role, model_id="synthetic-codex", content="",
                                   tool_calls=(ToolCall(
                                       call_id="synthetic-read", tool_name="filesystem_read_text",
                                       arguments={"path": "example.py", "max_bytes": 4096},
                                       requested_by=role,
                                   ),))
            return AgentResult(role=role, model_id="synthetic-codex",
                               content="La función suma sus argumentos.", finish_reason="stop")

        async def complete_stream(self, *, on_delta, **kwargs):
            result = await self.complete(**kwargs)
            if on_delta is not None:
                on_delta(result.content)
            return result

        async def complete_dialogue_review(self, *, review_role, draft_model_id, **kwargs):
            return await self.complete(role=review_role, **kwargs)

    shutdown = asyncio.Event()
    with patch(
        "aegis_core.providers.nvidia.NvidiaNimClient.__init__",
        side_effect=AssertionError("NVIDIA must never initialize in the Codex runtime"),
    ), patch.multiple(
        runtime,
        Settings=lambda: settings,
        MacOSIpcSecret=IPCSecret,
        MacOSMemorySecret=MemorySecret,
        MacOSGenericSecret=ProviderSecret,
        MacOSAuditAnchor=Anchor,
        CodexChatProvider=SyntheticCodex,
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
            provider = await client.call("provider.status")
            assert provider.ok and provider.payload == {
                "provider": "codex", "credential": "configured", "voice_output": "unconfigured",
            }
            inference = await client.call("settings.inference.status")
            assert inference.ok and inference.payload["model_id"] == "synthetic-codex"
            if not nested:
                # Catalogue/login may exceed the generic 4 s handler deadline.
                # Exercise the real IPC override, below the native 25 s limit.
                SyntheticCodex.status_delay = 4.1
                delayed = await client.call("settings.inference.status")
                SyntheticCodex.status_delay = 0
                assert delayed.ok and delayed.payload["state"] == "ready"
            speech = await client.call("speech.synthesize", {"text": "Prueba sintética"})
            assert not speech.ok and speech.error_code == "speech_provider_unconfigured"
            (root / "example.py").write_text("def suma(a, b):\n    return a + b\n")
            submitted = await client.call("engineering.submit", {
                "text": "Lee example.py y explica qué hace", "workspace_path": str(root),
                "inference_policy": "configured", "persist_conversation": False,
            })
            assert submitted.ok, submitted.error_code
            for _ in range(300):
                result = await client.call("jobs.status", {"job_id": submitted.payload["job_id"]})
                assert result.ok, result.error_code
                if result.payload["status"] in {"completed", "failed", "cancelled"}:
                    break
                await asyncio.sleep(0.01)
            assert result.payload["status"] == "completed", result.payload
            assert result.payload["result"] == "La función suma sus argumentos."
            assert result.payload["evaluation"]["brain"] == "codex"
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
            # Boot the source under test, not a possibly stale non-editable install.
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
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
