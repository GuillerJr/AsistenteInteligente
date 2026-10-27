"""Owned protocol fixtures plus an explicit, real Spanish semantic qualification."""

import asyncio
import base64
import hashlib
import io
import json
import math
import os
import struct
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis_core import model_artifacts as artifacts
from jarvis_core.embedding_worker import _failure_metadata, _spans
from jarvis_core.local_embeddings import (
    _WORKER_BOOTSTRAP,
    ENGINE_FINGERPRINT,
    ENGINE_SPEC,
    RUNTIME_VERSIONS,
    EncodedSpan,
    EngineError,
    LocalEmbeddingClient,
    _policy,
    _temporary_metadata,
    _vector,
    _worker_environment,
    _worker_import_roots,
    _worker_interpreter,
)
from jarvis_core.model_artifacts import ArtifactError, _copy_response, _public_url, inspect_model


def test_manifest_absence_never_downloads_or_opens_extra_files(tmp_path, monkeypatch):
    monkeypatch.setattr("urllib.request.OpenerDirector.open",
                        lambda *args, **kwargs: pytest.fail("inspection downloaded an asset"))
    with pytest.raises(ArtifactError, match="missing_model"):
        inspect_model(tmp_path / "absent")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("url", [
    "http://huggingface.co/model", "https://127.0.0.1/model",
    "https://huggingface.co@localhost/model", "https://huggingface.co:444/model",
    "file:///private/tmp/model", "https://example.com/model",
])
def test_artifact_urls_are_only_official_https(url):
    with pytest.raises(ArtifactError, match="download_rejected"):
        _public_url(url)


def test_download_enforces_length_hash_and_no_content_compression(tmp_path):
    payload = b"owned tiny fixture"
    row = {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}

    def response(data, headers):
        value = io.BytesIO(data)
        value.headers = headers
        return value

    with (tmp_path / "body").open("wb") as output:
        _copy_response(response(payload, {"Content-Length": str(len(payload))}), output,
                       row, None, time.monotonic() + 2)
    assert (tmp_path / "body").read_bytes() == payload
    for body, headers in ((payload + b"extra", {}), (b"wrong", {}),
                          (payload, {"Content-Encoding": "gzip"})):
        with (tmp_path / "rejected").open("wb") as output, pytest.raises(ArtifactError):
            _copy_response(response(body, headers), output, row, None, time.monotonic() + 2)


def test_model_publication_rechecks_host_guard_and_preserves_unknown_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts.shutil, "disk_usage",
                        lambda path: SimpleNamespace(free=2 * 1024**3))
    monkeypatch.setattr(artifacts, "MODEL_SPEC", {"files": []})
    monkeypatch.setattr(artifacts, "_MANIFEST", b"owned tiny protocol fixture")
    monkeypatch.setattr(artifacts, "inspect_model", lambda *args, **kwargs: {"ready": True})
    calls = 0

    def revoked():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ArtifactError("cancelled")

    with pytest.raises(ArtifactError, match="cancelled") as cancelled:
        artifacts.prepare_model(tmp_path / "cancelled", assert_current=revoked)
    assert cancelled.value.receipt["cleanup_confirmed"]
    assert not (tmp_path / "cancelled").exists()

    def published_then_failed(source, target):
        os.rename(source, target)
        raise OSError("synthetic failure after atomic publication")

    monkeypatch.setattr(artifacts, "_publish_new", published_then_failed)
    with pytest.raises(ArtifactError, match="publication_unknown") as error:
        artifacts.prepare_model(tmp_path / "published", assert_current=lambda: None)
    assert error.value.receipt["inspection_available"]
    assert (tmp_path / "published/manifest.json").read_bytes() == b"owned tiny protocol fixture"


