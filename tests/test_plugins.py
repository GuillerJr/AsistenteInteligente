"""Own tiny extension archives and host decisions, without programs or accounts."""

import base64
import hashlib
import io
import json
import os
import stat
import tarfile
import threading
import time
import zipfile
from dataclasses import replace

import pytest

from jarvis_core.plugin_packages import PluginError, inspect_package
from jarvis_core.plugins import PluginsStore
from jarvis_core.private_json import PrivateJsonError


def package(tmp_path, *, version="1.0.0", mutate=None, kind="zip", extra=None):
    files = {
        "skills/example/SKILL.md": (
            "---\nname: Ejemplo\ndescription: Habilidad sintética\nversion: " + version
            + '\nresources: ["references/data.txt"]\n---\nExplica con un ejemplo propio.\n'
        ).encode(),
        "skills/example/references/data.txt": b"RESOURCE SYNTHETIC 4682",
        "prompts/start.txt": "Contexto sintético de inicio".encode(),
    }
    manifest = {
        "format": "jarvis-extension/1", "package_id": "jarvis.synthetic",
        "name": "Extensión sintética", "description": "Datos propios de prueba",
        "version": version, "publisher": "Pruebas Jarvis",
        "source_uri": "https://example.com/jarvis-synthetic",
        "files": [{"path": path, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                   "media_type": "text/markdown" if path.endswith(".md") else "text/plain"}
                  for path, raw in files.items()],
        "skills": [{"skill_id": "example", "path": "skills/example/SKILL.md"}],
        "prompts": [{"prompt_id": "start", "path": "prompts/start.txt"}],
        "hooks": [{"event": "before_turn", "prompt_id": "start"},
                  {"event": "enable", "mcp_server_id": "synthetic", "tool": "echo",
                   "arguments": {"text": "SYNTHETIC HOOK"}}],
        "mcp": [{"server_id": "synthetic", "transport": "stdio", "command": ["/bin/echo"],
                 "protocol_version": "2025-11-25"}],
    }
    if mutate:
        mutate(manifest, files)
    entries = {"jarvis-extension.json": json.dumps(manifest, ensure_ascii=False).encode(), **files}
    if extra:
        entries.update(extra)
    path = tmp_path / ("package-" + version + (".zip" if kind == "zip" else ".tar"))
    if kind == "zip":
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, raw in entries.items():
                archive.writestr(name, raw)
    else:
        with tarfile.open(path, "w", format=tarfile.USTAR_FORMAT) as archive:
            for name, raw in entries.items():
                row = tarfile.TarInfo(name)
                row.size, row.mode = len(raw), 0o600
                archive.addfile(row, io.BytesIO(raw))
    path.chmod(0o600)
    return path


@pytest.fixture
def store(tmp_path):
    return PluginsStore(tmp_path.resolve() / "profile")


def install(store, path, agent_id="jarvis"):
    preview = inspect_package(path)
    review = store.review("install", agent_id, package=preview)
    receipt = store.apply(review, assert_current=lambda: None)
    return store.get(agent_id, receipt["plugin_id"])


def change(store, row, action, **options):
    review = store.review(action, row.agent_id, plugin_id=row.plugin_id,
                          expected_revision=row.revision, expected_sha256=row.sha256, **options)
    return store.apply(review, assert_current=lambda: None)


@pytest.mark.parametrize("kind", ["zip", "tar"])
def test_package_snapshot_pins_all_bytes_and_never_extracts(tmp_path, kind):
    path = package(tmp_path, kind=kind)
    before = set(tmp_path.iterdir())
    preview = inspect_package(path)
    assert preview.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert preview.public()["source_verified"] is False
    assert len(preview.files) == 3 and preview.archive_bytes == path.stat().st_size
    assert set(tmp_path.iterdir()) == before
    assert b"RESOURCE SYNTHETIC" in base64.b64decode(dict(preview.files)[
        "skills/example/references/data.txt"])


