"""Real service/cut/authority release integration with synthetic local artifacts."""

import asyncio

import pytest
from test_local_release_update import artifact
from test_maintenance_integration import allow_synthetic_owner, finish, new_backup

from jarvis_core import release_maintenance as module
from jarvis_core.local_release_launch import resolve_launch
from jarvis_core.local_release_update import ReleaseError
from jarvis_core.protocol import RpcError
from jarvis_core.service import JarvisService


class _OwnPeer:
    calls = ()

    async def aclose(self):
        pass


@pytest.fixture
async def host(tmp_path):
    path = tmp_path / "own-profile"
    path.mkdir(mode=0o700)
    service = JarvisService(path, _OwnPeer())
    service.store.create_session("Fixture explícito de releases")
    coordinator = service.maintenance_coordinator
    try:
        yield service, coordinator, tmp_path
    finally:
        await service.aclose()


async def verification(coordinator, parent):
    _, operation = await new_backup(coordinator, parent, "recovery-copy")
    await allow_synthetic_owner(coordinator)
    done = await finish(coordinator, operation["operation_id"])
    assert done["phase"] == "complete", done
    job = await coordinator.verify({"backup_path": str(parent / "recovery-copy")})
    done = await finish(coordinator, job["operation_id"])
    assert done["phase"] == "complete", done
    return done["result"]["verification_id"]


def source(parent, version):
    return artifact(
        parent / version, version, {"state": [0, 2], "memory": [0, 2], "schedules": [0, 1]}
    )


async def reviewed(coordinator, artifact_path, install_root, identity):
    job = await coordinator.release_review(
        {
            "artifact_path": str(artifact_path),
            "install_root": str(install_root),
            "verification_id": identity,
        }
    )
    done = await finish(coordinator, job["operation_id"])
    assert done["phase"] == "complete", done
    return done["result"]["review"]


async def applied(coordinator, review, *, rollback=False):
    callback = coordinator.release_rollback_apply if rollback else coordinator.release_apply
    operation = await callback({key: review[key] for key in ("review_id", "review_digest")})
    await allow_synthetic_owner(coordinator)
    return await finish(coordinator, operation["operation_id"])


async def test_host_install_update_rollback_durable_receipts_and_coherent_launch_plan(host):
    service, coordinator, parent = host
    identity = await verification(coordinator, parent)
    target = parent / "dedicated-install"
    target.mkdir(mode=0o700)
    first = await reviewed(coordinator, source(parent, "v1"), target, identity)
    assert first["metadata"]["profile_pin"]["schemas"]["memory"] == 0
    assert first["metadata"]["source_profile_and_recovery_copy_match"]
    assert first["metadata"]["backup_created_by_this_operation"] is False
    assert not list(target.iterdir())
    one = await applied(coordinator, first)
    assert one["phase"] == "complete" and one["receipt_durable"], one
    assert one["receipt"]["purpose"] == "release.update.apply"
    assert one["receipt"]["target_name"] == "v1"
    assert one["receipt"]["effect_status"] == "current_selected"
    assert one["result"]["release"]["launch_performed"] is False
    again = await coordinator.release_apply(
        {key: first[key] for key in ("review_id", "review_digest")}
    )
    assert again["operation_id"] == one["operation_id"]
    two = await applied(
        coordinator, await reviewed(coordinator, source(parent, "v2"), target, identity)
    )
    assert two["result"]["release"]["previous_key"] == "v1", two
    job = await coordinator.release_rollback_review(
        {"install_root": str(target), "verification_id": identity}
    )
    back_review = (await finish(coordinator, job["operation_id"]))["result"]["review"]
    back = await applied(coordinator, back_review, rollback=True)
    assert back["phase"] == "complete" and back["receipt_durable"], back
    assert back["receipt"]["purpose"] == "release.rollback.apply"
    assert back["result"]["release"]["previous_key"] == "v2"
    job = await coordinator.release_resolve_launch(
        {
            "install_root": str(target),
            "verification_id": identity,
            "expected_current_sha256": back["result"]["release"]["current_sha256"],
        }
    )
    done = await finish(coordinator, job["operation_id"])
    plan = done["result"]["release_launch"]
    assert plan["version_key"] == "v1" and plan["payload_verified"]
    assert plan["native_executable"].startswith(str(target / "releases/v1/app/"))
    assert plan["python_source"] == str(target / "releases/v1/python")
    assert plan["same_release_source_and_native"] and not plan["launch_performed"]
    assert not service.provider.calls
    journal = coordinator.journal.document.read()["operations"]
    assert journal[-1]["receipt"] == back["receipt"]


