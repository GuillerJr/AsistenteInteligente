from __future__ import annotations

import asyncio
import json
import math
import os
import stat
import subprocess
from collections.abc import Sequence
from pathlib import Path

from aegis_core.providers.base import (
    EmbeddingBatch,
    EmbeddingInputType,
    EmbeddingProviderError,
)

MAX_LOCAL_EMBEDDING_BATCH = 16
MAX_LOCAL_EMBEDDING_TEXT_BYTES = 16_384
MAX_LOCAL_EMBEDDING_REQUEST_BYTES = 131_072
MAX_LOCAL_EMBEDDING_RESPONSE_BYTES = 1_048_576


class AppleLocalEmbeddingClient:
    model_id = "apple/natural-language-sentence-es-v1"

    def __init__(self, executable_path: Path, *, timeout_seconds: float = 5.0) -> None:
        if not 0.5 <= timeout_seconds <= 20:
            raise ValueError("local embedding timeout is out of range")
        self._path = executable_path
        self._timeout_seconds = timeout_seconds

    def is_available(self) -> bool:
        if not self._is_private_executable():
            return False
        try:
            completed = subprocess.run(
                (str(self._path), "--status"),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
                env={"LC_ALL": "C", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
            )
            payload = json.loads(completed.stdout)
        except (OSError, ValueError):
            return False
        return (
            completed.returncode == 0
            and isinstance(payload, dict)
            and set(payload) == {"available", "dimensions", "model_id"}
            and payload["available"] is True
            and payload["model_id"] == self.model_id
            and type(payload["dimensions"]) is int
            and 1 <= payload["dimensions"] <= 8_192
        )

    async def embed(
        self,
        texts: Sequence[str],
        *,
        input_type: EmbeddingInputType,
    ) -> EmbeddingBatch:
        normalized = tuple(texts)
        if not 1 <= len(normalized) <= MAX_LOCAL_EMBEDDING_BATCH:
            raise ValueError("local embedding batch size is out of range")
        if any(
            not isinstance(text, str)
            or not text.strip()
            or len(text.encode("utf-8")) > MAX_LOCAL_EMBEDDING_TEXT_BYTES
            or "\0" in text
            for text in normalized
        ):
            raise ValueError("local embedding input is invalid")
        if not self._is_private_executable():
            raise EmbeddingProviderError("local embedding helper is unavailable")
        encoded = json.dumps(
            {"texts": normalized, "input_type": input_type.value},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > MAX_LOCAL_EMBEDDING_REQUEST_BYTES:
            raise ValueError("local embedding request is too large")
        process: asyncio.subprocess.Process | None = None
        output = b""
        try:
            process = await asyncio.create_subprocess_exec(
                str(self._path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={"LC_ALL": "C", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
            )
            if process.stdin is None or process.stdout is None:
                raise EmbeddingProviderError("local embedding pipes are unavailable")
            async with asyncio.timeout(self._timeout_seconds):
                process.stdin.write(encoded)
                await process.stdin.drain()
                process.stdin.close()
                await process.stdin.wait_closed()
                output = await process.stdout.read(MAX_LOCAL_EMBEDDING_RESPONSE_BYTES + 1)
                await process.wait()
        except (OSError, TimeoutError) as error:
            raise EmbeddingProviderError("local embedding execution failed") from error
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
        if (
            process is None
            or process.returncode != 0
            or len(output) > MAX_LOCAL_EMBEDDING_RESPONSE_BYTES
        ):
            raise EmbeddingProviderError("local embedding helper failed")
        try:
            payload = json.loads(output)
            raw_vectors = payload["vectors"]
            if (
                not isinstance(payload, dict)
                or set(payload) != {"model_id", "vectors"}
                or payload["model_id"] != self.model_id
                or not isinstance(raw_vectors, list)
                or len(raw_vectors) != len(normalized)
            ):
                raise ValueError
            vectors = tuple(self._normalize_vector(vector) for vector in raw_vectors)
            return EmbeddingBatch(model_id=self.model_id, vectors=vectors)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise EmbeddingProviderError("local embedding output is invalid") from error

    def _is_private_executable(self) -> bool:
        try:
            status = self._path.stat(follow_symlinks=False)
        except OSError:
            return False
        return (
            stat.S_ISREG(status.st_mode)
            and status.st_uid == os.getuid()
            and bool(status.st_mode & stat.S_IXUSR)
            and not bool(status.st_mode & 0o022)
        )

    @staticmethod
    def _normalize_vector(raw: object) -> tuple[float, ...]:
        if (
            not isinstance(raw, list)
            or not 1 <= len(raw) <= 8_192
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in raw
            )
        ):
            raise ValueError("embedding vector is invalid")
        vector = tuple(float(value) for value in raw)
        norm = math.sqrt(math.fsum(value * value for value in vector))
        if not math.isfinite(norm) or norm <= 0:
            raise ValueError("embedding vector has no norm")
        return tuple(value / norm for value in vector)
