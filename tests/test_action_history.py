import asyncio
import base64
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from aegis_core import action_history as history_module
from aegis_core.action_history import ActionHistoryConflict, ActionHistoryStore
from aegis_core.action_history_ipc import ActionHistoryIpcService
from aegis_core.approval_signing import (
    PinnedApprovalSigner,
    action_history_deletion_digest,
    action_history_signature_message,
)
from aegis_core.contracts import (
    TEMPORARY_CONVERSATION_METADATA,
    AgentResult,
    AgentRole,
    PolicyDecision,
    ToolAuthorization,
    ToolExecutionResult,
    UserRequest,
)
from aegis_core.ipc.client import IpcClient
from aegis_core.ipc.peer_identity import JARVIS_CLIENT_METHODS, UNSIGNED_PEER_METHODS
from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.ipc.server import AegisDaemon
from aegis_core.jobs import JobStatus, SwarmJobManager
from aegis_core.memory.conversations import ConversationCoordinator
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.tools.audit import HashChainAuditLog

NOW = 1_800_000_000_000
STAMP = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def authorization(call: str = "synthetic-1", decision=PolicyDecision.ALLOW) -> ToolAuthorization:
    return ToolAuthorization(call_id=call, tool_name="filesystem_read_text", call_digest="a" * 64,
                             decision=decision, reason_code="policy_allowed",
                             normalized_arguments={"path": "DO_NOT_PERSIST_SECRET.txt"})


def result(call: str = "synthetic-1", success: bool = True) -> ToolExecutionResult:
    return ToolExecutionResult(call_id=call, tool_name="filesystem_read_text", success=success,
                               output="DO_NOT_PERSIST_PRIVATE_OUTPUT",
                               error_code=None if success else "execution_timeout",
                               metadata={"url": "https://private.invalid"})


def begin(store: ActionHistoryStore, *, conversation: UUID | None = None) -> UUID:
    request = uuid4()
    store.begin_request(request, conversation_id=conversation, temporary=False)
    return request


def test_metadata_projection_is_private_and_does_not_copy_content(tmp_path: Path) -> None:
    store = ActionHistoryStore(tmp_path / "activity.json", clock=lambda: STAMP)
    conversation = uuid4()
    request = begin(store, conversation=conversation)
    audit = HashChainAuditLog(tmp_path / "audit.jsonl", activity_observer=store)
    audit.record_authorization(request, authorization())
    first = store.page()["entries"][0]
    assert first["state"] == "authorized"
    audit.record_execution(request, result())
    store.finish_request(request)
    page = store.page()
    assert len(page["entries"]) == 1
    assert page["entries"][0]["id"] == first["id"]
    assert page["entries"][0]["state"] == "reported_success"
    assert page["entries"][0]["conversation_id"] == str(conversation)
    assert "correlation" not in page["entries"][0]
    raw = store.path.read_text()
    for secret in ("DO_NOT_PERSIST", "private.invalid", "synthetic-1"):
        assert secret not in raw
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert len(audit.verify()) == 2
    assert ActionHistoryStore(store.path).page()["entries"] == page["entries"]


def test_temporary_unknown_and_finished_requests_cannot_publish(tmp_path: Path) -> None:
    store = ActionHistoryStore(tmp_path / "activity.json")
    temporary = uuid4()
    store.begin_request(temporary, conversation_id=uuid4(), temporary=True)
    for request in (temporary, uuid4()):
        store.record_authorization(request, authorization())
        store.record_execution(request, result())
    request = begin(store)
    store.finish_request(request)
    store.record_execution(request, result())
    assert store.page()["entries"] == []
    assert not store.path.exists()


@pytest.mark.parametrize("restart", [False, True])
def test_pending_becomes_unconfirmed_on_finish_or_restart(tmp_path: Path, restart: bool) -> None:
    store = ActionHistoryStore(tmp_path / "activity.json")
    request = begin(store)
    store.record_authorization(request, authorization(decision=PolicyDecision.REQUIRE_CONFIRMATION))
    before = store.page()
    assert before["entries"][0]["state"] == "awaiting_approval"
    if restart:
        store = ActionHistoryStore(store.path)
    else:
        store.finish_request(request)
    after = store.page()
    assert after["entries"][0]["state"] == "unconfirmed"
    assert after["revision"] != before["revision"]
    assert json.loads(store.path.read_text())["entries"][0]["state"] == "unconfirmed"


def test_denial_and_reported_failure_never_become_success(tmp_path: Path) -> None:
    store = ActionHistoryStore(tmp_path / "activity.json")
    request = begin(store)
    store.record_authorization(request, authorization("denied", PolicyDecision.DENY))
    store.record_execution(request, result("failed", success=False))
    store.record_authorization(request, authorization("failed"))
    store.finish_request(request)
    assert [r["state"] for r in store.page()["entries"]] == ["failed", "denied"]


