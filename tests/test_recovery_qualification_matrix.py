"""Finite 16-case own qualification fixtures, source oracles and portable bytes."""

import importlib.util
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from test_recovery_extensions import decision, params
from test_wal_host_boundaries import NoAccount

from jarvis_core.service import JarvisService

_SPEC = importlib.util.spec_from_file_location(
    "jarvis_matrix_fixture", Path(__file__).resolve().parents[1]
    / "script/recovery_qualification_matrix.py")
matrix = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(matrix)


@pytest.fixture(scope="module")
def prepared():
    root = matrix._TEMP / ("jarvis-mx-" + uuid4().hex[:6])
    result = matrix.prepare(root)
    try:
        yield {row["case_id"]: row for row in result["cases"]}
    finally:
        shutil.rmtree(root)


async def finish(coordinator, operation):
    import asyncio
    identity = operation if isinstance(operation, str) else operation["operation_id"]
    async with asyncio.timeout(40):
        while True:
            result = await coordinator.status({"operation_id": identity})
            if not result["worker_pending"]:
                return result
            await asyncio.sleep(.005)


@pytest.mark.parametrize("case_id", [row["id"] for row in matrix.cases()])
async def test_each_finite_case_matches_independent_full_projection_and_original_hashes(
    prepared, case_id,
):
    entry = prepared[case_id]
    fixture = Path(entry["fixture"])
    assert matrix.verify(fixture)["source_unchanged"]
    root = Path(entry["profile"])
    peer = NoAccount()
    service = JarvisService(root, peer)
    coordinator = service.maintenance_coordinator
    try:
        assert matrix.verify(fixture)["source_unchanged"]
        if entry["repair_role"] is None:
            doctor = await finish(coordinator, await coordinator.doctor({}))
            assert doctor["phase"] == "complete", doctor
            review = await coordinator.review_backup({"destination_parent": str(fixture.parent),
                                                       "name": "respaldo"})
            operation = await coordinator.backup(params(review))
            target = "respaldo"
        else:
            initial = await coordinator.repair_review({"role": entry["repair_role"],
                "repair_kind": "wal_valid_prefix", "destination_parent": str(fixture.parent),
                "name": "reparado"})
            reviewed = await finish(coordinator, initial)
            assert reviewed["phase"] == "complete", reviewed
            review = reviewed["result"]["review"]
            details = review["metadata"]["candidate"]
            for key, expected in entry["expected_loss"].items():
                assert details[key] == expected
            operation = await coordinator.repair_apply(params(review))
            target = "reparado"
        await decision(coordinator, True)  # Synthetic owner, never an actual user's decision.
        result = await finish(coordinator, operation)
        assert result["phase"] == "complete" and result["receipt_durable"], result
        status = fixture.parent / "effect-status.json"
        status.write_bytes(matrix.canonical(result))
        status.chmod(0o600)
        verified = matrix.verify(fixture, fixture.parent / target, status)
        assert verified["logical_roles"] == ["memory", "schedules", "state"]
        assert verified["artifact_integrity"] and verified["source_unchanged"]
        assert verified["owner_decision_verified_by_this_script"] is False
        assert service.store._disk is service.memory._disk is None
        assert service.schedule_store._connection is None and peer.calls == 0
    finally:
        await service.aclose()


def test_fixture_prepare_never_overwrites_or_uses_a_personal_root(tmp_path):
    with pytest.raises(ValueError, match="fixture_new_private_temp_root_required"):
        matrix.prepare(tmp_path / "personal")
    root = matrix._TEMP / ("jarvis-matrix-invalid-" + uuid4().hex)
    with pytest.raises(ValueError, match="fixture_case_invalid"):
        matrix.prepare(root, ["unknown"])
    assert not root.exists()


def test_verifier_refuses_unmarked_outside_and_changed_original_before_sqlite(
    prepared, tmp_path, monkeypatch,
):
    fixture = Path(prepared["wal_absent_all"]["fixture"])
    with pytest.raises(ValueError, match="fixture_known_temporary_case_required"):
        matrix.verify(tmp_path / "auth.json")
    path = Path(prepared["wal_absent_all"]["profile"]) / "jarvis.sqlite3"
    raw, _ = matrix.read_private(path)
    path.write_bytes(raw + b"synthetic mutation")
    monkeypatch.setattr(matrix.sqlite3, "connect", lambda *_args, **_kwargs: pytest.fail(
        "a changed original must fail before any SQLite constructor"))
    with pytest.raises(ValueError, match="fixture_original_changed"):
        matrix.verify(fixture)


async def test_representative_prefix_artifact_restores_and_verifies_as_new_disarmed_profile():
    root = matrix._TEMP / ("jarvis-mr-" + uuid4().hex[:6])
    entry = matrix.prepare(root, ["prefix_truncated_memory"])["cases"][0]
    fixture = Path(entry["fixture"])
    service = JarvisService(Path(entry["profile"]), NoAccount())
    coordinator = service.maintenance_coordinator
    try:
        read = await coordinator.repair_review({"role": "memory", "repair_kind": "wal_valid_prefix",
            "destination_parent": str(fixture.parent), "name": "reparado"})
        result = await finish(coordinator, read)
        review = result["result"]["review"]
        operation = await coordinator.repair_apply(params(review))
        await decision(coordinator, True)
        published = await finish(coordinator, operation)
        assert published["phase"] == "complete", published
        verified = await finish(coordinator, await coordinator.verify({
            "backup_path": str(fixture.parent / "reparado")}))
        review = await coordinator.review_restore({
            "verification_id": verified["result"]["verification_id"],
            "destination_parent": str(fixture.parent), "name": "restaurado"})
        operation = await coordinator.restore(params(review))
        await decision(coordinator, True)
        result = await finish(coordinator, operation)
        assert result["phase"] == "staged_disarmed", result
        status = fixture.parent / "restore-status.json"
        status.write_bytes(matrix.canonical(result))
        status.chmod(0o600)
        checked = matrix.verify(fixture, fixture.parent / "restaurado", status)
        assert checked["restored_disarmed"] and checked["source_unchanged"]
        assert checked["logical_roles"] == ["memory", "schedules", "state"]
        assert service.profile_gate._cut is None
    finally:
        await service.aclose()
        shutil.rmtree(root)


def test_explicit_dead_socket_fixture_intervention_requires_exit_and_exclusive_lease(prepared):
    from jarvis_core.host_profile import HostProfileError, ProfileLease
    entry = prepared["wal_empty_all"]
    fixture, profile = Path(entry["fixture"]), Path(entry["profile"])
    with pytest.raises(ValueError, match="fixture_confirmed_child_exit_required"):
        matrix.retire_fixture_socket(fixture, {"type": "socket"})
    lease = ProfileLease.acquire(profile)
    try:
        with pytest.raises(HostProfileError, match="profile_busy"):
            matrix.retire_fixture_socket(fixture, {"type": "socket", "metadata": []},
                                         child_exit_confirmed=True)
    finally:
        lease.close()
    assert matrix.verify(fixture)["source_unchanged"]