def test_install_enable_context_resources_and_mcp_are_independent_host_effects(store, tmp_path):
    row = install(store, package(tmp_path))
    assert not row.enabled and row.revision == 1 and row.mcp_count == 1
    assert store.context("jarvis")["text"] == ""
    assert store.contributions("other")["plugins"] == []
    assert stat.S_IMODE(store.document.path.stat().st_mode) == 0o600
    receipt = change(store, row, "enable")
    assert receipt["status"] == "committed"
    assert not receipt["executed_package_code"] and not receipt["connected_mcp"]
    assert receipt["lifecycle"]["hooks"][0]["mcp_server_id"] == "synthetic"
    current = store.get("jarvis", row.plugin_id)
    context = store.context("jarvis")
    assert "Explica con un ejemplo" in context["text"]
    assert "Contexto sintético" in context["text"] and "nunca permisos" in context["text"]
    contribution = store.contributions("jarvis")["plugins"][0]
    assert contribution["mcp"][0]["command"] == ["/bin/echo"]
    resource = store.resource("jarvis", row.plugin_id, "skills/example/references/data.txt",
                              expected_revision=current.revision, expected_sha256=current.sha256)
    assert base64.b64decode(resource["content_base64"]) == b"RESOURCE SYNTHETIC 4682"
    restarted = PluginsStore(store.document.directory)
    assert restarted.context("jarvis") == context
    assert restarted.observe_lifecycle("jarvis", "before_turn")["hooks"][0]["prompt_id"] == "start"


def test_versions_rollback_history_retirement_and_remove_preserve_cas(store, tmp_path):
    row = install(store, package(tmp_path))
    for number in (2, 3):
        next_package = inspect_package(package(tmp_path, version=f"{number}.0.0"))
        change(store, row, "update", package=next_package)
        row = store.get("jarvis", row.plugin_id)
        assert not row.enabled and row.version == f"{number}.0.0"
    assert len(store.versions("jarvis", row.plugin_id)) == 3
    with pytest.raises(PluginError, match="plugin_history_capacity"):
        change(store, row, "update", package=inspect_package(package(tmp_path, version="4.0.0")))
    change(store, row, "rollback", target_version="1.0.0")
    row = store.get("jarvis", row.plugin_id)
    assert row.version == "1.0.0" and not row.enabled
    change(store, row, "retire_history", target_version="2.0.0")
    row = store.get("jarvis", row.plugin_id)
    change(store, row, "update", package=inspect_package(package(tmp_path, version="4.0.0")))
    row = store.get("jarvis", row.plugin_id)
    assert row.version == "4.0.0"
    change(store, row, "remove")
    assert store.list("jarvis") == ()


def test_changed_review_configuration_source_and_agent_fail_before_effect(store, tmp_path):
    path = package(tmp_path)
    preview = inspect_package(path)
    review = store.review("install", "jarvis", package=preview)
    forged = replace(review, package=replace(preview, source_path="/tmp/other-source.zip"))
    with pytest.raises(PluginError, match="plugin_review_expired_or_changed"):
        store.apply(forged, assert_current=lambda: None)
    row = install(store, path, "another")
    with pytest.raises(PluginError, match="plugin_configuration_changed"):
        store.apply(review, assert_current=lambda: None)
    with pytest.raises(PluginError, match="plugin_not_found"):
        store.get("jarvis", row.plugin_id)
    assert len(store.list("another")) == 1 and not store.list("jarvis")


def test_cancel_and_final_guard_reject_before_publication_without_auto_retry(store, tmp_path):
    review = store.review("install", "jarvis", package=inspect_package(package(tmp_path)))
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(PluginError, match="plugin_cancelled"):
        store.apply(review, assert_current=lambda: None, cancellation=cancelled)
    assert not store.document.path.exists()
    cancelled.clear()
    calls = 0

    def revoked():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise PluginError("plugin_revoked")

    with pytest.raises(PluginError, match="plugin_revoked") as failed:
        store.apply(review, assert_current=revoked)
    assert failed.value.receipt["status"] == "not_published"
    assert failed.value.receipt["cleanup_confirmed"]
    assert store.list("jarvis") == ()
    with pytest.raises(PluginError, match="plugin_review_expired_or_changed"):
        store.apply(review, assert_current=lambda: None)


def test_lost_durability_ack_is_unknown_and_not_replayed(store, tmp_path, monkeypatch):
    review = store.review("install", "jarvis", package=inspect_package(package(tmp_path)))

    def lost(change, **options):
        raise PrivateJsonError("registry_durability_uncertain")

    monkeypatch.setattr(store.document, "update", lost)
    with pytest.raises(PluginError) as caught:
        store.apply(review, assert_current=lambda: None)
    assert caught.value.receipt["status"] == "publication_unknown"
    with pytest.raises(PluginError, match="plugin_review_expired_or_changed"):
        store.apply(review, assert_current=lambda: None)


