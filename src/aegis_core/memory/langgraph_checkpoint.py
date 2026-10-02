"""Durable JSON-only LangGraph saver in the authenticated conversation envelope.

This saver is deliberately for the context graph: it cannot persist broker
permissions, callbacks, credentials or tool execution continuations.
"""
from __future__ import annotations

import copy
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any
from uuid import UUID

from aegis_core.async_tasks import run_blocking_owned
from aegis_core.langgraph_compat import BaseCheckpointSaver, CheckpointTuple
from aegis_core.memory.sqlite import SQLiteMemoryStore

__all__ = ["ConversationCheckpointSaver"]


class ConversationCheckpointSaver(BaseCheckpointSaver):
    def __init__(self, store: SQLiteMemoryStore, namespace: str) -> None:
        super().__init__()
        self._store, self._namespace = store, namespace

    @staticmethod
    def _identity(config) -> UUID:
        if config["configurable"].get("checkpoint_ns", ""):
            raise ValueError("nested checkpoint namespaces are not supported")
        return UUID(config["configurable"]["thread_id"])

    def get_tuple(self, config) -> CheckpointTuple | None:
        entries = self._store.conversation_context(
            namespace=self._namespace, conversation_id=self._identity(config),
        ).get("entries", [])
        requested = config["configurable"].get("checkpoint_id")
        for entry in reversed(entries):
            if requested and entry["checkpoint"]["id"] != requested:
                continue
            return self._tuple(config, entry)
        return None

    @staticmethod
    def _tuple(config, entry) -> CheckpointTuple:
        fields = {"thread_id": config["configurable"]["thread_id"], "checkpoint_ns": ""}
        return CheckpointTuple(
            config={"configurable": {**fields, "checkpoint_id": entry["checkpoint"]["id"]}},
            checkpoint=copy.deepcopy(entry["checkpoint"]),
            metadata=copy.deepcopy(entry["metadata"]),
            parent_config=({"configurable": {**fields, "checkpoint_id": entry["parent"]}}
                           if entry.get("parent") else None),
            pending_writes=[tuple(write) for write in entry.get("writes", [])],
        )

    def list(self, config, *, filter=None, before=None, limit=None) -> Iterator[CheckpointTuple]:
        if config is None:
            raise ValueError("conversation identity is required")
        entries = self._store.conversation_context(
            namespace=self._namespace, conversation_id=self._identity(config),
        ).get("entries", [])
        before_id = (before or {}).get("configurable", {}).get("checkpoint_id")
        count = 0
        for entry in reversed(entries):
            if before_id and entry["checkpoint"]["id"] >= before_id:
                continue
            if filter and any(entry["metadata"].get(k) != v for k, v in filter.items()):
                continue
            if limit is not None and count >= limit:
                break
            count += 1
            yield self._tuple(config, entry)

    def put(self, config, checkpoint, metadata, new_versions):
        def update(document):
            entries = document.get("entries", [])
            entries = [e for e in entries if e["checkpoint"]["id"] != checkpoint["id"]]
            entries.append({"checkpoint": checkpoint, "metadata": metadata,
                            "parent": config["configurable"].get("checkpoint_id"), "writes": []})
            return {"entries": entries[-3:]}
        self._store.mutate_conversation_context(
            namespace=self._namespace, conversation_id=self._identity(config), transform=update,
        )
        return {"configurable": {"thread_id": config["configurable"]["thread_id"],
                                 "checkpoint_ns": "", "checkpoint_id": checkpoint["id"]}}

    def put_writes(self, config, writes: Sequence[tuple[str, Any]], task_id, task_path=""):
        def update(document):
            for entry in document.get("entries", []):
                if entry["checkpoint"]["id"] != config["configurable"]["checkpoint_id"]:
                    continue
                existing = {(w[0], w[1]) for w in entry["writes"]}
                for channel, value in writes:
                    if (task_id, channel) not in existing:
                        # Exceptions are diagnostic markers; never serialize their private bodies.
                        safe = "context_node_failed" if channel == "__error__" else value
                        entry["writes"].append([task_id, channel, safe])
            return document
        self._store.mutate_conversation_context(
            namespace=self._namespace, conversation_id=self._identity(config), transform=update,
        )

    def delete_thread(self, thread_id: str) -> None:
        self._store.mutate_conversation_context(
            namespace=self._namespace, conversation_id=UUID(thread_id), transform=lambda _: {},
        )

    async def aget_tuple(self, config):
        return await run_blocking_owned(self.get_tuple, config)

    async def alist(self, config, *, filter=None, before=None,
                    limit=None) -> AsyncIterator[CheckpointTuple]:
        entries = await run_blocking_owned(lambda: list(
            self.list(config, filter=filter, before=before, limit=limit)
        ))
        for entry in entries:
            yield entry

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await run_blocking_owned(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        await run_blocking_owned(self.put_writes, config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id):
        await run_blocking_owned(self.delete_thread, thread_id)
