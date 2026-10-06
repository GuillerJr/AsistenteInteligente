import gc
import hashlib
import json
import tracemalloc
from pathlib import Path
from uuid import UUID

import pytest

from aegis_core.tools.audit import AuditIntegrityError, HashChainAuditLog

REQUEST_ID = UUID('11111111-1111-4111-8111-111111111111')


def _large_journal(path: Path, count: int = 4000) -> None:
    previous = ''
    with path.open('w', encoding='utf-8') as handle:
        for sequence in range(1, count + 1):
            body = {
                'sequence': sequence,
                'timestamp': '2026-10-06T12:00:00+00:00',
                'event_type': 'synthetic_measurement',
                'request_id': str(REQUEST_ID),
                'call_id': f'call-{sequence}',
                'tool_name': 'synthetic_test',
                'data': {'index': sequence, 'padding': 'x' * 256},
                'previous_hash': previous,
            }
            canonical = json.dumps(body, ensure_ascii=False, separators=(',', ':'),
                                   sort_keys=True).encode()
            previous = hashlib.sha256(canonical).hexdigest()
            handle.write(json.dumps({**body, 'record_hash': previous}) + '\n')
    path.chmod(0o600)


def test_large_journal_verification_and_append_do_not_retain_history(tmp_path: Path) -> None:
    path = tmp_path / 'audit.jsonl'
    _large_journal(path)
    audit = HashChainAuditLog(path)
    gc.collect()
    tracemalloc.start()
    try:
        assert audit.verify_integrity() == 4000
        audit.record_system_event(REQUEST_ID, event_type='test', component='test',
                                  data={'value': 1})
        assert audit.verify_integrity() == 4001
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # The old materialized record tuple exceeds this budget by several MiB.
    # This heap bound is independent of machine speed and process RSS sampling.
    assert peak < 1_048_576
    assert len(audit.verify()) == 4001


@pytest.mark.parametrize('position', [0, 1999, 3999])
def test_streaming_verification_checks_every_record_and_latches_failure(
    tmp_path: Path, position: int,
) -> None:
    path = tmp_path / 'audit.jsonl'
    _large_journal(path)
    audit = HashChainAuditLog(path)
    assert audit.verify_integrity() == 4000
    original = path.read_text()
    lines = original.splitlines()
    record = json.loads(lines[position])
    record['data']['index'] = -1
    lines[position] = json.dumps(record)
    path.write_text('\n'.join(lines) + '\n')
    with pytest.raises(AuditIntegrityError, match='hash is invalid'):
        audit.verify_integrity()
    path.write_text(original)
    with pytest.raises(AuditIntegrityError, match='trust was revoked'):
        audit.verify_integrity()
    with pytest.raises(AuditIntegrityError, match='trust was revoked'):
        audit.record_system_event(REQUEST_ID, event_type='test', component='test', data={})


@pytest.mark.parametrize('invalid', [b'\xff\n', b'\n', b'{}\n'])
def test_streaming_verification_fails_closed_for_invalid_input(
    tmp_path: Path, invalid: bytes,
) -> None:
    path = tmp_path / 'audit.jsonl'
    path.write_bytes(invalid)
    path.chmod(0o600)
    audit = HashChainAuditLog(path)
    with pytest.raises(AuditIntegrityError):
        audit.verify_integrity()
    path.write_bytes(b'')
    with pytest.raises(AuditIntegrityError, match='trust was revoked'):
        audit.verify_integrity()


def test_streaming_verification_and_full_read_preserve_anchors(tmp_path: Path) -> None:
    path = tmp_path / 'audit.jsonl'
    audit = HashChainAuditLog(path)
    assert audit.verify_integrity() == 0
    audit.record_system_event(REQUEST_ID, event_type='test', component='test', data={})
    original = path.read_bytes()
    assert audit.verify_integrity() == len(audit.verify()) == 1
    path.write_bytes(b'')
    with pytest.raises(AuditIntegrityError, match='rolled back'):
        audit.verify_integrity()
    path.write_bytes(original)
    with pytest.raises(AuditIntegrityError, match='trust was revoked'):
        audit.verify()
