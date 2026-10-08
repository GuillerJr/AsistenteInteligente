"""The real-browser qualification entry point must remain opt-in and redact failures."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


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
