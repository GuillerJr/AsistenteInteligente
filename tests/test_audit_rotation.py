from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from aegis_core.audit_anchor import DurableAuditAnchor
from aegis_core.secrets import SecretNotFoundError
from aegis_core.tools.audit import (
    SEGMENT_STARTED_EVENT,
    AuditIntegrityError,
    HashChainAuditLog,
)

REQUEST_ID = UUID("22222222-2222-4222-8222-222222222222")
FIXED_TIME = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
# Roughly three system events fit in one segment.
SEGMENT_BYTES = 1_200


class _AnchorStore:
    def __init__(self) -> None:
        self.anchor: str | None = None

    def get(self) -> str:
        if self.anchor is None:
            raise SecretNotFoundError("missing")
        return self.anchor

    def set(self, anchor: str) -> None:
        self.anchor = anchor


def _log(path: Path, *, archived_segments: int = 16) -> HashChainAuditLog:
    return HashChainAuditLog(
        path,
        clock=lambda: FIXED_TIME,
        max_bytes=SEGMENT_BYTES,
        archived_segments=archived_segments,
    )


def _record(log: HashChainAuditLog, index: int) -> None:
    log.record_system_event(
        REQUEST_ID,
        event_type="tool_execution",
        component="test-suite",
        data={"index": index, "note": "x" * 40},
    )


def _archives(path: Path) -> list[Path]:
    return sorted((path.parent / "audit.archive").glob("audit-*.jsonl"))


def _lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_full_segment_rotates_into_a_linked_read_only_archive(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = _log(path)

    for index in range(10):
        _record(log, index)

    archives = _archives(path)
    assert archives, "the active segment never rotated"
    assert path.stat().st_size <= SEGMENT_BYTES
    assert stat.S_IMODE(archives[0].stat().st_mode) == 0o400
    assert stat.S_IMODE((tmp_path / "audit.archive").stat().st_mode) == 0o700
    first_active = _lines(path)[0]
    newest = _lines(archives[-1])
    assert first_active["event_type"] == SEGMENT_STARTED_EVENT
    assert first_active["previous_hash"] == newest[-1]["record_hash"]
    assert first_active["sequence"] == newest[-1]["sequence"] + 1
    assert first_active["data"]["previous_segment"] == archives[-1].name
    assert (
        first_active["data"]["previous_segment_sha256"]
        == hashlib.sha256(archives[-1].read_bytes()).hexdigest()
    )
    # Every event survives: 10 events plus one start record per rotation.
    assert log.verify_chain() == 10 + len(archives)


def test_sequences_are_contiguous_across_all_segments(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = _log(path)
    for index in range(12):
        _record(log, index)

    sequences = [
        record["sequence"] for segment in [*_archives(path), path] for record in _lines(segment)
    ]
    assert sequences == list(range(1, len(sequences) + 1))


def test_restarted_writer_verifies_the_link_and_keeps_appending(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    first = _log(path)
    for index in range(7):
        _record(first, index)

    restarted = _log(path)
    restarted.verify()
    _record(restarted, 99)

    assert _log(path).verify_chain() == 8 + len(_archives(path))
    assert _lines(path)[-1]["data"]["index"] == 99


def test_tampered_newest_archive_breaks_the_active_link(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = _log(path)
    for index in range(7):
        _record(log, index)
    newest = _archives(path)[-1]
    newest.chmod(0o600)
    newest.write_bytes(newest.read_bytes().replace(b'"index":', b'"index" :', 1))
    newest.chmod(0o400)

    with pytest.raises(AuditIntegrityError):
        _log(path).verify()


def test_tampered_older_archive_is_caught_by_full_chain_verification(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = _log(path)
    for index in range(14):
        _record(log, index)
    archives = _archives(path)
    assert len(archives) >= 3
    oldest = archives[0]
    oldest.chmod(0o600)
    lines = oldest.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[-1])
    record["data"]["index"] = 1_000
    lines[-1] = json.dumps(record)
    oldest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    oldest.chmod(0o400)

    _log(path).verify()  # The active segment only checks its immediate predecessor.
    with pytest.raises(AuditIntegrityError):
        _log(path).verify_chain()


def test_missing_middle_archive_is_reported_as_a_gap(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = _log(path)
    for index in range(14):
        _record(log, index)
    archives = _archives(path)
    assert len(archives) >= 3
    archives[1].unlink()

    with pytest.raises(AuditIntegrityError, match="gap"):
        _log(path).verify_chain()


def test_retention_prunes_only_the_oldest_archives(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = _log(path, archived_segments=2)
    for index in range(20):
        _record(log, index)

    archives = _archives(path)
    assert len(archives) == 2
    numbers = [int(archive.stem.rsplit("-", 1)[1]) for archive in archives]
    assert numbers[1] == numbers[0] + 1
    assert numbers[0] > 1
    assert _log(path, archived_segments=2).verify_chain() > 0


def test_rotation_disabled_keeps_the_capacity_error(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = _log(path, archived_segments=0)

    with pytest.raises(AuditIntegrityError, match="capacity reached"):
        for index in range(10):
            _record(log, index)
    assert not (tmp_path / "audit.archive").exists()
    log.verify()


def test_interrupted_rotation_link_is_discarded_on_the_next_append(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = _log(path)
    _record(log, 0)
    archive_directory = tmp_path / "audit.archive"
    archive_directory.mkdir(mode=0o700)
    # Simulate a crash after linking the active inode but before the rename.
    os.link(path, archive_directory / "audit-000001.jsonl")

    restarted = _log(path)
    restarted.verify()
    for index in range(1, 6):
        _record(restarted, index)

    assert _log(path).verify_chain() == 6 + len(_archives(path))
    names = [archive.name for archive in _archives(path)]
    assert names[0] == "audit-000001.jsonl"
    assert _lines(_archives(path)[0])[0]["sequence"] == 1


def test_durable_anchor_follows_the_rotated_active_segment(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    store = _AnchorStore()
    anchor = DurableAuditAnchor(store)
    log = _log(path)
    for index in range(8):
        _record(log, index)
    anchor.seal_shutdown(log)

    anchor.validate_startup(_log(path))
    assert store.anchor == hashlib.sha256(path.read_bytes()).hexdigest()


def test_fast_append_path_still_detects_history_rewrites(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = _log(path)
    _record(log, 0)
    original = path.read_bytes()
    path.write_bytes(original.replace(b'"index":0', b'"index":7', 1))

    with pytest.raises(AuditIntegrityError, match="history changed"):
        _record(log, 1)


def test_negative_retention_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        HashChainAuditLog(tmp_path / "audit.jsonl", archived_segments=-1)
