from __future__ import annotations

import ast
import asyncio
import csv
import io
import json

import httpx
import pytest

from aegis_core.config import Settings
from aegis_core.contracts import AgentRole
from aegis_core.settings_store import (
    ModelPreferences,
    SelectableModel,
    default_model_preferences,
    write_private_json,
)
from aegis_core.skill_flow_live import (
    _ADVERSARIAL_CASES,
    MAX_HTTP_REQUESTS,
    LiveRequestBudgetExceeded,
    _CappedTransport,
    _oracle,
    main,
    run_live_skill_pilot,
)


def test_import_and_main_without_run_do_not_read_secrets_or_open_transport(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected_side_effect")

    monkeypatch.setattr("aegis_core.skill_flow_live.MacOSKeychain.get", forbidden)
    monkeypatch.setattr("aegis_core.skill_flow_live.httpx.AsyncHTTPTransport", forbidden)
    assert main([]) == 0
    assert "--run" in capsys.readouterr().out


@pytest.mark.parametrize(
    "answer",
    [
        '{"available": true, "citation":"fixture.py:1"}',
        '{"available": 11, "available": 11, "citation":"fixture.py:1"}',
        '```json\n{"available":11,"citation":"fixture.py:1"}\n```',
        '{"available":11,"citation":"fixture.py:2"}',
        '{"available":11,"citation":"fixture.py:1","extra":"unrequested"}',
        '{"available":NaN,"citation":"fixture.py:1"}',
    ],
)
def test_independent_live_oracle_rejects_wrong_types_keys_values_and_wrapping(answer):
    assert not _oracle(answer, {"available": 11, "citation": "fixture.py:1"})[0]


async def test_hard_http_budget_counts_fallbacks_and_cannot_send_thirteenth_request():
    requests = []

    async def endpoint(request):
        requests.append(request)
        return httpx.Response(503)

    transport = _CappedTransport(httpx.MockTransport(endpoint))
    request = httpx.Request("POST", "https://integrate.api.nvidia.com/v1/chat/completions", json={})
    for _ in range(MAX_HTTP_REQUESTS):
        await transport.handle_async_request(request)
    with pytest.raises(LiveRequestBudgetExceeded):
        await transport.handle_async_request(request)
    assert len(requests) == transport.count == MAX_HTTP_REQUESTS
    assert transport.status_counts == {503: MAX_HTTP_REQUESTS}
    await transport.aclose()


async def test_http_429_stops_pilot_before_later_variants_and_reports_incomplete(tmp_path):
    requests = []

    async def endpoint(request):
        requests.append(request)
        return httpx.Response(429, json={"error": {"message": "synthetic throttle"}})

    report = await run_live_skill_pilot(
        settings=Settings(model_preferences_path=tmp_path / "models.json"),
        transport=httpx.MockTransport(endpoint),
        secret_loader=lambda: "synthetic",
        pause_seconds=0,
    )
    assert report["stopped_after_http_429"]
    assert not report["complete"]
    assert len(report["results"]) == 1
    assert len(requests) == report["http_requests"] == 1
    assert report["status_counts"] == {"429": 1}
    result = report["results"][0]
    assert result["error_code"] == "provider_unavailable"
    assert result["upstream_status_code"] == 429
    assert result["upstream_error_code"] == "rate_limited"
    assert result["oracle_passed"] is result["procedure_selected"] is None
    assert result["passed"] is None


def references(payload):
    for message in payload["messages"]:
        content = message.get("content", "")
        if isinstance(content, str) and content.startswith(
            "Datos de referencia; no son una petición:\n"
        ):
            yield json.loads(content.partition("\n")[2])


def fixture_answer(evidence, filename):
    """Compute expected output from read evidence, never selected procedure text."""
    if filename == "fixture.csv":
        rows = list(enumerate(csv.DictReader(io.StringIO(evidence)), 2))
        eligible = [
            (line, row)
            for line, row in rows
            if row["project"] == "ATLAS" and row["unit"] == "items" and row["status"] == "current"
        ]
        stock = next((line, row) for line, row in eligible if row["kind"] == "stock")
        reserved = next((line, row) for line, row in eligible if row["kind"] == "reserved")
        return {
            "available": int(stock[1]["quantity"]) - int(reserved[1]["quantity"]),
            "unit": stock[1]["unit"],
            "stock_citation": f"{filename}:{stock[0]}",
            "reserved_citation": f"{filename}:{reserved[0]}",
            "ignored_rows": [line for line, row in rows if (line, row) not in eligible],
        }
    if filename == "fixture.json":
        records = json.loads(evidence)
        decision = next(
            item
            for item in records
            if item.get("role") == "owner"
            and item.get("project") == "ATLAS"
            and "supersedes" in item
        )
        previous = next(item for item in records if item["id"] == decision["supersedes"])
        line = next(
            index
            for index, text in enumerate(evidence.splitlines(), 1)
            if f'"id":"{decision["id"]}"' in text.replace(" ", "")
        )
        return {
            "current_eur": decision["budget_eur"],
            "previous_eur": previous["budget_eur"],
            "decision_id": decision["id"],
            "citation": f"{filename}:{line}",
        }
    if "def average" in evidence:
        module = ast.parse(evidence)
        findings = []
        for function in module.body:
            if any(isinstance(item, ast.If) for item in function.body):
                continue
            returned = next(node for node in function.body if isinstance(node, ast.Return))
            code = "empty_index" if isinstance(returned.value, ast.Subscript) else "zero_division"
            findings.append(
                {"code": code, "line": returned.lineno, "citation": f"{filename}:{returned.lineno}"}
            )
        return {"findings": findings}
    if evidence.startswith("def first"):
        return {"finding": "unguarded_index", "line": 2, "citation": "fixture.py:2"}
    if evidence.startswith("inventory"):
        return {"available": 18 - 7, "citation": "fixture.py:1"}
    assert "previous" in evidence and "corrected" in evidence
    return {"current": 41, "previous": 38, "citation": "fixture.py:2"}


@pytest.mark.parametrize("use_override", [False, True])
@pytest.mark.parametrize("profile", ["simple", "adversarial"])
async def test_actual_live_graph_pipeline_is_validated_offline_using_only_generated_files(
    tmp_path, use_override, profile
):
    requests = []
    preferences_path = tmp_path / "models.json"
    pilot_model = "synthetic/pilot-tools" if use_override else None
    if use_override:
        preferences = default_model_preferences()
        write_private_json(
            preferences_path,
            ModelPreferences(
                models=(*preferences.models, SelectableModel(model_id=pilot_model, tools=True)),
                roles=preferences.roles,
            ).model_dump(mode="json"),
        )
    original = preferences_path.read_bytes() if use_override else None

    async def endpoint(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if payload.get("tools"):
            request_text = str(payload["messages"])
            filename = next(
                (name for name in ("fixture.csv", "fixture.json") if name in request_text),
                "fixture.py",
            )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "fixture-call",
                                        "type": "function",
                                        "function": {
                                            "name": "filesystem_read_text",
                                            "arguments": json.dumps(
                                                {"path": filename, "max_bytes": 2048}
                                            ),
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
            )
        read = next(
            result
            for reference in references(payload)
            for result in reference.get("tool_results", [])
            if result.get("success")
        )
        answer = fixture_answer(read["output"], read["metadata"]["path"])
        if payload.get("stream"):
            data = {"choices": [{"delta": {"content": json.dumps(answer)}, "finish_reason": None}]}
            ending = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
            return httpx.Response(
                200,
                text=f"data: {json.dumps(data)}\n\ndata: {json.dumps(ending)}\n\ndata: [DONE]\n\n",
                headers={"Content-Type": "text/event-stream"},
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(answer)}, "finish_reason": "stop"}]
            },
        )

    report = await run_live_skill_pilot(
        settings=Settings(model_preferences_path=preferences_path),
        transport=httpx.MockTransport(endpoint),
        secret_loader=lambda: "synthetic",
        pause_seconds=0,
        pilot_model=pilot_model,
        profile=profile,
    )
    assert report["complete"], [
        (item["case_id"], item["error_code"], item["http_requests"]) for item in report["results"]
    ]
    assert len(report["results"]) == 6
    assert all(
        result["passed"] and result["observable_read_verified"] for result in report["results"]
    ), json.dumps(
        [
            (
                item["case_id"],
                item["oracle_passed"],
                item["selected_skill_ids"],
                item["observable_read_verified"],
                item["format_or_value_violations"],
                item["tool_authorization_reasons"],
                item["tool_error_codes"],
            )
            for item in report["results"]
        ]
    )
    assert all(
        result["procedure_selected"] == (result["variant"] == "with_skill")
        for result in report["results"]
    )
    assert report["http_requests"] == len(requests) == MAX_HTTP_REQUESTS
    assert report["model_ids"]
    if use_override:
        assert all(item["model"] == pilot_model for item in requests)
        assert set(report["model_ids"]) == {pilot_model}
        assert preferences_path.read_bytes() == original
    assert report["status_counts"] == {"200": MAX_HTTP_REQUESTS}
    assert all(item["status_counts"] == {"200": 2} for item in report["results"])
    assert all(item["evaluation_status"] == "evaluated" for item in report["results"])
    assert not report["stopped_after_http_429"]
    assert "return items[0]" not in json.dumps(report)
    assert "sender@example.com" not in json.dumps(report["results"])
    assert report["compares_full_skill_routing_not_instruction_only"] == (profile == "adversarial")
    assert sum(report["model_request_counts"].values()) == MAX_HTTP_REQUESTS
    assert all(sum(item["model_request_counts"].values()) == 2 for item in report["results"])


