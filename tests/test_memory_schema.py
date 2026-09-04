from __future__ import annotations

import sqlite3

import pytest
import sqlite_vec

from aegis_core.memory.errors import MemoryStoreError
from aegis_core.memory.schema import (
    APPLICATION_ID,
    SCHEMA_VERSION,
    ensure_current_schema,
    verify_current_schema,
)


def _connection_with_vector_extension() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.enable_load_extension(True)
    try:
        sqlite_vec.load(connection)
    finally:
        connection.enable_load_extension(False)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA trusted_schema = OFF")
    return connection


def test_schema_coordinator_creates_current_database_atomically() -> None:
    connection = _connection_with_vector_extension()
    try:
        ensure_current_schema(
            connection,
            key_identifier="a" * 64,
            stateful_migrations={},
        )

        assert connection.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert (
            connection.execute(
                "SELECT key_identifier FROM memory_security WHERE singleton = 1"
            ).fetchone()[0]
            == "a" * 64
        )
        assert connection.in_transaction is False
    finally:
        connection.close()


def test_schema_coordinator_rejects_unrelated_database() -> None:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        connection.execute("CREATE TABLE unrelated(value TEXT)")

        with pytest.raises(MemoryStoreError, match="unrelated database"):
            ensure_current_schema(
                connection,
                key_identifier="a" * 64,
                stateful_migrations={},
            )
    finally:
        connection.close()


def test_schema_coordinator_requires_every_ordered_migration() -> None:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        connection.execute("PRAGMA user_version = 4")

        with pytest.raises(MemoryStoreError, match="migration v4 is unavailable"):
            ensure_current_schema(
                connection,
                key_identifier="a" * 64,
                stateful_migrations={},
            )
    finally:
        connection.close()


def test_schema_coordinator_rejects_migration_that_skips_version_contract() -> None:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        connection.execute("PRAGMA user_version = 4")

        with pytest.raises(MemoryStoreError, match="advance exactly one version"):
            ensure_current_schema(
                connection,
                key_identifier="a" * 64,
                stateful_migrations={4: lambda _connection: None},
            )
    finally:
        connection.close()


def test_schema_verifier_detects_removed_security_index() -> None:
    connection = _connection_with_vector_extension()
    try:
        ensure_current_schema(
            connection,
            key_identifier="a" * 64,
            stateful_migrations={},
        )
        connection.execute("DROP INDEX memory_items_namespace_decay")

        with pytest.raises(MemoryStoreError, match="decay index is unavailable"):
            verify_current_schema(connection)
    finally:
        connection.close()
