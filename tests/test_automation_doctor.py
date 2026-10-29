"""Metadata-only automation diagnosis and cold portable snapshots of test profiles."""

import hashlib
import json
import threading

import pytest
from test_maintenance_integration import allow_synthetic_owner, finish, new_backup

from jarvis_core.automation_flows import AutomationFlowStore
from jarvis_core.doctor import DoctorContext, DoctorInventory, inspect_report
from jarvis_core.profiles import _initial as initial_agents
from jarvis_core.scheduler import DispatchResult
from jarvis_core.service import JarvisService


def make_profile(tmp_path):
    root = tmp_path.resolve() / "profile"
    root.mkdir(mode=0o700)
    profiles = root / "profiles"
    profiles.mkdir(mode=0o700)
    agent_file = profiles / "agents.json"
    agent_file.write_text(json.dumps(initial_agents()))
    agent_file.chmod(0o600)
    store = AutomationFlowStore(root / "automation.json")
    hook = store.create_hook("PRIVATE SYNTHETIC TITLE", "manual", "jarvis", "workspace", [
        {"prompt": "PRIVATE SYNTHETIC PROMPT", "pause_after": True},
        {"prompt": "PRIVATE NEXT STEP", "pause_after": False},
    ])
    hook = store.update_hook(hook["hook_id"], hook["revision"], enabled=True)
    flow = store.emit("manual", "PRIVATE_SYNTHETIC_EVENT", {"text": "PRIVATE PAYLOAD"},
                      hook_id=hook["hook_id"])[0]
    claim = store.claim_next()
    store.finish_step(claim, DispatchResult(None))
    store.close()
    return root, hook, flow


def report(root):
    info = root.stat()
    inventory = DoctorInventory("synthetic_profile", 1, root, (info.st_dev, info.st_ino),
                                "offline_quiescent", roles=("agents", "automations"))
    return inspect_report(inventory, DoctorContext(lambda *_args, **_kw: None, threading.Event()))


def test_automation_doctor_only_projects_counts_and_keeps_original_bytes(tmp_path, monkeypatch):
    root, _hook, _flow = make_profile(tmp_path)
    path = root / "automation.json"
    before = path.read_bytes()
    fingerprint = path.stat()

    def forbidden(*_args, **_kw):
        raise AssertionError("doctor_must_not_construct_store_or_start_listener")

    monkeypatch.setattr(AutomationFlowStore, "__init__", forbidden)
    result = report(root)
    assert result.status == "ok"
    check = next(row for row in result.checks if row.role == "automations")
    assert check.format_version == 1
    assert dict(check.counts) == {
        "automation_hooks": 1, "enabled_automation_hooks": 1,
        "automation_flows": 1, "paused_automation_flows": 1,
        "unsettled_automation_flows": 0,
    }
    serialized = json.dumps(result.to_dict())
    assert "PRIVATE" not in serialized
    assert "PROMPT" not in serialized and "PAYLOAD" not in serialized
    assert path.read_bytes() == before
    assert (path.stat().st_ino, path.stat().st_mtime_ns) == (
        fingerprint.st_ino, fingerprint.st_mtime_ns
    )


@pytest.mark.parametrize("change", ["invalid", "missing_agent", "digest", "symlink"])
def test_automation_doctor_fails_closed_without_exposing_payload(tmp_path, change):
    root, _hook, _flow = make_profile(tmp_path)
    path = root / "automation.json"
    value = json.loads(path.read_bytes())
    if change == "invalid":
        value["revision"] = True
    elif change == "missing_agent":
        value["hooks"][0]["agent_id"] = "missing"
    elif change == "digest":
        value["flows"][0]["payload_sha256"] = "a" * 64
    else:
        outside = tmp_path / "outside"
        outside.write_bytes(b"PRIVATE OUTSIDE SENTINEL")
        path.unlink()
        path.symlink_to(outside)
    if change != "symlink":
        path.write_text(json.dumps(value))
        path.chmod(0o600)
    result = report(root)
    assert result.status in {"corrupt", "unsafe"}
    assert "PRIVATE" not in json.dumps(result.to_dict())
    if change == "symlink":
        assert outside.read_bytes() == b"PRIVATE OUTSIDE SENTINEL"


def test_automation_doctor_reports_absence_without_creating_a_registry(tmp_path):
    root, _hook, _flow = make_profile(tmp_path)
    path = root / "automation.json"
    path.unlink()
    result = report(root)
    check = next(row for row in result.checks if row.role == "automations")
    assert check.status == "absent"
    assert result.status == "partial"
    assert not path.exists()


class NoProvider:
    async def aclose(self):
        pass


async def test_service_backup_restores_automation_checkpoint_disarmed_without_consuming_source(
    tmp_path,
):
    root = tmp_path.resolve() / "synthetic"
    root.mkdir(mode=0o700)
    service = JarvisService(root, NoProvider())
    try:
        service.store.create_session("Perfil sintético de recuperación")
        hook = service.automation_flows.create_hook("Sintética", "manual", "jarvis",
                                                     service._workspace_id, [
            {"prompt": "Sólo datos sintéticos", "pause_after": True},
            {"prompt": "Sin herramientas", "pause_after": False},
        ])
        hook = service.automation_flows.update_hook(hook["hook_id"], 1, enabled=True)
        flow = service.automation_flows.emit("manual", "synthetic-event", {},
                                             hook_id=hook["hook_id"])[0]
        claim = service.automation_flows.claim_next()
        service.automation_flows.finish_step(claim, DispatchResult(None))
        original = (root / "automation.json").read_bytes()
        digest = hashlib.sha256(original).hexdigest()
        coordinator = service.maintenance_coordinator
        _review, operation = await new_backup(coordinator, tmp_path)
        await allow_synthetic_owner(coordinator)
        result = await finish(coordinator, operation["operation_id"])
        assert result["phase"] == "complete", result
        verification = await coordinator.verify({"backup_path": str(tmp_path / "backup")})
        verified = await finish(coordinator, verification["operation_id"])
        assert verified["phase"] == "complete", verified
        review = await coordinator.review_restore({
            "verification_id": verified["result"]["verification_id"],
            "destination_parent": str(tmp_path), "name": "restored",
        })
        operation = await coordinator.restore({
            "review_id": review["review_id"], "review_digest": review["review_digest"],
        })
        await allow_synthetic_owner(coordinator)
        restored = await finish(coordinator, operation["operation_id"])
        assert restored["phase"] == "staged_disarmed", restored
        value = json.loads((tmp_path / "restored" / "automation.json").read_bytes())
        assert not value["hooks"][0]["enabled"]
        assert value["flows"][0]["flow_id"] == flow["flow_id"]
        assert value["flows"][0]["status"] == "interrupted"
        assert value["flows"][0]["outcome"] == "unknown"
        assert len(value["flows"][0]["receipts"]) == 1
        assert hashlib.sha256((root / "automation.json").read_bytes()).hexdigest() == digest
        assert service.automation_flows.get_flow(flow["flow_id"])["status"] == "paused"
    finally:
        await service.aclose()