def test_model_staging_cleanup_failure_keeps_slot_and_reports_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts.shutil, "disk_usage",
                        lambda path: SimpleNamespace(free=2 * 1024**3))
    monkeypatch.setattr(artifacts, "MODEL_SPEC", {"files": []})
    monkeypatch.setattr(artifacts, "_MANIFEST", b"owned tiny protocol fixture")
    monkeypatch.setattr(artifacts, "inspect_model", lambda *args, **kwargs: {"ready": True})
    calls = 0

    def revoked():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ArtifactError("cancelled")

    def cannot_remove(*args, **kwargs):
        raise OSError("synthetic owned cleanup failure")

    with monkeypatch.context() as patch:
        patch.setattr(artifacts.shutil, "rmtree", cannot_remove)
        with pytest.raises(ArtifactError, match="cleanup_pending") as error:
            artifacts.prepare_model(tmp_path / "cancelled", assert_current=revoked)
    assert error.value.receipt["cleanup_confirmed"] is False
    assert not (tmp_path / "cancelled").exists()


def test_model_repair_keeps_unknown_original_untouched(tmp_path, monkeypatch):
    original = tmp_path / "original"
    original.mkdir(mode=0o700)
    (original / "unknown.txt").write_text("owned synthetic marker")

    def prepare(directory, **kwargs):
        kwargs["assert_current"]()
        directory.mkdir(mode=0o700)
        info = directory.stat()
        return {"ready": True, "bytes": 1, "root_identity": [info.st_dev, info.st_ino]}

    monkeypatch.setattr(artifacts, "prepare_model", prepare)
    result = artifacts.repair_model(original, tmp_path / "replacement", assert_current=lambda: None)
    assert result["previous_untouched"] and result["activation_required"]
    assert (original / "unknown.txt").read_text() == "owned synthetic marker"


@pytest.fixture
def owned_cache_copy(tmp_path):
    root = tmp_path / "public-copy"
    root.mkdir(mode=0o700)
    (root / "onnx").mkdir(mode=0o700)
    for name in [row["path"] for row in artifacts.MODEL_SPEC["files"]] + ["manifest.json"]:
        path = root / name
        path.write_bytes(b"owned synthetic corrupt public artifact")
        path.chmod(0o600)
    info = root.stat()
    identity = info.st_dev, info.st_ino
    record = {"format": "jarvis-model-copy-ownership/1", "profile_binding": "a" * 64,
              "slot_id": "b" * 32, "root_identity": list(identity),
              "asset_fingerprint": artifacts.MODEL_FINGERPRINT, "source": "managed_preparation"}
    return root, identity, record


def test_retire_requires_known_manifest_or_historical_managed_ownership(owned_cache_copy):
    root, identity, record = owned_cache_copy
    with pytest.raises(ArtifactError, match="invalid_model"):
        artifacts.retire_model_copy(root, expected_root_identity=identity,
                                    assert_current=lambda: None)
    assert (root / "manifest.json").exists()
    result = artifacts.retire_model_copy(root, expected_root_identity=identity,
                                        assert_current=lambda: None, ownership_record=record)
    assert result["retired"] and result["files"] == 5 and not root.exists()


def test_retire_never_unlinks_unknown_entry_even_with_managed_record(owned_cache_copy):
    root, identity, record = owned_cache_copy
    extra = root / "unknown.txt"
    extra.write_text("synthetic unrelated bytes")
    with pytest.raises(ArtifactError, match="invalid_model"):
        artifacts.retire_model_copy(root, expected_root_identity=identity,
                                    assert_current=lambda: None, ownership_record=record)
    assert extra.read_text() == "synthetic unrelated bytes"
    assert (root / "onnx/model_qint8_arm64.onnx").exists()


