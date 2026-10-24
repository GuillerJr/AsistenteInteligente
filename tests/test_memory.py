"""Memory tests use real SQLite and private synthetic files, never owner data."""

import hashlib
import os
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from jarvis_core.memory import (
    Citation,
    ConversationMessage,
    MemoryError,
    MemoryLimits,
    MemoryScope,
    MemoryStore,
    compact_messages,
)


@pytest.fixture
def path(tmp_path):
    return tmp_path.resolve() / "private-memory" / "memory.sqlite3"


@pytest.fixture
def scope():
    return MemoryScope("jarvis", "workspace", "session_1")


@pytest.fixture
def memory(path):
    with MemoryStore(path) as value:
        yield value


def put(memory, scope, key="control", text="La palabra sintética es NARANJA.", **changes):
    return memory.upsert(scope, key, text, source_uri="manual://fixture/" + str(key), **changes)


def fails(code):
    return pytest.raises(MemoryError, match="^" + code + "$")


def test_document_restart_revision_noop_and_byte_provenance(path, scope):
    with MemoryStore(path) as first:
        doc = put(first, scope, title="Control")
        assert doc.revision == 1 and doc.chunk_count == 1
        assert not doc.source_verified and doc.provenance == "provided_text"
        assert put(first, scope, title="Control") == doc
        result = first.search(scope, "naranja")
        citation = result.hits[0].citation
        assert Citation(**asdict(citation)) == citation
        assert result.generation == 1 and result.mode in {"fts5_lexical", "lexical_scan"}
    with MemoryStore(path) as reopened:
        assert reopened.get(scope, "control").document == doc
        assert reopened.resolve_citation(scope, citation) == "La palabra sintética es NARANJA."
        updated = put(reopened, scope, text="La palabra sintética es LIMÓN.", expected_revision=1)
        assert updated.document_id == doc.document_id and updated.revision == 2
        assert updated.created_ns == doc.created_ns and updated.updated_ns >= doc.updated_ns
        assert not reopened.search(scope, "naranja").hits
        assert reopened.search(scope, "limon").hits[0].document == updated
        with fails("stale_citation"):
            reopened.resolve_citation(scope, citation)


def test_scope_identity_is_exact_for_agent_workspace_and_session(memory, scope):
    put(memory, scope)
    alternatives = [
        replace(scope, agent_id="other"), replace(scope, workspace_id="other"),
        replace(scope, session_id="other"), replace(scope, session_id=None),
        replace(scope, temporary=True),
    ]
    citation = memory.search(scope, "naranja").hits[0].citation
    for other in alternatives:
        assert not memory.list_documents(other) and not memory.search(other, "naranja").hits
        with fails("document_not_found"):
            memory.get(other, "control")
        with fails("document_not_found"):
            memory.delete(other, "control")
        with fails("stale_citation"):
            memory.resolve_citation(other, citation)


