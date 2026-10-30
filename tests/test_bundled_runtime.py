"""Inert artificial runtime trees; no interpreter/client is ever executed."""

import hashlib
import json
import os
import shutil
import struct
from pathlib import Path

import pytest

from jarvis_core.bundled_runtime import (
    CA_CERTIFICATES_PATH,
    ENTRYPOINTS,
    MANIFEST_NAME,
    MODEL_ENTRYPOINT,
    SCHEMA,
    BundledRuntimeError,
    parse_manifest,
    verify_runtime_tree,
)

COMMIT = "4" * 40
MACHO = struct.pack("<IIII", 0xFEEDFACF, 0x0100000C, 0, 2) + b"inert fixture"


def encode(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return raw, hashlib.sha256(raw).hexdigest()


def fixture(root: Path, *, model=False):
    root.mkdir(mode=0o755)
    content = {
        ENTRYPOINTS["python"]: MACHO, ENTRYPOINTS["codex"]: MACHO,
        "core/jarvis_core/__init__.py": b"# own inert core\n",
        "site-packages/fixture.py": b"raise AssertionError('must not import')\n",
        CA_CERTIFICATES_PATH: b"synthetic CA metadata, not a real trust store",
        "licenses/python.txt": b"synthetic Python license fixture",
        "licenses/codex.txt": b"synthetic Codex license fixture",
        "licenses/jarvis.txt": b"synthetic own license fixture",
        "python/lib/_CodeSignature/CodeResources": b"included nested signing resource",
    }
    if model:
        content.update({MODEL_ENTRYPOINT + "/" + p: b"synthetic model asset" for p in (
            "manifest.json", "tokenizer.json", "onnx/model_qint8_arm64.onnx",
            "LICENSE.Apache-2.0.txt",
        )})
    files = []
    for name, payload in sorted(content.items()):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        path.write_bytes(payload)
        executable = name in (ENTRYPOINTS["python"], ENTRYPOINTS["codex"])
        path.chmod(0o755 if executable else 0o644)
        files.append({"path": name, "bytes": len(payload),
                      "sha256": hashlib.sha256(payload).hexdigest(), "executable": executable})
    value = {
        "schema": SCHEMA, "source_commit": COMMIT, "target": "aarch64-apple-darwin",
        "minimum_macos": "14.0", "python_version": "3.11.18", "codex_version": "0.163.0",
        "entrypoints": {**ENTRYPOINTS, **({"model": MODEL_ENTRYPOINT} if model else {})},
        "files": files, "components": [
            {"id": name, "version": "fixture-1", "origin": "https://example.com/fixture",
             "archive_sha256": "a" * 64, "license_paths": ["licenses/" + license_name]}
            for name, license_name in (("python", "python.txt"), ("codex", "codex.txt"),
                                       ("jarvis-core", "jarvis.txt"))
        ],
    }
    raw, pin = encode(value)
    (root / MANIFEST_NAME).write_bytes(raw)
    (root / MANIFEST_NAME).chmod(0o644)
    return value, raw, pin


def test_verification_is_relocatable_read_only_and_never_executes(tmp_path):
    root = tmp_path.resolve() / "first"
    _, _, pin = fixture(root, model=True)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    verified = verify_runtime_tree(root, expected_manifest_sha256=pin,
                                   expected_source_commit=COMMIT)
    moved = tmp_path.resolve() / "another location.app"
    shutil.move(root, moved)
    relocated = verify_runtime_tree(moved, expected_manifest_sha256=pin)
    assert relocated.path("python") == moved / ENTRYPOINTS["python"]
    assert relocated.path("model") == moved / MODEL_ENTRYPOINT
    assert relocated.bytes == verified.bytes and relocated.files == verified.files
    assert {p.relative_to(moved): p.read_bytes() for p in moved.rglob("*") if p.is_file()} == before
    assert not list(moved.rglob("__pycache__"))


@pytest.mark.parametrize("mutation", ["tamper", "missing", "extra", "empty_directory", "mode",
                                      "inner_signature"])
def test_any_missing_changed_extra_or_repermissioned_resource_fails(tmp_path, mutation):
    root = tmp_path.resolve() / "runtime"
    _, _, pin = fixture(root)
    target = root / "site-packages/fixture.py"
    if mutation == "tamper":
        target.write_bytes(b"different")
    elif mutation == "missing":
        target.unlink()
    elif mutation == "extra":
        (root / "unlisted.txt").write_text("unlisted")
    elif mutation == "empty_directory":
        (root / "unlisted-directory").mkdir()
    elif mutation == "mode":
        target.chmod(0o755)
    else:
        (root / "python/lib/_CodeSignature/CodeResources").write_bytes(b"changed")
    with pytest.raises(BundledRuntimeError):
        verify_runtime_tree(root, expected_manifest_sha256=pin)


@pytest.mark.parametrize("kind", ["symlink_file", "symlink_directory", "hardlink", "fifo"])
def test_links_and_special_files_cannot_escape_or_join_the_runtime(tmp_path, kind):
    root = tmp_path.resolve() / "runtime"
    _, _, pin = fixture(root)
    target = root / "site-packages/fixture.py"
    if kind == "symlink_file":
        target.unlink()
        target.symlink_to(tmp_path / "not-read")
    elif kind == "symlink_directory":
        shutil.rmtree(root / "site-packages")
        (root / "site-packages").symlink_to(tmp_path, target_is_directory=True)
    elif kind == "hardlink":
        os.link(target, tmp_path / "other-name")
    else:
        target.unlink()
        os.mkfifo(target)
    with pytest.raises(BundledRuntimeError):
        verify_runtime_tree(root, expected_manifest_sha256=pin)


@pytest.mark.parametrize("name", ["/outside", "../outside", "a/../b", "a//b", "./file",
                                  "a\\b", "a/", "a\x00b", "a\nb", "bad\ud800"])
def test_manifest_paths_are_literal_bounded_relative_names(tmp_path, name):
    value, _, _ = fixture(tmp_path.resolve() / "runtime")
    value["files"][0]["path"] = name
    # JSON can spell a lone surrogate as an ASCII escape. The parser must
    # reject that decoded name, rather than the fixture failing to encode it.
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True,
                     separators=(",", ":")).encode()
    pin = hashlib.sha256(raw).hexdigest()
    with pytest.raises(BundledRuntimeError):
        parse_manifest(raw, expected_sha256=pin)


