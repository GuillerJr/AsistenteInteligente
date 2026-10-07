"""Opt-in NVIDIA pilot on generated fixtures, with a hard cap of 12 HTTP requests.

Nothing runs on import or without ``--run``. The pilot reads no personal documents,
executes no fixture code and sends no messages. Its six graph runs are one paired
measurement per task, not a statistically significant quality assessment.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from aegis_core.brain.errors import RemoteProviderUnavailableError
from aegis_core.brain.nvidia_client import NvidiaBrainClient
from aegis_core.config import Settings
from aegis_core.contracts import AgentRole, PolicyDecision, UserRequest
from aegis_core.orchestration.graph import build_swarm_graph
from aegis_core.providers.nvidia import NvidiaGlobalCooldown, NvidiaNimClient
from aegis_core.secrets import MacOSKeychain, contains_likely_secret_material
from aegis_core.settings_store import (
    ModelPreferences,
    RoleSelection,
    load_model_preferences,
    write_private_json,
)
from aegis_core.skills import (
    SkillDraft,
    SkillPolicyStore,
    SkillRegistry,
    SkillStore,
    skill_content_sha256,
)
from aegis_core.tools.broker import PolicyContext
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context
from aegis_core.tools.execution import ReadOnlyToolExecutor

MAX_HTTP_REQUESTS = 12
TOTAL_SECONDS = 180
_TRIGGER = "revisa el código del archivo fixture.py"


class LiveRequestBudgetExceeded(RuntimeError):
    pass


class _CappedTransport(httpx.AsyncBaseTransport):
    """Counts actual requests, including provider retries and model fallbacks."""

    def __init__(self, delegate: httpx.AsyncBaseTransport, *, maximum: int = MAX_HTTP_REQUESTS):
        self.delegate, self.maximum = delegate, maximum
        self.count = 0
        self.prompt_bytes = 0
        self.responses = 0
        self.status_counts: Counter[int] = Counter()
        self.last_status_code: int | None = None
        self.rate_limited = False
        self.model_ids: set[str] = set()
        self._lock = asyncio.Lock()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        async with self._lock:
            if self.rate_limited or self.count >= self.maximum:
                raise LiveRequestBudgetExceeded("synthetic_pilot_request_limit")
            self.count += 1
        try:
            value = json.loads(request.content)
            model = value.get("model")
            if isinstance(model, str) and not contains_likely_secret_material(model):
                self.model_ids.add(model)
            self.prompt_bytes += len(
                json.dumps(value.get("messages", []), ensure_ascii=False).encode()
            )
        except (TypeError, ValueError):
            pass
        response = await self.delegate.handle_async_request(request)
        self.responses += 1
        self.status_counts[response.status_code] += 1
        self.last_status_code = response.status_code
        if response.status_code == 429:
            self.rate_limited = True
        return response

    async def aclose(self) -> None:
        await self.delegate.aclose()


@dataclass(frozen=True, slots=True)
class _PilotCase:
    case_id: str
    fixture: str
    request: str
    expected: dict[str, str | int]


_CASES = (
    _PilotCase(
        "unguarded_index",
        "def first(items):\n    return items[0]\n",
        "Identifica el acceso que falla para una lista vacía. Devuelve sólo JSON con "
        "finding (unguarded_index), line (entero) y citation (fixture.py:N).",
        {"finding": "unguarded_index", "line": 2, "citation": "fixture.py:2"},
    ),
    _PilotCase(
        "inventory_arithmetic",
        "inventory = {'stock': 18, 'reserved': 7}\n",
        "Calcula disponibles como stock menos reserved. Devuelve sólo JSON con "
        "available (entero) y citation (fixture.py:N).",
        {"available": 11, "citation": "fixture.py:1"},
    ),
    _PilotCase(
        "explicit_correction",
        "previous = {'available': 38, 'superseded': True}\n"
        "corrected = {'available': 41, 'current': True}\n",
        "Distingue el valor vigente de la referencia sustituida. Devuelve sólo JSON con "
        "current (entero), previous (entero) y citation de la corrección (fixture.py:N).",
        {"current": 41, "previous": 38, "citation": "fixture.py:2"},
    ),
)


def _oracle(answer: str, expected: dict[str, str | int]) -> tuple[bool, tuple[str, ...]]:
    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_key")
            result[key] = value
        return result

    try:
        value = json.loads(
            answer,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
        )
    except (ValueError, TypeError, RecursionError):
        return False, ("json_format",)
    if not isinstance(value, dict) or set(value) != set(expected):
        return False, ("json_fields",)
    violations = tuple(
        key
        for key, wanted in expected.items()
        if type(value[key]) is not type(wanted) or value[key] != wanted
    )
    return not violations, violations


def _safe_status_code(failure: BaseException) -> int | None:
    """Read typed status metadata, never exception messages or remote error bodies."""
    current: BaseException | None = failure
    for _ in range(8):
        if current is None:
            break
        status = getattr(current, "status_code", None)
        if type(status) is int and 100 <= status <= 599:
            return status
        current = current.__cause__
    return None


def _safe_error_category(status: int | None) -> str:
    if status in {401, 403}:
        return "authentication_rejected"
    if status in {404, 410}:
        return "endpoint_or_model_unavailable"
    if status == 429:
        return "rate_limited"
    if status in {408, 504}:
        return "upstream_timeout"
    if status is not None and status >= 500:
        return "upstream_server_error"
    if status is not None and status >= 400:
        return "upstream_request_rejected"
    if status is not None:
        return "response_unusable"
    return "provider_unavailable_without_http_status"


def _public_status_counts(counts: Counter[int]) -> dict[str, int]:
    return {str(status): count for status, count in sorted(counts.items()) if count > 0}


async def _run_case(
    root: Path,
    case: _PilotCase,
    with_skill: bool,
    provider: NvidiaBrainClient,
    counter: _CappedTransport,
) -> dict[str, Any]:
    root.mkdir(mode=0o700)
    (root / "fixture.py").write_text(case.fixture, encoding="utf-8")
    broker = build_default_tool_broker()
    context = PolicyContext(
        workspace_root=root, network_scopes=default_policy_context(root).network_scopes
    )
    registry = SkillRegistry(
        broker,
        SkillStore(root / "skills"),
        builtins=(),
        policy=SkillPolicyStore(root / "skill_policy.json"),
    )
    if with_skill:
        manifest = registry.learn(
            SkillDraft(
                skill_id="synthetic-evidence-review",
                name="Revisión de evidencia sintética",
                description="Verifica el resultado solicitado contra un archivo sintético acotado.",
                role="code_security",
                trigger_phrases=(_TRIGGER,),
                instructions=(
                    "Lee exclusivamente el archivo solicitado; nunca ejecutes su código.",
                    "Relaciona cada afirmación con la línea exacta que la sustenta.",
                    "Respeta claves y tipos JSON pedidos y conserva las correcciones explícitas.",
                ),
                allowed_tools=frozenset({"filesystem_read_text"}),
                starter_tools=frozenset({"filesystem_read_text"}),
            )
        )
        registry.set_remote_consent(manifest.skill_id, skill_content_sha256(manifest), True)
    graph = build_swarm_graph(
        provider,
        tool_broker=broker,
        policy_context=context,
        skill_registry=registry,
        tool_executor=ReadOnlyToolExecutor(),
        inference_nvidia_only=True,
        dynamic_tool_names=lambda _: frozenset(),
    )
    before, before_bytes = counter.count, counter.prompt_bytes
    before_responses, before_statuses = counter.responses, counter.status_counts.copy()
    started = time.perf_counter_ns()
    state: dict[str, Any] = {}
    error = None
    failure_type = None
    provider_status = None
    answer = ""
    try:
        async with asyncio.timeout(40):
            state = await graph.ainvoke(
                {"request": UserRequest(text=f"{_TRIGGER.capitalize()}. {case.request}")}
            )
        answer = state["final_result"].content
    except LiveRequestBudgetExceeded:
        error = "request_budget_exhausted"
    except TimeoutError:
        error = "task_deadline"
    except Exception as failure:
        failure_type = type(failure).__name__
        provider_status = _safe_status_code(failure)
        error = (
            "provider_unavailable"
            if isinstance(failure, RemoteProviderUnavailableError)
            else failure_type
        )
    evaluated = error is None
    valid, violations = _oracle(answer, case.expected) if evaluated else (None, ())
    executions = state.get("tool_results", ())
    read_verified = (
        len(executions) == 1
        and executions[0].success
        and executions[0].tool_name == "filesystem_read_text"
        and executions[0].output == case.fixture
        and executions[0].metadata.get("path") == "fixture.py"
        and executions[0].metadata.get("truncated") is False
        and len(state.get("tool_authorizations", ())) == 1
        and state["tool_authorizations"][0].decision is PolicyDecision.ALLOW
    )
    selected = tuple(item.manifest.skill_id for item in state.get("skills", ()))
    selection_valid = ("synthetic-evidence-review" in selected) == with_skill
    upstream_status = counter.last_status_code if counter.responses > before_responses else None
    return {
        "case_id": case.case_id,
        "variant": "with_skill" if with_skill else "without_skill",
        "evaluation_status": "evaluated" if evaluated else "not_evaluated",
        "passed": valid and read_verified and selection_valid if evaluated else None,
        "oracle_passed": valid,
        "format_or_value_violations": list(violations) if evaluated else None,
        "observable_read_verified": read_verified if evaluated else None,
        "procedure_selected": "synthetic-evidence-review" in selected if evaluated else None,
        "selection_matches_variant": selection_valid if evaluated else None,
        "selected_skill_count": len(selected) if evaluated else None,
        "error_code": error,
        "provider_error_type": failure_type,
        "provider_status_code": provider_status,
        "upstream_status_code": upstream_status if error else None,
        # Closed, locally derived categories; no arbitrary remote code or message.
        "upstream_error_code": _safe_error_category(upstream_status) if error else None,
        "provider_error_code": _safe_error_category(provider_status) if error else None,
        "latency_ms": (time.perf_counter_ns() - started) // 1_000_000,
        "http_requests": counter.count - before,
        "provider_responses": counter.responses - before_responses,
        "status_counts": _public_status_counts(counter.status_counts - before_statuses),
        "prompt_bytes": counter.prompt_bytes - before_bytes,
        "answer_sha256": hashlib.sha256(answer.encode()).hexdigest() if answer else None,
    }


async def run_live_skill_pilot(
    *,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    secret_loader=None,
    pause_seconds: float = 3,
    pilot_model: str | None = None,
) -> dict[str, Any]:
    """Call only after explicit authorization; defaults reuse the existing NVIDIA setup."""
    configured = settings or Settings()
    endpoint = urlsplit(str(configured.nvidia_base_url))
    if (
        endpoint.scheme != "https"
        or not endpoint.hostname
        or not endpoint.hostname.endswith(".nvidia.com")
    ):
        raise ValueError("pilot_requires_configured_nvidia_https_endpoint")
    preferences = load_model_preferences(configured.model_preferences_path)
    original_selection = preferences.roles[AgentRole.CODE_SECURITY]
    if pilot_model is not None:
        model = next((item for item in preferences.models if item.model_id == pilot_model), None)
        if model is None or not model.tools:
            raise ValueError("pilot_model_must_be_configured_and_tool_capable")
        preferences = ModelPreferences(
            models=preferences.models,
            roles={
                **preferences.roles,
                AgentRole.CODE_SECURITY: RoleSelection(primary=pilot_model, fallback=None),
            },
        )
    loader = (
        secret_loader
        or MacOSKeychain(configured.nvidia_keychain_service, configured.nvidia_keychain_account).get
    )
    counter = _CappedTransport(transport or httpx.AsyncHTTPTransport(retries=0))
    results = []
    timed_out = False
    with tempfile.TemporaryDirectory(prefix="jarvis-skill-live-", dir="/private/tmp") as raw:
        if pilot_model is not None:
            temporary_preferences = Path(raw) / "model_preferences.json"
            write_private_json(temporary_preferences, preferences.model_dump(mode="json"))
            configured = configured.model_copy(
                update={"model_preferences_path": temporary_preferences}
            )
        async with NvidiaNimClient(
            configured, loader, transport=counter, cooldown=NvidiaGlobalCooldown()
        ) as client:
            provider = NvidiaBrainClient(client)
            try:
                async with asyncio.timeout(TOTAL_SECONDS):
                    for index, case in enumerate(_CASES):
                        # Alternate pair order rather than systematically favoring
                        # the second run's warm connection.
                        for with_skill in (False, True) if index % 2 == 0 else (True, False):
                            results.append(
                                await _run_case(
                                    Path(raw) / f"{case.case_id}-{with_skill}",
                                    case,
                                    with_skill,
                                    provider,
                                    counter,
                                )
                            )
                            if counter.rate_limited:
                                break
                            if len(results) < 6:
                                await asyncio.sleep(pause_seconds)
                        if counter.rate_limited:
                            break
            except TimeoutError:
                timed_out = True
    return {
        "schema_version": 1,
        "profile": "nvidia_synthetic_skill_pilot",
        "http_request_limit": MAX_HTTP_REQUESTS,
        "http_requests": counter.count,
        "provider_responses": counter.responses,
        "status_counts": _public_status_counts(counter.status_counts),
        "timed_out": timed_out,
        "complete": (
            len(results) == 6
            and not timed_out
            and all(item["answer_sha256"] is not None for item in results)
        ),
        "stopped_after_http_429": counter.rate_limited,
        "model_ids": sorted(counter.model_ids),
        "configured_role": AgentRole.CODE_SECURITY.value,
        "configured_model_id": original_selection.primary,
        "configured_fallback_model_id": original_selection.fallback,
        "pilot_override": (
            {"role": AgentRole.CODE_SECURITY.value, "model_id": pilot_model, "fallback": None}
            if pilot_model is not None
            else None
        ),
        "pause_between_runs_seconds": pause_seconds,
        "statistical_significance_claimed": False,
        "runs_per_task_variant": 1,
        "contains_prompts_or_answers": False,
        "real_tool_side_effects": False,
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Authorize the bounded NVIDIA pilot.")
    parser.add_argument(
        "--pilot-model",
        help="Temporarily use an already configured tool-capable model for this pilot only.",
    )
    arguments = parser.parse_args(argv)
    if not arguments.run:
        parser.print_help()
        return 0
    try:
        report = asyncio.run(run_live_skill_pilot(pilot_model=arguments.pilot_model))
    except Exception as error:
        print(json.dumps({"complete": False, "error_code": type(error).__name__}))
        return 1
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