@pytest.mark.parametrize("decision", ["deny", "cancel", "revoke"])
async def test_denied_cancelled_or_revoked_never_changes_installation(host, decision):
    service, coordinator, parent = host
    identity = await verification(coordinator, parent)
    target = parent / "install"
    target.mkdir(mode=0o700)
    review = await reviewed(coordinator, source(parent, "v1"), target, identity)
    operation = await coordinator.release_apply(
        {key: review[key] for key in ("review_id", "review_digest")}
    )
    async with asyncio.timeout(3):
        while not (pending := (await coordinator.pending({}))["pending"]):
            await asyncio.sleep(0)
    card = pending[0]
    assert card["purpose"] == "release.update.apply"
    if decision == "deny":
        await coordinator.decide(
            {
                "approval_id": card["approval_id"],
                "digest": card["digest"],
                "allow": False,
                **card["scope"],
            }
        )
    elif decision == "cancel":
        await coordinator.cancel({"operation_id": operation["operation_id"]})
    else:
        service.authority.revoke_host(service.profile_lease.scope)
    done = await finish(coordinator, operation["operation_id"])
    assert done["phase"] in {"denied", "cancelled"}, done
    assert not list(target.iterdir())


async def test_verified_copy_must_match_current_data_not_just_same_generation(host):
    service, coordinator, parent = host
    identity = await verification(coordinator, parent)
    service.store.create_session("Cambio sintético posterior al respaldo")
    target = parent / "install"
    target.mkdir(mode=0o700)
    job = await coordinator.release_review(
        {
            "artifact_path": str(source(parent, "v1")),
            "install_root": str(target),
            "verification_id": identity,
        }
    )
    done = await finish(coordinator, job["operation_id"])
    assert done["phase"] == "failed"
    assert done["error_code"] == "release_recovery_copy_does_not_match_current_profile"
    assert not list(target.iterdir()) and not (await coordinator.pending({}))["pending"]


async def test_verified_copy_survives_new_host_scope_when_all_portable_resources_match(host):
    service, coordinator, parent = host
    with service.profile_gate.admit("synthetic-change-before-backup", mutating=True):
        service.store.create_session("Dato sintético incluido antes de respaldar")
    identity = await verification(coordinator, parent)
    historical = coordinator._verification[identity][1]
    original_scope = service.profile_lease.scope
    original_root_identity = service.profile_lease.identity
    root = service.directory
    await service.aclose()
    reopened = JarvisService(root, _OwnPeer())
    try:
        current = reopened.maintenance_coordinator
        assert reopened.profile_lease.scope.profile_id != original_scope.profile_id
        assert reopened.profile_lease.scope.generation != historical.generation
        assert reopened.profile_lease.identity == original_root_identity
        job = await current.verify({"backup_path": str(parent / "recovery-copy")})
        checked = await finish(current, job["operation_id"])
        assert checked["phase"] == "complete", checked
        reverified = checked["result"]["verification_id"]
        assert current._verification[reverified][1] == historical
        target = parent / "after-restart-install"
        target.mkdir(mode=0o700)
        review = await reviewed(current, source(parent, "v1"), target, reverified)
        pin = review["metadata"]["profile_pin"]
        assert pin["profile_id"] == reopened.profile_lease.scope.profile_id
        assert pin["profile_id"] != historical.profile_id
        assert pin["generation"] == reopened.profile_lease.scope.generation
        assert review["metadata"]["source_profile_and_recovery_copy_match"]
        assert not list(target.iterdir())
    finally:
        await reopened.aclose()


