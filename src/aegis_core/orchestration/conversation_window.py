"""Bounded, contiguous dialogue: never join unrelated old answers to recent questions."""

from __future__ import annotations

import json
from collections.abc import Sequence

from aegis_core.memory.contracts import ConversationTurn

_OMITTED = "\n[Fragmento omitido por límite de contexto]\n"


def conversation_window(
    turns: Sequence[ConversationTurn], *, max_bytes: int
) -> list[dict[str, str]]:
    """Keep the latest exchanges, explicitly abbreviating oversized individual turns.

    Each message can consume at most half the budget, leaving room for its partner.
    Prefix and suffix preserve both the objective and a closing question/correction.
    We stop at the first boundary instead of skipping back to an unrelated old turn.
    The budget measures the actual JSON/UTF-8 bytes, including escaped code/control chars.
    """
    if max_bytes < 256:
        return []
    context: list[dict[str, str]] = []
    used = 2
    per_turn = (max_bytes - 4) // 2
    for turn in reversed(turns):
        item = {"role": turn.role.value, "content": turn.content}
        item = _fit(item, per_turn)
        size = _size(item) + bool(context)
        if used + size > max_bytes:
            break
        context.append(item)
        used += size
    context.reverse()
    # A suffix starting with an answer without its question is misleading.
    if 1 < len(context) < len(turns) and context[0]["role"] == "assistant":
        context.pop(0)
    return context


def _size(value: dict[str, str]) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _fit(item: dict[str, str], budget: int) -> dict[str, str]:
    if _size(item) <= budget:
        return item
    text = item["content"]
    low, high = 0, len(text)
    while low < high:
        keep = (low + high + 1) // 2
        candidate = {**item, "content": _abbreviate(text, keep)}
        if _size(candidate) <= budget:
            low = keep
        else:
            high = keep - 1
    return {**item, "content": _abbreviate(text, low)}


def _abbreviate(text: str, keep: int) -> str:
    prefix = (keep + 1) // 2
    suffix = keep // 2
    return text[:prefix] + _OMITTED + (text[-suffix:] if suffix else "")