def test_temporaries_never_open_inspect_or_initialize_disk(path, monkeypatch):
    scope = MemoryScope("jarvis", "workspace", "temp", temporary=True)
    opened = []
    original_connect = sqlite3.connect

    def connect(name, *args, **kwargs):
        opened.append(name)
        assert name == ":memory:"
        return original_connect(name, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("Una memoria temporal intentó consultar el disco")

    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr(os, "open", forbidden)
    monkeypatch.setattr(MemoryStore, "_open_disk", forbidden)
    with MemoryStore(path) as value:
        doc = put(value, scope)
        result = value.search(scope, "naranja")
        assert value.resolve_citation(scope, result.hits[0].citation)
        assert value.list_documents(scope) == (doc,)
        value.delete(scope, "control")
        assert not value.search(scope, "naranja").hits
        with fails("document_not_found"):
            value.get(scope, "missing")
    with MemoryStore(path) as restarted:
        assert not restarted.list_documents(scope)
    assert opened == [":memory:", ":memory:"]
    assert not path.parent.exists()


def test_temporary_does_not_consult_previously_open_durable_store(memory, scope):
    put(memory, scope)
    statements = []
    memory._disk.set_trace_callback(statements.append)
    temp = replace(scope, temporary=True)
    put(memory, temp, text="Sólo RAM")
    assert memory.search(temp, "ram").hits
    assert not memory.search(temp, "naranja").hits
    assert memory.get(temp, "control").text == "Sólo RAM"
    memory.delete(temp, "control")
    assert statements == []


def test_clear_scope_is_atomic_isolated_and_idempotent(memory, scope):
    put(memory, scope, key="one")
    put(memory, scope, key="two")
    other = replace(scope, session_id="other")
    put(memory, other, key="one")
    before = memory.search(scope, "naranja")
    citation = before.hits[0].citation
    assert memory.clear_scope(scope) == 2
    after = memory.search(scope, "naranja")
    assert not after.hits and after.generation == before.generation + 1
    assert memory.clear_scope(scope) == 0
    assert memory.search(scope, "naranja").generation == after.generation
    assert len(memory.search(other, "naranja").hits) == 1
    with fails("stale_citation"):
        memory.resolve_citation(scope, citation)


def test_clear_temporary_scope_never_touches_disk(path, monkeypatch):
    temporary = MemoryScope("jarvis", "workspace", "session", temporary=True)

    def forbidden(*args, **kwargs):
        pytest.fail("El borrado temporal intentó consultar el disco")

    with MemoryStore(path) as memory:
        put(memory, temporary)
        monkeypatch.setattr(os, "open", forbidden)
        monkeypatch.setattr(MemoryStore, "_open_disk", forbidden)
        assert memory.clear_scope(temporary) == 1
        assert memory.clear_scope(temporary) == 0
    assert not path.parent.exists()


def test_clear_scope_rollback_preserves_all_documents_and_citations(memory, scope):
    put(memory, scope, key="one")
    put(memory, scope, key="two")
    before = memory.search(scope, "naranja")
    memory._disk.execute("CREATE TRIGGER fixture_failure BEFORE DELETE ON documents BEGIN "
                         "SELECT RAISE(ABORT,'synthetic secret'); END")
    with fails("memory_unavailable"):
        memory.clear_scope(scope)
    assert memory.search(scope, "naranja").generation == before.generation
    assert len(memory.list_documents(scope)) == 2
    assert memory.resolve_citation(scope, before.hits[0].citation)


def test_fts5_is_used_when_available(memory, scope):
    put(memory, scope)
    result = memory.search(scope, "sintetica")
    if sqlite3.connect(":memory:").execute(
        "SELECT sqlite_compileoption_used('ENABLE_FTS5')",
    ).fetchone()[0]:
        assert result.mode == "fts5_lexical"
    assert result.hits and result.hits[0].score >= 0


def test_without_fts_reports_lexical_scan_and_does_not_use_other_provider(path, scope, monkeypatch):
    class NoFts(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith("CREATE VIRTUAL TABLE"):
                raise sqlite3.OperationalError("no such module: fts5")
            return super().execute(sql, parameters)

    original = sqlite3.connect

    def connect(*args, **kwargs):
        return original(*args, factory=NoFts, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    with MemoryStore(path) as store:
        put(store, scope, text="La calefacción funciona con LIMÓN de prueba.")
        result = store.search(scope, "calefaccion limon")
        assert result.mode == "lexical_scan" and len(result.hits) == 1
        assert store.resolve_citation(scope, result.hits[0].citation) == result.hits[0].excerpt
    with MemoryStore(path) as reopened:
        assert reopened.search(scope, "limón").mode == "lexical_scan"


def test_query_operators_are_plain_tokens_and_sql_values_are_parameters(memory, scope):
    put(memory, scope, key="with-or", text="NARANJA OR LIMON")
    put(memory, scope, key="without-or", text="NARANJA LIMON")
    assert [h.document.key for h in memory.search(scope, 'naranja "OR" limon').hits] == ["with-or"]
    assert not memory.search(scope, "foo' OR 1=1").hits
    assert len(memory.list_documents(scope)) == 2


def test_unicode_chunk_offsets_and_excerpt_surround_match(memory, scope):
    text = "á " * 600 + "PALABRACLAVE " + "ñ " * 1000
    document = put(memory, scope, text=text)
    assert document.chunk_count > 1
    hits = memory.search(scope, "palabraclave").hits
    assert hits
    for hit in hits:
        assert "PALABRACLAVE" in hit.excerpt
        citation = hit.citation
        raw = text.encode()[citation.start_byte:citation.end_byte]
        assert raw.decode() == hit.excerpt
        assert hashlib.sha256(raw).hexdigest() == citation.excerpt_sha256
        assert hashlib.sha256(text.encode()).hexdigest() == citation.text_sha256
        assert memory.resolve_citation(scope, citation) == hit.excerpt


def test_overlapping_chunks_retain_term_at_boundary(memory, scope):
    text = "x " * 1000 + "narANJA " + "y " * 1200
    put(memory, scope, text=text)
    hits = memory.search(scope, "naranja").hits
    assert hits and all("narANJA" in hit.excerpt for hit in hits)


def test_search_paging_detects_changed_generation(memory, scope):
    for key in ("a", "b", "c"):
        put(memory, scope, key=key)
    first = memory.search(scope, "naranja", limit=1)
    second = memory.search(scope, "naranja", limit=1, offset=first.next_offset,
                           expected_generation=first.generation)
    assert first.has_more and second.has_more
    assert first.hits[0].document.document_id != second.hits[0].document.document_id
    put(memory, scope, key="d")
    with fails("search_changed"):
        memory.search(scope, "naranja", offset=second.next_offset,
                      expected_generation=first.generation)
    assert not memory.search(scope, "naranja", offset=100).hits


def test_delete_reinsert_and_metadata_update_invalidate_citations(memory, scope):
    original = put(memory, scope)
    citation = memory.search(scope, "naranja").hits[0].citation
    title_changed = put(memory, scope, title="Título nuevo")
    assert title_changed.revision == 2
    with fails("stale_citation"):
        memory.resolve_citation(scope, citation)
    latest = memory.search(scope, "naranja").hits[0].citation
    receipt = memory.delete(scope, "control", expected_revision=2)
    assert receipt.deleted and receipt.document_id == original.document_id
    with fails("stale_citation"):
        memory.resolve_citation(scope, latest)
    recreated = put(memory, scope)
    assert recreated.document_id != original.document_id and recreated.revision == 1


@pytest.mark.parametrize("change", [
    {"text_sha256": "0" * 64}, {"excerpt_sha256": "0" * 64},
    {"source_uri": "manual://forged"}, {"revision": 99},
])
def test_forged_citation_does_not_resolve(memory, scope, change):
    put(memory, scope)
    citation = memory.search(scope, "naranja").hits[0].citation
    with fails("stale_citation"):
        memory.resolve_citation(scope, replace(citation, **change))


@pytest.mark.parametrize("change", [
    {"revision": True}, {"start_byte": True}, {"document_id": "wrong"},
    {"text_sha256": "wrong"}, {"end_byte": 0}, {"source_uri": "\ud800"},
])
def test_citation_types_and_digests_are_strict(memory, scope, change):
    put(memory, scope)
    citation = memory.search(scope, "naranja").hits[0].citation
    with fails("invalid_citation"):
        replace(citation, **change)


def test_citation_cannot_split_multibyte_character(memory, scope):
    put(memory, scope, text="á NARANJA")
    citation = memory.search(scope, "naranja").hits[0].citation
    with fails("invalid_citation"):
        memory.resolve_citation(scope, replace(citation, start_byte=1))


def test_update_rollback_preserves_original_chunks_search_citations_and_generation(memory, scope):
    original = put(memory, scope)
    search = memory.search(scope, "naranja")
    memory._disk.execute("CREATE TRIGGER fixture_failure BEFORE INSERT ON chunks BEGIN "
                         "SELECT RAISE(ABORT,'synthetic secret'); END")
    with fails("memory_unavailable") as failure:
        put(memory, scope, text="Texto distinto con PERA", expected_revision=1)
    assert "synthetic secret" not in str(failure.value)
    assert memory.get(scope, "control").document == original
    assert memory.search(scope, "naranja").generation == search.generation
    assert not memory.search(scope, "pera").hits
    assert memory.resolve_citation(scope, search.hits[0].citation)


def test_delete_rollback_preserves_document_and_citation(memory, scope):
    put(memory, scope)
    citation = memory.search(scope, "naranja").hits[0].citation
    memory._disk.execute("CREATE TRIGGER fixture_failure BEFORE DELETE ON documents BEGIN "
                         "SELECT RAISE(ABORT,'synthetic secret'); END")
    with fails("memory_unavailable"):
        memory.delete(scope, "control")
    assert memory.resolve_citation(scope, citation)
    assert memory.search(scope, "naranja").hits


def test_optimistic_revision_prevents_lost_updates_under_concurrency(memory, scope):
    put(memory, scope)

    def update(number):
        try:
            return put(memory, scope, text=f"Cambio {number}", expected_revision=1).revision
        except MemoryError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(update, range(16)))
    assert results.count(2) == 1 and results.count("revision_conflict") == 15
    assert memory.get(scope, "control").document.revision == 2


def test_store_lock_is_exclusive_and_released_by_close(path, scope):
    first, second = MemoryStore(path), MemoryStore(path)
    try:
        put(first, scope)
        with fails("memory_in_use"):
            second.list_documents(scope)
        assert second.search(replace(scope, temporary=True), "naranja").hits == ()
        first.close()
        assert second.list_documents(scope)
    finally:
        first.close()
        second.close()


def test_private_permissions_and_lazy_open(path, scope):
    with MemoryStore(path) as store:
        assert not path.parent.exists()
        put(store, scope)
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.with_name(path.name + ".lock").stat().st_mode) == 0o600
        assert store._disk.execute("PRAGMA application_id").fetchone()[0] == 0x4A524D4D


@pytest.mark.parametrize("part", ["database", "lock", "journal", "wal", "shm"])
@pytest.mark.parametrize("attack", ["symlink", "hardlink", "fifo", "public"])
def test_untrusted_database_lock_and_sidecars_fail_closed(path, scope, part, attack):
    path.parent.mkdir(mode=0o700)
    target = path.with_name(path.name + {
        "database": "", "lock": ".lock", "journal": "-journal", "wal": "-wal", "shm": "-shm",
    }[part])
    fixture = path.parent / "synthetic-target"
    fixture.write_bytes(b"synthetic private fixture")
    fixture.chmod(0o600)
    if attack == "symlink":
        target.symlink_to(fixture)
    elif attack == "hardlink":
        os.link(fixture, target)
    elif attack == "fifo":
        os.mkfifo(target, mode=0o600)
    else:
        target.write_bytes(b"synthetic")
        target.chmod(0o644)
    with MemoryStore(path) as store, pytest.raises(MemoryError):
        store.list_documents(scope)
    assert fixture.read_bytes() == b"synthetic private fixture"


def test_ancestor_symlink_and_public_directory_are_rejected(tmp_path, scope):
    root = tmp_path.resolve()
    real = root / "real"
    real.mkdir(mode=0o700)
    (root / "link").symlink_to(real, target_is_directory=True)
    with MemoryStore(root / "link/nested/memory.sqlite3") as store, pytest.raises(MemoryError):
        store.list_documents(scope)
    public = root / "public"
    public.mkdir(mode=0o755)
    with MemoryStore(public / "memory.sqlite3") as store, pytest.raises(MemoryError):
        store.list_documents(scope)


def test_replaced_directory_is_detected_before_query(memory, scope, path):
    put(memory, scope)
    old = path.parent.with_name("moved-private-memory")
    path.parent.rename(old)
    path.parent.mkdir(mode=0o700)
    statements = []
    memory._disk.set_trace_callback(statements.append)
    with fails("unsafe_memory_path"):
        memory.get(scope, "control")
    assert statements == []


def test_corrupt_or_foreign_database_is_not_imported(path, scope):
    path.parent.mkdir(mode=0o700)
    path.write_bytes(b"SYNTHETIC NOT A DATABASE")
    path.chmod(0o600)
    with MemoryStore(path) as value, fails("memory_unavailable") as failure:
        value.search(scope, "test")
    assert "SYNTHETIC" not in str(failure.value) and str(path) not in str(failure.value)
    path.unlink()
    with sqlite3.connect(path) as other:
        other.execute("CREATE TABLE unrelated(id INTEGER)")
    path.chmod(0o600)
    with MemoryStore(path) as value, fails("invalid_memory_database"):
        value.list_documents(scope)


def test_tampered_snapshot_cannot_produce_verified_citation(memory, scope):
    put(memory, scope)
    memory._disk.execute("UPDATE documents SET text='FORGED'")
    with fails("invalid_memory_database"):
        memory.get(scope, "control")
    with fails("invalid_memory_database"):
        memory.search(scope, "naranja")


@pytest.mark.parametrize("column,value", [
    ("source_uri", "not-a-source"), ("title", "x" * 513), ("document_id", "wrong"),
    ("chunk_count", -1), ("byte_length", 0), ("text_sha256", "wrong"),
])
def test_corrupt_metadata_is_rejected_without_becoming_a_citation(memory, scope, column, value):
    put(memory, scope)
    if column == "document_id":
        memory._disk.execute("PRAGMA foreign_keys=OFF")
    memory._disk.execute(f"UPDATE documents SET {column}=?", (value,))
    with fails("invalid_memory_database"):
        memory.list_documents(scope)


def test_corrupt_chunk_offset_cannot_leak_unverified_excerpt(memory, scope):
    put(memory, scope)
    memory._disk.execute("UPDATE chunks SET start_byte=-1")
    with fails("invalid_memory_database"):
        memory.search(scope, "naranja")


def test_capacity_limits_do_not_drop_or_partially_reindex_documents(path, scope):
    limits = MemoryLimits(max_documents=2, max_documents_per_scope=2, max_scope_bytes=20)
    with MemoryStore(path, limits=limits) as memory:
        put(memory, scope, key="a", text="naranja")
        put(memory, scope, key="b", text="limon")
        with fails("memory_capacity_exceeded"):
            put(memory, scope, key="c", text="pera")
        with fails("memory_capacity_exceeded"):
            put(memory, scope, key="a", text="demasiado texto para la memoria")
        assert memory.get(scope, "a").text == "naranja"
        assert len(memory.list_documents(scope)) == 2
        assert memory.search(scope, "naranja").hits


def test_total_bytes_limit_covers_all_namespaces_without_dropping_existing_data(path, scope):
    with MemoryStore(path, limits=MemoryLimits(max_total_bytes=10)) as memory:
        put(memory, scope, text="naranja")
        other = replace(scope, workspace_id="another")
        with fails("memory_capacity_exceeded"):
            put(memory, other, text="limon")
        assert memory.get(scope, "control").text == "naranja"
        assert memory.list_documents(other) == ()


def test_generation_retention_has_a_scope_capacity_bound(path, scope):
    with MemoryStore(path, limits=MemoryLimits(max_scopes=1)) as memory:
        put(memory, scope)
        memory.clear_scope(scope)
        with fails("memory_capacity_exceeded"):
            put(memory, replace(scope, session_id="new_session"))
        # Existing namespace remains usable; generations are not silently reset.
        assert put(memory, scope).revision == 1
        assert memory.search(scope, "naranja").generation == 3


@pytest.mark.parametrize("query", ["", "!!!", "x" * 513, "a b c d e f g h i", "x" * 65,
                                   "\ud800", None, 1])
def test_invalid_queries_fail_before_opening_storage(path, scope, query):
    with MemoryStore(path) as memory, fails("invalid_query"):
        memory.search(scope, query)
    assert not path.parent.exists()


@pytest.mark.parametrize("text", ["", " ", "\0", "\ud800", None, 1])
def test_invalid_text_fails_before_opening_storage(path, scope, text):
    with MemoryStore(path) as memory, fails("invalid_document_text"):
        put(memory, scope, text=text)
    assert not path.parent.exists()


@pytest.mark.parametrize("key", ["", "../outside", "nested/../outside", "a//b", "/absolute", 1])
def test_invalid_keys_fail_before_opening_storage(path, scope, key):
    with MemoryStore(path) as memory, fails("invalid_document_key"):
        put(memory, scope, key=key)
    assert not path.parent.exists()


@pytest.mark.parametrize("values", [
    ("", "w", None, False), ("agent", "../w", None, False),
    ("agent", "w", None, True), ("agent", "w", "s", 1),
    ("agent", "w", "s/other", False),
])
def test_invalid_scope_identifiers_are_rejected(values):
    with fails("invalid_scope"):
        MemoryScope(*values)


@pytest.mark.parametrize("value", [True, 0, -1, 1.5, "1"])
def test_revision_is_not_coerced(memory, scope, value):
    put(memory, scope)
    with fails("invalid_revision"):
        put(memory, scope, expected_revision=value)
    with fails("invalid_revision"):
        memory.delete(scope, "control", expected_revision=value)


@pytest.mark.parametrize("limit,offset", [(True, 0), (0, 0), (101, 0), (1, -1), (1, True)])
def test_invalid_paging_is_rejected(memory, scope, limit, offset):
    with fails("invalid_page"):
        memory.search(scope, "naranja", limit=limit, offset=offset)
    with fails("invalid_page"):
        memory.list_documents(scope, limit=limit, offset=offset)


def test_closed_store_does_not_reopen_disk(path, scope):
    memory = MemoryStore(path)
    memory.close()
    with fails("memory_closed"):
        memory.list_documents(scope)
    assert not path.parent.exists()


def messages(session_id="session_1"):
    return [ConversationMessage(f"message_{i}", session_id, i, role, text)
            for i, (role, text) in enumerate([
                ("user", "Primera instrucción de prueba " * 20),
                ("assistant", "Afirmación del modelo, no recibo " * 20),
                ("user", "Consulta más reciente"),
                ("assistant", "Respuesta reciente"),
            ], 1)]


def test_compaction_preserves_exact_text_offsets_and_model_provenance(scope, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("La compactación abrió almacenamiento o llamó un proveedor")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    source = messages()
    compacted = compact_messages(scope, source, budget_bytes=320, max_recent_messages=2)
    assert compacted.algorithm == "extractive-prefix-v1" and not compacted.verified_action_claims
    assert compacted.budget_unit == "utf8_bytes" and compacted.used_bytes <= 320
    assert compacted.recent_messages == tuple(source[-2:])
    for extract in compacted.extracts:
        original = next(message for message in source if message.message_id == extract.message_id)
        assert extract.role == original.role
        raw = original.text.encode()
        assert raw[extract.start_byte:extract.end_byte].decode() == extract.text
        assert extract.source_sha256 == hashlib.sha256(raw).hexdigest()
        assert extract.source_byte_length == len(raw)
        assert extract.truncated is (extract.end_byte < len(raw))
    assert compacted == compact_messages(scope, source, budget_bytes=320, max_recent_messages=2)
    assert compacted.source_digest != compact_messages(replace(scope, agent_id="other"), source,
                                                       budget_bytes=320).source_digest
    assert asdict(compacted)["algorithm"] == "extractive-prefix-v1"


def test_compaction_retains_recent_oversized_message_as_marked_prefix(scope):
    source = [ConversationMessage("one", "session_1", 1, "user", "á" * 1000)]
    result = compact_messages(scope, source, budget_bytes=65)
    assert not result.recent_messages and result.extracts[0].truncated
    assert len(result.extracts[0].text.encode()) == 64
    assert result.used_bytes == 64 and not result.omitted_message_ids


@pytest.mark.parametrize("change", [
    "other_session", "duplicate_id", "sequence", "role", "surrogate",
])
def test_compaction_rejects_foreign_or_ambiguous_messages(scope, change):
    source = messages()
    if change == "other_session":
        source[0] = replace(source[0], session_id="foreign")
    elif change == "duplicate_id":
        source[1] = replace(source[1], message_id=source[0].message_id)
    elif change == "sequence":
        source[1] = replace(source[1], sequence=source[0].sequence)
    elif change == "role":
        source[0] = replace(source[0], role="verified_action")
    else:
        source[0] = replace(source[0], text="\ud800")
    with fails("invalid_compaction"):
        compact_messages(scope, source, budget_bytes=1000)


def test_compaction_explicitly_reports_fully_omitted_messages_and_empty_input(scope):
    source = messages()
    result = compact_messages(scope, source, budget_bytes=64, max_recent_messages=0)
    assert result.omitted_message_ids == (source[0].message_id,)
    assert result.extracts[0].message_id == source[1].message_id and result.extracts[0].truncated
    empty = compact_messages(scope, [], budget_bytes=64)
    assert not empty.extracts and not empty.recent_messages and empty.used_bytes == 0


def test_compaction_requires_session_bound_scope(scope):
    with fails("invalid_compaction"):
        compact_messages(replace(scope, session_id=None), messages(), budget_bytes=1000)


def test_store_constructor_does_not_accept_credential_or_relative_paths():
    for path in (Path("memory.sqlite3"), Path("/private/tmp/auth.json"), None):
        with fails("invalid_memory_path"):
            MemoryStore(path)
