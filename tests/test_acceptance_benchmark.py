from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from aegis_core import cli
from aegis_core.acceptance_benchmark import (
    CASES_PER_CATEGORY,
    EXPECTED_CASES,
    AcceptanceCategory,
    JarvisAcceptanceBenchmark,
    _AcceptanceCase,
)


def test_acceptance_manifest_has_five_unique_cases_per_category(tmp_path: Path) -> None:
    benchmark = JarvisAcceptanceBenchmark(temporary_root=tmp_path)
    manifest = benchmark.manifest

    assert len(manifest) == EXPECTED_CASES
    assert len({case_id for case_id, _ in manifest}) == EXPECTED_CASES
    assert Counter(category for _, category in manifest) == {
        category: CASES_PER_CATEGORY for category in AcceptanceCategory
    }


@pytest.mark.asyncio
async def test_acceptance_gate_passes_all_local_contracts_without_user_data(
    tmp_path: Path,
) -> None:
    report = await JarvisAcceptanceBenchmark(temporary_root=tmp_path).run()
    payload = json.loads(report.private_json())

    assert report.gate_passed
    assert report.score == 100
    assert report.passed == EXPECTED_CASES
    assert payload["privacy"] == {
        "contains_prompt_text": False,
        "contains_target_urls": False,
        "contains_transcripts": False,
        "network_calls": 0,
    }
    serialized = report.private_json().casefold()
    assert "hola jarvis" not in serialized
    assert "owner@example.com" not in serialized
    assert "nvapi-" not in serialized
    assert str(tmp_path).casefold() not in serialized


@pytest.mark.asyncio
async def test_acceptance_failure_is_bounded_and_does_not_leak_exception(
    tmp_path: Path,
) -> None:
    benchmark = JarvisAcceptanceBenchmark(temporary_root=tmp_path)

    async def fail_with_sensitive_detail(_: object) -> None:
        raise RuntimeError("private prompt and target URL")

    first = benchmark._cases[0]
    benchmark._cases = (
        _AcceptanceCase(first.case_id, first.category, fail_with_sensitive_detail),
        *benchmark._cases[1:],
    )

    report = await benchmark.run()
    serialized = report.private_json()

    assert not report.gate_passed
    assert report.score == 96
    assert report.results[0].failure_code == "internal_error"
    assert "private prompt" not in serialized
    assert "target URL" not in serialized


@pytest.mark.asyncio
async def test_acceptance_cli_emits_one_private_json_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "JarvisAcceptanceBenchmark",
        lambda: JarvisAcceptanceBenchmark(temporary_root=tmp_path),
    )

    status = await cli.acceptance_benchmark()
    payload = json.loads(capsys.readouterr().out)

    assert status == 0
    assert payload["gate_passed"] is True
    assert payload["score"] == 100
    assert payload["total"] == EXPECTED_CASES
