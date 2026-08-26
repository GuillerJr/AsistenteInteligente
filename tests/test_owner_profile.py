from pathlib import Path

import pytest

from aegis_core.contracts import InputModality, UserRequest
from aegis_core.memory.profile import (
    OwnerProfile,
    OwnerProfileAction,
    extract_owner_profile_facts,
)
from aegis_core.memory.sqlite import SQLiteMemoryStore


def _profile(tmp_path: Path) -> OwnerProfile:
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3")
    store.initialize()
    return OwnerProfile(store, namespace="user.default")


def test_explicit_owner_facts_are_small_and_deterministic() -> None:
    liked = extract_owner_profile_facts("Me gusta el jazz.")
    disliked = extract_owner_profile_facts("No me gusta el jazz.")
    question = extract_owner_profile_facts("¿Te dije que me gusta el jazz?")

    assert liked[0].content == "Al propietario le gusta o prefiere el jazz."
    assert disliked[0].content == "Al propietario no le gusta el jazz."
    assert liked[0].source == disliked[0].source
    assert question == ()


def test_owner_voice_verification_is_fail_closed_and_never_accepts_text() -> None:
    modalities = frozenset({InputModality.TEXT, InputModality.AUDIO})
    recognized = UserRequest(
        text="Hola",
        modalities=modalities,
        metadata={
            "speaker_identity": {"id": "guillermo", "confidence": 0.91},
            "sole_speaker_profile": True,
        },
    )
    low_confidence = recognized.model_copy(
        update={
            "metadata": {
                "speaker_identity": {"id": "guillermo", "confidence": 0.77},
                "sole_speaker_profile": True,
            }
        }
    )

    assert OwnerProfile.is_verified_owner_voice(recognized) is True
    assert OwnerProfile.is_verified_owner_voice(low_confidence) is False
    assert OwnerProfile.is_verified_owner_voice(UserRequest(text="Hola")) is False


def test_multiple_explicit_facts_do_not_bleed_into_each_other() -> None:
    facts = extract_owner_profile_facts(
        "Mi nombre es Guillermo y me gusta el jazz y trabajo como desarrollador"
    )

    assert [fact.content for fact in facts] == [
        "El propietario se llama Guillermo.",
        "Al propietario le gusta o prefiere el jazz.",
        "El propietario trabaja en o como desarrollador.",
    ]


@pytest.mark.asyncio
async def test_profile_learns_replaces_and_resets_local_preferences(tmp_path: Path) -> None:
    profile = _profile(tmp_path)

    learned = await profile.observe(UserRequest(text="Me gusta el jazz."))
    replaced = await profile.observe(UserRequest(text="No me gusta el jazz."))
    hits = await profile.recall()

    assert learned is OwnerProfileAction.UPSERTED
    assert replaced is OwnerProfileAction.UPSERTED
    assert [hit.excerpt for hit in hits] == ["Al propietario no le gusta el jazz."]

    reset = await profile.observe(UserRequest(text="Olvida todo lo que sabes de mí"))

    assert reset is OwnerProfileAction.RESET
    assert await profile.recall() == ()


@pytest.mark.asyncio
async def test_voice_learning_requires_a_locally_recognized_speaker(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    modalities = frozenset({InputModality.TEXT, InputModality.AUDIO})

    rejected = await profile.observe(
        UserRequest(text="Me encanta el café.", modalities=modalities)
    )
    accepted = await profile.observe(
        UserRequest(
            text="Me encanta el café.",
            modalities=modalities,
            metadata={
                "speaker_identity": {"id": "guillermo", "confidence": 0.91},
                "sole_speaker_profile": True,
            },
        )
    )

    assert rejected is OwnerProfileAction.INELIGIBLE
    assert accepted is OwnerProfileAction.UPSERTED
    assert [hit.excerpt for hit in await profile.recall()] == [
        "Al propietario le gusta o prefiere el café."
    ]


@pytest.mark.asyncio
async def test_specific_forget_removes_only_the_matching_slot(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    await profile.observe(UserRequest(text="Me gusta el jazz."))
    await profile.observe(UserRequest(text="Me interesa la astronomía."))

    result = await profile.observe(UserRequest(text="Olvida que me gusta el jazz"))

    assert result is OwnerProfileAction.FORGOTTEN
    assert [hit.excerpt for hit in await profile.recall()] == [
        "Al propietario le interesa la astronomía."
    ]
