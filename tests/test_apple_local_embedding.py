from __future__ import annotations

from pathlib import Path

import pytest

from aegis_core.providers.apple_embedding import AppleLocalEmbeddingClient
from aegis_core.providers.base import EmbeddingInputType, EmbeddingProviderError


def _helper(tmp_path: Path) -> Path:
    path = tmp_path / "jarvis-local-embedding"
    path.write_text(
        """#!/usr/bin/python3
import json
import sys

if sys.argv[1:] == ["--status"]:
    print(json.dumps({
        "available": True,
        "dimensions": 3,
        "model_id": "apple/natural-language-sentence-es-v1"
    }))
    raise SystemExit(0)
request = json.load(sys.stdin)
assert request["input_type"] in {"passage", "query"}
print(json.dumps({
    "model_id": "apple/natural-language-sentence-es-v1",
    "vectors": [[3.0, 4.0, 0.0] for _ in request["texts"]]
}))
""",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


@pytest.mark.asyncio
async def test_private_apple_embedding_helper_is_bounded_and_normalized(
    tmp_path: Path,
) -> None:
    client = AppleLocalEmbeddingClient(_helper(tmp_path))

    batch = await client.embed(
        ["Prefiere español.", "Usa respuestas breves."],
        input_type=EmbeddingInputType.PASSAGE,
    )

    assert client.is_available() is True
    assert batch.model_id == "apple/natural-language-sentence-es-v1"
    assert batch.vectors[0] == pytest.approx((0.6, 0.8, 0.0))
    assert batch.vectors[1] == pytest.approx((0.6, 0.8, 0.0))


@pytest.mark.asyncio
async def test_apple_embedding_helper_fails_closed_for_unsafe_permissions(
    tmp_path: Path,
) -> None:
    helper = _helper(tmp_path)
    helper.chmod(0o722)
    client = AppleLocalEmbeddingClient(helper)

    assert client.is_available() is False
    with pytest.raises(EmbeddingProviderError, match="unavailable"):
        await client.embed(["dato"], input_type=EmbeddingInputType.QUERY)


@pytest.mark.asyncio
async def test_apple_embedding_helper_rejects_empty_or_unbounded_batches(
    tmp_path: Path,
) -> None:
    client = AppleLocalEmbeddingClient(_helper(tmp_path))

    with pytest.raises(ValueError, match="batch size"):
        await client.embed([], input_type=EmbeddingInputType.QUERY)
    with pytest.raises(ValueError, match="input"):
        await client.embed([" "], input_type=EmbeddingInputType.QUERY)
