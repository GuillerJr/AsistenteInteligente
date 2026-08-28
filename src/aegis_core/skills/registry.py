from __future__ import annotations

import os
import re
import stat
import threading
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from aegis_core.skills.builtins import BUILTIN_SKILLS
from aegis_core.skills.contracts import (
    SkillActivation,
    SkillDraft,
    SkillManifest,
    SkillOrigin,
)
from aegis_core.tools.broker import ToolBroker

MAX_LEARNED_SKILLS = 128
MAX_SKILL_FILE_BYTES = 32_768


class SkillError(ValueError):
    """Raised when a declarative skill violates the local runtime contract."""


def _read_regular_skill_file(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= MAX_SKILL_FILE_BYTES:
            raise SkillError("skill source size or type is invalid")
        data = os.read(descriptor, MAX_SKILL_FILE_BYTES + 1)
    finally:
        os.close(descriptor)
    if not data or len(data) > MAX_SKILL_FILE_BYTES:
        raise SkillError("skill source size is invalid")
    return data


class SkillStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def load_all(self) -> tuple[SkillManifest, ...]:
        if self.directory.is_symlink():
            return ()
        try:
            entries = sorted(self.directory.glob("*.json"))
        except OSError:
            return ()
        manifests: list[SkillManifest] = []
        for path in entries[:MAX_LEARNED_SKILLS]:
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                data = _read_regular_skill_file(path)
                draft = SkillDraft.model_validate_json(data)
                if path.stem != draft.skill_id:
                    continue
                manifests.append(
                    SkillManifest(
                        **draft.model_dump(mode="python"),
                        origin=SkillOrigin.LEARNED,
                        remote_safe=False,
                    )
                )
            except (OSError, SkillError, UnicodeError, ValidationError, ValueError):
                continue
        return tuple(manifests)

    def learn(self, draft: SkillDraft, *, reserved_ids: frozenset[str]) -> SkillManifest:
        if draft.skill_id in reserved_ids:
            raise SkillError("a learned skill cannot replace a built-in skill")
        self._ensure_private_directory()
        existing = self.load_all()
        if (
            draft.skill_id not in {item.skill_id for item in existing}
            and len(existing) >= MAX_LEARNED_SKILLS
        ):
            raise SkillError("learned skill capacity reached")
        manifest = SkillManifest(
            **draft.model_dump(mode="python"),
            origin=SkillOrigin.LEARNED,
            remote_safe=False,
        )
        target = self.directory / f"{draft.skill_id}.json"
        temporary = self.directory / f".{draft.skill_id}.{uuid4().hex}.tmp"
        payload = draft.model_dump_json(indent=2).encode("utf-8") + b"\n"
        if len(payload) > MAX_SKILL_FILE_BYTES:
            raise SkillError("skill file exceeds size limit")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            os.chmod(target, 0o600)
            self._sync_directory()
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        return manifest

    def forget(self, skill_id: str, *, reserved_ids: frozenset[str]) -> bool:
        if re.fullmatch(r"^[a-z][a-z0-9-]{2,63}$", skill_id) is None:
            raise SkillError("skill identifier is invalid")
        if skill_id in reserved_ids:
            raise SkillError("built-in skills cannot be removed")
        if not self.directory.exists():
            return False
        if self.directory.is_symlink() or not self.directory.is_dir():
            raise SkillError("skill directory is unsafe")
        target = self.directory / f"{skill_id}.json"
        if target.is_symlink():
            raise SkillError("skill file is unsafe")
        try:
            target.unlink()
        except FileNotFoundError:
            return False
        self._sync_directory()
        return True

    def signature(self) -> tuple[int, int]:
        if self.directory.is_symlink():
            return (0, 0)
        try:
            stat = self.directory.stat()
            count = sum(1 for _ in self.directory.glob("*.json"))
        except OSError:
            return (0, 0)
        return (stat.st_mtime_ns, min(count, MAX_LEARNED_SKILLS + 1))

    def _ensure_private_directory(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink() or not self.directory.is_dir():
            raise SkillError("skill directory is unsafe")
        os.chmod(self.directory, 0o700)

    def _sync_directory(self) -> None:
        descriptor = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class SkillRegistry:
    def __init__(
        self,
        broker: ToolBroker,
        store: SkillStore,
        *,
        builtins: tuple[SkillManifest, ...] = BUILTIN_SKILLS,
        plugin_skills: tuple[SkillManifest, ...] = (),
    ) -> None:
        self._broker = broker
        self._store = store
        self._lock = threading.RLock()
        self._builtins = self._validate_unique(builtins)
        self._plugin_skills = self._validate_unique(plugin_skills)
        self._validate_unique((*self._builtins, *self._plugin_skills))
        self._reserved_ids = frozenset(
            item.skill_id for item in (*self._builtins, *self._plugin_skills)
        )
        self._user_signature: tuple[int, int] | None = None
        self._learned: tuple[SkillManifest, ...] = ()
        for manifest in self._builtins:
            self._validate_tool_boundary(manifest)
        for manifest in self._plugin_skills:
            if manifest.origin is not SkillOrigin.PLUGIN:
                raise SkillError("external skill must have plugin origin")
            self._validate_tool_boundary(manifest)

    @property
    def store(self) -> SkillStore:
        return self._store

    @property
    def reserved_ids(self) -> frozenset[str]:
        return self._reserved_ids

    def all(self) -> tuple[SkillManifest, ...]:
        with self._lock:
            self._refresh()
            return (*self._builtins, *self._plugin_skills, *self._learned)

    def learn(self, draft: SkillDraft) -> SkillManifest:
        manifest = SkillManifest(
            **draft.model_dump(mode="python"),
            origin=SkillOrigin.LEARNED,
            remote_safe=False,
        )
        self._validate_tool_boundary(manifest)
        learned = self._store.learn(draft, reserved_ids=self._reserved_ids)
        with self._lock:
            self._user_signature = None
            self._refresh()
        return learned

    def forget(self, skill_id: str) -> bool:
        forgotten = self._store.forget(skill_id, reserved_ids=self._reserved_ids)
        with self._lock:
            self._user_signature = None
            self._refresh()
        return forgotten

    def select(self, text: str) -> SkillActivation | None:
        normalized = " ".join(text.casefold().split())
        terms = frozenset(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))
        candidates: list[SkillActivation] = []
        for manifest in self.all():
            if not manifest.enabled:
                continue
            phrases = tuple(phrase for phrase in manifest.trigger_phrases if phrase in normalized)
            matched_terms = manifest.trigger_terms & terms
            if not phrases and len(matched_terms) < manifest.minimum_term_matches:
                continue
            score = len(phrases) * 100 + len(matched_terms) * 10 + manifest.priority
            candidates.append(
                SkillActivation(
                    manifest=manifest,
                    score=score,
                    matched_phrases=phrases,
                    matched_terms=matched_terms,
                )
            )
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda candidate: (
                candidate.score,
                candidate.manifest.origin is SkillOrigin.BUILTIN,
                candidate.manifest.skill_id,
            ),
        )

    def _refresh(self) -> None:
        signature = self._store.signature()
        if signature == self._user_signature:
            return
        learned = []
        for manifest in self._store.load_all():
            if manifest.skill_id in self._reserved_ids:
                continue
            try:
                self._validate_tool_boundary(manifest)
            except SkillError:
                continue
            learned.append(manifest)
        self._learned = self._validate_unique(tuple(learned))
        self._user_signature = signature

    def _validate_tool_boundary(self, manifest: SkillManifest) -> None:
        available = {
            str(schema["function"]["name"])
            for schema in self._broker.schemas_for(manifest.role)
            if isinstance(schema.get("function"), dict)
        }
        unavailable = manifest.allowed_tools - available
        if unavailable:
            names = ",".join(sorted(unavailable))
            raise SkillError(f"skill exceeds broker tool boundary: {names}")

    @staticmethod
    def _validate_unique(skills: tuple[SkillManifest, ...]) -> tuple[SkillManifest, ...]:
        identifiers = [skill.skill_id for skill in skills]
        if len(identifiers) != len(set(identifiers)):
            raise SkillError("duplicate skill identifier")
        return skills


def load_skill_draft(path: Path) -> SkillDraft:
    if path.is_symlink() or not path.is_file():
        raise SkillError("skill source must be a regular file")
    data = _read_regular_skill_file(path)
    try:
        return SkillDraft.model_validate_json(data)
    except ValidationError as error:
        raise SkillError("skill source is invalid") from error
