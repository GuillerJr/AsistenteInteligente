import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from aegis_core.memory.contracts import ConversationRole, ConversationTurn
from aegis_core.orchestration.conversation_window import conversation_window


def turns(*contents: str) -> tuple[ConversationTurn, ...]:
    conversation = uuid4()
    return tuple(
        ConversationTurn(
            conversation_id=conversation,
            sequence=i + 1,
            role=ConversationRole.USER if i % 2 == 0 else ConversationRole.ASSISTANT,
            content=content,
            created_at=datetime.now(UTC),
            content_sha256=ConversationTurn.digest_content(content),
        )
        for i, content in enumerate(contents)
    )


@pytest.mark.parametrize("budget", [512, 1_536, 4_096])
@pytest.mark.parametrize("character", ["x", "á", "😀", '"', "\n"])
def test_large_recent_turn_never_resurrects_an_old_answer(budget: int, character: str) -> None:
    history = turns(
        "OLD business",
        "OLD answer",
        "Ahora una veterinaria",
        "Inicio" + character * (12_000 // len(character.encode())) + "¿Web o móvil?",
    )
    context = conversation_window(history, max_bytes=budget)
    assert context[-2] == {"role": "user", "content": "Ahora una veterinaria"}
    assert context[-1]["content"].startswith("Inicio")
    assert context[-1]["content"].endswith("¿Web o móvil?")
    assert "Fragmento omitido" in context[-1]["content"]
    assert len(json.dumps(context, ensure_ascii=False, separators=(",", ":")).encode()) <= budget


def test_small_history_is_exact_and_reset_is_empty() -> None:
    context = conversation_window(turns("citas", "¿Dónde?", "web", "De acuerdo"), max_bytes=512)
    assert [turn["content"] for turn in context] == ["citas", "¿Dónde?", "web", "De acuerdo"]
    assert conversation_window((), max_bytes=512) == []


def test_large_exchange_preserves_both_roles() -> None:
    context = conversation_window(turns("objetivo" * 1_000, "respuesta" * 1_000), max_bytes=512)
    assert [turn["role"] for turn in context] == ["user", "assistant"]
    assert len(json.dumps(context, ensure_ascii=False, separators=(",", ":")).encode()) <= 512
