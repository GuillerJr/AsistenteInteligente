"""Independent adversarial checks of the one-owned-staging inventory exception."""

import os
import stat
import threading
from dataclasses import replace
from uuid import uuid4

import pytest
from test_activation import prepared as prepared
from test_channel_reconciliation import host as host
from test_channel_reconciliation import registry, review, submit
from test_memory_management import decide, finish
from test_recovery import bundle as bundle

from jarvis_core.cold_sqlite import ColdSqliteError
from jarvis_core.host_profile import HostProfileError, freeze_sources
from jarvis_core.private_json import PrivateJsonStaging
from jarvis_core.protocol import RpcError


def staging(service):
    document = service._telegram_journal._resource.document
    name = ".registry-" + uuid4().hex
    path = document.directory / name
    path.write_bytes(b"{}")
    path.chmod(0o600)
    info = path.stat()
    identity = (info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode), info.st_uid,
                info.st_nlink, info.st_size)
    return path, PrivateJsonStaging(document, name, identity)


async def test_only_exact_owned_staging_permits_the_same_complete_frozen_comparison(host):
    service, _, provider = host
    with service.profile_gate.exclusive() as cut:
        frozen = freeze_sources(service, cut)
        path, pin = staging(service)
        try:
            with pytest.raises(HostProfileError, match="profile_inventory_unclassified"):
                frozen.assert_current(cut, full=True)
            frozen.assert_json_staging_current(cut, pin)
            assert provider.calls == 0
        finally:
            path.unlink()


@pytest.mark.parametrize("change", ["document_proxy", "name", "identity", "size", "link",
                                    "second_temporary", "unclassified_root"])
async def test_staging_exception_neither_allows_other_names_nor_borrows_a_pin(host, change):
    service, _, _ = host
    with service.profile_gate.exclusive() as cut:
        frozen = freeze_sources(service, cut)
        path, pin = staging(service)
        extras = []
        try:
            if change == "document_proxy":
                pin = replace(pin, document=service._telegram_journal.document)
            elif change == "name":
                pin = replace(pin, name="telegram.json")
            elif change == "identity":
                pin = replace(pin, identity=(pin.identity[0], pin.identity[1] + 1,
                                              *pin.identity[2:]))
            elif change == "size":
                path.write_bytes(b"NOT THE PINNED BYTES")
            elif change == "link":
                other = service.directory / "other-stage"
                other.write_bytes(b"{}")
                other.chmod(0o600)
                extras.append(other)
                path.unlink()
                path.symlink_to(other)
            else:
                extra = (path.parent / (".registry-" + uuid4().hex)
                         if change == "second_temporary" else service.directory / "unknown")
                extra.write_bytes(b"{}")
                extra.chmod(0o600)
                extras.append(extra)
            with pytest.raises(HostProfileError):
                frozen.assert_json_staging_current(cut, pin)
        finally:
            path.unlink()
            for extra in extras:
                extra.unlink()


@pytest.mark.parametrize("change", ["session", "agent", "configuration", "cold_memory"])
async def test_all_frozen_siblings_are_revalidated_while_the_exact_owned_temporary_exists(
    host, change,
):
    service, _, provider = host
    with service.profile_gate.exclusive() as cut:
        frozen = freeze_sources(service, cut)
        path, pin = staging(service)
        try:
            if change == "session":
                service.store._resource._disk.execute("UPDATE sessions SET archived=1")
            elif change == "agent":
                service.agent_profiles._resource.document.update(lambda value: {
                    **value, "agents": {key: {**row, "revision": row["revision"] + 1}
                                        for key, row in value["agents"].items()}})
            elif change == "configuration":
                original = service.config.path.stat()
                os.utime(service.config.path, ns=(original.st_atime_ns,
                                                  original.st_mtime_ns + 1))
            else:
                original = service.directory / "memory.sqlite3"
                info = original.stat()
                os.utime(original, ns=(info.st_atime_ns, info.st_mtime_ns + 1))
            with pytest.raises((HostProfileError, ColdSqliteError),
                               match=r"profile_sources_changed|family_changed"):
                frozen.assert_json_staging_current(cut, pin)
            assert provider.calls == 0
        finally:
            path.unlink()