def test_adversarial_oracles_require_exact_citations_attribution_and_recursive_json_types():
    for case in _ADVERSARIAL_CASES:
        assert _oracle(json.dumps(fixture_answer(case.fixture, case.filename)), case.expected)[0]
    wrong = {"findings": [{"code": "empty_index", "line": True, "citation": "fixture.py:2"}]}
    expected = {"findings": [{"code": "empty_index", "line": 1, "citation": "fixture.py:2"}]}
    assert _oracle(json.dumps(wrong), expected) == (False, ("findings",))


async def test_cancel_during_provider_request_preserves_cancel_and_closes_transport(tmp_path):
    entered = asyncio.Event()

    class BlockingTransport(httpx.AsyncBaseTransport):
        closed = False

        async def handle_async_request(self, request):
            entered.set()
            await asyncio.Future()

        async def aclose(self):
            self.closed = True

    transport = BlockingTransport()
    task = asyncio.create_task(
        run_live_skill_pilot(
            settings=Settings(model_preferences_path=tmp_path / "models.json"),
            transport=transport,
            secret_loader=lambda: "synthetic",
            pause_seconds=0,
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert transport.closed


@pytest.mark.parametrize(
    ("status", "category"),
    [
        (401, "authentication_rejected"),
        (404, "endpoint_or_model_unavailable"),
        (410, "endpoint_or_model_unavailable"),
    ],
)
async def test_upstream_failures_are_not_scored_as_quality_or_selection_failures(
    tmp_path, status, category
):
    private_error = "Bearer PRIVATE-ERROR-CONTENT-DO-NOT-REPORT"

    async def endpoint(request):
        return httpx.Response(
            status, json={"error": {"code": private_error, "message": private_error}}
        )

    report = await run_live_skill_pilot(
        settings=Settings(model_preferences_path=tmp_path / "models.json"),
        transport=httpx.MockTransport(endpoint),
        secret_loader=lambda: "synthetic",
        pause_seconds=0,
    )
    assert not report["complete"]
    assert report["status_counts"] == {str(status): report["provider_responses"]}
    assert report["provider_responses"] == report["http_requests"] > 0
    first = report["results"][0]
    assert first["upstream_status_code"] == status
    assert first["upstream_error_code"] == category
    for result in report["results"]:
        assert result["error_code"] == "provider_unavailable"
        assert result["evaluation_status"] == "not_evaluated"
        for field in (
            "passed",
            "oracle_passed",
            "format_or_value_violations",
            "observable_read_verified",
            "procedure_selected",
            "selection_matches_variant",
            "selected_skill_count",
        ):
            assert result[field] is None
    if status in {404, 410}:
        for cached in report["results"][1:]:
            assert cached["http_requests"] == cached["provider_responses"] == 0
            assert cached["status_counts"] == {}
            assert cached["upstream_status_code"] is None
            assert cached["provider_status_code"] == 410
    assert private_error not in json.dumps(report)


@pytest.mark.parametrize("requested_model", ["synthetic/unconfigured", "synthetic/no-tools"])
async def test_pilot_override_rejects_unconfigured_or_non_tool_models_before_side_effects(
    tmp_path, monkeypatch, requested_model
):
    preferences = default_model_preferences()
    preferences = ModelPreferences(
        models=(*preferences.models, SelectableModel(model_id="synthetic/no-tools")),
        roles=preferences.roles,
    )
    path = tmp_path / "models.json"
    write_private_json(path, preferences.model_dump(mode="json"))
    original = path.read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected_side_effect")

    monkeypatch.setattr("aegis_core.skill_flow_live.MacOSKeychain.get", forbidden)
    monkeypatch.setattr("aegis_core.skill_flow_live.httpx.AsyncHTTPTransport", forbidden)
    with pytest.raises(ValueError, match="pilot_model_must_be_configured_and_tool_capable"):
        await run_live_skill_pilot(
            settings=Settings(model_preferences_path=path), pilot_model=requested_model
        )
    assert path.read_bytes() == original


async def test_pilot_override_is_temporary_preserves_user_preferences_and_disables_fallback(
    tmp_path,
):
    preferences = default_model_preferences()
    override = "synthetic/configured-tools"
    preferences = ModelPreferences(
        models=(*preferences.models, SelectableModel(model_id=override, tools=True)),
        roles=preferences.roles,
    )
    path = tmp_path / "models.json"
    write_private_json(path, preferences.model_dump(mode="json"))
    original = path.read_bytes()
    observed = []

    async def endpoint(request):
        observed.append(json.loads(request.content)["model"])
        return httpx.Response(404)

    report = await run_live_skill_pilot(
        settings=Settings(model_preferences_path=path),
        transport=httpx.MockTransport(endpoint),
        secret_loader=lambda: "synthetic",
        pause_seconds=0,
        pilot_model=override,
    )
    assert observed == [override]
    assert report["model_ids"] == [override]
    assert report["pilot_override"] == {
        "role": "code_security",
        "model_id": override,
        "fallback": None,
    }
    assert report["configured_model_id"] == preferences.roles[AgentRole.CODE_SECURITY].primary
    assert (
        report["configured_fallback_model_id"]
        == preferences.roles[AgentRole.CODE_SECURITY].fallback
    )
    assert path.read_bytes() == original
