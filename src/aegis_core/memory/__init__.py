"""Persistent memory primitives, loaded lazily to keep contracts cycle-free."""
from importlib import import_module

_EXPORTS = {
    "ConversationCoordinator": "conversations",
    "ConversationIpcService": "conversations",
    "EmbeddingBackfillWorker": "retrieval",
    "GraphRAGService": "graph_service",
    "HybridMemoryRetriever": "retrieval",
    "HybridRRFResult": "graph_service",
    "MemoryDecayWorker": "graph_service",
    "MemoryIpcService": "service",
    "OwnerProfile": "profile",
    "SQLiteMemoryStore": "sqlite",
    "SessionResetResult": "graph_service",
    "SocialMemory": "social",
}
__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{module}"), name)
    globals()[name] = value
    return value
