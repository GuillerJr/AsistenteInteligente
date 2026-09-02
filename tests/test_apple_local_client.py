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
assert "maximumResponseTokens" in request
assert "temperature" in request
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
async def test_private_apple_helper_enables_only_fixed_native_tool_mode(tmp_path: Path) -> None:
    helper = tmp_path / "jarvis-local-brain"
    helper.write_text(
        """#!/usr/bin/python3
import json
import sys
request = json.load(sys.stdin)
assert request["toolAugmented"] is True
print(json.dumps({"type": "completed", "content": "Trabajo local aceptado"}), flush=True)
""",
        encoding="utf-8",
    )
    helper.chmod(0o700)
    client = AppleLocalModelClient(helper)

    result = await client.complete(
        role=AgentRole.CODE_SECURITY,
        messages=(
            {"role": "system", "content": "Usa herramientas locales autorizadas."},
            {"role": "user", "content": "Revisa y ejecuta localmente."},
        ),
        extra_body={"local_tool_augmented": True},
    )

    assert result.content == "Trabajo local aceptado"


@pytest.mark.asyncio
async def test_private_apple_helper_rejects_unbounded_remote_tool_schemas(
    tmp_path: Path,
) -> None:
    client = AppleLocalModelClient(_helper(tmp_path))

    with pytest.raises(AppleLocalModelError, match="options are unsupported"):
        await client.complete(
            role=AgentRole.PLANNER,
            messages=(
                {"role": "system", "content": "Seguro."},
                {"role": "user", "content": "Ejecuta."},
            ),
            extra_body={"tools": []},
        )


@pytest.mark.asyncio
async def test_apple_helper_receives_bounded_generation_options(tmp_path: Path) -> None:
    helper = tmp_path / "jarvis-local-brain"
    helper.write_text(
        """#!/usr/bin/python3
import json
import sys
request = json.load(sys.stdin)
assert request["maximumResponseTokens"] == 192
assert request["temperature"] == 0.45
print(json.dumps({"type": "completed", "content": "Respuesta breve"}), flush=True)
""",
        encoding="utf-8",
    )
    helper.chmod(0o700)
    client = AppleLocalModelClient(helper)

    result = await client.complete_stream(
        role=AgentRole.PLANNER,
        messages=(
            {"role": "system", "content": "Sé breve."},
            {"role": "user", "content": "Responde."},
        ),
        max_tokens=192,
        temperature=0.45,
        on_delta=None,
    )

    assert result.content == "Respuesta breve"


@pytest.mark.asyncio
@pytest.mark.parametrize("max_tokens", [0, 4_097, True])
async def test_apple_helper_rejects_invalid_token_limits(
    tmp_path: Path,
    max_tokens: object,
) -> None:
    client = AppleLocalModelClient(_helper(tmp_path))

    with pytest.raises(AppleLocalModelError, match="token limit"):
        await client.complete_stream(
            role=AgentRole.PLANNER,
            messages=(
                {"role": "system", "content": "Sé breve."},
                {"role": "user", "content": "Responde."},
            ),
            max_tokens=max_tokens,  # type: ignore[arg-type]
            on_delta=None,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("temperature", [-0.1, 2.1, float("nan"), True])
async def test_apple_helper_rejects_invalid_temperatures(
    tmp_path: Path,
    temperature: object,
) -> None:
    client = AppleLocalModelClient(_helper(tmp_path))

    with pytest.raises(AppleLocalModelError, match="temperature"):
        await client.complete_stream(
            role=AgentRole.PLANNER,
            messages=(
                {"role": "system", "content": "Sé breve."},
                {"role": "user", "content": "Responde."},
            ),
            temperature=temperature,  # type: ignore[arg-type]
            on_delta=None,
        )


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
