"""Real private SQLite/citation contracts with an explicit geometric encoder.

These unit vectors are synthetic geometry, not an imitation of MiniLM and not
semantic-quality evidence. A separate real-encoder corpus must qualify that.
"""

from __future__ import annotations

import asyncio
import math
import sqlite3
import time
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from jarvis_core.memory import MemoryScope, MemoryStore
from jarvis_core.memory_index import MemoryIndex, MemoryIndexError
from jarvis_core.memory_snapshot import snapshot_scope


class GeometryEncoder:
    model_fingerprint = "a" * 64
    dimension = 384

    def __init__(self):
        self.queries = 0
        self.documents = 0

    @staticmethod
    def vector(text):
        axis = (
            0
            if "ALFA" in text or "consulta-a" in text
            else 1
            if "BETA" in text or "consulta-b" in text
            else 2
        )
        return tuple(1.0 if i == axis else 0.0 for i in range(384))

    async def embed_query(self, text, **options):
        self.queries += 1
        return self.vector(text)

    async def embed_document(self, text, **options):
        self.documents += 1
        return (
            SimpleNamespace(
                start_byte=0,
                end_byte=len(text.encode()),
                token_count=10,
                vector=self.vector(text),
            ),
        )


def add(store, scope, key, text):
    return store.upsert(
        scope,
        key,
        text,
        source_uri="jarvis://synthetic/" + key,
        title="Título privado " + key,
    )


@pytest.fixture
def corpus(tmp_path):
    store = MemoryStore(tmp_path.resolve() / "source/memory.sqlite3")
    scope = MemoryScope("jarvis", "workspace")
    add(store, scope, "alfa", "ALFA café y acción 🎙 azul")
    add(store, scope, "beta", "BETA campana y montaña")
    yield store, scope, tmp_path / "cache"
    store.close()


async def test_actual_sqlite_and_citations_use_source_bytes_without_plaintext_cache(
    corpus,
):
    store, scope, cache = corpus
    snapshot = snapshot_scope(store, scope)
    encoder = GeometryEncoder()
    index = MemoryIndex(cache, "f" * 64)
    try:
        receipt = await index.build(snapshot, encoder)
        assert receipt["documents"] == 2 and receipt["chunks"] == 2
        result = await index.search(snapshot, "consulta-a", encoder, mode="semantic_local", limit=1)
        assert result.mode == "semantic_local" and result.hits[0].document.key == "alfa"
        hit = result.hits[0]
        assert store.resolve_citation(scope, hit.citation) == hit.excerpt
        assert not hit.document.source_verified
        data = next(cache.glob("*.sqlite3")).read_bytes()
        for private in (
            "ALFA café",
            "Título privado alfa",
            "jarvis://synthetic/alfa",
            "consulta-a",
        ):
            assert private.encode() not in data
        assert index.status(scope)["freshness"] == "requires_current_snapshot_check"
    finally:
        assert (await index.close())["cleanup_confirmed"]


async def test_hybrid_lexical_channel_is_or_and_preserves_exact_byte_citations(corpus):
    store, scope, cache = corpus
    index = MemoryIndex(cache, "f" * 64)
    snapshot = snapshot_scope(store, scope)
    encoder = GeometryEncoder()
    try:
        await index.build(snapshot, encoder)
        result = await index.search(
            snapshot, "azul campana", encoder, mode="hybrid_local", limit=10
        )
        assert {hit.document.key for hit in result.hits} == {"alfa", "beta"}
        assert all(math.isfinite(hit.score) for hit in result.hits)
        assert all(
            store.resolve_citation(scope, hit.citation) == hit.excerpt for hit in result.hits
        )
    finally:
        await index.close()


