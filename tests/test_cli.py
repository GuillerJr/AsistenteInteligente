from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
from uuid import uuid4

import pytest

from aegis_core import cli
from aegis_core.audit_anchor import DurableAuditAnchor
from aegis_core.capability_learning import CapabilityLearningStore
from aegis_core.contracts import AgentResult, AgentRole, ToolCall
from aegis_core.tools.audit import HashChainAuditLog


class FakeKeychain:
    def __init__(self, **_: object) -> None:
        pass

    def get(self) -> str:
        return "secret-value"


class FakeAuditAnchorStore:
    def __init__(self, anchor: str = "0" * 64) -> None:
        self.anchor = anchor

    def get(self) -> str:
        return self.anchor

    def set(self, anchor: str) -> None:
        self.anchor = anchor


class FakeStatusClient:
    credential = "configured"
    suspended = False

    def __init__(self, *_: object, **__: object) -> None:
        pass

    async def call(self, method: str) -> SimpleNamespace:
        if method == "swarm.activity" and self.suspended:
            return SimpleNamespace(
                ok=False,
                payload={},
                error_code="runtime_suspended",
            )
        payloads = {
            "health": {
                "protocol_version": "1.0",
                "architecture": "arm64",
                "build_revision": "1" * 40,
            },
            "provider.status": {
                "provider": "nvidia_nim",
                "credential": self.credential,
            },
            "security.status": {"state": "intact"},
            "swarm.activity": {"agents": []},
            "plugins.status": {
                "protocol_version": "2026-07-28",
                "plugins": [],
                "tool_count": 0,
            },
            "capabilities.status": {
                "observed": 0,
                "researched": 0,
                "total": 0,
            },
        }
        return SimpleNamespace(ok=True, payload=payloads[method], error_code=None)


class FakeToolClient:
    result = AgentResult(
        role=AgentRole.CODE_SECURITY,
        model_id="fake/code-security",
        content="",
        tool_calls=(
            ToolCall(
                call_id="call-probe",
                tool_name="terminal_run_template",
                arguments={"template": "security_posture"},
                requested_by=AgentRole.CODE_SECURITY,
            ),
        ),
    )
    observed: ClassVar[dict[str, Any]] = {}

    def __init__(self, settings: object, api_key_loader: Any) -> None:
        del settings
        assert api_key_loader() == "secret-value"

    async def __aenter__(self) -> FakeToolClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    async def complete(self, **kwargs: Any) -> AgentResult:
        type(self).observed = kwargs
        return type(self).result

    async def synthesize_speech(self, text: str) -> bytes:
        type(self).observed = {"text": text}
        return b"RIFF" + bytes(4) + b"WAVE" + bytes(32)

    async def stream_speech(self, text: str):
        type(self).observed = {"text": text}
        yield bytes(20)
        yield bytes(24)


class FakeSoakClient:
    calls = 0
    health_calls = 0
    restart = False
    suspended = False

    def __init__(self, *_: object, **__: object) -> None:
        type(self).calls = 0
        type(self).health_calls = 0

    async def call(self, method: str) -> SimpleNamespace:
        type(self).calls += 1
        if method == "health":
            type(self).health_calls += 1
            pid = 101 if not self.restart or self.health_calls == 1 else 202
            payload = {
                "protocol_version": "1.0",
                "architecture": "arm64",
                "build_revision": "1" * 40,
                "pid": pid,
                "runtime_state": "suspended" if self.suspended else "active",
            }
        elif method == "runtime.info":
            payload = {"architecture": "arm64", "operating_system": "Darwin"}
        elif method == "runtime.metrics":
            payload = {
                "uptime_seconds": self.calls / 100,
                "cpu_seconds": self.calls / 1_000,
                "rss_bytes": 31 * 1_024 * 1_024,
                "peak_rss_bytes": 32 * 1_024 * 1_024,
                "thread_count": 6,
                "active_clients": 5,
                "runtime_state": "suspended" if self.suspended else "active",
            }
        elif method == "security.status":
            payload = {"state": "intact"}
        elif method == "runtime.power.status":
            payload = {
                "state": "suspended" if self.suspended else "active",
                "cause": "low_power_mode" if self.suspended else "low_power_disabled",
                "thermal_state": "nominal",
                "low_power_mode": self.suspended,
                "source_id": "01234567-89ab-cdef-0123-456789abcdef",
                "sequence": 4,
                "changed": False,
                "power_source": "battery" if self.suspended else "ac",
            }
        else:
            assert method == "swarm.activity"
            payload = {"agents": []}
        return SimpleNamespace(ok=True, payload=payload, error_code=None)