def test_partial_retirement_is_unknown_and_can_resume_only_under_same_record(
    owned_cache_copy, monkeypatch,
):
    root, identity, record = owned_cache_copy
    unlink = os.unlink
    calls = 0

    def interrupted(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("owned fixture unlink interrupted")
        return unlink(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(artifacts.os, "unlink", interrupted)
        with pytest.raises(ArtifactError, match="retirement_unknown") as error:
            artifacts.retire_model_copy(root, expected_root_identity=identity,
                                        assert_current=lambda: None, ownership_record=record)
    assert error.value.receipt["files"] == 1 and root.exists()
    assert (root / "manifest.json").exists()
    result = artifacts.retire_model_copy(root, expected_root_identity=identity,
                                        assert_current=lambda: None, ownership_record=record)
    assert result["root_removed"] and not root.exists()


class CharacterTokenizer:
    """Protocol fixture, deliberately not evidence of model tokenization or quality."""
    @staticmethod
    def num_special_tokens_to_add(pair):
        assert pair is False
        return 2

    @staticmethod
    def encode(text, *, add_special_tokens):
        return SimpleNamespace(ids=[1] * (len(text) + (2 if add_special_tokens else 0)),
                               offsets=[(index, index + 1) for index in range(len(text))])


def test_span_protocol_covers_late_utf8_and_never_silently_truncates():
    text = "á🙂 " * 100
    raw = text.encode()
    spans = _spans(text, CharacterTokenizer(), lambda: None)
    assert len(spans) > 1 and spans[0][0] == 0 and spans[-1][1] == len(raw)
    covered = 0
    for start, end, count, chunk in spans:
        assert start <= covered < end and 1 <= count <= 128
        assert raw[start:end].decode() == chunk
        covered = end


def test_span_work_checks_cancel_before_tokenization():
    def cancelled():
        raise EngineError("cancelled")

    with pytest.raises(EngineError, match="cancelled"):
        _spans("texto propio", CharacterTokenizer(), cancelled)


def test_span_byte_budget_covers_long_whitespace_instead_of_dropping_it():
    class SparseTokenizer(CharacterTokenizer):
        @staticmethod
        def encode(text, *, add_special_tokens):
            offsets = [(i, i + 1) for i, char in enumerate(text) if not char.isspace()]
            return SimpleNamespace(ids=[1] * (len(offsets) + (2 if add_special_tokens else 0)),
                                   offsets=offsets)

    text = " " * 2000 + "nota á🙂" + " " * 1800
    raw = text.encode()
    spans = _spans(text, SparseTokenizer(), lambda: None)
    covered = 0
    for start, end, count, chunk in spans:
        assert start <= covered < end and end - start <= 768 and count <= 128
        assert raw[start:end].decode() == chunk
        covered = end
    assert covered == len(raw)


def test_vector_wire_rejects_nonfinite_short_and_nonunit_vectors():
    for values in ([1.0] * 383, [float("nan")] + [0.0] * 383, [0.0] * 384):
        raw = struct.pack("<" + "f" * len(values), *values)
        with pytest.raises(EngineError, match="invalid_model"):
            _vector(base64.b64encode(raw).decode())
    raw = struct.pack("<384f", 1, *([0] * 383))
    assert _vector(base64.b64encode(raw).decode())[0] == 1


def test_worker_policy_has_no_writes_network_or_fork(tmp_path):
    policy = _policy(tmp_path, tmp_path, type(tmp_path)(sys.executable))
    assert "(deny file-write* network*)" in policy and "(deny process-fork)" in policy
    assert "auth.json" not in policy


def test_startup_diagnostics_never_contain_exception_text_or_raw_stderr(tmp_path):
    client = LocalEmbeddingClient(tmp_path)
    client._stderr_tail.extend(b"SYNTHETIC_PRIVATE_TEXT Operation not permitted")
    diagnostic = client._diagnostic(PermissionError(1, "SYNTHETIC_PRIVATE_TEXT"), "temporary")
    assert diagnostic["errno"] == 1 and diagnostic["exception_type"] == "PermissionError"
    assert diagnostic["stderr_flags"] == ["os_permission_denied"]
    assert "SYNTHETIC_PRIVATE_TEXT" not in str(diagnostic)


def test_environment_disables_native_telemetry_before_import(tmp_path, monkeypatch):
    monkeypatch.setenv("ORT_DISABLE_TELEMETRY", "0")
    monkeypatch.setenv("PYTHONPATH", "SYNTHETIC_UNTRUSTED_PATH")
    environment = _worker_environment(tmp_path)
    assert environment["ORT_DISABLE_TELEMETRY"] == "1"
    assert environment["HF_HUB_DISABLE_TELEMETRY"] == "1"
    assert "PYTHONPATH" not in environment
    assert ENGINE_SPEC["runtime_versions"] == RUNTIME_VERSIONS
    assert ENGINE_SPEC["intra_op_threads"] == ENGINE_SPEC["inter_op_threads"] == 1
    assert ENGINE_SPEC["provider"] == "CPUExecutionProvider"
    assert ENGINE_SPEC["telemetry"] == "ORT_DISABLE_TELEMETRY=1-before-import"


def test_worker_bootstrap_uses_explicit_roots_without_executing_pth(tmp_path, monkeypatch):
    source, dependencies = tmp_path / "fuente propia", tmp_path / "dependencias"
    source.mkdir()
    dependencies.mkdir()
    (dependencies / "hostile.pth").write_text("raise AssertionError('site hook executed')")
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "argv", ["-c", json.dumps([str(source), str(dependencies)]),
                                     "--model", "fixture"])
    calls = []
    monkeypatch.setattr("runpy.run_module", lambda *args, **kwargs: calls.append((
        args, kwargs, sys.path[:2], sys.argv[1:])))
    exec(_WORKER_BOOTSTRAP, {})
    assert calls == [(("jarvis_core.embedding_worker",),
                      {"run_name": "__main__", "alter_sys": True},
                      [str(source), str(dependencies)], ["--model", "fixture"])]


def test_import_roots_resolve_installed_distributions_without_importing_sdk(tmp_path, monkeypatch):
    root = tmp_path / "physical-dependencies"
    root.mkdir()
    for name in RUNTIME_VERSIONS:
        (root / name).mkdir()
        (root / name / "__init__.py").write_text("raise AssertionError('SDK imported in parent')")

    def distribution(name):
        return SimpleNamespace(version=RUNTIME_VERSIONS[name],
                               locate_file=lambda relative: root / relative)

    monkeypatch.setattr("importlib.metadata.distribution", distribution)
    roots = _worker_import_roots()
    assert roots[-1] == root.resolve() and len(roots) == 2
    assert roots[0].name == "src"
    monkeypatch.setattr("importlib.metadata.distribution", lambda name: SimpleNamespace(
        version="0.0.0", locate_file=lambda relative: root / relative))
    with pytest.raises(EngineError, match="unavailable_runtime"):
        _worker_import_roots()


def test_framework_interpreter_bypasses_only_its_own_launcher(tmp_path, monkeypatch):
    base = tmp_path / "Python.framework/Versions/3.11"
    base.mkdir(parents=True)
    launcher = base / "bin/python3.11"
    launcher.parent.mkdir()
    launcher.write_text("owned launcher fixture; never executed")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "base_prefix", str(base))
    monkeypatch.setattr(sys, "executable", str(launcher))
    # A missing app does not discover an unrelated Python or relax exec rules.
    assert _worker_interpreter() == launcher.resolve()
    actual = base / "Resources/Python.app/Contents/MacOS/Python"
    actual.parent.mkdir(parents=True)
    actual.write_text("owned interpreter fixture; never executed")
    actual.chmod(0o700)
    assert _worker_interpreter() == actual.resolve()
    policy = _policy(tmp_path, tmp_path, _worker_interpreter())
    exec_rule = next(row for row in policy.splitlines() if "(allow process-exec" in row)
    assert str(actual.resolve()) in exec_rule and str(launcher.resolve()) not in exec_rule
    assert "(deny process-fork)" in policy
    monkeypatch.setattr(sys, "platform", "linux")
    assert _worker_interpreter() == launcher.resolve()


