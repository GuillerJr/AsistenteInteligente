"""Owned synthetic workspace artifacts; no private home or external service."""

import fcntl
import hashlib
import json
import os
import stat
import threading
import time

import pytest

from jarvis_core.media_attachments import (
    DIRECTORY,
    AttachmentScope,
    AttachmentStore,
)
from jarvis_core.media_documents import MediaError, MediaLimits
from jarvis_core.workspace import WorkspaceTools

WORKSPACE = "b" * 64
SESSION = "a" * 32
ATTACHMENT = "c" * 32


@pytest.fixture
def attachments(tmp_path):
    root = tmp_path.resolve() / "workspace"
    root.mkdir(mode=0o700)
    workspace = WorkspaceTools(root)
    return root, AttachmentStore(workspace, WORKSPACE), AttachmentScope(SESSION, WORKSPACE)


def ingest(store, scope, payload=b"MARCADOR ADJUNTO 8654", identity=ATTACHMENT, **kwargs):
    return store.ingest_bytes(
        scope, payload, "nota.txt", attachment_id=identity, guard=lambda: None, **kwargs
    )


def test_artifact_bytes_manifest_scope_hash_and_permissions_survive_reopen(attachments):
    root, store, scope = attachments
    row = ingest(store, scope)
    assert row.sha256 == hashlib.sha256(b"MARCADOR ADJUNTO 8654").hexdigest()
    assert row.public()["format"] == "jarvis-attachment/1"
    assert "payload" not in row.public() and "path" not in row.public()
    folder = root / DIRECTORY / SESSION
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    for file in folder.iterdir():
        assert stat.S_IMODE(file.stat().st_mode) == 0o600
        assert file.stat().st_nlink == 1
    read = store.read(scope, row.attachment_id, row.sha256, guard=lambda: None)
    assert read.payload == b"MARCADOR ADJUNTO 8654" and read.manifest == row
    payload, manifest = read
    assert payload == read.payload and manifest == row
    store.close()
    fresh = AttachmentStore(WorkspaceTools(root), WORKSPACE)
    assert fresh.list(scope) == (row,)
    assert fresh.read(scope, ATTACHMENT, row.sha256, guard=lambda: None).payload == payload


def test_identical_request_is_idempotent_but_argument_change_is_a_conflict(attachments):
    root, store, scope = attachments
    first = ingest(store, scope)
    before = {
        p.name: (p.stat().st_ino, p.read_bytes()) for p in (root / DIRECTORY / SESSION).iterdir()
    }
    assert ingest(store, scope) == first
    after = {
        p.name: (p.stat().st_ino, p.read_bytes()) for p in (root / DIRECTORY / SESSION).iterdir()
    }
    assert before == after
    with pytest.raises(MediaError, match="media_attachment_conflict"):
        ingest(store, scope, payload=b"CAMBIO")


def test_temporary_attachments_remain_ram_only_and_close_or_clear_withdraws_them(attachments):
    root, store, _ = attachments
    scope = AttachmentScope(SESSION, WORKSPACE, True)
    row = ingest(store, scope)
    assert not list(root.iterdir())
    assert store.list(scope) == (row,)
    assert store.read(scope, ATTACHMENT, row.sha256, guard=lambda: None).payload
    store.clear_temporary(scope)
    assert store.list(scope) == () and not list(root.iterdir())
    with pytest.raises(MediaError, match="media_attachment_unavailable"):
        store.read(scope, ATTACHMENT, row.sha256, guard=lambda: None)
    ingest(store, scope)
    store.close()
    with pytest.raises(MediaError, match="media_scope_unavailable"):
        store.list(scope)
    assert not list(root.iterdir())


def test_ids_cannot_transfer_access_to_another_session_or_workspace(attachments):
    _root, store, scope = attachments
    row = ingest(store, scope)
    other = AttachmentScope("e" * 32, WORKSPACE)
    assert store.list(other) == ()
    with pytest.raises(MediaError, match="media_attachment_unavailable"):
        store.read(other, ATTACHMENT, row.sha256, guard=lambda: None)
    wrong_workspace = AttachmentScope(SESSION, "f" * 64)
    with pytest.raises(MediaError, match="media_scope_unavailable"):
        store.read(wrong_workspace, ATTACHMENT, row.sha256, guard=lambda: None)
    with pytest.raises(MediaError, match="media_attachment_changed"):
        store.read(scope, ATTACHMENT, "f" * 64, guard=lambda: None)


