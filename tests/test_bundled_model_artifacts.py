"""Tiny owned offline assets; no model download, inference or installed bundle."""

import hashlib
import os
import stat
import threading
import time
from types import SimpleNamespace

import pytest
from test_memory_intelligence import FixtureService, SyntheticEncoder

import jarvis_core.memory_intelligence as intelligence_module
from jarvis_core import model_artifacts as artifacts
from jarvis_core.memory_intelligence import MemoryIntelligence
from jarvis_core.model_artifacts import ArtifactError


@pytest.fixture
def bundled(tmp_path, monkeypatch):
    payloads = {
        "onnx/model_qint8_arm64.onnx": b"OWNED_NOT_AN_ONNX_MODEL",
        "tokenizer.json": b'{"owned":"not-a-real-tokenizer"}',
        "README.md": b"Own small protocol fixture.\n",
        "LICENSE.Apache-2.0.txt": b"OWNED SYNTHETIC LICENSE CONTENT\n",
    }
    spec = {"files": [
        {"path": name, "bytes": len(value), "sha256": hashlib.sha256(value).hexdigest(),
         "url": "https://example.invalid/never-contacted"}
        for name, value in payloads.items()
    ]}
    manifest = artifacts._canonical({"owned_fixture": spec}) + b"\n"
    monkeypatch.setattr(artifacts, "MODEL_SPEC", spec)
    monkeypatch.setattr(intelligence_module, "MODEL_SPEC", spec)
    monkeypatch.setattr(artifacts, "_MANIFEST", manifest)
    monkeypatch.setattr(artifacts.shutil, "disk_usage", lambda _: SimpleNamespace(free=2 * 1024**3))

    def network_forbidden(*_, **__):
        pytest.fail("bundled source must never construct or use a network transport")

    monkeypatch.setattr(artifacts.urllib.request, "build_opener", network_forbidden)
    monkeypatch.setattr(artifacts.urllib.request, "Request", network_forbidden)
    monkeypatch.setattr(artifacts, "_public_url", network_forbidden)
    source = tmp_path.resolve() / "public-model"
    source.mkdir(mode=0o755)
    (source / "onnx").mkdir(mode=0o755)
    for name, value in {**payloads, "manifest.json": manifest}.items():
        path = source / name
        path.write_bytes(value)
        path.chmod(0o644)
    return source, payloads


def test_public_bundle_copies_to_private_verified_cache_without_any_network(bundled, tmp_path):
    source, payloads = bundled
    destination = tmp_path.resolve() / "private-copy"
    before = {name: (source / name).read_bytes() for name in payloads}
    result = artifacts.prepare_model(destination, bundled_source=source,
                                     assert_current=lambda: None)
    assert result["ready"] and result["offline"]
    assert stat.S_IMODE(destination.stat().st_mode) == 0o700
    assert stat.S_IMODE((destination / "onnx").stat().st_mode) == 0o700
    for name, expected in payloads.items():
        assert stat.S_IMODE((destination / name).stat().st_mode) == 0o600
        assert artifacts.read_model_asset(destination, name) == expected
        assert (source / name).read_bytes() == before[name]
        assert stat.S_IMODE((source / name).stat().st_mode) == 0o644
    assert artifacts.inspect_model(destination)["ready"]
    # Accepting public installation resources never relaxes private cache APIs.
    with pytest.raises(ArtifactError, match="invalid_model"):
        artifacts.inspect_model(source)
    with pytest.raises(ArtifactError, match="invalid_model"):
        artifacts.read_model_asset(source, "tokenizer.json")


