from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import stat
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.tools.audit import AuditSink, NullAuditSink

_PROTOCOL_VERSION = "1.0"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_IMAGE_BYTES = 128 * 1_024
_MAX_FRAME_BYTES = 262_144


class LocalizedVisionError(RuntimeError):
    """The ephemeral local VLM rejected or failed to analyze a bounded crop."""


@dataclass(frozen=True, slots=True)
class LocalizedVisionDecision:
    offset_x: float
    offset_y: float
    confidence: float
    target_found: bool


class LocalizedVisionAnalyzer:
    def __init__(
        self,
        executable_path: Path,
        *,
        model_directory: Path,
        timeout_seconds: float = 15.0,
        audit_sink: AuditSink | None = None,
    ) -> None:
        if not 1.0 <= timeout_seconds <= 60.0:
            raise ValueError("localized VLM timeout is out of range")
        self._executable_path = executable_path
        self._model_directory = model_directory
        self._timeout_seconds = timeout_seconds
        self._audit = audit_sink or NullAuditSink()
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return self._private_executable() and self._private_model_directory()

    async def analyze(
        self,
        *,
        image_png: bytes,
        target_description: str,
        expected_x: float,
        expected_y: float,
        request_id: UUID | None = None,
    ) -> LocalizedVisionDecision:
        event_id = request_id or uuid4()
        self._validate_request(
            image_png=image_png,
            target_description=target_description,
            expected_x=expected_x,
            expected_y=expected_y,
        )
        if not self.available:
            raise LocalizedVisionError("localized VLM is not installed or is not private")
        payload = {
            "protocol_version": _PROTOCOL_VERSION,
            "request_id": str(event_id),
            "task": "localized_click_offset",
            "target_description": target_description,
            "expected_x": expected_x,
            "expected_y": expected_y,
            "image_png_base64": base64.b64encode(image_png).decode("ascii"),
        }
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(encoded) > _MAX_FRAME_BYTES:
            raise LocalizedVisionError("localized VLM request exceeds its frame limit")
        started = asyncio.get_running_loop().time()
        async with self._lock:
            response = await self._invoke(encoded)
        decision = self._parse_response(response, event_id)
        elapsed_ms = round((asyncio.get_running_loop().time() - started) * 1_000)
        self._audit.record_system_event(
            event_id,
            event_type="localized_vision_completed",
            component="vision_fallback",
            data={
                "elapsed_ms": elapsed_ms,
                "image_bytes": len(image_png),
                "image_sha256": hashlib.sha256(image_png).hexdigest(),
                "confidence_milli": round(decision.confidence * 1_000),
                "target_found": decision.target_found,
                "ephemeral_process": True,
            },
        )
        return decision

    async def _invoke(self, encoded: bytes) -> dict[str, Any]:
        environment = {
            "AEGIS_MLX_VLM_DIRECTORY": str(self._model_directory),
            "HF_HUB_OFFLINE": "1",
            "HOME": str(Path.home()),
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        }
        try:
            process = await asyncio.create_subprocess_exec(
                str(self._executable_path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=environment,
            )
        except OSError as error:
            raise LocalizedVisionError("localized VLM launch failed") from error
        try:
            framed = struct.pack(">I", len(encoded)) + encoded
            async with asyncio.timeout(self._timeout_seconds):
                stdout, _ = await process.communicate(framed)
        except TimeoutError as error:
            process.kill()
            await process.wait()
            raise LocalizedVisionError("localized VLM inference timed out") from error
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        if len(stdout) < 4:
            raise LocalizedVisionError("localized VLM returned an incomplete frame")
        length = struct.unpack(">I", stdout[:4])[0]
        if not 1 <= length <= 65_536 or len(stdout) != length + 4:
            raise LocalizedVisionError("localized VLM returned an invalid frame")
        try:
            response = json.loads(stdout[4:])
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise LocalizedVisionError("localized VLM response is not JSON") from error
        if not isinstance(response, dict):
            raise LocalizedVisionError("localized VLM response has an invalid shape")
        return response

    @staticmethod
    def _parse_response(
        response: dict[str, Any], request_id: UUID
    ) -> LocalizedVisionDecision:
        if (
            response.get("protocol_version") != _PROTOCOL_VERSION
            or response.get("request_id") != str(request_id)
            or response.get("success") is not True
            or response.get("error_code") is not None
            or not isinstance(response.get("decision"), dict)
        ):
            raise LocalizedVisionError("localized VLM rejected the request")
        raw = response["decision"]
        if set(raw) != {"offset_x", "offset_y", "confidence", "target_found"}:
            raise LocalizedVisionError("localized VLM decision has unknown fields")
        values = (raw["offset_x"], raw["offset_y"], raw["confidence"])
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
            raise LocalizedVisionError("localized VLM decision is not numeric")
        offset_x, offset_y, confidence = map(float, values)
        target_found = raw["target_found"]
        if (
            not isinstance(target_found, bool)
            or not -60 <= offset_x <= 60
            or not -60 <= offset_y <= 60
            or not 0 <= confidence <= 1
        ):
            raise LocalizedVisionError("localized VLM decision is outside policy")
        return LocalizedVisionDecision(offset_x, offset_y, confidence, target_found)

    @staticmethod
    def _validate_request(
        *,
        image_png: bytes,
        target_description: str,
        expected_x: float,
        expected_y: float,
    ) -> None:
        if (
            not isinstance(image_png, bytes)
            or not 33 <= len(image_png) <= _MAX_IMAGE_BYTES
            or image_png[:8] != _PNG_SIGNATURE
            or image_png[12:16] != b"IHDR"
            or struct.unpack(">II", image_png[16:24]) != (120, 120)
        ):
            raise LocalizedVisionError("localized vision input must be a 120x120 PNG")
        if not target_description or len(target_description.encode("utf-8")) > 512:
            raise LocalizedVisionError("localized vision target is invalid")
        if not 0 <= expected_x < 120 or not 0 <= expected_y < 120:
            raise LocalizedVisionError("localized vision expected point is outside the crop")

    def _private_executable(self) -> bool:
        try:
            status = self._executable_path.stat(follow_symlinks=False)
        except OSError:
            return False
        return (
            stat.S_ISREG(status.st_mode)
            and status.st_uid == os.getuid()
            and bool(status.st_mode & stat.S_IXUSR)
            and not bool(status.st_mode & 0o022)
        )

    def _private_model_directory(self) -> bool:
        try:
            status = self._model_directory.stat(follow_symlinks=False)
        except OSError:
            return False
        return (
            stat.S_ISDIR(status.st_mode)
            and status.st_uid == os.getuid()
            and not bool(status.st_mode & 0o022)
        )


class VisionFallbackIpcService:
    METHOD = "vision.localized.analyze"

    def __init__(self, analyzer: LocalizedVisionAnalyzer) -> None:
        self._analyzer = analyzer

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {self.METHOD: self.handle}

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        if request.method != self.METHOD:
            return IpcHandlerResult(ok=False, error_code="method_not_found")
        if set(request.payload) != {
            "image_png_base64",
            "target_description",
            "expected_x",
            "expected_y",
        }:
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        try:
            encoded = request.payload["image_png_base64"]
            if not isinstance(encoded, str) or len(encoded) > 175_000:
                raise ValueError
            image = base64.b64decode(encoded, validate=True)
            target = request.payload["target_description"]
            expected_x = request.payload["expected_x"]
            expected_y = request.payload["expected_y"]
            if (
                not isinstance(target, str)
                or isinstance(expected_x, bool)
                or not isinstance(expected_x, (int, float))
                or isinstance(expected_y, bool)
                or not isinstance(expected_y, (int, float))
            ):
                raise ValueError
            decision = await self._analyzer.analyze(
                image_png=image,
                target_description=target,
                expected_x=float(expected_x),
                expected_y=float(expected_y),
                request_id=request.request_id,
            )
        except (ValueError, LocalizedVisionError):
            return IpcHandlerResult(ok=False, error_code="localized_vision_failed")
        return IpcHandlerResult(
            ok=True,
            payload={
                "offset_x": decision.offset_x,
                "offset_y": decision.offset_y,
                "confidence": decision.confidence,
                "target_found": decision.target_found,
            },
        )
