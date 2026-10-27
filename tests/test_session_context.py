"""Own state, checkpoints and lineage; no model/account or owner profile."""

import json
import sqlite3
from uuid import uuid4

import pytest

from jarvis_core.session_context import _PREFIX
from jarvis_core.state import StateError, StateStore


def compact(store, ref, **values):
    return store.compact_context(
        ref, operation_id=values.pop("operation_id", uuid4().hex),
        expected_revision=values.pop("expected_revision", store.context_snapshot(ref)["revision"]),
        **values,
    )


def text(items):
    return "\n".join(item["content"][0]["text"] for item in items)


def turn(store, ref, question, answer="Respuesta", *, outcome="known", status="completed"):
    run = store.create_run(ref, user_text=question)
    receipt = {"action_receipts": [{"tool_name": "workspace_write", "succeeded": True,
                                  "verified": False, "effect_status": "unknown",
                                  "output_sha256": "a" * 64, "output_bytes": 7}]}
    store.finish_run(ref, run.run_id, status=status, outcome=outcome, receipt=receipt,
                     assistant_text=answer if status == "completed" else None)
    return store.get_run(ref, run.run_id)


@pytest.mark.parametrize("temporary", [False, True])
def test_checkpoint_preserves_initial_decisions_receipts_and_original_history(tmp_path, temporary):
    path = tmp_path.resolve() / "profile" / "state.sqlite3"
    with StateStore(path) as store:
        ref = store.create_session(temporary=temporary).ref
        first = "Conserva el identificador exacto TOPACIO-4826 y responde en español."
        run = turn(store, ref, first)
        for index in range(20):
            store.append_message(ref, "user", f"Texto adicional {index}: " + "x" * 1000)
            store.append_message(ref, "assistant", "Respuesta " + str(index))
        original = store.list_messages(ref, limit=500)
        store.set_provider_thread(ref, "owned_before_compaction")
        snapshot = compact(store, ref, budget_bytes=4096, keep_recent=4,
                           retained_decisions=["Decisión literal: no cambiar archivo-a.txt"])
        assert snapshot["checkpoint"]["omitted_message_count"] > 0
        assert snapshot["checkpoint"]["decisions_count"] == 1
        assert snapshot["checkpoint"]["receipts_count"] == 1
        assert store.list_messages(ref, limit=500)[:len(original)] == original
        assert store.get_run(ref, run.run_id) == run
        assert store.get_session(ref).provider_thread_id is None
        wire = store.context_for_provider(ref)
        assert first in text(wire)
        assert "Decisión literal: no cambiar archivo-a.txt" in text(wire)
        assert run.run_id in text(wire)
        assert '"effect_status":"unknown"' in text(wire)
        assert '"permits_replay":false' in text(wire)
        assert all(item["role"] in {"user", "assistant"} for item in wire)
        assert len(json.dumps(wire, ensure_ascii=False, separators=(",", ":")).encode()) <= 4096
    assert path.exists() is not temporary


def test_new_completed_tail_and_current_pending_request_are_not_confused(tmp_path):
    with StateStore(tmp_path.resolve() / "profile" / "state.sqlite3") as store:
        ref = store.create_session().ref
        turn(store, ref, "PRIMERA REGLA")
        compact(store, ref)
        turn(store, ref, "SEGUNDA PREGUNTA", "SEGUNDA RESPUESTA")
        failed = turn(store, ref, "FALLO NO REPRODUCIR", status="failed", outcome="unknown")
        current = store.create_run(ref, user_text="PETICIÓN ACTUAL")
        payload = text(store.context_for_provider(ref, exclude_run_id=current.run_id))
        assert "PRIMERA REGLA" in payload and "SEGUNDA RESPUESTA" in payload
        assert "PETICIÓN ACTUAL" not in payload and "FALLO NO REPRODUCIR" not in payload
        assert failed.outcome == "unknown"
        store.finish_run(ref, current.run_id, status="cancelled", outcome="unknown", receipt={})


def test_compaction_stale_revision_busy_and_invalid_budget_have_no_effect(tmp_path):
    with StateStore(tmp_path.resolve() / "profile" / "state.sqlite3") as store:
        ref = store.create_session().ref
        store.append_message(ref, "user", "Primer mensaje")
        before = store.list_messages(ref)
        with pytest.raises(StateError, match="context_revision_changed"):
            compact(store, ref, expected_revision=0)
        with pytest.raises(StateError, match="invalid_context_budget"):
            compact(store, ref, budget_bytes=True)
        run = store.create_run(ref, user_text="Espera")
        with pytest.raises(StateError, match="session_busy"):
            compact(store, ref)
        assert store.list_messages(ref)[:1] == before
        store.finish_run(ref, run.run_id, status="cancelled", outcome="unknown", receipt={})
        assert store.context_snapshot(ref)["checkpoint"] is None