class FakeColdStartSoakClient(FakeSoakClient):
    metrics_calls = 0

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        type(self).metrics_calls = 0

    async def call(self, method: str) -> SimpleNamespace:
        response = await super().call(method)
        if method == "runtime.metrics":
            type(self).metrics_calls += 1
            rss_mebibytes = 24 if self.metrics_calls == 1 else 72
            response.payload["rss_bytes"] = rss_mebibytes * 1_024 * 1_024
            response.payload["peak_rss_bytes"] = rss_mebibytes * 1_024 * 1_024
        return response


class FakeStartupGrowthSoakClient(FakeSoakClient):
    metrics_total = 0

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)

    async def call(self, method: str) -> SimpleNamespace:
        response = await super().call(method)
        if method == "runtime.metrics":
            type(self).metrics_total += 1
            rss_mebibytes = 32 if self.metrics_total <= 2 else 44
            response.payload["rss_bytes"] = rss_mebibytes * 1_024 * 1_024
            response.payload["peak_rss_bytes"] = rss_mebibytes * 1_024 * 1_024
        return response


class FakeRecoveryClient:
    busy = False
    restarted = False

    def __init__(self, *_: object, **__: object) -> None:
        type(self).restarted = False

    async def call(self, method: str) -> SimpleNamespace:
        if method == "health":
            payload = {
                "protocol_version": "1.0",
                "architecture": "arm64",
                "build_revision": "1" * 40,
                "pid": 202 if self.restarted else 101,
            }
        elif method == "security.status":
            payload = {"state": "intact"}
        else:
            assert method == "swarm.activity"
            payload = (
                {"agents": [{"role": "router", "active_jobs": 1}]} if self.busy else {"agents": []}
            )
        return SimpleNamespace(ok=True, payload=payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("credential", ["configured", "missing", "unavailable"])
async def test_daemon_status_reports_only_provider_readiness(
    credential: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = SimpleNamespace(
        ipc_socket_path="/tmp/fake.sock",
        ipc_max_frame_bytes=65_536,
        ipc_max_message_bytes=1_048_576,
        ipc_clock_skew_seconds=30,
    )
    FakeStatusClient.credential = credential
    FakeStatusClient.suspended = False
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "_ipc_authenticator", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(cli, "IpcClient", FakeStatusClient)

    status = await cli.daemon_status()

    assert status == 0
    assert capsys.readouterr().out == (
        "status=ok protocol=1.0 architecture=arm64 build=111111111111 "
        "runtime=active security=intact "
        f"provider={credential} local_model=unavailable active_agents=0 "
        "plugins=0 mcp=2026-07-28 capabilities=0 researched=0\n"
    )


@pytest.mark.asyncio
async def test_daemon_status_reports_safe_runtime_suspension(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = SimpleNamespace(
        ipc_socket_path="/tmp/fake.sock",
        ipc_max_frame_bytes=65_536,
        ipc_max_message_bytes=1_048_576,
        ipc_clock_skew_seconds=30,
    )
    FakeStatusClient.credential = "configured"
    FakeStatusClient.suspended = True
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "_ipc_authenticator", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(cli, "IpcClient", FakeStatusClient)

    status = await cli.daemon_status()

    assert status == 0
    assert "runtime=suspended" in capsys.readouterr().out
    FakeStatusClient.suspended = False


def test_capability_cli_lists_and_forgets_local_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    directory = tmp_path / "capabilities"
    record = CapabilityLearningStore(directory).observe("Organiza estas descargas")
    assert record is not None
    monkeypatch.setattr(
        cli,
        "Settings",
        lambda: SimpleNamespace(capability_learning_directory=directory),
    )

    listed = cli.capabilities_list()
    lines = capsys.readouterr().out.splitlines()

    assert listed == 0
    payload = json.loads(lines[0])
    assert payload["gap_id"] == record.gap_id
    assert payload["goal"] == "organiza estas descargas"
    assert payload["sources"] == 0
    assert payload["readiness"] == "needs_research"
    assert payload["integration_path"] == "shortcut_workflow"
    assert payload["risk"] == "high"
    assert payload["priority_score"] == 45
    assert lines[1] == "status=ok capabilities=1 researched=0"

    inspected = cli.capabilities_inspect(record.gap_id)
    inspect_lines = capsys.readouterr().out.splitlines()

    assert inspected == 0
    blueprint = json.loads(inspect_lines[0])
    assert blueprint["gap_id"] == record.gap_id
    assert blueprint["readiness"] == "needs_research"
    assert "evidence" in blueprint
    assert inspect_lines[1].endswith("readiness=needs_research risk=high")

    planned = cli.capabilities_plan(record.gap_id)
    plan_lines = capsys.readouterr().out.splitlines()

    assert planned == 0
    dossier = json.loads("\n".join(plan_lines[:-1]))
    assert dossier["gap_id"] == record.gap_id
    assert dossier["review_gate"] == "research_required"
    assert dossier["execution_allowed"] is False
    assert plan_lines[-1].endswith("gate=research_required executable=false")

    review_path = tmp_path / "review.json"
    review_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "gap_id": record.gap_id,
                "dossier_sha256": dossier["integrity_sha256"],
                "decision": "reject_design",
                "review_gate": "research_required",
                "completed_inputs": {},
                "accepted_security_checks": [],
                "passed_acceptance_tests": [],
                "test_evidence_sha256": None,
                "security_review_reference": None,
                "rationale": "El diseño todavía no satisface la necesidad del propietario.",
                "acknowledges_no_execution": True,
            }
        ),
        encoding="utf-8",
    )
    reviewed = cli.capabilities_review(review_path)
    review_lines = capsys.readouterr().out.splitlines()

    assert reviewed == 0
    verdict = json.loads("\n".join(review_lines[:-1]))
    assert verdict["status"] == "rejected"
    assert verdict["execution_allowed"] is False
    assert review_lines[-1].endswith("review=rejected executable=false")

    forgotten = cli.capabilities_forget(record.gap_id)

    assert forgotten == 0
    assert capsys.readouterr().out == (f"status=ok capability={record.gap_id} forgotten=true\n")
    assert CapabilityLearningStore(directory).load_all() == ()

    assert cli.capabilities_plan(record.gap_id) == 1
    assert capsys.readouterr().out == "status=error reason=capability_not_found\n"


