from __future__ import annotations

import errno
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from aegis_core.contracts import AgentRole, PolicyDecision, ToolAuthorization, ToolCall
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context
from aegis_core.tools.execution import (
    _CALENDAR_LIST_SCRIPT,
    ReadOnlyToolExecutor,
    _security_control_state,
)


def test_calendar_script_sorts_globally_before_applying_the_limit() -> None:
    assert "output.length >= payload.limit" not in _CALENDAR_LIST_SCRIPT
    assert "calendar candidate limit exceeded" in _CALENDAR_LIST_SCRIPT
    assert "local_start_at: localISOString(eventStart)" in _CALENDAR_LIST_SCRIPT
    assert _CALENDAR_LIST_SCRIPT.index("output.sort") < _CALENDAR_LIST_SCRIPT.index(
        "output.slice"
    )


def test_calendar_executor_returns_the_global_earliest_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    later = {"title": "Later", "start_at": "2026-08-26T18:00:00.000Z"}
    earlier = {"title": "Earlier", "start_at": "2026-08-26T15:00:00.000Z"}
    monkeypatch.setattr(
        ReadOnlyToolExecutor,
        "_run_jxa",
        staticmethod(lambda payload, script, context: {"events": [later, earlier]}),
    )
    authorization = _authorize(
        "calendar_list_events",
        {
            "start_at": "2026-08-26T00:00:00-05:00",
            "end_at": "2026-08-27T00:00:00-05:00",
            "limit": 1,
        },
        tmp_path,
        role=AgentRole.PLANNER,
    )

    result = ReadOnlyToolExecutor().execute(
        authorization, default_policy_context(tmp_path)
    )

    assert result.success is True
    assert json.loads(result.output) == {"events": [earlier]}


def _authorize(
    tool_name: str,
    arguments: dict[str, object],
    root: Path,
    *,
    role: AgentRole = AgentRole.CODE_SECURITY,
) -> ToolAuthorization:
    call = ToolCall(
        call_id="call-1",
        tool_name=tool_name,
        arguments=arguments,
        requested_by=role,
    )
    return build_default_tool_broker().authorize(call, default_policy_context(root))


def test_runtime_executor_returns_only_non_secret_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command == (
            "/usr/sbin/sysctl",
            "-n",
            "machdep.cpu.brand_string",
            "hw.model",
            "hw.memsize",
        )
        assert kwargs["timeout"] == 1.0
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Apple M5\nMac17,3\n17179869184\n",
        )

    monkeypatch.setattr("aegis_core.tools.execution.subprocess.run", fake_run)
    authorization = _authorize("system_describe_runtime", {}, tmp_path)

    result = ReadOnlyToolExecutor().execute(authorization, default_policy_context(tmp_path))

    assert result.success is True
    assert set(json.loads(result.output)) == {
        "architecture",
        "chip",
        "hardware_model",
        "macos_version",
        "memory_bytes",
        "operating_system",
        "os_release",
        "python",
    }


def test_runtime_executor_keeps_base_metadata_when_sysctl_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        raise OSError("unavailable")

    monkeypatch.setattr("aegis_core.tools.execution.subprocess.run", fail)
    authorization = _authorize("system_describe_runtime", {}, tmp_path)

    result = ReadOnlyToolExecutor().execute(authorization, default_policy_context(tmp_path))

    assert result.success is True
    assert set(json.loads(result.output)) == {
        "architecture",
        "macos_version",
        "operating_system",
        "os_release",
        "python",
    }


