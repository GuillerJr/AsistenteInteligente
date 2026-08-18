from pathlib import Path

import pytest

from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.memory.service import MemoryIpcService
from aegis_core.memory.sqlite import SQLiteMemoryStore

AUTHENTICATOR = IpcAuthenticator(bytes.fromhex("99" * 32))


def _service(tmp_path: Path, *, max_entries: int = 50_000) -> MemoryIpcService:
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3", max_entries=max_entries)
    store.initialize()
    return MemoryIpcService(store)


@pytest.mark.asyncio
async def test_memory_ipc_round_trip(tmp_path: Path) -> None:
    service = _service(tmp_path)
    put = AUTHENTICATOR.create_request(
        "memory.put",
        {
            "namespace": "user.default",
            "kind": "preference",
            "content": "Prefiere español.",
            "tags": ["language.es"],
        },
    )

    stored = await service.handle(put)
    search = AUTHENTICATOR.create_request(
        "memory.search",
        {"namespace": "user.default", "query": "español"},
    )
    found = await service.handle(search)
    get = AUTHENTICATOR.create_request(
        "memory.get",
        {
            "namespace": "user.default",
            "memory_id": stored.payload["memory_id"],
        },
    )
    loaded = await service.handle(get)
    delete = AUTHENTICATOR.create_request(
        "memory.delete",
        {
            "namespace": "user.default",
            "memory_id": stored.payload["memory_id"],
        },
    )
    deleted = await service.handle(delete)

    assert stored.ok is True
    assert found.ok is True
    assert found.payload["hits"][0]["memory_id"] == stored.payload["memory_id"]
    assert loaded.payload["content"] == "Prefiere español."
    assert deleted.payload == {
        "memory_id": stored.payload["memory_id"],
        "status": "deleted",
    }


@pytest.mark.asyncio
async def test_memory_ipc_rejects_invalid_and_secret_payloads(tmp_path: Path) -> None:
    service = _service(tmp_path)
    invalid = AUTHENTICATOR.create_request(
        "memory.put",
        {"namespace": "../escape", "kind": "semantic", "content": "dato"},
    )
    secret = AUTHENTICATOR.create_request(
        "memory.put",
        {
            "namespace": "user.default",
            "kind": "semantic",
            "content": "nvapi-example-credential",
        },
    )

    invalid_result = await service.handle(invalid)
    secret_result = await service.handle(secret)

    assert invalid_result.error_code == "invalid_payload"
    assert secret_result.error_code == "memory_secret_rejected"


@pytest.mark.asyncio
async def test_memory_ipc_uses_stable_missing_and_capacity_errors(tmp_path: Path) -> None:
    service = _service(tmp_path, max_entries=1)
    first = AUTHENTICATOR.create_request(
        "memory.put",
        {"namespace": "user.default", "kind": "episodic", "content": "primera"},
    )
    second = AUTHENTICATOR.create_request(
        "memory.put",
        {"namespace": "user.default", "kind": "episodic", "content": "segunda"},
    )
    missing = AUTHENTICATOR.create_request(
        "memory.get",
        {
            "namespace": "user.default",
            "memory_id": "b8af0d81-5efc-42a8-a689-8a52c735fc45",
        },
    )

    assert (await service.handle(first)).ok is True
    assert (await service.handle(second)).error_code == "memory_capacity_reached"
    assert (await service.handle(missing)).error_code == "memory_not_found"
