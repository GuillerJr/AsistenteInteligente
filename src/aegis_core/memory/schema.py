from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping

from aegis_core.memory.errors import MemoryStoreError

SCHEMA_VERSION = 8
APPLICATION_ID = 0x41454749
SUPPORTED_SCHEMA_VERSIONS = frozenset(range(SCHEMA_VERSION + 1))

Migration = Callable[[sqlite3.Connection], None]

_CURRENT_SCHEMA_SQL = f"""
BEGIN IMMEDIATE;
CREATE TABLE memory_items (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id TEXT NOT NULL UNIQUE,
    namespace TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT,
    tags_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
    evidence TEXT NOT NULL,
    expires_at TEXT,
    last_confirmed_at TEXT,
    nonce BLOB NOT NULL CHECK(length(nonce) = 12),
    ciphertext BLOB NOT NULL CHECK(length(ciphertext) >= 17),
    source_digest TEXT,
    tags_digest_json TEXT NOT NULL
);
CREATE INDEX memory_items_namespace_updated
    ON memory_items(namespace, updated_at DESC);
CREATE INDEX memory_items_namespace_decay
    ON memory_items(namespace, kind, updated_at, confidence);
CREATE INDEX memory_items_namespace_source_digest
    ON memory_items(namespace, source_digest);
CREATE VIRTUAL TABLE memory_fts USING fts5(content, tokenize='ascii');
CREATE TABLE memory_security (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    key_identifier TEXT NOT NULL CHECK(length(key_identifier) = 64)
);
CREATE TABLE nodes (
    node_id TEXT PRIMARY KEY NOT NULL,
    namespace TEXT NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN (
        'person','project','document','tool','concept','device','sensor','location'
    )),
    properties_json TEXT NOT NULL CHECK(json_valid(properties_json)),
    memory_id TEXT REFERENCES memory_items(memory_id) ON DELETE CASCADE,
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
    nonce BLOB NOT NULL CHECK(length(nonce) = 12),
    ciphertext BLOB NOT NULL CHECK(length(ciphertext) >= 17),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;
CREATE INDEX nodes_type_name ON nodes(type, name);
CREATE INDEX nodes_namespace_type_name ON nodes(namespace, type, name);
CREATE UNIQUE INDEX nodes_shared_identity
    ON nodes(namespace, type, name) WHERE memory_id IS NULL;
CREATE UNIQUE INDEX nodes_document_memory
    ON nodes(memory_id) WHERE memory_id IS NOT NULL;
CREATE TABLE edges (
    edge_id TEXT PRIMARY KEY NOT NULL,
    namespace TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
    target_id TEXT NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0 CHECK(weight > 0.0 AND weight <= 10.0),
    memory_id TEXT REFERENCES memory_items(memory_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    UNIQUE(memory_id, source_id, target_id, type)
) STRICT;
CREATE INDEX edges_source_id ON edges(source_id);
CREATE INDEX edges_target_id ON edges(target_id);
CREATE INDEX edges_namespace_source ON edges(namespace, source_id);
CREATE VIRTUAL TABLE node_embeddings USING vec0(
    node_id TEXT PRIMARY KEY,
    namespace_key INTEGER PARTITION KEY,
    embedding FLOAT[384]
);
CREATE TABLE node_embedding_metadata (
    node_id TEXT PRIMARY KEY NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
    namespace TEXT NOT NULL,
    model_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
    created_at TEXT NOT NULL
) STRICT;
CREATE INDEX node_embedding_metadata_namespace_created
    ON node_embedding_metadata(namespace, created_at, node_id);
CREATE TABLE node_property_index (
    node_id TEXT NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
    namespace TEXT NOT NULL,
    property_name TEXT NOT NULL CHECK(
        property_name IN ('ip_address','mac_address','api_token')
    ),
    value_digest TEXT NOT NULL CHECK(length(value_digest) = 64),
    PRIMARY KEY(node_id, property_name)
) STRICT, WITHOUT ROWID;
CREATE INDEX node_property_lookup
    ON node_property_index(namespace, property_name, value_digest);
CREATE TABLE conversations (
    conversation_id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    title TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX conversations_namespace_updated
    ON conversations(namespace, updated_at DESC);
CREATE TABLE conversation_turns (
    turn_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    UNIQUE(conversation_id, sequence)
);
PRAGMA application_id = {APPLICATION_ID};
PRAGMA user_version = {SCHEMA_VERSION};
"""