def test_pagination_filter_and_revision_conflict_preserve_order(tmp_path: Path) -> None:
    store = ActionHistoryStore(tmp_path / "activity.json")
    conversation = uuid4()
    request = begin(store, conversation=conversation)
    for i in range(7):
        store.record_execution(request, result(str(i)))
    first = store.page(limit=3, conversation_id=conversation)
    second = store.page(limit=3, before_sequence=first["next_before_sequence"],
                        revision=UUID(first["revision"]), conversation_id=conversation)
    assert [r["sequence"] for r in first["entries"] + second["entries"]] == [7, 6, 5, 4, 3, 2]
    assert store.page(conversation_id=uuid4())["entries"] == []
    store.record_execution(request, result("new"))
    with pytest.raises(ActionHistoryConflict):
        store.page(before_sequence=second["next_before_sequence"], revision=UUID(first["revision"]))


def test_clear_revokes_inflight_writers_but_new_requests_can_record(tmp_path: Path) -> None:
    store = ActionHistoryStore(tmp_path / "activity.json")
    old = begin(store)
    store.record_authorization(old, authorization())
    page = store.page()
    receipt = store.delete(revision=UUID(page["revision"]), entry_id=None)
    assert receipt["deleted"] == 1
    store.record_execution(old, result())
    store.record_execution(old, result("another_late_call"))
    store.finish_request(old)
    assert store.page()["entries"] == []
    new = begin(store)
    store.record_execution(new, result())
    assert len(store.page()["entries"]) == 1


def test_delete_one_suppresses_only_that_action_and_keeps_security_audit(tmp_path: Path) -> None:
    store = ActionHistoryStore(tmp_path / "activity.json")
    audit = HashChainAuditLog(tmp_path / "audit.jsonl", activity_observer=store)
    request = begin(store)
    audit.record_authorization(request, authorization())
    before = audit.path.read_bytes()
    page = store.page()
    store.delete(revision=UUID(page["revision"]), entry_id=UUID(page["entries"][0]["id"]))
    assert audit.path.read_bytes() == before
    audit.record_execution(request, result())
    audit.record_execution(request, result("other"))
    store.finish_request(request)
    assert len(store.page()["entries"]) == 1
    assert len(audit.verify()) == 3


def test_retention_does_not_resurrect_an_evicted_inflight_action(tmp_path: Path) -> None:
    store = ActionHistoryStore(tmp_path / "activity.json", max_entries=2)
    request = begin(store)
    store.record_authorization(request, authorization("first"))
    store.record_execution(request, result("second"))
    store.record_execution(request, result("third"))
    before = store.page()["entries"]
    store.record_execution(request, result("first"))
    assert store.page()["entries"] == before
    assert store.page()["retention_limit"] == 2


def test_corrupt_or_nonprivate_store_is_preserved_and_audit_keeps_working(tmp_path: Path) -> None:
    path = tmp_path / "activity.json"
    path.write_text("{broken", encoding="utf-8")
    path.chmod(0o600)
    store = ActionHistoryStore(path)
    audit = HashChainAuditLog(tmp_path / "audit.jsonl", activity_observer=store)
    request = begin(store)
    audit.record_execution(request, result())
    assert path.read_text() == "{broken"
    assert store.recording_incomplete
    assert len(audit.verify()) == 1
    with pytest.raises(ValueError):
        store.page()


def test_activity_observer_exception_cannot_change_security_audit(tmp_path: Path) -> None:
    class FailingObserver:
        def record_execution(self, *_args):
            raise RuntimeError("DO_NOT_LOG_PRIVATE_OUTPUT")

    audit = HashChainAuditLog(tmp_path / "audit.jsonl", activity_observer=FailingObserver())
    audit.record_execution(uuid4(), result())
    assert len(audit.verify()) == 1


def test_deletion_conflict_does_not_erase_new_activity(tmp_path: Path) -> None:
    store = ActionHistoryStore(tmp_path / "activity.json")
    request = begin(store)
    store.record_execution(request, result())
    before = store.page()
    store.record_execution(request, result("new"))
    with pytest.raises(ActionHistoryConflict):
        store.delete(revision=UUID(before["revision"]), entry_id=None)
    assert len(store.page()["entries"]) == 2


