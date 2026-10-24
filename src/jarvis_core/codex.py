"""Original stdio client for the official Codex app-server protocol.

Only this client's new HOME is visible to its configuration discovery. No credential
file or external history is imported or read by Jarvis. The first slice is chat-only:
built-in effect tools are disabled and all server requests fail closed. Tool/approval
bridging is a separate, still unqualified part of the baseline parity matrix.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from jarvis_core.state import ensure_private_directory


class CodexError(RuntimeError):
    """A stable public error code, never raw stderr, credentials or RPC error text."""


class CodexClient:
    def __init__(self, executable: Path, directory: Path, *, timeout: float = 20) -> None:
        if not executable.is_absolute() or not executable.is_file():
            raise ValueError("codex_executable_invalid")
        if not 0 < timeout <= 30:
            raise ValueError("codex_timeout_invalid")
        self.executable = executable
        self.directory = directory
        self.timeout = timeout
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task | None = None
        self._stderr: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._events: dict[str, asyncio.Queue] = {}
        self._threads: set[str] = set()
        self._uncertain_threads: set[str] = set()
        self._next_id = 0
        self._start_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._closed = False

    async def start(self) -> None:
        async with self._start_lock:
            if self._closed:
                raise CodexError("codex_closed")
            if self._process is not None:
                if (self._process.returncode is not None
                        or self._reader is None or self._reader.done()):
                    raise CodexError("codex_disconnected")
                return
            directory = ensure_private_directory(self.directory)
            home = ensure_private_directory(directory / "home")
            codex_home = ensure_private_directory(directory / "client")
            workspace = ensure_private_directory(directory / "workspace")
            environment = {key: os.environ[key] for key in ("PATH", "TMPDIR", "LANG")
                           if key in os.environ}
            environment.update(HOME=str(home), CODEX_HOME=str(codex_home))
            arguments = [str(self.executable), "app-server", "--listen", "stdio://"]
            # No inherited MCP servers, host skills, plugins, provider keys or proxies.
            for setting in (
                'cli_auth_credentials_store="file"', 'web_search="disabled"',
                "features.shell_tool=false", "features.apps=false",
                "features.multi_agent=false", "features.image_generation=false",
                "features.view_image=false", "features.skill_search=false",
                "features.skill_mcp_dependency_install=false",
                "features.skip_host_skill_discovery=true",
            ):
                arguments.extend(("-c", setting))
            try:
                self._process = await asyncio.create_subprocess_exec(
                    *arguments, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, env=environment, cwd=workspace,
                    limit=1024 * 1024, start_new_session=True,
                )
                self._reader = asyncio.create_task(self._read())
                self._stderr = asyncio.create_task(self._drain_stderr())
                await self.request("initialize", {
                    "clientInfo": {"name": "jarvis", "title": "Jarvis", "version": "0.1.0"},
                })
                await self._write({"method": "initialized", "params": {}})
            except BaseException:
                await self._stop_process_locked()
                raise

    async def _write(self, message: dict) -> None:
        data = json.dumps(message, allow_nan=False, separators=(",", ":")).encode() + b"\n"
        if len(data) > 1024 * 1024:
            raise CodexError("codex_frame_too_large")
        async with self._write_lock:
            process = self._process
            if process is None or process.stdin is None or process.returncode is not None:
                raise CodexError("codex_disconnected")
            try:
                process.stdin.write(data)
                await asyncio.wait_for(process.stdin.drain(), self.timeout)
            except (OSError, TimeoutError) as error:
                raise CodexError("codex_disconnected") from error

    async def request(self, method: str, params: dict) -> dict:
        self._next_id += 1
        request_id = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, self.timeout)
        except TimeoutError as error:
            raise CodexError("codex_request_timeout") from error
        finally:
            self._pending.pop(request_id, None)

    async def _read(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            while line := await self._process.stdout.readline():
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError
                if "method" in message:
                    if "id" in message:
                        # Unknown or unimplemented approval/tool requests never authorize effects.
                        await self._write({"id": message["id"], "error": {
                            "code": -32601, "message": "Jarvis no ha habilitado esta acción",
                        }})
                        continue
                    params = message.get("params", {})
                    if not isinstance(params, dict):
                        raise ValueError
                    queue = self._events.get(params.get("threadId"))
                    if queue is not None:
                        queue.put_nowait((message["method"], params))
                elif type(message.get("id")) is int:
                    future = self._pending.get(message["id"])
                    if future is not None and not future.done():
                        if "error" in message:
                            future.set_exception(CodexError("codex_rpc_error"))
                        elif isinstance(message.get("result"), dict):
                            future.set_result(message["result"])
                        else:
                            future.set_exception(CodexError("codex_invalid_response"))
        except (OSError, ValueError, asyncio.QueueFull):
            pass
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(CodexError("codex_disconnected"))
            for queue in self._events.values():
                # A full queue is already a failed stream; never block shutdown.
                if queue.full():
                    queue.get_nowait()
                queue.put_nowait(("disconnected", {}))

    async def _drain_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        while await self._process.stderr.read(8192):
            pass  # Never persist provider diagnostics containing prompts or credentials.

    async def account_status(self) -> dict:
        await self.start()
        result = await self.request("account/read", {"refreshToken": False})
        account = result.get("account")
        mode = account.get("type") if isinstance(account, dict) else None
        return {"connected": mode == "chatgpt", "mode": "chatgpt" if mode == "chatgpt" else None}

    async def login(self) -> dict:
        await self.start()
        result = await self.request("account/login/start", {"type": "chatgpt"})
        url = result.get("authUrl")
        try:
            parts = urlsplit(url) if isinstance(url, str) else None
            valid = (parts is not None and parts.scheme == "https"
                     and parts.hostname == "auth.openai.com" and not parts.username
                     and not parts.password and parts.port in (None, 443))
        except ValueError:
            valid = False
        if not valid:
            raise CodexError("codex_login_url_invalid")
        return {"url": url}

    async def logout(self) -> dict:
        await self.start()
        await self.request("account/logout", {})
        return {"connected": False, "mode": None}

    async def chat(
        self, message: str, *, thread_id: str | None = None, temporary: bool = False,
        on_event: Callable[[str, dict], None] | None = None,
        on_thread: Callable[[str], None] | None = None, turn_timeout: float = 180,
    ) -> dict:
        if not isinstance(message, str) or not message.strip() or len(message.encode()) > 32000:
            raise ValueError("codex_message_invalid")
        if not (await self.account_status())["connected"]:
            raise CodexError("codex_login_required")
        if thread_id in self._uncertain_threads:
            raise CodexError("codex_thread_recovery_required")
        workspace = str(self.directory / "workspace")
        if thread_id is None:
            result = await self.request("thread/start", {
                "cwd": workspace, "ephemeral": temporary, "sandbox": "read-only",
                "approvalPolicy": "untrusted", "approvalsReviewer": "user",
                "developerInstructions": (
                    "Responde en español. Este canal solo tiene chat de texto."
                ),
            })
            thread_id = result.get("thread", {}).get("id")
            if not isinstance(thread_id, str) or not 1 <= len(thread_id) <= 256:
                raise CodexError("codex_thread_invalid")
            self._threads.add(thread_id)
            if on_thread:
                on_thread(thread_id)
        elif thread_id not in self._threads:
            # Only a binding supplied by our own session store can be resumed. No thread/list.
            await self.request("thread/resume", {
                "threadId": thread_id, "cwd": workspace, "sandbox": "read-only",
                "approvalPolicy": "untrusted", "approvalsReviewer": "user",
            })
            self._threads.add(thread_id)
        if thread_id in self._events:
            raise CodexError("codex_thread_busy")
        queue: asyncio.Queue = asyncio.Queue(maxsize=1024)
        self._events[thread_id] = queue
        turn_id = None
        settled = False
        try:
            result = await self.request("turn/start", {
                "threadId": thread_id, "input": [{"type": "text", "text": message}],
            })
            turn_id = result.get("turn", {}).get("id")
            if not isinstance(turn_id, str):
                raise CodexError("codex_turn_invalid")
            text_parts: dict[str, str] = {}
            async with asyncio.timeout(turn_timeout):
                while True:
                    method, params = await queue.get()
                    if method == "disconnected":
                        raise CodexError("codex_disconnected")
                    turn = params.get("turn", {})
                    if not isinstance(turn, dict):
                        raise CodexError("codex_event_invalid")
                    if params.get("turnId", turn.get("id")) != turn_id:
                        continue
                    if method == "item/agentMessage/delta":
                        delta, item_id = params.get("delta"), params.get("itemId")
                        if not isinstance(delta, str) or not isinstance(item_id, str):
                            raise CodexError("codex_delta_invalid")
                        text_parts[item_id] = text_parts.get(item_id, "") + delta
                        if sum(len(part.encode()) for part in text_parts.values()) > 32000:
                            raise CodexError("codex_answer_too_large")
                        if on_event:
                            on_event("answer.delta", {"text": delta})
                    elif method == "item/completed":
                        item = params.get("item", {})
                        if item.get("type") == "agentMessage":
                            if not isinstance(item.get("text"), str):
                                raise CodexError("codex_answer_invalid")
                            text_parts[item.get("id", "final")] = item["text"]
                    elif method == "turn/completed":
                        settled = True
                        if params.get("turn", {}).get("status") != "completed":
                            raise CodexError("codex_turn_failed")
                        answer = "\n\n".join(text_parts.values()).strip()
                        if not answer or len(answer.encode()) > 32000:
                            raise CodexError("codex_answer_invalid")
                        return {"thread_id": thread_id, "answer": answer}
        except BaseException:
            # An interrupt ACK is not settlement. Retain ownership until its
            # terminal event, or terminate the owned process and quarantine all
            # affected threads. Never replay a possibly accepted turn/start.
            if not settled:
                stopped = await self._interrupt_and_settle(thread_id, turn_id, queue)
                if not stopped:
                    self._uncertain_threads.update(self._events)
                    await self._stop_process()
            raise
        finally:
            self._events.pop(thread_id, None)

    async def _interrupt_and_settle(
        self, thread_id: str, turn_id: str | None, queue: asyncio.Queue,
    ) -> bool:
        if turn_id is None:
            return False
        try:
            async with asyncio.timeout(2):
                await self.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
                while True:
                    method, params = await queue.get()
                    if method == "disconnected":
                        return False
                    if method == "turn/completed" and params.get("turn", {}).get("id") == turn_id:
                        return True
        except (CodexError, OSError, TimeoutError, asyncio.CancelledError):
            return False

    async def _stop_process(self) -> None:
        async with self._start_lock:
            await self._stop_process_locked()

    async def _stop_process_locked(self) -> None:
        process = self._process
        if process is not None and process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), 2)
            except TimeoutError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
        for task in (self._reader, self._stderr):
            if task is not None:
                task.cancel()
        await asyncio.gather(*(t for t in (self._reader, self._stderr) if t),
                             return_exceptions=True)
        self._process = None
        self._reader = self._stderr = None
        self._threads.clear()

    async def aclose(self) -> None:
        self._closed = True
        await self._stop_process()