@pytest.mark.parametrize("path", ["../secret", "/absolute", "a/../b", "a\\b", ".env",
                                  "a/auth.json", "a//b", "C:/owner", "unlisted.txt"])
def test_archive_traversal_private_and_unknown_entries_are_rejected(tmp_path, path):
    with pytest.raises(PluginError):
        inspect_package(package(tmp_path, extra={path: b"never use"}))


@pytest.mark.parametrize("defect", ["file_hash", "missing_file", "unknown_key", "duplicate_id",
                                    "resource_missing", "shell", "remote_credentials",
                                    "hook_unknown", "mcp_url_userinfo", "mcp_loopback_grant"])
def test_manifest_invalid_contributions_and_secret_fields_fail_closed(tmp_path, defect):
    def mutate(manifest, files):
        if defect == "file_hash":
            manifest["files"][0]["sha256"] = "0" * 64
        elif defect == "missing_file":
            del files[manifest["files"][0]["path"]]
        elif defect == "unknown_key":
            manifest["execute"] = "arbitrary code"
        elif defect == "duplicate_id":
            manifest["skills"].append(manifest["skills"][0])
        elif defect == "resource_missing":
            manifest["skills"][0]["path"] = "missing/SKILL.md"
        elif defect == "shell":
            manifest["mcp"][0]["command"] = ["/bin/sh", "-c", "never execute"]
        elif defect == "remote_credentials":
            manifest["mcp"][0]["token"] = "SYNTHETIC NOT A SECRET"
        elif defect == "hook_unknown":
            manifest["hooks"][0]["event"] = "unknown_hook"
        else:
            manifest["mcp"][0] = {"server_id": "synthetic", "transport": "streamable_http",
                                   "protocol_version": "2025-11-25",
                                   "endpoint": "https://example.com/mcp", "allow_loopback": False}
            if defect == "mcp_url_userinfo":
                manifest["mcp"][0]["endpoint"] = "https://credential@example.com/mcp"
            else:
                manifest["mcp"][0]["allow_loopback"] = True

    with pytest.raises(PluginError):
        inspect_package(package(tmp_path, mutate=mutate))


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "writable"])
def test_source_alias_special_and_writable_files_never_opened_as_packages(tmp_path, kind):
    path = package(tmp_path)
    if kind == "symlink":
        alias = tmp_path / "alias.zip"
        alias.symlink_to(path)
        path = alias
    elif kind == "hardlink":
        os.link(path, tmp_path / "another.zip")
    elif kind == "fifo":
        path.unlink()
        os.mkfifo(path)
    else:
        path.chmod(0o666)
    with pytest.raises(PluginError):
        inspect_package(path)


def test_zip_symlinks_are_rejected(tmp_path):
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as archive:
        entry = zipfile.ZipInfo("link")
        entry.create_system, entry.external_attr = 3, (stat.S_IFLNK | 0o600) << 16
        archive.writestr(entry, b"outside")
    with pytest.raises(PluginError):
        inspect_package(path)


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_tar_link_and_special_members_never_become_package_data(tmp_path, kind):
    path = tmp_path / "unsafe.tar"
    with tarfile.open(path, "w", format=tarfile.USTAR_FORMAT) as archive:
        entry = tarfile.TarInfo("references/data.txt")
        entry.type, entry.linkname, entry.mode = kind, "outside", 0o600
        archive.addfile(entry)
    with pytest.raises(PluginError):
        inspect_package(path)


def test_duplicate_and_case_colliding_archive_paths_fail_closed(tmp_path):
    path = package(tmp_path)
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("SKILLS/example/SKILL.md", b"case alias")
    with pytest.raises(PluginError):
        inspect_package(path)


def test_compression_bombs_and_compressed_tar_are_rejected(tmp_path):
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("bomb.txt", b"a" * 65536)
    with pytest.raises(PluginError):
        inspect_package(path)
    with tarfile.open(path, "w:gz") as archive:
        entry = tarfile.TarInfo("bomb.txt")
        entry.size = 65536
        archive.addfile(entry, io.BytesIO(b"a" * entry.size))
    with pytest.raises(PluginError):
        inspect_package(path)


def test_package_parse_deadline_never_mutates_or_contacts_any_service(tmp_path):
    path = package(tmp_path)
    before = path.read_bytes()
    with pytest.raises(PluginError, match="plugin_deadline"):
        inspect_package(path, deadline=time.monotonic() - 1)
    assert path.read_bytes() == before
