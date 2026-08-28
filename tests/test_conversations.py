import asyncio
import json
from pathlib import Path

import pytest

from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.memory.conversations import (
    ConversationCoordinator,
    ConversationIpcService,
)
from aegis_core.memory.sqlite import SQLiteMemoryStore

AUTHENTICATOR = IpcAuthenticator(bytes.fromhex("aa" * 32))


def _components(
    tmp_path: Path,
) -> tuple[SQLiteMemoryStore, ConversationCoordinator, ConversationIpcService]:
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3", encryption_secret=b"m" * 32)
    store.initialize()
    coordinator = ConversationCoordinator(store, namespace="user.default")
    return store, coordinator, ConversationIpcService(store, coordinator)


@pytest.mark.asyncio
async def test_conversation_ipc_create_history_delete_round_trip(tmp_path: Path) -> None:
    _, _, service = _components(tmp_path)
    created = await service.handle(
        AUTHENTICATOR.create_request(
            "conversations.create",
            {"title": "Sesión táctica"},
        )
    )
    conversation_id = created.payload["conversation_id"]

    history = await service.handle(
        AUTHENTICATOR.create_request(
            "conversations.history",
            {"conversation_id": conversation_id},
        )
    )
    deleted = await service.handle(
        AUTHENTICATOR.create_request(
            "conversations.delete",
            {"conversation_id": conversation_id},
        )
    )
    missing = await service.handle(
        AUTHENTICATOR.create_request(
            "conversations.history",
            {"conversation_id": conversation_id},
        )
    )

    assert created.payload["namespace"] == "user.default"
    assert history.payload["turns"] == []
    assert deleted.payload["status"] == "deleted"
    assert missing.error_code == "conversation_not_found"


@pytest.mark.asyncio
async def test_conversation_ipc_rejects_invalid_payload(tmp_path: Path) -> None:
    _, _, service = _components(tmp_path)
    result = await service.handle(
        AUTHENTICATOR.create_request(
            "conversations.create",
            {"title": "", "namespace": "attacker.scope"},
        )
    )

    assert result.error_code == "invalid_payload"


@pytest.mark.asyncio
async def test_coordinator_serializes_same_conversation(tmp_path: Path) -> None:
    store, coordinator, _ = _components(tmp_path)
    conversation = store.create_conversation(namespace="user.default")
    entered = asyncio.Event()

    async def waiter() -> None:
        async with coordinator.serialized(conversation.conversation_id):
            entered.set()

    async with coordinator.serialized(conversation.conversation_id):
        task = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        assert entered.is_set() is False

    await task
    assert entered.is_set() is True


@pytest.mark.asyncio
async def test_coordinator_does_not_persist_credential_like_exchange(
    tmp_path: Path,
) -> None:
    store, coordinator, _ = _components(tmp_path)
    conversation = store.create_conversation(namespace="user.default")

    async with coordinator.serialized(conversation.conversation_id):
        persisted = await coordinator.record_exchange(
            conversation.conversation_id,
            user_content="nvapi-example-credential",
            assistant_content="No almacenaré esa credencial.",
        )

    assert persisted is False
    assert (
        store.conversation_history(
            namespace="user.default",
            conversation_id=conversation.conversation_id,
        )
        == ()
    )


@pytest.mark.asyncio
async def test_conversation_count_capacity_is_enforced(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3", encryption_secret=b"m" * 32)
    store.initialize()
    coordinator = ConversationCoordinator(
        store,
        namespace="user.default",
        max_conversations=1,
    )
    service = ConversationIpcService(store, coordinator)

    first = await service.handle(AUTHENTICATOR.create_request("conversations.create", {}))
    second = await service.handle(AUTHENTICATOR.create_request("conversations.create", {}))

    assert first.ok is True
    assert second.error_code == "conversation_capacity_reached"


@pytest.mark.asyncio
async def test_public_history_is_bounded_below_ipc_frame(tmp_path: Path) -> None:
    store, _, service = _components(tmp_path)
    conversation = store.create_conversation(namespace="user.default")
    store.append_conversation_exchange(
        namespace="user.default",
        conversation_id=conversation.conversation_id,
        user_content="\x00" * 8_000,
        assistant_content="\\" * 8_000,
    )

    result = await service.handle(
        AUTHENTICATOR.create_request(
            "conversations.history",
            {"conversation_id": str(conversation.conversation_id), "limit": 10},
        )
    )

    assert result.ok is True
    assert all(
        len(json.dumps(turn, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 4_096
        for turn in result.payload["turns"]
    )
    assert all(turn["truncated"] is True for turn in result.payload["turns"])
    assert len(result.model_dump_json().encode("utf-8")) < 65_536
