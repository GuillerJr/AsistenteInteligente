from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAX_INDEX_TERMS = 24
_HKDF_SALT = b"ai.aegis.memory.row-aead.v1"


class RowAuthenticationError(RuntimeError):
    """Raised when an encrypted memory row cannot be authenticated."""


@dataclass(frozen=True, slots=True)
class SealedMemoryRow:
    nonce: bytes
    ciphertext: bytes


class MemoryRowCipher:
    def __init__(self, raw_secret: bytes) -> None:
        if len(raw_secret) != 32:
            raise ValueError("memory encryption secret must contain exactly 32 bytes")
        self._aead_key = self._derive(raw_secret, b"row-aead")
        self._index_key = self._derive(raw_secret, b"blind-index")
        self._aead = AESGCM(self._aead_key)

    @property
    def key_identifier(self) -> str:
        return hashlib.sha256(b"aegis-memory-key-id\0" + self._aead_key).hexdigest()

    def seal(
        self,
        *,
        namespace: str,
        memory_id: str,
        document: dict[str, Any],
    ) -> SealedMemoryRow:
        plaintext = json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        nonce = os.urandom(12)
        ciphertext = self._aead.encrypt(
            nonce,
            plaintext,
            self._associated_data(namespace=namespace, memory_id=memory_id),
        )
        return SealedMemoryRow(nonce=nonce, ciphertext=ciphertext)

    def open(
        self,
        *,
        namespace: str,
        memory_id: str,
        nonce: object,
        ciphertext: object,
    ) -> dict[str, Any]:
        raw_nonce = bytes(nonce) if isinstance(nonce, (bytes, bytearray, memoryview)) else b""
        raw_ciphertext = (
            bytes(ciphertext)
            if isinstance(ciphertext, (bytes, bytearray, memoryview))
            else b""
        )
        if len(raw_nonce) != 12 or len(raw_ciphertext) < 17:
            raise RowAuthenticationError("encrypted memory row structure is invalid")
        try:
            plaintext = self._aead.decrypt(
                raw_nonce,
                raw_ciphertext,
                self._associated_data(namespace=namespace, memory_id=memory_id),
            )
        except InvalidTag as error:
            raise RowAuthenticationError("encrypted memory row authentication failed") from error
        try:
            document = json.loads(plaintext)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RowAuthenticationError("encrypted memory row payload is invalid") from error
        if not isinstance(document, dict):
            raise RowAuthenticationError("encrypted memory row payload is invalid")
        return document

    def blind_search_document(self, content: str) -> str:
        return " ".join(self._blind_term(term) for term in self._terms(content))

    def blind_query(self, query: str) -> str:
        terms = self._terms(query)
        if not terms:
            raise ValueError("query has no searchable terms")
        return " OR ".join(f'"{self._blind_term(term)}"' for term in terms)

    def blind_exact(self, value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).casefold().encode("utf-8")
        return hmac.new(self._index_key, b"exact\0" + normalized, hashlib.sha256).hexdigest()

    @staticmethod
    def _derive(raw_secret: bytes, scope: bytes) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=_HKDF_SALT,
            info=b"aegis-memory-v1/" + scope,
        ).derive(raw_secret)

    @staticmethod
    def _associated_data(*, namespace: str, memory_id: str) -> bytes:
        return b"aegis-memory-row-v1\0" + namespace.encode("utf-8") + b"\0" + memory_id.encode(
            "ascii"
        )

    def _blind_term(self, term: str) -> str:
        return hmac.new(
            self._index_key,
            b"term\0" + term.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _terms(value: str) -> tuple[str, ...]:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        terms: list[str] = []
        for term in re.findall(r"\w+", normalized, flags=re.UNICODE):
            bounded = term[:64]
            if bounded not in terms:
                terms.append(bounded)
            if len(terms) == MAX_INDEX_TERMS:
                break
        return tuple(terms)