@pytest.mark.parametrize("failure", [
    "missing", "hash", "manifest", "symlink_file", "symlink_directory", "hardlink",
    "writable_file", "writable_directory", "symlink_root",
])
def test_unsafe_or_changed_public_source_fails_closed_without_fallback(
    bundled, tmp_path, failure,
):
    source, _ = bundled
    tokenizer = source / "tokenizer.json"
    selected = source
    if failure == "missing":
        tokenizer.unlink()
    elif failure == "hash":
        tokenizer.write_bytes(b"X" * tokenizer.stat().st_size)
    elif failure == "manifest":
        path = source / "manifest.json"
        path.write_bytes(b"X" * path.stat().st_size)
    elif failure == "symlink_file":
        saved = tmp_path / "saved-tokenizer"
        tokenizer.rename(saved)
        tokenizer.symlink_to(saved)
    elif failure == "symlink_directory":
        saved = tmp_path / "saved-onnx"
        (source / "onnx").rename(saved)
        (source / "onnx").symlink_to(saved, target_is_directory=True)
    elif failure == "hardlink":
        os.link(tokenizer, tmp_path / "extra-tokenizer-link")
    elif failure == "writable_file":
        tokenizer.chmod(0o664)
    elif failure == "writable_directory":
        source.chmod(0o775)
    else:
        selected = tmp_path / "linked-source"
        selected.symlink_to(source, target_is_directory=True)
    destination = tmp_path.resolve() / "not-published"
    with pytest.raises(ArtifactError, match="bundled_model_invalid") as error:
        artifacts.prepare_model(destination, bundled_source=selected, assert_current=lambda: None)
    assert not destination.exists()
    assert error.value.receipt["cleanup_confirmed"] is True
    assert not list(tmp_path.glob(".jarvis-model-acquire-*"))


def test_offline_copy_rechecks_all_source_identities_before_using_bytes(
    bundled, tmp_path, monkeypatch,
):
    source, payloads = bundled
    original_copy = artifacts._copy_response
    changed = False

    def replace_after_admission(response, output, row, cancellation, deadline):
        nonlocal changed
        if not changed:
            changed = True
            tokenizer = source / "tokenizer.json"
            tokenizer.rename(source / "original-tokenizer")
            tokenizer.write_bytes(payloads["tokenizer.json"])
            tokenizer.chmod(0o644)
        return original_copy(response, output, row, cancellation, deadline)

    monkeypatch.setattr(artifacts, "_copy_response", replace_after_admission)
    destination = tmp_path.resolve() / "not-published"
    with pytest.raises(ArtifactError, match="bundled_model_invalid"):
        artifacts.prepare_model(destination, bundled_source=source)
    assert not destination.exists()
    assert (source / "original-tokenizer").read_bytes() == payloads["tokenizer.json"]
    assert not list(tmp_path.glob(".jarvis-model-acquire-*"))


@pytest.mark.parametrize("mode", ["cancel", "revoke"])
def test_offline_copy_cancellation_and_guard_revocation_remove_only_private_staging(
    bundled, tmp_path, monkeypatch, mode,
):
    source, payloads = bundled
    cancellation, revoked = threading.Event(), threading.Event()
    original_copy = artifacts._copy_response

    def interrupt_after_copy(*args):
        original_copy(*args)
        (cancellation if mode == "cancel" else revoked).set()

    def current():
        if revoked.is_set():
            raise ArtifactError("cancelled")

    monkeypatch.setattr(artifacts, "_copy_response", interrupt_after_copy)
    destination = tmp_path.resolve() / "cancelled-copy"
    with pytest.raises(ArtifactError, match="cancelled") as error:
        artifacts.prepare_model(destination, bundled_source=source,
                                cancellation=cancellation, assert_current=current)
    assert error.value.receipt["status"] == "not_published"
    assert error.value.receipt["cleanup_confirmed"] is True
    assert not destination.exists() and not list(tmp_path.glob(".jarvis-model-acquire-*"))
    assert all((source / name).read_bytes() == value for name, value in payloads.items())


def test_offline_copy_keeps_deadline_and_capacity_limits(bundled, tmp_path, monkeypatch):
    source, _ = bundled
    for options, expected in [({"deadline": time.monotonic() - 1}, "timeout"), ({}, "capacity")]:
        if not options:
            monkeypatch.setattr(artifacts, "MAX_DOWNLOAD_BYTES", 1)
        with pytest.raises(ArtifactError, match=expected):
            artifacts.prepare_model(tmp_path.resolve() / expected, bundled_source=source, **options)
        assert not (tmp_path / expected).exists()


