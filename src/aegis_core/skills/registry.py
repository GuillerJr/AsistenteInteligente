from __future__ import annotations

import json
import os
import re
import stat
import threading
import unicodedata
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from aegis_core.request_intent import mask_quoted_text
from aegis_core.skills.builtins import BUILTIN_SKILLS
from aegis_core.skills.contracts import (
    SkillActivation,
    SkillDraft,
    SkillManifest,
    SkillOrigin,
)
from aegis_core.skills.policy import (
    MAX_SKILL_PREVIEW_BYTES,
    SkillPolicyStore,
    skill_content_sha256,
    skill_preview_bytes,
)
from aegis_core.tools.broker import ToolBroker

MAX_LEARNED_SKILLS = 128
MAX_SKILL_FILE_BYTES = 32_768
MAX_ACTIVE_SKILLS = 3
MAX_SEMANTIC_CATALOGUE_BYTES = 4096
SkillSourceSignature = tuple[tuple[str, int, int, int, int, int], ...]


def _public_builtin(manifest: SkillManifest) -> bool:
    return manifest.origin is SkillOrigin.BUILTIN and manifest.remote_safe


def _remote_entry(manifest: SkillManifest) -> dict:
    return {
        "skill_id": manifest.skill_id,
        "name": manifest.name,
        "content_sha256": skill_content_sha256(manifest),
        "instructions": list(manifest.instructions),
        "resources": list(manifest.resources),
    }


