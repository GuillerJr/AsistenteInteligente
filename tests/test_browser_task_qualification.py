"""The real-browser qualification entry point must remain opt-in and redact failures."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from aegis_core.contracts import Capability
from aegis_core.mcp.client import McpServerConfiguration
from aegis_core.mcp.integrations import BrowserSessionConfiguration, IntegrationScope


@pytest.fixture
def harness():
    path = Path(__file__).resolve().parents[1] / "script/qualify_browser_tasks.py"
    spec = importlib.util.spec_from_file_location("browser_task_qualification", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("arguments,code", [([], 2), (["--help"], 0)])
def test_cli_never_opens_browser_without_explicit_run(harness, monkeypatch, arguments, code):
    qualification = AsyncMock(side_effect=AssertionError("qualification_must_not_start"))
    monkeypatch.setattr(harness, "qualify", qualification)
    monkeypatch.setattr(harness.sys, "argv", ["qualify_browser_tasks.py", *arguments])
    with pytest.raises(SystemExit) as exit_info:
        harness.main()
    assert exit_info.value.code == code
    qualification.assert_not_called()


def test_failure_report_does_not_emit_peer_content_or_exception_details(
    harness, monkeypatch, capsys
):
    qualification = AsyncMock(side_effect=RuntimeError("synthetic private token and peer payload"))
    monkeypatch.setattr(harness, "qualify", qualification)
    monkeypatch.setattr(harness.sys, "argv", ["qualify_browser_tasks.py", "--run"])
    with pytest.raises(SystemExit) as exit_info:
        harness.main()
    assert exit_info.value.code == 1
    qualification.assert_awaited_once()
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["passed"] is False
    assert result["failure_code"] == "qualification_failed"
    assert result["extension_consent_not_exercised"] is True
    assert result["certifies"] == "no_success_claim"
    assert "synthetic private" not in captured.out + captured.err


def configuration(tmp_path, *, mode="extension", endpoint=None, origin="https://fixture.test"):
    return McpServerConfiguration(
        server_id="playwright",
        command=tmp_path / "synthetic-node",
        capability=Capability.WEB_READ,
        working_directory=tmp_path,
        integration=IntegrationScope(provider="playwright", browser_origins={origin}),
        browser_session=BrowserSessionConfiguration(
            mode=mode,
            cdp_endpoint=endpoint,
            selected_tab_url=origin + "/form",
            remote_content_allowed=True,
        ),
        read_only=False,
        approved_tools={"browser_snapshot": "b" * 64},
        artifact_sha256={str(tmp_path / "synthetic-peer"): "a" * 64},
    )


@pytest.mark.parametrize(
    "task_mode,source_mode,source_endpoint",
    [
        ("extension", "extension", None),
        ("chrome", "cdp", "chrome"),
    ],
)
def test_fixture_override_preserves_scope_url_and_pins_after_verifying_source_transport(
    harness, monkeypatch, tmp_path, task_mode, source_mode, source_endpoint
):
    original = configuration(tmp_path, mode=source_mode, endpoint=source_endpoint)
    calls = []

    def local_peer(scope, **arguments):
        calls.append((scope, arguments))
        return original.model_copy(update={"browser_session": arguments["browser_session"]})

    class SyntheticPeer:
        def __init__(self, value):
            self.configuration = value

    monkeypatch.setattr(harness, "local_peer_configuration", local_peer)
    monkeypatch.setattr(harness, "ObservedFixturePeer", SyntheticPeer)
    endpoint = "http://127.0.0.1:57319"
    factory = harness.FixturePeerFactory(
        endpoint, tmp_path, tmp_path / "packages", tmp_path / "node", task_mode=task_mode
    )
    peer = factory(original)
    assert len(calls) == 1
    assert calls[0][0] == original.integration
    actual = peer.configuration
    assert actual.browser_session.mode == "cdp"
    assert actual.browser_session.cdp_endpoint == endpoint
    assert actual.browser_session.selected_tab_url == "https://fixture.test/form"
    assert actual.browser_session.remote_content_allowed is True
    assert actual.artifact_sha256 == original.artifact_sha256
    assert actual.approved_tools == original.approved_tools
    assert actual.server_id == original.server_id
    assert factory.source_transports == [(source_mode, source_endpoint)]
    assert factory.peers == [peer]


@pytest.mark.parametrize(
    "task_mode,source_mode,source_endpoint,origin,reason",
    [
        ("chrome", "extension", None, "https://fixture.test", "chrome_task_transport_not_cdp"),
        (
            "chrome",
            "cdp",
            "http://127.0.0.1:9222",
            "https://fixture.test",
            "chrome_task_channel_not_literal_chrome",
        ),
        ("extension", "cdp", "chrome", "https://fixture.test", "unexpected_production_mode"),
        ("chrome", "cdp", "chrome", "https://other.test", "non_fixture_origin_scope"),
    ],
)
def test_invalid_task_source_never_constructs_a_peer(
    harness, monkeypatch, tmp_path, task_mode, source_mode, source_endpoint, origin, reason
):
    def forbidden(*args, **kwargs):
        raise AssertionError("transport_override_must_not_start")

    monkeypatch.setattr(harness, "local_peer_configuration", forbidden)
    monkeypatch.setattr(harness, "ObservedFixturePeer", forbidden)
    factory = harness.FixturePeerFactory(
        "http://127.0.0.1:57319",
        tmp_path,
        tmp_path / "packages",
        tmp_path / "node",
        task_mode=task_mode,
    )
    source = configuration(tmp_path, mode=source_mode, endpoint=source_endpoint, origin=origin)
    with pytest.raises(AssertionError, match=reason):
        factory(source)
    assert factory.peers == [] and factory.source_transports == []


def test_fixture_transport_cannot_replace_peer_artifact_pins(harness, monkeypatch, tmp_path):
    source = configuration(tmp_path, mode="cdp", endpoint="chrome")

    def changed_pin(scope, **arguments):
        return source.model_copy(update={"artifact_sha256": {str(tmp_path / "peer"): "c" * 64}})

    monkeypatch.setattr(harness, "local_peer_configuration", changed_pin)
    factory = harness.FixturePeerFactory(
        "http://127.0.0.1:57319",
        tmp_path,
        tmp_path / "packages",
        tmp_path / "node",
        task_mode="chrome",
    )
    with pytest.raises(AssertionError, match="fixture_pin_changed"):
        factory(source)
    assert factory.peers == [] and factory.source_transports == []


def test_fixture_override_cannot_supply_missing_remote_content_consent(
    harness, monkeypatch, tmp_path
):
    source = configuration(tmp_path, mode="cdp", endpoint="chrome")
    source = source.model_copy(
        update={
            "browser_session": source.browser_session.model_copy(
                update={"remote_content_allowed": False}
            )
        }
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("transport_override_must_not_start")

    monkeypatch.setattr(harness, "local_peer_configuration", forbidden)
    factory = harness.FixturePeerFactory(
        "http://127.0.0.1:57319",
        tmp_path,
        tmp_path / "packages",
        tmp_path / "node",
        task_mode="chrome",
    )
    with pytest.raises(AssertionError, match="fixture_remote_content_not_approved"):
        factory(source)
    assert factory.peers == [] and factory.source_transports == []


def test_chrome_mode_failure_does_not_claim_owner_channel_or_permission_qualification(
    harness, monkeypatch, capsys
):
    qualification = AsyncMock(side_effect=RuntimeError("chrome_task_transport_not_cdp"))
    monkeypatch.setattr(harness, "qualify", qualification)
    monkeypatch.setattr(
        harness.sys,
        "argv",
        [
            "qualify_browser_tasks.py",
            "--run",
            "--task-mode",
            "chrome",
        ],
    )
    with pytest.raises(SystemExit):
        harness.main()
    result = json.loads(capsys.readouterr().out)
    assert result["passed"] is False
    assert result["task_mode"] == "chrome"
    assert result["chrome_channel_discovery_not_exercised"] is True
    assert result["chrome_permission_prompt_not_exercised"] is True
    assert result["extension_consent_not_exercised"] is True
    assert result["certifies"] == "no_success_claim"
