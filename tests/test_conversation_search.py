from __future__ import annotations

import base64
import sqlite3
from uuid import UUID

import pytest
from test_conversations import AUTHENTICATOR, _components

from aegis_core.memory.conversation_search import (
    ConversationSearchPayload,
    ConversationSearchRepository,
    SearchCursorCodec,
    match_excerpt,
)
from aegis_core.memory.conversations import ConversationIpcService
from aegis_core.memory.errors import MemorySecurityError


async def search(service, query="aguja", **payload):
    return await service.handle(
        AUTHENTICATOR.create_request(
            "conversations.search",
            {"query": query, **payload},
        )
    )


async def all_pages(service, query="aguja", **payload):
    rows = []
    cursor = None
    seen = set()
    for _ in range(200):
        response = await search(service, query, **payload, **({"cursor": cursor} if cursor else {}))
        assert response.ok
        rows.extend(response.payload["conversations"])
        cursor = response.payload["next_cursor"]
        if cursor is None:
            return rows
        assert cursor not in seen
        seen.add(cursor)
    raise AssertionError("search never completed")


@pytest.mark.asyncio
async def test_search_finds_old_user_and_assistant_turns_beyond_recent_history(tmp_path):
    store, coordinator, service = _components(tmp_path)
    record = await coordinator.create("Título distinto")
    for number in range(75):
        await coordinator.record_exchange(
            record.conversation_id,
            user_content=f"Pregunta {number}" + (" AGUJA lejana" if number == 34 else ""),
            assistant_content=f"Respuesta {number}" + (" faro lejano" if number == 35 else ""),
        )
    assert all(
        "AGUJA" not in turn.content for turn in await coordinator.history(record.conversation_id)
    )
    for query, sequence in [("aguja", 69), ("FARO", 72)]:
        rows = await all_pages(service, query)
        assert len(rows) == 1
        assert rows[0]["title"] == "Título distinto"
        match = rows[0]["search_match"]
        assert match["sequence"] == sequence and query.casefold() in match["excerpt"].casefold()
        opened = await service.handle(
            AUTHENTICATOR.create_request(
                "conversations.messages",
                {
                    "conversation_id": str(record.conversation_id),
                    "before_sequence": sequence + 1,
                },
            )
        )
        assert opened.ok and opened.payload["turns"][-1]["turn_id"] == match["turn_id"]
        assert opened.payload["turns"][-1]["sequence"] == sequence
    for path in tmp_path.glob("memory.sqlite3*"):
        contents = path.read_bytes()
        assert b"AGUJA lejana" not in contents and b"faro lejano" not in contents
    assert (
        store.get_conversation(
            namespace=coordinator.namespace, conversation_id=record.conversation_id
        ).title
        == "Título distinto"
    )


@pytest.mark.asyncio
async def test_search_scope_temporary_trash_filed_namespace_and_title_dedup(tmp_path):
    store, coordinator, service = _components(tmp_path)
    ids = {}
    for title, temporary in [
        ("aguja título", False),
        ("Archivado", False),
        ("Papelera", False),
        ("Temporal", True),
    ]:
        record = await coordinator.create(title, temporary=temporary)
        ids[title] = str(record.conversation_id)
        await coordinator.record_exchange(
            record.conversation_id, user_content="Una aguja", assistant_content="Otra aguja"
        )
    store.mutate_conversation_context(
        namespace=coordinator.namespace,
        conversation_id=UUID(ids["Archivado"]),
        transform=lambda value: {**value, "filed_chat": True},
    )
    await coordinator.archive(UUID(ids["Papelera"]), archived=True)
    other = store.create_conversation(namespace="user.other", title="aguja ajena")
    store.append_conversation_exchange(
        namespace="user.other",
        conversation_id=other.conversation_id,
        user_content="aguja secreta",
        assistant_content="Otro dueño",
    )
    active = await all_pages(service)
    assert [row["conversation_id"] for row in active] == [ids["aguja título"]]
    assert active[0]["search_match"] is None
    assert [row["conversation_id"] for row in await all_pages(service, filed=True)] == [
        ids["Archivado"]
    ]
    assert [row["conversation_id"] for row in await all_pages(service, archived=True)] == [
        ids["Papelera"]
    ]
    denied = await search(service, namespace="user.other")
    assert not denied.ok and denied.error_code == "invalid_payload"


