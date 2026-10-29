"""Real SQLite/filesystem tests, using only explicitly created test directories."""

import json
import os
import sqlite3
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from jarvis_core.state import (
    SessionRef,
    StateError,
    StateLimits,
    StateStore,
    ensure_private_directory,
)


@pytest.fixture
def state_path(tmp_path):
    # /var and /tmp can themselves be aliases on macOS; fixtures use their real
    # private temporary path. Production callers must also supply an explicit path.
    return tmp_path.resolve() / "own-state" / "sessions.sqlite3"


@pytest.fixture
def store(state_path):
    with StateStore(state_path) as value:
        yield value


def error_code(code):
    return pytest.raises(StateError, match=f"^{code}$")


def test_durable_sessions_messages_and_provider_binding_survive_restart(state_path):
    with StateStore(state_path) as first:
        session = first.create_session("Sesión de prueba")
        assert UUID(hex=session.ref.session_id).version == 4
        first.set_provider_thread(session.ref, "thread_own-123")
        message = first.append_message(session.ref, "user", "Hola, prueba sintética")
        first.update_session(session.ref, title="Renombrada", archived=True)
        assert not first.list_sessions()
        assert first.list_sessions(include_archived=True)[0].archived
    with StateStore(state_path) as reopened:
        record = reopened.get_session(session.ref)
        assert record.title == "Renombrada" and record.archived
        assert record.provider_thread_id == "thread_own-123"
        assert reopened.list_messages(session.ref) == (message,)
        assert reopened.update_session(session.ref, archived=False).archived is False
        assert reopened.set_provider_thread(session.ref, None).provider_thread_id is None