@pytest.mark.parametrize("changed", [None, "state", "memory", "schedules", "json", "absent_memory"])
async def test_restart_canonical_ram_portable_resource_hashes_cover_all_roles_and_absence(
    host, changed
):
    from jarvis_core.memory import MemoryScope
    from jarvis_core.scheduler import ScheduleSpec

    service, coordinator, parent = host
    scope = MemoryScope("jarvis", service._workspace_id)
    if changed != "absent_memory":
        service.memory.upsert(
            scope, "own-key", "Dato sintético base", source_uri="jarvis://own-test"
        )
    scheduled = service.schedule_store.create(
        "Tarea propia",
        "Sin ejecutar",
        "jarvis",
        service._workspace_id,
        ScheduleSpec("once", at=4_000_000_000),
        enabled=False,
    )
    service.agent_profiles.create("Agente propio", agent_id="own-fixture")
    identity = await verification(coordinator, parent)
    original_manifest = coordinator._verification[identity][1].manifest_sha256
    original_backup = {
        p.relative_to(parent / "recovery-copy").as_posix(): p.read_bytes()
        for p in (parent / "recovery-copy").rglob("*")
        if p.is_file()
    }
    root = service.directory
    await service.aclose()
    reopened = JarvisService(root, _OwnPeer())
    try:
        if changed == "state":
            reopened.store.create_session("Fila nueva tras reinicio")
        elif changed in {"memory", "absent_memory"}:
            reopened.memory.upsert(
                scope, "own-key", "Dato sintético cambiado", source_uri="jarvis://own-test"
            )
        elif changed == "schedules":
            reopened.schedule_store.update(
                scheduled.schedule_id, scheduled.revision, title="Tarea cambiada tras reinicio"
            )
        elif changed == "json":
            reopened.agent_profiles.update(
                "own-fixture", expected_revision=1, name="Nombre cambiado"
            )
        current = reopened.maintenance_coordinator
        job = await current.verify(
            {
                "backup_path": str(parent / "recovery-copy"),
                "expected_manifest_sha256": original_manifest,
            }
        )
        checked = await finish(current, job["operation_id"])
        assert checked["phase"] == "complete", checked
        target = parent / "after-restart"
        target.mkdir(mode=0o700)
        job = await current.release_review(
            {
                "artifact_path": str(source(parent, "v1")),
                "install_root": str(target),
                "verification_id": checked["result"]["verification_id"],
            }
        )
        done = await finish(current, job["operation_id"])
        if changed is None:
            assert done["phase"] == "complete", done
            assert done["result"]["review"]["metadata"]["source_profile_and_recovery_copy_match"]
        else:
            assert done["phase"] == "failed", done
            assert done["error_code"] == "release_recovery_copy_does_not_match_current_profile"
        assert original_backup == {
            p.relative_to(parent / "recovery-copy").as_posix(): p.read_bytes()
            for p in (parent / "recovery-copy").rglob("*")
            if p.is_file()
        }
        assert not list(target.iterdir())
    finally:
        await reopened.aclose()


async def test_recovery_copy_is_revalidated_after_owner_card(host):
    _, coordinator, parent = host
    identity = await verification(coordinator, parent)
    target = parent / "install"
    target.mkdir(mode=0o700)
    review = await reviewed(coordinator, source(parent, "v1"), target, identity)
    operation = await coordinator.release_apply(
        {key: review[key] for key in ("review_id", "review_digest")}
    )
    async with asyncio.timeout(3):
        while not (await coordinator.pending({}))["pending"]:
            await asyncio.sleep(0)
    path = parent / "recovery-copy/manifest.json"
    path.write_bytes(path.read_bytes() + b" ")
    await allow_synthetic_owner(coordinator)
    done = await finish(coordinator, operation["operation_id"])
    assert done["phase"] == "failed" and not list(target.iterdir()), done
    assert done["receipt_durable"]