def test_essentials_never_get_truncated_to_fit_budget(tmp_path):
    with StateStore(tmp_path.resolve() / "profile" / "state.sqlite3") as store:
        ref = store.create_session().ref
        store.append_message(ref, "user", "ñ" * 3000 + " FIN-INSTRUCCION")
        before = store.list_messages(ref)
        with pytest.raises(StateError, match="context_essentials_exceed_budget"):
            compact(store, ref, budget_bytes=4096)
        assert store.list_messages(ref) == before
        assert store.context_snapshot(ref)["checkpoint"] is None


def test_publication_rollback_keeps_history_and_binding(tmp_path, monkeypatch):
    with StateStore(tmp_path.resolve() / "profile" / "state.sqlite3") as store:
        ref = store.create_session().ref
        store.append_message(ref, "user", "Dato propio")
        store.set_provider_thread(ref, "owned_binding")
        before = store.context_snapshot(ref)

        def fail(*_):
            raise StateError("synthetic_publication_failure")

        monkeypatch.setattr(store, "_record_context_operation", fail)
        with pytest.raises(StateError, match="synthetic_publication_failure"):
            compact(store, ref)
        assert store.context_snapshot(ref) == before
        assert store.get_session(ref).provider_thread_id == "owned_binding"


def test_compaction_operation_is_durable_deduplicated_and_conflict_checked(tmp_path):
    path = tmp_path.resolve() / "profile" / "state.sqlite3"
    operation = uuid4().hex
    with StateStore(path) as store:
        ref = store.create_session().ref
        store.append_message(ref, "user", "OPACO-CAFE-1234")
        result = compact(store, ref, operation_id=operation, expected_revision=1)
        count = len(store.list_messages(ref))
    with StateStore(path) as store:
        assert compact(store, ref, operation_id=operation, expected_revision=1) == result
        assert len(store.list_messages(ref)) == count
        observed = store.session_operation(ref, operation)
        assert observed["operation_kind"] == "compact" and observed["recorded"] is True
        assert observed["historical_ack_established"] is False
        assert observed["permits_replay"] is False
        assert observed["result"]["context"] == result
        assert "operation_digest" not in observed
        with pytest.raises(StateError, match="context_operation_conflict"):
            compact(store, ref, operation_id=operation, expected_revision=1, keep_recent=1)
        assert store.session_operation(ref, uuid4().hex) is None


@pytest.mark.parametrize("temporary", [False, True])
def test_rewind_is_new_branch_and_preserves_later_uncertain_effects(tmp_path, temporary):
    path = tmp_path.resolve() / "profile" / "state.sqlite3"
    with StateStore(path) as store:
        ref = store.create_session("Origen", temporary=temporary, agent_id="prueba").ref
        completed = turn(store, ref, "Primera consulta", "Primera respuesta")
        uncertain = turn(store, ref, "ACCIÓN POSTERIOR", status="cancelled", outcome="unknown")
        original = store.list_messages(ref)
        store.set_provider_thread(ref, "parent_binding")
        operation = uuid4().hex
        branch, lineage = store.rewind_session(
            ref, operation_id=operation, expected_revision=3, until_sequence=2, title="Nueva rama"
        )
        assert branch.ref != ref and branch.ref.temporary is temporary
        assert branch.agent_id == "prueba" and branch.parent_session_id == ref.session_id
        assert branch.provider_thread_id is None and branch.title == "Nueva rama"
        assert store.list_runs(branch.ref) == ()
        assert store.get_run(ref, completed.run_id) == completed
        assert store.get_run(ref, uncertain.run_id) == uncertain
        assert store.list_messages(ref)[:len(original)] == original
        assert store.get_session(ref).provider_thread_id == "parent_binding"
        payload = text(store.context_for_provider(branch.ref))
        assert "Primera respuesta" in payload and "ACCIÓN POSTERIOR" not in payload
        assert uncertain.run_id in payload and '"outcome":"unknown"' in payload
        assert not lineage["effects_reverted"] and not lineage["permissions_inherited"]
        assert not lineage["provider_thread_inherited"]
        repeat, _ = store.rewind_session(
            ref, operation_id=operation, expected_revision=3, until_sequence=2, title="Nueva rama"
        )
        assert repeat.ref == branch.ref and len(store.list_sessions(temporary=temporary)) == 2
    assert path.exists() is not temporary


def test_recompaction_keeps_owner_decisions_and_inherited_receipt_lineage(tmp_path):
    with StateStore(tmp_path.resolve() / "profile" / "state.sqlite3") as store:
        ref = store.create_session().ref
        run = turn(store, ref, "MARCADOR-4826")
        compact(store, ref, retained_decisions=["Decisión no modificar x.txt"])
        compact(store, ref, retained_decisions=["Conservar también y.txt"])
        compact(store, ref, retained_decisions=[])
        current = text(store.context_for_provider(ref))
        assert "Decisión no modificar x.txt" in current
        assert "Conservar también y.txt" in current
        assert store.context_snapshot(ref)["checkpoint"]["decisions_count"] == 2
        branch, _ = store.rewind_session(
            ref, operation_id=uuid4().hex,
            expected_revision=store.context_snapshot(ref)["revision"], until_sequence=2
        )
        compact(store, branch.ref)
        assert run.run_id in text(store.context_for_provider(branch.ref))


