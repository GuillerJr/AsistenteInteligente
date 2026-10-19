"""Bounded, disposable media decoding; no local AI models or durable raw files."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import sys
import tempfile
from pathlib import Path

from aegis_core.contracts import DocumentInput, ImageInput

MAX_DOWNLOAD = 10_000_000
TEXT_EXTENSIONS = frozenset(
    {
        "txt",
        "md",
        "csv",
        "tsv",
        "json",
        "yaml",
        "yml",
        "xml",
        "html",
        "log",
        "py",
        "swift",
        "js",
        "ts",
        "css",
        "sql",
        "sh",
        "c",
        "h",
        "cpp",
        "java",
        "go",
        "rs",
        "toml",
        "ini",
        "conf",
        "pdf",
    }
)


class MediaError(ValueError):
    pass


def decode_document(data: bytes, name: str) -> dict:
    extension = name.rsplit(".", 1)[-1].lower()
    if extension not in TEXT_EXTENSIONS:
        raise MediaError("unsupported")
    if extension == "pdf":
        from pypdf import PdfReader, filters

        filters.ZLIB_MAX_OUTPUT_LENGTH = 2_000_000

        if not data.startswith(b"%PDF-"):
            raise MediaError("invalid")
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted or len(reader.pages) > 100:
            raise MediaError("invalid")
        pages = []
        size = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            size += len(text.encode("utf-8")) + 1
            if size > 32768:
                raise MediaError("too_large")
            pages.append(text)
        text = "\n".join(pages)
    else:
        if len(data) > 131072:
            raise MediaError("too_large")
        text = data.decode("utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
    if not text.strip() or "\x00" in text:
        raise MediaError("no_text")
    return DocumentInput(name=name, content=text).model_dump()


def decode_image(data: bytes) -> dict:
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = 16_000_000
    with Image.open(io.BytesIO(data)) as source:
        if source.format not in {"JPEG", "PNG", "WEBP"} or source.n_frames != 1:
            raise MediaError("invalid")
        if source.width * source.height > Image.MAX_IMAGE_PIXELS:
            raise MediaError("too_large")
        source.load()
        picture = ImageOps.exif_transpose(source).convert("RGB")
        # Strip metadata and fit the existing vision IPC contract, never expand it.
        for edge in (1024, 768, 512, 384):
            image = picture.copy()
            image.thumbnail((edge, edge))
            for quality in (80, 60, 40):
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=quality, optimize=True)
                if len(output.getvalue()) <= 32768:
                    return ImageInput(
                        media_type="image/jpeg",
                        data_base64=base64.b64encode(output.getvalue()).decode(),
                    ).model_dump()
    raise MediaError("too_large")


def worker() -> int:
    # PDF/image parsing happens in a killable process with no tokens and bounded memory.
    import resource

    # Under RLIMIT_FSIZE, bytecode for large modules (PIL.Image) is written truncated and
    # then breaks every later interpreter sharing the environment. Never write any.
    sys.dont_write_bytecode = True
    resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
    resource.setrlimit(resource.RLIMIT_FSIZE, (131072, 131072))
    try:
        value = json.loads(sys.stdin.buffer.read(14_000_000))
        data = base64.b64decode(value["data"], validate=True)
        if not 0 < len(data) <= MAX_DOWNLOAD:
            raise MediaError("too_large")
        result = (
            decode_image(data) if value["kind"] == "image" else decode_document(data, value["name"])
        )
        print(json.dumps({"result": result}, ensure_ascii=True))
        return 0
    except Exception:
        print('{"error":"media_invalid"}')
        return 2


async def decode(data: bytes, *, kind: str, name: str) -> dict:
    if kind not in {"image", "document"} or not 0 < len(data) <= MAX_DOWNLOAD:
        raise MediaError("invalid")
    command = (
        [sys.executable, "--telegram-media"]
        if getattr(sys, "frozen", False)
        else [sys.executable, "-m", "aegis_core.telegram.media"]
    )
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={
            **{
                key: val
                for key, val in os.environ.items()
                if key in {"PATH", "LANG", "SYSTEMROOT", "PYTHONPATH"}
            },
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )

    async def guard_memory() -> None:
        import psutil

        while process.returncode is None:
            try:
                if psutil.Process(process.pid).memory_info().rss > 384_000_000:
                    process.kill()
                    return
            except psutil.NoSuchProcess:
                return
            await asyncio.sleep(0.1)

    guard = asyncio.create_task(guard_memory())
    try:
        async with asyncio.timeout(12):
            output, _ = await process.communicate(
                json.dumps(
                    {
                        "data": base64.b64encode(data).decode(),
                        "kind": kind,
                        "name": name,
                    }
                ).encode()
            )
        if process.returncode or len(output) > 100000:
            raise MediaError("invalid")
        value = json.loads(output)
        return value["result"]
    except (ValueError, KeyError, TimeoutError) as error:
        raise MediaError("invalid") from error
    finally:
        guard.cancel()
        await asyncio.gather(guard, return_exceptions=True)
        if process.returncode is None:
            process.kill()
            await process.wait()


async def voice_file(wav: bytes) -> bytes:
    # Apple's encoder converts the existing NVIDIA voice to Telegram's M4A format.
    with tempfile.TemporaryDirectory(prefix="jarvis-telegram-voice-") as root:
        source, target = Path(root) / "speech.wav", Path(root) / "speech.m4a"
        source.write_bytes(wav)
        source.chmod(0o600)
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/afconvert",
            "-f",
            "m4af",
            "-d",
            "aac",
            "-b",
            "64000",
            str(source),
            str(target),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=15)
            if process.returncode or not target.is_file() or target.stat().st_size > 8388608:
                raise MediaError("voice_encoding_failed")
            return target.read_bytes()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()


if __name__ == "__main__":
    raise SystemExit(worker())
