from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Callable
from datetime import datetime
from typing import NoReturn

from aegis_core.memory.contracts import (
    MAX_MEMORY_EXCERPT_BYTES,
    ConversationRecord,
    ConversationTurn,
    MemoryRecord,
    MemorySearchHit,
)
from aegis_core.memory.crypto import MemoryRowCipher, RowAuthenticationError
from aegis_core.memory.errors import DecryptionAuthError, MemoryStoreError

CompromiseHandler = Callable[[str, Exception | None], NoReturn]


class MemoryRowCodec:
    """Own the authenticated representation of memories and conversation rows.

    The store controls transactions; this codec controls the byte-level contract.
    Keeping those responsibilities separate makes it impossible for a query refactor
    to silently weaken AEAD or blind-index validation.
    """

    def __init__(
        self,
        cipher: MemoryRowCipher,
        *,
        on_compromised: CompromiseHandler,
    ) -> None:
        self._cipher = cipher
        self._on_compromised = on_compromised

    def seal_record(
        self,
        record: MemoryRecord,
    ) -> tuple[bytes, bytes, str | None, str, str]:
        document = {
            "content": record.content,
            "source": record.source,
            "tags": list(record.tags),
            "kind": record.kind.value,
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
            "content_sha256": record.content_sha256,
            "confidence": record.confidence,
            "evidence": record.evidence.value,
            "expires_at": record.expires_at.isoformat() if record.expires_at else None,
            "last_confirmed_at": (
                record.last_confirmed_at.isoformat() if record.last_confirmed_at else None
            ),
        }
        sealed = self._cipher.seal(
            namespace=record.namespace,
            memory_id=str(record.memory_id),
            document=document,
        )
        source_digest = (
            self._cipher.blind_exact(record.source) if record.source is not None else None
        )
        tag_digests = json.dumps(
            [self._cipher.blind_exact(tag) for tag in record.tags],
            separators=(",", ":"),
        )
        return (
            sealed.nonce,
            sealed.ciphertext,
            source_digest,
            tag_digests,
            self._cipher.blind_search_document(record.content),
        )

    def record_from_row(self, row: sqlite3.Row) -> MemoryRecord:
        memory_id = row["memory_id"]
        namespace = row["namespace"]
        if not isinstance(memory_id, str) or not isinstance(namespace, str):
            self._fail("memory_row_identity_invalid")
        try:
            document = self._cipher.open(
                namespace=namespace,
                memory_id=memory_id,
                nonce=row["nonce"],
                ciphertext=row["ciphertext"],
            )
        except RowAuthenticationError:
            self._fail("memory_row_aead_authentication_failed")

        expected_keys = {
            "content",
            "source",
            "tags",
            "kind",
            "created_at",
            "updated_at",
            "content_sha256",
            "confidence",
            "evidence",
            "expires_at",
            "last_confirmed_at",
        }
        if set(document) != expected_keys:
            self._fail("memory_row_document_shape_invalid")
        try:
            content = self.verified_content(document["content"], document["content_sha256"])
            tags = tuple(document["tags"])
            record = MemoryRecord(
                memory_id=memory_id,
                namespace=namespace,
                kind=document["kind"],
                content=content,
                source=document["source"],
                tags=tags,
                created_at=datetime.fromisoformat(document["created_at"]),
                updated_at=datetime.fromisoformat(document["updated_at"]),
                content_sha256=document["content_sha256"],
                confidence=document["confidence"],
                evidence=document["evidence"],
                expires_at=(
                    datetime.fromisoformat(document["expires_at"])
                    if document["expires_at"]
                    else None
                ),
                last_confirmed_at=(
                    datetime.fromisoformat(document["last_confirmed_at"])
                    if document["last_confirmed_at"]
                    else None
                ),
            )
            structural = {
                "kind": row["kind"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "content_sha256": row["content_sha256"],
                "confidence": row["confidence"],
                "evidence": row["evidence"],
                "expires_at": row["expires_at"],
                "last_confirmed_at": row["last_confirmed_at"],
            }
            if any(document[name] != value for name, value in structural.items()):
                self._fail("memory_row_metadata_mismatch")
            source_digest = (
                self._cipher.blind_exact(record.source) if record.source is not None else None
            )
            tag_digests = json.dumps(
                [self._cipher.blind_exact(tag) for tag in record.tags],
                separators=(",", ":"),
            )
            if (
                row["content"] != ""
                or row["source"] is not None
                or row["tags_json"] != "[]"
                or row["source_digest"] != source_digest
                or row["tags_digest_json"] != tag_digests
            ):
                self._fail("memory_row_lookup_metadata_mismatch")
            return record
        except DecryptionAuthError:
            raise
        except (KeyError, MemoryStoreError, TypeError, ValueError) as error:
            self._fail("memory_row_decrypted_payload_invalid", cause=error)

    @staticmethod
    def plaintext_record_from_migration(row: sqlite3.Row) -> MemoryRecord:
        content = MemoryRowCodec.verified_content(row["content"], row["content_sha256"])
        try:
            return MemoryRecord(
                memory_id=row["memory_id"],
                namespace=row["namespace"],
                kind=row["kind"],
                content=content,
                source=row["source"],
                tags=tuple(json.loads(row["tags_json"])),
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
                content_sha256=row["content_sha256"],
                confidence=row["confidence"],
                evidence=row["evidence"],
                expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
                last_confirmed_at=datetime.fromisoformat(row["last_confirmed_at"])
                if row["last_confirmed_at"]
                else None,
            )
        except (TypeError, ValueError) as error:
            raise MemoryStoreError("stored migration record is invalid") from error

    @staticmethod
    def conversation_from_row(row: sqlite3.Row) -> ConversationRecord:
        return ConversationRecord(
            conversation_id=row["conversation_id"],
            namespace=row["namespace"],
            title=row["title"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def turn_from_row(row: sqlite3.Row) -> ConversationTurn:
        content = MemoryRowCodec.verified_content(row["content"], row["content_sha256"])
        try:
            return ConversationTurn(
                turn_id=row["turn_id"],
                conversation_id=row["conversation_id"],
                sequence=row["sequence"],
                role=row["role"],
                content=content,
                created_at=datetime.fromisoformat(row["created_at"]),
                content_sha256=row["content_sha256"],
            )
        except (TypeError, ValueError) as error:
            raise MemoryStoreError("stored conversation turn is invalid") from error

    def hit_from_row(
        self,
        row: sqlite3.Row,
        *,
        score: float | None = None,
    ) -> MemorySearchHit:
        record = self.record_from_row(row)
        if "decayed_confidence" in row.keys():
            decayed = float(row["decayed_confidence"])
            if not math.isfinite(decayed) or not 0.0 <= decayed <= 1.0:
                raise MemoryStoreError("stored memory decay result is invalid")
            record = record.model_copy(update={"confidence": decayed})
        encoded = record.content.encode("utf-8")[:MAX_MEMORY_EXCERPT_BYTES]
        excerpt = encoded.decode("utf-8", errors="ignore")
        try:
            return MemorySearchHit(
                memory_id=record.memory_id,
                namespace=record.namespace,
                kind=record.kind,
                excerpt=excerpt,
                source=record.source,
                tags=record.tags,
                updated_at=record.updated_at,
                content_sha256=record.content_sha256,
                score=max(0.0, -float(row["rank"])) if score is None else score,
                confidence=record.confidence,
                evidence=record.evidence,
                expires_at=record.expires_at,
                last_confirmed_at=record.last_confirmed_at,
            )
        except (TypeError, ValueError) as error:
            raise MemoryStoreError("stored memory search row is invalid") from error

    @staticmethod
    def verified_content(content: object, expected_sha256: object) -> str:
        if (
            not isinstance(content, str)
            or not isinstance(expected_sha256, str)
            or MemoryRecord.digest_content(content) != expected_sha256
        ):
            raise MemoryStoreError("stored content hash is invalid")
        return content

    def _fail(self, reason: str, *, cause: Exception | None = None) -> NoReturn:
        self._on_compromised(reason, cause)
