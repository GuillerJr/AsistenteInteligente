"""Offline MiniLM CPU arithmetic. Called only by LocalEmbeddingClient."""

from __future__ import annotations

import argparse
import base64
import bisect
import contextlib
import hashlib
import importlib.metadata
import json
import os
import resource
import struct
import sys
import threading

from jarvis_core.local_embeddings import (
    ENGINE_FINGERPRINT,
    FRAME_LIMIT,
    MAX_CHUNKS,
    MAX_SPAN_BYTES,
    MAX_TEXT_BYTES,
    RUNTIME_VERSIONS,
)
from jarvis_core.model_artifacts import ArtifactError, inspect_model, read_model_asset


class _WorkerError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _spans(text, tokenizer, checkpoint):
    """Cover original UTF-8 bytes; every independently encoded slice must fit."""
    raw = text.encode()
    if not text.strip() or "\0" in text or len(raw) > MAX_TEXT_BYTES:
        raise _WorkerError("capacity")
    checkpoint()
    specials = tokenizer.num_special_tokens_to_add(False)
    if type(specials) is not int or not 1 <= specials < 16:
        raise _WorkerError("invalid_model")
    positions = [0]
    for number, character in enumerate(text):
        if number % 256 == 0:
            checkpoint()
        positions.append(positions[-1] + len(character.encode()))
    result, start, covered = [], 0, 0
    while start < len(text):
        checkpoint()
        if len(result) >= MAX_CHUNKS:
            raise _WorkerError("capacity")
        end = bisect.bisect_right(positions, positions[start] + MAX_SPAN_BYTES) - 1
        while end > start:
            checkpoint()
            chunk = text[start:end]
            encoded = tokenizer.encode(chunk, add_special_tokens=True)
            offsets, previous = [], 0
            for begin, finish in encoded.offsets:
                # Added special tokens carry (0,0), unlike original source tokens.
                if begin == finish == 0:
                    continue
                if (type(begin) is not int or type(finish) is not int
                        or not 0 <= begin < finish <= len(chunk) or begin < previous):
                    raise _WorkerError("invalid_model")
                previous = begin
                offsets.append((begin, finish))
            if 1 <= len(encoded.ids) <= 128:
                break
            allowed = min(len(offsets), 128 - specials)
            cutoff = offsets[allowed - 1][1] if allowed else 0
            end = start + (cutoff if 0 < cutoff < end - start else (end - start) // 2)
        else:
            raise _WorkerError("capacity")
        begin_byte, end_byte = positions[start], positions[end]
        if begin_byte > covered or end_byte <= covered:
            raise _WorkerError("invalid_model")
        result.append((begin_byte, end_byte, len(encoded.ids), chunk))
        covered = end_byte
        if end == len(text):
            break
        overlap = offsets[max(0, len(offsets) - 16)][0] if offsets else 0
        start = start + overlap if overlap > 0 else end
    if covered != len(raw):
        raise _WorkerError("invalid_model")
    return result


class _Engine:
    def __init__(self, directory, cancelled, phase=lambda value: None):
        self.cancelled = cancelled
        self._run_options = None
        self._options_lock = threading.Lock()
        phase("asset_verification")
        inspect_model(directory, cancellation=cancelled)
        phase("dependency_versions")
        if any(importlib.metadata.version(name) != version
               for name, version in RUNTIME_VERSIONS.items()):
            raise _WorkerError("unavailable_runtime")
        # Only this isolated child imports the selected native dependencies.
        # The API below runs too late to suppress ORT's initialization telemetry.
        if os.environ.get("ORT_DISABLE_TELEMETRY") != "1":
            raise _WorkerError("unavailable_runtime")
        phase("native_import")
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.np, self.ort = np, ort
        ort.disable_telemetry_events()
        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
        options.enable_profiling = False
        options.optimized_model_filepath = ""
        phase("tokenizer_initialization")
        tokenizer_bytes = read_model_asset(directory, "tokenizer.json", cancellation=cancelled)
        self.tokenizer = Tokenizer.from_str(tokenizer_bytes.decode())
        del tokenizer_bytes
        self.tokenizer.no_padding()
        self.tokenizer.no_truncation()
        self.pad = self.tokenizer.token_to_id("<pad>")
        if type(self.pad) is not int or self.pad < 0:
            raise _WorkerError("invalid_model")
        phase("session_initialization")
        model_bytes = read_model_asset(directory, "onnx/model_qint8_arm64.onnx",
                                       cancellation=cancelled)
        self.session = ort.InferenceSession(model_bytes, sess_options=options,
                                           providers=["CPUExecutionProvider"])
        del model_bytes
        self.session.disable_fallback()
        if self.session.get_providers() != ["CPUExecutionProvider"]:
            raise _WorkerError("invalid_model")
        self.inputs = {row.name: row.type for row in self.session.get_inputs()}
        if (not {"input_ids", "attention_mask"} <= set(self.inputs)
                or set(self.inputs) - {"input_ids", "attention_mask", "token_type_ids"}
                or set(self.inputs.values()) != {"tensor(int64)"}
                or "last_hidden_state" not in {row.name for row in self.session.get_outputs()}):
            raise _WorkerError("invalid_model")

    def checkpoint(self):
        if self.cancelled.is_set():
            raise _WorkerError("cancelled")

    def cancel(self):
        self.cancelled.set()
        with self._options_lock:
            if self._run_options is not None:
                self._run_options.terminate = True

    def _batch(self, chunks):
        self.checkpoint()
        np = self.np
        encoded = self.tokenizer.encode_batch([row[3] for row in chunks],
                                              add_special_tokens=True)
        if any(len(row.ids) != chunk[2] or not 1 <= len(row.ids) <= 128
               for row, chunk in zip(encoded, chunks, strict=True)):
            raise _WorkerError("invalid_model")
        length = max(len(row.ids) for row in encoded)
        ids = np.full((len(chunks), length), self.pad, dtype=np.int64)
        mask = np.zeros_like(ids)
        types = np.zeros_like(ids)
        for index, row in enumerate(encoded):
            ids[index, :len(row.ids)] = row.ids
            mask[index, :len(row.ids)] = row.attention_mask
            types[index, :len(row.ids)] = row.type_ids
        inputs = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.inputs:
            inputs["token_type_ids"] = types
        options = self.ort.RunOptions()
        with self._options_lock:
            self._run_options = options
            options.terminate = self.cancelled.is_set()
        try:
            hidden = self.session.run(["last_hidden_state"], inputs, options)[0]
        finally:
            with self._options_lock:
                self._run_options = None
        self.checkpoint()
        hidden = np.asarray(hidden, dtype=np.float32)
        if hidden.shape != (len(chunks), length, 384) or not np.isfinite(hidden).all():
            raise _WorkerError("invalid_model")
        weights = mask.astype(np.float32)
        counts = weights.sum(axis=1)
        if (counts <= 0).any():
            raise _WorkerError("invalid_model")
        pooled = (hidden * weights[:, :, None]).sum(axis=1) / counts[:, None]
        norms = np.linalg.norm(pooled, axis=1)
        if not np.isfinite(norms).all() or (norms <= 0).any():
            raise _WorkerError("invalid_model")
        return pooled / norms[:, None]

    def encode(self, text, operation):
        if operation == "query":
            self.checkpoint()
            if not text.strip() or "\0" in text or len(text.encode()) > 512:
                raise _WorkerError("capacity")
            count = len(self.tokenizer.encode(text, add_special_tokens=True).ids)
            if not 1 <= count <= 128:
                raise _WorkerError("capacity")
            vector = self._batch([(0, len(text.encode()), count, text)])[0]
            return {"vector_f32le": base64.b64encode(vector.astype("<f4").tobytes()).decode()}
        spans = _spans(text, self.tokenizer, self.checkpoint)
        vectors = []
        for begin in range(0, len(spans), 8):
            vectors.extend(self._batch(spans[begin:begin + 8]))
        self.checkpoint()
        return {"spans": [
            {"start_byte": row[0], "end_byte": row[1], "token_count": row[2],
             "vector_f32le": base64.b64encode(vector.astype("<f4").tobytes()).decode()}
            for row, vector in zip(spans, vectors, strict=True)]}


def _read_frame():
    raw = sys.stdin.buffer.read(4)
    if not raw:
        return None
    if len(raw) != 4:
        raise _WorkerError("capacity")
    length = struct.unpack("!I", raw)[0]
    if not 1 <= length <= FRAME_LIMIT:
        raise _WorkerError("capacity")
    raw = sys.stdin.buffer.read(length)
    if len(raw) != length:
        raise _WorkerError("capacity")
    return json.loads(raw)


def _emit(value, nonce):
    value.update(nonce=nonce, model_fingerprint=ENGINE_FINGERPRINT)
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > FRAME_LIMIT:
        raise _WorkerError("capacity")
    sys.stdout.buffer.write(struct.pack("!I", len(raw)) + raw)
    sys.stdout.buffer.flush()


def _failure_metadata(error, stage):
    name = "".join(char for char in type(error).__name__
                   if char.isascii() and (char.isalnum() or char == "_"))[:64]
    number = getattr(error, "errno", None)
    return {"stage": stage, "exception_type": name,
            "errno": number if type(number) is int and 0 < number < 4096 else None}


def main():
    from pathlib import Path

    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--cancel-fd", type=int, required=True)
    parser.add_argument("--isolation", choices=("seatbelt_readonly", "ci_resource_only"),
                        required=True)
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 61))
    cancelled = threading.Event()
    engine = None
    stage = "asset_verification"

    def phase(value):
        nonlocal stage
        stage = value

    def control():
        try:
            os.read(args.cancel_fd, 1)
        finally:
            cancelled.set()
            if engine is not None:
                engine.cancel()

    listener = threading.Thread(target=control, daemon=True)
    listener.start()
    try:
        engine = _Engine(args.model, cancelled, phase)
        engine.checkpoint()
        phase("ready")
        _emit({"id": 0, "ready": True, "dimension": 384, "provider": "CPUExecutionProvider",
               "isolation": args.isolation, "disk_write_quota_bytes": 0}, args.nonce)
        previous = 0
        while not cancelled.is_set():
            request = _read_frame()
            if request is None:
                break
            if (not isinstance(request, dict) or set(request) != {
                    "id", "nonce", "operation", "model_fingerprint", "text", "text_sha256"}
                    or type(request["id"]) is not int or request["id"] <= previous
                    or request["nonce"] != args.nonce
                    or request["model_fingerprint"] != ENGINE_FINGERPRINT
                    or request["operation"] not in {"query", "document"}
                    or not isinstance(request["text"], str)
                    or hashlib.sha256(request["text"].encode()).hexdigest()
                    != request["text_sha256"]):
                raise _WorkerError("invalid_model")
            previous = request["id"]
            phase("encode")
            result = engine.encode(request["text"], request["operation"])
            usage = resource.getrusage(resource.RUSAGE_SELF)
            retiring = usage.ru_utime + usage.ru_stime >= 25
            _emit({"id": previous, "text_sha256": request["text_sha256"], "complete": True,
                   "retiring": retiring, **result}, args.nonce)
            if retiring:
                # Bound process lifetime without killing a large corpus at the
                # same cumulative CPU point on every rebuild. No request replay.
                return 0
        return 0
    except (ArtifactError, _WorkerError) as error:
        _emit({"error": error.code, "diagnostic": _failure_metadata(error, stage)}, args.nonce)
        return 1
    except (ImportError, importlib.metadata.PackageNotFoundError) as error:
        _emit({"error": "unavailable_runtime",
               "diagnostic": _failure_metadata(error, stage)}, args.nonce)
        return 1
    except Exception as error:
        # Never print a traceback, source text, paths or native environment values.
        _emit({"error": "cancelled" if cancelled.is_set() else "invalid_model",
               "diagnostic": _failure_metadata(error, stage)}, args.nonce)
        return 1
    finally:
        cancelled.set()
        if engine is not None:
            engine.cancel()
        with contextlib.suppress(OSError):
            os.close(args.cancel_fd)


if __name__ == "__main__":
    raise SystemExit(main())
