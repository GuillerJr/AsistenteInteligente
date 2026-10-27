"""Real selected CPU encoder, SQLite and exact citations over owned Spanish notes.

No synthetic vectors, alternate engines or network inference. This small corpus
qualifies these cases; it is not a general Spanish accuracy benchmark.
"""

import math
import os
import sqlite3
import time
from types import SimpleNamespace

import pytest

from jarvis_core.local_embeddings import LocalEmbeddingClient
from jarvis_core.memory import MemoryError, MemoryScope, MemoryStore
from jarvis_core.memory_index import MemoryIndex, MemoryIndexError
from jarvis_core.memory_retrieval import tokens


@pytest.fixture
async def real_memory(tmp_path):
    assets = os.environ.get("JARVIS_MINILM_TEST_ASSETS")
    if not assets:
        if os.environ.get("JARVIS_MINILM_REQUIRE_REAL") == "1":
            pytest.fail("Pinned MiniLM assets are mandatory for real memory qualification.")
        pytest.skip("Real CPU qualification requires explicitly provisioned pinned assets.")
    store = MemoryStore(tmp_path.resolve() / "source/memory.sqlite3")
    scope = MemoryScope("jarvis", "real_qualification")
    cache = tmp_path.resolve() / "derived"
    value = SimpleNamespace(
        store=store,
        scope=scope,
        cache=cache,
        encoder=LocalEmbeddingClient(assets),
        index=MemoryIndex(cache, "f" * 64),
    )
    try:
        yield value
    finally:
        assert (await value.index.close())["cleanup_confirmed"]
        assert (await value.encoder.close())["cleanup_confirmed"]
        store.close()


def add(world, key, text, *, expected_revision=None):
    return world.store.upsert(
        world.scope,
        key,
        text,
        source_uri="jarvis://synthetic-real/" + key,
        expected_revision=expected_revision,
    )


async def build(world):
    snapshot = world.store.snapshot(world.scope)
    receipt = await world.index.build(snapshot, world.encoder, deadline=time.monotonic() + 60)
    assert receipt["model_fingerprint"] == world.encoder.model_fingerprint
    return snapshot


async def search(world, snapshot, query, *, mode="semantic_local", limit=3):
    result = await world.index.search(
        snapshot, query, world.encoder, mode=mode, limit=limit, deadline=time.monotonic() + 30
    )
    assert result.mode == mode and result.generation == snapshot.generation
    for hit in result.hits:
        assert math.isfinite(hit.score)
        assert world.store.resolve_citation(world.scope, hit.citation) == hit.excerpt
        assert not hit.document.source_verified
    return result


async def test_real_semantic_spanish_paraphrases_with_related_distractors(real_memory):
    world = real_memory
    pairs = (
        (
            "pagos",
            "Los pagos a terceros se realizan mediante una transferencia bancaria.",
            "¿Cómo remito dinero a otra persona?",
        ),
        (
            "cuenta",
            "La contraseña se cambia desde el panel de seguridad de la cuenta.",
            "Necesito renovar mi clave de acceso al perfil.",
        ),
        (
            "hotel",
            "El hotel elegido debe ser silencioso y permite descansar durante la noche.",
            "Busco alojamiento tranquilo para dormir sin ruido.",
        ),
    )
    stop = {"a", "al", "de", "del", "el", "la", "los", "las", "se", "en", "y", "mi", "para"}
    for key, note, query in pairs:
        assert not (set(tokens(note)) - stop) & (set(tokens(query)) - stop)
        add(world, key, note)
    add(world, "parque", "En el jardín, el banco de madera sirve para sentarse a descansar.")
    add(world, "cerrada", "La puerta lateral no abre los domingos.")
    add(world, "abierta", "La entrada principal admite visitas los domingos por la mañana.")
    snapshot = await build(world)
    for key, _, query in pairs:
        result = await search(world, snapshot, query, limit=1)
        assert result.hits and result.hits[0].document.key == key
    # Negation stays verbatim as source data. Embedding similarity alone does not
    # establish which instruction is applicable or replace reasoning over sources.
    schedules = await search(world, snapshot, "visitas del domingo", limit=10)
    excerpts = {hit.document.key: hit.excerpt for hit in schedules.hits}
    assert excerpts["cerrada"] == "La puerta lateral no abre los domingos."
    assert excerpts["abierta"] == "La entrada principal admite visitas los domingos por la mañana."


async def test_real_hybrid_keeps_exact_identifier_distinct_from_prefix(real_memory):
    world = real_memory
    wanted = add(
        world, "exacto", "El marcador TOPACIO4826 identifica el casillero azul de la oficina."
    )
    add(world, "prefijo", "El marcador TOPACIO48260 identifica una caja roja del almacén.")
    add(world, "otro", "Las llaves de la vivienda están guardadas junto al abrigo.")
    snapshot = await build(world)
    result = await search(world, snapshot, "TOPACIO4826", mode="hybrid_local", limit=1)
    assert result.hits and result.hits[0].document.document_id == wanted.document_id
    assert "TOPACIO4826 " in result.hits[0].excerpt
    assert "TOPACIO48260" not in result.hits[0].excerpt