def test_revocation_is_durable_and_preserves_the_original_bytes(attachments):
    root, store, scope = attachments
    row = ingest(store, scope)
    original = root / DIRECTORY / SESSION / (ATTACHMENT + ".blob")
    before = original.read_bytes()
    revoked = store.revoke(scope, ATTACHMENT, row.sha256, guard=lambda: None)
    assert revoked.revoked and original.read_bytes() == before
    assert store.revoke(scope, ATTACHMENT, row.sha256, guard=lambda: None) == revoked
    with pytest.raises(MediaError, match="media_attachment_revoked"):
        store.read(scope, ATTACHMENT, row.sha256, guard=lambda: None)
    fresh = AttachmentStore(WorkspaceTools(root), WORKSPACE)
    assert fresh.list(scope)[0].revoked
    with pytest.raises(MediaError, match="media_attachment_conflict"):
        ingest(fresh, scope)
    assert original.read_bytes() == before


def test_temporary_revocation_withdraws_future_reads_without_disk_writes(attachments):
    root, store, _ = attachments
    scope = AttachmentScope(SESSION, WORKSPACE, True)
    row = ingest(store, scope)
    assert store.revoke(scope, ATTACHMENT, row.sha256, guard=lambda: None).revoked
    with pytest.raises(MediaError, match="media_attachment_revoked"):
        store.read(scope, ATTACHMENT, row.sha256, guard=lambda: None)
    assert not list(root.iterdir())


def test_invalid_scope_names_and_missing_authority_do_not_create_a_store(attachments):
    root, store, scope = attachments
    with pytest.raises(MediaError, match="media_identity_invalid"):
        AttachmentScope("../private", WORKSPACE)
    with pytest.raises(MediaError, match="media_authority_required"):
        store.ingest_bytes(scope, b"X", "nota.txt", attachment_id=ATTACHMENT, guard=None)
    with pytest.raises(MediaError, match="media_name_invalid"):
        store.ingest_bytes(scope, b"X", "../nota.txt", attachment_id=ATTACHMENT, guard=lambda: None)
    assert store.list(scope) == () and not list(root.iterdir())


def test_cancellation_deadline_and_denial_precede_publication(attachments):
    root, store, scope = attachments
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(MediaError, match="media_cancelled"):
        ingest(store, scope, cancellation=cancelled)
    with pytest.raises(MediaError, match="media_timeout"):
        ingest(store, scope, deadline=time.monotonic() - 1)

    def denied():
        raise MediaError("synthetic_denial")

    with pytest.raises(MediaError, match="synthetic_denial"):
        store.ingest_bytes(scope, b"X", "nota.txt", attachment_id=ATTACHMENT, guard=denied)
    assert not list(root.iterdir())


def test_partial_publication_is_unknown_retained_and_not_automatically_repeated(
    attachments,
    monkeypatch,
):
    root, store, scope = attachments
    original_publish = store._publish

    def lose_manifest(root_fd, parent, exact_scope, name, data, guard, cancellation, deadline):
        if name.endswith(".json"):
            raise MediaError("synthetic_manifest_ack_lost")
        return original_publish(
            root_fd, parent, exact_scope, name, data, guard, cancellation, deadline
        )

    monkeypatch.setattr(store, "_publish", lose_manifest)
    with pytest.raises(MediaError, match="media_publication_unknown") as caught:
        ingest(store, scope)
    assert caught.value.effect_status == "unknown" and caught.value.attachment_id == ATTACHMENT
    blob = root / DIRECTORY / SESSION / (ATTACHMENT + ".blob")
    assert blob.read_bytes() == b"MARCADOR ADJUNTO 8654" and store.list(scope) == ()
    before = blob.stat().st_ino
    monkeypatch.setattr(store, "_publish", original_publish)
    with pytest.raises(MediaError, match="media_publication_unknown"):
        ingest(store, scope)
    assert blob.stat().st_ino == before
    assert not list((root / DIRECTORY / SESSION).glob(".stage-*"))


