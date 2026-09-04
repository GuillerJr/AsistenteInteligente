from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class MemoryStorageMetrics:
    memory_items: int
    memory_capacity: int
    namespace_memory_items: int
    namespace_memory_capacity: int
    namespace_node_embeddings: int
    namespace_node_embedding_capacity: int
    graph_index_bytes: int
    sqlite_vec_loaded: bool


@dataclass(frozen=True, slots=True)
class GraphNodeRecord:
    node_id: UUID
    namespace: str
    name: str
    type: str
    properties: dict[str, object]
    memory_id: UUID | None
    content_sha256: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class GraphEdgeRecord:
    edge_id: UUID
    namespace: str
    source_id: UUID
    target_id: UUID
    type: str
    weight: float
    memory_id: UUID | None


@dataclass(frozen=True, slots=True)
class GraphSeedRecord:
    node_id: UUID
    score: float


@dataclass(frozen=True, slots=True)
class GraphEmbeddingCandidate:
    node_id: UUID
    namespace: str
    name: str
    type: str
    properties: dict[str, object]
    content_sha256: str


@dataclass(frozen=True, slots=True)
class SpotlightGraphRecord:
    node_id: UUID
    name: str
    type: str
    content_sha256: str
    relationships: tuple[tuple[str, str, str], ...]