async def test_real_late_utf8_spans_keep_all_bytes_and_retrieve_after_token128(real_memory):
    world = real_memory
    prefix = "Café, música y decoración de muebles para la cocina. " * 45
    tail = "El mapa del sendero alto se guarda en la caja violeta junto a la brújula."
    text = prefix + "🧭 " + tail
    raw = text.encode()
    add(world, "sendero", text)
    add(world, "horario", "La biblioteca abre sus salas de lectura cada tarde.")
    snapshot = await build(world)
    spans = await world.encoder.embed_document(text, deadline=time.monotonic() + 30)
    covered = 0
    for span in spans:
        assert 0 <= span.start_byte <= covered < span.end_byte <= len(raw)
        assert span.end_byte - span.start_byte <= 768 and 1 <= span.token_count <= 128
        raw[span.start_byte : span.end_byte].decode("utf-8")
        covered = span.end_byte
    assert covered == len(raw) and len(spans) > 1
    result = await search(
        world, snapshot, "¿Dónde está el plano para orientarse en la ruta de montaña?", limit=1
    )
    assert result.hits and result.hits[0].document.key == "sendero"
    assert result.hits[0].citation.end_byte > len(prefix.encode())
    assert "mapa" in result.hits[0].excerpt


async def test_real_revision_forget_restart_and_corrupt_cache_rebuild(real_memory):
    world = real_memory
    original = add(world, "pagos", "Los pagos se realizan mediante una transferencia bancaria.")
    add(world, "parque", "El banco de madera del jardín sirve para descansar sentado.")
    first = await build(world)
    old = (await search(world, first, "transferencia bancaria", limit=1)).hits[0].citation
    changed = add(
        world,
        "pagos",
        "La instrucción vigente exige gestionar el envío en ventanilla.",
        expected_revision=original.revision,
    )
    current = world.store.snapshot(world.scope)
    with pytest.raises(MemoryIndexError, match="memory_index_stale"):
        await search(world, current, "ventanilla")
    await build(world)
    revised = await search(world, current, "ventanilla", mode="hybrid_local", limit=1)
    assert revised.hits[0].document.revision == changed.revision
    with pytest.raises(MemoryError, match="stale_citation"):
        world.store.resolve_citation(world.scope, old)
    await world.index.close()
    world.index = MemoryIndex(world.cache, "f" * 64)
    await search(world, current, "ventanilla", mode="hybrid_local")
    files = list(world.cache.glob("*.sqlite3"))
    assert len(files) == 1
    with sqlite3.connect(files[0]) as connection:
        connection.execute(
            "UPDATE spans SET vector=zeroblob(length(vector)) "
            "WHERE span_id=(SELECT min(span_id) FROM spans)"
        )
    with pytest.raises(MemoryIndexError, match="memory_index_corrupt"):
        await search(world, current, "ventanilla")
    await build(world)
    restored = await search(world, current, "ventanilla", mode="hybrid_local", limit=1)
    assert restored.hits[0].document.document_id == changed.document_id
    world.store.delete(world.scope, "pagos", expected_revision=changed.revision)
    final = await build(world)
    result = await search(world, final, "ventanilla", mode="hybrid_local")
    assert all(hit.document.document_id != changed.document_id for hit in result.hits)


async def test_real_auto_recall_can_abstain_despite_high_hybrid_rank_and_keep_relevant_paraphrase(
    real_memory,
):
    world = real_memory
    add(
        world, "hotel", "El hotel elegido debe ser silencioso y permite descansar durante la noche."
    )
    add(world, "cuenta", "La contraseña se cambia desde el panel de seguridad de la cuenta.")
    add(world, "casillero", "El marcador DORADO8631 identifica el casillero azul de la oficina.")
    add(world, "prefijo", "El marcador DORADO86310 identifica una caja roja del almacén.")
    snapshot = await build(world)
    instruction = (
        " Responde brevemente, sin usar herramientas ni guardar datos. "
        "Si no consta indícalo y no inventes."
    )
    for suffix in ("", instruction):
        absent_query = "¿Qué palabra de control para esta prueba tienes guardada?" + suffix
        manual = await search(world, snapshot, absent_query, mode="hybrid_local", limit=4)
        assert manual.hits and max(hit.score for hit in manual.hits) > 0.9
        automatic = await world.index.search(
            snapshot,
            absent_query,
            world.encoder,
            mode="hybrid_local",
            limit=4,
            minimum_similarity=0.35,
            deadline=time.monotonic() + 30,
        )
        assert not automatic.hits and automatic.generation == snapshot.generation
        positive = await world.index.search(
            snapshot,
            "Busco alojamiento tranquilo para dormir sin ruido." + suffix,
            world.encoder,
            mode="hybrid_local",
            limit=4,
            minimum_similarity=0.35,
            deadline=time.monotonic() + 30,
        )
        assert positive.hits and positive.hits[0].document.key == "hotel"
        for hit in positive.hits:
            assert world.store.resolve_citation(world.scope, hit.citation) == hit.excerpt
            assert hit.document.source_verified is False
