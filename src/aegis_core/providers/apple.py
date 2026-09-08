from __future__ import annotations

import asyncio
import json
import math
import os
import stat
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from aegis_core.contracts import AgentResult, AgentRole

MAX_LOCAL_REQUEST_BYTES = 24_576
MAX_LOCAL_RESPONSE_BYTES = 24_576
MAX_LOCAL_STREAM_EVENT_BYTES = 65_536
MAX_LOCAL_STREAM_EVENTS = 8_192
MAX_LOCAL_RESPONSE_TOKENS = 4_096
MAX_LOCAL_TEMPERATURE = 2.0
PERSISTENT_PROTOCOL_VERSION = "2.0"


class AppleLocalModelError(RuntimeError):
    """The bounded Apple Foundation Models helper is unavailable or failed closed."""


class AppleLocalModelClient:
    model_id = "apple/system-language-model"

    def __init__(
        self,
        executable_path: Path,
        *,
        timeout_seconds: float = 20.0,
        first_event_timeout_seconds: float = 4.0,
    ) -> None:
        if not 1 <= timeout_seconds <= 60:
            raise ValueError("local model timeout is out of range")
        if not 0.05 <= first_event_timeout_seconds <= timeout_seconds:
            raise ValueError("local model first event timeout is out of range")
        self._path = executable_path
        self._timeout_seconds = timeout_seconds
        self._first_event_timeout_seconds = first_event_timeout_seconds
        self._status_lock = threading.Lock()
        self._status_checked = False
        self._available = False
        self._persistent_protocol = False
        self._request_lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None

    @property
    def executable_path(self) -> Path:
        return self._path

    def is_available(self) -> bool:
        with self._status_lock:
            if self._status_checked:
                return self._available
            available = False
            persistent_protocol = False
            if self._is_private_executable():
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
                    available = (
                        completed.returncode == 0
                        and isinstance(payload, dict)
                        and payload.get("available") is True
                    )
                    persistent_protocol = (
                        available
                        and payload.get("protocol_version") == PERSISTENT_PROTOCOL_VERSION
                    )
                except (OSError, ValueError):
                    pass
            self._available = available
            self._persistent_protocol = persistent_protocol
            self._status_checked = True
            return available

    async def prewarm(self) -> None:
        """Start and prewarm the long-lived native model helper when supported."""
        if not self.is_available():
            raise AppleLocalModelError("local model helper is unavailable")
        if not self._persistent_protocol:
            return
        async with self._request_lock:
            await self._ensure_persistent_process()

    async def aclose(self) -> None:
        async with self._request_lock:
            await self._stop_persistent_process()

    async def complete(
        self,
        *,
        role: AgentRole,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int | None = None,
        temperature: float | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> AgentResult:
        return await self.complete_stream(
            role=role,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            extra_body=extra_body,
            on_delta=None,
        )

    async def complete_stream(
        self,
        *,
        role: AgentRole,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int | None = None,
        temperature: float | None = None,
        extra_body: Mapping[str, Any] | None = None,
        on_delta: Callable[[str], None] | None,
    ) -> AgentResult:
        tool_augmented = self._tool_augmented_mode(extra_body)
        if not self._is_private_executable():
            raise AppleLocalModelError("local model helper is unavailable")
        maximum_response_tokens, local_temperature = self._generation_options(
            max_tokens=max_tokens,
            temperature=temperature,
        )
        instructions, prompt = self._bounded_prompt(messages)
        encoded = json.dumps(
            {
                "instructions": instructions,
                "prompt": prompt,
                "maximumResponseTokens": maximum_response_tokens,
                "temperature": local_temperature,
                "toolAugmented": tool_augmented,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > MAX_LOCAL_REQUEST_BYTES:
            raise AppleLocalModelError("local model request is too large")
        if self._persistent_protocol:
            return await self._complete_persistent(
                role=role,
                encoded=encoded,
                on_delta=on_delta,
            )
        return await self._complete_one_shot(
            role=role,
            encoded=encoded,
            on_delta=on_delta,
        )

    async def _complete_persistent(
        self,
        *,
        role: AgentRole,
        encoded: bytes,
        on_delta: Callable[[str], None] | None,
    ) -> AgentResult:
        request_id = str(uuid4())
        try:
            request = json.loads(encoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AppleLocalModelError("local model request is invalid") from error
        request["request_id"] = request_id
        framed = json.dumps(
            request,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        if len(framed) > MAX_LOCAL_REQUEST_BYTES:
            raise AppleLocalModelError("local model request is too large")
        async with self._request_lock:
            try:
                process = await self._ensure_persistent_process()
                if process.stdin is None or process.stdout is None:
                    raise AppleLocalModelError("local model pipes are unavailable")
                process.stdin.write(framed)
                await process.stdin.drain()
                accumulated = await self._read_persistent_response(
                    process,
                    request_id=request_id,
                    on_delta=on_delta,
                )
            except (OSError, TimeoutError, ValueError, AppleLocalModelError):
                await self._stop_persistent_process()
                raise
        return AgentResult(
            role=role,
            model_id=self.model_id,
            content=accumulated,
            finish_reason="stop",
        )

    async def _read_persistent_response(
        self,
        process: asyncio.subprocess.Process,
        *,
        request_id: str,
        on_delta: Callable[[str], None] | None,
    ) -> str:
        assert process.stdout is not None
        accumulated = ""
        stream_events = 0
        first_event_received = False
        async with asyncio.timeout(self._timeout_seconds):
            while True:
                if first_event_received:
                    raw_line = await process.stdout.readline()
                else:
                    try:
                        raw_line = await asyncio.wait_for(
                            process.stdout.readline(),
                            timeout=self._first_event_timeout_seconds,
                        )
                    except TimeoutError as error:
                        raise AppleLocalModelError(
                            "local model first response timed out"
                        ) from error
                if not raw_line:
                    raise AppleLocalModelError("local model process closed unexpectedly")
                stream_events += 1
                if (
                    stream_events > MAX_LOCAL_STREAM_EVENTS
                    or len(raw_line) > MAX_LOCAL_STREAM_EVENT_BYTES
                ):
                    raise AppleLocalModelError("local model stream exceeds safety ceiling")
                event = self._decode_stream_event(raw_line, request_id=request_id)
                event_type = event["type"]
                content = event["content"]
                if event_type == "error":
                    raise AppleLocalModelError("local model rejected the request")
                if not content.startswith(accumulated):
                    raise AppleLocalModelError("local model stream is not monotonic")
                delta = content[len(accumulated) :]
                accumulated = content
                if delta and on_delta is not None:
                    on_delta(delta)
                first_event_received = True
                if event_type == "completed":
                    normalized = accumulated.strip()
                    if not normalized:
                        raise AppleLocalModelError("local model response is incomplete")
                    return normalized

    async def _ensure_persistent_process(self) -> asyncio.subprocess.Process:
        process = self._process
        if process is not None and process.returncode is None:
            return process
        if not self._is_private_executable():
            raise AppleLocalModelError("local model helper is unavailable")
        process = await asyncio.create_subprocess_exec(
            str(self._path),
            "--serve-stdio",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={"LC_ALL": "C", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        )
        if process.stdin is None or process.stdout is None:
            process.kill()
            await process.wait()
            raise AppleLocalModelError("local model pipes are unavailable")
        try:
            ready_line = await asyncio.wait_for(
                process.stdout.readline(),
                timeout=self._first_event_timeout_seconds,
            )
            ready = json.loads(ready_line)
        except (OSError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as error:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise AppleLocalModelError("local model prewarm failed") from error
        if (
            not isinstance(ready, dict)
            or ready != {
                "protocol_version": PERSISTENT_PROTOCOL_VERSION,
                "type": "ready",
            }
        ):
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise AppleLocalModelError("local model ready event is invalid")
        self._process = process
        return process

    async def _stop_persistent_process(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()
            try:
                await process.stdin.wait_closed()
            except (BrokenPipeError, ConnectionError):
                pass
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=1.0)
            except TimeoutError:
                process.kill()
                await process.wait()

    @staticmethod
    def _decode_stream_event(raw_line: bytes, *, request_id: str) -> dict[str, str]:
        try:
            event = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AppleLocalModelError("local model returned invalid output") from error
        if (
            not isinstance(event, dict)
            or set(event) != {"request_id", "type", "content"}
            or event.get("request_id") != request_id
            or event.get("type") not in {"snapshot", "completed", "error"}
            or not isinstance(event.get("content"), str)
            or len(event["content"].encode("utf-8")) > MAX_LOCAL_RESPONSE_BYTES
        ):
            raise AppleLocalModelError("local model returned invalid output")
        return event

    async def _complete_one_shot(
        self,
        *,
        role: AgentRole,
        encoded: bytes,
        on_delta: Callable[[str], None] | None,
    ) -> AgentResult:
        process: asyncio.subprocess.Process | None = None
        accumulated = ""
        completed = False
        stream_events = 0
        try:
            process = await asyncio.create_subprocess_exec(
                str(self._path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={"LC_ALL": "C", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
            )
            if process.stdin is None or process.stdout is None:
                raise AppleLocalModelError("local model pipes are unavailable")
            async with asyncio.timeout(self._timeout_seconds):
                process.stdin.write(encoded)
                await process.stdin.drain()
                process.stdin.close()
                await process.stdin.wait_closed()
                first_event_received = False
                while True:
                    if first_event_received:
                        raw_line = await process.stdout.readline()
                    else:
                        try:
                            raw_line = await asyncio.wait_for(
                                process.stdout.readline(),
                                timeout=self._first_event_timeout_seconds,
                            )
                        except TimeoutError as error:
                            raise AppleLocalModelError(
                                "local model first response timed out"
                            ) from error
                    if not raw_line:
                        break
                    stream_events += 1
                    if (
                        stream_events > MAX_LOCAL_STREAM_EVENTS
                        or len(raw_line) > MAX_LOCAL_STREAM_EVENT_BYTES
                    ):
                        raise AppleLocalModelError("local model stream exceeds safety ceiling")
                    try:
                        event = json.loads(raw_line)
                    except (UnicodeDecodeError, json.JSONDecodeError) as error:
                        raise AppleLocalModelError("local model returned invalid output") from error
                    if not isinstance(event, dict) or set(event) != {"type", "content"}:
                        raise AppleLocalModelError("local model returned invalid output")
                    content = event.get("content")
                    if (
                        not isinstance(content, str)
                        or len(content.encode("utf-8")) > MAX_LOCAL_RESPONSE_BYTES
                    ):
                        raise AppleLocalModelError("local model returned invalid output")
                    if event.get("type") == "snapshot":
                        if not content.startswith(accumulated):
                            raise AppleLocalModelError("local model stream is not monotonic")
                        delta = content[len(accumulated) :]
                        accumulated = content
                        if delta and on_delta is not None:
                            on_delta(delta)
                    elif event.get("type") == "completed":
                        if not content.startswith(accumulated):
                            raise AppleLocalModelError("local model completion is inconsistent")
                        delta = content[len(accumulated) :]
                        accumulated = content
                        if delta and on_delta is not None:
                            on_delta(delta)
                        completed = True
                    else:
                        raise AppleLocalModelError("local model returned an unknown event")
                    first_event_received = True
                await process.wait()
        except (OSError, TimeoutError, ValueError) as error:
            raise AppleLocalModelError("local model execution failed") from error
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
        if process is None or process.returncode != 0:
            raise AppleLocalModelError("local model returned no valid response")
        normalized = accumulated.strip()
        if not completed or not normalized:
            raise AppleLocalModelError("local model response is incomplete")
        return AgentResult(
            role=role,
            model_id=self.model_id,
            content=normalized,
            finish_reason="stop",
        )

    @staticmethod
    def _tool_augmented_mode(extra_body: Mapping[str, Any] | None) -> bool:
        if extra_body is None:
            return False
        if set(extra_body) != {"local_tool_augmented"}:
            raise AppleLocalModelError("local model request options are unsupported")
        enabled = extra_body["local_tool_augmented"]
        if enabled is not True:
            raise AppleLocalModelError("local tool augmentation must be explicitly enabled")
        return True

    @staticmethod
    def _generation_options(
        *,
        max_tokens: int | None,
        temperature: float | None,
    ) -> tuple[int | None, float | None]:
        if max_tokens is not None and (
            isinstance(max_tokens, bool)
            or not isinstance(max_tokens, int)
            or not 1 <= max_tokens <= MAX_LOCAL_RESPONSE_TOKENS
        ):
            raise AppleLocalModelError("local model token limit is out of range")
        if temperature is not None and (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not math.isfinite(temperature)
            or not 0.0 <= temperature <= MAX_LOCAL_TEMPERATURE
        ):
            raise AppleLocalModelError("local model temperature is out of range")
        return max_tokens, float(temperature) if temperature is not None else None

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
    def _bounded_prompt(messages: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
        instructions: list[str] = []
        prompts: list[str] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if not isinstance(role, str) or not isinstance(content, str):
                raise AppleLocalModelError("local model accepts text messages only")
            if role == "system":
                instructions.append(content)
            else:
                prompts.append(f"{role}: {content}")
        instruction_text = "\n".join(instructions)
        prompt_text = "\n".join(prompts)
        if not instruction_text or not prompt_text:
            raise AppleLocalModelError("local model prompt is incomplete")
        return instruction_text, prompt_text
