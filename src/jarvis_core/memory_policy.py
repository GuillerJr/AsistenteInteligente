"""Owner-selected memory automation policy; never a source-access grant.

The sidecar is outside portable profile data, bound to its physical directory
and the authenticated client/agent/workspace. Temporary policy is RAM-only and
session-scoped. The coordinator supplies authority checks immediately before a
reviewed JSON publication; model tools cannot configure this policy.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from jarvis_core.client_identity import profile_binding
from jarvis_core.private_json import PrivateJsonDocument

_HASH = re.compile(r"[a-f0-9]{64}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_MAX_REVISION = (1 << 52) - 1
_MODES = frozenset({"lexical", "semantic_local", "hybrid_local"})
_SETTINGS = {"auto_recall_enabled", "mode", "auto_index_enabled", "source_policy"}


class MemoryPolicyError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True)
class MemoryPolicySettings:
    auto_recall_enabled: bool = False
    mode: str = "lexical"
    auto_index_enabled: bool = False
    source_policy: str = "approved_notes_only"

    def __post_init__(self):
        if (type(self.auto_recall_enabled) is not bool
                or type(self.auto_index_enabled) is not bool
                or not isinstance(self.mode, str) or self.mode not in _MODES
                or self.source_policy != "approved_notes_only"):
            raise MemoryPolicyError("memory_policy_settings_invalid")

    def metadata(self):
        return {"auto_recall_enabled": self.auto_recall_enabled, "mode": self.mode,
                "auto_index_enabled": self.auto_index_enabled, "source_policy": self.source_policy}

    @classmethod
    def parse(cls, value):
        if not isinstance(value, Mapping) or set(value) != _SETTINGS:
            raise MemoryPolicyError("memory_policy_settings_invalid")
        return cls(**dict(value))


@dataclass(frozen=True)
class MemoryPolicyScope:
    client_id: str
    agent_id: str
    workspace_id: str
    temporary: bool = False
    session_id: str | None = None

    def __post_init__(self):
        if (not isinstance(self.client_id, str) or not _HASH.fullmatch(self.client_id)
                or not isinstance(self.workspace_id, str) or not _HASH.fullmatch(self.workspace_id)
                or not isinstance(self.agent_id, str) or not _IDENTIFIER.fullmatch(self.agent_id)
                or type(self.temporary) is not bool
                or (self.temporary and (not isinstance(self.session_id, str)
                    or not _IDENTIFIER.fullmatch(self.session_id)))
                or (not self.temporary and self.session_id is not None)):
            raise MemoryPolicyError("memory_policy_scope_invalid")

    def metadata(self):
        return {"client_id": self.client_id, "agent_id": self.agent_id,
                "workspace_id": self.workspace_id, "temporary": self.temporary,
                "session_id": self.session_id}

    @property
    def key(self):
        return _digest(self.metadata())


@dataclass(frozen=True)
class MemoryPolicySnapshot:
    profile_binding: str
    scope: MemoryPolicyScope
    revision: int
    settings: MemoryPolicySettings

    def unsigned_metadata(self):
        return {"profile_binding": self.profile_binding, "scope": self.scope.metadata(),
                "revision": self.revision, "settings": self.settings.metadata(),
                "permissions_portable": False}

    @property
    def digest(self):
        return _digest(self.unsigned_metadata())

    def metadata(self):
        return {**self.unsigned_metadata(), "digest": self.digest}


@dataclass(frozen=True)
class MemoryPolicyReview:
    review_id: str
    before: MemoryPolicySnapshot
    settings: MemoryPolicySettings
    expires_at: float
    review_digest: str

    def unsigned_metadata(self):
        return {"kind": "memory.policy.configure", "review_id": self.review_id,
                "profile_binding": self.before.profile_binding,
                "scope": self.before.scope.metadata(), "before_revision": self.before.revision,
                "before_digest": self.before.digest, "settings": self.settings.metadata(),
                "source_resources_opened": False, "permissions_portable": False}

    def metadata(self):
        return self.unsigned_metadata()


class MemoryPolicyStore:
    def __init__(self, profile: Path, *, clock: Callable[[], float] = time.monotonic):
        self.profile = Path(profile)
        if (not self.profile.is_absolute() or ".." in self.profile.parts
                or self.profile.name in {"", ".", ".."}):
            raise MemoryPolicyError("memory_policy_profile_invalid")
        self.binding = profile_binding(self.profile)
        self.directory = self.profile.with_name(self.profile.name + ".memory-control")
        self.clock = clock
        self._lock = threading.RLock()
        self._durable_document = None
        self._volatile_revision = 1
        self._volatile = {}

    def _assert_binding(self):
        if profile_binding(self.profile) != self.binding:
            raise MemoryPolicyError("memory_policy_profile_changed")

    def _validate(self, value):
        if (not isinstance(value, dict) or set(value) != {
                "version", "profile_binding", "revision", "policies"}
                or type(value["version"]) is not int or value["version"] != 1
                or value["profile_binding"] != self.binding
                or type(value["revision"]) is not int
                or not 1 <= value["revision"] <= _MAX_REVISION
                or not isinstance(value["policies"], list) or len(value["policies"]) > 128):
            raise MemoryPolicyError("memory_policy_registry_invalid")
        keys = set()
        for row in value["policies"]:
            if not isinstance(row, dict) or set(row) != {
                    "client_id", "agent_id", "workspace_id", "settings"}:
                raise MemoryPolicyError("memory_policy_registry_invalid")
            scope = MemoryPolicyScope(row["client_id"], row["agent_id"], row["workspace_id"])
            if scope.key in keys:
                raise MemoryPolicyError("memory_policy_registry_invalid")
            keys.add(scope.key)
            MemoryPolicySettings.parse(row["settings"])

    def _document(self):
        # Deliberately lazy: a temporary conversation never even opens the
        # durable policy registry or creates its sidecar directory.
        if self._durable_document is None:
            self._durable_document = PrivateJsonDocument(self.directory, "policy.json",
                initial=lambda: {"version": 1, "profile_binding": self.binding,
                                 "revision": 1, "policies": []},
                validate=self._validate, max_bytes=131072)
        return self._durable_document

    def _from_data(self, scope, data):
        settings = MemoryPolicySettings()
        for row in data["policies"]:
            if (row["client_id"], row["agent_id"], row["workspace_id"]) == (
                    scope.client_id, scope.agent_id, scope.workspace_id):
                settings = MemoryPolicySettings.parse(row["settings"])
                break
        return MemoryPolicySnapshot(self.binding, scope, data["revision"], settings)

    def snapshot(self, *, client_id, agent_id, workspace_id, temporary=False, session_id=None):
        scope = MemoryPolicyScope(client_id, agent_id, workspace_id, temporary, session_id)
        with self._lock:
            self._assert_binding()
            if scope.temporary:
                row = self._volatile.get(scope.key)
                return MemoryPolicySnapshot(self.binding, scope, self._volatile_revision,
                                            row[1] if row else MemoryPolicySettings())
            return self._from_data(scope, self._document().read())

    def assert_current(self, snapshot: MemoryPolicySnapshot):
        if (not isinstance(snapshot, MemoryPolicySnapshot)
                or snapshot.profile_binding != self.binding):
            raise MemoryPolicyError("memory_policy_changed")
        current = self.snapshot(**snapshot.scope.metadata())
        if current.digest != snapshot.digest:
            raise MemoryPolicyError("memory_policy_changed")

    def review(self, *, client_id, agent_id, workspace_id, settings,
               expected_revision, expected_digest, temporary=False, session_id=None):
        settings = settings if isinstance(settings, MemoryPolicySettings) else \
            MemoryPolicySettings.parse(settings)
        snapshot = self.snapshot(client_id=client_id, agent_id=agent_id, workspace_id=workspace_id,
                                 temporary=temporary, session_id=session_id)
        if (type(expected_revision) is not int or snapshot.revision != expected_revision
                or snapshot.digest != expected_digest):
            raise MemoryPolicyError("memory_policy_changed")
        expires = self.clock() + 120
        review = MemoryPolicyReview(uuid4().hex, snapshot, settings, expires, "")
        return MemoryPolicyReview(review.review_id, snapshot, settings, expires,
                                  _digest(review.unsigned_metadata()))

    def _check_review(self, review):
        if (not isinstance(review, MemoryPolicyReview)
                or not isinstance(review.review_id, str)
                or re.fullmatch(r"[a-f0-9]{32}", review.review_id) is None
                or type(review.expires_at) not in {int, float}
                or not math.isfinite(review.expires_at)
                or review.review_digest != _digest(review.unsigned_metadata())):
            raise MemoryPolicyError("memory_policy_review_invalid")
        if self.clock() >= review.expires_at:
            raise MemoryPolicyError("memory_policy_review_expired")
        if review.before.profile_binding != self.binding:
            raise MemoryPolicyError("memory_policy_profile_changed")

    def apply(self, review: MemoryPolicyReview, *, assert_current: Callable[[], None],
              before_publish=None):
        if not callable(assert_current) or (before_publish is not None
                                           and not callable(before_publish)):
            raise MemoryPolicyError("memory_policy_guard_required")
        with self._lock:
            self._check_review(review)
            self.assert_current(review.before)
            assert_current()
            scope = review.before.scope
            if scope.temporary:
                if scope.key not in self._volatile and len(self._volatile) >= 128:
                    raise MemoryPolicyError("memory_policy_capacity")
                if self._volatile_revision == _MAX_REVISION:
                    raise MemoryPolicyError("memory_policy_capacity")
                self._assert_binding()
                self._check_review(review)
                assert_current()
                self._volatile[scope.key] = scope, review.settings
                self._volatile_revision += 1
                return self.snapshot(**scope.metadata())

            def change(data):
                current = self._from_data(scope, data)
                if current.digest != review.before.digest:
                    raise MemoryPolicyError("memory_policy_changed")
                if data["revision"] == _MAX_REVISION:
                    raise MemoryPolicyError("memory_policy_capacity")
                replacement = {"client_id": scope.client_id, "agent_id": scope.agent_id,
                    "workspace_id": scope.workspace_id, "settings": review.settings.metadata()}
                for index, row in enumerate(data["policies"]):
                    if (row["client_id"], row["agent_id"], row["workspace_id"]) == (
                            scope.client_id, scope.agent_id, scope.workspace_id):
                        data["policies"][index] = replacement
                        break
                else:
                    if len(data["policies"]) >= 128:
                        raise MemoryPolicyError("memory_policy_capacity")
                    data["policies"].append(replacement)
                data["revision"] += 1
                return data

            def guard(staging):
                self._assert_binding()
                self._check_review(review)
                assert_current()
                if before_publish is not None:
                    before_publish(staging)

            # Preserve PrivateJsonError.owner and durability uncertainty exactly;
            # this layer never retries or reconstructs a successful publication.
            data = self._document().update(change, before_publish=guard)
            return self._from_data(scope, data)

    def forget_temporary(self, *, client_id, session_id):
        if (not isinstance(client_id, str) or not _HASH.fullmatch(client_id)
                or not isinstance(session_id, str) or not _IDENTIFIER.fullmatch(session_id)):
            raise MemoryPolicyError("memory_policy_scope_invalid")
        with self._lock:
            selected = [key for key, (scope, _settings) in self._volatile.items()
                        if scope.client_id == client_id and scope.session_id == session_id]
            if not selected:
                return
            if self._volatile_revision == _MAX_REVISION:
                raise MemoryPolicyError("memory_policy_capacity")
            for key in selected:
                self._volatile.pop(key)
            self._volatile_revision += 1
