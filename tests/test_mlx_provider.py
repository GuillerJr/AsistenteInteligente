from __future__ import annotations

from pathlib import Path

import pytest

from aegis_core.contracts import AgentRole
from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.providers.mlx_provider import (
    MLXProvider,
    MLXProviderError,
    MLXVerificationIpcService,
    MLXWhisperTranscriber,
)


@pytest.fixture
def helper(tmp_path: Path) -> Path:
    source = Path(__file__).parent / "fixtures/mlx_engine.py"
    target = tmp_path / "jarvis-mlx-engine"
    target.write_bytes(source.read_bytes())
    target.chmod(0o700)
    return target


@pytest.mark.asyncio
async def test_provider_reuses_only_matching_conversation_prefix(helper: Path) -> None:
    provider = MLXProvider(helper)
    first = await provider.complete(
        role=AgentRole.PLANNER,
        messages=(
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "hello"},
        ),
    )
    second = await provider.complete(
        role=AgentRole.PLANNER,
        messages=(
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": first.content},
            {"role": "user", "content": "continue"},
        ),
    )
    await provider.aclose()

    assert first.content == "ok:user: hello"
    assert second.content == "ok:user: continue"


@pytest.mark.asyncio
async def test_provider_relays_single_pass_draft_verification(helper: Path) -> None:
    provider = MLXProvider(helper)
    result = await provider.verify_draft(prompt="hello", draft_token_ids=(1, 2, 3))
    await provider.aclose()

    assert result.accepted_token_count == 2
    assert result.correction_token_id == 42
    assert result.verified_token_count == 3


@pytest.mark.asyncio
async def test_provider_rejects_unbounded_or_tool_payloads(helper: Path) -> None:
    provider = MLXProvider(helper)
    with pytest.raises(MLXProviderError):
        await provider.complete(
            role=AgentRole.PLANNER,
            messages=(
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": "hello"},
            ),
            extra_body={"tools": []},
        )
    with pytest.raises(MLXProviderError):
        await provider.verify_draft(prompt="hello", draft_token_ids=tuple(range(65)))
    await provider.aclose()


@pytest.mark.asyncio
async def test_signed_daemon_service_exposes_bounded_verification(helper: Path) -> None:
    provider = MLXProvider(helper)
    service = MLXVerificationIpcService(provider)
    request = IpcAuthenticator(b"x" * 32).create_request(
        service.METHOD,
        {
            "prompt": "hello",
            "draft_token_ids": [1, 2, 3],
            "system_instructions": "Be concise.",
        },
    )

    response = await service.handle(request)
    await provider.aclose()

    assert response.ok
    assert response.payload == {
        "accepted_token_count": 2,
        "correction_token_id": 42,
        "verified_token_count": 3,
    }


def test_whisper_model_validation_requires_private_four_bit_assets(tmp_path: Path) -> None:
    model = tmp_path / "whisper-tiny-mlx-4bit"
    model.mkdir(mode=0o700)
    (model / "config.json").write_text(
        '{"model_type":"whisper","n_mels":80,'
        '"quantization":{"bits":4,"group_size":64}}',
        encoding="utf-8",
    )
    weights = model / "weights.npz"
    weights.write_bytes(b"0" * 1_048_576)
    model.chmod(0o700)
    weights.chmod(0o600)
    (model / "config.json").chmod(0o600)

    MLXWhisperTranscriber(model)._validate_private_model()

    weights.chmod(0o620)
    with pytest.raises(MLXProviderError, match="unsafe"):
        MLXWhisperTranscriber(model)._validate_private_model()
