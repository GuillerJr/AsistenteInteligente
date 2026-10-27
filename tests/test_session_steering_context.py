"""Own historical steering data survives context cuts without becoming a grant."""

import hashlib
from uuid import uuid4

import pytest

from jarvis_core.state import StateError, StateStore


def correction(store, ref, message, *, status="accepted", malformed=False):
    run = store.create_run(ref, user_text="Solicitud original")
    store.set_run_status(ref, run.run_id, "running")
    identity = uuid4().hex
    store.append_event(ref, run.run_id, "steering.intent", {
        "request_id": identity, "message": message,
        "message_sha256": "0" * 64 if malformed else hashlib.sha256(message.encode()).hexdigest(),
        "thread_id": "owned-thread", "turn_id": "owned-turn", "permits_replay": False})
    if status is not None:
        store.append_event(ref, run.run_id, "steering.receipt", {
            "request_id": identity, "status": status, "permits_replay": False})
    store.finish_run(ref, run.run_id, status="completed", outcome="known",
                     receipt={"action_receipts": []}, assistant_text="Respuesta original")
    return run.run_id, identity


def compact(store, ref):
    return store.compact_context(ref, expected_revision=store.context_snapshot(ref)["revision"],
                                  operation_id=uuid4().hex)


def payload(items):
    return "\n".join(item["content"][0]["text"] for item in items)


@pytest.mark.parametrize("status", ["accepted", "unknown", None])
def test_steering_preserved_before_and_after_compaction_rewind_and_restart(tmp_path, status):
    path = tmp_path.resolve() / "profile/state.sqlite3"
    with StateStore(path) as store:
        ref = store.create_session().ref
        store.append_message(ref, "user", "PRIMERA PETICIÓN CONSERVADA")
        run_id, request_id = correction(
            store, ref, "CORRECCIÓN LITERAL TOPACIO-4826", status=status)
        before = store.list_messages(ref)
        raw = store.context_for_provider(ref)
        assert all(item["role"] in {"user", "assistant"} for item in raw)
        for current in (raw, store.context_for_provider(ref)):
            text = payload(current)
            assert "CORRECCIÓN LITERAL TOPACIO-4826" in text
            assert '"status":"' + (status or "unknown") + '"' in text
            assert '"instructions_current":false' in text
            assert '"historical_ack_established":false' in text
            assert '"permits_replay":false' in text
        compact(store, ref)
        compact(store, ref)
        text = payload(store.context_for_provider(ref))
        assert text.count("CORRECCIÓN LITERAL TOPACIO-4826") == 1
        assert run_id in text and request_id in text
        branch, _ = store.rewind_session(
            ref, expected_revision=store.context_snapshot(ref)["revision"],
            operation_id=uuid4().hex, until_sequence=1)
        compact(store, branch.ref)
        branch_text = payload(store.context_for_provider(branch.ref))
        assert "CORRECCIÓN LITERAL TOPACIO-4826" in branch_text
        assert '"instructions_current":false' in branch_text
        assert branch.ref.session_id != ref.session_id
        assert store.list_runs(branch.ref) == ()
        assert store.list_messages(ref)[:len(before)] == before
        assert store.get_session(branch.ref).provider_thread_id is None
    with StateStore(path) as reopened:
        for target in (ref, branch.ref):
            text = payload(reopened.context_for_provider(target))
            assert "CORRECCIÓN LITERAL TOPACIO-4826" in text
            assert '"status":"' + (status or "unknown") + '"' in text
            assert '"historical_ack_established":false' in text
        assert reopened.list_runs(branch.ref) == ()


def test_new_steering_after_checkpoint_is_added_once_then_deduplicated_on_recompaction(tmp_path):
    with StateStore(tmp_path.resolve() / "profile/state.sqlite3") as store:
        ref = store.create_session().ref
        correction(store, ref, "CORRECCIÓN ANTIGUA")
        compact(store, ref)
        correction(store, ref, "CORRECCIÓN NUEVA", status=None)
        text = payload(store.context_for_provider(ref))
        assert text.count("CORRECCIÓN ANTIGUA") == text.count("CORRECCIÓN NUEVA") == 1
        compact(store, ref)
        text = payload(store.context_for_provider(ref))
        assert text.count("CORRECCIÓN ANTIGUA") == text.count("CORRECCIÓN NUEVA") == 1
        assert '"status":"unknown"' in text


def test_current_run_steering_is_not_injected_again_as_historical_input(tmp_path):
    with StateStore(tmp_path.resolve() / "profile/state.sqlite3") as store:
        ref = store.create_session().ref
        run_id, _ = correction(store, ref, "CORRECCIÓN DEL RUN EXCLUIDO")
        assert "CORRECCIÓN DEL RUN EXCLUIDO" not in payload(
            store.context_for_provider(ref, exclude_run_id=run_id))


def test_essential_corrections_cannot_be_silently_truncated_to_fit_context(tmp_path):
    with StateStore(tmp_path.resolve() / "profile/state.sqlite3") as store:
        ref = store.create_session().ref
        for marker in ("A", "B", "C"):
            correction(store, ref, marker + "x" * 6000)
        before = store.list_messages(ref)
        with pytest.raises(StateError, match="context_essentials_exceed_budget"):
            compact(store, ref)
        with pytest.raises(StateError, match="context_checkpoint_exceeds_budget"):
            store.context_for_provider(ref)
        assert store.list_messages(ref) == before
        assert store.context_snapshot(ref)["checkpoint"] is None


def test_corrupt_correction_digest_fails_before_mutation_or_provider_input(tmp_path):
    with StateStore(tmp_path.resolve() / "profile/state.sqlite3") as store:
        ref = store.create_session().ref
        correction(store, ref, "No otorgar permisos", malformed=True)
        before = store.list_messages(ref)
        with pytest.raises(StateError, match="context_steering_record_invalid"):
            store.context_for_provider(ref)
        with pytest.raises(StateError, match="context_steering_record_invalid"):
            compact(store, ref)
        assert store.list_messages(ref) == before


def test_receipt_without_its_exact_intent_never_invents_an_accepted_correction(tmp_path):
    with StateStore(tmp_path.resolve() / "profile/state.sqlite3") as store:
        ref = store.create_session().ref
        run = store.create_run(ref, user_text="Dato propio")
        store.append_event(ref, run.run_id, "steering.receipt", {
            "request_id": uuid4().hex, "status": "accepted", "permits_replay": False})
        store.finish_run(ref, run.run_id, status="completed", receipt={})
        with pytest.raises(StateError, match="context_steering_record_invalid"):
            store.context_for_provider(ref)


def test_temporary_corrections_disappear_with_their_own_ram_session(tmp_path):
    path = tmp_path.resolve() / "profile/state.sqlite3"
    with StateStore(path) as store:
        ref = store.create_session(temporary=True).ref
        correction(store, ref, "SÓLO TEMPORAL", status=None)
        compact(store, ref)
        assert "SÓLO TEMPORAL" in payload(store.context_for_provider(ref))
    with StateStore(path) as reopened:
        assert reopened.list_sessions(temporary=True) == ()