def test_power_executor_returns_sanitized_battery_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command == ("/usr/bin/pmset", "-g", "batt")
        assert kwargs["timeout"] == 1.0
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                "Now drawing from 'AC Power'\n"
                " -InternalBattery-0 (id=secret)\t98%; discharging; "
                "8:00 remaining present: true\n"
            ),
        )

    monkeypatch.setattr("aegis_core.tools.execution.subprocess.run", fake_run)
    authorization = _authorize(
        "system_power_status",
        {},
        tmp_path,
        role=AgentRole.PLANNER,
    )

    result = ReadOnlyToolExecutor().execute(authorization, default_policy_context(tmp_path))

    assert result.success is True
    assert json.loads(result.output) == {
        "battery_percent": 98,
        "battery_present": True,
        "battery_state": "discharging",
        "power_source": "ac",
        "time_remaining_minutes": 480,
    }
    assert "secret" not in result.output
    assert result.metadata == {"source": "local_power"}


def test_power_executor_handles_a_mac_without_an_internal_battery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "aegis_core.tools.execution.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="Now drawing from 'AC Power'\n"
        ),
    )
    authorization = _authorize(
        "system_power_status",
        {},
        tmp_path,
        role=AgentRole.PLANNER,
    )

    result = ReadOnlyToolExecutor().execute(authorization, default_policy_context(tmp_path))

    assert result.success is True
    assert json.loads(result.output) == {
        "battery_present": False,
        "power_source": "ac",
    }


def test_power_executor_fails_closed_on_an_invalid_percentage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "aegis_core.tools.execution.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            stdout="Now drawing from 'Battery Power'\n battery 999%; discharging;\n",
        ),
    )
    authorization = _authorize(
        "system_power_status",
        {},
        tmp_path,
        role=AgentRole.PLANNER,
    )

    result = ReadOnlyToolExecutor().execute(authorization, default_policy_context(tmp_path))

    assert result.success is False
    assert result.error_code == "io_error"


def test_storage_executor_returns_only_bounded_capacity_statistics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_statvfs(path: str) -> SimpleNamespace:
        assert path == "/"
        return SimpleNamespace(
            f_bavail=125_000_000,
            f_blocks=500_000_000,
            f_bsize=1_000,
            f_frsize=1_000,
        )

    monkeypatch.setattr("aegis_core.tools.execution.os.statvfs", fake_statvfs)
    authorization = _authorize(
        "system_storage_status",
        {},
        tmp_path,
        role=AgentRole.PLANNER,
    )

    result = ReadOnlyToolExecutor().execute(authorization, default_policy_context(tmp_path))

    assert result.success is True
    assert json.loads(result.output) == {
        "available_bytes": 125_000_000_000,
        "total_bytes": 500_000_000_000,
        "used_bytes": 375_000_000_000,
    }
    assert result.metadata == {"source": "local_storage"}


def test_storage_executor_fails_closed_on_inconsistent_statistics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "aegis_core.tools.execution.os.statvfs",
        lambda path: SimpleNamespace(
            f_bavail=101,
            f_blocks=100,
            f_bsize=4_096,
            f_frsize=4_096,
        ),
    )
    authorization = _authorize(
        "system_storage_status",
        {},
        tmp_path,
        role=AgentRole.PLANNER,
    )

    result = ReadOnlyToolExecutor().execute(authorization, default_policy_context(tmp_path))

    assert result.success is False
    assert result.error_code == "io_error"


