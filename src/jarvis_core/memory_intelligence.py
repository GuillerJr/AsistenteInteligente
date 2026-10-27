"""Own local-memory coordination, finite jobs and current per-turn recall.

Only approved MemoryStore notes are input. A source URI is a citation label,
never permission to open a file. Sidecars are physically bound and nonportable;
temporary notes/policies/jobs remain in RAM. No status/search path downloads.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import re
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from jarvis_core.client_identity import profile_binding
from jarvis_core.local_embeddings import ENGINE_FINGERPRINT, EngineError, LocalEmbeddingClient
from jarvis_core.memory import MemoryError, SearchResult
from jarvis_core.memory_context import make_recall_lease, recall_query
from jarvis_core.memory_index import MemoryIndex
from jarvis_core.memory_policy import MemoryPolicyStore
from jarvis_core.memory_retrieval import MemoryIndexError, validate_query
from jarvis_core.model_artifacts import (
    MODEL_FINGERPRINT,
    MODEL_SPEC,
    ArtifactError,
    inspect_model,
    prepare_model,
    repair_model,
    retire_model_copy,
)
from jarvis_core.private_json import PrivateJsonDocument
from jarvis_core.protocol import authenticated_client
from jarvis_core.recovery_fs import Directory, FileIdentity, PinnedFile, RecoveryFsError
from jarvis_core.state import ensure_private_directory

_HASH = re.compile(r"[a-f0-9]{64}\Z")
_UUID = re.compile(r"[a-f0-9]{32}\Z")
_CODE = re.compile(r"[a-z0-9_]{1,100}\Z")
_ACTIVE = frozenset({"queued", "running", "cancelling", "cleanup_pending"})
_MODES = frozenset({"lexical", "semantic_local", "hybrid_local"})
_BASE = frozenset({"session_id", "temporary"})


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _error(error):
    code = getattr(error, "code", "memory_intelligence_unavailable")
    return (
        code
        if isinstance(code, str) and _CODE.fullmatch(code)
        else "memory_intelligence_unavailable"
    )


def _params(value, extra=(), required=()):
    if (
        not isinstance(value, dict)
        or set(value) - (_BASE | set(extra))
        or not ({"session_id"} | set(required)) <= set(value)
    ):
        raise MemoryIntelligenceError("memory_intelligence_params_invalid")


class MemoryIntelligenceError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _ThreadMutationUnknown(MemoryIntelligenceError):
    def __init__(self, result=None, failure=None):
        super().__init__("memory_mutation_unknown")
        self.completed_result = result
        receipt = getattr(failure, "receipt", None)
        self.receipt = (
            receipt
            if isinstance(receipt, dict)
            else {
                "status": "unknown",
                "replayed": False,
            }
        )
        if isinstance(result, dict):
            # A joined callback may have committed an effect before its async
            # acknowledgement was cancelled. Never relabel it not_published.
            self.receipt = {
                "status": "unknown",
                "replayed": False,
                "completed_callback": True,
                **{
                    key: result[key]
                    for key in ("bytes", "files", "retired", "root_removed", "model_fingerprint")
                    if key in result
                },
            }


@dataclass(frozen=True)
class IntelligentSearchResult(SearchResult):
    index_fingerprint: str


@dataclass
class _Job:
    metadata: dict
    scope_key: str
    client_id: str
    request_digest: str
    temporary: bool
    ref: object
    cancellation: threading.Event
    task: asyncio.Task | None = None
    admission: object = None
    unresolved: bool = False


class MemoryIntelligence:
    def __init__(
        self,
        service,
        *,
        model_directory=None,
        model_preparer=prepare_model,
        engine_factory=LocalEmbeddingClient,
    ):
        self.service = service
        self.profile = Path(service.directory)
        self.policy = MemoryPolicyStore(self.profile)
        self.binding = self.policy.binding
        self.model_root = self.profile.with_name(self.profile.name + ".memory-model")
        self.model_directory = (
            Path(model_directory) if model_directory is not None else self.model_root / "initial"
        )
        self.cache_directory = self.profile.with_name(self.profile.name + ".memory-cache")
        self.jobs_directory = self.profile.with_name(self.profile.name + ".memory-jobs")
        self.index = MemoryIndex(self.cache_directory, self.binding)
        self.model_preparer, self.engine_factory = model_preparer, engine_factory
        self._engine = None
        self._model_metadata = None
        self._model_signature = None
        self._model_error = None
        self._jobs = {}
        self._journal_document = None
        self._journal_snapshot = None
        self._model_control = None
        self._model_control_data = None
        self._model_control_seen = False
        self._initial_model_directory = self.model_directory
        self._external_model = model_directory is not None
        self._pending_owners = []
        self._reviews = {}
        self._registrations = {}
        self._auto_attempts = {}
        self._last_recall = {}
        self._lane = asyncio.Lock()
        self._background = None
        self._foreground = set()
        self._threads = set()
        self._closed = False

    @property
    def blockers(self):
        values = set()
        if (
            self._foreground
            or self._threads
            or any(job.task is not None and not job.task.done() for job in self._jobs.values())
        ):
            values.add("memory_jobs_active")
        if self._pending_owners or any(
            job.metadata["phase"] == "cleanup_pending" for job in self._jobs.values()
        ):
            values.add("memory_cleanup_pending")
        return tuple(sorted(values))

    def _owner(self, ref, client_id, *, closing=False):
        if self._closed and not closing:
            raise MemoryIntelligenceError("memory_intelligence_closed")
        if not isinstance(client_id, str) or not _HASH.fullmatch(client_id):
            raise MemoryIntelligenceError("memory_owner_required")
        self.service.profile_lease.assert_current()
        if profile_binding(self.profile) != self.binding:
            raise MemoryIntelligenceError("memory_profile_changed")
        self.service.client_authority.lookup(client_id, scope="owner")
        record = self.service.store.get_session(ref)
        return record.agent_id

    def _policy(self, ref, client_id):
        agent = self._owner(ref, client_id)
        return self.policy.snapshot(
            client_id=client_id,
            agent_id=agent,
            workspace_id=self.service._workspace_id,
            temporary=ref.temporary,
            session_id=ref.session_id if ref.temporary else None,
        )

    def _guard(self, ref, client_id, policy, generation=None):
        def check():
            agent = self._owner(ref, client_id)
            if (
                agent != policy.scope.agent_id
                or self.service._workspace_id != policy.scope.workspace_id
            ):
                raise MemoryIntelligenceError("memory_scope_changed")
            self.policy.assert_current(policy)
            if (
                generation is not None
                and self.service.memory.generation(self.service._memory_scope(ref)) != generation
            ):
                raise MemoryIntelligenceError("memory_generation_changed")

        return check

    def _scope(self, ref, client_id):
        policy = self._policy(ref, client_id)
        scope = self.service._memory_scope(ref)
        if policy.scope.key not in self._registrations and len(self._registrations) >= 128:
            raise MemoryIntelligenceError("memory_intelligence_capacity")
        self._registrations[policy.scope.key] = (ref, client_id)
        if policy.settings.auto_index_enabled:
            self._start_background()
        return scope, policy

    def _request(self, params, extra=(), required=()):
        _params(params, extra, required)
        ref = self.service._ref(params)
        client = authenticated_client.get()
        scope, policy = self._scope(ref, client)
        return ref, client, scope, policy

    def handlers(self):
        return {
            "memory.intelligence.status": self.status,
            "memory.intelligence.policy.review": self.review_policy,
            "memory.intelligence.policy.apply": self.apply_policy,
            "memory.intelligence.model.prepare": self.prepare_model,
            "memory.intelligence.model.retire": self.retire_model,
            "memory.intelligence.index.rebuild": self.rebuild_index,
            "memory.intelligence.index.verify": self.verify_index,
            "memory.intelligence.job.get": self.get_job,
            "memory.intelligence.job.cancel": self.cancel_job,
        }

    @staticmethod
    def _validate_journal(value):
        if (
            not isinstance(value, dict)
            or set(value) != {"version", "profile_binding", "jobs"}
            or value["version"] != 1
            or not _HASH.fullmatch(value["profile_binding"])
            or not isinstance(value["jobs"], list)
            or len(value["jobs"]) > 64
        ):
            raise MemoryIntelligenceError("memory_job_journal_invalid")
        seen = set()
        for row in value["jobs"]:
            if (
                not isinstance(row, dict)
                or set(row)
                != {"metadata", "scope_key", "client_id", "request_digest", "unresolved"}
                or any(
                    not isinstance(row[key], str) or not _HASH.fullmatch(row[key])
                    for key in ("scope_key", "client_id", "request_digest")
                )
                or type(row["unresolved"]) is not bool
            ):
                raise MemoryIntelligenceError("memory_job_journal_invalid")
            meta = row["metadata"]
            if (
                not isinstance(meta, dict)
                or set(meta)
                != {
                    "id",
                    "kind",
                    "phase",
                    "error",
                    "source_generation",
                    "policy_revision",
                    "progress",
                    "receipt",
                }
                or not isinstance(meta["id"], str)
                or not _UUID.fullmatch(meta["id"])
                or meta["id"] in seen
                or meta["kind"]
                not in {"model_prepare", "model_retire", "index_rebuild", "index_auto"}
                or meta["phase"] not in _ACTIVE | {"complete", "failed", "cancelled"}
                or (
                    meta["error"] is not None
                    and (not isinstance(meta["error"], str) or not _CODE.fullmatch(meta["error"]))
                )
                or type(meta["policy_revision"]) is not int
                or not 1 <= meta["policy_revision"] <= (1 << 52) - 1
                or (
                    meta["source_generation"] is not None
                    and (
                        type(meta["source_generation"]) is not int
                        or not 0 <= meta["source_generation"] <= (1 << 52) - 1
                    )
                )
                or not isinstance(meta["progress"], dict)
                or set(meta["progress"]) != {"documents_done", "documents_total", "chunks"}
                or any(
                    type(n) is not int or not 0 <= n <= 1_000_000 for n in meta["progress"].values()
                )
                or (meta["receipt"] is not None and not isinstance(meta["receipt"], dict))
            ):
                raise MemoryIntelligenceError("memory_job_journal_invalid")
            seen.add(meta["id"])

    def _journal(self):
        if self._journal_document is None:
            self._journal_document = PrivateJsonDocument(
                self.jobs_directory,
                "jobs.json",
                initial=lambda: {"version": 1, "profile_binding": self.binding, "jobs": []},
                validate=self._validate_journal,
                max_bytes=262144,
            )
        if self._journal_snapshot is None:
            data = self._journal_document.read()
            if data["profile_binding"] != self.binding:
                raise MemoryIntelligenceError("memory_profile_changed")
            self._journal_snapshot = json.loads(json.dumps(data))
            dirty = False
            for row in data["jobs"]:
                metadata = row["metadata"]
                unresolved = row["unresolved"]
                if metadata["phase"] in _ACTIVE:
                    metadata.update(
                        phase="failed",
                        error="interrupted_unknown",
                        receipt={"status": "unknown", "replayed": False},
                    )
                    unresolved = True
                    dirty = True
                job = _Job(
                    metadata,
                    row["scope_key"],
                    row["client_id"],
                    row["request_digest"],
                    False,
                    None,
                    threading.Event(),
                    unresolved=unresolved,
                )
                if metadata["id"] not in self._jobs:
                    self._jobs[metadata["id"]] = job
            if dirty:
                self._persist_jobs()
        return self._journal_document

    def _persist_jobs(self):
        if self._journal_document is None:
            return
        durable = [job for job in self._jobs.values() if not job.temporary]
        retained = [job for job in durable if job.metadata["phase"] in _ACTIVE or job.unresolved]
        terminals = [job for job in durable if job not in retained]
        keep = retained + terminals[-32:]
        if len(retained) > 32 or len(keep) > 64:
            raise MemoryIntelligenceError("memory_job_capacity")
        rows = [
            {
                "metadata": job.metadata,
                "scope_key": job.scope_key,
                "client_id": job.client_id,
                "request_digest": job.request_digest,
                "unresolved": job.unresolved,
            }
            for job in keep
        ]
        try:

            def change(old):
                if old != self._journal_snapshot:
                    raise MemoryIntelligenceError("memory_job_journal_changed")
                return {"version": 1, "profile_binding": self.binding, "jobs": rows}

            published = self._journal_document.update(
                change,
                before_publish=lambda staging: self.service.profile_lease.assert_current(),
            )
            self._journal_snapshot = json.loads(json.dumps(published))
        except BaseException as error:
            owner = getattr(error, "owner", None)
            if owner is not None and owner not in self._pending_owners:
                self._pending_owners.append(owner)
            raise
        kept_ids = {job.metadata["id"] for job in keep}
        for job in durable:
            if job.metadata["id"] not in kept_ids:
                self._jobs.pop(job.metadata["id"], None)

    def _model_files_signature(self):
        paths = [self.model_directory, self.model_directory / "manifest.json"]
        paths += [self.model_directory / row["path"] for row in MODEL_SPEC["files"]]
        try:
            return tuple(
                (
                    p.lstat().st_dev,
                    p.lstat().st_ino,
                    p.lstat().st_size,
                    p.lstat().st_mtime_ns,
                    p.lstat().st_ctime_ns,
                )
                for p in paths
            )
        except OSError:
            return None

    def _validate_model_control(self, value):
        if (
            not isinstance(value, dict)
            or set(value)
            != {"version", "profile_binding", "revision", "current", "previous", "pending"}
            or value["version"] != 1
            or value["profile_binding"] != self.binding
            or type(value["revision"]) is not int
            or not 1 <= value["revision"] < 2**52
        ):
            raise MemoryIntelligenceError("memory_model_control_invalid")
        for key in ("current", "previous"):
            slot = value[key]
            if slot is None:
                continue
            if (
                not isinstance(slot, dict)
                or set(slot) != {"id", "identity", "bytes", "ownership"}
                or not isinstance(slot["id"], str)
                or not _UUID.fullmatch(slot["id"])
                or not isinstance(slot["identity"], list)
                or len(slot["identity"]) != 2
                or any(type(n) is not int or n < 0 for n in slot["identity"])
                or type(slot["bytes"]) is not int
                or not 0 <= slot["bytes"] <= 256_000_000
            ):
                raise MemoryIntelligenceError("memory_model_control_invalid")
            ownership = slot["ownership"]
            if ownership is not None and ownership != {
                "format": "jarvis-model-copy-ownership/1",
                "profile_binding": self.binding,
                "slot_id": slot["id"],
                "root_identity": slot["identity"],
                "asset_fingerprint": MODEL_FINGERPRINT,
                "source": "managed_preparation",
            }:
                raise MemoryIntelligenceError("memory_model_control_invalid")
        if (
            value["previous"] is not None
            and value["current"] is not None
            and value["previous"]["id"] == value["current"]["id"]
        ):
            raise MemoryIntelligenceError("memory_model_control_invalid")
        pending = value["pending"]
        if isinstance(pending, dict) and set(pending) == {"id", "identity", "bytes", "ownership"}:
            self._validate_model_control({**value, "pending": None, "previous": pending})
            return
        if pending is not None and (
            not isinstance(pending, dict)
            or set(pending) != {"id", "bytes"}
            or not isinstance(pending["id"], str)
            or not _UUID.fullmatch(pending["id"])
            or type(pending["bytes"]) is not int
            or not 0 <= pending["bytes"] <= 256_000_000
            or value["previous"] is not None
        ):
            raise MemoryIntelligenceError("memory_model_control_invalid")

    def _model_slots(self, *, durable=True):
        if self._external_model:
            return self._model_control_data
        if not durable:
            try:
                return self._read_public_model_slots()
            except (OSError, RecoveryFsError):
                raise MemoryIntelligenceError("memory_model_control_changed") from None
        if self._model_control is None:
            self._model_control = PrivateJsonDocument(
                self.policy.directory,
                "models.json",
                initial=lambda: {
                    "version": 1,
                    "profile_binding": self.binding,
                    "revision": 1,
                    "current": None,
                    "previous": None,
                    "pending": None,
                },
                validate=self._validate_model_control,
                max_bytes=4096,
            )
        data = self._model_control.read()
        if data["current"] is not None:
            try:
                pinned = self._slot_path(data["current"]).lstat()
            except FileNotFoundError:
                raise MemoryIntelligenceError("memory_model_changed") from None
            if [pinned.st_dev, pinned.st_ino] != data["current"]["identity"]:
                raise MemoryIntelligenceError("memory_model_changed")
        self._model_control_data = data
        self._model_control_seen = self._model_control_seen or self._model_control._has_seen_file
        self.model_directory = self._slot_path(data["current"])
        return data

    def _read_public_model_slots(self):
        """Read only a pre-existing public-assets selector; never initialize it."""

        def absent():
            self._cleanup_guard()
            if self._model_control_seen:
                raise MemoryIntelligenceError("memory_model_control_changed")
            self._model_control_data = None
            self.model_directory = self._initial_model_directory
            return None

        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise MemoryIntelligenceError("memory_model_control_invalid")
                value[key] = item
            return value

        self._cleanup_guard()
        try:
            parent = Directory(self.policy.directory)
        except FileNotFoundError:
            return absent()
        except RecoveryFsError:
            raise MemoryIntelligenceError("memory_model_control_changed") from None
        with parent:
            try:
                pinned = PinnedFile(parent, "models.json", 4096)
            except FileNotFoundError:
                return absent()
            with pinned:
                raw = pinned.read(self._cleanup_guard)
                try:
                    data = json.loads(raw, object_pairs_hook=unique)
                except (ValueError, UnicodeError):
                    raise MemoryIntelligenceError("memory_model_control_invalid") from None
                self._validate_model_control(data)
                if _canonical(data) != raw:
                    raise MemoryIntelligenceError("memory_model_control_invalid")
                if data["current"] is not None:
                    with Directory(
                        self._slot_path(data["current"]),
                        expected=FileIdentity(*data["current"]["identity"]),
                    ) as selected:
                        selected.check()
                        pinned.check()
                        self._cleanup_guard()
                pinned.check()
                self._cleanup_guard()
                self._model_control_seen = True
                self._model_control_data = data
                self.model_directory = self._slot_path(data["current"])
                return data

    def _slot_path(self, slot):
        return (
            self._initial_model_directory
            if slot is None or slot["id"] == "0" * 32
            else self.model_root / ("copy-" + slot["id"])
        )

    def _write_slots(self, before, after, guard):
        def change(current):
            if current != before:
                raise MemoryIntelligenceError("memory_model_changed")
            return after

        try:
            self._model_control.update(change, before_publish=lambda staging: guard())
        except BaseException as error:
            owner = getattr(error, "owner", None)
            if owner is not None and owner not in self._pending_owners:
                self._pending_owners.append(owner)
            raise
        self._model_control_data = after
        self._model_control_seen = True
        self.model_directory = self._slot_path(after["current"])

    def _cleanup_guard(self):
        self.service.profile_lease.assert_current()
        if profile_binding(self.profile) != self.binding:
            raise MemoryIntelligenceError("memory_profile_changed")

    def _model_status(self):
        signature = self._model_files_signature()
        available = (
            signature is not None
            and signature == self._model_signature
            and self._model_metadata is not None
        )
        runtime = all(
            importlib.util.find_spec(name) is not None
            for name in ("numpy", "onnxruntime", "tokenizers")
        )
        return {
            "id": MODEL_SPEC["repository"],
            "state": "ready" if available else "unverified" if signature else "missing",
            "available": available,
            "runtime_available": runtime,
            "bytes": self._model_metadata["bytes"] if available else 0,
            "fingerprint": ENGINE_FINGERPRINT if available else None,
            "cpu_only": True,
            "network_for_inference": False,
            "previous_copy": (
                {"id": copy["id"], "bytes": copy["bytes"]}
                if self._model_control_data
                and (
                    copy := self._model_control_data["previous"]
                    or self._model_control_data["pending"]
                )
                else None
            ),
        }

    async def _thread(self, callback, *args, _mutating=False, **kwargs):
        task = asyncio.create_task(asyncio.to_thread(callback, *args, **kwargs))
        self._threads.add(task)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Cancelling the Future cannot stop a Python thread. Keep custody and
            # join it before propagating cancellation or admitting another lane.
            while not task.done():
                with suppress(asyncio.CancelledError):
                    await asyncio.shield(task)
            if _mutating:
                try:
                    result = task.result()
                except BaseException as failure:
                    raise _ThreadMutationUnknown(failure=failure) from None
                raise _ThreadMutationUnknown(result=result) from None
            with suppress(BaseException):
                task.result()
            raise
        finally:
            if task.done():
                self._threads.discard(task)

    async def _verified_engine(self, guard, cancellation, deadline):
        guard()
        signature = self._model_files_signature()
        if self._model_metadata is None or self._model_signature != signature:
            value = await self._thread(
                inspect_model, self.model_directory, cancellation=cancellation, deadline=deadline
            )
            guard()
            self._model_metadata = value
            self._model_signature = self._model_files_signature()
        if self._engine is None:
            self._engine = self.engine_factory(
                self.model_directory, custody=self.service.profile_lease.child_custody_fds
            )
        return self._engine

    async def status(self, params):
        ref, client, scope, policy = self._request(params)
        if not ref.temporary:
            self._journal()
            self._model_slots()
        else:
            self._model_slots(durable=False)
        self._start_background()
        not_opened = (
            not ref.temporary
            and not policy.settings.auto_recall_enabled
            and not policy.settings.auto_index_enabled
            and self.service.memory._disk is None
        )
        generation = 0 if not_opened else self.service.memory.generation(scope)
        return {
            "intelligence": self._status(
                ref, client, scope, policy, generation, not_opened=not_opened
            )
        }

    def _status(self, ref, client, scope, policy, generation, *, not_opened=False):
        raw = {"state": "not_opened"} if not_opened else self.index.status(scope)
        jobs = [
            job
            for job in self._jobs.values()
            if job.scope_key == policy.scope.key and job.client_id == client
        ]
        current = next(
            (j for j in reversed(jobs) if j.metadata["phase"] in _ACTIVE),
            jobs[-1] if jobs else None,
        )
        return {
            "scope": {"session_id": ref.session_id, "temporary": ref.temporary},
            "policy": policy.metadata(),
            "model": self._model_status(),
            "index": {
                "state": raw["state"],
                "source_generation": generation,
                "generation": raw.get("source_generation"),
                "document_count": raw.get("documents", 0),
                "chunk_count": raw.get("chunks", 0),
                "bytes": raw.get("cache_bytes", 0),
                "model_fingerprint": raw.get("model_fingerprint"),
                "error": raw.get("last_error"),
            },
            "current_job": json.loads(json.dumps(current.metadata)) if current else None,
            "last_recall": self._last_recall.get((policy.scope.key, ref.session_id)),
        }

    async def review_policy(self, params):
        _ref, _client, _scope, policy = self._request(
            params,
            {"settings", "expected_revision", "expected_digest"},
            {"settings", "expected_revision", "expected_digest"},
        )
        review = self.policy.review(
            **policy.scope.metadata(),
            settings=params["settings"],
            expected_revision=params["expected_revision"],
            expected_digest=params["expected_digest"],
        )
        if len(self._reviews) >= 128:
            now = self.policy.clock()
            self._reviews = {
                key: value for key, value in self._reviews.items() if value.expires_at > now
            }
            if len(self._reviews) >= 128:
                raise MemoryIntelligenceError("memory_policy_capacity")
        self._reviews[review.review_id] = review
        return {
            "review": {
                "review_id": review.review_id,
                "review_digest": review.review_digest,
                "expires_in_seconds": max(0, review.expires_at - self.policy.clock()),
                "metadata": review.metadata(),
            }
        }

    def _invalidate(self, scope_key):
        for key in tuple(self._last_recall):
            if key[0] == scope_key:
                self._last_recall.pop(key)
        for job in self._jobs.values():
            if job.scope_key == scope_key and job.metadata["phase"] in _ACTIVE:
                job.cancellation.set()

    async def apply_policy(self, params):
        ref, client, _scope, policy = self._request(
            params, {"review_id", "review_digest"}, {"review_id", "review_digest"}
        )
        if not isinstance(params["review_id"], str) or not _UUID.fullmatch(params["review_id"]):
            raise MemoryIntelligenceError("memory_policy_review_invalid")
        review = self._reviews.get(params["review_id"])
        if (
            review is None
            or review.before.scope.key != policy.scope.key
            or review.review_digest != params["review_digest"]
        ):
            raise MemoryIntelligenceError("memory_policy_review_invalid")

        # The policy store invokes this under its write flock: never reread policy
        # here. Namespace identity/client/profile are the independent authority.
        def owner_guard():
            if (
                self._owner(ref, client) != review.before.scope.agent_id
                or self.service._workspace_id != review.before.scope.workspace_id
            ):
                raise MemoryIntelligenceError("memory_scope_changed")

        self.policy.apply(
            review,
            assert_current=owner_guard,
            before_publish=lambda staging: self._invalidate(policy.scope.key),
        )
        self._reviews.pop(review.review_id, None)
        self._start_background()
        return await self.status({"session_id": ref.session_id, "temporary": ref.temporary})

    async def _lock_lane(self, cancellation=None, deadline=None):
        while self._lane.locked():
            if cancellation is not None and cancellation.is_set():
                raise MemoryIntelligenceError("memory_cancelled")
            if deadline is not None and time.monotonic() >= deadline:
                raise MemoryIntelligenceError("memory_deadline")
            await asyncio.sleep(0.02)
        await self._lane.acquire()
        if self._pending_owners or any(
            job.metadata["phase"] == "cleanup_pending" for job in self._jobs.values()
        ):
            self._lane.release()
            raise MemoryIntelligenceError("memory_cleanup_pending")

    async def _search(
        self,
        ref,
        client_id,
        query,
        *,
        mode,
        limit,
        offset,
        expected_generation,
        expected_index_fingerprint,
        cancellation,
        deadline,
        minimum_similarity=None,
    ):
        scope, policy = self._scope(ref, client_id)
        self._model_slots(durable=not ref.temporary)
        generation = self.service.memory.generation(scope)
        if expected_generation is not None and (
            type(expected_generation) is not int or expected_generation != generation
        ):
            raise MemoryIntelligenceError("memory_generation_changed")
        guard = self._guard(ref, client_id, policy, generation)
        if mode == "lexical":
            guard()
            result = self.service.memory.search(
                scope, query, limit=limit, offset=offset, expected_generation=expected_generation
            )
            guard()
            return result, policy
        if mode not in _MODES:
            raise MemoryIntelligenceError("memory_search_mode_invalid")
        validate_query(query, mode, limit, offset)
        if offset and (
            expected_generation is None
            or not isinstance(expected_index_fingerprint, str)
            or not _HASH.fullmatch(expected_index_fingerprint)
        ):
            raise MemoryIntelligenceError("memory_page_changed")
        await self._lock_lane(cancellation, deadline)
        try:
            guard()
            snapshot = self.service.memory.snapshot(
                scope, cancellation=cancellation, deadline=deadline
            )
            guard()
            engine = await self._verified_engine(guard, cancellation, deadline)
            raw = self.index.status(scope)
            token = self._page_token(raw, scope, policy, mode, query, limit)
            if offset and token != expected_index_fingerprint:
                raise MemoryIntelligenceError("memory_page_changed")
            if (
                raw.get("source_generation") != generation
                or raw.get("snapshot_fingerprint") != snapshot.fingerprint
                or raw.get("model_fingerprint") != engine.model_fingerprint
            ):
                if offset:
                    raise MemoryIntelligenceError("memory_page_changed")
                await self.index.build(
                    snapshot,
                    engine,
                    cancellation=cancellation,
                    deadline=deadline,
                    assert_current=guard,
                )
            result = await self.index.search(
                snapshot,
                query,
                engine,
                mode=mode,
                limit=limit,
                offset=offset,
                cancellation=cancellation,
                deadline=deadline,
                assert_current=guard,
                minimum_similarity=minimum_similarity,
            )
            for hit in result.hits:
                if self.service.memory.resolve_citation(scope, hit.citation) != hit.excerpt:
                    raise MemoryIntelligenceError("memory_citation_changed")
            guard()
            raw = self.index.status(scope)
            return IntelligentSearchResult(
                **result.__dict__,
                index_fingerprint=self._page_token(raw, scope, policy, mode, query, limit),
            ), policy
        finally:
            self._lane.release()

    def _page_token(self, raw, scope, policy, mode, query, limit):
        return _digest(
            {
                "format": "jarvis-memory-page/1",
                "scope": scope.key,
                "source_generation": raw.get("source_generation"),
                "snapshot": raw.get("snapshot_fingerprint"),
                "index": raw.get("index_fingerprint"),
                "model": raw.get("model_fingerprint"),
                "policy": policy.digest,
                "mode": mode,
                "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
                "limit": limit,
            }
        )

    async def search(
        self,
        ref,
        client_id,
        query,
        *,
        mode,
        limit=10,
        offset=0,
        expected_generation=None,
        expected_index_fingerprint=None,
    ):
        return await self._search_admitted(
            ref,
            client_id,
            query,
            mode=mode,
            limit=limit,
            offset=offset,
            expected_generation=expected_generation,
            expected_index_fingerprint=expected_index_fingerprint,
            minimum_similarity=None,
        )

    async def _search_admitted(
        self,
        ref,
        client_id,
        query,
        *,
        mode,
        limit,
        offset=0,
        expected_generation=None,
        expected_index_fingerprint=None,
        minimum_similarity=None,
    ):
        admission = self.service.profile_gate.hold("memory.search", mutating=True)
        task = asyncio.current_task()
        self._foreground.add(task)
        try:
            result, _ = await self._search(
                ref,
                client_id,
                query,
                mode=mode,
                limit=limit,
                offset=offset,
                expected_generation=expected_generation,
                expected_index_fingerprint=expected_index_fingerprint,
                cancellation=None,
                deadline=time.monotonic() + 30,
                minimum_similarity=minimum_similarity,
            )
            return result
        finally:
            self._foreground.discard(task)
            admission.close()

    async def _recall_search(self, ref, client_id, query, *, mode):
        return await self._search_admitted(
            ref,
            client_id,
            query,
            mode=mode,
            limit=4,
            minimum_similarity=0.35 if mode != "lexical" else None,
        )

    async def prepare_recall(self, context, ref, client_id):
        scope, policy = self._scope(ref, client_id)
        selected_mode = policy.settings.mode
        guard = self._guard(ref, client_id, policy)
        generation, result, error, projected = 0, None, None, False
        state = "disabled"
        if policy.settings.auto_recall_enabled:
            generation = self.service.memory.generation(scope)
            guard = self._guard(ref, client_id, policy, generation)
            query, projected = recall_query(context.message)
            try:
                try:
                    result = await self._recall_search(ref, client_id, query, mode=selected_mode)
                except EngineError as failure:
                    if failure.code != "capacity" or projected:
                        raise
                    query, projected = recall_query(context.message, force_projection=True)
                    result = await self._recall_search(ref, client_id, query, mode=selected_mode)
                generation = result.generation
                guard = self._guard(ref, client_id, policy, generation)
                state = "ready" if result.hits else "empty"
            except (
                MemoryError,
                MemoryIndexError,
                MemoryIntelligenceError,
                EngineError,
                ArtifactError,
            ) as failure:
                guard()
                state, error = "unavailable", _error(failure)
        mode = result.mode if result is not None else selected_mode
        base_guard = guard

        def verify():
            base_guard()
            if result is not None:
                for hit in result.hits:
                    if self.service.memory.resolve_citation(scope, hit.citation) != hit.excerpt:
                        raise MemoryIntelligenceError("memory_citation_changed")
            base_guard()

        lease = make_recall_lease(
            scope_key=scope.key,
            generation=generation,
            policy_digest=policy.digest,
            model_fingerprint=ENGINE_FINGERPRINT if selected_mode != "lexical" else None,
            result=result,
            assert_current=verify,
            state=state,
            mode=mode,
            query_projected=projected,
            error=error,
            run_id=context.run_id,
        )
        key = policy.scope.key, ref.session_id
        self._last_recall.pop(key, None)
        if len(self._last_recall) >= 128:
            self._last_recall.pop(next(iter(self._last_recall)))
        self._last_recall[key] = {**dict(lease.status), "mode": selected_mode}
        return lease

    def _existing_job(self, ref, client, policy, kind, request_id, arguments):
        if not isinstance(request_id, str) or not _UUID.fullmatch(request_id):
            raise MemoryIntelligenceError("memory_job_id_invalid")
        if not ref.temporary:
            self._journal()
        digest = _digest({"kind": kind, "scope": policy.scope.key, "arguments": arguments})
        existing = self._jobs.get(request_id)
        if existing is not None:
            if existing.scope_key != policy.scope.key or existing.client_id != client:
                raise MemoryIntelligenceError("memory_job_not_found")
            if existing.request_digest != digest:
                raise MemoryIntelligenceError("memory_job_conflict")
            return existing
        return None

    def _new_job(self, ref, client, policy, kind, request_id, arguments, generation):
        existing = self._existing_job(ref, client, policy, kind, request_id, arguments)
        if existing is not None:
            return existing, False
        digest = _digest({"kind": kind, "scope": policy.scope.key, "arguments": arguments})
        if (
            sum(job.unresolved or job.metadata["phase"] in _ACTIVE for job in self._jobs.values())
            >= 32
        ):
            raise MemoryIntelligenceError("memory_job_capacity")
        job = _Job(
            {
                "id": request_id,
                "kind": kind,
                "phase": "queued",
                "error": None,
                "source_generation": generation,
                "policy_revision": policy.revision,
                "progress": {"documents_done": 0, "documents_total": 0, "chunks": 0},
                "receipt": None,
            },
            policy.scope.key,
            client,
            digest,
            ref.temporary,
            ref,
            threading.Event(),
        )
        job.admission = self.service.profile_gate.hold("memory.job", mutating=True)
        self._jobs[request_id] = job
        try:
            if not ref.temporary:
                self._persist_jobs()
        except BaseException:
            job.admission.close()
            job.admission = None
            self._jobs.pop(request_id, None)
            raise
        return job, True

    async def prepare_model(self, params):
        ref, client, _scope, policy = self._request(params, {"request_id"}, {"request_id"})
        if ref.temporary:
            raise MemoryIntelligenceError("memory_model_durable_required")
        if self._external_model:
            raise MemoryIntelligenceError("memory_model_external_readonly")
        job, fresh = self._new_job(
            ref, client, policy, "model_prepare", params["request_id"], {}, None
        )
        if fresh:
            job.task = asyncio.create_task(self._run_job(job, policy))
        return {"job": json.loads(json.dumps(job.metadata))}

    async def retire_model(self, params):
        ref, client, _scope, policy = self._request(
            params, {"request_id", "copy_id"}, {"request_id", "copy_id"}
        )
        if ref.temporary:
            raise MemoryIntelligenceError("memory_model_durable_required")
        if self._external_model:
            raise MemoryIntelligenceError("memory_model_external_readonly")
        existing = self._existing_job(
            ref,
            client,
            policy,
            "model_retire",
            params["request_id"],
            {"copy_id": params["copy_id"]},
        )
        if existing is not None:
            return {"job": json.loads(json.dumps(existing.metadata))}
        slots = self._model_slots()
        if (
            slots is not None
            and slots["pending"] is not None
            and "identity" not in slots["pending"]
        ):
            raise MemoryIntelligenceError("memory_model_cleanup_unknown")
        copy = (slots["previous"] or slots["pending"]) if slots is not None else None
        if copy is None or params["copy_id"] != copy["id"]:
            raise MemoryIntelligenceError("memory_model_copy_changed")
        job, fresh = self._new_job(
            ref,
            client,
            policy,
            "model_retire",
            params["request_id"],
            {"copy_id": params["copy_id"]},
            None,
        )
        if fresh:
            job.copy_id = params["copy_id"]
            job.task = asyncio.create_task(self._run_job(job, policy))
        return {"job": json.loads(json.dumps(job.metadata))}

    async def _close_engine(self):
        if self._engine is not None:
            result = await self._engine.close()
            if not result.get("closed") or not result.get("cleanup_confirmed", True):
                raise MemoryIntelligenceError("memory_cleanup_pending")
            self._engine = None

    async def _prepare_assets(self, job, guard, deadline):
        if self._external_model:
            raise MemoryIntelligenceError("memory_model_external_readonly")
        guard()
        ensure_private_directory(self.model_root)
        slots = self._model_slots()
        if slots is not None and slots["pending"] is not None:
            raise MemoryIntelligenceError("memory_model_cleanup_unknown")
        try:
            result = await self._thread(
                self.model_preparer,
                self.model_directory,
                _mutating=True,
                cancellation=job.cancellation,
                deadline=deadline,
                assert_current=guard,
            )
            job.effect_receipt = {
                "status": "unknown",
                "completed_callback": True,
                "model_fingerprint": ENGINE_FINGERPRINT,
                "bytes": result["bytes"],
                "replayed": False,
            }
        except Exception as error:
            if _error(error) != "invalid_model" or self._external_model:
                raise
            if slots["previous"] is not None:
                raise MemoryIntelligenceError("memory_model_retire_previous_required") from None
            await self._close_engine()
            guard()
            identity = self.model_directory.lstat()
            old = slots["current"]
            # The original standard directory also gets an opaque ID; its path
            # stays fixed and is recorded by its distinguished all-zero ID.
            old = old or {
                "id": "0" * 32,
                "identity": [identity.st_dev, identity.st_ino],
                "bytes": sum(row["bytes"] for row in MODEL_SPEC["files"]),
                "ownership": None,
            }
            new_id = uuid4().hex
            replacement = self._slot_path({"id": new_id})
            intent = {
                **slots,
                "revision": slots["revision"] + 1,
                "pending": {"id": new_id, "bytes": old["bytes"]},
            }
            self._write_slots(slots, intent, guard)
            try:
                result = await self._thread(
                    repair_model,
                    self.model_directory,
                    replacement,
                    _mutating=True,
                    cancellation=job.cancellation,
                    deadline=deadline,
                    assert_current=guard,
                )
                job.effect_receipt = {
                    "status": "unknown",
                    "completed_callback": True,
                    "model_fingerprint": ENGINE_FINGERPRINT,
                    "bytes": result["bytes"],
                    "replayed": False,
                }
            except BaseException as failure:
                if (
                    not replacement.exists()
                    and not replacement.is_symlink()
                    and "unknown" not in _error(failure)
                    and "cleanup" not in _error(failure)
                    and getattr(failure, "owner", None) is None
                    and isinstance(getattr(failure, "receipt", None), dict)
                    and failure.receipt.get("status") == "not_published"
                    and failure.receipt.get("cleanup_confirmed") is True
                ):
                    self._write_slots(
                        intent,
                        {**intent, "revision": intent["revision"] + 1, "pending": None},
                        self._cleanup_guard,
                    )
                elif isinstance(failure, _ThreadMutationUnknown) and failure.completed_result:
                    self._record_pending(intent, failure.completed_result, new_id)
                raise
            intent = self._record_pending(intent, result, new_id)
            guard()
            after = {
                **intent,
                "revision": intent["revision"] + 1,
                "pending": None,
                "previous": old,
                "current": {
                    "id": new_id,
                    "identity": list(result["replacement_identity"]),
                    "bytes": result["bytes"],
                    "ownership": intent["pending"]["ownership"],
                },
            }
            self._write_slots(intent, after, guard)
        guard()
        if slots is not None and self._model_control_data["current"] is None:
            pinned = self.model_directory.lstat()
            if list(result.get("root_identity", ())) != [pinned.st_dev, pinned.st_ino]:
                raise MemoryIntelligenceError("memory_model_publication_unknown")
            current = {
                "id": "0" * 32,
                "identity": list(result["root_identity"]),
                "bytes": result["bytes"],
                "ownership": self._ownership("0" * 32, result["root_identity"]),
            }
            before = self._model_control_data
            self._write_slots(
                before, {**before, "revision": before["revision"] + 1, "current": current}, guard
            )
        self._model_metadata = result
        self._model_signature = self._model_files_signature()
        return {
            "status": "completed",
            "model_fingerprint": ENGINE_FINGERPRINT,
            "assets_fingerprint": result["model_fingerprint"],
            "bytes": result["bytes"],
            "cpu_only": True,
            "previous_untouched": bool(
                self._model_control_data and self._model_control_data["previous"]
            ),
        }

    def _record_pending(self, intent, result, identifier):
        identity = result.get("replacement_identity")
        if (
            not isinstance(identity, (tuple, list))
            or len(identity) != 2
            or any(type(n) is not int or n < 0 for n in identity)
        ):
            raise MemoryIntelligenceError("memory_model_cleanup_unknown")
        slot = {
            "id": identifier,
            "identity": list(identity),
            "bytes": result["bytes"],
            "ownership": self._ownership(identifier, identity),
        }
        after = {**intent, "revision": intent["revision"] + 1, "pending": slot}
        self._write_slots(intent, after, self._cleanup_guard)
        return after

    def _ownership(self, identifier, pinned):
        # os.stat_result is also a tuple; its ten fields are not an inode pin.
        if hasattr(pinned, "st_dev") and hasattr(pinned, "st_ino"):
            identity = [pinned.st_dev, pinned.st_ino]
        elif isinstance(pinned, (tuple, list)) and len(pinned) == 2:
            identity = list(pinned)
        else:
            raise MemoryIntelligenceError("memory_model_control_invalid")
        if any(type(value) is not int or value < 0 for value in identity):
            raise MemoryIntelligenceError("memory_model_control_invalid")
        return {
            "format": "jarvis-model-copy-ownership/1",
            "profile_binding": self.binding,
            "slot_id": identifier,
            "root_identity": identity,
            "asset_fingerprint": MODEL_FINGERPRINT,
            "source": "managed_preparation",
        }

    async def _retire_assets(self, job, guard):
        slots = self._model_slots()
        target_key = "previous" if slots["previous"] is not None else "pending"
        old = slots[target_key]
        if old is None or old["id"] != job.copy_id:
            raise MemoryIntelligenceError("memory_model_copy_changed")
        await self._close_engine()
        guard()
        old_path = self._initial_model_directory if old["id"] == "0" * 32 else self._slot_path(old)
        if not old_path.exists() and not old_path.is_symlink() and old["ownership"] is not None:
            proven = next(
                (
                    known.metadata["receipt"]
                    for known in self._jobs.values()
                    if known.metadata["kind"] == "model_retire"
                    and isinstance(known.metadata["receipt"], dict)
                    and known.metadata["receipt"].get("copy_id") == old["id"]
                    and known.metadata["receipt"].get("root_removed") is True
                    and known.metadata["receipt"].get("completed_callback") is True
                ),
                None,
            )
            if proven is not None:
                self._write_slots(
                    slots, {**slots, "revision": slots["revision"] + 1, target_key: None}, guard
                )
                return {
                    **proven,
                    "status": "completed",
                    "reconciled_known_receipt": True,
                    "new_unlinks": 0,
                }

        def before_unlink():
            guard()
            if self._model_control.read() != slots:
                raise MemoryIntelligenceError("memory_model_changed")

        try:
            result = await self._thread(
                retire_model_copy,
                old_path,
                _mutating=True,
                expected_root_identity=tuple(old["identity"]),
                assert_current=before_unlink,
                ownership_record=old["ownership"],
            )
            job.effect_receipt = {
                **result,
                "status": "unknown",
                "completed_callback": True,
                "copy_id": old["id"],
                "replayed": False,
            }
        except _ThreadMutationUnknown as error:
            error.receipt = {**error.receipt, "copy_id": old["id"]}
            # All callback threads are joined. Retain the exact CAS state and
            # receipt; a later owner review can reconcile without repeating an
            # unlink. Cancellation itself never permits another deletion.
            raise
        self._write_slots(
            slots, {**slots, "revision": slots["revision"] + 1, target_key: None}, guard
        )
        return result

    async def rebuild_index(self, params):
        fields = {
            "request_id",
            "expected_generation",
            "expected_policy_revision",
            "expected_model_fingerprint",
        }
        ref, client, scope, policy = self._request(params, fields, fields)
        args = {key: params[key] for key in fields - {"request_id"}}
        existing = self._existing_job(
            ref, client, policy, "index_rebuild", params["request_id"], args
        )
        if existing is not None:
            return {"job": json.loads(json.dumps(existing.metadata))}
        if (
            type(params["expected_generation"]) is not int
            or params["expected_generation"] != self.service.memory.generation(scope)
            or type(params["expected_policy_revision"]) is not int
            or params["expected_policy_revision"] != policy.revision
            or params["expected_model_fingerprint"] != ENGINE_FINGERPRINT
        ):
            raise MemoryIntelligenceError("memory_generation_changed")
        job, fresh = self._new_job(
            ref,
            client,
            policy,
            "index_rebuild",
            params["request_id"],
            args,
            params["expected_generation"],
        )
        if fresh:
            job.task = asyncio.create_task(self._run_job(job, policy))
        return {"job": json.loads(json.dumps(job.metadata))}

    def _job_update(self, job, **changes):
        job.metadata.update(changes)
        if not job.temporary:
            self._persist_jobs()

    async def _run_job(self, job, policy):
        locked = False
        try:
            deadline = time.monotonic() + (300 if job.metadata["kind"] == "model_prepare" else 60)
            await self._lock_lane(job.cancellation, deadline)
            locked = True
            generation = job.metadata["source_generation"]
            guard = self._guard(job.ref, job.client_id, policy, generation)
            guard()
            if job.cancellation.is_set():
                raise MemoryIntelligenceError("memory_cancelled")
            self._job_update(job, phase="running")
            self._model_slots(durable=not job.temporary)
            if job.metadata["kind"] == "model_prepare":
                receipt = await self._prepare_assets(job, guard, deadline)
            elif job.metadata["kind"] == "model_retire":
                receipt = await self._retire_assets(job, guard)
            else:
                scope = self.service._memory_scope(job.ref)
                snapshot = self.service.memory.snapshot(
                    scope, cancellation=job.cancellation, deadline=deadline
                )
                self._job_update(
                    job,
                    progress={
                        "documents_done": 0,
                        "documents_total": len(snapshot.documents),
                        "chunks": 0,
                    },
                )
                engine = await self._verified_engine(guard, job.cancellation, deadline)
                receipt = await self.index.build(
                    snapshot,
                    engine,
                    cancellation=job.cancellation,
                    deadline=deadline,
                    assert_current=guard,
                )
                guard()
                self._job_update(
                    job,
                    progress={
                        "documents_done": receipt["documents"],
                        "documents_total": receipt["documents"],
                        "chunks": receipt["chunks"],
                    },
                )
            self._job_update(job, phase="complete", receipt=receipt)
        except BaseException as failure:
            code = _error(failure)
            cleanup = "cleanup" in code or getattr(failure, "owner", None) is not None
            job.unresolved = (
                cleanup
                or "unknown" in code
                or getattr(job, "effect_receipt", None) is not None
                or (
                    self._model_control_data is not None
                    and self._model_control_data["pending"] is not None
                )
            )
            owner = getattr(failure, "owner", None)
            if owner is not None and owner not in self._pending_owners:
                self._pending_owners.append(owner)
            phase = (
                "cleanup_pending"
                if cleanup
                else "failed"
                if job.unresolved
                else "cancelled"
                if (job.cancellation.is_set() or isinstance(failure, asyncio.CancelledError))
                else "failed"
            )
            with suppress(Exception):
                self._job_update(
                    job,
                    phase=phase,
                    error=code,
                    receipt=getattr(failure, "receipt", None)
                    or getattr(job, "effect_receipt", None)
                    or {
                        "status": "unknown" if job.unresolved else "not_published",
                        "cleanup_confirmed": not cleanup,
                        "replayed": False,
                    },
                )
        finally:
            if locked:
                self._lane.release()
            if not job.unresolved and job.admission is not None:
                job.admission.close()
                job.admission = None

    async def verify_index(self, params):
        ref, client, scope, policy = self._request(params)
        generation = self.service.memory.generation(scope)
        guard = self._guard(ref, client, policy, generation)
        await self._lock_lane(deadline=time.monotonic() + 10)
        try:
            snapshot = self.service.memory.snapshot(scope)
            engine = await self._verified_engine(guard, None, time.monotonic() + 10)
            # Full span scan and source reconciliation; this is not status metadata.
            await self.index.search(
                snapshot,
                "verificación",
                engine,
                mode="semantic_local",
                limit=1,
                deadline=time.monotonic() + 10,
                assert_current=guard,
            )
            guard()
            raw = self.index.status(scope)
            return {
                "verified": True,
                "generation": generation,
                "index_fingerprint": self._page_token(
                    raw, scope, policy, policy.settings.mode, "verificación", 1
                ),
            }
        finally:
            self._lane.release()

    def _find_job(self, params):
        ref, client, _scope, policy = self._request(params, {"job_id"}, {"job_id"})
        if not isinstance(params["job_id"], str) or not _UUID.fullmatch(params["job_id"]):
            raise MemoryIntelligenceError("memory_job_not_found")
        if not ref.temporary:
            self._journal()
        job = self._jobs.get(params["job_id"])
        if job is None or job.client_id != client or job.scope_key != policy.scope.key:
            raise MemoryIntelligenceError("memory_job_not_found")
        return job

    async def get_job(self, params):
        job = self._find_job(params)
        return {"job": json.loads(json.dumps(job.metadata))}

    async def cancel_job(self, params):
        job = self._find_job(params)
        if job.metadata["phase"] in _ACTIVE:
            job.cancellation.set()
            if job.metadata["phase"] != "cleanup_pending":
                self._job_update(job, phase="cancelling")
        # Cancellation request acknowledgement is deliberately not cleanup proof.
        return {"job": json.loads(json.dumps(job.metadata))}

    def _start_background(self):
        if self._background is None and not self._closed:
            self._background = asyncio.create_task(self._auto_index())

    async def _auto_index(self):
        while not self._closed:
            await asyncio.sleep(1.5)
            for ref, client in tuple(self._registrations.values()):
                if self._closed:
                    return
                try:
                    scope, policy = self._scope(ref, client)
                    if not policy.settings.auto_index_enabled or policy.settings.mode == "lexical":
                        continue
                    if any(
                        job.scope_key == policy.scope.key and job.metadata["phase"] in _ACTIVE
                        for job in self._jobs.values()
                    ):
                        continue
                    generation = self.service.memory.generation(scope)
                    raw = self.index.status(scope)
                    if (
                        raw.get("source_generation") == generation
                        and raw.get("model_fingerprint") == ENGINE_FINGERPRINT
                    ):
                        continue
                    # Missing artifacts are an explicit unavailable state, never
                    # an instruction to download during background maintenance.
                    if self._model_files_signature() is None:
                        self._model_error = "missing_model"
                        continue
                    attempt = (generation, policy.digest, self._model_files_signature())
                    if self._auto_attempts.get(policy.scope.key) == attempt:
                        continue
                    job, fresh = self._new_job(
                        ref,
                        client,
                        policy,
                        "index_auto",
                        uuid4().hex,
                        {"source_generation": generation, "policy_digest": policy.digest},
                        generation,
                    )
                    if fresh:
                        job.task = asyncio.create_task(self._run_job(job, policy))
                        self._auto_attempts[policy.scope.key] = attempt
                except Exception:
                    # A stale registration or revoked owner cannot keep a failed
                    # background loop alive or reveal note/source content.
                    continue

    async def forget_temporary(self, ref):
        if not ref.temporary:
            return
        matches = [
            (key, client) for key, (known, client) in self._registrations.items() if known == ref
        ]
        for key, client in matches:
            for job in tuple(self._jobs.values()):
                if job.temporary and job.scope_key == key:
                    job.cancellation.set()
                    if job.task is not None:
                        await asyncio.shield(job.task)
                    if job.unresolved:
                        raise MemoryIntelligenceError("memory_cleanup_pending")
                    self._jobs.pop(job.metadata["id"], None)
            self.policy.forget_temporary(client_id=client, session_id=ref.session_id)
            self._last_recall.pop((key, ref.session_id), None)
            self._auto_attempts.pop(key, None)
            self._registrations.pop(key, None)
        await self._lock_lane(deadline=time.monotonic() + 10)
        try:
            await self.index.forget_temporary(self.service._memory_scope(ref))
        finally:
            self._lane.release()

    async def close(self):
        self._closed = True
        if self._background is not None:
            self._background.cancel()
            with suppress(asyncio.CancelledError):
                await self._background
            self._background = None
        for job in self._jobs.values():
            job.cancellation.set()
        tasks = [job.task for job in self._jobs.values() if job.task is not None]
        for task in tasks + list(self._foreground) + list(self._threads):
            if task is not asyncio.current_task():
                with suppress(Exception, asyncio.CancelledError):
                    await asyncio.shield(task)
        errors = []
        if self._engine is not None:
            try:
                result = await self._engine.close()
                if not result.get("closed"):
                    raise MemoryIntelligenceError("memory_cleanup_pending")
                self._engine = None
            except Exception as error:
                errors.append(error)
        try:
            await self.index.close()
        except Exception as error:
            errors.append(error)
        for owner in tuple(self._pending_owners):
            try:
                owner.cleanup()
                self._pending_owners.remove(owner)
            except Exception as error:
                errors.append(error)
        if not errors:
            for job in self._jobs.values():
                if job.admission is not None:
                    job.admission.close()
                    job.admission = None
                if job.metadata["phase"] == "cleanup_pending":
                    job.metadata.update(phase="failed", error="interrupted_unknown")
                # Unknown effects stay unknown in the finite journal, although
                # all local workers are now proven closed. Never replay them.
        if errors:
            raise MemoryIntelligenceError("memory_cleanup_pending")
        return {
            "closed": True,
            "cleanup_confirmed": True,
            "active_jobs": 0,
            "journal_terminal_window": 32,
            "replayed": False,
        }