def test_framework_interpreter_rejects_app_symlink_outside_framework(tmp_path, monkeypatch):
    base = tmp_path / "Python.framework/Versions/3.11"
    actual = base / "Resources/Python.app/Contents/MacOS/Python"
    actual.parent.mkdir(parents=True)
    unrelated = tmp_path / "unrelated-python"
    unrelated.write_text("owned fixture; never executed")
    unrelated.chmod(0o700)
    actual.symlink_to(unrelated)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "base_prefix", str(base))
    monkeypatch.setattr(sys, "executable", str(unrelated))
    with pytest.raises(EngineError, match="unavailable_runtime"):
        _worker_interpreter()


def test_cleanup_metadata_is_bounded_and_never_reads_bodies_or_exposes_names(tmp_path):
    for number in range(20):
        (tmp_path / f"SYNTHETIC_PRIVATE_NAME_{number}").mkdir()
    info = tmp_path.lstat()
    diagnostic = _temporary_metadata(tmp_path, (info.st_dev, info.st_ino))
    assert diagnostic["truncated"] and len(diagnostic["entries"]) == 16
    assert all(row["kind"] == "directory" for row in diagnostic["entries"])
    assert "SYNTHETIC_PRIVATE_NAME" not in str(diagnostic)
    assert len(list(tmp_path.iterdir())) == 20


