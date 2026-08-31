import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest

from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.memory.retrieval import HybridMemoryRetriever
from aegis_core.memory.service import MemoryIpcService
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.providers.base import EmbeddingBatch, EmbeddingInputType

AUTHENTICATOR = IpcAuthenticator(bytes.fromhex("99" * 32))


class ServiceEmbeddingProvider:
    async def embed(
        self,
        texts: Sequence[str],
        *,
        input_type: EmbeddingInputType,
    ) -> EmbeddingBatch:
        del input_type
        return EmbeddingBatch(
            model_id="test/embed",
            vectors=tuple((1.0, 0.0) for _ in texts),
        )


def _service(tmp_path: Path, *, max_entries: int = 50_000) -> MemoryIpcService:
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(
        tmp_path / "memory.sqlite3",
        max_entries=max_entries,
        encryption_secret=b"m" * 32,
    )
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
async def test_memory_ipc_uses_fifo_and_stable_capacity_errors(tmp_path: Path) -> None:
    service = _service(tmp_path, max_entries=1)
    first = AUTHENTICATOR.create_request(
        "memory.put",
        {"namespace": "user.default", "kind": "episodic", "content": "primera"},
    )
    second = AUTHENTICATOR.create_request(
        "memory.put",
        {"namespace": "user.default", "kind": "episodic", "content": "segunda"},
    )
    unrelated = AUTHENTICATOR.create_request(
        "memory.put",
        {"namespace": "project.other", "kind": "episodic", "content": "tercera"},
    )
    missing = AUTHENTICATOR.create_request(
        "memory.get",
        {
            "namespace": "user.default",
            "memory_id": "b8af0d81-5efc-42a8-a689-8a52c735fc45",
        },
    )

    first_result = await service.handle(first)
    assert first_result.ok is True
    assert (await service.handle(second)).ok is True
    evicted = AUTHENTICATOR.create_request(
        "memory.get",
        {
            "namespace": "user.default",
            "memory_id": first_result.payload["memory_id"],
        },
    )
    assert (await service.handle(evicted)).error_code == "memory_not_found"
    assert (await service.handle(unrelated)).error_code == "memory_capacity_reached"
    assert (await service.handle(missing)).error_code == "memory_not_found"


@pytest.mark.asyncio
async def test_memory_ipc_reports_explicit_graphrag_indexing(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3", encryption_secret=b"m" * 32)
    store.initialize()
    retriever = HybridMemoryRetriever(
        store,
        embedding_provider=ServiceEmbeddingProvider(),
    )
    service = MemoryIpcService(store, retriever=retriever)
    put = AUTHENTICATOR.create_request(
        "memory.put",
        {
            "namespace": "user.default",
            "kind": "semantic",
            "content": "Contexto semántico persistente.",
        },
    )

    stored = await service.handle(put)
    search = AUTHENTICATOR.create_request(
        "memory.search",
        {"namespace": "user.default", "query": "concepto relacionado"},
    )
    found = await service.handle(search)

    assert stored.payload["embedding_status"] == "indexed"
    assert found.payload["retrieval_mode"] == "graphrag"
    assert found.payload["hits"] == []
    assert "Contexto semántico persistente" in found.payload["graph_context"]


@pytest.mark.asyncio
async def test_memory_ipc_hides_tampered_persistent_content(tmp_path: Path) -> None:
    service = _service(tmp_path)
    stored = await service.handle(
        AUTHENTICATOR.create_request(
            "memory.put",
            {
                "namespace": "user.default",
                "kind": "semantic",
                "content": "contenido confiable",
            },
        )
    )
    with sqlite3.connect(tmp_path / "memory.sqlite3") as connection:
        connection.execute(
            "UPDATE memory_items SET content = ? WHERE memory_id = ?",
            ("contenido manipulado", stored.payload["memory_id"]),
        )

    result = await service.handle(
        AUTHENTICATOR.create_request(
            "memory.get",
            {
                "namespace": "user.default",
                "memory_id": stored.payload["memory_id"],
            },
        )
    )

    assert result.ok is False
    assert result.error_code == "security_compromised"
    assert "manipulado" not in result.model_dump_json()
