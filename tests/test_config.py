from __future__ import annotations

import pytest
from pydantic import ValidationError

from aegis_core.config import Settings


def test_default_memory_sliding_window_is_bounded() -> None:
    settings = Settings()

    assert settings.memory_namespace_max_entries == 2_000
    assert settings.memory_namespace_max_entries <= settings.memory_max_entries


def test_namespace_memory_limit_cannot_exceed_global_capacity() -> None:
    with pytest.raises(ValidationError, match="namespace limit"):
        Settings(memory_max_entries=100, memory_namespace_max_entries=101)


def test_mlx_draft_configuration_is_atomic() -> None:
    with pytest.raises(ValidationError, match="configured together"):
        Settings(mlx_draft_model_id="mlx-community/Qwen2.5-0.5B-Instruct-4bit")
    with pytest.raises(ValidationError, match="configured together"):
        Settings(mlx_draft_model_bytes=400_000_000)

    settings = Settings(
        mlx_draft_model_id="mlx-community/Qwen2.5-0.5B-Instruct-4bit",
        mlx_draft_model_bytes=400_000_000,
    )
    assert settings.mlx_draft_model_bytes == 400_000_000
