"""Project pages use synthetic encrypted records, never a real owner's history."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from test_projects import Request, _setup

from aegis_core.memory.contracts import ConversationRecord
from aegis_core.memory.conversations import ConversationCoordinator
from aegis_core.projects import ProjectIpcService

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


def project(store, name):
    return store.save(project_id=None, name=name, color="clay", icon="folder", instructions="")


def seed(memory, project_id, count, *, namespace="user.default", start=1, date=NOW,
         archived=False, temporary=False):
    """Seed authenticated rows efficiently, including deliberate timestamp ties."""
    records = []
    with memory._lock, memory._connect() as connection:
        for index in range(start, start + count):
            record = ConversationRecord(
                conversation_id=UUID(int=index), namespace=namespace, title=f"Synthetic {index}",
                created_at=date, updated_at=date, archived=archived,
                context_checkpoints={"project_id": str(project_id), "temporary_chat": temporary},
            )
            sealed = memory._conversation_codec.seal_conversation(record, turn_count=0)
            connection.execute(
                "INSERT INTO conversations (conversation_id, namespace, title, created_at, "
                "updated_at, turn_count, nonce, ciphertext) VALUES (?, ?, NULL, ?, ?, 0, ?, ?)",
                (str(record.conversation_id), namespace, date.isoformat(), date.isoformat(),
                 sealed.nonce, sealed.ciphertext),
            )
            records.append(record)
    return records


async def page(service, project_id, **options):
    return await service.handle(Request("projects.conversations", {
        "project_id": str(project_id), **options,
    }))


@pytest.mark.asyncio
async def test_project_pages_cover_more_than_50_chats_with_stable_ties_and_no_duplicates(tmp_path):
    memory, conversations, store = _setup(tmp_path)
    target = project(store, "Pages")
    records = seed(memory, target.project_id, 123)
    service = ProjectIpcService(store, conversations)
    cursor = None
    listed = []
    sizes = []
    while True:
        response = await page(service, target.project_id, limit=50,
                              **({"cursor": cursor} if cursor else {}))
        assert response.ok
        assert response.payload["project_id"] == str(target.project_id)
        items = response.payload["conversations"]
        sizes.append(len(items))
        listed.extend(item["conversation_id"] for item in items)
        next_cursor = response.payload["next_cursor"]
        assert next_cursor is None or next_cursor != cursor
        cursor = next_cursor
        if cursor is None:
            break
    assert sizes == [50, 50, 23]
    assert listed == [str(record.conversation_id) for record in reversed(records)]
    assert len(set(listed)) == 123


@pytest.mark.asyncio
async def test_project_pages_are_not_cut_off_by_1000_newer_global_chats(tmp_path):
    memory, conversations, store = _setup(tmp_path)
    target, other = project(store, "Target"), project(store, "Other")
    seed(memory, other.project_id, 1_005, start=100, date=NOW + timedelta(days=1))
    expected = seed(memory, target.project_id, 3)
    response = await page(ProjectIpcService(store, conversations), target.project_id)
    assert response.ok
    assert [item["conversation_id"] for item in response.payload["conversations"]] == [
        str(record.conversation_id) for record in reversed(expected)
    ]


@pytest.mark.asyncio
async def test_newer_insertion_between_pages_does_not_repeat_or_skip_original_rows(tmp_path):
    memory, conversations, store = _setup(tmp_path)
    target = project(store, "Stable")
    original = seed(memory, target.project_id, 55)
    service = ProjectIpcService(store, conversations)
    first = await page(service, target.project_id, limit=50)
    seed(memory, target.project_id, 1, start=100, date=NOW + timedelta(seconds=1))
    second = await page(service, target.project_id, cursor=first.payload["next_cursor"], limit=50)
    ids = [item["conversation_id"] for response in (first, second)
           for item in response.payload["conversations"]]
    assert ids == [str(record.conversation_id) for record in reversed(original)]
    assert second.payload["next_cursor"] is None


@pytest.mark.asyncio
async def test_project_owner_trash_and_temporary_boundaries_are_enforced(tmp_path):
    memory, conversations, store = _setup(tmp_path)
    target, other = project(store, "Owner target"), project(store, "Owner other")
    seed(memory, target.project_id, 3)
    seed(memory, other.project_id, 2, start=10)
    seed(memory, target.project_id, 2, start=20, namespace="user.other")
    seed(memory, target.project_id, 2, start=30, archived=True)
    seed(memory, target.project_id, 2, start=40, temporary=True)
    service = ProjectIpcService(store, conversations)
    first = await page(service, target.project_id, limit=2)
    assert [item["conversation_id"] for item in first.payload["conversations"]] == [
        str(UUID(int=3)), str(UUID(int=2)),
    ]
    cursor = first.payload["next_cursor"]
    assert not (await page(service, other.project_id, cursor=cursor)).ok
    other_owner = ProjectIpcService(store, ConversationCoordinator(memory, namespace="user.other"))
    # Even a common signing key cannot make another owner's cursor valid.
    other_owner._cursor_key = service._cursor_key
    assert not (await page(other_owner, target.project_id, cursor=cursor)).ok
    isolated = await page(other_owner, target.project_id)
    assert {item["conversation_id"] for item in isolated.payload["conversations"]} == {
        str(UUID(int=20)), str(UUID(int=21)),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [
    {"limit": 0}, {"limit": 51}, {"limit": True}, {"limit": "30"},
    {"cursor": ""}, {"cursor": 12}, {"cursor": "x" * 1_025},
    {"cursor": "not-a-cursor"}, {"cursor": "a." + "0" * 64}, {"offset": 30},
])
async def test_project_pagination_rejects_invalid_limits_and_cursors(tmp_path, options):
    _, conversations, store = _setup(tmp_path)
    target = project(store, "Invalid")
    response = await page(ProjectIpcService(store, conversations), target.project_id, **options)
    assert not response.ok and response.error_code == "projects_invalid_request"


@pytest.mark.asyncio
async def test_tampered_cursor_missing_project_and_empty_page_are_explicit(tmp_path):
    memory, conversations, store = _setup(tmp_path)
    target = project(store, "Validation")
    service = ProjectIpcService(store, conversations)
    empty = await page(service, target.project_id)
    assert empty.ok and empty.payload["conversations"] == []
    assert empty.payload["next_cursor"] is None
    seed(memory, target.project_id, 2)
    first = await page(service, target.project_id, limit=1)
    cursor = first.payload["next_cursor"]
    tampered = cursor[:-1] + ("0" if cursor[-1] != "0" else "1")
    assert not (await page(service, target.project_id, cursor=tampered)).ok
    missing = await page(service, uuid4())
    assert not missing.ok and missing.error_code == "project_not_found"
    store.delete(target.project_id)
    deleted = await page(service, target.project_id, cursor=cursor)
    assert not deleted.ok and deleted.error_code == "project_not_found"
