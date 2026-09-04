from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import struct
from pathlib import Path
from uuid import uuid4

from aegis_core.memory.records import SpotlightGraphRecord
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.tools.audit import AuditSink, NullAuditSink

_PROTOCOL_VERSION = "1.0"
_DOMAIN = "ai.aegis.graphrag"
_MAX_FRAME_BYTES = 262_144


class SpotlightSyncError(RuntimeError):
    """A private Core Spotlight graph synchronization failed."""


class SpotlightGraphSync:
    def __init__(
        self,
        store: SQLiteMemoryStore,
        *,
        namespace: str,
        helper_path: Path,
        enabled: bool,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._store = store
        self._namespace = namespace
        self._helper_path = helper_path
        self._enabled = enabled
        self._audit = audit_sink or NullAuditSink()
        self._dirty = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    def arm(self) -> None:
        if self._enabled:
            self._dirty.set()

    def notify_change(self, namespace: str) -> None:
        if not self._enabled or namespace != self._namespace:
            return
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(self._dirty.set)

    async def run(self) -> None:
        if not self._enabled:
            return
        if not self._private_helper():
            raise SpotlightSyncError("Spotlight helper is unavailable or unsafe")
        self._loop = asyncio.get_running_loop()
        self._store.set_graph_change_listener(self.notify_change)
        try:
            while True:
                await self._dirty.wait()
                self._dirty.clear()
                try:
                    await self._synchronize()
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self._audit.record_system_event(
                        uuid4(),
                        event_type="spotlight_graph_sync_failed",
                        component="spotlight_sync",
                        data={"reason": type(error).__name__},
                    )
                    await asyncio.sleep(30)
                    self._dirty.set()
        finally:
            self._store.set_graph_change_listener(None)
            self._loop = None

    async def _synchronize(self) -> None:
        await self._invoke("reset_domain")
        after: str | None = None
        count = 0
        digest = hashlib.sha256()
        while True:
            records = await asyncio.to_thread(
                self._store.spotlight_graph_records,
                namespace=self._namespace,
                after_node_id=after,
                limit=100,
            )
            if not records:
                break
            items = [self._item(record) for record in records]
            for item in items:
                digest.update(json.dumps(item, sort_keys=True).encode())
            await self._invoke("upsert", items=items)
            count += len(records)
            after = str(records[-1].node_id)
            await asyncio.sleep(0)
        self._audit.record_system_event(
            uuid4(),
            event_type="spotlight_graph_sync_completed",
            component="spotlight_sync",
            data={
                "items": count,
                "snapshot_sha256": digest.hexdigest(),
                "index": _DOMAIN,
                "protection": "complete",
            },
        )

    @staticmethod
    def _item(record: SpotlightGraphRecord) -> dict[str, object]:
        relationships: list[str] = []
        edge_types: set[str] = set()
        for direction, edge_type, neighbor in record.relationships:
            edge_types.add(edge_type)
            if direction == "outgoing":
                relationships.append(f"{record.name} --({edge_type})--> {neighbor}")
            else:
                relationships.append(f"{neighbor} --({edge_type})--> {record.name}")
        summary = "\n".join(relationships) or f"{record.name} ({record.type})"
        return {
            "identifier": f"aegis-graph-{record.node_id}",
            "domain_identifier": _DOMAIN,
            "node_name": record.name,
            "node_type": record.type,
            "relationship_summary": summary[:2_048],
            "edge_types": sorted(edge_types)[:32],
            "content_digest": record.content_sha256,
        }

    async def _invoke(
        self,
        operation: str,
        *,
        items: list[dict[str, object]] | None = None,
    ) -> None:
        request_id = uuid4()
        payload = {
            "protocol_version": _PROTOCOL_VERSION,
            "request_id": str(request_id),
            "operation": operation,
            "items": items,
            "identifiers": None,
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        if not encoded or len(encoded) > _MAX_FRAME_BYTES:
            raise SpotlightSyncError("Spotlight request exceeds its frame limit")
        process = await asyncio.create_subprocess_exec(
            str(self._helper_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={
                "HOME": str(Path.home()),
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            },
        )
        try:
            async with asyncio.timeout(20):
                stdout, _ = await process.communicate(struct.pack(">I", len(encoded)) + encoded)
        except TimeoutError as error:
            process.kill()
            await process.wait()
            raise SpotlightSyncError("Spotlight helper timed out") from error
        if process.returncode != 0 or len(stdout) < 4:
            raise SpotlightSyncError("Spotlight helper failed")
        length = struct.unpack(">I", stdout[:4])[0]
        if not 1 <= length <= 65_536 or len(stdout) != length + 4:
            raise SpotlightSyncError("Spotlight helper frame is invalid")
        try:
            response = json.loads(stdout[4:])
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SpotlightSyncError("Spotlight helper response is invalid") from error
        if (
            not isinstance(response, dict)
            or response.get("protocol_version") != _PROTOCOL_VERSION
            or response.get("request_id") != str(request_id)
            or response.get("success") is not True
            or response.get("error_code") is not None
        ):
            raise SpotlightSyncError("Spotlight helper rejected the sync")

    def _private_helper(self) -> bool:
        try:
            status = self._helper_path.stat(follow_symlinks=False)
        except OSError:
            return False
        return (
            stat.S_ISREG(status.st_mode)
            and status.st_uid == os.getuid()
            and bool(status.st_mode & stat.S_IXUSR)
            and not bool(status.st_mode & 0o022)
        )
