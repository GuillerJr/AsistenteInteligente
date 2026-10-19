import sqlite3
from uuid import UUID, uuid4

import pytest
from test_conversations import AUTHENTICATOR, _components

from aegis_core.contracts import DocumentInput
from aegis_core.memory.errors import ConversationCapacityError, MemoryNotFoundError


@pytest.mark.asyncio
async def test_rename_and_fork_are_encrypted_independent_complete_histories(tmp_path):
    store, coordinator, service = _components(tmp_path)
    original = await coordinator.create("Original privado")
    for number in range(12):
        documents = (DocumentInput(name="nota.txt", content="Documento privado"),) \
            if number == 0 else ()
        await coordinator.record_exchange(original.conversation_id,
                                          user_content=f"Pregunta {number}",
                                          assistant_content=f"Respuesta {number}",
                                          documents=documents)
    renamed = await service.handle(AUTHENTICATOR.create_request("conversations.rename", {
        "conversation_id": str(original.conversation_id), "title": "  Nuevo nombre privado  ",
    }))
    assert renamed.ok and renamed.payload["title"] == "Nuevo nombre privado"
    assert "task_checkpoint" not in renamed.payload
    response = await service.handle(AUTHENTICATOR.create_request("conversations.fork", {
        "conversation_id": str(original.conversation_id),
    }))
    assert response.ok and "task_checkpoint" not in response.payload
    fork_id = UUID(response.payload["conversation_id"])
    assert fork_id != original.conversation_id and response.payload["title"].endswith(" · fork")
    before = store.conversation_history(namespace=coordinator.namespace,
                                        conversation_id=original.conversation_id, limit=50)
    copied = store.conversation_history(namespace=coordinator.namespace,
                                        conversation_id=fork_id, limit=50)
    assert len(copied) == 24
    assert [turn.content for turn in before] == [turn.content for turn in copied]
    assert not {turn.turn_id for turn in before} & {turn.turn_id for turn in copied}
    assert copied[0].documents == before[0].documents
    await coordinator.record_exchange(fork_id, user_content="Solo fork",
                                      assistant_content="Independiente")
    await coordinator.archive(original.conversation_id, archived=True)
    assert store.empty_conversation_trash(namespace=coordinator.namespace) == 1
    assert len(store.conversation_history(namespace=coordinator.namespace,
                                        conversation_id=fork_id, limit=50)) == 26
    with sqlite3.connect(store.path) as connection:
        headers = connection.execute("SELECT title, ciphertext FROM conversations").fetchall()
        assert all(title is None for title, _ in headers)
        assert all(b"Nuevo nombre privado" not in ciphertext for _, ciphertext in headers)
        assert connection.execute("SELECT COUNT(*) FROM conversation_turns "
                                  "WHERE conversation_id = ?",
                                  (str(original.conversation_id),)).fetchone()[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("method,payload", [
    ("conversations.rename", {"title": "   "}),
    ("conversations.rename", {"title": "nvapi-example-credential"}),
    ("conversations.rename", {"title": "x" * 129}),
    ("conversations.fork", {"namespace": "another.user"}),
    ("conversations.empty_trash", {"namespace": "another.user"}),
])
async def test_chat_management_rejects_invalid_or_secret_payloads(tmp_path, method, payload):
    store, coordinator, service = _components(tmp_path)
    original = await coordinator.create("Sin cambios")
    if method != "conversations.empty_trash":
        payload = {"conversation_id": str(original.conversation_id), **payload}
    response = await service.handle(AUTHENTICATOR.create_request(method, payload))
    assert not response.ok
    assert store.get_conversation(namespace=coordinator.namespace,
                                  conversation_id=original.conversation_id).title == "Sin cambios"


@pytest.mark.asyncio
async def test_empty_trash_purges_all_pages_only_within_owner_namespace(tmp_path):
    store, coordinator, service = _components(tmp_path)
    active = await coordinator.create("Conservar activo")
    other = store.create_conversation(namespace="another.user", title="Conservar ajeno")
    store.archive_conversation(namespace="another.user", conversation_id=other.conversation_id,
                               archived=True)
    for number in range(35):
        entry = await coordinator.create(f"Papelera {number}")
        await coordinator.archive(entry.conversation_id, archived=True)
    page = await service.handle(AUTHENTICATOR.create_request("conversations.list", {
        "archived": True, "query": "Papelera 1", "limit": 2,
    }))
    assert len(page.payload["conversations"]) == 2
    emptied = await service.handle(AUTHENTICATOR.create_request("conversations.empty_trash", {}))
    assert emptied.ok and emptied.payload["deleted_count"] == 35
    assert store.get_conversation(namespace=coordinator.namespace,
                                  conversation_id=active.conversation_id)
    assert len(store.list_conversations(namespace="another.user", archived=True)[0]) == 1
    repeated = await service.handle(AUTHENTICATOR.create_request("conversations.empty_trash", {}))
    assert repeated.payload["deleted_count"] == 0


@pytest.mark.asyncio
async def test_fork_capacity_missing_archived_sources_roll_back_without_empty_clones(tmp_path):
    store, coordinator, service = _components(tmp_path)
    original = await coordinator.create("Original")
    await coordinator.archive(original.conversation_id, archived=True)
    for source in (original.conversation_id, uuid4()):
        response = await service.handle(AUTHENTICATOR.create_request("conversations.fork", {
            "conversation_id": str(source),
        }))
        assert response.error_code == "conversation_not_found"
        assert store.list_conversations(namespace=coordinator.namespace)[0] == ()
    await coordinator.archive(original.conversation_id, archived=False)
    with pytest.raises(ConversationCapacityError) as caught:
        store.fork_conversation(namespace=coordinator.namespace,
                                conversation_id=original.conversation_id, max_conversations=1)
    assert "capacity" in str(caught.value)
    assert len(store.list_conversations(namespace=coordinator.namespace)[0]) == 1
    with pytest.raises(MemoryNotFoundError):
        store.fork_conversation(namespace="another.user", conversation_id=original.conversation_id)
    assert store.list_conversations(namespace="another.user")[0] == ()