async def test_model_generation_scope_and_aba_are_rejected_before_query_inference(
    corpus,
):
    store, scope, cache = corpus
    encoder = GeometryEncoder()
    index = MemoryIndex(cache, "f" * 64)
    original = snapshot_scope(store, scope)
    try:
        await index.build(original, encoder)
        changed_model = GeometryEncoder()
        changed_model.model_fingerprint = "b" * 64
        with pytest.raises(MemoryIndexError, match="memory_index_model_changed"):
            await index.search(original, "consulta-a", changed_model, mode="semantic_local")
        assert changed_model.queries == 0
        record = store.get(scope, "alfa").document
        store.delete(scope, "alfa", expected_revision=record.revision)
        replacement = add(store, scope, "alfa", "ALFA reemplazo con otra identidad")
        assert replacement.revision == 1 and replacement.document_id != record.document_id
        # Even a caller accidentally reusing an old generation/fingerprint cannot
        # pass the independent current-document manifest check.
        current = replace(
            snapshot_scope(store, scope),
            generation=original.generation,
            fingerprint=original.fingerprint,
        )
        with pytest.raises(MemoryIndexError, match="memory_index_stale"):
            await index.search(current, "consulta-a", encoder, mode="semantic_local")
        assert encoder.queries == 0
        other = replace(original, scope=MemoryScope("jarvis", "other"))
        with pytest.raises(MemoryIndexError, match="memory_index_missing"):
            await index.search(other, "consulta-a", encoder, mode="semantic_local")
    finally:
        await index.close()


@pytest.mark.parametrize("fault", ["nan", "zero", "dimension", "gap", "utf8", "oversized_span"])
async def test_invalid_embedding_never_replaces_published_index(corpus, fault):
    store, scope, cache = corpus
    encoder = GeometryEncoder()
    index = MemoryIndex(cache, "f" * 64)
    snapshot = snapshot_scope(store, scope)
    try:
        await index.build(snapshot, encoder)
        path = next(cache.glob("*.sqlite3"))
        original = path.read_bytes()

        class Invalid(GeometryEncoder):
            model_fingerprint = "b" * 64

            async def embed_document(self, text, **options):
                span = (await super().embed_document(text, **options))[0]
                if fault == "nan":
                    span.vector = (float("nan"), *span.vector[1:])
                if fault == "zero":
                    span.vector = (0.0,) * 384
                if fault == "dimension":
                    span.vector = (1.0,)
                if fault == "gap":
                    span.start_byte = 1
                if fault == "utf8":
                    span.end_byte = text.encode().index("🎙".encode()) + 1 if "🎙" in text else 1
                if fault == "oversized_span":
                    span.end_byte = 769
                return (span,)

        with pytest.raises(MemoryIndexError):
            await index.build(snapshot, Invalid())
        assert path.read_bytes() == original
        assert not list(cache.glob(".build-*"))
        assert store.get(scope, "alfa").text == "ALFA café y acción 🎙 azul"
    finally:
        await index.close()


async def test_revocation_after_encoder_await_preserves_published_generation(corpus):
    store, scope, cache = corpus
    index = MemoryIndex(cache, "f" * 64)
    snapshot = snapshot_scope(store, scope)
    allowed = True

    class Revoked(Exception):
        pass

    def current():
        if not allowed:
            raise Revoked

    class RevokeAfterEncode(GeometryEncoder):
        model_fingerprint = "b" * 64

        async def embed_document(self, text, **options):
            nonlocal allowed
            result = await super().embed_document(text, **options)
            allowed = False
            return result

    try:
        await index.build(snapshot, GeometryEncoder())
        path = next(cache.glob("*.sqlite3"))
        before = path.read_bytes()
        with pytest.raises(Revoked):
            await index.build(snapshot, RevokeAfterEncode(), assert_current=current)
        assert path.read_bytes() == before
        assert not list(cache.glob(".build-*"))
    finally:
        await index.close()


async def test_temporal_index_never_creates_or_opens_durable_cache(tmp_path):
    source = tmp_path.resolve() / "never-created/memory.sqlite3"
    scope = MemoryScope("jarvis", "workspace", "temporary", True)
    store = MemoryStore(source)
    cache = tmp_path / "never-cache"
    index = MemoryIndex(cache, "f" * 64)
    encoder = GeometryEncoder()
    try:
        add(store, scope, "temporary", "ALFA temporal")
        snapshot = snapshot_scope(store, scope)
        assert index.status(scope)["state"] == "absent"
        await index.build(snapshot, encoder)
        found = await index.search(snapshot, "consulta-a", encoder, mode="semantic_local")
        assert found.hits and not source.parent.exists() and not cache.exists()
        assert index.status(scope)["temporary"]
    finally:
        await index.close()
        store.close()