def test_temporary_operations_never_open_or_inspect_disk(state_path, monkeypatch):
    connects = []
    connect = sqlite3.connect

    def isolated_connect(path, *args, **kwargs):
        connects.append(path)
        assert path == ":memory:"
        return connect(path, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("Una sesión temporal intentó abrir o consultar el disco")

    monkeypatch.setattr(sqlite3, "connect", isolated_connect)
    monkeypatch.setattr(os, "open", forbidden)
    monkeypatch.setattr(StateStore, "_open_disk", forbidden)
    with StateStore(state_path) as memory:
        session = memory.create_session("Temporal", temporary=True)
        memory.set_provider_thread(session.ref, "thread_temporary")
        memory.append_message(session.ref, "user", "No guardar")
        run = memory.create_run(session.ref)
        memory.set_run_status(session.ref, run.run_id, "running")
        memory.append_event(session.ref, run.run_id, "fixture.read", {"only": "RAM"})
        memory.finish_run(session.ref, run.run_id, status="completed", receipt={"synthetic": True})
        assert memory.list_messages(session.ref)[0].text == "No guardar"
        assert memory.list_events(session.ref, run.run_id)[-1].kind == "run_settled"
        assert memory.list_runs(session.ref)[0].status == "completed"
        assert memory.list_sessions(temporary=True)[0].ref.temporary
        memory.update_session(session.ref, archived=True)
        memory.delete_session(session.ref)
        with error_code("session_not_found"):
            memory.get_session(session.ref)
    with StateStore(state_path) as fresh:
        with error_code("session_not_found"):
            fresh.get_session(session.ref)
    assert connects == [":memory:", ":memory:"]
    assert not state_path.parent.exists()


def test_temporary_activity_does_not_query_an_already_open_durable_database(store):
    durable = store.create_session("Durable")
    statements = []
    store._disk.set_trace_callback(statements.append)
    temporary = store.create_session("Temporal", temporary=True)
    store.append_message(temporary.ref, "assistant", "Texto de prueba")
    store.list_sessions(temporary=True)
    store.list_messages(temporary.ref)
    assert statements == []
    store._disk.set_trace_callback(None)
    assert store.list_messages(durable.ref) == ()


@pytest.mark.parametrize("phase", [
    "queued", "running", "waiting_approval", "cancelling", "cleaning",
])
def test_restart_marks_every_unfinished_phase_unknown_without_replay(state_path, phase):
    with StateStore(state_path) as first:
        ref = first.create_session("Reinicio").ref
        first.set_provider_thread(ref, "thread_before_interruption")
        run = first.create_run(ref)
        if phase != "queued":
            first.set_run_status(ref, run.run_id, "running")
        if phase in {"waiting_approval", "cancelling", "cleaning"}:
            first.set_run_status(ref, run.run_id,
                                 "cancelling" if phase == "cleaning" else phase)
        if phase == "cleaning":
            first.set_run_status(ref, run.run_id, "cleaning")
        first.append_event(ref, run.run_id, "fixture.proposed", {"effect": "uncertain"})
    with StateStore(state_path) as second:
        result = second.get_run(ref, run.run_id)
        assert second.get_session(ref).provider_thread_id is None
        assert result.status == "interrupted" and result.outcome == "unknown"
        assert json.loads(result.receipt_json) == {
            "reason": "service_restart", "effect_status": "unknown", "replay_allowed": False,
        }
        recovered_events = second.list_events(ref, run.run_id)
        assert recovered_events[-1].kind == "run_interrupted"
        assert [event.sequence for event in recovered_events] == list(
            range(1, len(recovered_events) + 1)
        )
        with error_code("run_already_settled"):
            second.finish_run(ref, run.run_id, status="completed", receipt={})
    with StateStore(state_path) as third:
        assert third.list_events(ref, run.run_id) == recovered_events
        assert third.get_run(ref, run.run_id) == result


def test_terminal_receipts_stay_separate_from_model_messages_and_recovery(state_path):
    with StateStore(state_path) as first:
        ref = first.create_session("Recibos").ref
        run = first.create_run(ref)
        first.append_message(ref, "assistant", "El modelo dice que todo está verificado",
                             run_id=run.run_id)
        result = first.finish_run(ref, run.run_id, status="failed", outcome="unknown", receipt={
            "actions": [{"operation_id": uuid4().hex, "verified": False, "effect": "unknown"}],
        })
        events = first.list_events(ref, run.run_id)
    with StateStore(state_path) as reopened:
        assert reopened.get_run(ref, run.run_id) == result
        assert reopened.list_events(ref, run.run_id) == events
        assert not json.loads(result.receipt_json)["actions"][0]["verified"]
        assert reopened.list_messages(ref)[0].role == "assistant"


def test_abrupt_process_exit_releases_owner_lock_and_recovers_committed_run(state_path):
    fixture = '''
import os, sys
from jarvis_core.state import StateStore
state = StateStore(sys.argv[1])
ref = state.create_session("Proceso de prueba interrumpido").ref
run = state.create_run(ref, user_text="Solicitud durable antes de la caída")
state.set_run_status(ref, run.run_id, "running")
print(ref.session_id, run.run_id, flush=True)
os._exit(0)
'''
    process = subprocess.run(
        [sys.executable, "-c", fixture, str(state_path)],
        env={"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        capture_output=True, text=True, timeout=10, check=True,
    )
    session_id, run_id = process.stdout.strip().split()
    with StateStore(state_path) as reopened:
        ref = SessionRef(session_id)
        assert reopened.get_run(ref, run_id).status == "interrupted"
        assert reopened.get_run(ref, run_id).outcome == "unknown"
        messages = reopened.get_run_messages(ref, run_id)
        assert messages[0].text == "Solicitud durable antes de la caída"
        assert reopened.list_events(ref, run_id)[-1].kind == "run_interrupted"


def test_session_and_run_boundaries_prevent_cross_session_attachment(store):
    first = store.create_session("A").ref
    second = store.create_session("B").ref
    run = store.create_run(first)
    with error_code("run_not_found"):
        store.append_message(second, "assistant", "No adjuntar", run_id=run.run_id)
    with error_code("run_not_found"):
        store.append_event(second, run.run_id, "fixture.event", {})
    with error_code("run_not_found"):
        store.finish_run(second, run.run_id, status="completed", receipt={})
    assert store.list_messages(second) == ()
    assert store.list_runs(second) == ()
    assert len(store.list_events(first, run.run_id)) == 1


def test_delete_rejects_active_run_then_cascades_all_session_state(store, state_path):
    ref = store.create_session("Eliminar prueba").ref
    run = store.create_run(ref)
    store.append_message(ref, "user", "Mensaje", run_id=run.run_id)
    with error_code("session_busy"):
        store.delete_session(ref)
    store.finish_run(ref, run.run_id, status="cancelled", receipt={"cleanup": "settled"})
    store.delete_session(ref)
    with error_code("session_not_found"):
        store.get_session(ref)
    with sqlite3.connect(state_path) as database:
        for table in ("sessions", "runs", "messages", "events"):
            assert database.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_real_sql_failure_rolls_back_terminal_receipt_and_event_together(store, state_path):
    ref = store.create_session("Atomicidad").ref
    store.set_provider_thread(ref, "thread_must_remain_on_rollback")
    run = store.create_run(ref)
    with sqlite3.connect(state_path) as fixture:
        fixture.execute("CREATE TRIGGER fixture_reject_settlement BEFORE INSERT ON events "
                        "WHEN NEW.kind='run_settled' BEGIN SELECT RAISE(ABORT, 'fixture'); END")
    with error_code("state_unavailable"):
        store.finish_run(ref, run.run_id, status="completed", receipt={"must": "rollback"},
                         assistant_text="Debe revertirse junto al recibo",
                         clear_provider_thread=True)
    assert store.get_run(ref, run.run_id) == run
    assert store.get_session(ref).provider_thread_id == "thread_must_remain_on_rollback"
    assert store.list_messages(ref) == ()
    assert [event.kind for event in store.list_events(ref, run.run_id)] == ["run_queued"]
    with sqlite3.connect(state_path) as fixture:
        fixture.execute("DROP TRIGGER fixture_reject_settlement")
    assert store.finish_run(ref, run.run_id, status="completed", receipt={}).status == "completed"


def test_unknown_settlement_can_atomically_clear_remote_thread_binding(state_path):
    with StateStore(state_path) as state:
        ref = state.create_session("Hilo ambiguo").ref
        state.set_provider_thread(ref, "own_uncertain_thread")
        run = state.create_run(ref, user_text="Solicitud incierta")
        state.finish_run(ref, run.run_id, status="failed", outcome="unknown",
                         receipt={"error": "provider_interrupted"}, clear_provider_thread=True)
        assert state.get_session(ref).provider_thread_id is None
    with StateStore(state_path) as reopened:
        assert reopened.get_session(ref).provider_thread_id is None
        assert reopened.get_run(ref, run.run_id).outcome == "unknown"
        assert reopened.list_events(ref, run.run_id)[-1].kind == "run_settled"


def test_settlement_stores_assistant_response_once_in_same_transaction(state_path):
    with StateStore(state_path) as state:
        ref = state.create_session("Respuesta atómica").ref
        run = state.create_run(ref)
        state.append_message(ref, "user", "Petición de prueba", run_id=run.run_id)
        result = state.finish_run(ref, run.run_id, status="completed", receipt={"actions": []},
                                  assistant_text="Respuesta de modelo")
        with error_code("run_already_settled"):
            state.finish_run(ref, run.run_id, status="completed", receipt={},
                             assistant_text="No duplicar")
    with StateStore(state_path) as reopened:
        assert reopened.get_run(ref, run.run_id) == result
        messages = reopened.list_messages(ref)
        assert [message.role for message in messages] == ["user", "assistant"]
        assert messages[-1].text == "Respuesta de modelo"
        assert messages[-1].run_id == run.run_id


def test_message_capacity_failure_rolls_back_run_settlement(state_path):
    with StateStore(state_path, limits=StateLimits(max_messages_per_session=1)) as state:
        ref = state.create_session("Capacidad atómica").ref
        run = state.create_run(ref)
        state.append_message(ref, "user", "Llena el presupuesto")
        with error_code("capacity_exceeded"):
            state.finish_run(ref, run.run_id, status="completed", receipt={},
                             assistant_text="Sin sitio")
        assert state.get_run(ref, run.run_id) == run
        assert len(state.list_events(ref, run.run_id)) == 1
        assert len(state.list_messages(ref)) == 1


def test_queued_run_request_and_event_are_atomic_and_duplicate_id_cannot_repeat(store):
    ref = store.create_session("Solicitud atómica").ref
    identity = uuid4().hex
    run = store.create_run(ref, run_id=identity, user_text="Solicitud de prueba")
    assert store.list_messages(ref)[0].run_id == run.run_id == identity
    assert store.list_messages(ref)[0].role == "user"
    assert store.list_events(ref, identity)[0].kind == "run_queued"
    with error_code("run_already_exists"):
        store.create_run(ref, run_id=identity, user_text="No duplicar ni cambiar solicitud")
    assert len(store.list_messages(ref)) == 1
    assert len(store.list_runs(ref)) == 1


def test_run_message_lookup_does_not_depend_on_session_history_prefix(store):
    ref = store.create_session("Buscar solicitud por run").ref
    for index in range(501):
        store.append_message(ref, "user", f"Historial sintético {index}")
    run = store.create_run(ref, user_text="Solicitud después de 500 mensajes")
    store.finish_run(ref, run.run_id, status="completed", receipt={}, assistant_text="Respuesta")
    messages = store.get_run_messages(ref, run.run_id)
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[0].text == "Solicitud después de 500 mensajes"
    assert store.get_run_messages(ref, run.run_id, after=messages[0].sequence) == messages[1:]
    other = store.create_session("Otro contexto").ref
    with error_code("run_not_found"):
        store.get_run_messages(other, run.run_id)


def test_request_capacity_failure_leaves_no_queued_run_or_event(state_path):
    with StateStore(state_path, limits=StateLimits(max_messages_per_session=1)) as state:
        ref = state.create_session("Sin capacidad de solicitud").ref
        state.append_message(ref, "user", "Llena el presupuesto")
        with error_code("capacity_exceeded"):
            state.create_run(ref, user_text="No crear run huérfano")
        assert state.list_runs(ref) == ()
        with sqlite3.connect(state_path) as fixture:
            assert fixture.execute("SELECT count(*) FROM events").fetchone()[0] == 0
        assert len(state.list_messages(ref)) == 1


def test_initial_event_sql_failure_rolls_back_queued_run_and_request(store, state_path):
    ref = store.create_session("Rollback solicitud").ref
    with sqlite3.connect(state_path) as fixture:
        fixture.execute("CREATE TRIGGER fixture_reject_start BEFORE INSERT ON events "
                        "WHEN NEW.kind='run_queued' BEGIN SELECT RAISE(ABORT, 'fixture'); END")
    with error_code("state_unavailable"):
        store.create_run(ref, user_text="No registrar a medias")
    assert store.list_runs(ref) == ()
    assert store.list_messages(ref) == ()


def test_parallel_appends_have_unique_contiguous_run_sequences(store):
    ref = store.create_session("Concurrente").ref
    run = store.create_run(ref)

    def append(index):
        return store.append_event(ref, run.run_id, "fixture.parallel", {"index": index})

    with ThreadPoolExecutor(max_workers=6) as pool:
        events = tuple(pool.map(append, range(40)))
    assert sorted(event.sequence for event in events) == list(range(2, 42))
    assert [event.sequence for event in store.list_events(ref, run.run_id)] == list(range(1, 42))
    assert len({event.event_id for event in events}) == 40


def test_message_and_event_pagination_use_stable_sequences(store):
    ref = store.create_session("Páginas").ref
    run = store.create_run(ref)
    for index in range(5):
        store.append_message(ref, "user", str(index))
        store.append_event(ref, run.run_id, "fixture.page", {"i": index})
    assert [row.sequence for row in store.list_messages(ref, after=2, limit=2)] == [3, 4]
    assert [row.sequence for row in store.list_events(ref, run.run_id, after=3, limit=2)] == [4, 5]
    assert store.list_sessions(limit=1, offset=1) == ()


def test_capacity_is_explicit_and_reserves_a_terminal_event_slot(state_path):
    limits = StateLimits(max_sessions=1, max_messages_per_session=1, max_runs_per_session=1,
                         max_events_per_run=4)
    with StateStore(state_path, limits=limits) as state:
        ref = state.create_session("Límites").ref
        state.append_message(ref, "user", "Uno")
        with error_code("capacity_exceeded"):
            state.append_message(ref, "user", "Dos")
        with error_code("capacity_exceeded"):
            state.create_session("Otra")
        run = state.create_run(ref)
        with error_code("capacity_exceeded"):
            state.create_run(ref)
        state.set_run_status(ref, run.run_id, "running")
        state.append_event(ref, run.run_id, "fixture.last", {})
        with error_code("capacity_exceeded"):
            state.append_event(ref, run.run_id, "fixture.overflow", {})
        state.finish_run(ref, run.run_id, status="completed", receipt={})
        assert len(state.list_events(ref, run.run_id)) == 4


def test_full_event_budget_can_still_record_recovery(state_path):
    limits = StateLimits(max_events_per_run=4)
    with StateStore(state_path, limits=limits) as state:
        ref = state.create_session("Recuperar con presupuesto agotado").ref
        run = state.create_run(ref)
        state.append_event(ref, run.run_id, "fixture.a", {})
        state.append_event(ref, run.run_id, "fixture.b", {})
    with StateStore(state_path, limits=limits) as state:
        assert state.get_run(ref, run.run_id).outcome == "unknown"
        assert state.list_events(ref, run.run_id)[-1].sequence == 4


def test_reduced_budget_on_restart_cannot_block_unknown_outcome_recovery(state_path):
    with StateStore(state_path) as state:
        ref = state.create_session("Presupuesto cambiado").ref
        run = state.create_run(ref)
        for index in range(6):
            state.append_event(ref, run.run_id, "fixture.budget", {"index": index})
    with StateStore(state_path, limits=StateLimits(max_events_per_run=4)) as reopened:
        assert reopened.get_run(ref, run.run_id).outcome == "unknown"
        assert reopened.list_events(ref, run.run_id)[-1].sequence == 8


def test_invalid_payloads_and_oversize_text_do_not_write_partial_data(state_path):
    with StateStore(state_path, limits=StateLimits(max_text_bytes=20, max_json_bytes=64)) as state:
        ref = state.create_session("Prueba").ref
        run = state.create_run(ref)
        for payload in ({"x": float("nan")}, {"x": object()}, {"x": "\ud800"}):
            with error_code("invalid_payload"):
                state.append_event(ref, run.run_id, "fixture.bad", payload)
        cycle = []
        cycle.append(cycle)
        with error_code("invalid_payload"):
            state.append_event(ref, run.run_id, "fixture.cycle", {"x": cycle})
        with error_code("payload_too_large"):
            state.append_event(ref, run.run_id, "fixture.large", {"x": "a" * 64})
        with error_code("text_too_large"):
            state.append_message(ref, "user", "á" * 11)
        with error_code("invalid_text"):
            state.append_message(ref, "user", "\ud800")
        assert state.list_messages(ref) == ()
        assert len(state.list_events(ref, run.run_id)) == 1


@pytest.mark.parametrize("key", ["max_sessions", "max_messages_per_session", "max_runs_per_session",
                                 "max_events_per_run", "max_text_bytes", "max_json_bytes",
                                 "max_page_size"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, 1_000_001])
def test_invalid_limits_rejected_before_any_disk_access(state_path, key, value):
    with error_code("invalid_limits"):
        StateStore(state_path, limits=StateLimits(**{key: value}))
    assert not state_path.parent.exists()


def test_invalid_session_references_and_untyped_values_fail_safely(store):
    with error_code("invalid_identity"):
        SessionRef("../../private")
    with error_code("invalid_temporary_mode"):
        SessionRef(uuid4().hex, 1)
    with error_code("invalid_session_reference"):
        store.get_session(uuid4().hex)
    ref = store.create_session("Validaciones").ref
    with error_code("invalid_role"):
        store.append_message(ref, {}, "x")
    with error_code("invalid_provider_thread"):
        store.set_provider_thread(ref, "../../external-thread")
    with error_code("invalid_provider_thread"):
        store.set_provider_thread(ref, "x" * 257)
    for kwargs in ({"limit": True}, {"limit": 501}, {"after": -1}):
        with error_code("invalid_page"):
            store.list_messages(ref, **kwargs)
    with error_code("invalid_archive_mode"):
        store.update_session(ref, archived=1)
    with error_code("invalid_text"):
        store.update_session(ref, title=" ")


def test_run_transition_and_reserved_event_contracts(store):
    ref = store.create_session("Estados").ref
    run = store.create_run(ref)
    with error_code("invalid_run_transition"):
        store.set_run_status(ref, run.run_id, "waiting_approval")
    with error_code("invalid_run_status"):
        store.set_run_status(ref, run.run_id, [])
    with error_code("reserved_event_kind"):
        store.append_event(ref, run.run_id, "run_settled", {})
    with error_code("invalid_run_outcome"):
        store.finish_run(ref, run.run_id, status="interrupted", receipt={})
    with error_code("run_already_exists"):
        store.create_run(ref, run_id=run.run_id)
    store.finish_run(ref, run.run_id, status="cancelled", receipt={})
    with error_code("run_already_settled"):
        store.append_event(ref, run.run_id, "fixture.late", {})
    with error_code("invalid_run_transition"):
        store.set_run_status(ref, run.run_id, "running")
    store.update_session(ref, archived=True)
    with error_code("session_archived"):
        store.create_run(ref)


def test_private_directory_database_and_lock_permissions(state_path):
    with StateStore(state_path) as state:
        state.create_session("Permisos")
        assert stat.S_IMODE(state_path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
        lock_path = state_path.with_name(state_path.name + ".lock")
        assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
        with sqlite3.connect(state_path) as fixture:
            assert fixture.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
            assert fixture.execute("PRAGMA application_id").fetchone()[0] == int.from_bytes(
                b"JRVS", "big",
            )


def test_existing_public_directory_is_rejected_without_chmod(state_path):
    state_path.parent.mkdir(mode=0o755)
    with StateStore(state_path) as state:
        with error_code("unsafe_state_permissions"):
            state.create_session("No abrir")
    assert stat.S_IMODE(state_path.parent.stat().st_mode) == 0o755
    assert not state_path.exists()


@pytest.mark.parametrize("target", ["database", "lock", "journal", "wal", "shm", "directory"])
def test_symlinks_are_rejected_without_reading_or_changing_targets(state_path, target):
    root = state_path.parent.parent
    sentinel = root / "sentinel"
    sentinel.write_bytes(b"Private fixture. Do not read through a link.")
    sentinel.chmod(0o600)
    if target == "directory":
        actual = root / "actual"
        actual.mkdir(mode=0o700)
        state_path.parent.symlink_to(actual, target_is_directory=True)
    else:
        state_path.parent.mkdir(mode=0o700)
        suffix = {"database": "", "lock": ".lock", "journal": "-journal",
                  "wal": "-wal", "shm": "-shm"}[target]
        state_path.with_name(state_path.name + suffix).symlink_to(sentinel)
    before = sentinel.stat()
    with StateStore(state_path) as state:
        with pytest.raises(StateError):
            state.create_session("No seguir enlaces")
    after = sentinel.stat()
    assert after.st_size == before.st_size and after.st_mtime_ns == before.st_mtime_ns
    assert sentinel.read_bytes() == b"Private fixture. Do not read through a link."


def test_directory_symlink_in_an_ancestor_is_rejected(state_path):
    root = state_path.parent.parent
    actual = root / "actual"
    actual.mkdir(mode=0o700)
    alias = root / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    with error_code("unsafe_state_path"):
        ensure_private_directory(alias / "nested")
    assert not (actual / "nested").exists()


def test_replacement_and_permission_change_are_detected_between_operations(store, state_path):
    ref = store.create_session("Identidad").ref
    original = state_path.with_suffix(".old")
    state_path.rename(original)
    state_path.touch(mode=0o600)
    with error_code("unsafe_state_path"):
        store.get_session(ref)
    state_path.unlink()
    original.rename(state_path)
    assert store.get_session(ref).ref == ref
    state_path.chmod(0o644)
    with error_code("unsafe_state_path"):
        store.get_session(ref)
    state_path.chmod(0o600)


def test_hardlinks_and_fifo_are_rejected(state_path):
    state_path.parent.mkdir(mode=0o700)
    fixture = state_path.parent / "fixture"
    fixture.touch(mode=0o600)
    os.link(fixture, state_path)
    with StateStore(state_path) as state:
        with error_code("unsafe_state_permissions"):
            state.create_session("No hardlink")
    state_path.unlink()
    os.mkfifo(state_path, mode=0o600)
    with StateStore(state_path) as state:
        with error_code("unsafe_state_permissions"):
            state.create_session("No FIFO")


def test_corrupt_database_is_not_replaced_or_reported_with_contents(state_path):
    state_path.parent.mkdir(mode=0o700)
    secret_fixture = b"not-a-database: synthetic-private-content"
    state_path.write_bytes(secret_fixture)
    state_path.chmod(0o600)
    with StateStore(state_path) as state:
        with error_code("state_unavailable") as error:
            state.list_sessions()
    assert state_path.read_bytes() == secret_fixture
    assert "synthetic-private-content" not in str(error.value)
    assert str(state_path) not in str(error.value)


def test_unrelated_sqlite_database_is_rejected_without_schema_mutation(state_path):
    state_path.parent.mkdir(mode=0o700)
    with sqlite3.connect(state_path) as fixture:
        fixture.execute("CREATE TABLE unrelated (fixture TEXT)")
        fixture.execute("INSERT INTO unrelated VALUES ('test-only')")
    state_path.chmod(0o600)
    before = state_path.read_bytes()
    with StateStore(state_path) as state:
        with error_code("invalid_database"):
            state.list_sessions()
    assert state_path.read_bytes() == before


def test_only_one_owner_can_open_durable_database_and_close_releases_lock(state_path):
    with StateStore(state_path) as owner, StateStore(state_path) as competitor:
        ref = owner.create_session("Dueño del estado").ref
        with error_code("state_in_use"):
            competitor.get_session(ref)
        # The rejected owner may still use a genuinely temporary conversation.
        assert competitor.create_session("RAM", temporary=True).ref.temporary
        owner.close()
        assert competitor.get_session(ref).ref == ref
    with StateStore(state_path) as next_owner:
        assert next_owner.get_session(ref).ref == ref


def test_close_is_idempotent_and_closed_store_cannot_reopen(state_path):
    state = StateStore(state_path)
    state.close()
    state.close()
    with error_code("state_closed"):
        state.create_session("Cerrado", temporary=True)
    assert not state_path.parent.exists()


def test_explicit_absolute_path_is_required_and_helper_does_not_change_permissions(state_path):
    with error_code("invalid_state_path"):
        StateStore("relative.sqlite3")
    with error_code("invalid_state_path"):
        ensure_private_directory("relative")
    with error_code("invalid_state_path"):
        StateStore(state_path.parent / ".." / "unsafe.sqlite3")
    assert ensure_private_directory(state_path.parent) == state_path.parent
    state_path.parent.chmod(0o750)
    with error_code("unsafe_state_permissions"):
        ensure_private_directory(state_path.parent)
    assert stat.S_IMODE(state_path.parent.stat().st_mode) == 0o750


def test_attachment_context_failure_rolls_back_run_and_message(state_path, monkeypatch):
    from jarvis_core.state import StateError, StateStore

    store = StateStore(state_path)
    try:
        ref = store.create_session().ref
        original = store._insert_event

        def insert(connection, run_id, kind, payload, **kwargs):
            if kind == "media.references":
                raise StateError("synthetic_attachment_context_failure")
            return original(connection, run_id, kind, payload, **kwargs)

        monkeypatch.setattr(store, "_insert_event", insert)
        with pytest.raises(StateError, match="synthetic_attachment_context_failure"):
            store.create_run(ref, user_text="Solicitud con adjunto", attachment_context={
                "attachments": [{"attachment_id": "a" * 32, "sha256": "b" * 64}]
            })
        assert store.list_runs(ref) == ()
        assert store.list_recent_messages(ref) == ()
    finally:
        store.close()