@pytest.mark.asyncio
async def test_signed_delete_binds_scope_revision_freshness_and_replay(tmp_path: Path) -> None:
    store = ActionHistoryStore(tmp_path / "activity.json")
    request = begin(store)
    store.record_execution(request, result())
    signer = PinnedApprovalSigner(tmp_path / "signer.pub", clock_milliseconds=lambda: NOW)
    key = ec.generate_private_key(ec.SECP256R1())
    signer.register(key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint))
    service = ActionHistoryIpcService(store, approval_signer=signer)
    auth = IpcAuthenticator(b"h" * 32)
    page = store.page()
    target = UUID(page["entries"][0]["id"])
    fields = {"revision": UUID(page["revision"]), "entry_id": target,
              "approval_nonce": "1f" * 16, "issued_at_milliseconds": NOW}
    signature = key.sign(action_history_signature_message(**fields), ec.ECDSA(hashes.SHA256()))
    payload = {**fields, "revision": str(fields["revision"]), "entry_id": str(target),
               "signature": base64.b64encode(signature).decode()}
    for alteration in ({"entry_id": None}, {"revision": str(uuid4())},
                       {"issued_at_milliseconds": NOW - 31_000}):
        response = await service.handle(auth.create_request("actions.history.delete",
                                                            {**payload, **alteration}))
        assert not response.ok
        assert len(store.page()["entries"]) == 1
    stale_fields = {**fields, "issued_at_milliseconds": NOW - 31_000}
    stale_signature = key.sign(action_history_signature_message(**stale_fields),
                               ec.ECDSA(hashes.SHA256()))
    stale = await service.handle(auth.create_request("actions.history.delete", {
        **payload, "issued_at_milliseconds": NOW - 31_000,
        "signature": base64.b64encode(stale_signature).decode(),
    }))
    assert not stale.ok and stale.error_code == "action_history_signature_required"
    response = await service.handle(auth.create_request("actions.history.delete", payload))
    assert response.ok and response.payload["deleted"] == 1
    replay = await service.handle(auth.create_request("actions.history.delete", payload))
    assert not replay.ok and replay.error_code == "action_history_signature_required"
    assert store.page()["entries"] == []


@pytest.mark.asyncio
async def test_ipc_rejects_injected_context_invalid_cursor_and_unsigned_delete(
    tmp_path: Path,
) -> None:
    store = ActionHistoryStore(tmp_path / "activity.json")
    service = ActionHistoryIpcService(store, approval_signer=PinnedApprovalSigner(tmp_path / "key"))
    auth = IpcAuthenticator(b"h" * 32)
    for payload in ({"temporary": False}, {"entries": []}, {"before_sequence": 20},
                    {"limit": True}, {"limit": 51}):
        response = await service.handle(auth.create_request("actions.history.list", payload))
        assert not response.ok
    response = await service.handle(auth.create_request("actions.history.delete", {
        "revision": store.page()["revision"], "entry_id": None,
    }))
    assert not response.ok
    assert not store.path.exists()
    response = await service.handle(auth.create_request("actions.history.list"))
    assert response.ok and response.payload["entries"] == []


def test_delete_digest_vector_matches_swift_and_target_changes_scope() -> None:
    assert action_history_deletion_digest(None) == (
        "07175c55cc4d85786df2740267191857f12cba69f91c6536fb45351fb5228d18"
    )
    assert action_history_deletion_digest(UUID(int=1)) != action_history_deletion_digest(None)


def test_private_store_schema_rejects_duplicate_records(tmp_path: Path) -> None:
    store = ActionHistoryStore(tmp_path / "activity.json")
    request = begin(store)
    store.record_execution(request, result())
    document = json.loads(store.path.read_text())
    document["entries"] *= 2
    store.path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        store.page()


def test_retry_keeps_failure_count_and_latest_execution_report(tmp_path: Path) -> None:
    store = ActionHistoryStore(tmp_path / "activity.json")
    request = begin(store)
    store.record_authorization(request, authorization())
    store.record_execution(request, result(success=False))
    store.record_execution(request, result(success=True))
    store.record_authorization(request, authorization())
    [entry] = store.page()["entries"]
    assert entry["state"] == "reported_success"
    assert entry["execution_count"] == 2 and entry["failure_count"] == 1


class _RecordingGraph:
    def __init__(self, audit: HashChainAuditLog) -> None:
        self.audit = audit

    async def ainvoke(self, payload: dict) -> dict:
        request = payload["request"]
        self.audit.record_authorization(request.request_id, authorization())
        self.audit.record_execution(request.request_id, result())
        return {"final_result": AgentResult(role=AgentRole.SYNTHESIZER, model_id="test/model",
                                             content="Respuesta sintética")}


async def _wait_job(jobs: SwarmJobManager, job_id: UUID) -> None:
    for _ in range(500):
        status = await jobs.status(job_id)
        if status.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
            assert status.status is JobStatus.COMPLETED
            return
        await asyncio.sleep(0.001)
    pytest.fail("synthetic job did not finish")


