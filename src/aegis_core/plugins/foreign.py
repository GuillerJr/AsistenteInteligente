"""Codex and Claude plugins in Jarvis (both apps' "complementos").

A Codex plugin (``.codex-plugin/plugin.json``) and a Claude plugin
(``.claude-plugin/plugin.json``) bundle skills, slash commands, MCP servers, apps,
hooks and subagents. Jarvis installs the declarative part, skills and commands, as
its own learned skills, through the same importer as a SKILL.md file: anything
executable stays out. MCP servers (they run a program or reach a service with their
own sign-in), ChatGPT apps, hooks and subagents are listed as left out, so the owner
sees exactly what was not brought over.

Plugins come from the ones Codex and Claude already installed on this Mac and from
marketplaces the owner adds: a GitHub repository with a ``marketplace.json``, the file
both apps read (``.agents/plugins/`` for Codex, ``.claude-plugin/`` for Claude).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import shutil
import stat
import tarfile
import threading
import time
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from aegis_core.contracts import AgentRole
from aegis_core.settings_store import read_private_json, write_private_json
from aegis_core.skills.contracts import SKILL_ID_PATTERN
from aegis_core.skills.importer import (
    SkillImportError,
    SkillImportPreview,
    finalize_skill_import,
    preview_skill_text,
)

MAX_MANIFEST_BYTES = 65_536
MAX_PLUGIN_STATE_BYTES = 1_048_576
MAX_SKILL_SOURCE_BYTES = 32_768
MAX_PLUGIN_SKILLS = 16
MAX_CATALOGUE_PLUGINS = 400
MAX_MARKETPLACES = 12
MAX_ARCHIVE_BYTES = 25 * 1_048_576
MAX_EXTRACTED_BYTES = 80 * 1_048_576
MAX_ARCHIVE_FILES = 6_000
# The app's IPC client waits at most 30 s for an answer.
DOWNLOAD_SECONDS = 24.0
CATALOGUE_TTL_SECONDS = 10.0
_PLUGIN_ID = re.compile(r"^[a-z][a-z0-9-]{2,40}$")
_MARKETPLACE_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{1,80}$")
_GITHUB_SPEC = re.compile(
    r"^(?:https://github\.com/)?([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"([A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$"
)
_MANIFESTS = (".codex-plugin/plugin.json", ".claude-plugin/plugin.json", "plugin.json")
_MARKETPLACE_FILES = (".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json",
                      "marketplace.json")

_SKIP_REASONS = {
    "markdown_body_exceeds_resource_budget": "es más larga de lo que Jarvis admite",
    "source_size_or_type_invalid": "el archivo es demasiado grande",
    "executable": "incluye scripts o comandos para ejecutar",
}


class ForeignPluginError(ValueError):
    """A safe, Spanish message for the owner; never file content."""


class ForeignPluginStateError(ForeignPluginError):
    """An unreadable registry is not an empty registry and must be preserved."""

    def __init__(self) -> None:
        super().__init__(
            "No pude leer el registro de complementos. Se conserva sin cambios; "
            "revisa el diagnóstico antes de instalar o quitar complementos.")


@dataclass(frozen=True, slots=True)
class ForeignSkill:
    skill_id: str
    name: str
    description: str
    trigger: str
    preview: SkillImportPreview = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ForeignPlugin:
    plugin_id: str
    name: str
    description: str
    version: str
    publisher: str
    format: str
    source: str
    root: Path = field(compare=False)
    keywords: tuple[str, ...] = ()
    skills: tuple[ForeignSkill, ...] = ()
    left_out: tuple[str, ...] = ()

    @property
    def format_label(self) -> str:
        return "Codex" if self.format == "codex" else "Claude"

    def public(self, *, installed: bool) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id, "name": self.name, "description": self.description,
            "version": self.version, "publisher": self.publisher, "format": self.format,
            "source": self.source, "installed": installed, "installable": bool(self.skills),
            "skills": [{"name": s.name, "description": s.description} for s in self.skills],
            "left_out": list(self.left_out),
        }

    @property
    def content_sha256(self) -> str:
        content = {
            **self.public(installed=False), "root": str(self.root),
            "skill_sources": [(s.skill_id, s.trigger, s.preview.source_sha256)
                              for s in self.skills],
        }
        return hashlib.sha256(json.dumps(
            content, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")).hexdigest()


def _folded(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def _slug(value: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.casefold()).strip("-")


def _clean(value: Any, maximum: int) -> str:
    text = " ".join(str(value).split()) if isinstance(value, str) else ""
    text = "".join(character for character in text if character.isprintable())
    if len(text) <= maximum:
        return text
    cut = text[: maximum - 1].rsplit(" ", 1)[0]
    return f"{cut}…"


def _read(path: Path, maximum: int) -> bytes | None:
    """One regular file, never through a symlink, within ``maximum`` bytes."""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= maximum:
            return None
        data = os.read(descriptor, maximum + 1)
    finally:
        os.close(descriptor)
    return data if 0 < len(data) <= maximum else None


def _read_json(path: Path) -> dict[str, Any] | None:
    data = _read(path, MAX_MANIFEST_BYTES)
    if data is None:
        return None
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _within(root: Path, relative: Any) -> Path | None:
    if not isinstance(relative, str) or not relative.strip() or "\x00" in relative:
        return None
    parts = PurePosixPath(relative.strip())
    if parts.is_absolute() or ".." in parts.parts:
        return None
    candidate = root.joinpath(*[part for part in parts.parts if part != "."])
    try:
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(root.resolve()):
            return None
    except OSError:
        return None
    return candidate


def _paths(value: Any, default: str) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)][:8]
    return [default]


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    """``name`` and ``description`` from any frontmatter; other keys are ignored."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"),
               None)
    if end is None:
        return {}, text
    values: dict[str, str] = {}
    index = 1
    while index < end:
        line = lines[index]
        index += 1
        match = re.fullmatch(r"(name|description):\s*(.*)", line)
        if match is None:
            continue
        key, raw = match.group(1), match.group(2).strip()
        if raw in {"|", "|-", "|+", ">", ">-", ">+"} or not raw:
            block = []
            while index < end and (lines[index].startswith((" ", "\t")) or not lines[index]):
                block.append(lines[index].strip())
                index += 1
            raw = " ".join(part for part in block if part)
        elif raw[:1] in {'"', "'"} and raw[-1:] == raw[:1] and len(raw) >= 2:
            raw = raw[1:-1]
        values[key] = raw
    return values, "\n".join(lines[end + 1:]).strip()