@pytest.mark.asyncio
async def test_unicode_normalization_casefold_and_excerpt_bounds(tmp_path):
    _, coordinator, service = _components(tmp_path)
    record = await coordinator.create("Otra cosa")
    await coordinator.record_exchange(
        record.conversation_id,
        user_content="Inicio " + "🌲" * 220 + " cancio\u0301n Straße " + "🌲" * 220,
        assistant_content="Respuesta",
    )
    for query in ["CANCIÓN", "STRASSE"]:
        rows = await all_pages(service, query)
        excerpt = rows[0]["search_match"]["excerpt"]
        assert len(excerpt) <= 194 and len(excerpt.encode()) <= 776
        assert query.casefold() in excerpt.casefold()
        assert excerpt.startswith("…") and excerpt.endswith("…")
    assert match_excerpt("un texto breve", "texto") == "un texto breve"


@pytest.mark.asyncio
async def test_partial_pages_resume_inside_long_chat_and_never_report_complete_early(tmp_path):
    store, coordinator, service = _components(tmp_path)
    store._conversation_search.MAX_WORK_ITEMS = 3
    old = await coordinator.create("Antiguo")
    for index in range(8):
        await coordinator.record_exchange(
            old.conversation_id,
            user_content=f"Pregunta {index}",
            assistant_content="aguja" if index == 7 else "Respuesta",
        )
    newer = await coordinator.create("aguja reciente")
    first = await search(service, limit=1)
    assert first.ok and first.payload["conversations"][0]["conversation_id"] == str(
        newer.conversation_id
    )
    assert first.payload["next_cursor"]
    second = await search(service, cursor=first.payload["next_cursor"])
    assert second.ok and second.payload["conversations"] == [] and second.payload["next_cursor"]
    rows = await all_pages(service)
    assert [row["conversation_id"] for row in rows] == [
        str(newer.conversation_id),
        str(old.conversation_id),
    ]
    assert rows[-1]["search_match"]["sequence"] == 16


@pytest.mark.asyncio
async def test_time_sliced_search_always_advances_even_when_header_spends_budget(tmp_path):
    store, coordinator, service = _components(tmp_path)
    counter = iter(range(1000))
    store._conversation_search = ConversationSearchRepository(
        store._conversation_codec,
        clock=lambda: float(next(counter)),
    )
    record = await coordinator.create("Lento")
    for index in range(4):
        await coordinator.record_exchange(
            record.conversation_id,
            user_content=f"Pregunta {index}",
            assistant_content="aguja" if index == 3 else "Respuesta",
        )
    rows = await all_pages(service)
    assert len(rows) == 1 and rows[0]["search_match"]["sequence"] == 8


@pytest.mark.asyncio
async def test_cursor_is_authenticated_scoped_and_contains_no_query_text(tmp_path):
    store, coordinator, service = _components(tmp_path)
    store._conversation_search.MAX_WORK_ITEMS = 1
    for index in range(3):
        await coordinator.create(f"Título {index}")
    first = await search(service, "consulta confidencial")
    cursor = first.payload["next_cursor"]
    body = cursor.split(".")[0]
    assert b"consulta confidencial" not in base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    for values in [
        {"query": "otra consulta"},
        {"archived": True},
        {"filed": True},
        {"cursor": cursor[:-1] + ("a" if cursor[-1] != "a" else "b")},
    ]:
        payload = {"query": "consulta confidencial", "cursor": cursor, **values}
        response = await search(service, **payload)
        assert not response.ok and response.error_code == "invalid_payload"
    restarted = ConversationIpcService(store, coordinator)
    assert (
        await search(restarted, "consulta confidencial", cursor=cursor)
    ).error_code == "invalid_payload"
    with pytest.raises(ValueError):
        SearchCursorCodec("user.other").decode(
            ConversationSearchPayload(
                query="consulta confidencial",
                cursor=cursor,
            )
        )


@pytest.mark.asyncio
async def test_updates_do_not_reorder_scan_and_new_chats_wait_for_refresh(tmp_path):
    _store, coordinator, service = _components(tmp_path)
    old = await coordinator.create("aguja antigua")
    newer = await coordinator.create("aguja reciente")
    first = await search(service, limit=1)
    assert first.payload["conversations"][0]["conversation_id"] == str(newer.conversation_id)
    await coordinator.record_exchange(
        old.conversation_id,
        user_content="Editar el antiguo",
        assistant_content="Sin cambiar su creación",
    )
    added = await coordinator.create("aguja posterior")
    second = await search(service, cursor=first.payload["next_cursor"], limit=1)
    assert second.payload["conversations"][0]["conversation_id"] == str(old.conversation_id)
    assert second.payload["next_cursor"] is None
    assert (await all_pages(service))[0]["conversation_id"] == str(added.conversation_id)


