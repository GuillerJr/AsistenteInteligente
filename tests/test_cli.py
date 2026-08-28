from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from aegis_core import cli
from aegis_core.contracts import AgentResult, AgentRole, ToolCall


class FakeKeychain:
    def __init__(self, **_: object) -> None:
        pass

    def get(self) -> str:
        return "secret-value"


class FakeStatusClient:
    credential = "configured"

    def __init__(self, *_: object, **__: object) -> None:
        pass

    async def call(self, method: str) -> SimpleNamespace:
        payloads = {
            "health": {"protocol_version": "1.0", "architecture": "arm64"},
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
        }
        return SimpleNamespace(ok=True, payload=payloads[method])


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

    def __init__(self, *_: object, **__: object) -> None:
        type(self).calls = 0
        type(self).health_calls = 0

    async def call(self, method: str) -> SimpleNamespace:
        type(self).calls += 1
        if method == "health":
            type(self).health_calls += 1
            pid = 101 if not self.restart or self.health_calls == 1 else 202
            payload = {"protocol_version": "1.0", "architecture": "arm64", "pid": pid}
        elif method == "runtime.info":
            payload = {"architecture": "arm64", "operating_system": "Darwin"}
        elif method == "runtime.metrics":
            payload = {
                "uptime_seconds": self.calls / 100,
                "cpu_seconds": self.calls / 1_000,
                "peak_rss_bytes": 32 * 1_024 * 1_024,
            }
        elif method == "security.status":
            payload = {"state": "intact"}
        else:
            assert method == "swarm.activity"
            payload = {"agents": []}
        return SimpleNamespace(ok=True, payload=payload)


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
        ipc_clock_skew_seconds=30,
    )
    FakeStatusClient.credential = credential
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "_ipc_authenticator", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(cli, "IpcClient", FakeStatusClient)

    status = await cli.daemon_status()

    assert status == 0
    assert capsys.readouterr().out == (
        "status=ok protocol=1.0 architecture=arm64 security=intact "
        f"provider={credential} local_model=unavailable active_agents=0 "
        "plugins=0 mcp=2026-07-28\n"
    )


@pytest.mark.asyncio
async def test_daemon_recovery_verifies_supervised_pid_replacement(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = SimpleNamespace(
        ipc_socket_path="/tmp/fake.sock",
        ipc_max_frame_bytes=65_536,
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


@pytest.mark.asyncio
async def test_daemon_soak_checks_five_surfaces_per_cycle(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = SimpleNamespace(
        ipc_socket_path="/tmp/fake.sock",
        ipc_max_frame_bytes=65_536,
        ipc_clock_skew_seconds=30,
    )
    FakeSoakClient.restart = False
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "_ipc_authenticator", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(cli, "IpcClient", FakeSoakClient)

    status = await cli.daemon_soak(cycles=3, max_p95_ms=1_000)

    assert status == 0
    assert FakeSoakClient.calls == 15
    assert capsys.readouterr().out.startswith("status=ok cycles=3 p95_ms=")


@pytest.mark.asyncio
async def test_daemon_soak_detects_daemon_restart(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = SimpleNamespace(
        ipc_socket_path="/tmp/fake.sock",
        ipc_max_frame_bytes=65_536,
        ipc_clock_skew_seconds=30,
    )
    FakeSoakClient.restart = True
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "_ipc_authenticator", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(cli, "IpcClient", FakeSoakClient)

    status = await cli.daemon_soak(cycles=3, max_p95_ms=1_000)

    assert status == 1
    assert capsys.readouterr().out == "status=error reason=daemon_restarted\n"


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