def test_file_executor_reads_bounded_utf8_inside_workspace(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("abcdef", encoding="utf-8")
    authorization = _authorize(
        "filesystem_read_text", {"path": "notes.txt", "max_bytes": 4}, tmp_path
    )

    result = ReadOnlyToolExecutor().execute(authorization, default_policy_context(tmp_path))

    assert result.success is True
    assert result.output == "abcd"
    assert result.metadata == {"path": "notes.txt", "bytes_read": 4, "truncated": True}


def test_web_research_executor_returns_bounded_client_result(tmp_path: Path) -> None:
    class FakeWebClient:
        closed = False

        def research(self, query: str, *, max_results: int) -> list[dict[str, str]]:
            assert query == "NVIDIA NIM updates"
            assert max_results == 2
            return [{"url": "https://example.com", "title": "Update", "content": "Current"}]

        def close(self) -> None:
            self.closed = True

    client = FakeWebClient()
    authorization = _authorize(
        "web_research",
        {"query": "NVIDIA NIM updates", "max_results": 2},
        tmp_path,
        role=AgentRole.PLANNER,
    )

    result = ReadOnlyToolExecutor(web_client_factory=lambda: client).execute(
        authorization, default_policy_context(tmp_path)
    )

    assert result.success is True
    assert json.loads(result.output)["results"][0]["content"] == "Current"
    assert client.closed is True


def test_mail_send_uses_fixed_jxa_stdin_only_after_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}

    def fake_run(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=b'{"sent":true,"recipient_count":1}',
        )

    monkeypatch.setattr("aegis_core.tools.execution.subprocess.run", fake_run)
    authorization = ToolAuthorization(
        call_id="call-mail",
        tool_name="mail_send_message",
        call_digest="e" * 64,
        decision=PolicyDecision.ALLOW,
        reason_code="confirmation_consumed",
        normalized_arguments={
            "recipients": ["owner@example.com"],
            "subject": "Estado",
            "body": "Contenido privado",
        },
    )

    result = ReadOnlyToolExecutor().execute(authorization, default_policy_context(tmp_path))

    assert result.success is True
    assert observed["command"] == ("/usr/bin/osascript", "-l", "JavaScript")
    assert "Contenido privado" not in " ".join(observed["command"])
    assert b"Contenido privado" in observed["input"]
    assert observed["stderr"] == subprocess.DEVNULL
    assert observed["timeout"] == 12.0


def test_application_open_requires_consumed_confirmation(tmp_path: Path) -> None:
    forged = ToolAuthorization(
        call_id="call-app",
        tool_name="application_open",
        call_digest="f" * 64,
        decision=PolicyDecision.ALLOW,
        reason_code="policy_allowed",
        normalized_arguments={"bundle_identifier": "com.apple.Safari"},
    )

    result = ReadOnlyToolExecutor().execute(forged, default_policy_context(tmp_path))

    assert result.success is False
    assert result.error_code == "access_denied"


def test_shortcut_run_uses_native_cli_only_after_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}

    def fake_run(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout=b"ok")

    monkeypatch.setattr("aegis_core.tools.execution.subprocess.run", fake_run)
    authorization = ToolAuthorization(
        call_id="call-shortcut",
        tool_name="shortcut_run",
        call_digest="a" * 64,
        decision=PolicyDecision.ALLOW,
        reason_code="confirmation_consumed",
        normalized_arguments={"name": "Preparar reunión"},
    )

    result = ReadOnlyToolExecutor().execute(authorization, default_policy_context(tmp_path))

    assert result.success is True
    assert observed["command"] == ("/usr/bin/shortcuts", "run", "Preparar reunión")
    assert observed["stdin"] == subprocess.DEVNULL
    assert observed["stderr"] == subprocess.DEVNULL
    assert json.loads(result.output) == {"name": "Preparar reunión", "completed": True}


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


def test_confirmed_network_call_probes_only_explicit_ports(tmp_path: Path) -> None:
    probes: list[tuple[str, int, float]] = []

    def connector(address: str, port: int, timeout: float) -> str:
        probes.append((address, port, timeout))
        return "open" if port == 443 else "closed"

    authorization = ToolAuthorization(
        call_id="call-network",
        tool_name="network_discover_hosts",
        call_digest="c" * 64,
        decision=PolicyDecision.ALLOW,
        reason_code="confirmation_consumed",
        normalized_arguments={"target": "127.0.0.1/32", "ports": [80, 443]},
    )

    result = ReadOnlyToolExecutor(tcp_connector=connector).execute(
        authorization, default_policy_context(tmp_path)
    )

    assert result.success is True
    assert json.loads(result.output) == {
        "hosts": [{"address": "127.0.0.1", "open_ports": [443]}],
        "ports": [80, 443],
        "target": "127.0.0.1/32",
    }
    assert [(address, port) for address, port, _ in probes] == [
        ("127.0.0.1", 80),
        ("127.0.0.1", 443),
    ]
    assert result.metadata == {
        "endpoints_scanned": 2,
        "hosts_scanned": 1,
        "responsive_hosts": 1,
        "source": "tcp_connect",
    }