def _first_sentence(body: str) -> str:
    for line in body.splitlines():
        line = line.strip().lstrip("#").strip()
        if len(line) >= 8 and not line.startswith(("```", "---", "<", "!")):
            return line
    return ""


def _skill(text: str, *, fallback_name: str, command: bool) -> ForeignSkill:
    metadata, body = _frontmatter(text)
    stem = _clean(fallback_name.replace("-", " ").replace("_", " "), 60)
    name = _clean(metadata.get("name") or stem, 80)
    if command:
        name = f"/{_slug(fallback_name)}"
    description = _clean(metadata.get("description") or _first_sentence(body), 300)
    if len(description) < 8:
        description = _clean(f"Procedimiento «{name}» importado de un complemento.", 300)
    if len(name) < 3:
        name = _clean(f"Habilidad {stem}", 80)
    if not body:
        raise SkillImportError("markdown_body_required")
    source = (f"---\nname: {json.dumps(name, ensure_ascii=False)}\n"
              f"description: {json.dumps(description, ensure_ascii=False)}\n---\n{body}\n")
    preview = preview_skill_text(source)
    if preview.requires_disabled:
        raise SkillImportError("executable")
    trigger = " ".join(_folded(stem if command else name).split())[:120]
    if len(trigger) < 3:
        trigger = " ".join(_folded(name).split())[:120]
    return ForeignSkill(preview.skill_id, name, description, trigger, preview)