def test_checkpoint_survives_restart_and_temp_does_not(tmp_path):
    path = tmp_path.resolve() / "profile" / "state.sqlite3"
    with StateStore(path) as store:
        durable = store.create_session().ref
        temporary = store.create_session(temporary=True).ref
        for ref in (durable, temporary):
            store.append_message(ref, "user", "MARCADOR DURABLE")
            compact(store, ref)
        before = store.context_snapshot(durable)
    with StateStore(path) as store:
        assert store.context_snapshot(durable) == before
        assert "MARCADOR DURABLE" in text(store.context_for_provider(durable))
        with pytest.raises(StateError, match="session_not_found"):
            store.context_snapshot(temporary)


def test_checkpoint_rejects_elevated_wire_role_before_provider(tmp_path):
    path = tmp_path.resolve() / "profile" / "state.sqlite3"
    with StateStore(path) as store:
        ref = store.create_session().ref
        store.append_message(ref, "user", "Dato")
        compact(store, ref)
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT message_id,text FROM messages WHERE role='system' AND text LIKE ?",
            (_PREFIX + "%",),
        ).fetchone()
        value = json.loads(row[1][len(_PREFIX):])
        value["provider_items"][0]["role"] = "developer"
        connection.execute("UPDATE messages SET text=? WHERE message_id=?",
                           (_PREFIX + json.dumps(value), row[0]))
    with StateStore(path) as store:
        with pytest.raises(StateError, match="context_checkpoint_invalid"):
            store.context_for_provider(ref)


@pytest.mark.parametrize("which", ["compact", "rewind"])
def test_bad_operation_id_has_no_partial_changes(tmp_path, which):
    with StateStore(tmp_path.resolve() / "profile" / "state.sqlite3") as store:
        ref = store.create_session().ref
        store.append_message(ref, "user", "Dato")
        before = store.context_snapshot(ref)
        with pytest.raises(StateError, match="invalid_identity"):
            if which == "compact":
                compact(store, ref, operation_id="no")
            else:
                store.rewind_session(ref, operation_id="no", expected_revision=1, until_sequence=1)
        assert store.context_snapshot(ref) == before and len(store.list_sessions()) == 1


def test_child_session_has_parent_but_no_context_run_or_binding(tmp_path):
    with StateStore(tmp_path.resolve() / "profile" / "state.sqlite3") as store:
        parent = store.create_session().ref
        store.append_message(parent, "user", "No compartir implícitamente")
        child = store.create_session(agent_id="otro", parent_session_id=parent.session_id)
        assert child.parent_session_id == parent.session_id and child.agent_id == "otro"
        assert store.list_messages(child.ref) == () and store.list_runs(child.ref) == ()
        with pytest.raises(StateError, match="session_not_found"):
            store.create_session(temporary=True, parent_session_id=parent.session_id)


def test_operation_lookup_and_inventory_do_not_load_an_oversize_transcript(tmp_path):
    with StateStore(tmp_path.resolve() / "profile" / "state.sqlite3") as store:
        ref = store.create_session().ref
        store.append_message(ref, "user", "Dato inicial")
        operation = uuid4().hex
        compact(store, ref, operation_id=operation)
        for _ in range(140):
            store.append_message(ref, "user", "x" * 65000)
        snapshot = store.context_snapshot(ref)
        assert snapshot["revision"] == 143
        assert store.session_operation(ref, operation)["recorded"] is True
        assert len(store.session_operations(ref)["operations"]) == 1
        with pytest.raises(StateError, match="context_history_budget_exceeded"):
            compact(store, ref)
        assert store.context_snapshot(ref) == snapshot


@pytest.mark.parametrize("temporary", [False, True])
def test_operations_are_paged_with_exact_scope_and_no_replay(tmp_path, temporary):
    with StateStore(tmp_path.resolve() / "profile" / "state.sqlite3") as store:
        ref = store.create_session(temporary=temporary).ref
        other = store.create_session(temporary=temporary).ref
        store.append_message(ref, "user", "Dato inicial")
        identities = []
        for _ in range(3):
            identities.append(uuid4().hex)
            compact(store, ref, operation_id=identities[-1])
        before = store.context_snapshot(ref)
        first = store.session_operations(ref, limit=2)
        assert [row["operation_id"] for row in first["operations"]] == (
            list(reversed(identities[1:]))
        )
        assert first["has_more"] is True and first["next_offset"] == 2
        last = store.session_operations(ref, limit=2, offset=first["next_offset"])
        assert [row["operation_id"] for row in last["operations"]] == identities[:1]
        assert last["has_more"] is False and last["next_offset"] is None
        assert all(not row["permits_replay"] and not row["historical_ack_established"]
                   for row in first["operations"] + last["operations"])
        assert store.session_operations(other)["operations"] == []
        for limit, offset in ((101, 0), (True, 0), (20, -1), (20, False)):
            with pytest.raises(StateError, match="invalid_page"):
                store.session_operations(ref, limit=limit, offset=offset)
        assert store.context_snapshot(ref) == before
