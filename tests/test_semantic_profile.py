import json
from pathlib import Path

import pytest

from aegis_core.contracts import InputModality, UserRequest
from aegis_core.memory.profile import OwnerProfile, OwnerProfileAction
from aegis_core.memory.semantic_profile import (
    KnownFact,
    MemoryAction,
    MemoryCategory,
    interpret_owner_memory,
)
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.style import owner_style_instruction


def _replying(*payloads):
    calls = []
    queue = list(payloads)

    async def complete(messages):
        calls.append(json.loads(messages[1]["content"]))
        payload = queue.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return payload if isinstance(payload, str) else json.dumps(payload)

    complete.calls = calls
    return complete


def _profile(tmp_path: Path, complete) -> OwnerProfile:
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3", encryption_secret=b"m" * 32)
    store.initialize()
    return OwnerProfile(store, namespace="user.default", interpreter=complete)


def _remember(key, fact, category="preference", **extra):
    return {"action": "remember", "category": category, "key": key, "fact": fact, **extra}


@pytest.mark.asyncio
async def test_free_phrasing_is_learned_without_fixed_patterns(tmp_path):
    complete = _replying({"operations": [
        _remember("musica", "Al propietario le encanta el jazz."),
    ]})
    profile = _profile(tmp_path, complete)

    action = await profile.observe(UserRequest(text="Soy fanático del jazz desde niño"))
    await profile.drain()

    assert action is OwnerProfileAction.SCHEDULED
    assert [hit.excerpt for hit in await profile.recall()] == [
        "Al propietario le encanta el jazz."
    ]


@pytest.mark.asyncio
async def test_correction_replaces_the_known_fact_by_local_identifier(tmp_path):
    complete = _replying(
        {"operations": [_remember("trabajo", "El propietario trabaja en Quito.", "work")]},
        {"operations": [_remember(
            "trabajo", "El propietario trabaja en Guayaquil.", "work", replaces="work:trabajo",
        )]},
    )
    profile = _profile(tmp_path, complete)

    await profile.observe(UserRequest(text="Trabajo en Quito"))
    await profile.drain()
    await profile.observe(UserRequest(text="Ya no estoy en Quito, me mudé a Guayaquil"))
    await profile.drain()

    assert complete.calls[1]["known_facts"] == [
        {"id": "work:trabajo", "fact": "El propietario trabaja en Quito."}
    ]
    assert [hit.excerpt for hit in await profile.recall()] == [
        "El propietario trabaja en Guayaquil."
    ]


@pytest.mark.asyncio
async def test_forget_one_and_forget_all(tmp_path):
    complete = _replying(
        {"operations": [_remember("jazz", "Le gusta el jazz.")]},
        {"operations": [_remember("astro", "Le interesa la astronomía.", "interest")]},
        {"operations": [{"action": "forget", "id": "preference:jazz"}]},
        {"operations": [{"action": "forget_all"}]},
    )
    profile = _profile(tmp_path, complete)
    for text in ("Me gusta el jazz", "Me interesa la astronomía"):
        await profile.observe(UserRequest(text=text))
        await profile.drain()

    await profile.observe(UserRequest(text="ya no me acuerdes lo del jazz"))
    await profile.drain()
    assert [hit.excerpt for hit in await profile.recall()] == ["Le interesa la astronomía."]

    await profile.observe(UserRequest(text="borra todo lo que sabes de mí"))
    await profile.drain()
    assert await profile.recall() == ()


@pytest.mark.asyncio
async def test_style_preference_feeds_the_style_instruction(tmp_path):
    complete = _replying({"operations": [_remember(
        "brevedad", "El propietario prefiere respuestas breves.", "communication",
        style="style.verbosity.concise",
    )]})
    profile = _profile(tmp_path, complete)

    await profile.observe(UserRequest(text="no me sueltes testamentos, ve corto"))
    await profile.drain()

    assert "concise" in owner_style_instruction(await profile.recall())


@pytest.mark.asyncio
async def test_unusable_router_output_falls_back_to_local_rules(tmp_path):
    profile = _profile(tmp_path, _replying("no es json"))

    await profile.observe(UserRequest(text="Me gusta el jazz."))
    await profile.drain()

    assert [hit.excerpt for hit in await profile.recall()] == [
        "Al propietario le gusta o prefiere el jazz."
    ]


