"""Projects (Claude's and Codex's): chats grouped around instructions and files.

A project has a name, an icon and a color, the owner's instructions and up to eight
text files of reference knowledge. Every chat started in a project (or moved into
one) carries the project's id in its encrypted conversation record; when Jarvis
answers in that chat, admission attaches the project's instructions and files as
context, so the owner does not repeat them. Files are plain text the app extracted
(PDFs included), stored owner-only next to the project list; anything that looks like
a credential is refused, as with chat attachments.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from aegis_core.memory.errors import MemoryNotFoundError
from aegis_core.secrets import contains_likely_secret_material

MAX_PROJECTS = 50
MAX_FILES = 8
MAX_FILE_BYTES = 65_536
MAX_INSTRUCTIONS = 4_000
# The project's share of each prompt: instructions plus as much of the files as fits.
MAX_CONTEXT_BYTES = 16_384

Color = Literal["clay", "sage", "sky", "plum", "sand", "slate"]
Icon = Literal["folder", "briefcase", "book", "chart", "code", "heart", "house",
               "graduation", "plane", "star"]


class ProjectError(ValueError):
    """A safe, Spanish message for the owner."""


class ProjectFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: UUID
    name: str = Field(min_length=1, max_length=120)
    size: int = Field(ge=1, le=MAX_FILE_BYTES)
    added_at: datetime


class Project(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    name: str = Field(min_length=1, max_length=60)
    color: Color = "clay"
    icon: Icon = "folder"
    instructions: str = Field(default="", max_length=MAX_INSTRUCTIONS)
    files: list[ProjectFile] = Field(default_factory=list, max_length=MAX_FILES)
    created_at: datetime
    updated_at: datetime

    def public(self, chats: int = 0) -> dict[str, Any]:
        return {**self.model_dump(mode="json"), "chats": chats}


def _private_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class ProjectStore:
    """Owner-only JSON list plus one directory of text files per project."""

    def __init__(self, path: Path, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self._path = path
        self._files = path.parent / "projects"
        self._clock = clock
        self._lock = threading.RLock()

    def projects(self) -> tuple[Project, ...]:
        with self._lock:
            return tuple(sorted(self._read().values(), key=lambda p: p.name.casefold()))

    def get(self, project_id: UUID) -> Project | None:
        with self._lock:
            return self._read().get(project_id)

    def save(self, *, project_id: UUID | None, name: str, color: str, icon: str,
             instructions: str) -> Project:
        name = " ".join(name.split())
        if not name:
            raise ProjectError("El proyecto necesita un nombre.")
        if contains_likely_secret_material(instructions):
            raise ProjectError("Las instrucciones parecen contener una credencial; quítala.")
        with self._lock:
            projects = self._read()
            now = self._clock()
            if project_id is None:
                if len(projects) >= MAX_PROJECTS:
                    raise ProjectError("Ya tienes el máximo de proyectos.")
                project = Project(project_id=uuid4(), name=name, color=color, icon=icon,
                                  instructions=instructions.strip(), created_at=now,
                                  updated_at=now)
            else:
                current = projects.get(project_id)
                if current is None:
                    raise ProjectError("Ese proyecto ya no existe.")
                project = current.model_copy(update={
                    "name": name, "color": color, "icon": icon,
                    "instructions": instructions.strip(), "updated_at": now})
                Project.model_validate(project.model_dump())
            projects[project.project_id] = project
            self._write(projects)
            return project

    def delete(self, project_id: UUID) -> bool:
        with self._lock:
            projects = self._read()
            if projects.pop(project_id, None) is None:
                return False
            self._write(projects)
            shutil.rmtree(self._files / str(project_id), ignore_errors=True)
            return True

    def add_file(self, project_id: UUID, name: str, text: str) -> Project:
        data = text.encode("utf-8")
        if not data.strip():
            raise ProjectError("El archivo no tiene texto que Jarvis pueda leer.")
        if len(data) > MAX_FILE_BYTES:
            raise ProjectError("El archivo es demasiado largo (máximo 64 KB de texto).")
        if contains_likely_secret_material(text):
            raise ProjectError("El archivo parece contener una credencial; no lo agregué.")
        with self._lock:
            projects = self._read()
            project = projects.get(project_id)
            if project is None:
                raise ProjectError("Ese proyecto ya no existe.")
            if len(project.files) >= MAX_FILES:
                raise ProjectError("El proyecto ya tiene el máximo de archivos.")
            entry = ProjectFile(file_id=uuid4(), name=" ".join(name.split())[:120] or "archivo",
                                size=len(data), added_at=self._clock())
            _private_write(self._files / str(project_id) / f"{entry.file_id}.txt", data)
            project.files.append(entry)
            project.updated_at = self._clock()
            self._write(projects)
            return project

    def remove_file(self, project_id: UUID, file_id: UUID) -> Project:
        with self._lock:
            projects = self._read()
            project = projects.get(project_id)
            if project is None:
                raise ProjectError("Ese proyecto ya no existe.")
            project.files = [entry for entry in project.files if entry.file_id != file_id]
            (self._files / str(project_id) / f"{file_id}.txt").unlink(missing_ok=True)
            project.updated_at = self._clock()
            self._write(projects)
            return project

    def context(self, project_id: UUID) -> dict[str, Any] | None:
        """What a chat of the project tells Jarvis, within MAX_CONTEXT_BYTES."""
        project = self.get(project_id)
        if project is None:
            return None
        budget = MAX_CONTEXT_BYTES - len(project.instructions.encode("utf-8"))
        knowledge = []
        for entry in project.files:
            path = self._files / str(project_id) / f"{entry.file_id}.txt"
            if budget <= 200 or path.is_symlink():
                break
            try:
                text = path.read_bytes()[:budget].decode("utf-8", errors="ignore")
            except OSError:
                continue
            budget -= len(text.encode("utf-8"))
            knowledge.append({"name": entry.name, "text": text})
        return {"name": project.name, "instructions": project.instructions,
                "knowledge": knowledge}

    def _read(self) -> dict[UUID, Project]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            items = [Project.model_validate(item) for item in raw.get("projects", [])]
        except (OSError, ValueError, ValidationError, AttributeError):
            return {}
        return {item.project_id: item for item in items}

    def _write(self, projects: dict[UUID, Project]) -> None:
        payload = {"projects": [p.model_dump(mode="json") for p in projects.values()]}
        _private_write(self._path, json.dumps(payload, ensure_ascii=False).encode("utf-8"))


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Save(_Payload):
    project_id: UUID | None = None
    name: str = Field(min_length=1, max_length=60)
    color: Color = "clay"
    icon: Icon = "folder"
    instructions: str = Field(default="", max_length=MAX_INSTRUCTIONS)


class _ProjectId(_Payload):
    project_id: UUID


class _ConversationPage(_ProjectId):
    cursor: str | None = Field(default=None, min_length=1, max_length=1_024, strict=True)
    limit: int = Field(default=30, ge=1, le=50, strict=True)


class _ConversationCursor(_ProjectId):
    version: Literal["1"] = "1"
    updated_at: AwareDatetime
    conversation_id: UUID


class _AddFile(_ProjectId):
    name: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=MAX_FILE_BYTES)


class _RemoveFile(_ProjectId):
    file_id: UUID


class _Assign(_Payload):
    conversation_id: UUID
    project_id: UUID | None = None


class ProjectIpcService:
    METHODS = frozenset({
        "projects.list", "projects.save", "projects.delete", "projects.file.add",
        "projects.file.remove", "projects.assign", "projects.conversations",
    })

    def __init__(self, store: ProjectStore, conversations: Any) -> None:
        self._store = store
        self._conversations = conversations
        # Cursors are local to this owner/service lifetime and contain no chat text.
        self._cursor_key = os.urandom(32)

    def _cursor_signature(self, encoded: str) -> str:
        message = (self._conversations.namespace + "\0" + encoded).encode("utf-8")
        return hmac.new(self._cursor_key, message, hashlib.sha256).hexdigest()

    def _encode_cursor(self, project_id: UUID, record: Any) -> str:
        cursor = _ConversationCursor(project_id=project_id, updated_at=record.updated_at,
                                     conversation_id=record.conversation_id)
        encoded = base64.urlsafe_b64encode(cursor.model_dump_json().encode()).decode().rstrip("=")
        return encoded + "." + self._cursor_signature(encoded)

    def _decode_cursor(self, token: str, project_id: UUID) -> tuple[datetime, UUID]:
        if re.fullmatch(r"[A-Za-z0-9_-]{1,900}\.[0-9a-f]{64}", token) is None:
            raise ValueError("invalid project cursor")
        encoded, signature = token.split(".")
        if not hmac.compare_digest(signature, self._cursor_signature(encoded)):
            raise ValueError("invalid project cursor")
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        cursor = _ConversationCursor.model_validate_json(raw)
        if cursor.project_id != project_id:
            raise ValueError("invalid project cursor")
        return cursor.updated_at, cursor.conversation_id

    def handlers(self) -> dict[str, Any]:
        return {method: self.handle for method in self.METHODS}

    async def _chats_by_project(self) -> dict[UUID, list[Any]]:
        from aegis_core.memory.conversations import project_of

        grouped: dict[UUID, list[Any]] = {}
        for record in await self._conversations.all_records():
            project = project_of(record)
            if project is not None:
                grouped.setdefault(project, []).append(record)
        return grouped

    async def _listing(self) -> dict[str, Any]:
        grouped = await self._chats_by_project()
        projects = await asyncio.to_thread(self._store.projects)
        return {"projects": [p.public(len(grouped.get(p.project_id, []))) for p in projects]}

    async def handle(self, request: Any) -> Any:
        from aegis_core.ipc.server import IpcHandlerResult
        from aegis_core.memory.conversations import public_record

        if request.method not in self.METHODS:
            return IpcHandlerResult(ok=False, error_code="method_not_found")
        raw = request.payload or {}
        try:
            if request.method == "projects.list":
                _Payload.model_validate(raw)
            elif request.method == "projects.save":
                change = _Save.model_validate(raw)
                project = await asyncio.to_thread(
                    self._store.save, project_id=change.project_id, name=change.name,
                    color=change.color, icon=change.icon, instructions=change.instructions)
                return IpcHandlerResult(ok=True, payload={
                    **(await self._listing()), "project": project.public()})
            elif request.method == "projects.delete":
                target = _ProjectId.model_validate(raw).project_id
                # The chats stay, outside any project.
                for record in (await self._chats_by_project()).get(target, []):
                    await self._conversations.set_project(record.conversation_id, None)
                await asyncio.to_thread(self._store.delete, target)
            elif request.method == "projects.file.add":
                added = _AddFile.model_validate(raw)
                await asyncio.to_thread(self._store.add_file, added.project_id, added.name,
                                        added.text)
            elif request.method == "projects.file.remove":
                removed = _RemoveFile.model_validate(raw)
                await asyncio.to_thread(self._store.remove_file, removed.project_id,
                                        removed.file_id)
            elif request.method == "projects.assign":
                change = _Assign.model_validate(raw)
                if change.project_id is not None and await asyncio.to_thread(
                        self._store.get, change.project_id) is None:
                    raise ProjectError("Ese proyecto ya no existe.")
                await self._conversations.set_project(change.conversation_id, change.project_id)
            else:
                page = _ConversationPage.model_validate(raw)
                boundary = (
                    self._decode_cursor(page.cursor, page.project_id) if page.cursor else None
                )
                if await asyncio.to_thread(self._store.get, page.project_id) is None:
                    return IpcHandlerResult(ok=False, error_code="project_not_found")
                chats, has_more = await self._conversations.project_records(
                    page.project_id, before=boundary, limit=page.limit,
                )
                return IpcHandlerResult(ok=True, payload={
                    "project_id": str(page.project_id),
                    "conversations": [public_record(record) for record in chats],
                    "next_cursor": self._encode_cursor(page.project_id, chats[-1])
                    if has_more else None,
                })
            return IpcHandlerResult(ok=True, payload=await self._listing())
        except ProjectError as error:
            return IpcHandlerResult(ok=True, payload={**(await self._listing()),
                                                      "error": str(error)})
        except (ValidationError, ValueError, OSError, MemoryNotFoundError):
            return IpcHandlerResult(ok=False, error_code="projects_invalid_request")