def test_offline_copy_never_writes_into_the_bundled_source(bundled):
    source, _ = bundled
    with pytest.raises(ArtifactError, match="bundled_model_invalid"):
        artifacts.prepare_model(source / "new-cache", bundled_source=source)
    assert not (source / "new-cache").exists()
    assert not list(source.glob(".jarvis-model-acquire-*"))


def test_offline_publication_uncertainty_preserves_the_published_copy(
    bundled, tmp_path, monkeypatch,
):
    source, _ = bundled

    def committed_then_failed(staging, destination):
        os.rename(staging, destination)
        raise OSError("own publication acknowledgement failed")

    monkeypatch.setattr(artifacts, "_publish_new", committed_then_failed)
    destination = tmp_path.resolve() / "uncertain-copy"
    with pytest.raises(ArtifactError, match="publication_unknown") as error:
        artifacts.prepare_model(destination, bundled_source=source)
    assert error.value.receipt["status"] == "unknown"
    assert error.value.receipt["automatic_retry"] is False
    assert artifacts.inspect_model(destination)["ready"]


def test_offline_repair_creates_new_private_generation_and_preserves_original(bundled, tmp_path):
    source, _ = bundled
    original, replacement = tmp_path.resolve() / "original", tmp_path.resolve() / "replacement"
    original.mkdir(mode=0o700)
    marker = original / "unknown-owned-content"
    marker.write_bytes(b"PRESERVE THIS ORIGINAL")
    result = artifacts.repair_model(original, replacement, assert_current=lambda: None,
                                    bundled_source=source)
    assert result["previous_untouched"] and result["activation_required"]
    assert marker.read_bytes() == b"PRESERVE THIS ORIGINAL"
    assert artifacts.inspect_model(replacement)["ready"]


async def test_memory_facade_default_preparation_and_repair_keep_same_offline_source(
    bundled, tmp_path,
):
    source, _ = bundled
    profile = tmp_path.resolve() / "profile"
    profile.mkdir(mode=0o700)
    service = FixtureService(profile)
    facade = MemoryIntelligence(service, bundled_model_source=source,
                                engine_factory=SyntheticEncoder)
    job = SimpleNamespace(cancellation=threading.Event())
    try:
        first = await facade._prepare_assets(job, lambda: None, time.monotonic() + 5)
        assert first["status"] == "completed" and not first["previous_untouched"]
        original = facade.model_directory
        tokenizer = original / "tokenizer.json"
        tokenizer.write_bytes(b"X" * tokenizer.stat().st_size)
        corrupted = tokenizer.read_bytes()
        second = await facade._prepare_assets(job, lambda: None, time.monotonic() + 5)
        assert second["status"] == "completed" and second["previous_untouched"]
        assert facade.model_directory != original
        assert tokenizer.read_bytes() == corrupted
        assert artifacts.inspect_model(facade.model_directory)["ready"]
    finally:
        await facade.close()
        service.memory.close()


async def test_memory_facade_preserves_explicit_custom_preparer_contract(bundled, tmp_path):
    source, _ = bundled
    profile = tmp_path.resolve() / "profile"
    profile.mkdir(mode=0o700)
    service, calls = FixtureService(profile), []

    def custom(directory, *, cancellation, deadline, assert_current):
        calls.append(directory)
        return artifacts.prepare_model(directory, cancellation=cancellation, deadline=deadline,
                                       assert_current=assert_current, bundled_source=source)

    facade = MemoryIntelligence(service, model_preparer=custom,
                                bundled_model_source=tmp_path / "unused-source",
                                engine_factory=SyntheticEncoder)
    try:
        result = await facade._prepare_assets(SimpleNamespace(cancellation=threading.Event()),
                                              lambda: None, time.monotonic() + 5)
        assert result["status"] == "completed"
        assert calls == [facade.model_directory]
    finally:
        await facade.close()
        service.memory.close()