def test_worker_error_metadata_excludes_exception_messages():
    diagnostic = _failure_metadata(PermissionError(13, "SYNTHETIC_PRIVATE_TEXT"), "native_import")
    assert diagnostic == {"stage": "native_import", "exception_type": "PermissionError",
                          "errno": 13}
    assert "SYNTHETIC_PRIVATE_TEXT" not in str(diagnostic)


_STUB = r'''
import argparse, base64, hashlib, json, os, struct, sys, time
p=argparse.ArgumentParser()
p.add_argument('--model'); p.add_argument('--nonce'); p.add_argument('--cancel-fd')
p.add_argument('--isolation'); a=p.parse_args()
fingerprint = FINGERPRINT
def emit(value):
    value.update(nonce=a.nonce,model_fingerprint=fingerprint)
    raw=json.dumps(value).encode();sys.stdout.buffer.write(struct.pack('!I',len(raw))+raw)
    sys.stdout.buffer.flush()
emit({'id':0,'ready':True})
while True:
    header=sys.stdin.buffer.read(4)
    if not header: break
    body=json.loads(sys.stdin.buffer.read(struct.unpack('!I',header)[0]))
    if body['text']=='sleep': time.sleep(30)
    vector=base64.b64encode(struct.pack('<384f',1,*([0]*383))).decode()
    result={'id':body['id'],'complete':True,'text_sha256':body['text_sha256']}
    if body['operation']=='query': result['vector_f32le']=vector
    else:
        result['spans']=[{'start_byte':0,'end_byte':len(body['text'].encode()),
                         'token_count':4,'vector_f32le':vector}]
    if body['text']=='retire': result['retiring']=True
    emit(result)
    if body['text']=='retire': break
'''


@pytest.fixture
def protocol_client(tmp_path):
    # Owned tiny child outside the production command. No native dependencies/models.
    script = tmp_path / "stub.py"
    script.write_text(_STUB.replace("FINGERPRINT", repr(ENGINE_FINGERPRINT)))
    return LocalEmbeddingClient(tmp_path, _worker_command=[sys.executable, "-I", "-B", str(script)],
                                _platform="fixture")


async def test_protocol_cancellation_reaps_and_removes_only_own_empty_tmp(protocol_client):
    with pytest.raises(EngineError, match="timeout"):
        await protocol_client.embed_query("sleep", deadline=time.monotonic() + 0.2)
    assert protocol_client._process is None and protocol_client._temporary is None
    result = await protocol_client.embed_query("consulta sintética")
    assert len(result) == 384
    receipt = await protocol_client.close()
    assert (receipt["process_reaped"] and receipt["temporary_removed"]
            and receipt["closed"] and receipt["cleanup_confirmed"])