def test_network_executor_revalidates_scope_after_forged_authorization(tmp_path: Path) -> None:
    authorization = ToolAuthorization(
        call_id="call-network",
        tool_name="network_discover_hosts",
        call_digest="c" * 64,
        decision=PolicyDecision.ALLOW,
        reason_code="confirmation_consumed",
        normalized_arguments={"target": "8.8.8.8/32", "ports": [443]},
    )

    result = ReadOnlyToolExecutor(tcp_connector=lambda *_: "open").execute(
        authorization, default_policy_context(tmp_path)
    )

    assert result.success is False
    assert result.error_code == "access_denied"


def test_network_executor_requires_consumed_confirmation_even_when_forged_allow(
    tmp_path: Path,
) -> None:
    authorization = ToolAuthorization(
        call_id="call-network",
        tool_name="network_discover_hosts",
        call_digest="c" * 64,
        decision=PolicyDecision.ALLOW,
        reason_code="policy_allowed",
        normalized_arguments={"target": "127.0.0.1/32", "ports": [443]},
    )

    result = ReadOnlyToolExecutor(tcp_connector=lambda *_: "open").execute(
        authorization, default_policy_context(tmp_path)
    )

    assert result.success is False
    assert result.error_code == "access_denied"


def test_confirmed_terminal_template_runs_only_fixed_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}

    def fake_run(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout=b"## main\n M README.md\n")

    monkeypatch.setattr("aegis_core.tools.execution.subprocess.run", fake_run)
    authorization = ToolAuthorization(
        call_id="call-terminal",
        tool_name="terminal_run_template",
        call_digest="d" * 64,
        decision=PolicyDecision.ALLOW,
        reason_code="confirmation_consumed",
        normalized_arguments={"template": "git_status"},
    )

    result = ReadOnlyToolExecutor().execute(authorization, default_policy_context(tmp_path))

    assert result.success is True
    assert result.output == "## main\n M README.md\n"
    assert observed["command"] == (
        "/usr/bin/git",
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "core.hooksPath=/dev/null",
        "status",
        "--short",
        "--branch",
        "--untracked-files=no",
    )
    assert observed["cwd"] == tmp_path
    assert observed["timeout"] == 3.0
    assert observed["check"] is False
    assert observed["stdin"] == subprocess.DEVNULL
    assert observed["stderr"] == subprocess.DEVNULL
    assert result.metadata == {
        "bytes_read": 21,
        "return_code": 0,
        "template": "git_status",
        "truncated": False,
    }


