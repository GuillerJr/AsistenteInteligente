"""Remembered choices stay scoped, nonportable, bounded and explicitly revocable."""
import copy
import json

import pytest

from jarvis_core.automation_permissions import AutomationPermissions, permission_selector
from jarvis_core.private_json import PrivateJsonError
from jarvis_core.tools import ToolError

BASE = {"client_id": "a" * 64, "agent_id": "jarvis", "workspace_id": "b" * 64,
        "session_id": "synthetic-chat", "temporary": False}
DESKTOP = {"target": {"bundle_id": "com.jarvis.fixture", "pid": 7319,
                       "launch_id": "synthetic-launch"}, "window_ref": "synthetic-window",
           "snapshot_id": "snapshot-one", "ref": "element-one", "action": "set_value",
           "value": "OWN SYNTHETIC TEXT"}
BROWSER = {"tab_id": "synthetic-tab", "expected_origin": "https://example.com",
           "snapshot_id": "snapshot-one", "ref": "element-one", "action": "fill",
           "text": "OWN SYNTHETIC TEXT"}


@pytest.fixture
def permissions(tmp_path):
    profile = tmp_path.resolve() / "profile"
    profile.mkdir(mode=0o700)
    return profile, AutomationPermissions(profile)


def remember(policy, tool="desktop_action", args=None, mode="always", **context):
    return policy.remember(tool=tool, arguments=args or DESKTOP, mode=mode, **{**BASE, **context})


def match(policy, tool="desktop_action", args=None, **context):
    return policy.match(tool=tool, arguments=args or DESKTOP, **{**BASE, **context})


def test_always_survives_restart_but_not_client_agent_or_workspace_changes(permissions):
    profile, policy = permissions
    identity = remember(policy)
    assert match(policy) == identity
    assert match(AutomationPermissions(profile)) == identity
    for changes in [{"client_id": "c" * 64}, {"agent_id": "another-agent"},
                    {"workspace_id": "d" * 64}]:
        assert match(policy, **changes) is None
    assert policy.snapshot()["permissions_portable"] is False
    assert not list(profile.iterdir())
    assert (profile.parent / "profile.automation" / "permissions.json").is_file()


def test_session_rule_never_crosses_conversation_mode_or_restart(permissions):
    profile, policy = permissions
    identity = remember(policy, mode="session")
    assert match(policy) == identity
    assert match(policy, session_id="other-chat") is None
    assert match(policy, temporary=True) is None
    assert match(AutomationPermissions(profile)) is None
    policy.clear_session(BASE["session_id"])
    assert match(policy) is None


def test_temporary_cannot_remember_always_or_inherit_permanent_rules(permissions):
    _, policy = permissions
    remember(policy)
    assert match(policy, temporary=True) is None
    with pytest.raises(ToolError, match="mode_invalid"):
        remember(policy, temporary=True)
    identity = remember(policy, mode="session", temporary=True)
    assert match(policy, temporary=True) == identity
    assert policy.snapshot()["session_rules"] == 1
    assert len(policy.snapshot()["rules"]) == 1


@pytest.mark.parametrize("change", ["bundle", "pid", "launch", "window", "action", "key"])
def test_desktop_permission_binds_physical_instance_window_and_action_class(permissions, change):
    _, policy = permissions
    identity = remember(policy)
    refreshed = {**DESKTOP, "snapshot_id": "snapshot-two", "ref": "element-two",
                 "value": "new text"}
    assert match(policy, args=refreshed) == identity
    other = copy.deepcopy(refreshed)
    if change == "bundle":
        other["target"]["bundle_id"] = "com.jarvis.other-fixture"
    elif change == "pid":
        other["target"]["pid"] += 1
    elif change == "launch":
        other["target"]["launch_id"] = "another-launch"
    elif change == "window":
        other["window_ref"] = "other-window"
    elif change == "action":
        other["action"] = "press"
    else:
        other["key"] = "super+s"
    assert match(policy, args=other) is None


@pytest.mark.parametrize("change", ["tab", "origin", "port", "scheme", "action"])
def test_browser_permission_binds_exact_tab_origin_and_action_class(permissions, change):
    _, policy = permissions
    identity = remember(policy, tool="browser_action", args=BROWSER)
    refreshed = {**BROWSER, "snapshot_id": "snapshot-two", "ref": "element-two", "text": "new text"}
    assert match(policy, tool="browser_action", args=refreshed) == identity
    other = dict(refreshed)
    if change == "tab":
        other["tab_id"] = "other-tab"
    elif change == "origin":
        other["expected_origin"] = "https://other.example"
    elif change == "port":
        other["expected_origin"] = "https://example.com:8443"
    elif change == "scheme":
        other["expected_origin"] = "http://example.com"
    else:
        other["action"] = "click"
    assert match(policy, tool="browser_action", args=other) is None
    with pytest.raises(ToolError, match="origin_required"):
        permission_selector("browser_action", {k: v for k, v in BROWSER.items()
                                               if k != "expected_origin"})