def test_manifest_pin_commit_duplicates_and_case_aliases_are_rejected(tmp_path):
    value, raw, pin = fixture(tmp_path.resolve() / "runtime")
    with pytest.raises(BundledRuntimeError, match="runtime_manifest_hash"):
        parse_manifest(raw, expected_sha256="0" * 64)
    with pytest.raises(BundledRuntimeError, match="runtime_source_commit"):
        parse_manifest(raw, expected_sha256=pin, expected_source_commit="8" * 40)
    duplicate = b'{"schema":1,"schema":2}'
    with pytest.raises(BundledRuntimeError):
        parse_manifest(duplicate, expected_sha256=hashlib.sha256(duplicate).hexdigest())
    value["files"].append({**value["files"][0], "path": value["files"][0]["path"].upper()})
    raw, pin = encode(value)
    with pytest.raises(BundledRuntimeError):
        parse_manifest(raw, expected_sha256=pin)


@pytest.mark.parametrize("mutation", ["license", "license_shape", "empty_license", "empty_ca",
                                      "origin_credentials", "origin_invalid", "mode_both_changed",
                                      "wrong_architecture", "non_executable", "model_incomplete"])
def test_unusable_or_unlicensed_runtime_never_returns_an_operational_plan(tmp_path, mutation):
    root = tmp_path.resolve() / "runtime"
    value, _, _ = fixture(root, model=True)
    if mutation == "license":
        value["components"][0]["license_paths"] = ["unlisted.txt"]
    elif mutation == "license_shape":
        value["components"][0]["license_paths"] = [{}]
    elif mutation == "origin_credentials":
        value["components"][0]["origin"] = "https://secret@example.com/resource"
    elif mutation == "origin_invalid":
        value["components"][0]["origin"] = "https://[invalid/resource"
    elif mutation in {"empty_license", "empty_ca", "mode_both_changed"}:
        name = ("licenses/python.txt" if mutation == "empty_license" else
                CA_CERTIFICATES_PATH if mutation == "empty_ca" else ENTRYPOINTS["python"])
        row = next(r for r in value["files"] if r["path"] == name)
        if mutation == "mode_both_changed":
            row["executable"] = False
            (root / name).chmod(0o644)
        else:
            (root / name).write_bytes(b"")
            row.update(bytes=0, sha256=hashlib.sha256(b"").hexdigest())
    elif mutation in {"wrong_architecture", "non_executable"}:
        binary = b"#!/bin/sh\nexit 0" if mutation == "non_executable" else struct.pack(
            "<IIII", 0xFEEDFACF, 0x01000007, 0, 2)
        (root / ENTRYPOINTS["python"]).write_bytes(binary)
        row = next(r for r in value["files"] if r["path"] == ENTRYPOINTS["python"])
        row.update(bytes=len(binary), sha256=hashlib.sha256(binary).hexdigest())
    else:
        value["files"] = [r for r in value["files"] if not r["path"].endswith(".onnx")]
    raw, pin = encode(value)
    (root / MANIFEST_NAME).write_bytes(raw)
    with pytest.raises(BundledRuntimeError):
        verify_runtime_tree(root, expected_manifest_sha256=pin)


