import json
from pathlib import Path

from aegis_core.contracts import AgentRole, PolicyDecision, ToolAuthorization, ToolCall
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context
from aegis_core.tools.execution import ReadOnlyToolExecutor


def _authorize(tool_name: str, arguments: dict[str, object], root: Path) -> ToolAuthorization:
    call = ToolCall(
        call_id="call-1",
        tool_name=tool_name,
        arguments=arguments,
        requested_by=AgentRole.CODE_SECURITY,
    )
    return build_default_tool_broker().authorize(call, default_policy_context(root))


def test_runtime_executor_returns_only_non_secret_metadata(tmp_path: Path) -> None:
    authorization = _authorize("system_describe_runtime", {}, tmp_path)

    result = ReadOnlyToolExecutor().execute(authorization, default_policy_context(tmp_path))

    assert result.success is True
    assert set(json.loads(result.output)) == {
        "architecture",
        "operating_system",
        "os_release",
        "python",
    }


def test_file_executor_reads_bounded_utf8_inside_workspace(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("abcdef", encoding="utf-8")
    authorization = _authorize(
        "filesystem_read_text", {"path": "notes.txt", "max_bytes": 4}, tmp_path
    )

    result = ReadOnlyToolExecutor().execute(authorization, default_policy_context(tmp_path))

    assert result.success is True
    assert result.output == "abcd"
    assert result.metadata == {"path": "notes.txt", "bytes_read": 4, "truncated": True}


def test_executor_rejects_symlink_even_if_authorization_is_forged(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-aegis-test.txt"
    outside.write_text("do not read", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(outside)
    forged = ToolAuthorization(
        call_id="call-1",
        tool_name="filesystem_read_text",
        call_digest="b" * 64,
        decision=PolicyDecision.ALLOW,
        reason_code="policy_allowed",
        normalized_arguments={"path": "link.txt", "max_bytes": 128},
    )

    result = ReadOnlyToolExecutor().execute(forged, default_policy_context(tmp_path))

    assert result.success is False
    assert result.error_code in {"access_denied", "io_error"}
    assert result.output == ""
    outside.unlink()


def test_executor_never_runs_a_non_allowed_authorization(tmp_path: Path) -> None:
    denied = _authorize("filesystem_read_text", {"path": "../secret.txt"}, tmp_path)

    result = ReadOnlyToolExecutor().execute(denied, default_policy_context(tmp_path))

    assert denied.decision is PolicyDecision.DENY
    assert result.success is False
    assert result.error_code == "authorization_not_allowed"


def test_confirmed_network_call_has_no_executor(tmp_path: Path) -> None:
    authorization = ToolAuthorization(
        call_id="call-network",
        tool_name="network_discover_hosts",
        call_digest="c" * 64,
        decision=PolicyDecision.ALLOW,
        reason_code="policy_allowed",
        normalized_arguments={"target": "127.0.0.1/32", "mode": "ping", "ports": []},
    )

    result = ReadOnlyToolExecutor().execute(authorization, default_policy_context(tmp_path))

    assert result.success is False
    assert result.error_code == "executor_unavailable"