def normalize_skill_text(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    folded = "".join(character for character in folded if not unicodedata.combining(character))
    return " ".join(re.findall(r"[^\W_]+", folded, flags=re.UNICODE))


_SKILL_CONTINUATIONS = frozenset(
    {
        "continua",
        "continua con eso",
        "continua con la tarea",
        "continua con lo anterior",
        "continua con el siguiente paso",
        "sigue",
        "sigue con eso",
        "sigue con la tarea",
        "sigue con el siguiente paso",
        "hazlo",
        "si",
        "si hazlo",
        "ok",
        "adelante",
        "el siguiente paso",
        "ahora el siguiente paso",
        "y despues",
        "y luego",
        "comprueba eso",
        "verifica eso",
        "termina eso",
    }
)
_SKILL_ACKNOWLEDGEMENTS = frozenset(
    {"gracias", "muchas gracias", "perfecto", "entendido", "de acuerdo", "vale", "ok gracias"}
)
_SELECTION_CLAUSES = re.compile(r";|\n|(?<=[.!?])\s+|\b(?:pero|en cambio|but|instead)\b", re.I)
_SELECTION_PREFIX = re.compile(
    r"^(?:(?:hola(?: jarvis)?|jarvis|por favor|please) )*"
    r"(?:(?:me )?(?:puedes|podrias) |(?:quiero|necesito) (?:que )?|can you |could you )?"
)
_NON_ACTION_EXPLANATION = re.compile(
    r"^(?:traduce|traducir|traduzcas|translate|"
    r"que (?:significa|quiere decir|es|son)|what (?:is|are|does)|"
    r"(?:explica|explicame|explicar|expliques|describe|defineme|define) "
    r"(?:como|que (?:es|son|significa)|para que)|"
    r"cuales? (?:es|son) (?:la|el|las|los) (?:diferencia|significado)|"
    r"como (?:se dice|se traduce))\b"
)
_EXPLANATION_VERB = re.compile(
    r"^(?:explica|explicame|explicar|expliques|describe|describeme|define|defineme|"
    r"explain|describe|define)\b"
)
_EXPLANATION_SOURCE_TERMS = frozenset(
    {"archivo", "archivos", "documento", "documentos", "adjunto", "adjuntos", "csv", "codigo",
     "datos", "fallos", "error", "errores", "file", "files", "document", "documents", "code"}
)
_CATALOGUE_STOP_WORDS = frozenset(
    {
        "a", "al", "con", "de", "del", "el", "en", "es", "la", "las", "lo", "los", "me",
        "mi", "mis", "para", "por", "que", "se", "su", "sus", "un", "una", "y", "the", "of",
        "and", "to", "in", "my", "analiza", "ayudame", "busca", "comprueba", "explica", "haz",
        "necesito", "puedes", "quiero", "resume", "revisa", "verifica", "favor", "please",
    }
)


def selection_instruction_text(text: str) -> str:
    """Choose procedures from instructions, never quoted payload or a denied operation."""
    clauses = []
    for clause in _SELECTION_CLAUSES.split(mask_quoted_text(text)):
        clause = re.split(
            r"\b(?:sin|without)\s+(?:acceder|consultar|"
            r"leer(?=\s+(?:mis?|el|la|los|las|un[oa]?s?|ning[uú]n[oa]?s?|nada|"
            r"est[ea]s?|estos|tus?|sus?)\b)|abrir|enviar|modificar|"
            r"borrar|ejecutar|usar|accessing|reading|opening|sending|modifying|"
            r"deleting|executing|using)\b", clause, maxsplit=1, flags=re.I,
        )[0]
        normalized = _SELECTION_PREFIX.sub("", normalize_skill_text(clause))
        if not normalized or re.match(
            r"^(?:no|nunca|jamas|evita|dont|do not|never|avoid)\b", normalized
        ):
            continue
        if _NON_ACTION_EXPLANATION.match(normalized):
            continue
        if (
            _EXPLANATION_VERB.match(normalized)
            and _EXPLANATION_SOURCE_TERMS.isdisjoint(normalized.split())
        ):
            continue
        clauses.append(clause.strip())
    return "; ".join(clauses)


def _continuation_key(text: str) -> str:
    normalized = _SELECTION_PREFIX.sub("", normalize_skill_text(mask_quoted_text(text)))
    return re.sub(r" (?:por favor|please)$", "", normalized)


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

    def signature(self) -> SkillSourceSignature:
        if self.directory.is_symlink():
            return ()
        try:
            entries = sorted(self.directory.glob("*.json"))[: MAX_LEARNED_SKILLS + 1]
            signature = []
            for path in entries:
                metadata = path.lstat()
                signature.append(
                    (
                        path.name,
                        metadata.st_ino,
                        metadata.st_mtime_ns,
                        metadata.st_ctime_ns,
                        metadata.st_size,
                        metadata.st_mode,
                    )
                )
        except OSError:
            return ()
        return tuple(signature)

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
        policy: SkillPolicyStore | None = None,
        plugin_available: Callable[[str], bool] | None = None,
    ) -> None:
        self._broker = broker
        self._store = store
        self.policy = policy or SkillPolicyStore(store.directory.parent / "skills_policy.json")
        self._plugin_available = plugin_available
        self._lock = threading.RLock()
        self._builtins = self._validate_unique(builtins)
        self._plugin_skills = self._validate_unique(plugin_skills)
        self._validate_unique((*self._builtins, *self._plugin_skills))
        self._reserved_ids = frozenset(
            item.skill_id for item in (*self._builtins, *self._plugin_skills)
        )
        self._user_signature: SkillSourceSignature | None = None
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
            plugins = tuple(
                manifest for manifest in self._plugin_skills if self._plugin_is_available(manifest)
            )
            return (*self._builtins, *plugins, *self._learned)

    def _plugin_is_available(self, manifest: SkillManifest) -> bool:
        if manifest.origin is not SkillOrigin.PLUGIN or self._plugin_available is None:
            return True
        try:
            return self._plugin_available(manifest.skill_id) is True
        except Exception:
            return False

    def reload(self) -> None:
        with self._lock:
            self._user_signature = None
            self._refresh()

    def remove_plugin_skills(self, identifiers: Sequence[str]) -> None:
        with self._lock:
            removed = frozenset(identifiers)
            self._plugin_skills = tuple(
                item for item in self._plugin_skills if item.skill_id not in removed
            )

    def _manifest(self, skill_id: str) -> SkillManifest:
        manifest = next((item for item in self.all() if item.skill_id == skill_id), None)
        if manifest is None:
            raise SkillError("skill is unavailable")
        return manifest

    def describe_manifest(self, manifest: SkillManifest) -> dict:
        size = skill_preview_bytes(manifest)
        allowed = self.policy.remote_allowed(manifest)
        return {
            "skill_id": manifest.skill_id,
            "name": manifest.name,
            "description": manifest.description,
            "origin": manifest.origin.value,
            "role": manifest.role.value,
            "enabled": self.policy.enabled(manifest),
            "content_sha256": skill_content_sha256(manifest),
            "remote_allowed": allowed,
            "remote_authorized": self.policy.remote_authorized(manifest),
            "builtin_public": manifest.origin is SkillOrigin.BUILTIN and manifest.remote_safe,
            "approval_required": not allowed,
            "instruction_count": len(manifest.instructions),
            "resource_count": len(manifest.resources),
            "preview_bytes": size,
            "preview_available": size <= MAX_SKILL_PREVIEW_BYTES,
            "source_version": manifest.source_version,
            "adaptation_required": manifest.adaptation_required,
            "allowed_tools": sorted(manifest.allowed_tools),
            "starter_tools": sorted(manifest.starter_tools),
        }

    def catalogue(self) -> list[dict]:
        return [self.describe_manifest(manifest) for manifest in self.all()]

    def preview(self, skill_id: str, content_sha256: str) -> dict:
        manifest = self._manifest(skill_id)
        if skill_content_sha256(manifest) != content_sha256:
            raise SkillError("skill content changed; refresh the preview")
        result = {
            **self.describe_manifest(manifest),
            "instructions": list(manifest.instructions),
            "resources": list(manifest.resources),
        }
        if skill_preview_bytes(manifest) > MAX_SKILL_PREVIEW_BYTES:
            raise SkillError("skill exceeds the complete preview sharing limit")
        return result

    def set_remote_consent(self, skill_id: str, content_sha256: str, approved: bool) -> dict:
        manifest = self._manifest(skill_id)
        if approved:
            self.preview(skill_id, content_sha256)
        self.policy.set_choice(manifest, content_sha256, approved=approved)
        return self.describe_manifest(manifest)

    def set_enabled(self, skill_id: str, content_sha256: str, enabled: bool) -> dict:
        manifest = self._manifest(skill_id)
        if enabled and manifest.adaptation_required:
            raise SkillError("skill requires declarative adaptation before activation")
        self.policy.set_choice(manifest, content_sha256, enabled=enabled)
        return self.describe_manifest(manifest)

    def remote_context(
        self, activations: Sequence[SkillActivation], *, public_only: bool = False
    ) -> list[dict]:
        result: list[dict] = []
        for activation in activations[:MAX_ACTIVE_SKILLS]:
            try:
                current = self._manifest(activation.manifest.skill_id)
            except SkillError:
                continue
            if (
                (public_only and not _public_builtin(current))
                or skill_content_sha256(current) != skill_content_sha256(activation.manifest)
                or not self.policy.remote_allowed(current)
            ):
                continue
            entry = _remote_entry(current)
            if (
                len(json.dumps([*result, entry], ensure_ascii=False).encode())
                <= MAX_SKILL_PREVIEW_BYTES
            ):
                result.append(entry)
        return result

    def semantic_catalogue(self, *, query: str = "", public_only: bool = False) -> list[dict]:
        # Filter consent and identity before computing relevance. Unapproved/private
        # manifests must not affect either the transmitted catalogue or its ordering.
        manifests = [
            manifest for manifest in self.all()
            if not (public_only and not _public_builtin(manifest))
            and self.policy.remote_allowed(manifest)
        ]
        query_terms = frozenset(
            normalize_skill_text(selection_instruction_text(query)).split()
        ) - _CATALOGUE_STOP_WORDS
        if query_terms:
            fields = {
                manifest.skill_id: (
                    frozenset(normalize_skill_text(manifest.name).split()),
                    frozenset(normalize_skill_text(" ".join(
                        (*manifest.trigger_phrases, *sorted(manifest.trigger_terms))
                    )).split()),
                    frozenset(normalize_skill_text(manifest.description).split()),
                )
                for manifest in manifests
            }
            frequencies = Counter(
                term for groups in fields.values() for term in frozenset().union(*groups)
            )
            total_manifests = len(manifests)

            def relevance(manifest: SkillManifest) -> int:
                names, triggers, descriptions = fields[manifest.skill_id]
                return sum(
                    (total_manifests - frequencies[term] + 1)
                    * (3 * (term in names) + 2 * (term in triggers) + (term in descriptions))
                    for term in query_terms
                )

            # Stable sort preserves the existing public/plugin/learned order for ties.
            manifests.sort(key=relevance, reverse=True)
        result = []
        for manifest in manifests:
            entry = {
                "skill_id": manifest.skill_id,
                "name": manifest.name,
                "description": manifest.description,
                "role": manifest.role.value,
                "content_sha256": skill_content_sha256(manifest),
            }
            if (
                len(json.dumps([*result, entry], ensure_ascii=False).encode())
                > MAX_SEMANTIC_CATALOGUE_BYTES
            ):
                # A large entry near the limit must not hide a later entry that fits.
                continue
            result.append(entry)
        return result

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
        selected = self.select_many(text)
        return selected[0] if selected else None

    def _candidates(self, text: str, *, public_only: bool = False) -> list[SkillActivation]:
        normalized = normalize_skill_text(selection_instruction_text(text))
        terms = frozenset(normalized.split())
        candidates: list[SkillActivation] = []
        for manifest in self.all():
            if (public_only and not _public_builtin(manifest)) or not self.policy.enabled(manifest):
                continue
            phrase_forms: set[str] = set()
            phrases_list: list[str] = []
            for phrase in manifest.trigger_phrases:
                phrase_form = normalize_skill_text(phrase)
                if phrase_form not in phrase_forms and f" {phrase_form} " in f" {normalized} ":
                    phrase_forms.add(phrase_form)
                    phrases_list.append(phrase)
            phrases = tuple(phrases_list)
            matched_terms = frozenset(
                term for term in manifest.trigger_terms if normalize_skill_text(term) in terms
            )
            matched_count = len({normalize_skill_text(term) for term in matched_terms})
            if not phrases and matched_count < manifest.minimum_term_matches:
                continue
            # Every exact phrase outranks every possible terms-only match (<=420).
            # Priority still breaks ties within the same kind of activation.
            score = (bool(phrases) * 1000 + len(phrases) * 50
                     + matched_count * 10 + manifest.priority)
            candidates.append(
                SkillActivation(
                    manifest=manifest,
                    score=score,
                    matched_phrases=phrases,
                    matched_terms=matched_terms,
                )
            )
        return sorted(
            candidates,
            reverse=True,
            key=lambda candidate: (
                candidate.score,
                candidate.manifest.origin is SkillOrigin.BUILTIN,
                candidate.manifest.skill_id,
            ),
        )

    def select_many(
        self, text: str, *, limit: int = MAX_ACTIVE_SKILLS, public_only: bool = False
    ) -> tuple[SkillActivation, ...]:
        if not 1 <= limit <= MAX_ACTIVE_SKILLS:
            raise SkillError("active skill limit is invalid")
        candidates = self._candidates(text, public_only=public_only)
        if not candidates:
            return ()
        primary = candidates[0]
        selected: list[SkillActivation] = []
        context: list[dict] = []
        for item in candidates:
            if item.manifest.role is not primary.manifest.role:
                continue
            if self.policy.remote_allowed(item.manifest):
                proposed = [*context, _remote_entry(item.manifest)]
                if len(json.dumps(proposed, ensure_ascii=False).encode()) > MAX_SKILL_PREVIEW_BYTES:
                    continue
                context = proposed
            selected.append(item)
            if len(selected) == limit:
                break
        return tuple(selected)

    def selection_needs_semantic(self, text: str, *, public_only: bool = False) -> bool:
        normalized = _SELECTION_PREFIX.sub(
            "", normalize_skill_text(selection_instruction_text(text))
        )
        candidates = self._candidates(text, public_only=public_only)
        if candidates and candidates[0].matched_phrases:
            return False
        if candidates:
            return (
                len(candidates) > 1
                and candidates[0].manifest.role is not candidates[1].manifest.role
                and candidates[0].score - candidates[1].score < 40
            )
        words = normalized.split()
        return (
            len(words) >= 4
            and _continuation_key(normalized) not in _SKILL_CONTINUATIONS
            and words[0]
            in {
                "analiza",
                "ayudame",
                "ayuda",
                "investiga",
                "comprueba",
                "verifica",
                "necesito",
                "quiero",
                "puedes",
                "desarrolla",
                "organiza",
                "ordena",
                "revisa",
                "arregla",
                "haz",
                "compara",
                "resume",
                "explica",
            }
        )

    def resolve_semantic(
        self, identifiers: Sequence[str], catalogue: list[dict], *, public_only: bool = False
    ) -> tuple[SkillActivation, ...]:
        if not 1 <= len(identifiers) <= MAX_ACTIVE_SKILLS or len(set(identifiers)) != len(
            identifiers
        ):
            raise SkillError("invalid semantic skill selection")
        reviewed = {entry["skill_id"]: entry["content_sha256"] for entry in catalogue}
        selected = []
        for identifier in identifiers:
            manifest = self._manifest(identifier)
            if (
                (public_only and not _public_builtin(manifest))
                or reviewed.get(identifier) != skill_content_sha256(manifest)
                or not self.policy.remote_allowed(manifest)
            ):
                raise SkillError("semantic skill is unavailable or unapproved")
            if selected and selected[0].manifest.role is not manifest.role:
                raise SkillError("semantic skills have incompatible roles")
            selected.append(SkillActivation(manifest=manifest, score=1))
        if (
            len(
                json.dumps(
                    [_remote_entry(item.manifest) for item in selected], ensure_ascii=False
                ).encode()
            )
            > MAX_SKILL_PREVIEW_BYTES
        ):
            raise SkillError("semantic skills exceed the complete guidance budget")
        return tuple(selected)

    def selection_text(self, text: str, history: Sequence = ()) -> str:
        if _continuation_key(text) not in _SKILL_CONTINUATIONS:
            return selection_instruction_text(text)
        for turn in reversed(history[-8:]):
            role = turn.get("role") if isinstance(turn, dict) else getattr(turn, "role", None)
            content = (
                turn.get("content") if isinstance(turn, dict) else getattr(turn, "content", None)
            )
            if role != "user" or not isinstance(content, str):
                continue
            if (
                _continuation_key(content) in _SKILL_CONTINUATIONS
                or normalize_skill_text(content) in _SKILL_ACKNOWLEDGEMENTS
            ):
                continue
            return selection_instruction_text(content)
        return selection_instruction_text(text)

    def select_for_request(
        self, text: str, history: Sequence = (), *, public_only: bool = False
    ) -> tuple[SkillActivation, ...]:
        return self.select_many(self.selection_text(text, history), public_only=public_only)

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
