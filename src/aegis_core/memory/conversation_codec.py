from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import NoReturn

from aegis_core.memory.contracts import ConversationRecord, ConversationTurn
from aegis_core.memory.crypto import MemoryRowCipher, RowAuthenticationError, SealedMemoryRow
from aegis_core.memory.errors import DecryptionAuthError


class ConversationRowCodec:
    """Bind encrypted history to its namespace, conversation, identity and order."""

    def __init__(
        self,
        cipher: MemoryRowCipher,
        *,
        on_compromised: Callable[[str, Exception | None], NoReturn],
    ) -> None:
        self._cipher = cipher
        self._on_compromised = on_compromised

    def fail(self, reason: str) -> NoReturn:
        self._on_compromised(reason, None)

    def seal_conversation(self, record: ConversationRecord, *, turn_count: int) -> SealedMemoryRow:
        return self._cipher.seal(
            namespace=record.namespace,
            memory_id=f"conversation:{record.conversation_id}",
            document={
                **record.model_dump(mode="json"),
                "created_at": record.created_at.isoformat(),
                "updated_at": record.updated_at.isoformat(),
                "turn_count": turn_count,
            },
        )

    def conversation_from_row(self, row: sqlite3.Row) -> ConversationRecord:
        try:
            document = self._cipher.open(
                namespace=row["namespace"],
                memory_id=f"conversation:{row['conversation_id']}",
                nonce=row["nonce"],
                ciphertext=row["ciphertext"],
            )
            count = document.pop("turn_count")
            if type(count) is not int or count < 0 or count != row["turn_count"]:
                self.fail("conversation_count_mismatch")
            record = ConversationRecord.model_validate(document)
            if row["title"] is not None or any(
                document[field] != row[field]
                for field in ("conversation_id", "namespace", "created_at", "updated_at")
            ):
                self.fail("conversation_metadata_mismatch")
            return record
        except DecryptionAuthError:
            raise
        except (KeyError, IndexError, TypeError, ValueError, RowAuthenticationError):
            self.fail("conversation_authentication_failed")

    def seal_turn(self, turn: ConversationTurn, *, namespace: str) -> SealedMemoryRow:
        return self._cipher.seal(
            namespace=namespace,
            memory_id=f"conversation-turn:{turn.conversation_id}:{turn.turn_id}",
            document={**turn.model_dump(mode="json"), "created_at": turn.created_at.isoformat()},
        )

    def turn_from_row(self, row: sqlite3.Row, *, namespace: str) -> ConversationTurn:
        try:
            document = self._cipher.open(
                namespace=namespace,
                memory_id=f"conversation-turn:{row['conversation_id']}:{row['turn_id']}",
                nonce=row["nonce"],
                ciphertext=row["ciphertext"],
            )
            turn = ConversationTurn.model_validate(document)
            if (
                row["content"] != ""
                or turn.digest_content(turn.content) != turn.content_sha256
                or any(
                    document[field] != row[field]
                    for field in (
                        "turn_id",
                        "conversation_id",
                        "sequence",
                        "role",
                        "created_at",
                        "content_sha256",
                    )
                )
            ):
                self.fail("conversation_turn_metadata_mismatch")
            return turn
        except DecryptionAuthError:
            raise
        except (KeyError, IndexError, TypeError, ValueError, RowAuthenticationError):
            self.fail("conversation_turn_authentication_failed")