def read_plugin(root: Path, *, source: str, fmt: str | None = None,
                entry: dict[str, Any] | None = None) -> ForeignPlugin | None:
    """A Codex or Claude plugin directory, or None when it is not one."""
    manifest: dict[str, Any] | None = None
    detected = fmt
    for relative in _MANIFESTS:
        manifest = _read_json(root / relative)
        if manifest is not None:
            detected = detected or ("codex" if relative.startswith(".codex") else "claude")
            break
    if manifest is None:
        if entry is None or not any((root / name).is_dir() for name in ("skills", "commands")):
            return None
        manifest = entry
    metadata = {**(entry or {}), **manifest}
    interface = metadata.get("interface") if isinstance(metadata.get("interface"), dict) else {}
    plugin_id = _slug(str(metadata.get("name", "")))[:40].strip("-")
    if not _PLUGIN_ID.fullmatch(plugin_id):
        return None
    name = _clean(interface.get("displayName") or metadata.get("name"), 80) or plugin_id
    description = _clean(interface.get("shortDescription") or metadata.get("description"), 300)
    author = metadata.get("author")
    publisher = _clean(
        author.get("name") if isinstance(author, dict) else author
        or interface.get("developerName") or "", 80)
    raw_keywords = metadata.get("keywords")
    keywords = tuple(_clean(word, 40) for word in (
        raw_keywords if isinstance(raw_keywords, list) else ()) if isinstance(word, str))[:16]
    skills: list[ForeignSkill] = []
    left_out: list[str] = []
    seen: set[str] = set()

    def add(path: Path, *, fallback: str, command: bool) -> None:
        if len(skills) >= MAX_PLUGIN_SKILLS:
            return
        data = _read(path, MAX_SKILL_SOURCE_BYTES)
        label = f"/{_slug(fallback)}" if command else fallback
        if data is None:
            left_out.append(f"«{label}» ({_SKIP_REASONS['source_size_or_type_invalid']})")
            return
        try:
            skill = _skill(data.decode("utf-8"), fallback_name=fallback, command=command)
        except (SkillImportError, UnicodeError) as error:
            reason = _SKIP_REASONS.get(str(error), "su formato no es compatible")
            left_out.append(f"«{label}» ({reason})")
            return
        if skill.skill_id not in seen:
            seen.add(skill.skill_id)
            skills.append(skill)

    for relative in _paths(metadata.get("skills"), "skills"):
        directory = _within(root, relative)
        if directory is None or not directory.is_dir():
            continue
        if (directory / "SKILL.md").is_file():
            add(directory / "SKILL.md", fallback=directory.name, command=False)
        for child in sorted(directory.iterdir())[:64]:
            if child.is_dir() and not child.is_symlink() and (child / "SKILL.md").is_file():
                add(child / "SKILL.md", fallback=child.name, command=False)
    for relative in _paths(metadata.get("commands"), "commands"):
        directory = _within(root, relative)
        if directory is None or not directory.is_dir():
            continue
        for child in sorted(directory.glob("*.md"))[:32]:
            if not child.is_symlink():
                add(child, fallback=child.stem, command=True)
    servers = metadata.get("mcpServers")
    if servers is None and (root / ".mcp.json").is_file():
        servers = _read_json(root / ".mcp.json") or {}
        servers = servers.get("mcpServers", servers)
    if servers:
        count = len(servers) if isinstance(servers, dict) else 1
        left_out.append(f"{count} servidor{'es' if count != 1 else ''} MCP (ejecutan un "
                        "programa o piden su propio inicio de sesión)")
    if metadata.get("apps") or (root / ".app.json").is_file():
        left_out.append("conexiones de apps de ChatGPT")
    if metadata.get("hooks") or (root / "hooks").is_dir():
        left_out.append("hooks (ejecutan comandos)")
    if metadata.get("agents") or (root / "agents").is_dir():
        left_out.append("subagentes")
    version = _clean(metadata.get("version"), 40) or "1.0.0"
    return ForeignPlugin(
        plugin_id=plugin_id, name=name, description=description or name,
        version=version, publisher=publisher, format=detected or "claude", source=source,
        root=root, keywords=keywords, skills=tuple(skills), left_out=tuple(left_out[:12]),
    )


