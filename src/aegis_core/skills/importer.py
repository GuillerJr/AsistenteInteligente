"""Bounded import of declarative skills; importing never executes a resource."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aegis_core.contracts import AgentRole
from aegis_core.privacy import redact_secrets_for_remote
from aegis_core.secrets import contains_likely_secret_material
from aegis_core.skills.contracts import SKILL_ID_PATTERN, SkillDraft

MAX_SOURCE_BYTES = 32_768
MAX_RESOURCE_BYTES = 8_192
MAX_RESOURCE_FILES = 3
MAX_REFERENCE_CANDIDATES = 16
_TEXT_SUFFIXES = frozenset({".md", ".txt", ".rst"})
_CODE_SUFFIXES = frozenset({".py", ".sh", ".bash", ".js", ".ts", ".rb", ".ps1"})
_LINK = re.compile(r"!?\[[^\]\n]*\]\(\s*<?([^\s)>]+)>?(?:\s+['\"][^\n]*['\"])?\s*\)")
_INLINE_REFERENCE = re.compile(r"`((?:references|resources|scripts)/[^`\n]+)`")
_EXECUTABLE_FENCE = re.compile(
    r"(?m)^\s*(`{3,}|~{3,})\s*(?:bash|sh|shell|zsh|python|py|javascript|js|"
    r"typescript|ts|ruby|powershell|ps1|console|terminal)(?:\s|$)",
    re.IGNORECASE,
)
_EXECUTABLE_PROSE = re.compile(
    r"\b(?:execute|run|ejecuta|ejecutar)\s+(?:the\s+|el\s+)?"
    r"(?:script|python|bash|shell|command|comando)\b|"
    r"\b(?:pip|npm|uv)\s+(?:install|add|run)\b",
    re.IGNORECASE,
)
_SAFE_FENCE_LANGUAGES = frozenset(
    {"text", "plaintext", "markdown", "md", "json", "yaml", "yml", "csv"}
)
_UNSUPPORTED_METADATA = frozenset(
    {"allowed-tools", "allowed_tools", "dependencies", "scripts", "hooks", "command", "mcp"}
)


class SkillImportError(ValueError):
    """A safe diagnostic code, without source content or credential material."""


class SkillImportChoices(BaseModel):
    """Choices explicitly reviewed by the owner; no role or permission is inferred."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_id: str | None = Field(default=None, pattern=SKILL_ID_PATTERN)
    role: AgentRole | None = None
    trigger_phrases: tuple[str, ...] | None = Field(default=None, max_length=16)
    trigger_terms: frozenset[str] | None = Field(default=None, max_length=32)
    minimum_term_matches: int | None = Field(default=None, ge=1, le=4)
    allowed_tools: frozenset[str] | None = Field(default=None, max_length=16)
    starter_tools: frozenset[str] | None = Field(default=None, max_length=3)
    reviewed_resources: tuple[str, ...] = Field(default=(), max_length=MAX_RESOURCE_FILES)
    enabled: bool | None = None


@dataclass(frozen=True, slots=True)
class SkillResourceCandidate:
    path: str
    supported: bool
    diagnostic: str | None = None

    def public_dict(self) -> dict[str, object]:
        return {"path": self.path, "supported": self.supported, "diagnostic": self.diagnostic}


@dataclass(frozen=True, slots=True)
class SkillImportPreview:
    source_sha256: str
    format: str
    skill_id: str
    name: str
    description: str
    instructions: tuple[str, ...]
    resources: tuple[str, ...]
    draft: SkillDraft | None
    diagnostics: tuple[str, ...] = ()
    resource_candidates: tuple[SkillResourceCandidate, ...] = ()
    requires_disabled: bool = False
    source_path: Path | None = None

    def public_dict(self) -> dict[str, object]:
        """Only preview data, never an inferred permission or an executable entry point."""
        return {
            "source_sha256": self.source_sha256,
            "format": self.format,
            "skill_id": self.skill_id,
            "name": self.name,
            "description": self.description,
            "instructions": list(self.instructions),
            "resources": list(self.resources),
            "role": self.draft.role.value if self.draft else None,
            "trigger_phrases": list(self.draft.trigger_phrases) if self.draft else [],
            "trigger_terms": sorted(self.draft.trigger_terms) if self.draft else [],
            "allowed_tools": sorted(self.draft.allowed_tools) if self.draft else [],
            "starter_tools": sorted(self.draft.starter_tools) if self.draft else [],
            "enabled": self.draft.enabled if self.draft else not self.requires_disabled,
            "diagnostics": list(self.diagnostics),
            "resource_candidates": [item.public_dict() for item in self.resource_candidates],
            "requires_disabled": self.requires_disabled,
        }


