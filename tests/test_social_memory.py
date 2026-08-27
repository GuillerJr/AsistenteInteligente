from pathlib import Path

import pytest

from aegis_core.contracts import InputModality, UserRequest
from aegis_core.memory.social import (
    OWNER_COMMITMENT_TAG,
    SOCIAL_TOPIC_TAG,
    SocialMemory,
    SocialMemoryAction,
    extract_social_facts,
)
from aegis_core.memory.sqlite import SQLiteMemoryStore


def _memory(tmp_path: Path) -> SocialMemory:
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3")
    store.initialize()
    return SocialMemory(store, namespace="user.default")


def test_extracts_only_explicit_topics_and_commitments() -> None:
    facts = extract_social_facts(
        "Estoy trabajando en Jarvis. Recuerda que debo terminar la prueba de voz."
    )

    assert [(fact.category, fact.value) for fact in facts] == [
        ("topic", "Jarvis"),
        ("commitment", "terminar la prueba de voz"),
    ]
    assert extract_social_facts("¿Estoy trabajando en Jarvis?") == ()


@pytest.mark.asyncio
async def test_social_memory_recall_and_explicit_resolution(tmp_path: Path) -> None:
    memory = _memory(tmp_path)

    action = await memory.observe(
        UserRequest(
            text="Estoy trabajando en Jarvis. Recuerda que debo terminar la prueba de voz."
        )
    )
    recalled = await memory.recall()

    assert action is SocialMemoryAction.UPSERTED
    assert {hit.tags[0] for hit in recalled} == {SOCIAL_TOPIC_TAG, OWNER_COMMITMENT_TAG}
    assert any("Jarvis" in hit.excerpt for hit in recalled)

    resolved = await memory.observe(
        UserRequest(text="Marca como resuelto terminar la prueba de voz")
    )
    remaining = await memory.recall()

    assert resolved is SocialMemoryAction.RESOLVED
    assert [hit.tags[0] for hit in remaining] == [SOCIAL_TOPIC_TAG]


@pytest.mark.asyncio
async def test_social_memory_rejects_unverified_voice_and_supports_reset(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    unverified = UserRequest(
        text="Estoy trabajando en un proyecto secreto.",
        modalities=frozenset({InputModality.TEXT, InputModality.AUDIO}),
        metadata={"sole_speaker_profile": False},
    )

    assert await memory.observe(unverified) is SocialMemoryAction.INELIGIBLE
    assert await memory.recall() == ()

    await memory.observe(UserRequest(text="Mi proyecto actual es Jarvis."))
    assert await memory.observe(
        UserRequest(text="Olvida mis temas y compromisos")
    ) is SocialMemoryAction.RESET
    assert await memory.recall() == ()