async def test_publication_unknown_same_cut_cleanup_never_replays_and_inspect_is_readonly(
    host, monkeypatch
):
    _, coordinator, parent = host
    identity = await verification(coordinator, parent)
    target = parent / "install"
    target.mkdir(mode=0o700)
    calls = []
    original = module.apply_release

    def injected(review, context, **kwargs):
        calls.append(context.operation_id)

        def fault(stage):
            if stage == "after_current_replace":
                raise OSError("synthetic publication ACK lost")

        return original(review, context, fault=fault, **kwargs)

    monkeypatch.setattr(module, "apply_release", injected)
    review = await reviewed(coordinator, source(parent, "v1"), target, identity)
    done = await applied(coordinator, review)
    assert done["phase"] == "cleanup_pending" and done["cleanup_owner_pending"], done
    assert done["receipt"]["status"] == "unknown"
    row = coordinator._operations[done["operation_id"]]
    held_cut = row.capture_cut.cut
    with pytest.raises(RpcError):
        await coordinator.release_review(
            {
                "artifact_path": review["metadata"]["artifact_root"],
                "install_root": str(target),
                "verification_id": identity,
            }
        )
    await coordinator.cleanup({"operation_id": done["operation_id"]})
    cleaned = await finish(coordinator, done["operation_id"])
    assert not cleaned["cleanup_owner_pending"] and row.capture_cut is None
    assert cleaned["receipt"] == done["receipt"] and calls == [done["operation_id"]]
    assert held_cut.worker_count == 0
    before = (target / "PENDING").read_bytes()
    job = await coordinator.release_inspect({"install_root": str(target)})
    inspected = await finish(coordinator, job["operation_id"])
    assert inspected["result"]["release_installation"]["pending"] is not None
    assert (target / "PENDING").read_bytes() == before
    assert len(calls) == 1


async def test_live_profile_and_workspace_cannot_be_selected_as_release_paths(host):
    service, coordinator, parent = host
    identity = await verification(coordinator, parent)
    package = source(parent, "v1")
    for forbidden in (service.directory, service._workspace_path, parent / "recovery-copy"):
        job = await coordinator.release_review(
            {
                "artifact_path": str(package),
                "install_root": str(forbidden),
                "verification_id": identity,
            }
        )
        done = await finish(coordinator, job["operation_id"])
        assert done["error_code"] == "release_paths_overlap_profile_or_recovery_copy"


async def test_forged_or_missing_backup_identity_never_starts_review_worker(host):
    _, coordinator, parent = host
    target = parent / "install"
    target.mkdir(mode=0o700)
    with pytest.raises(RpcError):
        await coordinator.release_review(
            {
                "artifact_path": str(source(parent, "v1")),
                "install_root": str(target),
                "verification_id": "0" * 32,
            }
        )
    assert not list(target.iterdir())


def test_launch_resolver_detects_payload_tamper_and_pins_both_entrypoints(tmp_path):
    from test_local_release_update import context, install, profile_pin

    target = tmp_path / "install"
    target.mkdir(mode=0o700)
    installed = install(artifact(tmp_path / "source"), target)
    plan = resolve_launch(
        target, context(), profile_pin(), expected_current_sha256=installed.current_sha256
    )
    arguments = plan.arguments(python="/own/python", codex="/own/codex", state="/own/profile")
    assert arguments[0] == str(target / "releases/v1/app/Jarvis.app/Contents/MacOS/Jarvis")
    assert arguments[arguments.index("--source") + 1] == str(target / "releases/v1/python")
    assert "--provider-state" not in arguments
    path = target / "releases/v1/python/jarvis_core/service.py"
    path.chmod(0o600)
    path.write_text("TAMPERED")
    with pytest.raises(ReleaseError, match="release_resource_changed"):
        resolve_launch(target, context(), profile_pin())
