"""Bounded stdlib parent for the selected offline CPU embedding worker."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import math
import os
import signal
import stat
import struct
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from jarvis_core.model_artifacts import MODEL_FINGERPRINT

FRAME_LIMIT = 1024 * 1024
MAX_TEXT_BYTES = 65536
MAX_CHUNKS = 256
MAX_SPAN_BYTES = 768
ENGINE_SPEC = {"format": "jarvis-minilm-encoder/1", "assets": MODEL_FINGERPRINT,
               "chunking": "complete-utf8-token-offsets-128-overlap16-max768bytes-v1",
               "query_reduction": "complete-single-query-max512bytes-128tokens-v1",
               "max_text_bytes": MAX_TEXT_BYTES, "max_chunks": MAX_CHUNKS,
               "pooling": "float32-attention-masked-mean-including-specials-l2",
               "graph_optimization": "ORT_ENABLE_BASIC", "batch_size": 8}
ENGINE_FINGERPRINT = hashlib.sha256(json.dumps(
    ENGINE_SPEC, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
_ERRORS = {"missing_model", "invalid_model", "unavailable_runtime", "capacity",
           "timeout", "cancelled", "cleanup_pending"}


class EngineError(Exception):
    def __init__(self, code, *, diagnostic=None):
        self.code = code if code in _ERRORS else "unavailable_runtime"
        self.diagnostic = diagnostic
        super().__init__(self.code)
        if diagnostic:
            self.add_note("engine_diagnostic=" + json.dumps(diagnostic, sort_keys=True))


@dataclass(frozen=True)
class EncodedSpan:
    start_byte: int
    end_byte: int
    token_count: int
    vector: tuple[float, ...]


def _text_bytes(text):
    try:
        if not isinstance(text, str) or not text.strip() or "\0" in text:
            raise EngineError("capacity")
        raw = text.encode()
        if len(raw) > MAX_TEXT_BYTES:
            raise EngineError("capacity")
        return raw
    except UnicodeError:
        raise EngineError("capacity") from None


def _vector(value):
    try:
        raw = base64.b64decode(value, validate=True)
        if len(raw) != 384 * 4:
            raise ValueError
        vector = struct.unpack("<384f", raw)
        if not all(math.isfinite(number) for number in vector):
            raise ValueError
        norm = math.sqrt(math.fsum(number * number for number in vector))
        if not 0.999 <= norm <= 1.001:
            raise ValueError
        return vector
    except (ValueError, TypeError, struct.error):
        raise EngineError("invalid_model") from None


def _policy(model, temporary, python):
    package = Path(__file__).resolve().parent
    # Editable installs need PathFinder to list the source parent before it can
    # enter jarvis_core. Never derive a blanket /private/tmp grant from staging.
    code_root = package.parent if package.name == "jarvis_core" else package
    roots = {code_root, Path(sys.prefix).resolve(),
             Path(sys.base_prefix).resolve(), model, temporary,
             Path("/System"), Path("/usr"), Path("/bin"), Path("/sbin"),
             Path("/opt/homebrew"), Path("/Library/Frameworks/Python.framework"),
             Path("/private/var/db/dyld"), Path("/private/etc")}
    reads = " ".join(f"(subpath {json.dumps(str(root))})" for root in sorted(roots))
    programs = {str(python), str(python.resolve())}
    executables = " ".join(f"(literal {json.dumps(path)})" for path in programs)
    return "\n".join([
        "(version 1)", "(deny default)",
        "(allow sysctl-read file-read-metadata)",
        f"(allow process-exec {executables})", "(deny process-fork)",
        f'(allow file-read* (literal "/") (literal "/dev/urandom") '
        f'(literal "/dev/null") {reads})',
        "(deny file-write* network*)",
    ])


class LocalEmbeddingClient:
    model_fingerprint = ENGINE_FINGERPRINT
    dimension = 384

    def __init__(self, model_directory, *, custody=None, _worker_command=None,
                 _platform=None):
        self.directory = Path(model_directory)
        if not self.directory.is_absolute():
            raise EngineError("invalid_model")
        self._custody = custody or (lambda: ())
        self._worker_command = _worker_command
        self._platform = sys.platform if _platform is None else _platform
        self._lock = asyncio.Lock()
        self._process = None
        self._launch_task = None
        self._stop_task = None
        self._stderr_task = None
        self._temporary = None
        self._temporary_identity = None
        self._cancel_read = self._cancel_write = None
        self._nonce = None
        self._sequence = 0
        self._closing = False
        self._cleanup_pending = False
        self._stderr_tail = bytearray()
        self._stage = "idle"
        self._last_returncode = None

    def _diagnostic(self, error, stage):
        # Only exception metadata and fixed flags. Never retain exception text,
        # paths, model inputs, or raw native stderr in the diagnostic/receipt.
        name = "".join(char for char in type(error).__name__
                       if char.isascii() and (char.isalnum() or char == "_"))[:64]
        flags = []
        startup = bytes(self._stderr_tail).lower()
        for marker, flag in ((b"sandbox_apply", "sandbox_apply_failed"),
                             (b"operation not permitted", "os_permission_denied"),
                             (b"permission denied", "file_permission_denied"),
                             (b"modulenotfounderror", "module_not_found"),
                             (b"no module named 'jarvis_core'", "package_import_failed"),
                             (b"syntaxerror", "worker_syntax_error"),
                             (b"traceback", "python_startup_traceback")):
            if marker in startup:
                flags.append(flag)
        number = getattr(error, "errno", None)
        return {"stage": stage, "exception_type": name,
                "errno": number if type(number) is int and 0 < number < 4096 else None,
                "worker_returncode": self._last_returncode, "stderr_flags": flags}

    def _check(self, cancellation, deadline):
        if self._closing or (cancellation is not None and cancellation.is_set()):
            raise EngineError("cancelled")
        if time.monotonic() >= deadline:
            raise EngineError("timeout")
        if self._cleanup_pending:
            raise EngineError("cleanup_pending")

    async def _bounded(self, task, cancellation, deadline):
        while not task.done():
            self._check(cancellation, deadline)
            await asyncio.wait({task}, timeout=min(0.05, max(0, deadline - time.monotonic())))
        self._check(cancellation, deadline)
        return task.result()

    async def _acquire(self, cancellation, deadline):
        task = asyncio.create_task(self._lock.acquire())
        try:
            await self._bounded(task, cancellation, deadline)
        except BaseException:
            if task.done() and not task.cancelled() and task.exception() is None:
                self._lock.release()
            else:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            raise

    async def _read_frame(self):
        length = struct.unpack("!I", await self._process.stdout.readexactly(4))[0]
        if not 1 <= length <= FRAME_LIMIT:
            raise EngineError("capacity")
        raw = await self._process.stdout.readexactly(length)
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            raise EngineError("invalid_model") from None
        if not isinstance(value, dict) or value.get("nonce") != self._nonce:
            raise EngineError("invalid_model")
        if value.get("error") is not None:
            raise EngineError(value["error"])
        if value.get("model_fingerprint") != ENGINE_FINGERPRINT:
            raise EngineError("invalid_model")
        return value

    async def _drain_stderr(self, process):
        while block := await process.stderr.read(4096):
            self._stderr_tail.extend(block)
            if len(self._stderr_tail) > 16384:
                del self._stderr_tail[:-16384]

    async def _start(self, cancellation, deadline):
        if self._process is not None:
            if self._process.returncode is not None:
                await self._settle_cleanup()
            else:
                return
        self._stage = "temporary"
        temporary_parent = Path(tempfile.gettempdir()).resolve(strict=True)
        self._temporary = Path(tempfile.mkdtemp(prefix="jarvis-embedding-", dir=temporary_parent))
        self._temporary.chmod(0o700)
        info = self._temporary.lstat()
        self._temporary_identity = info.st_dev, info.st_ino
        self._nonce = uuid4().hex
        self._stderr_tail.clear()
        self._last_returncode = None
        self._stage = "control_pipe"
        self._cancel_read, self._cancel_write = os.pipe()
        python = Path(sys.executable)
        command = (list(self._worker_command) if self._worker_command else
                   [str(python), "-I", "-B", "-m", "jarvis_core.embedding_worker"])
        isolation = "ci_resource_only"
        if self._platform == "darwin":
            if not Path("/usr/bin/sandbox-exec").is_file():
                raise EngineError("unavailable_runtime")
            isolation = "seatbelt_readonly"
            command = ["/usr/bin/sandbox-exec", "-p",
                       _policy(self.directory, self._temporary, python), *command]
        elif os.environ.get("GITHUB_ACTIONS") != "true" and self._worker_command is None:
            # Linux is a remote qualification target, not a claimed desktop sandbox.
            raise EngineError("unavailable_runtime")
        command += ["--model", str(self.directory), "--nonce", self._nonce,
                    "--cancel-fd", str(self._cancel_read), "--isolation", isolation]
        environment = {"PATH": os.defpath, "LANG": "C.UTF-8", "HOME": str(self._temporary),
                       "TMPDIR": str(self._temporary) + "/", "PYTHONDONTWRITEBYTECODE": "1",
                       "PYTHONNOUSERSITE": "1", "HF_HUB_OFFLINE": "1",
                       "TRANSFORMERS_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false"}
        environment.update(dict.fromkeys(("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                                          "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
                                          "NUMEXPR_NUM_THREADS"), "1"))
        custody = tuple(self._custody())
        if any(type(fd) is not int or fd < 3 for fd in custody):
            raise EngineError("unavailable_runtime")
        self._stage = "launch"
        self._launch_task = asyncio.create_task(asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=environment, cwd=self._temporary,
            start_new_session=True, pass_fds=(*custody, self._cancel_read), limit=FRAME_LIMIT + 4))
        process = await self._bounded(self._launch_task, cancellation, deadline)
        self._process = process
        os.close(self._cancel_read)
        self._cancel_read = None
        self._stderr_task = asyncio.create_task(self._drain_stderr(process))
        self._stage = "startup_handshake"
        task = asyncio.create_task(self._read_frame())
        try:
            ready = await self._bounded(task, cancellation, deadline)
            if ready.get("id") != 0 or ready.get("ready") is not True:
                raise EngineError("invalid_model")
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def _shutdown_impl(self):
        process = self._process
        if self._launch_task is not None:
            try:
                process = await asyncio.wait_for(asyncio.shield(self._launch_task), 5)
                self._process = process
            except (TimeoutError, OSError):
                self._cleanup_pending = True
                raise EngineError("cleanup_pending") from None
        if self._cancel_read is not None:
            os.close(self._cancel_read)
            self._cancel_read = None
        if self._cancel_write is not None:
            with contextlib.suppress(BrokenPipeError):
                os.write(self._cancel_write, b"\1")
            os.close(self._cancel_write)
            self._cancel_write = None
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            for timeout, signum in ((1, None), (2, signal.SIGTERM), (2, signal.SIGKILL)):
                if process.returncode is not None:
                    break
                if signum is not None:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signum)
                try:
                    await asyncio.wait_for(process.wait(), timeout)
                except TimeoutError:
                    pass
            if process.returncode is None:
                self._cleanup_pending = True
                raise EngineError("cleanup_pending")
            self._last_returncode = process.returncode
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                pass
            else:
                self._cleanup_pending = True
                raise EngineError("cleanup_pending")
        if self._stderr_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._stderr_task), 1)
            except TimeoutError:
                self._cleanup_pending = True
                raise EngineError("cleanup_pending") from None
        if self._temporary is not None:
            info = self._temporary.lstat()
            if (not stat.S_ISDIR(info.st_mode) or self._temporary.is_symlink()
                    or (info.st_dev, info.st_ino) != self._temporary_identity):
                self._cleanup_pending = True
                raise EngineError("cleanup_pending")
            try:
                self._temporary.rmdir()  # Zero writes: never recursively delete unexpected files.
            except OSError:
                self._cleanup_pending = True
                raise EngineError("cleanup_pending") from None
        self._process = self._launch_task = self._stderr_task = None
        self._temporary = self._temporary_identity = None
        self._cleanup_pending = False
        return {"status": "closed", "closed": True, "cleanup_confirmed": True,
                "process_reaped": True, "temporary_removed": True,
                "disk_write_quota_bytes": 0}

    async def _shutdown(self):
        try:
            return await self._shutdown_impl()
        except EngineError:
            self._cleanup_pending = True
            raise
        except (OSError, ValueError, RuntimeError):
            self._cleanup_pending = True
            raise EngineError("cleanup_pending") from None

    async def _settle_cleanup(self):
        if self._stop_task is None or self._stop_task.done():
            self._stop_task = asyncio.create_task(self._shutdown())
        while True:
            try:
                return await asyncio.shield(self._stop_task)
            except asyncio.CancelledError:
                if self._stop_task.done():
                    return self._stop_task.result()

    async def _request(self, operation, text, cancellation, deadline):
        raw = _text_bytes(text)
        if deadline is None:
            deadline = time.monotonic() + 30
        if type(deadline) not in {int, float} or not math.isfinite(deadline):
            raise EngineError("timeout")
        deadline = min(deadline, time.monotonic() + 30)
        await self._acquire(cancellation, deadline)
        task = None
        try:
            self._check(cancellation, deadline)
            await self._start(cancellation, deadline)
            self._stage = "request"
            self._sequence += 1
            request = {"id": self._sequence, "nonce": self._nonce, "operation": operation,
                       "model_fingerprint": ENGINE_FINGERPRINT, "text": text,
                       "text_sha256": hashlib.sha256(raw).hexdigest()}
            body = json.dumps(request, ensure_ascii=False, allow_nan=False).encode()
            if len(body) > FRAME_LIMIT:
                raise EngineError("capacity")
            self._process.stdin.write(struct.pack("!I", len(body)) + body)
            task = asyncio.create_task(self._process.stdin.drain())
            await self._bounded(task, cancellation, deadline)
            task = asyncio.create_task(self._read_frame())
            value = await self._bounded(task, cancellation, deadline)
            if (value.get("id") != request["id"]
                    or value.get("text_sha256") != request["text_sha256"]
                    or value.get("complete") is not True
                    or ("retiring" in value and type(value["retiring"]) is not bool)):
                raise EngineError("invalid_model")
            if value.get("retiring") is True:
                await self._settle_cleanup()
            return value, raw
        except BaseException as error:
            stage = self._stage
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            try:
                await self._settle_cleanup()
            except EngineError as cleanup:
                diagnostic = self._diagnostic(error, stage)
                raise EngineError(cleanup.code, diagnostic=diagnostic) from None
            if isinstance(error, (EngineError, asyncio.CancelledError)):
                if isinstance(error, EngineError) and error.diagnostic is None:
                    error.diagnostic = self._diagnostic(error, stage)
                    error.add_note("engine_diagnostic=" + json.dumps(
                        error.diagnostic, sort_keys=True))
                raise
            raise EngineError("unavailable_runtime", diagnostic=self._diagnostic(
                error, stage)) from None
        finally:
            self._lock.release()

    async def embed_query(self, text, *, cancellation=None, deadline=None):
        if len(_text_bytes(text)) > 512:
            raise EngineError("capacity")
        value, _ = await self._request("query", text, cancellation, deadline)
        try:
            return _vector(value["vector_f32le"])
        except (KeyError, EngineError):
            self._cleanup_pending = True
            await self._settle_cleanup()
            raise EngineError("invalid_model") from None

    async def embed_document(self, text, *, cancellation=None, deadline=None):
        value, raw = await self._request("document", text, cancellation, deadline)
        try:
            rows = value["spans"]
            if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_CHUNKS:
                raise ValueError
            spans, covered = [], 0
            for row in rows:
                if not isinstance(row, dict) or set(row) != {
                    "start_byte", "end_byte", "token_count", "vector_f32le",
                }:
                    raise ValueError
                start, end, count = row["start_byte"], row["end_byte"], row["token_count"]
                if (any(type(number) is not int for number in (start, end, count))
                        or not 0 <= start < end <= len(raw) or start > covered
                        or end - start > MAX_SPAN_BYTES or end <= covered
                        or not 1 <= count <= 128):
                    raise ValueError
                raw[start:end].decode()
                spans.append(EncodedSpan(start, end, count, _vector(row["vector_f32le"])))
                covered = end
            if covered != len(raw):
                raise ValueError
            return tuple(spans)
        except (KeyError, ValueError, UnicodeError, EngineError):
            self._cleanup_pending = True
            await self._settle_cleanup()
            raise EngineError("invalid_model") from None

    async def close(self):
        self._closing = True
        return await self._settle_cleanup()