async def test_capacity_interruption_and_close_leave_original_source_and_index(corpus):
    store, scope, cache = corpus
    encoder = GeometryEncoder()
    index = MemoryIndex(cache, "f" * 64, max_bytes=131072)
    snapshot = snapshot_scope(store, scope)
    try:
        await index.build(snapshot, encoder)
        path = next(cache.glob("*.sqlite3"))
        before = path.read_bytes()
        add(store, scope, "large", "ALFA " + "x" * 600)
        expanded = snapshot_scope(store, scope)

        class Many(GeometryEncoder):
            async def embed_document(self, text, **options):
                data = text.encode()
                if "x" not in text:
                    return await super().embed_document(text, **options)
                return tuple(
                    SimpleNamespace(
                        start_byte=i,
                        end_byte=i + 1,
                        token_count=1,
                        vector=self.vector(text),
                    )
                    for i in range(len(data))
                )

        with pytest.raises(MemoryIndexError, match="memory_index_capacity"):
            await index.build(expanded, Many())
        assert path.read_bytes() == before and store.get(scope, "large").text.endswith("x" * 600)
        assert not list(cache.glob(".build-*"))
        cancelled = asyncio.Event()
        cancelled.set()
        with pytest.raises(MemoryIndexError, match="memory_index_cancelled"):
            await index.build(expanded, encoder, cancellation=cancelled)
        with pytest.raises(MemoryIndexError, match="memory_index_timeout"):
            await index.build(expanded, encoder, deadline=time.monotonic() - 1)
        assert path.read_bytes() == before
    finally:
        await index.close()


async def test_close_joins_encoder_cleanup_and_retains_lane_until_it_finishes(corpus):
    store, scope, cache = corpus
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Held(GeometryEncoder):
        model_fingerprint = "b" * 64

        async def embed_document(self, text, **options):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()

    index = MemoryIndex(cache, "f" * 64)
    build = asyncio.create_task(index.build(snapshot_scope(store, scope), Held()))
    await asyncio.wait_for(started.wait(), 2)
    closing = asyncio.create_task(index.close())
    await asyncio.wait_for(cleaning.wait(), 2)
    await asyncio.sleep(0)
    assert not closing.done()
    release.set()
    assert (await asyncio.wait_for(closing, 2))["cleanup_confirmed"]
    with pytest.raises(asyncio.CancelledError):
        await build
    assert not list(cache.glob(".build-*"))


async def test_rebuild_equivalence_paging_and_corrupt_cache_fail_closed(corpus):
    store, scope, cache = corpus
    for number in range(8):
        add(store, scope, "extra" + str(number), "ALFA número " + str(number))
    encoder = GeometryEncoder()
    index = MemoryIndex(cache, "f" * 64)
    snapshot = snapshot_scope(store, scope)
    try:
        await index.build(snapshot, encoder)
        full = await index.search(snapshot, "consulta-a", encoder, mode="hybrid_local", limit=5)
        first = await index.search(snapshot, "consulta-a", encoder, mode="hybrid_local", limit=2)
        second = await index.search(
            snapshot,
            "consulta-a",
            encoder,
            mode="hybrid_local",
            limit=3,
            offset=first.next_offset,
        )
        assert first.hits + second.hits == full.hits
        await index.build(snapshot, encoder)
        rebuilt = await index.search(snapshot, "consulta-a", encoder, mode="hybrid_local", limit=5)
        assert asdict(rebuilt) == asdict(full)
        path = next(cache.glob("*.sqlite3"))
        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE spans SET vector=zeroblob(length(vector))")
        with pytest.raises(MemoryIndexError):
            await index.search(snapshot, "consulta-a", encoder, mode="semantic_local")
        repaired = await index.build(snapshot, encoder)
        assert repaired["previous_cache_error"] == "memory_index_corrupt"
        assert repaired["encoded_documents"] == len(snapshot.documents)
        after_repair = await index.search(
            snapshot, "consulta-a", encoder, mode="hybrid_local", limit=5
        )
        assert asdict(after_repair) == asdict(full)
    finally:
        await index.close()