async def test_protocol_document_and_idempotent_close(protocol_client):
    result = await protocol_client.embed_document("área propia")
    assert protocol_client._temporary.parent == Path(tempfile.gettempdir()).resolve()
    assert result == (EncodedSpan(0, len("área propia".encode()), 4, (1.0, *([0.0] * 383))),)
    assert (await protocol_client.close())["status"] == "closed"


async def test_retirement_reaps_before_return_and_next_request_starts_same_engine(protocol_client):
    fingerprint = protocol_client.model_fingerprint
    first = await protocol_client.embed_query("retire")
    retired_nonce = protocol_client._nonce
    assert protocol_client._process is None and protocol_client._temporary is None
    second = await protocol_client.embed_query("otra consulta")
    assert first == second and protocol_client.model_fingerprint == fingerprint
    assert protocol_client._nonce != retired_nonce
    assert (await protocol_client.close())["process_reaped"]
    assert (await protocol_client.close())["status"] == "closed"


async def test_pre_cancelled_request_never_starts_worker(protocol_client):
    cancellation = asyncio.Event()
    cancellation.set()
    with pytest.raises(EngineError, match="cancelled"):
        await protocol_client.embed_query("consulta", cancellation=cancellation)
    assert protocol_client._process is None and protocol_client._temporary is None
    await protocol_client.close()


async def test_oversized_query_is_rejected_before_worker(protocol_client):
    with pytest.raises(EngineError, match="capacity"):
        await protocol_client.embed_query("a" * 513)
    assert protocol_client._process is None
    await protocol_client.close()


async def test_real_spanish_semantic_qualification():
    directory = os.environ.get("JARVIS_MINILM_TEST_ASSETS")
    if directory is None:
        if os.environ.get("JARVIS_MINILM_REQUIRE_REAL") == "1":
            pytest.fail("Pinned MiniLM assets are required for real semantic qualification.")
        pytest.skip("Real MiniLM qualification requires explicitly provisioned pinned assets.")
    client = LocalEmbeddingClient(directory)
    pairs = (
        ("La batería del portátil se reemplaza cuando deja de retener carga.",
         "¿Cómo renovar el acumulador de mi computadora móvil?"),
        ("El hotel que elegimos debe ser silencioso y permitir descansar por la noche.",
         "Busco alojamiento tranquilo para dormir sin ruido."),
        ("La veterinaria atiende animales enfermos y administra sus vacunas.",
         "¿Quién puede curar a una mascota que tiene fiebre?"),
        ("El paraguas está junto a la puerta para los días de lluvia.",
         "¿Dónde guardamos la protección contra el aguacero?"),
        ("La contraseña se cambia desde el panel de seguridad de la cuenta.",
         "Necesito renovar mi clave de acceso al perfil."),
        ("El pan recién horneado se conserva mejor dentro de una bolsa de tela.",
         "¿Cómo mantener fresco un alimento de harina hecho en el horno?"),
    )
    try:
        vectors = [(await client.embed_document(note))[0].vector for note, _ in pairs]
        correct = 0
        for index, (_, query) in enumerate(pairs):
            vector = await client.embed_query(query)
            scores = [math.fsum(a * b for a, b in zip(vector, other, strict=True))
                      for other in vectors]
            correct += max(range(len(scores)), key=scores.__getitem__) == index
        assert correct >= 5, f"real Spanish paraphrase top1={correct}/{len(pairs)}"
        long_note = "Información de relleno sobre mobiliario. " * 80 + pairs[2][0]
        spans = await client.embed_document(long_note)
        assert len(spans) > 1 and spans[-1].end_byte == len(long_note.encode())
        assert all(span.token_count <= 128 and span.end_byte - span.start_byte <= 768
                   for span in spans)
        query = await client.embed_query(pairs[2][1])
        scores = [math.fsum(a * b for a, b in zip(query, span.vector, strict=True))
                  for span in spans]
        relevant = spans[max(range(len(scores)), key=scores.__getitem__)]
        assert relevant.end_byte > len(long_note[:-len(pairs[2][0])].encode())
    finally:
        assert (await client.close())["process_reaped"]