def test_confirmed_security_posture_runs_only_fixed_native_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[tuple[tuple[str, ...], dict[str, object]]] = []
    responses = {
        "/usr/bin/csrutil": (0, b"System Integrity Protection status: enabled.\n"),
        "/usr/sbin/spctl": (0, b"assessments enabled\n"),
        "/usr/bin/fdesetup": (0, b"true\n"),
        "/usr/libexec/ApplicationFirewall/socketfilterfw": (
            0,
            b"Firewall is disabled. (State = 0)\n",
        ),
    }

    def fake_run(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed.append((command, kwargs))
        return_code, stdout = responses[command[0]]
        return subprocess.CompletedProcess(command, return_code, stdout=stdout)

    monkeypatch.setattr("aegis_core.tools.execution.subprocess.run", fake_run)
    authorization = ToolAuthorization(
        call_id="call-posture",
        tool_name="terminal_run_template",
        call_digest="d" * 64,
        decision=PolicyDecision.ALLOW,
        reason_code="confirmation_consumed",
        normalized_arguments={"template": "security_posture"},
    )

    result = ReadOnlyToolExecutor().execute(authorization, default_policy_context(tmp_path))

    assert result.success is True
    assert json.loads(result.output) == {
        "filevault": "enabled",
        "firewall": "disabled",
        "gatekeeper": "enabled",
        "sip": "enabled",
    }
    assert [command for command, _ in observed] == [
        ("/usr/bin/csrutil", "status"),
        ("/usr/sbin/spctl", "--status"),
        ("/usr/bin/fdesetup", "isactive"),
        (
            "/usr/libexec/ApplicationFirewall/socketfilterfw",
            "--getglobalstate",
        ),
    ]
    assert all(kwargs["cwd"] == tmp_path for _, kwargs in observed)
    assert all(kwargs["timeout"] == 3.0 for _, kwargs in observed)
    assert all(kwargs["stdin"] == subprocess.DEVNULL for _, kwargs in observed)
    assert all(kwargs["stderr"] == subprocess.DEVNULL for _, kwargs in observed)
    assert result.metadata == {
        "bytes_read": len(result.output.encode("utf-8")),
        "controls": 4,
        "template": "security_posture",
        "truncated": False,
        "unavailable_controls": 0,
    }


@pytest.mark.parametrize(
    ("label", "output", "expected"),
    (
        ("sip", b"System Integrity Protection status: disabled.\n", "disabled"),
        ("gatekeeper", b"assessments disabled\n", "disabled"),
        ("filevault", b"false\n", "disabled"),
        ("firewall", b"Firewall is enabled. (State = 1)\n", "enabled"),
        ("sip", b"unexpected native output\n", "unavailable"),
        ("unknown", b"true\n", "unavailable"),
    ),
)
def test_security_posture_normalizes_only_known_native_outputs(
    label: str, output: bytes, expected: str
) -> None:
    assert _security_control_state(label, output) == expected


def test_terminal_executor_requires_consumed_confirmation_even_when_forged_allow(
    tmp_path: Path,
) -> None:
    authorization = ToolAuthorization(
        call_id="call-terminal",
        tool_name="terminal_run_template",
        call_digest="d" * 64,
        decision=PolicyDecision.ALLOW,
        reason_code="policy_allowed",
        normalized_arguments={"template": "list_processes"},
    )

    result = ReadOnlyToolExecutor().execute(authorization, default_policy_context(tmp_path))

    assert result.success is False
    assert result.error_code == "access_denied"


def test_terminal_template_output_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "aegis_core.tools.execution.subprocess.run",
        lambda command, **_: subprocess.CompletedProcess(command, 0, stdout=b"x" * 20_000),
    )
    authorization = ToolAuthorization(
        call_id="call-terminal",
        tool_name="terminal_run_template",
        call_digest="d" * 64,
        decision=PolicyDecision.ALLOW,
        reason_code="confirmation_consumed",
        normalized_arguments={"template": "list_processes"},
    )

    result = ReadOnlyToolExecutor().execute(authorization, default_policy_context(tmp_path))

    assert result.success is True
    assert len(result.output.encode("utf-8")) == 16_384
    assert result.metadata["truncated"] is True


@pytest.mark.parametrize(
    ("connect_result", "expected"),
    ((0, "open"), (errno.ECONNREFUSED, "closed"), (errno.ETIMEDOUT, "unreachable")),
)
def test_native_tcp_probe_classifies_bounded_connect_results(
    monkeypatch: pytest.MonkeyPatch, connect_result: int, expected: str
) -> None:
    class FakeSocket:
        def __enter__(self) -> FakeSocket:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def settimeout(self, timeout: float) -> None:
            assert timeout == 0.25

        def connect_ex(self, destination: tuple[object, ...]) -> int:
            assert destination == ("127.0.0.1", 443)
            return connect_result

    monkeypatch.setattr("aegis_core.tools.execution.socket.socket", lambda *_: FakeSocket())

    assert ReadOnlyToolExecutor._probe_tcp("127.0.0.1", 443, 0.25) == expected