@pytest.mark.asyncio
async def test_daemon_recovery_verifies_supervised_pid_replacement(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = SimpleNamespace(
        ipc_socket_path="/tmp/fake.sock",
        ipc_max_frame_bytes=65_536,
        ipc_max_message_bytes=1_048_576,
        ipc_clock_skew_seconds=30,
    )
    killed: list[tuple[int, int]] = []
    FakeRecoveryClient.busy = False
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "_daemon_launch_agent_loaded", lambda: True)
    monkeypatch.setattr(cli, "_ipc_authenticator", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(cli, "IpcClient", FakeRecoveryClient)

    def terminate(pid: int, signum: int) -> None:
        killed.append((pid, signum))
        FakeRecoveryClient.restarted = True

    monkeypatch.setattr(cli.os, "kill", terminate)

    status = await cli.daemon_recovery(attempts=2, interval_seconds=0)

    assert status == 0
    assert killed == [(101, cli.signal.SIGTERM)]
    assert capsys.readouterr().out == "status=ok restart=verified security=intact\n"


@pytest.mark.asyncio
async def test_daemon_recovery_refuses_to_interrupt_active_agents(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = SimpleNamespace(
        ipc_socket_path="/tmp/fake.sock",
        ipc_max_frame_bytes=65_536,
        ipc_max_message_bytes=1_048_576,
        ipc_clock_skew_seconds=30,
    )
    FakeRecoveryClient.busy = True
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "_daemon_launch_agent_loaded", lambda: True)
    monkeypatch.setattr(cli, "_ipc_authenticator", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(cli, "IpcClient", FakeRecoveryClient)
    monkeypatch.setattr(
        cli.os,
        "kill",
        lambda *_args: pytest.fail("busy daemon must not be terminated"),
    )

    status = await cli.daemon_recovery(attempts=2, interval_seconds=0)

    assert status == 2
    assert capsys.readouterr().out == "status=blocked reason=daemon_busy\n"


def test_audit_anchor_recovery_requires_stopped_daemon_and_verified_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    audit_path = tmp_path / "audit.jsonl"
    audit = HashChainAuditLog(audit_path)
    audit.record_system_event(
        uuid4(),
        event_type="daemon_started",
        component="supervisor",
        data={"state": "ready"},
    )
    settings = SimpleNamespace(
        ipc_socket_path=tmp_path / "aegis.sock",
        audit_max_bytes=HashChainAuditLog.DEFAULT_MAX_BYTES,
    )
    store = FakeAuditAnchorStore()
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "MacOSAuditAnchor", lambda: store)
    monkeypatch.setattr(cli, "_daemon_launch_agent_loaded", lambda: True)

    assert cli.recover_audit_anchor(audit_path) == 2
    assert capsys.readouterr().out == (
        "status=blocked reason=daemon_service_must_be_stopped\n"
    )

    monkeypatch.setattr(cli, "_daemon_launch_agent_loaded", lambda: False)
    assert cli.recover_audit_anchor(audit_path) == 0
    assert capsys.readouterr().out == "status=ok recovery=anchored records=2\n"
    recovered = HashChainAuditLog(audit_path)
    records = recovered.verify()
    assert records[-1].event_type == "audit_anchor_recovered"
    assert records[-1].data == {"state": "operator_verified", "records_verified": 1}
    DurableAuditAnchor(store).validate_startup(recovered)


@pytest.mark.asyncio
async def test_daemon_soak_checks_runtime_and_idle_activity_per_cycle(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = SimpleNamespace(
        ipc_socket_path="/tmp/fake.sock",
        ipc_max_frame_bytes=65_536,
        ipc_max_message_bytes=1_048_576,
        ipc_clock_skew_seconds=30,
    )
    FakeSoakClient.restart = False
    FakeSoakClient.suspended = False
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "_ipc_authenticator", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(cli, "IpcClient", FakeSoakClient)

    status = await cli.daemon_soak(cycles=3, max_p95_ms=1_000)

    assert status == 0
    assert FakeSoakClient.calls == 24
    assert capsys.readouterr().out.startswith("status=ok cycles=3 mode=active p95_ms=")


@pytest.mark.asyncio
async def test_daemon_soak_excludes_one_time_handler_page_faults_from_rss_baseline(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = SimpleNamespace(
        ipc_socket_path="/tmp/fake.sock",
        ipc_max_frame_bytes=65_536,
        ipc_max_message_bytes=1_048_576,
        ipc_clock_skew_seconds=30,
    )
    FakeColdStartSoakClient.restart = False
    FakeColdStartSoakClient.suspended = False
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "_ipc_authenticator", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(cli, "IpcClient", FakeColdStartSoakClient)

    status = await cli.daemon_soak(
        cycles=3,
        max_p95_ms=1_000,
        max_rss_growth_bytes=1_024,
    )

    output = capsys.readouterr().out
    assert status == 0
    assert "rss_growth_kib=0.00" in output
    assert FakeColdStartSoakClient.metrics_calls == 4


@pytest.mark.asyncio
async def test_daemon_soak_rechecks_one_startup_high_water_mark_without_relaxing_budget(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = SimpleNamespace(
        ipc_socket_path="/tmp/fake.sock",
        ipc_max_frame_bytes=65_536,
        ipc_max_message_bytes=1_048_576,
        ipc_clock_skew_seconds=30,
    )
    FakeStartupGrowthSoakClient.restart = False
    FakeStartupGrowthSoakClient.suspended = False
    FakeStartupGrowthSoakClient.metrics_total = 0
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "_ipc_authenticator", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(cli, "IpcClient", FakeStartupGrowthSoakClient)

    status = await cli.daemon_soak(
        cycles=3,
        max_p95_ms=1_000,
        max_rss_growth_bytes=1_024,
    )

    output = capsys.readouterr().out
    assert status == 0
    assert "rss_growth_kib=0.00" in output
    assert "startup_recheck=true" in output
    assert FakeStartupGrowthSoakClient.metrics_total == 8


@pytest.mark.asyncio
async def test_daemon_soak_detects_daemon_restart(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = SimpleNamespace(
        ipc_socket_path="/tmp/fake.sock",
        ipc_max_frame_bytes=65_536,
        ipc_max_message_bytes=1_048_576,
        ipc_clock_skew_seconds=30,
    )
    FakeSoakClient.restart = True
    FakeSoakClient.suspended = False
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "_ipc_authenticator", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(cli, "IpcClient", FakeSoakClient)

    status = await cli.daemon_soak(cycles=3, max_p95_ms=1_000)

    assert status == 1
    assert capsys.readouterr().out == "status=error reason=daemon_restarted\n"


@pytest.mark.asyncio
async def test_daemon_soak_uses_safe_health_surface_while_energy_suspended(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = SimpleNamespace(
        ipc_socket_path="/tmp/fake.sock",
        ipc_max_frame_bytes=65_536,
        ipc_max_message_bytes=1_048_576,
        ipc_clock_skew_seconds=30,
    )
    FakeSoakClient.restart = False
    FakeSoakClient.suspended = True
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "_ipc_authenticator", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(cli, "IpcClient", FakeSoakClient)

    status = await cli.daemon_soak(cycles=20)

    output = capsys.readouterr().out
    assert status == 0
    assert output.startswith("status=ok cycles=20 mode=suspended p95_ms=")
    assert FakeSoakClient.calls == 105
    FakeSoakClient.suspended = False


@pytest.mark.asyncio
async def test_tool_probe_validates_function_call_without_execution(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "MacOSKeychain", FakeKeychain)
    monkeypatch.setattr(cli, "NvidiaNimClient", FakeToolClient)

    status = await cli.probe_nvidia_tools()

    assert status == 0
    assert capsys.readouterr().out == (
        "status=ok model=fake/code-security tool=terminal_run_template "
        "execution=none credential=keychain\n"
    )
    extra_body = FakeToolClient.observed["extra_body"]
    assert extra_body["tool_choice"] == "required"
    schemas = extra_body["tools"]
    assert len(schemas) == 1
    assert schemas[0]["function"]["name"] == "terminal_run_template"


@pytest.mark.asyncio
async def test_tts_probe_reports_only_bounded_metadata(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "MacOSKeychain", FakeKeychain)
    monkeypatch.setattr(cli, "NvidiaNimClient", FakeToolClient)
    timestamps = iter((10.0, 10.125))
    monkeypatch.setattr(cli.time, "perf_counter", lambda: next(timestamps))

    status = await cli.probe_nvidia_tts()

    assert status == 0
    assert FakeToolClient.observed == {"text": "Sistemas en línea."}
    assert capsys.readouterr().out == (
        "status=ok voice=Magpie-Multilingual.ES-US.Diego audio_bytes=44 "
        "chunks=2 first_audio_ms=125 mode=stream credential=keychain playback=none\n"
    )


@pytest.mark.asyncio
async def test_tool_probe_rejects_unexpected_provider_response(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "MacOSKeychain", FakeKeychain)
    monkeypatch.setattr(cli, "NvidiaNimClient", FakeToolClient)
    monkeypatch.setattr(
        FakeToolClient,
        "result",
        FakeToolClient.result.model_copy(update={"tool_calls": ()}),
    )

    status = await cli.probe_nvidia_tools()

    assert status == 1
    assert capsys.readouterr().out == "status=error reason=invalid_tool_call_response\n"


@pytest.mark.asyncio
async def test_vision_probe_uses_only_inline_synthetic_image(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "MacOSKeychain", FakeKeychain)
    monkeypatch.setattr(cli, "NvidiaNimClient", FakeToolClient)
    monkeypatch.setattr(
        FakeToolClient,
        "result",
        AgentResult(
            role=AgentRole.VISION,
            model_id="fake/vision",
            content="OK",
        ),
    )

    status = await cli.probe_nvidia_vision()

    assert status == 0
    output = capsys.readouterr().out
    assert output == "status=ok model=fake/vision input=synthetic credential=keychain\n"
    content = FakeToolClient.observed["messages"][0]["content"]
    assert content[1]["type"] == "image_url"
    data_uri = content[1]["image_url"]["url"]
    assert data_uri.startswith("data:image/png;base64,")
    assert base64.b64decode(data_uri.partition(",")[2])[25] == 2
    assert data_uri not in output


@pytest.mark.asyncio
async def test_vision_probe_rejects_empty_provider_response(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "MacOSKeychain", FakeKeychain)
    monkeypatch.setattr(cli, "NvidiaNimClient", FakeToolClient)
    monkeypatch.setattr(
        FakeToolClient,
        "result",
        AgentResult(role=AgentRole.VISION, model_id="fake/vision", content=""),
    )

    status = await cli.probe_nvidia_vision()

    assert status == 1
    assert capsys.readouterr().out == "status=error reason=invalid_vision_response\n"
