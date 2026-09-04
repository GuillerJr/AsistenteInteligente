from __future__ import annotations

import sqlite3

from aegis_core.memory.codec import MemoryRowCodec
from aegis_core.memory.errors import MemoryStoreError
from aegis_core.memory.graph_repository import GraphRepository


class EncryptedMemoryMigrator:
    """Upgrade legacy stores without leaking cipher concerns into the public facade."""

    def __init__(
        self,
        *,
        row_codec: MemoryRowCodec,
        graph_repository: GraphRepository,
        key_identifier: str,
    ) -> None:
        if len(key_identifier) != 64:
            raise ValueError("memory key identifier is invalid")
        self._row_codec = row_codec
        self._graph_repository = graph_repository
        self._key_identifier = key_identifier

    def migrate_v4_to_v5(self, connection: sqlite3.Connection) -> None:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(memory_items)")}
        encryption_columns = {
            "nonce",
            "ciphertext",
            "source_digest",
            "tags_digest_json",
        }
        if columns & encryption_columns:
            if not encryption_columns.issubset(columns):
                raise MemoryStoreError("memory encryption migration is incomplete")
            security_table = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'memory_security'
                """
            ).fetchone()
            if security_table is None:
                raise MemoryStoreError("memory encryption metadata is missing")
            connection.execute("PRAGMA user_version = 5")
            return
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("ALTER TABLE memory_items ADD COLUMN nonce BLOB")
            connection.execute("ALTER TABLE memory_items ADD COLUMN ciphertext BLOB")
            connection.execute("ALTER TABLE memory_items ADD COLUMN source_digest TEXT")
            connection.execute(
                "ALTER TABLE memory_items ADD COLUMN tags_digest_json TEXT NOT NULL DEFAULT '[]'"
            )
            connection.execute(
                """
                CREATE INDEX memory_items_namespace_source_digest
                ON memory_items(namespace, source_digest)
                """
            )
            connection.execute("DROP TABLE memory_fts")
            connection.execute(
                "CREATE VIRTUAL TABLE memory_fts USING fts5(content, tokenize='ascii')"
            )
            connection.execute(
                """
                CREATE TABLE memory_security (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    key_identifier TEXT NOT NULL CHECK(length(key_identifier) = 64)
                )
                """
            )

            rows = connection.execute(
                """
                SELECT row_id, memory_id, namespace, kind, content, source, tags_json,
                       created_at, updated_at, content_sha256, confidence, evidence,
                       expires_at, last_confirmed_at
                FROM memory_items
                ORDER BY row_id ASC
                """
            ).fetchall()
            for row in rows:
                record = self._row_codec.plaintext_record_from_migration(row)
                nonce, ciphertext, source_digest, tag_digests, blind_content = (
                    self._row_codec.seal_record(record)
                )
                connection.execute(
                    """
                    UPDATE memory_items SET
                        content = '', source = NULL, tags_json = '[]', nonce = ?,
                        ciphertext = ?, source_digest = ?, tags_digest_json = ?
                    WHERE row_id = ?
                    """,
                    (
                        nonce,
                        ciphertext,
                        source_digest,
                        tag_digests,
                        row["row_id"],
                    ),
                )
                connection.execute(
                    "INSERT INTO memory_fts(rowid, content) VALUES (?, ?)",
                    (row["row_id"], blind_content),
                )
            connection.execute(
                "INSERT INTO memory_security(singleton, key_identifier) VALUES (1, ?)",
                (self._key_identifier,),
            )
            connection.execute("PRAGMA user_version = 5")
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise

    def migrate_v5_to_v6(self, connection: sqlite3.Connection) -> None:
        try:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE nodes (
                    node_id TEXT PRIMARY KEY NOT NULL,
                    namespace TEXT NOT NULL,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL
                        CHECK(type IN ('person','project','document','tool','concept')),
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
                    node_id TEXT PRIMARY KEY NOT NULL
                        REFERENCES nodes(node_id) ON DELETE CASCADE,
                    namespace TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
                    created_at TEXT NOT NULL
                ) STRICT;
                CREATE INDEX node_embedding_metadata_namespace_created
                    ON node_embedding_metadata(namespace, created_at, node_id);
                """
            )
            rows = connection.execute(
                """
                SELECT memory_id, namespace, kind, content, source, tags_json,
                       created_at, updated_at, content_sha256, confidence, evidence,
                       expires_at, last_confirmed_at, nonce, ciphertext,
                       source_digest, tags_digest_json
                FROM memory_items
                ORDER BY row_id ASC
                """
            ).fetchall()
            for row in rows:
                self._graph_repository.upsert_record(
                    connection,
                    self._row_codec.record_from_row(row),
                )
            connection.execute("DROP TABLE memory_embeddings")
            connection.execute("PRAGMA user_version = 6")
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise

    def migrate_v6_to_v7(self, connection: sqlite3.Connection) -> None:
        """Rebuild graph tables so physical node types are enforced by SQLite."""

        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                DROP INDEX nodes_type_name;
                DROP INDEX nodes_namespace_type_name;
                DROP INDEX nodes_shared_identity;
                DROP INDEX nodes_document_memory;
                DROP INDEX edges_source_id;
                DROP INDEX edges_target_id;
                DROP INDEX edges_namespace_source;
                DROP INDEX node_embedding_metadata_namespace_created;
                ALTER TABLE edges RENAME TO edges_v6;
                ALTER TABLE node_embedding_metadata RENAME TO node_embedding_metadata_v6;
                ALTER TABLE nodes RENAME TO nodes_v6;
                CREATE TABLE nodes (
                    node_id TEXT PRIMARY KEY NOT NULL,
                    namespace TEXT NOT NULL,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL CHECK(type IN (
                        'person','project','document','tool','concept',
                        'device','sensor','location'
                    )),
                    properties_json TEXT NOT NULL CHECK(json_valid(properties_json)),
                    memory_id TEXT REFERENCES memory_items(memory_id) ON DELETE CASCADE,
                    content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
                    nonce BLOB NOT NULL CHECK(length(nonce) = 12),
                    ciphertext BLOB NOT NULL CHECK(length(ciphertext) >= 17),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                ) STRICT;
                INSERT INTO nodes SELECT * FROM nodes_v6;
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
                INSERT INTO edges SELECT * FROM edges_v6;
                CREATE INDEX edges_source_id ON edges(source_id);
                CREATE INDEX edges_target_id ON edges(target_id);
                CREATE INDEX edges_namespace_source ON edges(namespace, source_id);
                CREATE TABLE node_embedding_metadata (
                    node_id TEXT PRIMARY KEY NOT NULL
                        REFERENCES nodes(node_id) ON DELETE CASCADE,
                    namespace TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
                    created_at TEXT NOT NULL
                ) STRICT;
                INSERT INTO node_embedding_metadata SELECT * FROM node_embedding_metadata_v6;
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
                DROP TABLE edges_v6;
                DROP TABLE node_embedding_metadata_v6;
                DROP TABLE nodes_v6;
                PRAGMA user_version = 7;
                COMMIT;
                """
            )
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise MemoryStoreError("graph migration foreign-key validation failed")
        connection.execute("BEGIN IMMEDIATE")
        try:
            rows = connection.execute(
                """
                SELECT node_id, namespace, name, type, properties_json, memory_id,
                       content_sha256, nonce, ciphertext, created_at, updated_at
                FROM nodes ORDER BY node_id ASC
                """
            ).fetchall()
            for row in rows:
                node = self._graph_repository.node_from_row(row)
                self._graph_repository.replace_property_indices(
                    connection,
                    node_id=node.node_id,
                    namespace=node.namespace,
                    properties=node.properties,
                )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