def ensure_current_schema(
    connection: sqlite3.Connection,
    *,
    key_identifier: str,
    stateful_migrations: Mapping[int, Migration],
) -> None:
    """Create or migrate the Aegis memory schema through ordered, verified steps."""

    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise MemoryStoreError("unsupported memory schema version")
    if version == 0:
        existing_objects = int(
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
        )
        if application_id != 0 or existing_objects:
            raise MemoryStoreError("refusing to modify an unrelated database")
        create_current_schema(connection, key_identifier=key_identifier)
    else:
        if application_id != APPLICATION_ID:
            raise MemoryStoreError("memory database identity is invalid")
        migrations: dict[int, Migration] = {
            1: migrate_v1_to_v2,
            2: migrate_v2_to_v3,
            3: migrate_v3_to_v4,
            7: migrate_v7_to_v8,
            **stateful_migrations,
        }
        while version < SCHEMA_VERSION:
            migration = migrations.get(version)
            if migration is None:
                raise MemoryStoreError(f"memory migration v{version} is unavailable")
            migration(connection)
            next_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if next_version != version + 1:
                raise MemoryStoreError("memory migration did not advance exactly one version")
            version = next_version
    verify_current_schema(connection)


def create_current_schema(
    connection: sqlite3.Connection,
    *,
    key_identifier: str,
) -> None:
    if len(key_identifier) != 64:
        raise MemoryStoreError("memory key identifier is invalid")
    try:
        connection.executescript(_CURRENT_SCHEMA_SQL)
        connection.execute(
            "INSERT INTO memory_security(singleton, key_identifier) VALUES (1, ?)",
            (key_identifier,),
        )
        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise


def migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE memory_embeddings (
            memory_id TEXT PRIMARY KEY REFERENCES memory_items(memory_id) ON DELETE CASCADE,
            model_id TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            vector BLOB NOT NULL,
            content_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX memory_embeddings_model ON memory_embeddings(model_id);
        PRAGMA user_version = 2;
        COMMIT;
        """
    )


def migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE conversations (
            conversation_id TEXT PRIMARY KEY,
            namespace TEXT NOT NULL,
            title TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX conversations_namespace_updated
            ON conversations(namespace, updated_at DESC);
        CREATE TABLE conversation_turns (
            turn_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL
                REFERENCES conversations(conversation_id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            UNIQUE(conversation_id, sequence)
        );
        PRAGMA user_version = 3;
        COMMIT;
        """
    )


def migrate_v3_to_v4(connection: sqlite3.Connection) -> None:
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(memory_items)")}
    connection.execute("BEGIN IMMEDIATE")
    try:
        additions = {
            "confidence": (
                "ALTER TABLE memory_items ADD COLUMN confidence REAL NOT NULL DEFAULT 1.0 "
                "CHECK(confidence >= 0.0 AND confidence <= 1.0)"
            ),
            "evidence": (
                "ALTER TABLE memory_items ADD COLUMN evidence TEXT NOT NULL DEFAULT 'imported'"
            ),
            "expires_at": "ALTER TABLE memory_items ADD COLUMN expires_at TEXT",
            "last_confirmed_at": "ALTER TABLE memory_items ADD COLUMN last_confirmed_at TEXT",
        }
        for name, statement in additions.items():
            if name not in columns:
                connection.execute(statement)
        connection.execute("PRAGMA user_version = 4")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def migrate_v7_to_v8(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE INDEX memory_items_namespace_decay
            ON memory_items(namespace, kind, updated_at, confidence);
        PRAGMA user_version = 8;
        COMMIT;
        """
    )


def verify_current_schema(connection: sqlite3.Connection) -> None:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
    if version != SCHEMA_VERSION or application_id != APPLICATION_ID:
        raise MemoryStoreError("memory schema identity is invalid")
    names = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        )
    }
    required_names = {
        "memory_items",
        "memory_fts",
        "nodes",
        "edges",
        "node_embeddings",
        "node_embedding_metadata",
        "node_property_index",
        "memory_security",
        "conversations",
        "conversation_turns",
    }
    if not required_names.issubset(names):
        raise MemoryStoreError("memory schema is incomplete")
    memory_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(memory_items)")}
    required_memory_columns = {
        "confidence",
        "evidence",
        "expires_at",
        "last_confirmed_at",
        "nonce",
        "ciphertext",
        "source_digest",
        "tags_digest_json",
    }
    if not required_memory_columns.issubset(memory_columns):
        raise MemoryStoreError("memory evolution schema is incomplete")
    indices = {str(row[1]) for row in connection.execute("PRAGMA index_list(memory_items)")}
    if "memory_items_namespace_decay" not in indices:
        raise MemoryStoreError("memory decay index is unavailable")
    node_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(nodes)")}
    if node_columns != {
        "node_id",
        "namespace",
        "name",
        "type",
        "properties_json",
        "memory_id",
        "content_sha256",
        "nonce",
        "ciphertext",
        "created_at",
        "updated_at",
    }:
        raise MemoryStoreError("graph node schema is invalid")
    edge_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(edges)")}
    if edge_columns != {
        "edge_id",
        "namespace",
        "source_id",
        "target_id",
        "type",
        "weight",
        "memory_id",
        "created_at",
    }:
        raise MemoryStoreError("graph edge schema is invalid")
    property_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(node_property_index)")
    }
    if property_columns != {"node_id", "namespace", "property_name", "value_digest"}:
        raise MemoryStoreError("graph property index schema is invalid")