def _newest(directory: Path) -> Path | None:
    try:
        children = [child for child in directory.iterdir()
                    if child.is_dir() and not child.is_symlink()
                    and not child.name.startswith(".")]
    except OSError:
        return None
    return max(children, key=lambda child: child.stat().st_mtime, default=None)


def _read_state(path: Path) -> dict[str, Any] | None:
    """Strict owner-state reader; source manifests deliberately remain tolerant."""
    try:
        if path.parent.resolve() != path.parent:
            raise ValueError("registry location must not contain symlinks")
        return read_private_json(path, max_bytes=MAX_PLUGIN_STATE_BYTES)
    except FileNotFoundError:
        if path.is_symlink():
            raise ForeignPluginStateError() from None
        return None
    except (OSError, ValueError):
        raise ForeignPluginStateError() from None


def _write_json(path: Path, value: dict[str, Any]) -> None:
    write_private_json(path, value, max_bytes=MAX_PLUGIN_STATE_BYTES)


class ForeignPluginCatalogue:
    """Codex and Claude plugins Jarvis can install, and what it already installed."""

    def __init__(self, directory: Path, *, home: Path | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.directory = directory
        self._home = home or Path.home()
        self._clock = clock
        self._lock = threading.RLock()
        self._cache: tuple[float, tuple[ForeignPlugin, ...]] | None = None

    @property
    def marketplaces_directory(self) -> Path:
        return self.directory / "marketplaces"

    def invalidate(self) -> None:
        with self._lock:
            self._cache = None

    def _marketplace(self, root: Path, label: str) -> Iterable[ForeignPlugin]:
        for relative in _MARKETPLACE_FILES:
            listing = _read_json(root / relative)
            if listing is None:
                continue
            title = _clean(listing.get("name"), 60) or label
            entries = listing.get("plugins")
            for entry in entries[:200] if isinstance(entries, list) else ():
                if not isinstance(entry, dict):
                    continue
                source = entry.get("source")
                if isinstance(source, dict):
                    source = source.get("path")
                plugin_root = _within(root, source) if isinstance(source, str) else None
                if plugin_root is not None and plugin_root.is_dir():
                    plugin = read_plugin(plugin_root, source=title, entry=entry,
                                         fmt="codex" if ".agents" in relative else None)
                    if plugin is not None:
                        yield plugin
            return
        plugin = read_plugin(root, source=label)
        if plugin is not None:
            yield plugin

    def _scan(self) -> tuple[ForeignPlugin, ...]:
        found: dict[str, ForeignPlugin] = {}

        def keep(plugins: Iterable[ForeignPlugin]) -> None:
            for plugin in plugins:
                if len(found) < MAX_CATALOGUE_PLUGINS:
                    found.setdefault(plugin.plugin_id, plugin)

        for app, base in (("codex", self._home / ".codex/plugins/cache"),
                          ("claude", self._home / ".claude/plugins/cache")):
            label = "Codex" if app == "codex" else "Claude"
            if not base.is_dir() or base.is_symlink():
                continue
            for market in sorted(base.iterdir()):
                if not market.is_dir() or market.is_symlink() or market.name.startswith("."):
                    continue
                for plugin_directory in sorted(market.iterdir())[:200]:
                    if plugin_directory.is_symlink() or not plugin_directory.is_dir():
                        continue
                    version = _newest(plugin_directory)
                    if version is not None:
                        plugin = read_plugin(version, source=label, fmt=app)
                        keep((plugin,) if plugin is not None else ())
        claude_markets = self._home / ".claude/plugins/marketplaces"
        if claude_markets.is_dir() and not claude_markets.is_symlink():
            for market in sorted(claude_markets.iterdir())[:MAX_MARKETPLACES]:
                if market.is_dir() and not market.is_symlink():
                    keep(self._marketplace(market, f"Claude · {market.name}"))
        own = self.marketplaces_directory
        if own.is_dir():
            for item in self.marketplaces():
                market = own / item["slug"]
                if market.is_dir() and not market.is_symlink() and not market.name.startswith("."):
                    keep(self._marketplace(market, market.name))
        return tuple(sorted(found.values(), key=lambda plugin: _folded(plugin.name)))

    def plugins(self) -> tuple[ForeignPlugin, ...]:
        with self._lock:
            now = self._clock()
            if self._cache is None or now - self._cache[0] > CATALOGUE_TTL_SECONDS:
                self._cache = (now, self._scan())
            return self._cache[1]

    def get(self, plugin_id: str) -> ForeignPlugin | None:
        return next((p for p in self.plugins() if p.plugin_id == plugin_id), None)

    def resolve(self, wanted: str) -> tuple[ForeignPlugin, ...]:
        """By exact id, then by name or id containing the owner's words."""
        exact = self.get(_slug(wanted))
        if exact is not None:
            return (exact,)
        folded = _folded(" ".join(wanted.split()))
        words = [word for word in re.split(r"\W+", folded) if len(word) >= 2]
        return tuple(plugin for plugin in self.plugins()
                     if folded in _folded(plugin.name) or (words and all(
                         word in _folded(f"{plugin.name} {plugin.plugin_id}") for word in words)))

    def search(self, query: str, *, limit: int = 8) -> list[ForeignPlugin]:
        words = [w for w in re.split(r"\W+", _folded(query)) if len(w) >= 3]
        scored = []
        for plugin in self.plugins():
            haystack = _folded(" ".join((plugin.name, plugin.plugin_id, plugin.description,
                                         *plugin.keywords, *(s.name for s in plugin.skills))))
            score = sum(word in haystack for word in words) if words else 1
            if score:
                scored.append((-score, _folded(plugin.name), plugin))
        return [plugin for *_, plugin in sorted(scored, key=lambda item: item[:2])][:limit]

    # Installed plugins and owner-added marketplaces, in Jarvis's own files.

    def installed(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            value = _read_state(self.directory / "installed.json")
            return {} if value is None else self._validate_installed(value)

    @classmethod
    def _validate_installed(cls, value: dict[str, Any]) -> dict[str, dict[str, Any]]:
        plugins = value.get("plugins")
        if not isinstance(plugins, dict) or len(plugins) > MAX_CATALOGUE_PLUGINS:
            raise ForeignPluginStateError()
        claimed_skills: set[str] = set()
        for plugin_id, record in plugins.items():
            if (not isinstance(plugin_id, str) or not _PLUGIN_ID.fullmatch(plugin_id)
                    or not isinstance(record, dict)):
                raise ForeignPluginStateError()
            skills = record.get("skill_ids")
            if (not isinstance(skills, list) or not 1 <= len(skills) <= MAX_PLUGIN_SKILLS
                    or any(not isinstance(item, str) or not re.fullmatch(SKILL_ID_PATTERN, item)
                           for item in skills) or len(set(skills)) != len(skills)
                    or claimed_skills.intersection(skills)):
                raise ForeignPluginStateError()
            claimed_skills.update(skills)
            names = record.get("skill_names")
            if (not isinstance(names, list) or len(names) != len(skills)
                    or any(not isinstance(name, str) or not 1 <= len(name) <= 100
                           for name in names)
                    or not isinstance(record.get("format"), str)
                    or record["format"] not in {"codex", "claude"}
                    or any(not isinstance(record.get(key), str)
                           or not 1 <= len(record[key]) <= limit
                           for key, limit in (("name", 80), ("version", 40), ("source", 200)))):
                raise ForeignPluginStateError()
            cls._validate_timestamp(record.get("installed_at"))
        return dict(plugins)

    @staticmethod
    def _validate_timestamp(value: object) -> None:
        if not isinstance(value, str) or len(value) > 64:
            raise ForeignPluginStateError()
        try:
            datetime.fromisoformat(value)
        except ValueError:
            raise ForeignPluginStateError() from None

    def save_installed(self, plugins: dict[str, dict[str, Any]]) -> None:
        with self._lock:
            self.installed()  # Preserve malformed state even when called directly.
            self._validate_installed({"plugins": plugins})
            _write_json(self.directory / "installed.json", {"plugins": plugins})

    def marketplaces(self) -> list[dict[str, Any]]:
        with self._lock:
            value = _read_state(self.directory / "marketplaces.json")
            return [] if value is None else self._validate_marketplaces(value)

    @classmethod
    def _validate_marketplaces(cls, value: dict[str, Any]) -> list[dict[str, Any]]:
        items = value.get("marketplaces")
        if not isinstance(items, list) or len(items) > MAX_MARKETPLACES:
            raise ForeignPluginStateError()
        slugs: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                raise ForeignPluginStateError()
            slug, repository = item.get("slug"), item.get("repository")
            if (not isinstance(slug, str) or not _MARKETPLACE_SLUG.fullmatch(slug)
                    or slug in slugs or not isinstance(repository, str)
                    or not _GITHUB_SPEC.fullmatch(repository)):
                raise ForeignPluginStateError()
            slugs.add(slug)
            cls._validate_timestamp(item.get("added_at"))
        return list(items)

    def save_marketplaces(self, items: list[dict[str, Any]]) -> None:
        with self._lock:
            self.marketplaces()
            self._validate_marketplaces({"marketplaces": items})
            try:
                _write_json(self.directory / "marketplaces.json", {"marketplaces": items})
            finally:
                self._cache = None  # A directory fsync error can follow committed replacement.


def parse_marketplace_spec(spec: str) -> tuple[str, str]:
    match = _GITHUB_SPEC.fullmatch(spec.strip())
    if match is None:
        raise ForeignPluginError(
            "Indica el repositorio de GitHub como «usuario/repositorio» o su enlace.")
    return match.group(1), match.group(2)


def extract_marketplace(archive: bytes, destination: Path) -> None:
    """A GitHub tarball into ``destination``: regular files only, inside it, bounded."""
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise ForeignPluginError("El repositorio es demasiado grande.")
    destination.mkdir(parents=True, mode=0o700)
    written = files = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
            for member in bundle:
                parts = PurePosixPath(member.name).parts[1:]  # GitHub's "<repo>-<sha>/" prefix
                if not parts or member.isdir():
                    continue
                if not member.isreg():
                    continue  # symlinks, devices and links never leave the archive
                if any(part in {"", ".", ".."} for part in parts) or member.name.startswith("/"):
                    raise ForeignPluginError("El repositorio tiene rutas no válidas.")
                files += 1
                written += member.size
                if files > MAX_ARCHIVE_FILES or written > MAX_EXTRACTED_BYTES:
                    raise ForeignPluginError("El repositorio es demasiado grande.")
                target = destination.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                source = bundle.extractfile(member)
                if source is None:
                    continue
                with source, open(target, "xb") as handle:
                    shutil.copyfileobj(source, handle, length=65_536)
                os.chmod(target, 0o600)
    except (tarfile.TarError, OSError, EOFError) as error:
        shutil.rmtree(destination, ignore_errors=True)
        raise ForeignPluginError("No pude leer el repositorio descargado.") from error
    except ForeignPluginError:
        shutil.rmtree(destination, ignore_errors=True)
        raise


Downloader = Callable[[str], Any]  # async (url) -> bytes


async def _download(url: str) -> bytes:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                if response.status_code == 404:
                    raise ForeignPluginError(
                        "No encontré ese repositorio en GitHub (¿es público?).")
                if response.status_code != 200:
                    raise ForeignPluginError("GitHub no respondió; vuelve a intentarlo.")
                chunks, total = [], 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_ARCHIVE_BYTES:
                        raise ForeignPluginError("El repositorio es demasiado grande.")
                    chunks.append(chunk)
    except httpx.HTTPError as error:
        raise ForeignPluginError(
            "No pude descargar el repositorio. Revisa la conexión.") from error
    return b"".join(chunks)


class ForeignPluginManager:
    """Installs, updates and removes Codex and Claude plugins as learned skills."""

    def __init__(self, catalogue: ForeignPluginCatalogue, registry: Any, *,
                 on_change: Callable[[], None] = lambda: None,
                 download: Downloader = _download,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.catalogue = catalogue
        self._registry = registry
        self._on_change = on_change
        self._download = download
        self._clock = clock
        self._lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        installed = self.catalogue.installed()
        plugins = self.catalogue.plugins()
        known = {plugin.plugin_id for plugin in plugins}
        return {
            "plugins": [plugin.public(installed=plugin.plugin_id in installed)
                        for plugin in plugins],
            "installed": [{"plugin_id": key, **record} for key, record in sorted(
                installed.items()) if key not in known],
            "marketplaces": self.catalogue.marketplaces(),
        }

    def install(self, plugin_id: str, *, expected_sha256: str | None = None) -> dict[str, Any]:
        with self._lock:
            if expected_sha256 is not None:
                self.catalogue.invalidate()
            plugin = self.catalogue.get(plugin_id)
            if plugin is None:
                raise ForeignPluginError("Ese complemento ya no está disponible.")
            if expected_sha256 is not None and plugin.content_sha256 != expected_sha256:
                raise ForeignPluginError(
                    "El complemento cambió desde que lo revisaste. Vuelve a pedir su "
                    "instalación para revisar los cambios y aprobarlos.")
            if not plugin.skills:
                raise ForeignPluginError(
                    f"«{plugin.name}» no trae habilidades que Jarvis pueda usar.")
            records = self.catalogue.installed()
            if plugin.plugin_id not in records and len(records) >= MAX_CATALOGUE_PLUGINS:
                raise ForeignPluginError(
                    "Ya tienes el máximo de complementos; quita uno antes de instalar otro.")
            owned = set(records.get(plugin.plugin_id, {}).get("skill_ids", []))
            taken = {skill.skill_id for skill in self._registry.all()} - owned
            for other, record in records.items():
                if other != plugin.plugin_id:
                    taken |= set(record.get("skill_ids", []))
            installed: list[str] = []
            names: list[str] = []
            for skill in plugin.skills:
                skill_id = skill.skill_id
                if skill_id in taken:
                    skill_id = f"{plugin.plugin_id}-{skill.skill_id}"[:64].rstrip("-")
                if skill_id in taken or skill_id in installed:
                    continue
                draft = finalize_skill_import(skill.preview, {
                    "skill_id": skill_id, "enabled": True, "role": AgentRole.PLANNER.value,
                    "trigger_phrases": (skill.trigger,),
                })
                self._registry.learn(draft)
                record = next(item for item in self._registry.catalogue()
                              if item["skill_id"] == skill_id)
                # The owner approved this plugin: enable its skills and let NVIDIA read
                # them, as Jarvis answers with NVIDIA.
                self._registry.set_enabled(skill_id, record["content_sha256"], True)
                self._registry.set_remote_consent(skill_id, record["content_sha256"], True)
                installed.append(skill_id)
                names.append(skill.name)
            for stale in owned - set(installed):
                try:
                    self._registry.forget(stale)
                except ValueError:
                    pass
            if not installed:
                raise ForeignPluginError(
                    f"No pude instalar las habilidades de «{plugin.name}».")
            records[plugin.plugin_id] = {
                "name": plugin.name, "version": plugin.version, "format": plugin.format,
                "source": plugin.source, "skill_ids": installed, "skill_names": names,
                "installed_at": self._clock().isoformat(),
            }
            self.catalogue.save_installed(records)
        self._on_change()
        return {"plugin_id": plugin.plugin_id, "name": plugin.name, "skills": names,
                "left_out": list(plugin.left_out)}

    def remove(self, plugin_id: str) -> dict[str, Any]:
        with self._lock:
            records = self.catalogue.installed()
            record = records.pop(plugin_id, None)
            if record is None:
                raise ForeignPluginError("Ese complemento no está instalado.")
            for skill_id in record.get("skill_ids", []):
                try:
                    self._registry.forget(skill_id)
                except ValueError:
                    pass
            self.catalogue.save_installed(records)
        self._on_change()
        return {"plugin_id": plugin_id, "removed": True}

    async def add_marketplace(self, spec: str) -> dict[str, Any]:
        owner, repository = parse_marketplace_spec(spec)
        slug = _slug(f"{owner}-{repository}")[:80] or "marketplace"
        if not _MARKETPLACE_SLUG.fullmatch(slug):
            raise ForeignPluginError("Ese nombre de repositorio no es válido.")
        items = [item for item in self.catalogue.marketplaces() if item.get("slug") != slug]
        if len(items) >= MAX_MARKETPLACES:
            raise ForeignPluginError("Ya tienes el máximo de marketplaces; quita uno primero.")
        try:
            async with asyncio.timeout(DOWNLOAD_SECONDS):
                archive = await self._download(
                    f"https://codeload.github.com/{owner}/{repository}/tar.gz/HEAD")
        except TimeoutError:
            raise ForeignPluginError(
                "GitHub tardó demasiado en enviar el repositorio; vuelve a intentarlo.") from None
        with self._lock:
            # A download may overlap another mutation; commit against current state,
            # never the catalogue snapshot from before the network wait.
            previous = self.catalogue.marketplaces()
            items = [item for item in previous if item.get("slug") != slug]
            if len(items) >= MAX_MARKETPLACES:
                raise ForeignPluginError(
                    "Ya tienes el máximo de marketplaces; quita uno primero.")
            base = self.catalogue.marketplaces_directory
            if base.resolve() != base or (base / slug).is_symlink():
                raise ForeignPluginStateError()
            base.mkdir(parents=True, exist_ok=True, mode=0o700)
            staging = base / f".{slug}.{uuid4().hex}"
            extract_marketplace(archive, staging)
            if not any(_read_json(staging / name) for name in (*_MARKETPLACE_FILES, *_MANIFESTS)):
                shutil.rmtree(staging, ignore_errors=True)
                raise ForeignPluginError(
                    "Ese repositorio no es un marketplace ni un complemento de Codex o Claude.")
            target = base / slug
            retired = base / f".{slug}.old.{uuid4().hex}"
            items.append({"slug": slug, "repository": f"{owner}/{repository}",
                          "added_at": self._clock().isoformat()})
            if target.exists():
                os.replace(target, retired)
            try:
                os.replace(staging, target)
                self.catalogue.save_marketplaces(items)
            except (OSError, ValueError):
                # write_private_json can fail after replacement (directory fsync).
                # Keep the tree matching the committed state; an unreadable state
                # preserves both trees for recovery instead of guessing success.
                try:
                    current = self.catalogue.marketplaces()
                except ForeignPluginStateError:
                    current = None
                if current == previous:
                    shutil.rmtree(target, ignore_errors=True)
                    if retired.exists():
                        os.replace(retired, target)
                elif current == items:
                    shutil.rmtree(retired, ignore_errors=True)
                self.catalogue.invalidate()
                raise
            else:
                shutil.rmtree(retired, ignore_errors=True)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
            root = target.resolve()
            count = sum(1 for plugin in self.catalogue.plugins()
                        if plugin.root.resolve().is_relative_to(root))
        self._on_change()
        return {"slug": slug, "repository": f"{owner}/{repository}", "plugins": count}

    def remove_marketplace(self, slug: str) -> dict[str, Any]:
        if not _MARKETPLACE_SLUG.fullmatch(slug):
            raise ForeignPluginError("Ese marketplace no existe.")
        with self._lock:
            items = self.catalogue.marketplaces()
            remaining = [item for item in items if item.get("slug") != slug]
            if len(remaining) == len(items):
                raise ForeignPluginError("Ese marketplace no existe.")
            # Publish the removal first; a failed save must not delete the tree.
            self.catalogue.save_marketplaces(remaining)
            try:
                shutil.rmtree(self.catalogue.marketplaces_directory / slug)
            except FileNotFoundError:
                pass
        self._on_change()
        return {"slug": slug, "removed": True}
