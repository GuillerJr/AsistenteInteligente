from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aegis_core.contracts import AgentRole
from aegis_core.providers.apple import AppleLocalModelClient, AppleLocalModelError


def _helper(tmp_path: Path) -> Path:
    path = tmp_path / "jarvis-local-brain"
    path.write_text(
        """#!/usr/bin/python3
import json
import sys

if sys.argv[1:] == ["--status"]:
    print(json.dumps({"available": True}), flush=True)
    raise SystemExit(0)
request = json.load(sys.stdin)
assert request["instructions"] and request["prompt"]
print(json.dumps({"type": "snapshot", "content": "Hola"}), flush=True)
print(json.dumps({"type": "snapshot", "content": "Hola mundo"}), flush=True)
print(json.dumps({"type": "completed", "content": "Hola mundo"}), flush=True)
""",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


@pytest.mark.asyncio
async def test_private_apple_helper_streams_monotonic_deltas(tmp_path: Path) -> None:
    client = AppleLocalModelClient(_helper(tmp_path))
    chunks: list[str] = []

    result = await client.complete_stream(
        role=AgentRole.PLANNER,
        messages=(
            {"role": "system", "content": "Responde brevemente."},
            {"role": "user", "content": "Saluda."},
        ),
        on_delta=chunks.append,
    )

    assert client.is_available() is True
    assert chunks == ["Hola", " mundo"]
    assert result.content == "Hola mundo"
    assert result.model_id == "apple/system-language-model"


@pytest.mark.asyncio
async def test_private_apple_helper_supports_local_synthesis(tmp_path: Path) -> None:
    client = AppleLocalModelClient(_helper(tmp_path))

    result = await client.complete(
        role=AgentRole.SYNTHESIZER,
        messages=(
            {"role": "system", "content": "Resume datos locales."},
            {"role": "user", "content": "No hay eventos."},
        ),
    )

    assert result.role is AgentRole.SYNTHESIZER
    assert result.model_id == "apple/system-language-model"


@pytest.mark.asyncio
async def test_apple_helper_rejects_non_private_permissions(tmp_path: Path) -> None:
    helper = _helper(tmp_path)
    helper.chmod(0o722)
    client = AppleLocalModelClient(helper)

    assert client.is_available() is False
    with pytest.raises(AppleLocalModelError, match="unavailable"):
        await client.complete_stream(
            role=AgentRole.PLANNER,
            messages=(
                {"role": "system", "content": "Seguro."},
                {"role": "user", "content": "Hola."},
            ),
            on_delta=None,
        )


@pytest.mark.asyncio
async def test_apple_helper_falls_back_when_first_event_stalls(tmp_path: Path) -> None:
    helper = tmp_path / "jarvis-local-brain"
    helper.write_text(
        """#!/usr/bin/python3
import time
time.sleep(1)
""",
        encoding="utf-8",
    )
    helper.chmod(0o700)
    client = AppleLocalModelClient(
        helper,
        timeout_seconds=1,
        first_event_timeout_seconds=0.05,
    )
    loop = asyncio.get_running_loop()
    started_at = loop.time()

    with pytest.raises(AppleLocalModelError, match="first response timed out"):
        await client.complete_stream(
            role=AgentRole.PLANNER,
            messages=(
                {"role": "system", "content": "Responde rápido."},
                {"role": "user", "content": "Hola."},
            ),
            on_delta=None,
        )

    assert loop.time() - started_at < 0.5


def test_apple_helper_requires_first_event_before_total_timeout(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="first event timeout"):
        AppleLocalModelClient(
            _helper(tmp_path),
            timeout_seconds=2,
            first_event_timeout_seconds=3,
        )