@pytest.mark.asyncio
async def test_unverified_voice_is_never_sent_to_the_router(tmp_path):
    complete = _replying()
    profile = _profile(tmp_path, complete)

    action = await profile.observe(UserRequest(
        text="Me encanta el café.",
        modalities=frozenset({InputModality.TEXT, InputModality.AUDIO}),
    ))

    assert action is OwnerProfileAction.INELIGIBLE
    assert complete.calls == []


@pytest.mark.parametrize("operations", [
    [{"action": "forget", "id": "f9"}],
    [_remember("Bad Key", "Hecho válido.")],
    [_remember("ok", "x")],
    [_remember("ok", "Hecho válido.", category="secretos")],
    [_remember("ok", "Hecho válido.", style="style.anything")],
    [{"action": "forget_all"}, _remember("ok", "Hecho válido.")],
    [_remember(f"k{index}", "Hecho válido.") for index in range(5)],
    [{"action": "delete_database"}],
])
@pytest.mark.asyncio
async def test_invalid_operations_reject_the_whole_answer(operations):
    result = await interpret_owner_memory(
        "texto", [KnownFact("owner-profile:work:x", "Trabaja en Quito.")],
        _replying({"operations": operations}),
    )
    assert result is None


@pytest.mark.asyncio
async def test_nothing_to_learn_is_empty_not_failure():
    result = await interpret_owner_memory("revisa mis correos", [], _replying({"operations": []}))
    assert result == ()


@pytest.mark.asyncio
async def test_valid_operation_is_normalized():
    result = await interpret_owner_memory("texto", [], _replying({"operations": [
        _remember("cafe", "  Le gusta   el café. ", temporary=True),
    ]}))
    assert result is not None and len(result) == 1
    operation = result[0]
    assert operation.action is MemoryAction.REMEMBER
    assert operation.category is MemoryCategory.PREFERENCE
    assert operation.fact == "Le gusta el café."
    assert operation.temporary is True


@pytest.mark.asyncio
async def test_learning_turn_never_deletes_unrelated_facts():
    known = [KnownFact("owner-profile:preference:musica", "Le gusta el jazz."),
             KnownFact("owner-profile:work:trabajo", "Trabaja en Quito.")]
    result = await interpret_owner_memory("me llamo Guillermo", known, _replying({"operations": [
        _remember("nombre", "El propietario se llama Guillermo.", "identity"),
        {"action": "forget", "id": "preference:musica"},
        {"action": "forget", "id": "work:trabajo"},
    ]}))
    assert [operation.action for operation in result] == [MemoryAction.REMEMBER]


@pytest.mark.asyncio
async def test_replacement_is_limited_to_the_same_topic_and_known_ids():
    known = [KnownFact("owner-profile:preference:musica", "Le gusta el rock.")]
    cross_topic = await interpret_owner_memory("soy contador", known, _replying({"operations": [
        _remember("trabajo", "El propietario es contador.", "work", replaces="preference:musica"),
    ]}))
    unknown = await interpret_owner_memory("soy contador", known, _replying({"operations": [
        _remember("trabajo", "El propietario es contador.", "work", replaces="work:trabajo"),
    ]}))
    assert cross_topic[0].target_source is None
    assert unknown[0].target_source is None and unknown[0].fact == "El propietario es contador."


@pytest.mark.asyncio
async def test_style_outside_communication_is_dropped():
    result = await interpret_owner_memory("me encanta el rock", [], _replying({"operations": [
        _remember("musica", "Le encanta el rock.", style="style.verbosity.concise"),
    ]}))
    assert result[0].style is None


@pytest.mark.asyncio
async def test_restating_a_known_fact_is_not_a_change():
    known = [KnownFact("owner-profile:preference:musica", "Le gusta el jazz.")]
    result = await interpret_owner_memory("me gusta el jazz", known, _replying({"operations": [
        _remember("jazz", "Le gusta el jazz."),
    ]}))
    assert result == ()
