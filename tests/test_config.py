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