@pytest.mark.parametrize("tool,args,other", [
    ("process_run", {"argv": ["/bin/echo", "ONE"]}, {"argv": ["/bin/echo", "TWO"]}),
    ("workspace_patch", {"patch": "fixture-one", "expected_sha256": {}},
                        {"patch": "fixture-two", "expected_sha256": {}}),
    ("browser_navigate", {"tab_id": "t", "expected_origin": "https://example.com",
                          "url": "https://example.com/one"},
                         {"tab_id": "t", "expected_origin": "https://example.com",
                          "url": "https://example.com/two"}),
])
def test_other_tools_remember_only_exact_arguments(permissions, tool, args, other):
    _, policy = permissions
    identity = remember(policy, tool=tool, args=args)
    assert match(policy, tool=tool, args=args) == identity
    assert match(policy, tool=tool, args=other) is None


def test_input_limit_counts_utf8_bytes_and_cannot_expand_with_new_refs(permissions):
    _, policy = permissions
    args = {**BROWSER, "text": "á" * 2048}
    identity = remember(policy, tool="browser_action", args=args)
    assert match(policy, tool="browser_action", args=args) == identity
    oversized = {**args, "text": "á" * 2049, "ref": "new-element"}
    assert match(policy, tool="browser_action", args=oversized) is None
    with pytest.raises(ToolError, match="input_limit"):
        remember(policy, tool="browser_action", args=oversized, mode="session")


def test_revoke_is_cas_and_survives_restart(permissions):
    profile, policy = permissions
    identity = remember(policy)
    revision = policy.snapshot()["revision"]
    with pytest.raises(ToolError, match="revision_changed"):
        policy.revoke(identity, revision - 1)
    assert match(policy) == identity
    policy.revoke(identity, revision)
    assert match(policy) is None and match(AutomationPermissions(profile)) is None
    with pytest.raises(ToolError, match="not_found"):
        policy.revoke(identity, policy.snapshot()["revision"])


def test_session_rules_are_visible_and_can_be_revoked_without_closing_chat(permissions):
    _, policy = permissions
    identity = remember(policy, mode="session")
    snapshot = policy.snapshot()
    assert snapshot["volatile_rules"][0]["id"] == identity
    assert snapshot["volatile_rules"][0]["session_id"] == BASE["session_id"]
    with pytest.raises(ToolError, match="revision_changed"):
        policy.revoke(identity, snapshot["revision"], snapshot["session_revision"] - 1)
    assert match(policy) == identity
    policy.revoke(identity, snapshot["revision"], snapshot["session_revision"])
    assert match(policy) is None and policy.snapshot()["volatile_rules"] == []


def test_search_destination_change_invalidates_search_grants_only(permissions):
    _, policy = permissions
    policy.set_search("https://first.example/?q={query}", policy.snapshot()["revision"])
    query = {"query": "SYNTHETIC QUERY"}
    always = remember(policy, tool="browser_search", args=query)
    session = remember(policy, tool="browser_search", args=query, mode="session",
                       session_id="other-chat")
    unrelated = remember(policy, tool="browser_action", args=BROWSER)
    assert match(policy, tool="browser_search", args=query) == always
    assert match(policy, tool="browser_search", args=query, session_id="other-chat") == session
    policy.set_search("https://second.example/search?q={query}", policy.snapshot()["revision"])
    assert match(policy, tool="browser_search", args=query) is None
    assert match(policy, tool="browser_search", args=query, session_id="other-chat") is None
    assert match(policy, tool="browser_action", args=BROWSER) == unrelated


def test_replaced_physical_profile_does_not_adopt_adjacent_permissions(permissions):
    profile, policy = permissions
    remember(policy)
    profile.rename(profile.with_name("original-profile"))
    profile.mkdir(mode=0o700)
    with pytest.raises((PrivateJsonError, ToolError)):
        AutomationPermissions(profile).snapshot()


@pytest.mark.parametrize("mutation", ["mode", "symlink", "corrupt"])
def test_unsafe_permission_registry_fails_closed_without_changing_it(permissions, mutation):
    profile, policy = permissions
    remember(policy)
    path = profile.parent / "profile.automation" / "permissions.json"
    if mutation == "mode":
        path.chmod(0o644)
    elif mutation == "symlink":
        target = path.with_name("synthetic-original.json")
        path.rename(target)
        path.symlink_to(target)
    else:
        data = json.loads(path.read_text())
        data["rules"][0]["selector"] = "not-a-digest"
        path.write_text(json.dumps(data))
    before = path.read_bytes()
    with pytest.raises((PrivateJsonError, ToolError)):
        match(policy)
    assert path.read_bytes() == before


def test_snapshot_cannot_mutate_live_session_permission_without_a_revision(permissions):
    _, policy = permissions
    identity = remember(policy, mode="session")
    snapshot = policy.snapshot()
    snapshot["volatile_rules"][0]["client_id"] = "c" * 64
    assert match(policy) == identity
    assert match(policy, client_id="c" * 64) is None


def test_destination_is_utf8_bounded_and_not_generic_argument_payload(permissions):
    _, policy = permissions
    path = "/".join(["子" * 70] * 4) + "/fixture.txt"
    identity = remember(policy, tool="workspace_write", args={"path": path, "text": "fixture"})
    row = next(row for row in policy.snapshot()["rules"] if row["id"] == identity)
    assert len(row["destination"].encode()) <= 512
    secret = "SYNTHETIC SECRET MUST NOT BECOME LABEL"
    other = remember(policy, tool="mcp_fixture_command",
                     args={"target": "device-id", "text": secret})
    row = next(row for row in policy.snapshot()["rules"] if row["id"] == other)
    assert row["destination"] == "mcp_fixture_command" and secret not in row["destination"]