def _read_text(path: Path, *, maximum: int) -> bytes:
    absolute = path.absolute()
    parent = _directory_descriptor(absolute.parent)
    try:
        descriptor = os.open(
            absolute.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent,
        )
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= maximum:
                raise SkillImportError("source_size_or_type_invalid")
            data = os.read(descriptor, maximum + 1)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise SkillImportError("source_unreadable") from error
    finally:
        os.close(parent)
    if not data or len(data) > maximum:
        raise SkillImportError("source_size_or_type_invalid")
    return data


def _directory_descriptor(path: Path) -> int:
    """Pin every ancestor without following symlinks, including a selected root."""
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            if component == "..":
                raise SkillImportError("source_path_outside_directory")
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except (OSError, ValueError) as error:
        os.close(descriptor)
        raise SkillImportError("source_directory_unreadable_or_linked") from error


def _decode(data: bytes) -> str:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError as error:
        raise SkillImportError("source_must_be_utf8") from error
    if contains_likely_secret_material(text) or redact_secrets_for_remote(text).categories:
        raise SkillImportError("source_contains_secret_material")
    if any(ord(character) < 32 and character not in "\n\r\t" for character in text):
        raise SkillImportError("source_contains_control_characters")
    return text


def _has_executable_content(text: str) -> bool:
    if (
        _EXECUTABLE_FENCE.search(text)
        or _EXECUTABLE_PROSE.search(text)
        or re.search(r"<script\b|\bscripts/[^\s`]+", text, re.IGNORECASE)
    ):
        return True
    opened: tuple[str, int] | None = None
    for line in text.splitlines():
        match = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
        if match is None:
            continue
        marker, info = match.groups()
        if opened is not None:
            if marker[0] == opened[0] and len(marker) >= opened[1] and not info.strip():
                opened = None
            continue
        language = info.strip().split(maxsplit=1)[0].casefold() if info.strip() else ""
        if language not in _SAFE_FENCE_LANGUAGES:
            return True
        opened = (marker[0], len(marker))
    return False


def preview_skill_source(path: Path) -> SkillImportPreview:
    """Read one selected file only. Linked resources remain unopened until review."""
    path = Path(path)
    data = _read_text(path, maximum=MAX_SOURCE_BYTES)
    return _preview(data, source_name=path.name, source_path=path.absolute())


def preview_skill_text(text: str, *, source_name: str = "SKILL.md") -> SkillImportPreview:
    """Preview supplied text without filesystem or network access."""
    if not isinstance(text, str):
        raise SkillImportError("source_must_be_text")
    data = text.encode("utf-8")
    if not 0 < len(data) <= MAX_SOURCE_BYTES:
        raise SkillImportError("source_size_or_type_invalid")
    return _preview(data, source_name=source_name, source_path=None)


