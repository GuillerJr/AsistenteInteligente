from __future__ import annotations


class MemoryStoreError(RuntimeError):
    """Base error for persistent memory operations."""


class MemorySecurityError(MemoryStoreError):
    pass


class DecryptionAuthError(MemorySecurityError):
    """Fatal signal that an encrypted memory row or its namespace was tampered with."""


class MemoryCapacityError(MemoryStoreError):
    pass


class DatabaseCapacityError(MemoryCapacityError):
    """An atomic FIFO eviction/write transaction could not be completed."""


class MemoryNotFoundError(MemoryStoreError):
    pass


class MemoryQueryError(MemoryStoreError):
    pass


class SecretMaterialError(MemoryStoreError):
    pass


class ConversationCapacityError(MemoryStoreError):
    pass
