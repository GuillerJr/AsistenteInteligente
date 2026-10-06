"""Private, bounded originals produced by a verified generation tool.

Only opaque tokens cross the conversation boundary; paths never come from an LLM.
Original bytes (including NVIDIA's watermark) are preserved for both transports.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import stat
import threading
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aegis_core.async_tasks import run_blocking_owned
from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult
from aegis_core.settings_store import read_private_json, write_private_json

MAX_MEDIA_BYTES = 48_000_000
_TOKEN = re.compile(r"^[0-9a-f]{32}$")
_LINK = re.compile(r"\[[^\]\n]{1,120}\]\(jarvis-artifact://([0-9a-f]{32})\)")


class GeneratedMediaError(ValueError):
    pass


class GeneratedMedia(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    token: str = Field(pattern=r"^[0-9a-f]{32}$")
    file_name: str = Field(pattern=r"^jarvis-cosmos-[0-9a-f]{32}\.(jpg|mp4)$")
    media_type: str = Field(pattern=r"^(image/jpeg|video/mp4)$")
    byte_count: int = Field(ge=12, le=MAX_MEDIA_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_id: str = Field(default="nvidia/cosmos3-nano", pattern=r"^nvidia/cosmos3-nano$")

    @model_validator(mode="after")
    def consistent_file(self) -> GeneratedMedia:
        suffix = "jpg" if self.media_type == "image/jpeg" else "mp4"
        if self.file_name != f"jarvis-cosmos-{self.token}.{suffix}":
            raise ValueError("inconsistent generated media")
        return self

    @property
    def link(self) -> str:
        label = "Imagen generada" if self.media_type == "image/jpeg" else "Video generado"
        return f"[{label}](jarvis-artifact://{self.token})"


def media_tokens(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_LINK.findall(text)))[:4]


def without_media_links(text: str) -> str:
    return _LINK.sub("Archivo generado adjunto.", text)


def validate_media_bytes(data: bytes, media_type: str) -> None:
    if not isinstance(data, bytes) or not 12 <= len(data) <= MAX_MEDIA_BYTES:
        raise GeneratedMediaError("generated_media_invalid")
    if media_type == "image/jpeg":
        if len(data) > 8_000_000 or not data.startswith(b"\xff\xd8\xff"):
            raise GeneratedMediaError("generated_media_invalid")
        # Decode before publishing; reject corrupt files and decompression bombs.
        import io

        from PIL import Image

        with Image.open(io.BytesIO(data)) as picture:
            if picture.format != "JPEG" or picture.width * picture.height > 4_000_000:
                raise GeneratedMediaError("generated_media_invalid")
            picture.verify()
    elif media_type == "video/mp4":
        # Require a complete bounded ISO-BMFF container, not a fake ftyp prefix.
        offset, boxes = 0, set()
        while offset < len(data):
            if offset + 8 > len(data):
                raise GeneratedMediaError("generated_media_invalid")
            size, kind = (
                int.from_bytes(data[offset : offset + 4], "big"),
                data[offset + 4 : offset + 8],
            )
            header = 8
            if size == 1:
                if offset + 16 > len(data):
                    raise GeneratedMediaError("generated_media_invalid")
                size, header = int.from_bytes(data[offset + 8 : offset + 16], "big"), 16
            elif size == 0:
                size = len(data) - offset
            if size < header or offset + size > len(data):
                raise GeneratedMediaError("generated_media_invalid")
            boxes.add(kind)
            offset += size
        if not {b"ftyp", b"moov", b"mdat"}.issubset(boxes):
            raise GeneratedMediaError("generated_media_invalid")
    else:
        raise GeneratedMediaError("generated_media_invalid")


class GeneratedMediaStore:
    def __init__(self, root: Path, *, max_files: int = 64, max_bytes: int = 512_000_000):
        self.root = root
        self.max_files, self.max_bytes = max_files, max_bytes
        self._lock = threading.RLock()

    def _directory(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise GeneratedMediaError("generated_media_store_invalid")

    def put(self, data: bytes, media_type: str) -> GeneratedMedia:
        validate_media_bytes(data, media_type)
        with self._lock:
            self._directory()
            files = tuple(self.root.glob("jarvis-cosmos-*"))
            if (
                len(files) >= self.max_files
                or sum(p.lstat().st_size for p in files) + len(data) > self.max_bytes
            ):
                raise GeneratedMediaError("generated_media_capacity")
            token = secrets.token_hex(16)
            suffix = "jpg" if media_type == "image/jpeg" else "mp4"
            descriptor = GeneratedMedia(
                token=token,
                file_name=f"jarvis-cosmos-{token}.{suffix}",
                media_type=media_type,
                byte_count=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
            )
            path = self.root / descriptor.file_name
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                with os.fdopen(fd, "wb") as file:
                    file.write(data)
                    file.flush()
                    os.fsync(file.fileno())
                write_private_json(self.root / f"{token}.json", descriptor.model_dump())
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            return descriptor

    def read(self, token: str) -> tuple[GeneratedMedia, bytes]:
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise GeneratedMediaError("generated_media_unavailable")
        with self._lock:
            self._directory()
            descriptor = GeneratedMedia.model_validate(
                read_private_json(self.root / f"{token}.json")
            )
            if descriptor.token != token:
                raise GeneratedMediaError("generated_media_invalid")
            fd = os.open(
                self.root / descriptor.file_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            )
            try:
                info = os.fstat(fd)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_mode & 0o077
                    or info.st_nlink != 1
                    or info.st_size != descriptor.byte_count
                ):
                    raise GeneratedMediaError("generated_media_invalid")
                with os.fdopen(fd, "rb", closefd=False) as file:
                    data = file.read(descriptor.byte_count + 1)
            finally:
                os.close(fd)
            if (
                len(data) != descriptor.byte_count
                or hashlib.sha256(data).hexdigest() != descriptor.sha256
            ):
                raise GeneratedMediaError("generated_media_invalid")
            return descriptor, data

    def handlers(self) -> dict:
        return {"artifacts.get": self._get}

    async def _get(self, request: IpcRequest) -> IpcHandlerResult:
        try:
            if set(request.payload) != {"token"}:
                raise ValueError("invalid artifact request")
            descriptor, _ = await run_blocking_owned(self.read, request.payload["token"])
            return IpcHandlerResult(
                ok=True,
                payload={**descriptor.model_dump(), "path": str(self.root / descriptor.file_name)},
            )
        except (OSError, ValueError, ValidationError):
            return IpcHandlerResult(ok=False, error_code="generated_media_unavailable")
