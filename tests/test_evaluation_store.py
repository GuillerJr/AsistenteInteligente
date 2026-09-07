from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from aegis_core.evaluation import (
    APPLICATION_ID,
    SCHEMA_VERSION,
    SQLiteEvaluationStore,
)
from aegis_core.job_contracts import BrainTarget, JobEvaluation


def _evaluation() -> JobEvaluation:
    return JobEvaluation(
        brain=BrainTarget.LOCAL,
        model_id="apple/system-language-model",
        total_latency_ms=120,
        first_partial_latency_ms=40,
        stream_chunks=1,
        succeeded=True,
        outcome_verified=True,
        voice_request=False,
        owner_verified=False,
    )


def test_evaluation_history_is_strictly_scoped_to_one_build_revision(
    tmp_path: Path,
) -> None:
    database = tmp_path / "evaluations.sqlite3"
    first_revision = "a" * 40
    second_revision = "b" * 40
    first = SQLiteEvaluationStore(database, build_revision=first_revision)
    first.initialize()
    first_job = uuid4()
    first.append(first_job, _evaluation(), datetime.now(UTC))

    second = SQLiteEvaluationStore(database, build_revision=second_revision)
    second.initialize()
    assert second.load_recent() == ()
    second_job = uuid4()
    second.append(second_job, _evaluation(), datetime.now(UTC))

    assert tuple(item.job_id for item in first.load_recent()) == (first_job,)
    assert tuple(item.job_id for item in second.load_recent()) == (second_job,)
    with sqlite3.connect(database) as connection:
        revisions = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT build_revision FROM job_evaluations"
            )
        }
    assert revisions == {first_revision, second_revision}


def test_v1_evaluation_rows_migrate_as_legacy_without_polluting_current_metrics(
    tmp_path: Path,
) -> None:
    database = tmp_path / "evaluations.sqlite3"
    legacy_job = uuid4()
    with sqlite3.connect(database) as connection:
        connection.executescript(
            f"""
            PRAGMA application_id = {APPLICATION_ID};
            PRAGMA user_version = 1;
            CREATE TABLE job_evaluations (
                job_id TEXT PRIMARY KEY NOT NULL,
                recorded_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT;
            CREATE INDEX job_evaluations_recorded_at
            ON job_evaluations(recorded_at DESC, job_id DESC);
            """
        )
        connection.execute(
            """
            INSERT INTO job_evaluations(job_id, recorded_at, payload_json)
            VALUES (?, ?, ?)
            """,
            (
                str(legacy_job),
                datetime.now(UTC).isoformat(),
                _evaluation().model_dump_json(),
            ),
        )
    database.chmod(0o600)

    store = SQLiteEvaluationStore(database, build_revision="c" * 40)
    store.initialize()

    assert store.load_recent() == ()
    with sqlite3.connect(database) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        migrated_revision = connection.execute(
            "SELECT build_revision FROM job_evaluations WHERE job_id = ?",
            (str(legacy_job),),
        ).fetchone()[0]
    assert version == SCHEMA_VERSION
    assert migrated_revision == "legacy"