async def test_incremental_rebuild_reuses_unchanged_vectors_and_never_revives_deleted_notes(corpus):
    store, scope, cache = corpus
    encoder = GeometryEncoder()
    index = MemoryIndex(cache, "f" * 64)
    try:
        await index.build(snapshot_scope(store, scope), encoder)
        assert encoder.documents == 2
        original = store.get(scope, "alfa").document
        store.upsert(
            scope,
            "alfa",
            "ALFA edición actual",
            source_uri=original.source_uri,
            expected_revision=original.revision,
        )
        updated = await index.build(snapshot_scope(store, scope), encoder)
        assert updated["encoded_documents"] == 1 and updated["reused_documents"] == 1
        assert encoder.documents == 3
        removed = store.get(scope, "beta").document
        store.delete(scope, "beta", expected_revision=removed.revision)
        current = snapshot_scope(store, scope)
        deleted = await index.build(current, encoder)
        assert deleted["encoded_documents"] == 0 and deleted["reused_documents"] == 1
        result = await index.search(current, "consulta-b", encoder, mode="semantic_local")
        assert result.hits and all(
            hit.document.document_id != removed.document_id for hit in result.hits
        )
        assert all(
            store.resolve_citation(scope, hit.citation) == hit.excerpt for hit in result.hits
        )
    finally:
        await index.close()


async def test_hybrid_identifier_prefix_does_not_receive_whole_phrase_bonus(corpus):
    store, scope, cache = corpus
    existing = sorted(
        snapshot_scope(store, scope).documents, key=lambda item: item.document.document_id
    )
    # Deterministic tie ordering: an incorrect identifier has the lower opaque
    # document ID; an accidental substring bonus would wrongly put it first.
    add(store, scope, existing[0].document.key, "BETA marcador TOPACIO48260")
    add(store, scope, existing[1].document.key, "ALFA TOPACIO4826 algo marcador")

    class IdentifierEncoder(GeometryEncoder):
        async def embed_query(self, text, **options):
            return self.vector("ALFA")

    encoder = IdentifierEncoder()
    index = MemoryIndex(cache, "f" * 64)
    try:
        snapshot = snapshot_scope(store, scope)
        await index.build(snapshot, encoder)
        result = await index.search(
            snapshot, "marcador TOPACIO4826", encoder, mode="hybrid_local", limit=2
        )
        assert result.hits[0].document.document_id == existing[1].document.document_id
        assert "TOPACIO48260" not in result.hits[0].excerpt
    finally:
        await index.close()


@pytest.mark.parametrize("mode", ["semantic_local", "hybrid_local"])
@pytest.mark.parametrize("similarity", [-0.2, 0.19, 0.34, 0.36, 0.6])
async def test_automatic_similarity_floor_precedes_rrf_but_manual_candidates_are_preserved(
    corpus, mode, similarity
):
    store, scope, cache = corpus

    class RelevanceEncoder(GeometryEncoder):
        @staticmethod
        def vector(text):
            return (similarity, math.sqrt(1 - similarity**2)) + (0.0,) * 382

        async def embed_query(self, text, **options):
            return (1.0,) + (0.0,) * 383

    encoder = RelevanceEncoder()
    index = MemoryIndex(cache, "f" * 64)
    try:
        snapshot = snapshot_scope(store, scope)
        await index.build(snapshot, encoder)
        manual = await index.search(snapshot, "ALFA", encoder, mode=mode, limit=2)
        automatic = await index.search(
            snapshot, "ALFA", encoder, mode=mode, limit=2, minimum_similarity=0.35
        )
        assert len(manual.hits) == 2
        if mode == "hybrid_local":
            # A rank-only score near one is possible even at negative cosine.
            assert manual.hits[0].score > 0.9
        assert bool(automatic.hits) is (similarity >= 0.35)
        assert automatic.generation == manual.generation
        for hit in automatic.hits:
            assert store.resolve_citation(scope, hit.citation) == hit.excerpt
    finally:
        await index.close()
