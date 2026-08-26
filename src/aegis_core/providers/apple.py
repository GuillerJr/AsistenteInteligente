from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from aegis_core.contracts import AgentResult, AgentRole

MAX_LOCAL_REQUEST_BYTES = 24_576
MAX_LOCAL_RESPONSE_BYTES = 24_576
MAX_LOCAL_STREAM_BYTES = 262_144


class AppleLocalModelError(RuntimeError):
    """The bounded Apple Foundation Models helper is unavailable or failed closed."""


class AppleLocalModelClient:
    model_id = "apple/system-language-model"

    def __init__(self, executable_path: Path, *, timeout_seconds: float = 20.0) -> None:
        if not 1 <= timeout_seconds <= 60:
            raise ValueError("local model timeout is out of range")
        self._path = executable_path
        self._timeout_seconds = timeout_seconds

    @property
    def executable_path(self) -> Path:
        return self._path

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
            and payload.get("available") is True
        )

    async def complete(
        self,
        *,
        role: AgentRole,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int | None = None,
        temperature: float | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> AgentResult:
        del max_tokens, temperature
        return await self.complete_stream(
            role=role,
            messages=messages,
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
        del max_tokens, temperature
        if extra_body:
            raise AppleLocalModelError("local model tools are not supported")
        if role is not AgentRole.PLANNER:
            raise AppleLocalModelError("local model role is unsupported")
        if not self._is_private_executable():
            raise AppleLocalModelError("local model helper is unavailable")
        instructions, prompt = self._bounded_prompt(messages)
        encoded = json.dumps(
            {"instructions": instructions, "prompt": prompt},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > MAX_LOCAL_REQUEST_BYTES:
            raise AppleLocalModelError("local model request is too large")
        process: asyncio.subprocess.Process | None = None
        accumulated = ""
        completed = False
        total_stream_bytes = 0
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
                while raw_line := await process.stdout.readline():
                    total_stream_bytes += len(raw_line)
                    if total_stream_bytes > MAX_LOCAL_STREAM_BYTES:
                        raise AppleLocalModelError("local model stream is too large")
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
                await process.wait()
        except (OSError, TimeoutError) as error:
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
