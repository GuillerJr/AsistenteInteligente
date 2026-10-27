"""Synthetic policy metadata only; no note bodies, SDK accounts or model runtime."""
import json
from dataclasses import replace

import pytest

from jarvis_core.memory_policy import MemoryPolicyError, MemoryPolicySettings, MemoryPolicyStore

CLIENT, WORKSPACE = "a" * 64, "b" * 64


@pytest.fixture
def policy(tmp_path):
    root = tmp_path / "own-profile"
    root.mkdir(mode=0o700)
    return MemoryPolicyStore(root)


def snapshot(policy, **changes):
    return policy.snapshot(**({"client_id": CLIENT, "agent_id": "jarvis",
                               "workspace_id": WORKSPACE} | changes))


def review(policy, settings=None, **changes):
    current = snapshot(policy, **changes)
    return policy.review(**current.scope.metadata(), settings=settings or MemoryPolicySettings(
        auto_recall_enabled=True, auto_index_enabled=True, mode="hybrid_local"),
        expected_revision=current.revision, expected_digest=current.digest)


def apply(policy, settings=None, **changes):
    return policy.apply(review(policy, settings, **changes), assert_current=lambda: None)


def test_default_off_and_owner_sidecar_is_outside_portable_profile(policy):
    current = snapshot(policy)
    assert current.revision == 1 and current.settings == MemoryPolicySettings()
    assert current.metadata()["permissions_portable"] is False
    assert policy.directory == policy.profile.with_name(policy.profile.name + ".memory-control")
    assert not (policy.profile / "policy.json").exists()
    assert not (policy.directory / "policy.json").exists()


def test_review_readonly_then_guarded_cas_and_durable_reload(policy):
    selected = review(policy)
    assert snapshot(policy).settings.auto_recall_enabled is False
    checks, staged = [], []
    result = policy.apply(selected, assert_current=lambda: checks.append("host"),
                          before_publish=lambda value: staged.append(value.identity))
    assert len(checks) >= 3 and len(staged) == 2
    assert result.settings.auto_recall_enabled and result.settings.auto_index_enabled
    assert result.settings.mode == "hybrid_local" and result.revision == 2
    assert snapshot(MemoryPolicyStore(policy.profile)) == result
    with pytest.raises(MemoryPolicyError, match="memory_policy_changed"):
        policy.apply(selected, assert_current=lambda: None)


def test_policy_is_bound_to_exact_client_agent_and_workspace(policy):
    enabled = apply(policy)
    assert enabled.settings.auto_recall_enabled
    for change in ({"client_id": "c" * 64}, {"agent_id": "analista"},
                   {"workspace_id": "d" * 64}):
        assert not snapshot(policy, **change).settings.auto_recall_enabled


def test_aba_revocation_invalidates_old_snapshot_and_does_not_touch_notes(policy):
    notes = policy.profile / "notes-synthetic.fixture"
    notes.write_bytes(b"OWN NOTES RETAINED")
    before = snapshot(policy)
    active = apply(policy)
    disabled = apply(policy, MemoryPolicySettings())
    assert disabled.settings == before.settings and disabled.revision > active.revision
    assert disabled.digest != before.digest
    with pytest.raises(MemoryPolicyError, match="memory_policy_changed"):
        policy.assert_current(active)
    assert notes.read_bytes() == b"OWN NOTES RETAINED"


def test_replaced_physical_profile_cannot_reuse_control(policy, tmp_path):
    apply(policy)
    old = tmp_path / "moved-profile"
    policy.profile.rename(old)
    policy.profile.mkdir(mode=0o700)
    with pytest.raises(MemoryPolicyError, match="memory_policy_profile_changed"):
        snapshot(policy)
    with pytest.raises(MemoryPolicyError, match="memory_policy_registry_invalid"):
        snapshot(MemoryPolicyStore(policy.profile))


def test_temporary_policies_use_ram_only_and_are_per_session(policy, monkeypatch):
    import jarvis_core.memory_policy as module

    def forbidden(*_args, **_kwargs):
        pytest.fail("temporary scope opened durable policy")
    monkeypatch.setattr(module, "PrivateJsonDocument", forbidden)
    a = apply(policy, temporary=True, session_id="chat_a")
    b = apply(policy, temporary=True, session_id="chat_b", client_id="c" * 64)
    assert a.settings.auto_index_enabled and b.settings.auto_recall_enabled
    assert not policy.directory.exists()
    assert not snapshot(policy, temporary=True, session_id="chat_c").settings.auto_recall_enabled
    policy.forget_temporary(client_id=CLIENT, session_id="chat_a")
    assert not snapshot(policy, temporary=True, session_id="chat_a").settings.auto_index_enabled
    assert snapshot(policy, temporary=True, session_id="chat_b", client_id="c" * 64
                    ).settings.auto_recall_enabled


@pytest.mark.parametrize("settings", [
    {"auto_recall_enabled": True, "mode": "nvidia", "auto_index_enabled": True,
     "source_policy": "approved_notes_only"},
    {"auto_recall_enabled": 1, "mode": "lexical", "auto_index_enabled": True,
     "source_policy": "approved_notes_only"},
    {"auto_recall_enabled": True, "mode": [], "auto_index_enabled": True,
     "source_policy": "approved_notes_only"},
    {"auto_recall_enabled": True, "mode": "lexical", "auto_index_enabled": True,
     "source_policy": "scan_workspace"},
    {"mode": "lexical"},
])
def test_settings_never_enable_unknown_mode_fallback_or_source_discovery(policy, settings):
    with pytest.raises(MemoryPolicyError, match="memory_policy_settings_invalid"):
        review(policy, settings)


def test_revoked_host_before_publication_leaves_old_policy_and_no_staging(policy):
    original = snapshot(policy)
    calls = []
    def host():
        calls.append(True)
        if len(calls) == 2:
            raise RuntimeError("OWN revoked host")
    with pytest.raises(RuntimeError, match="revoked host"):
        policy.apply(review(policy), assert_current=host)
    assert snapshot(policy) == original
    assert not list(policy.directory.glob(".registry-*"))


def test_review_expiry_cannot_be_renewed_by_waiting_and_settings_tamper_rejects(policy):
    clock = [100.0]
    policy.clock = lambda: clock[0]
    selected = review(policy)
    changed = replace(selected, settings=MemoryPolicySettings(mode="semantic_local"))
    with pytest.raises(MemoryPolicyError, match="memory_policy_review_invalid"):
        policy.apply(changed, assert_current=lambda: None)
    clock[0] = 221
    with pytest.raises(MemoryPolicyError, match="memory_policy_review_expired"):
        policy.apply(selected, assert_current=lambda: None)
    assert not snapshot(policy).settings.auto_recall_enabled


@pytest.mark.parametrize("change", ["future_version", "binding", "unknown_setting"])
def test_corrupt_or_foreign_policy_is_visible_failure_without_defaults(policy, change):
    apply(policy)
    path = policy.directory / "policy.json"
    value = json.loads(path.read_bytes())
    if change == "future_version":
        value["version"] = 2
    elif change == "binding":
        value["profile_binding"] = "f" * 64
    else:
        value["policies"][0]["settings"]["source_path"] = "/synthetic/never-open"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    with pytest.raises((MemoryPolicyError, ValueError)):
        snapshot(policy)