def test_absent_runtime_returns_a_stable_failure_without_discovery(tmp_path):
    with pytest.raises(BundledRuntimeError, match="runtime_tree_unavailable"):
        verify_runtime_tree(tmp_path.resolve() / "missing", expected_manifest_sha256="a" * 64)


def test_noncanonical_json_is_not_an_alternate_manifest_representation(tmp_path):
    _, raw, _ = fixture(tmp_path.resolve() / "runtime")
    alternative = raw + b"\n"
    with pytest.raises(BundledRuntimeError, match="runtime_manifest_noncanonical"):
        parse_manifest(alternative, expected_sha256=hashlib.sha256(alternative).hexdigest())


def test_resource_read_rechecks_manifest_source_hash_and_limit(tmp_path):
    root = tmp_path.resolve() / "runtime"
    _, _, pin = fixture(root)
    runtime = verify_runtime_tree(root, expected_manifest_sha256=pin)
    assert runtime.read_file(CA_CERTIFICATES_PATH, limit=512 * 1024).startswith(b"synthetic CA")
    with pytest.raises(BundledRuntimeError, match="runtime_read_limit"):
        runtime.read_file(CA_CERTIFICATES_PATH, limit=1)
    with pytest.raises(BundledRuntimeError):
        runtime.read_file("../outside", limit=100)
    (root / CA_CERTIFICATES_PATH).write_bytes(b"replaced trust")
    with pytest.raises(BundledRuntimeError, match="runtime_file_hash"):
        runtime.read_file(CA_CERTIFICATES_PATH, limit=512 * 1024)


def test_resource_read_does_not_trust_a_manifest_replaced_after_launch(tmp_path):
    root = tmp_path.resolve() / "runtime"
    _, _, pin = fixture(root)
    runtime = verify_runtime_tree(root, expected_manifest_sha256=pin)
    (root / MANIFEST_NAME).write_bytes(b"{}")
    with pytest.raises(BundledRuntimeError, match="runtime_manifest_hash"):
        runtime.read_file(CA_CERTIFICATES_PATH, limit=512 * 1024)
