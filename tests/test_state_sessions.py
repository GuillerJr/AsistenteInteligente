"""Own SQLite v1 migration and forks of synthetic completed conversations."""
import sqlite3

import pytest

from jarvis_core.state import StateError, StateStore


@pytest.mark.parametrize("temporary", [False, True])
def test_fork_copies_completed_text_without_effects_or_bindings(tmp_path, temporary):
    path = tmp_path.resolve() / "profile" / "state.sqlite3"
    with StateStore(path) as store:
        ref = store.create_session("Origen", temporary=temporary, agent_id="agent_prueba").ref
        run = store.create_run(ref, user_text="Pregunta completada")
        store.finish_run(ref, run.run_id, status="completed", receipt={"action": "synthetic"},
                         assistant_text="Respuesta completada", outcome="known")
        failed = store.create_run(ref, user_text="No reproducir esta solicitud fallida")
        store.finish_run(ref, failed.run_id, status="failed", receipt={}, outcome="unknown")
        store.set_provider_thread(ref, "owned_parent_thread")
        branch = store.fork_session(ref, title="Rama de prueba")
        assert branch.ref.temporary is temporary
        assert branch.agent_id == "agent_prueba" and branch.parent_session_id == ref.session_id
        assert branch.provider_thread_id is None and branch.title == "Rama de prueba"
        assert [message.text for message in store.list_messages(branch.ref)
                if message.role in {"user", "assistant"}] == [
            "Pregunta completada", "Respuesta completada"]
        assert all(message.run_id is None for message in store.list_messages(branch.ref))
        assert store.list_runs(branch.ref) == ()
        assert store.get_session(ref).provider_thread_id == "owned_parent_thread"
    assert path.exists() is not temporary


def test_fork_cutoff_and_busy_parent_are_atomic(tmp_path):
    with StateStore(tmp_path.resolve() / "profile" / "state.sqlite3") as store:
        ref = store.create_session().ref
        store.append_message(ref, "user", "primero")
        store.append_message(ref, "assistant", "segundo")
        branch = store.fork_session(ref, until_sequence=1)
        assert [message.text for message in store.list_messages(branch.ref)
                if message.role in {"user", "assistant"}] == ["primero"]
        before = store.list_sessions()
        with pytest.raises(StateError, match="message_not_found"):
            store.fork_session(ref, until_sequence=999)
        run = store.create_run(ref, user_text="pendiente")
        with pytest.raises(StateError, match="session_busy"):
            store.fork_session(ref)
        assert store.list_sessions() == before
        store.finish_run(ref, run.run_id, status="cancelled", receipt={}, outcome="unknown")


def test_fork_rejects_excess_context_without_partial_session(tmp_path):
    with StateStore(tmp_path.resolve() / "profile" / "state.sqlite3") as store:
        ref = store.create_session().ref
        for _ in range(5):
            store.append_message(ref, "user", "x" * 60000)
        with pytest.raises(StateError, match="fork_context_too_large"):
            store.fork_session(ref)
        assert len(store.list_sessions()) == 1


def test_v1_owned_database_migrates_without_losing_history(tmp_path):
    path = tmp_path.resolve() / "profile" / "state.sqlite3"
    with StateStore(path) as store:
        ref = store.create_session("Conversación propia").ref
        store.append_message(ref, "user", "dato sintético")
    # Reconstruct the prior own v1 schema from a disposable v2 store. No owner
    # database or source checkout is needed by this migration fixture.
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE sessions DROP COLUMN parent_session_id")
        connection.execute("ALTER TABLE sessions DROP COLUMN agent_id")
        connection.execute("PRAGMA user_version=1")
    with StateStore(path) as store:
        session = store.get_session(ref)
        assert session.agent_id == "jarvis" and session.parent_session_id is None
        assert [message.text for message in store.list_messages(ref)] == ["dato sintético"]
        branch = store.fork_session(ref)
        assert branch.agent_id == "jarvis"
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