@pytest.mark.asyncio
async def test_partial_chat_snapshot_ignores_later_appends_until_refresh(tmp_path):
    store, coordinator, service = _components(tmp_path)
    store._conversation_search.MAX_WORK_ITEMS = 2
    record = await coordinator.create("Original")
    await coordinator.record_exchange(
        record.conversation_id, user_content="Antes", assistant_content="Antes"
    )
    first = await search(service)
    assert first.payload["conversations"] == [] and first.payload["next_cursor"]
    await coordinator.record_exchange(
        record.conversation_id, user_content="aguja añadida", assistant_content="Después"
    )
    pending = first.payload["next_cursor"]
    for _ in range(10):
        page = await search(service, cursor=pending)
        assert page.ok and page.payload["conversations"] == []
        pending = page.payload["next_cursor"]
        if pending is None:
            break
    else:
        raise AssertionError("snapshot did not complete")
    assert len(await all_pages(service)) == 1


@pytest.mark.asyncio
async def test_corrupt_ciphertext_fails_closed_without_returning_partial_results(tmp_path):
    store, coordinator, _service = _components(tmp_path)
    record = await coordinator.create("Título no coincidente")
    await coordinator.record_exchange(
        record.conversation_id, user_content="Texto distinto", assistant_content="aguja"
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE conversation_turns SET ciphertext = ? WHERE sequence = 2", (b"corrupt" * 4,)
        )
    with pytest.raises(MemorySecurityError):
        store.search_conversations(
            namespace=coordinator.namespace, request=ConversationSearchPayload(query="aguja")
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"query": ""},
        {"query": "  "},
        {"query": "a" * 129},
        {"limit": True},
        {"limit": 51},
        {"archived": 1},
        {"filed": "yes"},
        {"offset": 100},
        {"cursor": "forged"},
        {"cursor": "x" * 2049},
    ],
)
async def test_invalid_search_payloads_are_rejected(tmp_path, payload):
    _, _, service = _components(tmp_path)
    response = await search(service, **payload)
    assert not response.ok and response.error_code == "invalid_payload"


@pytest.mark.asyncio
async def test_missing_turn_fails_closed(tmp_path):
    store, coordinator, _ = _components(tmp_path)
    record = await coordinator.create("Distinto")
    await coordinator.record_exchange(
        record.conversation_id, user_content="Uno", assistant_content="Dos"
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute("DELETE FROM conversation_turns WHERE sequence = 1")
    with pytest.raises(MemorySecurityError):
        store.search_conversations(
            namespace=coordinator.namespace, request=ConversationSearchPayload(query="aguja")
        )


@pytest.mark.asyncio
async def test_search_lock_contention_fails_without_unbounded_wait(tmp_path, monkeypatch):
    store, _, service = _components(tmp_path)

    class OccupiedLock:
        def acquire(self, *, timeout):
            assert timeout == 0.5
            return False

        def release(self):
            raise AssertionError("an unacquired lock must not be released")

    monkeypatch.setattr(store, "_lock", OccupiedLock())
    response = await search(service)
    assert not response.ok and response.error_code == "conversation_unavailable"


@pytest.mark.asyncio
async def test_search_database_failure_is_bounded_and_reported_without_a_cursor(
    tmp_path, monkeypatch
):
    store, _, service = _components(tmp_path)

    def failed_database(connection, **_kwargs):
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 500
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store._conversation_search, "search", failed_database)
    response = await search(service)
    assert not response.ok and response.error_code == "conversation_unavailable"
    assert "next_cursor" not in response.payload
    # A failed request releases its lock for subsequent operations.
    assert store._lock.acquire(blocking=False)
    store._lock.release()


@pytest.mark.asyncio
async def test_search_order_uses_only_metadata_index_after_reinitialization(tmp_path):
    store, coordinator, service = _components(tmp_path)
    record = await coordinator.create("aguja conservada")
    store.initialize()
    with sqlite3.connect(store.path) as connection:
        index_columns = connection.execute(
            "PRAGMA index_info(conversations_namespace_created)"
        ).fetchall()
        assert [row[2] for row in index_columns] == ["namespace", "created_at", "conversation_id"]
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM conversations WHERE namespace = ? "
            "AND created_at <= ? ORDER BY created_at DESC, conversation_id DESC",
            (coordinator.namespace, "9999-01-01"),
        ).fetchall()
        assert any("conversations_namespace_created" in row[3] for row in plan)
        assert all("TEMP B-TREE" not in row[3] for row in plan)
    assert [row["conversation_id"] for row in await all_pages(service)] == [
        str(record.conversation_id)
    ]
