"""Own synthetic-profile coordination tests; no public model or owner data."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

import jarvis_core.memory_intelligence as intelligence_module
from jarvis_core.local_embeddings import ENGINE_FINGERPRINT
from jarvis_core.memory import MemoryScope, MemoryStore
from jarvis_core.memory_intelligence import MemoryIntelligence, MemoryIntelligenceError
from jarvis_core.memory_policy import MemoryPolicyError
from jarvis_core.memory_snapshot import scope_generation, snapshot_scope
from jarvis_core.model_artifacts import ArtifactError
from jarvis_core.protocol import authenticated_client
from jarvis_core.state import SessionRef

CLIENT = "a" * 64
WORKSPACE = "b" * 64


class MemorySource:
    def __init__(self, path):
        self.raw = MemoryStore(path)

    def snapshot(self, scope, **options):
        return snapshot_scope(self.raw, scope, **options)

    def generation(self, scope):
        return scope_generation(self.raw, scope)

    def __getattr__(self, name):
        return getattr(self.raw, name)


class Gate:
    def __init__(self):
        self.active = set()
        self.busy = False

    def hold(self, label, **options):
        if self.busy:
            raise MemoryIntelligenceError("memory_profile_busy")
        token = SimpleNamespace()
        self.active.add(id(token))
        token.close = lambda: self.active.discard(id(token))
        return token


class SyntheticEncoder:
    model_fingerprint = ENGINE_FINGERPRINT
    dimension = 384

    def __init__(self, directory, **options):
        self.closed = False
        self.document_calls = 0
        self.query_calls = 0

    async def embed_query(self, text, **options):
        self.query_calls += 1
        return (1.0,) + (0.0,) * 383

    async def embed_document(self, text, **options):
        self.document_calls += 1
        return (
            SimpleNamespace(
                start_byte=0,
                end_byte=len(text.encode()),
                token_count=10,
                vector=(1.0,) + (0.0,) * 383,
            ),
        )

    async def close(self):
        self.closed = True
        return {"closed": True, "cleanup_confirmed": True}


class FixtureService:
    def __init__(self, profile):
        self.directory = profile
        self.profile_gate = Gate()
        self.profile_lease = SimpleNamespace(
            assert_current=lambda: None, child_custody_fds=lambda: ()
        )
        self._workspace_id = WORKSPACE
        self.revoked = set()

        def lookup(client, *, scope):
            if client != CLIENT or client in self.revoked:
                raise MemoryIntelligenceError("memory_owner_revoked")

        self.client_authority = SimpleNamespace(lookup=lookup)
        self.sessions = {}
        self.store = SimpleNamespace(get_session=self.get_session)
        self.memory = MemorySource(profile / "memory.sqlite3")

    def get_session(self, ref):
        if ref not in self.sessions:
            raise MemoryIntelligenceError("memory_session_changed")
        return self.sessions[ref]

    def _ref(self, params):
        ref = SessionRef(params["session_id"], params.get("temporary", False))
        self.get_session(ref)
        return ref

    def _memory_scope(self, ref):
        return MemoryScope(
            self.get_session(ref).agent_id,
            self._workspace_id,
            ref.session_id if ref.temporary else None,
            ref.temporary,
        )


@pytest.fixture
def context(tmp_path, monkeypatch):
    profile = tmp_path.resolve() / "profile"
    profile.mkdir(mode=0o700)
    service = FixtureService(profile)
    ref = SessionRef(uuid4().hex)
    service.sessions[ref] = SimpleNamespace(agent_id="jarvis")
    metadata = {"ready": True, "model_fingerprint": "d" * 64, "bytes": 42}
    monkeypatch.setattr(intelligence_module, "inspect_model", lambda *a, **k: dict(metadata))
    token = authenticated_client.set(CLIENT)
    facade = MemoryIntelligence(service, engine_factory=SyntheticEncoder)
    monkeypatch.setattr(facade, "_model_files_signature", lambda: ((1, 2, 3, 4, 5),))
    yield service, facade, ref, metadata
    authenticated_client.reset(token)
    service.memory.close()


def base(ref):
    return {"session_id": ref.session_id, "temporary": ref.temporary}


def add(service, ref, key, text):
    return service.memory.upsert(
        service._memory_scope(ref),
        key,
        text,
        title="Título sintético",
        source_uri="jarvis://synthetic/" + key,
    )


async def policy(facade, ref, *, recall=False, auto=False, mode="lexical"):
    status = (await facade.status(base(ref)))["intelligence"]["policy"]
    review = (
        await facade.review_policy(
            {
                **base(ref),
                "settings": {
                    "auto_recall_enabled": recall,
                    "auto_index_enabled": auto,
                    "mode": mode,
                    "source_policy": "approved_notes_only",
                },
                "expected_revision": status["revision"],
                "expected_digest": status["digest"],
            }
        )
    )["review"]
    return await facade.apply_policy(
        {**base(ref), "review_id": review["review_id"], "review_digest": review["review_digest"]}
    )


async def settled(facade, ref, job_id):
    for _ in range(100):
        value = (await facade.get_job({**base(ref), "job_id": job_id}))["job"]
        if value["phase"] not in {"queued", "running", "cancelling"}:
            return value
        await asyncio.sleep(0.005)
    raise AssertionError("synthetic job did not settle")


async def test_disabled_status_preserves_cold_memory_custody(context):
    service, facade, ref, _metadata = context
    try:
        assert service.memory.raw._disk is None
        before = set(service.directory.iterdir())
        value = (await facade.status(base(ref)))["intelligence"]
        assert value["index"]["state"] == "not_opened"
        assert value["index"]["generation"] is None
        assert facade.model_directory == facade.model_root / "initial"
        assert not facade.model_root.exists()
        assert service.memory.raw._disk is None
        assert set(service.directory.iterdir()) == before
        add(service, ref, "note", "Nota aprobada")
        value = (await facade.status(base(ref)))["intelligence"]
        assert value["index"]["state"] == "absent"
        assert value["index"]["source_generation"] == 1
    finally:
        await facade.close()


async def test_missing_local_model_is_visible_without_download_or_lexical_fallback(
    context, monkeypatch
):
    service, facade, ref, _metadata = context
    add(service, ref, "note", "TOPACIO dato aprobado")

    def absent(*args, **kwargs):
        raise ArtifactError("missing_model")

    monkeypatch.setattr(intelligence_module, "inspect_model", absent)
    try:
        with pytest.raises(ArtifactError, match="missing_model"):
            await facade.search(ref, CLIENT, "TOPACIO", mode="semantic_local")
        assert facade._engine is None
        await policy(facade, ref, recall=True, mode="hybrid_local")
        value = await facade.prepare_recall(
            SimpleNamespace(run_id=uuid4().hex, message="TOPACIO"), ref, CLIENT
        )
        assert value.text == "" and value.status["status"] == "unavailable"
        assert value.status["error"] == "missing_model"
        assert not facade.cache_directory.exists()
        assert facade._engine is None
    finally:
        await facade.close()


async def test_manual_local_search_without_opt_in_and_page_scope_query_cas(context):
    service, facade, ref, _metadata = context
    add(service, ref, "one", "ALFA nota uno")
    add(service, ref, "two", "ALFA nota dos")
    try:
        first = await facade.search(ref, CLIENT, "consulta", mode="semantic_local", limit=1)
        assert first.hits and first.index_fingerprint
        assert not facade._policy(ref, CLIENT).settings.auto_recall_enabled
        second = await facade.search(
            ref,
            CLIENT,
            "consulta",
            mode="semantic_local",
            limit=1,
            offset=first.next_offset,
            expected_generation=first.generation,
            expected_index_fingerprint=first.index_fingerprint,
        )
        assert second.hits[0].document.document_id != first.hits[0].document.document_id
        for change in ({"query": "otra"}, {"limit": 2}):
            args = {"query": "consulta", "limit": 1, **change}
            with pytest.raises(MemoryIntelligenceError, match="memory_page_changed"):
                await facade.search(
                    ref,
                    CLIENT,
                    mode="semantic_local",
                    offset=first.next_offset,
                    expected_generation=first.generation,
                    expected_index_fingerprint=first.index_fingerprint,
                    **args,
                )
        await policy(facade, ref, mode="hybrid_local")
        with pytest.raises(MemoryIntelligenceError, match="memory_page_changed"):
            await facade.search(
                ref,
                CLIENT,
                "consulta",
                mode="semantic_local",
                limit=1,
                offset=first.next_offset,
                expected_generation=first.generation,
                expected_index_fingerprint=first.index_fingerprint,
            )
    finally:
        await facade.close()
    assert not service.profile_gate.active


async def test_recall_default_disabled_current_every_turn_and_revoked_before_io(context):
    service, facade, ref, _metadata = context
    add(service, ref, "one", "TOPACIO cuarenta y dos")
    turn = SimpleNamespace(run_id=uuid4().hex, message="TOPACIO")
    try:
        disabled = await facade.prepare_recall(turn, ref, CLIENT)
        assert disabled.text == "" and disabled.status["status"] == "disabled"
        await policy(facade, ref, recall=True)
        first = await facade.prepare_recall(turn, ref, CLIENT)
        assert "TOPACIO" in first.text and len(first.text.encode()) <= 8000
        first.assert_current()
        second = await facade.prepare_recall(replace_context(turn, message="cuarenta"), ref, CLIENT)
        assert first.fingerprint == second.fingerprint
        add(service, ref, "one", "TOPACIO corregido")
        with pytest.raises(MemoryIntelligenceError, match="memory_generation_changed"):
            first.assert_current()
        third = await facade.prepare_recall(turn, ref, CLIENT)
        assert third.fingerprint != first.fingerprint and "corregido" in third.text
        await policy(facade, ref, recall=False)
        with pytest.raises(MemoryPolicyError, match="memory_policy_changed"):
            third.assert_current()
        assert (await facade.status(base(ref)))["intelligence"]["last_recall"] is None
    finally:
        await facade.close()


def replace_context(value, **changes):
    return SimpleNamespace(**{**vars(value), **changes})


async def test_jobs_same_id_no_replay_conflict_and_owner_scope(context):
    service, facade, ref, _metadata = context
    add(service, ref, "one", "Nota para derivar")
    generation = service.memory.generation(service._memory_scope(ref))
    snapshot = facade._policy(ref, CLIENT)
    params = {
        **base(ref),
        "request_id": uuid4().hex,
        "expected_generation": generation,
        "expected_policy_revision": snapshot.revision,
        "expected_model_fingerprint": ENGINE_FINGERPRINT,
    }
    try:
        first = (await facade.rebuild_index(params))["job"]
        second = (await facade.rebuild_index(params))["job"]
        assert first["id"] == second["id"] == params["request_id"]
        result = await settled(facade, ref, first["id"])
        assert result["phase"] == "complete" and result["receipt"]["documents"] == 1
        calls = facade._engine.document_calls
        assert (await facade.rebuild_index(params))["job"]["phase"] == "complete"
        assert facade._engine.document_calls == calls
        different = {**params, "expected_generation": generation + 1}
        with pytest.raises(MemoryIntelligenceError):
            await facade.rebuild_index(different)
        other = SessionRef(uuid4().hex)
        service.sessions[other] = SimpleNamespace(agent_id="other")
        with pytest.raises(MemoryIntelligenceError, match="memory_job_not_found"):
            await facade.get_job({**base(other), "job_id": first["id"]})
        raw = (facade.jobs_directory / "jobs.json").read_text()
        assert "Nota para derivar" not in raw and "jarvis://" not in raw
    finally:
        await facade.close()


async def test_model_prepare_cancellation_retains_thread_and_profile_admission(context):
    service, facade, ref, _metadata = context
    entered, release = threading.Event(), threading.Event()

    def prepare(directory, *, cancellation, deadline, assert_current):
        entered.set()
        release.wait(2)
        if cancellation.is_set():
            raise MemoryIntelligenceError("memory_cancelled")
        assert_current()
        return metadata

    facade.model_preparer = prepare
    metadata = _metadata
    request = uuid4().hex
    try:
        await facade.prepare_model({**base(ref), "request_id": request})
        while not entered.is_set():
            await asyncio.sleep(0.005)
        response = (await facade.cancel_job({**base(ref), "job_id": request}))["job"]
        assert response["phase"] == "cancelling" and service.profile_gate.active
        closing = asyncio.create_task(facade.close())
        await asyncio.sleep(0.02)
        assert not closing.done() and service.profile_gate.active
        release.set()
        assert (await closing)["cleanup_confirmed"]
        assert not service.profile_gate.active
    finally:
        release.set()
        await facade.close()


async def test_cancelled_mutation_join_preserves_committed_receipt(context):
    _service, facade, _ref, _metadata = context
    entered, release = threading.Event(), threading.Event()

    def committed_effect():
        entered.set()
        release.wait(2)
        return {
            "status": "completed",
            "retired": True,
            "bytes": 17,
            "files": 1,
            "root_removed": True,
        }

    try:
        task = asyncio.create_task(facade._thread(committed_effect, _mutating=True))
        while not entered.is_set():
            await asyncio.sleep(0.005)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done() and facade._threads
        release.set()
        with pytest.raises(MemoryIntelligenceError, match="memory_mutation_unknown") as outcome:
            await task
        assert outcome.value.receipt["completed_callback"] is True
        assert outcome.value.receipt["retired"] is True
        assert outcome.value.receipt["status"] == "unknown"
        assert not facade._threads
    finally:
        release.set()
        await facade.close()


async def test_temporary_status_recall_and_search_are_ram_only(context):
    service, facade, _durable_ref, _metadata = context
    temp = SessionRef(uuid4().hex, True)
    service.sessions[temp] = SimpleNamespace(agent_id="jarvis")
    add(service, temp, "temp", "Nota temporal ALFA")
    try:
        await policy(facade, temp, recall=True, mode="semantic_local")
        result = await facade.search(temp, CLIENT, "consulta", mode="semantic_local")
        assert result.hits[0].document.key == "temp"
        lease = await facade.prepare_recall(
            SimpleNamespace(run_id=uuid4().hex, message="consulta"), temp, CLIENT
        )
        assert lease.status["status"] == "ready"
        with pytest.raises(MemoryIntelligenceError, match="memory_model_durable_required"):
            await facade.prepare_model({**base(temp), "request_id": uuid4().hex})
        assert not facade.jobs_directory.exists() and not facade.policy.directory.exists()
        assert not facade.cache_directory.exists()
        await facade.forget_temporary(temp)
        assert facade._policy(temp, CLIENT).settings.auto_recall_enabled is False
    finally:
        await facade.close()


async def test_restart_job_unknown_is_retained_without_replay_and_window_visible(context):
    service, facade, ref, _metadata = context
    calls = []
    facade.model_preparer = lambda *a, **k: calls.append(True)
    policy_snapshot = facade._policy(ref, CLIENT)
    job, fresh = facade._new_job(
        ref, CLIENT, policy_snapshot, "model_prepare", uuid4().hex, {}, None
    )
    assert fresh
    # Simulated previous-process crash: retain durable queued record without
    # starting its operation; release this process's artificial admission.
    job.admission.close()
    job.admission = None
    recovered = MemoryIntelligence(service, engine_factory=SyntheticEncoder)
    try:
        result = (await recovered.get_job({**base(ref), "job_id": job.metadata["id"]}))["job"]
        assert result["phase"] == "failed" and result["error"] == "interrupted_unknown"
        assert result["receipt"] == {"status": "unknown", "replayed": False}
        assert not calls
        assert (await recovered.close())["journal_terminal_window"] == 32
    finally:
        await facade.close()
        await recovered.close()


async def test_automatic_dirty_generation_coalesces_without_source_file_access(
    context, monkeypatch
):
    service, facade, ref, _metadata = context
    add(service, ref, "note", "ALFA versión inicial")
    try:
        await policy(facade, ref, auto=True, mode="semantic_local")
        await asyncio.sleep(1.6)
        first_jobs = [j for j in facade._jobs.values() if j.metadata["kind"] == "index_auto"]
        assert len(first_jobs) == 1
        assert (await settled(facade, ref, first_jobs[0].metadata["id"]))["phase"] == "complete"
        add(service, ref, "note", "ALFA versión segunda")
        add(service, ref, "note", "ALFA versión tercera")
        await asyncio.sleep(1.6)
        jobs = [j for j in facade._jobs.values() if j.metadata["kind"] == "index_auto"]
        assert len(jobs) == 2
        second = await settled(facade, ref, jobs[-1].metadata["id"])
        assert second["phase"] == "complete"
        assert second["source_generation"] == service.memory.generation(service._memory_scope(ref))
        assert facade._engine.document_calls == 2
    finally:
        await facade.close()


async def test_auto_index_waits_for_profile_cut_without_consuming_dirty_attempt(context):
    service, facade, ref, _metadata = context
    add(service, ref, "note", "ALFA actualización durante mantenimiento")
    try:
        await policy(facade, ref, auto=True, mode="semantic_local")
        service.profile_gate.busy = True
        await asyncio.sleep(1.6)
        assert not [job for job in facade._jobs.values() if job.metadata["kind"] == "index_auto"]
        assert not facade._auto_attempts
        service.profile_gate.busy = False
        await asyncio.sleep(1.6)
        jobs = [job for job in facade._jobs.values() if job.metadata["kind"] == "index_auto"]
        assert len(jobs) == 1
        assert (await settled(facade, ref, jobs[0].metadata["id"]))["phase"] == "complete"
        assert facade._engine.document_calls == 1
    finally:
        service.profile_gate.busy = False
        await facade.close()


async def test_injected_public_model_directory_is_readonly_even_for_owner(context):
    service, facade, ref, _metadata = context
    external_path = service.directory.parent / "explicit-public-model"
    external = MemoryIntelligence(
        service, model_directory=external_path, engine_factory=SyntheticEncoder
    )
    try:
        with pytest.raises(MemoryIntelligenceError, match="memory_model_external_readonly"):
            await external.prepare_model({**base(ref), "request_id": uuid4().hex})
        with pytest.raises(MemoryIntelligenceError, match="memory_model_external_readonly"):
            await external.retire_model(
                {**base(ref), "request_id": uuid4().hex, "copy_id": uuid4().hex}
            )
        assert not external_path.exists()
        assert not external.model_root.exists()
        assert not external.jobs_directory.exists()
    finally:
        await external.close()
        await facade.close()


async def test_recall_status_is_local_to_session_and_authenticated_client(context):
    service, facade, ref, _metadata = context
    other = SessionRef(uuid4().hex)
    service.sessions[other] = SimpleNamespace(agent_id="jarvis")
    add(service, ref, "note", "TOPACIO recuerdo compartido autorizado")
    try:
        await policy(facade, ref, recall=True)
        first_run, second_run = uuid4().hex, uuid4().hex
        await facade.prepare_recall(
            SimpleNamespace(run_id=first_run, message="TOPACIO"), ref, CLIENT
        )
        assert (await facade.status(base(ref)))["intelligence"]["last_recall"][
            "run_id"
        ] == first_run
        assert (await facade.status(base(other)))["intelligence"]["last_recall"] is None
        await facade.prepare_recall(
            SimpleNamespace(run_id=second_run, message="TOPACIO"), other, CLIENT
        )
        assert (await facade.status(base(ref)))["intelligence"]["last_recall"][
            "run_id"
        ] == first_run
        assert (await facade.status(base(other)))["intelligence"]["last_recall"][
            "run_id"
        ] == second_run
    finally:
        await facade.close()