@pytest.mark.asyncio
async def test_job_admission_records_real_events_but_never_temporary_chat(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    memory = SQLiteMemoryStore(tmp_path / "memory.sqlite3", encryption_secret=b"m" * 32)
    memory.initialize()
    conversations = ConversationCoordinator(memory, namespace="synthetic.owner")
    permanent = await conversations.create("Prueba sintética")
    temporary = await conversations.create("Prueba temporal sintética", temporary=True)
    store = ActionHistoryStore(tmp_path / "activity.json")
    audit = HashChainAuditLog(tmp_path / "audit.jsonl", activity_observer=store)
    jobs = SwarmJobManager(_RecordingGraph(audit), conversations=conversations,
                           action_history=store)
    try:
        for conversation in (permanent, temporary):
            # Spoof the inverse value: only the daemon's actual chat determines admission.
            request = UserRequest(text="Prueba sintética", metadata={
                TEMPORARY_CONVERSATION_METADATA: conversation is permanent,
            })
            job = await jobs.submit(request, conversation_id=conversation.conversation_id)
            await _wait_job(jobs, job.job_id)
    finally:
        await jobs.close()
    [entry] = store.page()["entries"]
    assert entry["conversation_id"] == str(permanent.conversation_id)
    assert entry["state"] == "reported_success"
    assert len(audit.verify()) == 4
    assert not store._requests


@pytest.mark.asyncio
async def test_activity_cleanup_failure_cannot_skip_existing_resource_cleanup(
    tmp_path: Path,
) -> None:
    class BrokenHistory(ActionHistoryStore):
        def finish_request(self, request_id: UUID) -> None:
            raise RuntimeError("synthetic projection failure")

    cleaned: list[UUID] = []

    async def cleanup(request_id: UUID) -> None:
        cleaned.append(request_id)

    store = BrokenHistory(tmp_path / "activity.json")
    audit = HashChainAuditLog(tmp_path / "audit.jsonl", activity_observer=store)
    jobs = SwarmJobManager(_RecordingGraph(audit), action_history=store, request_cleanup=cleanup)
    request = UserRequest(text="Prueba de limpieza sintética")
    try:
        job = await jobs.submit(request)
        await _wait_job(jobs, job.job_id)
    finally:
        await jobs.close()
    assert cleaned == [request.request_id]
    assert len(audit.verify()) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("peer", [None, "ai.aegis.menubar.computer"])
async def test_history_ipc_rejects_unsigned_and_helper_peers(
    tmp_path: Path, peer: str | None,
) -> None:
    store = ActionHistoryStore(tmp_path / "activity.json")
    request = begin(store)
    store.record_execution(request, result())
    service = ActionHistoryIpcService(store, approval_signer=PinnedApprovalSigner(tmp_path / "key"))
    authenticator = IpcAuthenticator(b"i" * 32)
    with tempfile.TemporaryDirectory(prefix="ah-", dir="/private/tmp") as directory:
        path = Path(directory) / "ipc.sock"
        async with AegisDaemon(
            path, authenticator, handlers=service.handlers(), peer_identifier=lambda _peer: peer,
            peer_client_methods=JARVIS_CLIENT_METHODS, unsigned_peer_methods=UNSIGNED_PEER_METHODS,
        ):
            client = IpcClient(path, authenticator)
            for method in service.handlers():
                response = await client.call(method)
                assert not response.ok and response.error_code == "peer_not_trusted"
                assert response.payload == {}
    assert len(store.page()["entries"]) == 1


@pytest.mark.parametrize("clear_all", [False, True])
def test_post_replace_delete_failure_never_restores_removed_activity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clear_all: bool,
) -> None:
    store = ActionHistoryStore(tmp_path / "activity.json")
    request = begin(store)
    store.record_authorization(request, authorization("removed"))
    page = store.page()
    removed_id = UUID(page["entries"][0]["id"])
    original = history_module.write_private_json
    writes = 0

    def uncertain_write(*args, **kwargs):
        nonlocal writes
        original(*args, **kwargs)
        writes += 1
        if writes == 1:
            raise OSError("synthetic directory fsync failure after replacement")

    monkeypatch.setattr(history_module, "write_private_json", uncertain_write)
    with pytest.raises(OSError):
        store.delete(revision=UUID(page["revision"]),
                     entry_id=None if clear_all else removed_id)
    assert store.page()["entries"] == []
    store.record_execution(request, result("removed"))
    store.record_authorization(request, authorization("removed"))
    assert store.page()["entries"] == []
    store.record_execution(request, result("another-call"))
    assert len(store.page()["entries"]) == (0 if clear_all else 1)
    assert all(entry["id"] != str(removed_id) for entry in store.page()["entries"])