def test_tampered_payload_and_manifest_do_not_reach_a_consumer(attachments):
    root, store, scope = attachments
    row = ingest(store, scope)
    folder = root / DIRECTORY / SESSION
    (folder / (ATTACHMENT + ".blob")).write_bytes(b"TAMPERED")
    with pytest.raises(MediaError, match="media_attachment_changed"):
        store.read(scope, ATTACHMENT, row.sha256, guard=lambda: None)
    manifest = folder / (ATTACHMENT + ".json")
    value = json.loads(manifest.read_bytes())
    value["session_id"] = "d" * 32
    manifest.write_text(json.dumps(value))
    with pytest.raises(MediaError, match="media_manifest_invalid"):
        store.list(scope)


def test_symlink_store_and_hardlinked_blob_are_not_accepted(attachments, tmp_path):
    root, store, scope = attachments
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (root / DIRECTORY).symlink_to(outside, target_is_directory=True)
    with pytest.raises(MediaError, match="media_storage_unavailable"):
        ingest(store, scope)
    assert not list(outside.iterdir())
    (root / DIRECTORY).unlink()
    row = ingest(store, scope)
    blob = root / DIRECTORY / SESSION / (ATTACHMENT + ".blob")
    os.link(blob, outside / "extra-link")
    with pytest.raises(MediaError, match="media_store_unsafe"):
        store.read(scope, ATTACHMENT, row.sha256, guard=lambda: None)


def test_existing_lock_contention_fails_without_waiting_or_borrowing_ownership(attachments):
    root, store, scope = attachments
    row = ingest(store, scope)
    with (root / DIRECTORY / ".lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(MediaError, match="media_store_busy"):
            store.read(scope, ATTACHMENT, row.sha256, guard=lambda: None)
        fcntl.flock(lock, fcntl.LOCK_UN)
    assert store.read(scope, ATTACHMENT, row.sha256, guard=lambda: None)


def test_quotas_count_revoked_originals_instead_of_silently_evicting(attachments):
    root, _store, scope = attachments
    limits = MediaLimits(max_session_attachments=1)
    store = AttachmentStore(WorkspaceTools(root), WORKSPACE, limits=limits)
    row = ingest(store, scope)
    store.revoke(scope, ATTACHMENT, row.sha256, guard=lambda: None)
    with pytest.raises(MediaError, match="media_store_limit"):
        ingest(store, scope, identity="d" * 32)
    assert (root / DIRECTORY / SESSION / (ATTACHMENT + ".blob")).exists()


def test_ingest_workspace_path_is_digest_bound_and_never_adopts_private_area(attachments):
    root, store, scope = attachments
    payload = b"FUENTE CONSENTIDA 9162"
    (root / "fuente.txt").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    row = store.ingest_path(
        scope, "fuente.txt", digest, attachment_id=ATTACHMENT, guard=lambda: None
    )
    assert row.source == "workspace" and row.sha256 == digest
    assert (root / "fuente.txt").read_bytes() == payload
    with pytest.raises(MediaError, match=r"media_workspace_unavailable|media_source_private"):
        store.ingest_path(
            scope,
            DIRECTORY + "/" + SESSION + "/" + ATTACHMENT + ".blob",
            digest,
            attachment_id="d" * 32,
            guard=lambda: None,
        )


def test_workspace_quota_cannot_be_bypassed_by_switching_temporary_storage_pool(attachments):
    root, _store, scope = attachments
    limits = MediaLimits(max_workspace_attachments=1)
    store = AttachmentStore(WorkspaceTools(root), WORKSPACE, limits=limits)
    ingest(store, scope)
    temporary = AttachmentScope("e" * 32, WORKSPACE, True)
    with pytest.raises(MediaError, match="media_store_limit"):
        ingest(store, temporary, identity="d" * 32)
    assert store.list(temporary) == ()


def test_persistent_import_also_accounts_for_retained_temporary_bytes(attachments):
    root, _store, scope = attachments
    limits = MediaLimits(max_workspace_attachments=1)
    store = AttachmentStore(WorkspaceTools(root), WORKSPACE, limits=limits)
    temporary = AttachmentScope("e" * 32, WORKSPACE, True)
    row = ingest(store, temporary)
    with pytest.raises(MediaError, match="media_store_limit"):
        ingest(store, scope, identity="d" * 32)
    assert store.read(temporary, ATTACHMENT, row.sha256, guard=lambda: None).payload
    assert not list((root / DIRECTORY / SESSION).glob("*.blob"))
