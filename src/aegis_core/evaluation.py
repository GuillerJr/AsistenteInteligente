from __future__ import annotations

import os
import sqlite3
import stat
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from aegis_core.job_contracts import JobEvaluation, StoredJobEvaluation

SCHEMA_VERSION = 1
APPLICATION_ID = 0x4A45564C


class EvaluationStoreError(RuntimeError):
    """Persistent, privacy-preserving evaluation storage failed."""


class SQLiteEvaluationStore:
    def __init__(self, path: Path, *, max_entries: int = 10_000) -> None:
        if max_entries < 20:
            raise ValueError("evaluation retention must contain at least 20 samples")
        self._path = path
        self._max_entries = max_entries
        self._expected_uid = os.getuid()
        self._lock = threading.RLock()
        self._initialized = False
        self._directory_identity: tuple[int, int] | None = None
        self._database_identity: tuple[int, int] | None = None

    def initialize(self) -> None:
        with self._lock:
            self._prepare_private_directory()
            self._prepare_database_file()
            with self._connect() as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                application_id = int(
                    connection.execute("PRAGMA application_id").fetchone()[0]
                )
                if version == 0:
                    existing = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
                        ).fetchone()[0]
                    )
                    if application_id != 0 or existing:
                        raise EvaluationStoreError(
                            "refusing to modify an unrelated evaluation database"
                        )
                    connection.executescript(
                        f"""
                        PRAGMA application_id = {APPLICATION_ID};
                        PRAGMA user_version = {SCHEMA_VERSION};
                        CREATE TABLE job_evaluations (
                            job_id TEXT PRIMARY KEY NOT NULL,
                            recorded_at TEXT NOT NULL,
                            payload_json TEXT NOT NULL
                        ) STRICT;
                        CREATE INDEX job_evaluations_recorded_at
                        ON job_evaluations(recorded_at DESC, job_id DESC);
                        """
                    )
                elif version != SCHEMA_VERSION or application_id != APPLICATION_ID:
                    raise EvaluationStoreError("evaluation database identity is invalid")
                self._verify_schema(connection)
                if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise EvaluationStoreError("evaluation database integrity check failed")
            self._secure_database_file()
            self._initialized = True

    def append(
        self,
        job_id: UUID,
        evaluation: JobEvaluation,
        recorded_at: datetime,
    ) -> None:
        self._require_initialized()
        if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
            raise EvaluationStoreError("evaluation timestamp must be timezone-aware")
        canonical_time = recorded_at.astimezone(UTC).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO job_evaluations(job_id, recorded_at, payload_json)
                VALUES (?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    recorded_at = excluded.recorded_at,
                    payload_json = excluded.payload_json
                """,
                (str(job_id), canonical_time, evaluation.model_dump_json()),
            )
            connection.execute(
                """
                DELETE FROM job_evaluations
                WHERE job_id IN (
                    SELECT job_id FROM job_evaluations
                    ORDER BY recorded_at DESC, job_id DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (self._max_entries,),
            )
        self._secure_database_file()

    def load_recent(self) -> tuple[StoredJobEvaluation, ...]:
        self._require_initialized()
        with self._lock, self._connect(read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT job_id, recorded_at, payload_json
                FROM job_evaluations
                ORDER BY recorded_at DESC, job_id DESC
                LIMIT ?
                """,
                (self._max_entries,),
            ).fetchall()
        try:
            return tuple(
                StoredJobEvaluation(
                    job_id=UUID(row["job_id"]),
                    recorded_at=datetime.fromisoformat(row["recorded_at"]),
                    evaluation=JobEvaluation.model_validate_json(row["payload_json"]),
                )
                for row in rows
            )
        except (ValueError, TypeError) as error:
            raise EvaluationStoreError("stored evaluation is invalid") from error

    def _prepare_private_directory(self) -> None:
        directory = self._path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = os.lstat(directory)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != self._expected_uid
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise EvaluationStoreError("evaluation directory is not private")
        self._directory_identity = (metadata.st_dev, metadata.st_ino)

    def _prepare_database_file(self) -> None:
        try:
            descriptor = os.open(
                self._path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                0o600,
            )
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        self._secure_database_file()

    def _secure_database_file(self) -> None:
        metadata = os.lstat(self._path)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != self._expected_uid
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) & 0o177
        ):
            raise EvaluationStoreError("evaluation database is not private")
        identity = (metadata.st_dev, metadata.st_ino)
        if self._database_identity is not None and identity != self._database_identity:
            raise EvaluationStoreError("evaluation database identity changed")
        self._database_identity = identity

    def _connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        self._verify_identity()
        target = self._path
        if read_only:
            target = f"file:{self._path}?mode=ro"
        connection = sqlite3.connect(
            target,
            uri=read_only,
            timeout=2,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 2000")
        return connection

    def _verify_identity(self) -> None:
        directory = os.lstat(self._path.parent)
        database = os.lstat(self._path)
        if self._directory_identity != (directory.st_dev, directory.st_ino):
            raise EvaluationStoreError("evaluation directory identity changed")
        if self._database_identity != (database.st_dev, database.st_ino):
            raise EvaluationStoreError("evaluation database identity changed")

    @staticmethod
    def _verify_schema(connection: sqlite3.Connection) -> None:
        objects = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
            )
            if not row["name"].startswith("sqlite_")
        }
        if objects != {"job_evaluations", "job_evaluations_recorded_at"}:
            raise EvaluationStoreError("evaluation schema is invalid")

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise EvaluationStoreError("evaluation store is not initialized")
