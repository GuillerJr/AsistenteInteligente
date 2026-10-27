"""Fork retains owned compacted context and audit, without action authority."""

from uuid import uuid4

import pytest
from test_session_context import compact, text, turn
from test_session_steering_context import correction

from jarvis_core.state import StateError, StateLimits, StateStore


@pytest.mark.parametrize("temporary", [False, True])
def test_full_fork_preserves_compacted_decisions_uncertain_receipts_and_steering(
    tmp_path, temporary
):
    path = tmp_path.resolve() / "profile/state.sqlite3"
    with StateStore(path) as store:
        source = store.create_session(temporary=temporary, agent_id="prueba").ref
        initial = turn(store, source, "Conservar los identificadores exactos")
        uncertain = turn(store, source, "NO REPETIR EFECTO", status="cancelled", outcome="unknown")
        _, correction_id = correction(store, source, "CORRECCIÓN LITERAL TOPACIO-5192", status=None)
        compact(store, source, retained_decisions=["Decisión exacta TOPACIO5192"])
        store.set_provider_thread(source, "owned_source_binding")
        before = store.list_messages(source)
        runs_before = store.list_runs(source)
        operation = uuid4().hex
        revision = store.context_snapshot(source)["revision"]
        branch = store.fork_session(source, expected_revision=revision, operation_id=operation)
        assert branch.ref.temporary is temporary and branch.agent_id == "prueba"
        assert branch.parent_session_id == source.session_id and branch.provider_thread_id is None
        assert store.list_runs(branch.ref) == ()
        assert all(row.run_id is None for row in store.list_messages(branch.ref))
        assert store.list_messages(source)[:len(before)] == before
        assert store.list_runs(source) == runs_before
        assert store.get_session(source).provider_thread_id == "owned_source_binding"
        payload = text(store.context_for_provider(branch.ref))
        for marker in ("TOPACIO5192", "CORRECCIÓN LITERAL TOPACIO-5192", initial.run_id,
                       uncertain.run_id, correction_id):
            assert marker in payload
        assert '"status":"unknown"' in payload and '"instructions_current":false' in payload
        assert '"permissions_inherited":false' in payload and '"permits_replay":false' in payload
        receipt = store.session_operation(source, operation)
        assert receipt["operation_kind"] == "fork"
        assert receipt["result"]["lineage"]["source_revision"] == revision
        assert receipt["result"]["lineage"]["until_sequence"] == revision
        assert receipt["historical_ack_established"] is False
        repeat = store.fork_session(source, expected_revision=revision, operation_id=operation)
        assert repeat.ref == branch.ref and len(store.list_sessions(temporary=temporary)) == 2
    assert path.exists() is not temporary
    if not temporary:
        with StateStore(path) as reopened:
            payload = text(reopened.context_for_provider(branch.ref))
            assert "TOPACIO5192" in payload and correction_id in payload
            observed = reopened.session_operation(source, operation)
            assert observed["result"]["session"]["session_id"] == branch.ref.session_id


@pytest.mark.parametrize("temporary", [False, True])
def test_empty_fork_is_same_mode_empty_and_idempotent(tmp_path, temporary):
    path = tmp_path.resolve() / "profile/state.sqlite3"
    with StateStore(path, limits=StateLimits(max_messages_per_session=1)) as store:
        ref = store.create_session(temporary=temporary).ref
        operation = uuid4().hex
        branch = store.fork_session(ref, expected_revision=0, operation_id=operation)
        assert branch.ref.temporary is temporary and branch.parent_session_id == ref.session_id
        assert store.list_messages(branch.ref) == () and store.list_runs(branch.ref) == ()
        assert store.context_snapshot(branch.ref) == {
            "revision": 0, "message_count": 0, "checkpoint": None,
        }
        record = store.session_operation(ref, operation)
        assert record["result"]["lineage"]["source_revision"] == 0
        assert record["result"]["lineage"]["until_sequence"] == 0
        repeated = store.fork_session(ref, expected_revision=0, operation_id=operation)
        assert repeated.ref == branch.ref and len(store.list_sessions(temporary=temporary)) == 2
    assert path.exists() is not temporary


def test_fork_stale_revision_conflict_and_publication_failure_never_create_another_branch(
    tmp_path, monkeypatch
):
    with StateStore(tmp_path.resolve() / "profile/state.sqlite3") as store:
        ref = store.create_session().ref
        store.append_message(ref, "user", "Dato original")
        with pytest.raises(StateError, match="context_revision_changed"):
            store.fork_session(ref, expected_revision=0, operation_id=uuid4().hex)
        assert len(store.list_sessions()) == 1
        operation = uuid4().hex
        created = store.fork_session(ref, expected_revision=1, operation_id=operation)
        with pytest.raises(StateError, match="context_operation_conflict"):
            store.fork_session(ref, expected_revision=1, operation_id=operation,
                               title="Otro título")
        assert len(store.list_sessions()) == 2 and store.get_session(created.ref)
        before = store.context_snapshot(ref)

        def fail(*_):
            raise StateError("synthetic_owner_publication_failure")

        monkeypatch.setattr(store, "_record_context_operation", fail)
        with pytest.raises(StateError, match="synthetic_owner_publication_failure"):
            store.fork_session(ref, expected_revision=before["revision"], operation_id=uuid4().hex)
        assert store.context_snapshot(ref) == before and len(store.list_sessions()) == 2