@pytest.mark.parametrize("failure", ["unlink_before_publish", "lock_close_after_publish",
                                     "parent_close_after_publish"])
async def test_exact_json_cleanup_owner_and_profile_cut_survive_until_cleanup_settles(
    host, monkeypatch, failure,
):
    import jarvis_core.private_json as primitive

    service, coordinator, provider = host
    document = service._telegram_journal._resource.document
    original = registry(service)
    prepared = await review(coordinator)
    injected = threading.Event()
    allow_cleanup = False
    capture = {}
    real_write = primitive.PrivateJsonDocument._write_temporary
    real_unlink, real_close, real_replace = os.unlink, os.close, os.replace
    publications = []

    def write(actor, parent, data):
        name = real_write(actor, parent, data)
        if actor is document:
            capture["owner"] = actor._reviewed_owner
            capture["name"] = name
            if failure == "unlink_before_publish":
                operation = coordinator._operations[capture["operation_id"]]
                operation.cancellation.set()
        return name

    def unlink(name, *args, **kwargs):
        owner = capture.get("owner")
        if (failure == "unlink_before_publish" and owner is not None
                and not allow_cleanup and name == capture.get("name")
                and kwargs.get("dir_fd") == owner.parent):
            injected.set()
            raise OSError("own fixture unlink denied")
        return real_unlink(name, *args, **kwargs)

    def replace_file(source, destination, *args, **kwargs):
        owner = capture.get("owner")
        result = real_replace(source, destination, *args, **kwargs)
        if owner is not None and destination == "telegram.json" and kwargs.get(
            "dst_dir_fd") == owner.parent:
            publications.append((source, destination))
        return result

    def close(descriptor):
        owner = capture.get("owner")
        target = (owner.lock if failure == "lock_close_after_publish" else owner.parent
                  if failure == "parent_close_after_publish" else None) if owner else None
        if target == descriptor and publications and not allow_cleanup:
            injected.set()
            raise OSError("own fixture close denied")
        return real_close(descriptor)

    monkeypatch.setattr(primitive.PrivateJsonDocument, "_write_temporary", write)
    monkeypatch.setattr(primitive.os, "unlink", unlink)
    monkeypatch.setattr(primitive.os, "close", close)
    monkeypatch.setattr(primitive.os, "replace", replace_file)
    try:
        operation = await submit(coordinator, prepared)
        capture["operation_id"] = operation["operation_id"]
        await decide(coordinator)
        result = await finish(coordinator, operation)
        assert injected.is_set() and result["phase"] == "cleanup_pending", result
        assert result["cleanup_owner_pending"] and result["receipt"]["status"] == "unknown"
        old_receipt = result["receipt"].copy()
        live = coordinator._operations[result["operation_id"]]
        held_cut, wrapper = service.profile_gate._cut, live.cleanup_owner
        owner = capture["owner"]
        assert wrapper.cut is held_cut and not owner.closed
        with pytest.raises(RpcError):
            await service.handlers()["memory.list"]({})
        repeated = await finish(coordinator, await coordinator.cleanup({
            "operation_id": result["operation_id"]}))
        assert repeated["phase"] == "cleanup_pending" and repeated["receipt"] == old_receipt
        assert service.profile_gate._cut is held_cut and live.cleanup_owner is wrapper
        allow_cleanup = True
        settled = await finish(coordinator, await coordinator.cleanup({
            "operation_id": result["operation_id"]}))
        assert settled["phase"] == "publication_unknown" and not settled["cleanup_owner_pending"]
        assert settled["receipt"] == old_receipt and service.profile_gate._cut is None
        assert wrapper.released and owner.closed
        assert len(publications) == int(failure != "unlink_before_publish")
        expected = original.copy()
        if failure != "unlink_before_publish":
            expected["routes"] = {**expected["routes"], "42": {
                **expected["routes"]["42"], "enabled": True, "revision": 2}}
        assert registry(service) == expected and provider.calls == 0
    finally:
        allow_cleanup = True
        for operation in coordinator._operations.values():
            if operation.cleanup_owner is not None:
                await finish(coordinator, await coordinator.cleanup({
                    "operation_id": operation.operation_id}))
