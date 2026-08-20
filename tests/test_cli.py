from __future__ import annotations

from typing import Any, ClassVar

import pytest

from aegis_core import cli
from aegis_core.contracts import AgentResult, AgentRole, ToolCall


class FakeKeychain:
    def __init__(self, **_: object) -> None:
        pass

    def get(self) -> str:
        return "secret-value"


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