def _preview(data: bytes, *, source_name: str, source_path: Path | None) -> SkillImportPreview:
    text = _decode(data)
    digest = hashlib.sha256(data).hexdigest()
    if source_name.casefold().endswith(".json"):
        try:
            draft = SkillDraft.model_validate_json(text)
        except (ValidationError, ValueError) as error:
            raise SkillImportError("json_skill_contract_invalid") from error
        for value in (draft.name, draft.description, *draft.instructions, *draft.resources):
            _decode(value.encode("utf-8"))
        disabled = draft.adaptation_required or any(
            _has_executable_content(value) for value in (*draft.instructions, *draft.resources)
        )
        return SkillImportPreview(
            digest,
            "jarvis-json",
            draft.skill_id,
            draft.name,
            draft.description,
            draft.instructions,
            draft.resources,
            draft,
            diagnostics=("executable_content_disabled",) if disabled else (),
            requires_disabled=disabled,
            source_path=source_path,
        )
    if source_name.casefold() != "skill.md":
        raise SkillImportError("source_format_not_supported")
    metadata, body, diagnostics = _parse_frontmatter(text)
    name = " ".join(metadata["name"].split())
    description = " ".join(metadata["description"].split())
    if (
        not 3 <= len(name) <= 80
        or not 8 <= len(description) <= 300
        or not name.isprintable()
        or not description.isprintable()
    ):
        raise SkillImportError("markdown_metadata_limits_invalid")
    if not body.strip():
        raise SkillImportError("markdown_body_required")
    if len(body.encode("utf-8")) > MAX_RESOURCE_BYTES:
        raise SkillImportError("markdown_body_exceeds_resource_budget")
    candidates, reference_diagnostics = _resource_candidates(body)
    disabled = _has_executable_content(body)
    disabled = disabled or any(
        item.diagnostic == "executable_resource_unsupported" for item in candidates
    )
    if disabled:
        diagnostics.append("executable_content_disabled")
    if any(key in _UNSUPPORTED_METADATA for key in metadata):
        diagnostics.append("external_execution_metadata_ignored")
        disabled = True
    slug = _skill_identifier(name)
    return SkillImportPreview(
        digest,
        "skill-markdown",
        slug,
        name,
        description,
        ("Aplica el procedimiento declarativo revisado, respetando los permisos del broker.",),
        (body,),
        None,
        tuple(dict.fromkeys([*diagnostics, *reference_diagnostics])),
        candidates,
        disabled,
        source_path,
    )


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str, list[str]]:
    """A deliberately small YAML subset: scalar keys and literal/folded block strings."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise SkillImportError("yaml_frontmatter_required")
    end = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        raise SkillImportError("yaml_frontmatter_unclosed")
    values: dict[str, str] = {}
    diagnostics: list[str] = []
    index = 1
    while index < end:
        line = lines[index].rstrip("\r\n")
        index += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_-]*):\s*(.*)", line)
        if match is None:
            raise SkillImportError("yaml_subset_unsupported")
        key, raw = match.groups()
        if key in values:
            raise SkillImportError("yaml_duplicate_key")
        if raw in {"|", "|-", "|+", ">", ">-", ">+"}:
            block: list[str] = []
            while index < end and (
                lines[index].startswith((" ", "\t")) or not lines[index].strip()
            ):
                if lines[index].startswith("\t"):
                    raise SkillImportError("yaml_subset_unsupported")
                block.append(lines[index].strip())
                index += 1
            raw = " ".join(block) if raw.startswith(">") else "\n".join(block)
        else:
            raw = _yaml_scalar(raw)
        values[key] = raw
        if key not in {"name", "description"}:
            diagnostics.append("extra_frontmatter_not_imported")
    if not {"name", "description"}.issubset(values):
        raise SkillImportError("yaml_name_and_description_required")
    return values, "".join(lines[end + 1 :]).strip(), diagnostics


def _yaml_scalar(raw: str) -> str:
    if not raw or raw.startswith(("!", "&", "*", "[", "{", "|", ">")):
        raise SkillImportError("yaml_subset_unsupported")
    if raw.startswith('"'):
        try:
            value = json.loads(raw)
        except ValueError as error:
            raise SkillImportError("yaml_subset_unsupported") from error
        if not isinstance(value, str):
            raise SkillImportError("yaml_subset_unsupported")
        return value
    if raw.startswith("'"):
        if not raw.endswith("'"):
            raise SkillImportError("yaml_subset_unsupported")
        return raw[1:-1].replace("''", "'")
    if ": " in raw or " #" in raw:
        raise SkillImportError("yaml_subset_unsupported")
    return raw


def _skill_identifier(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.casefold()).strip("-")
    if not slug or not slug[0].isalpha():
        slug = f"imported-{slug}".strip("-")
    slug = slug[:64].rstrip("-")
    return slug if re.fullmatch(SKILL_ID_PATTERN, slug) else "imported-skill"


def _reference_candidate(raw: str) -> SkillResourceCandidate | None:
    decoded = unquote(raw)
    split = urlsplit(decoded)
    if split.scheme or split.netloc or decoded.startswith(("/", "\\", "~")):
        return SkillResourceCandidate(raw, False, "external_resource_not_imported")
    path = PurePosixPath(split.path)
    if not split.path or split.path.startswith("#"):
        return None
    if ".." in path.parts or "\\" in decoded or "\x00" in decoded:
        return SkillResourceCandidate(raw, False, "resource_path_outside_skill")
    suffix = path.suffix.casefold()
    if suffix in _CODE_SUFFIXES or path.parts[0] == "scripts":
        return SkillResourceCandidate(raw, False, "executable_resource_unsupported")
    if suffix not in _TEXT_SUFFIXES:
        return SkillResourceCandidate(raw, False, "resource_format_not_supported")
    return SkillResourceCandidate(str(path), True)


def _resource_candidates(body: str) -> tuple[tuple[SkillResourceCandidate, ...], list[str]]:
    candidates: list[SkillResourceCandidate] = []
    seen: set[str] = set()
    for raw in [*_LINK.findall(body), *_INLINE_REFERENCE.findall(body)]:
        candidate = _reference_candidate(raw)
        if candidate is not None and candidate.path not in seen:
            candidates.append(candidate)
            seen.add(candidate.path)
    diagnostics = ["referenced_resources_require_review"] if candidates else []
    if len(candidates) > MAX_REFERENCE_CANDIDATES:
        raise SkillImportError("too_many_resource_references")
    return tuple(candidates), diagnostics


def _read_reviewed_resource(root: Path, relative: str) -> str:
    candidate = _reference_candidate(relative)
    if candidate is None or not candidate.supported or candidate.path != relative:
        raise SkillImportError("reviewed_resource_path_invalid")
    # Open every component relative to the selected skill directory, without
    # following links. No resolve/read fallback can escape that descriptor tree.
    descriptor = _directory_descriptor(root.absolute())
    try:
        parts = PurePosixPath(relative).parts
        for component in parts[:-1]:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        child = os.open(
            parts[-1],
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            dir_fd=descriptor,
        )
        try:
            metadata = os.fstat(child)
            if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= MAX_RESOURCE_BYTES:
                raise SkillImportError("reviewed_resource_size_or_type_invalid")
            data = os.read(child, MAX_RESOURCE_BYTES + 1)
        finally:
            os.close(child)
    except OSError as error:
        raise SkillImportError("reviewed_resource_unreadable") from error
    finally:
        os.close(descriptor)
    if not data or len(data) > MAX_RESOURCE_BYTES:
        raise SkillImportError("reviewed_resource_size_or_type_invalid")
    return _decode(data)


def finalize_skill_import(
    preview: SkillImportPreview,
    choices: SkillImportChoices | Mapping[str, Any] | None = None,
) -> SkillDraft:
    """Produce a validated draft only; persistence/permission review belongs to the caller."""
    try:
        selected = (
            choices
            if isinstance(choices, SkillImportChoices)
            else SkillImportChoices.model_validate(choices or {})
        )
    except ValidationError as error:
        raise SkillImportError("import_choices_invalid") from error
    if preview.source_path is not None:
        current = preview_skill_source(preview.source_path)
        if current.source_sha256 != preview.source_sha256:
            raise SkillImportError("source_changed_since_preview")
        preview = current
    payload = (
        preview.draft.model_dump(mode="python")
        if preview.draft
        else {
            "skill_id": preview.skill_id,
            "name": preview.name,
            "description": preview.description,
            "instructions": preview.instructions,
            "resources": preview.resources,
        }
    )
    for key, value in selected.model_dump(mode="python", exclude_none=True).items():
        if key != "reviewed_resources":
            payload[key] = value
    if selected.trigger_terms and selected.minimum_term_matches is None:
        payload["minimum_term_matches"] = min(
            payload.get("minimum_term_matches", 2),
            len(selected.trigger_terms),
        )
    if "role" not in payload or not (
        payload.get("trigger_phrases") or payload.get("trigger_terms")
    ):
        raise SkillImportError("owner_role_and_triggers_required")
    resources = list(payload.get("resources", ()))
    approved = {item.path for item in preview.resource_candidates if item.supported}
    if len(selected.reviewed_resources) != len(set(selected.reviewed_resources)):
        raise SkillImportError("reviewed_resources_must_be_unique")
    disabled = preview.requires_disabled
    for relative in selected.reviewed_resources:
        if preview.source_path is None or relative not in approved:
            raise SkillImportError("reviewed_resource_not_in_preview")
        resource = _read_reviewed_resource(preview.source_path.parent, relative)
        disabled = disabled or _has_executable_content(resource)
        resources.append(resource)
    if sum(len(value.encode("utf-8")) for value in resources) > MAX_RESOURCE_BYTES:
        raise SkillImportError("import_resources_exceed_budget")
    payload["resources"] = tuple(resources)
    if disabled:
        payload["enabled"] = False
        payload["adaptation_required"] = True
    try:
        return SkillDraft.model_validate(payload)
    except ValidationError as error:
        raise SkillImportError("imported_skill_contract_invalid") from error
