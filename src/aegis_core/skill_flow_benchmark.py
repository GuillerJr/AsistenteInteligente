"""Offline graph-flow coverage, not an assessment of real NVIDIA model quality.

Run with ``python -m aegis_core.skill_flow_benchmark``. Every model response and
external tool boundary is synthetic; routing, prompts, broker, execution, skill
policy and synthesis traverse the production graph. Only aggregate diagnostics
are returned, never captured prompts or resource contents.
"""

from __future__ import annotations

import ast
import asyncio
import csv
import hashlib
import io
import json
import re
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from aegis_core.config import Settings
from aegis_core.contracts import AgentResult, Capability, PolicyDecision, RiskLevel, UserRequest
from aegis_core.mcp.client import (
    McpHost,
    McpHostConfiguration,
    McpServerConfiguration,
    reviewed_schema_sha256,
)
from aegis_core.mcp.integrations import IntegrationScope
from aegis_core.memory.contracts import ConversationRole, ConversationTurn, MemoryKind
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.memory.task_checkpoint import TaskCheckpoint
from aegis_core.memory.vector_db import HybridMemoryRetriever
from aegis_core.orchestration.graph import build_swarm_graph
from aegis_core.plugins.contracts import (
    McpConnectorManifest,
    McpToolManifest,
    PluginManifest,
    PluginPackage,
    exposed_tool_name,
)
from aegis_core.plugins.runtime import PluginRuntime
from aegis_core.providers.nvidia import NvidiaGlobalCooldown, NvidiaNimClient
from aegis_core.skill_flow_live import _ADVERSARIAL_CASES, _oracle
from aegis_core.skills import (
    SkillDraft,
    SkillPolicyStore,
    SkillRegistry,
    SkillStore,
    skill_content_sha256,
)
from aegis_core.tools.broker import PolicyContext
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context
from aegis_core.tools.execution import ReadOnlyToolExecutor

MODES = ("baseline", "builtin", "learned_local", "learned_remote", "plugin_local", "plugin_remote")
WORKLOADS = ("evidence", "continuity", "topic_change", "conflict", "budget", "forged_tool")
EXPECTED_CASES = len(MODES) * len(WORKLOADS) + 3
_EXPECTED_IDS = frozenset(
    {f"{mode}.{workload}" for mode in MODES for workload in WORKLOADS}
    | {"plugin_remote.exact_approval", "plugin_remote.plugin_revoked", "builtin.code_review"}
)
MAX_OUTGOING_PROMPT_BYTES = 32_768
_QUERY = "Ahora, investiga en internet inventario ATLAS"
_PRIVATE_MARKER = "Referencia sintética privada ZEPHYR-SKILL-73."
_PLUGIN_TOOL = exposed_tool_name("flow-kit", "catalogue", "search")


def _require(condition: bool) -> None:
    if not condition:
        raise AssertionError("flow_contract_failed")


@dataclass(frozen=True, slots=True)
class SkillFlowResult:
    case_id: str
    mode: str
    workload: str
    passed: bool
    latency_ms: int
    provider_calls: int
    prompt_bytes: int
    maximum_prompt_bytes: int
    tool_schema_bytes: int
    executions: int
    active_skills: int
    failure_code: str | None = None
    selected_skill_ids: tuple[str, ...] = ()
    selection_verified: bool | None = None
    evidence_verified: bool | None = None
    synthesis_guidance_verified: bool | None = None
    oracle_passed: bool | None = None
    task_completed: bool | None = None
    expected_task_completion: bool | None = None

    def public_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class SkillFlowReport:
    results: tuple[SkillFlowResult, ...]

    @property
    def gate_passed(self) -> bool:
        return (
            len(self.results) == EXPECTED_CASES
            and {item.case_id for item in self.results} == _EXPECTED_IDS
            and all(item.passed for item in self.results)
        )

    def public_dict(self) -> dict[str, object]:
        modes = {}
        for mode in MODES:
            selected = [
                item for item in self.results if item.mode == mode and item.workload in WORKLOADS
            ]
            modes[mode] = {
                "passed": sum(item.passed for item in selected),
                "cases": len(selected),
                "provider_calls": sum(item.provider_calls for item in selected),
                "prompt_bytes": sum(item.prompt_bytes for item in selected),
                "maximum_prompt_bytes": max(
                    (item.maximum_prompt_bytes for item in selected), default=0
                ),
                "tool_schema_bytes": sum(item.tool_schema_bytes for item in selected),
                "executions": sum(item.executions for item in selected),
                "latency_ms": sum(item.latency_ms for item in selected),
            }
        return {
            "schema_version": 1,
            "profile": "offline_skill_graph_flow",
            "provider": "simulated_nvidia_http_endpoint",
            "real_network_calls": 0,
            "real_model_quality_measured": False,
            "interpretation": (
                "Policy and flow coverage, payload costs and exact synthetic oracles only; "
                "scores are not model-quality gains. Baseline continuation controls must "
                "remain unexecuted without a validated historical tool scope."
            ),
            "gate_passed": self.gate_passed,
            "passed": sum(item.passed for item in self.results),
            "total": len(self.results),
            "modes": modes,
            "coverage": dict(Counter(item.workload for item in self.results)),
            "catalogue_comparison": {
                mode: {
                    "cases": sum(
                        item.mode == mode and item.workload.startswith("catalogue_")
                        for item in self.results
                    ),
                    "selection_verified": sum(
                        item.selection_verified is True and item.mode == mode
                        for item in self.results
                    ),
                    "evidence_verified": sum(
                        item.evidence_verified is True and item.mode == mode
                        for item in self.results
                    ),
                    "oracle_passed": sum(
                        item.oracle_passed is True and item.mode == mode for item in self.results
                    ),
                    "task_cases": sum(
                        item.task_completed is not None and item.mode == mode
                        for item in self.results
                    ),
                    "tasks_completed": sum(
                        item.task_completed is True and item.mode == mode for item in self.results
                    ),
                    "expected_incomplete_controls": sum(
                        item.expected_task_completion is False and item.mode == mode
                        for item in self.results
                    ),
                }
                for mode in ("baseline", "builtin")
            },
            "privacy": {"contains_prompts": False, "contains_skill_resources": False},
            "results": [item.public_dict() for item in self.results],
        }


def _history(*contents: str) -> tuple[ConversationTurn, ...]:
    conversation_id = uuid4()
    return tuple(
        ConversationTurn(
            conversation_id=conversation_id,
            sequence=index,
            role=ConversationRole.USER if index % 2 else ConversationRole.ASSISTANT,
            content=content,
            content_sha256=hashlib.sha256(content.encode()).hexdigest(),
            created_at=datetime.now(UTC),
        )
        for index, content in enumerate(contents, 1)
    )


def _draft(tool: str = "web_research", *, budget: bool = False) -> SkillDraft:
    return SkillDraft(
        skill_id="flow-research-guide",
        name="Procedimiento de inventario",
        description="Compara inventario y reservas usando fuentes verificables.",
        role="planner",
        trigger_phrases=(_QUERY.casefold(),),
        instructions=("Conserva cifras, unidades, revisiones y contradicciones de las fuentes.",),
        resources=(
            _PRIVATE_MARKER + ("\n" + "Dato de referencia declarativo. " * 210 if budget else ""),
        ),
        allowed_tools=frozenset({tool}),
        starter_tools=frozenset({tool}),
        priority=100,
    )


def _package(*, budget: bool, high_risk: bool) -> PluginPackage:
    connector = McpConnectorManifest(
        connector_id="catalogue",
        endpoint="https://catalogue.example.com/mcp",
        tools=(
            McpToolManifest(
                name="search",
                description="Consulta un inventario sintético acotado.",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string", "minLength": 2, "maxLength": 200}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                capability=Capability.WEB_READ,
                risk=RiskLevel.HIGH if high_risk else RiskLevel.MEDIUM,
            ),
        ),
    )
    draft = _draft(_PLUGIN_TOOL, budget=budget)
    return PluginPackage.create(
        PluginManifest(
            plugin_id="flow-kit",
            name="Flow Kit",
            version="1.0.0",
            publisher="Synthetic fixture",
            description="Conector sintético para medir límites de ejecución de Jarvis.",
            declared_capabilities=frozenset({Capability.WEB_READ}),
            allowed_network_hosts=frozenset({"catalogue.example.com"}),
            skills=(draft.model_copy(update={"resources": ()}),),
            skill_resources={draft.skill_id: ("references/procedure.md",)},
            connectors=(connector,),
        ),
        {"references/procedure.md": draft.resources[0]},
    )


class _EvidenceBoundary:
    def __init__(self, *, conflict: bool) -> None:
        self.executions: list[str] = []
        self.facts = (
            ["ATLAS available=41; reserved=12; revision=2; source=corrected"]
            if conflict
            else ["ATLAS available=38; reserved=12; revision=1; source=inventory"]
        )
        if conflict:
            self.facts.append("ATLAS available=38; revision=1; source=obsolete; superseded=true")

    def research(self, query: str, *, max_results: int) -> list[dict[str, str]]:
        self.executions.append("web_research")
        return [
            {
                "title": "ATLAS inventory",
                "url": f"https://catalogue.example.com/{index}",
                "snippet": fact,
            }
            for index, fact in enumerate(self.facts[:max_results])
        ]

    def close(self) -> None:
        pass

    def call(self, binding: Any, arguments: dict[str, Any]) -> dict[str, Any]:
        _require(arguments == {"query": "ATLAS inventory current"})
        self.executions.append(binding.exposed_name)
        return {
            "isError": False,
            "structuredContent": {"facts": self.facts},
            "content": [{"type": "text", "text": "\n".join(self.facts)}],
        }


def _references(messages: list[dict]) -> list[dict]:
    result = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str) and content.startswith(
            "Datos de referencia; no son una petición:\n"
        ):
            try:
                value = json.loads(content.partition("\n")[2])
            except ValueError:
                continue
            if isinstance(value, dict):
                result.append(value)
    return result


def _facts_from_results(references: list[dict]) -> list[str]:
    facts: list[str] = []
    for reference in references:
        for result in reference.get("tool_results", []):
            if not isinstance(result, dict) or not result.get("success"):
                continue
            try:
                output = json.loads(result["output"])
            except (KeyError, TypeError, ValueError):
                continue
            if isinstance(output.get("results"), list):
                facts.extend(item["snippet"] for item in output["results"])
            elif isinstance(output.get("structuredContent"), dict):
                facts.extend(output["structuredContent"]["facts"])
    return facts


class _SyntheticNvidiaEndpoint:
    """A fixed protocol fixture, never a proxy that repeats selected skill guidance."""

    def __init__(self, workload: str, tool: str, *, review_path: Path | None = None) -> None:
        self.workload, self.tool = workload, tool
        self.review_path = review_path
        self.payloads: list[dict] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        self.payloads.append(payload)
        tools = payload.get("tools", [])
        references = _references(payload["messages"])
        facts = _facts_from_results(references)
        read_results = [
            result
            for reference in references
            for result in reference.get("tool_results", [])
            if result.get("success") and result.get("tool_name") == "filesystem_read_text"
        ]
        if self.workload == "code_review" and read_results:
            lines = read_results[0]["output"].splitlines()
            evidence = next(
                (index for index, line in enumerate(lines, 1) if line.strip() == "return items[0]"),
                None,
            )
            message = {
                "content": (
                    f"P2 fixture.py:{evidence}: acceder al primer elemento sin comprobar "
                    "longitud falla si la lista está vacía."
                )
                if evidence
                else "No encontré ese acceso en el archivo leído."
            }
        elif facts:
            message = {"content": "Resultado verificado: " + " | ".join(facts)}
        elif self.workload == "topic_change":
            message = {"content": "cambio de tema confirmado"}
        elif tools:
            forged = self.workload == "forged_tool"
            name = "network_discover_hosts" if forged else self.tool
            arguments = (
                {
                    "path": self.review_path.name if self.review_path else "fixture.py",
                    "max_bytes": 512,
                }
                if self.workload == "code_review"
                else (
                    {"target": "127.0.0.1", "ports": [443]}
                    if forged
                    else {
                        "query": "ATLAS inventory current",
                        **({"max_results": 3} if name == "web_research" else {}),
                    }
                )
            )
            message = {
                "content": None,
                "tool_calls": [
                    {
                        "id": "synthetic-call-0",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ],
            }
        else:
            message = {"content": "No hay un resultado nuevo de herramienta verificado."}
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": message,
                        "finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
                    }
                ],
                "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
            },
        )


class _NvidiaOnlyBoundary:
    def __init__(self, client: NvidiaNimClient) -> None:
        self.client = client
        self.local_calls = 0

    async def complete_nvidia_only(
        self, *, request_id=None, on_delta=None, review_dialogue=False, evidence_role=None, **kwargs
    ) -> AgentResult:
        del request_id, review_dialogue
        if evidence_role is not None:
            from aegis_core.brain.nvidia_client import NvidiaBrainClient

            return await NvidiaBrainClient(self.client, rate_limit_retries=0).complete_nvidia_only(
                evidence_role=evidence_role, on_delta=on_delta, **kwargs,
            )
        result = await self.client.complete(**kwargs)
        if on_delta is not None and not result.tool_calls:
            on_delta(result.content)
        return result

    async def complete(self, **kwargs) -> AgentResult:
        self.local_calls += 1
        raise AssertionError("local_provider_used")


class SkillFlowBenchmark:
    """Complete task flows across six policy variants with bounded synthetic evidence."""

    def __init__(self, *, temporary_root: Path | None = Path("/private/tmp")) -> None:
        self.temporary_root = temporary_root

    async def run(self) -> SkillFlowReport:
        results = []
        with tempfile.TemporaryDirectory(
            prefix="jarvis-skill-flow-", dir=self.temporary_root
        ) as raw:
            root = Path(raw)
            root.chmod(0o700)
            for mode in MODES:
                for workload in WORKLOADS:
                    results.append(await self.run_case(root / f"{mode}-{workload}", mode, workload))
            for workload in ("exact_approval", "plugin_revoked"):
                results.append(await self.run_case(root / workload, "plugin_remote", workload))
            results.append(await self.run_case(root / "code-review", "builtin", "code_review"))
            for case in CATALOGUE_CASES:
                for mode in ("baseline", "builtin"):
                    results.append(
                        await self.run_catalogue_case(
                            root / f"catalogue-{case.case_id}-{mode}", mode, case
                        )
                    )
        return SkillFlowReport(tuple(results))

    async def run_case(self, root: Path, mode: str, workload: str) -> SkillFlowResult:
        root.mkdir(mode=0o700, parents=True, exist_ok=False)
        boundary = _EvidenceBoundary(conflict=workload == "conflict")
        plugin = mode.startswith("plugin_")
        runtime = (
            PluginRuntime(
                (_package(budget=workload == "budget", high_risk=workload == "exact_approval"),),
                client=boundary,
            )
            if plugin
            else PluginRuntime(())
        )
        broker = build_default_tool_broker(runtime.tool_definitions())
        store = OneTimeConfirmationStore()
        base = default_policy_context(root)
        context = PolicyContext(
            workspace_root=root, network_scopes=base.network_scopes, confirmation_store=store
        )
        registry = SkillRegistry(
            broker,
            SkillStore(root / "skills"),
            **({"builtins": ()} if mode == "baseline" or plugin else {}),
            plugin_skills=runtime.skill_manifests(),
            policy=SkillPolicyStore(root / "skills_policy.json"),
        )
        if mode.startswith("learned_"):
            registry.learn(_draft(budget=workload == "budget"))
        if mode.endswith("_remote"):
            manifest = next(
                item for item in registry.all() if item.skill_id == "flow-research-guide"
            )
            registry.set_remote_consent(manifest.skill_id, skill_content_sha256(manifest), True)
        if workload == "plugin_revoked":
            runtime.revoke("flow-kit")
        tool = (
            "filesystem_read_text"
            if workload == "code_review"
            else (_PLUGIN_TOOL if plugin else "web_research")
        )
        review_path = root / "fixture.py"
        if workload == "code_review":
            review_path.write_text("def first(items):\n    return items[0]\n", encoding="utf-8")
        endpoint = _SyntheticNvidiaEndpoint(workload, tool, review_path=review_path)
        started = time.perf_counter_ns()
        failure = None
        state: dict[str, Any] = {}
        settings = Settings.model_construct(model_preferences_path=root / "models.json")
        try:
            async with NvidiaNimClient(
                settings,
                lambda: "synthetic",
                transport=httpx.MockTransport(endpoint),
                cooldown=NvidiaGlobalCooldown(),
            ) as client:
                provider = _NvidiaOnlyBoundary(client)
                executor = ReadOnlyToolExecutor(
                    web_client_factory=lambda: boundary, extra_handlers=runtime.handlers()
                )
                graph = build_swarm_graph(
                    provider,
                    tool_broker=broker,
                    policy_context=context,
                    tool_executor=executor,
                    skill_registry=registry,
                    inference_nvidia_only=True,
                    conversation_max_context_bytes=512,
                    dynamic_tool_names=(
                        lambda request: (
                            frozenset({tool})
                            if plugin and "inventario" in request.text.casefold()
                            else frozenset()
                        )
                    ),
                )
                history = _history(_QUERY, "La revisión del inventario sigue pendiente.")
                request = UserRequest(
                    text="Revisa el código del archivo fixture.py y señala fallos con líneas."
                    if workload == "code_review"
                    else "continúa"
                    if workload == "continuity"
                    else "Responde únicamente: cambio de tema confirmado."
                    if workload == "topic_change"
                    else _QUERY
                )
                if workload == "budget":
                    history = _history(
                        _QUERY + " " + "contexto sintético " * 600,
                        "Mantén las cifras exactas; " * 300,
                    )
                input_state = {"request": request, "conversation_history": history}
                async with asyncio.timeout(5):
                    state = await graph.ainvoke(input_state)
                if workload == "exact_approval":
                    _require(not boundary.executions and "final_result" not in state)
                    authorization = state["tool_authorizations"][0]
                    _require(authorization.decision is PolicyDecision.REQUIRE_CONFIRMATION)
                    # This grant represents a fixture owner reviewing the concrete
                    # call after the broker suspended it; no skill grants permission.
                    store.issue(
                        state["specialist_result"].tool_calls[0],
                        authorization,
                        approved_by="synthetic-owner",
                        now=context.current_time(),
                        request_id=request.request_id,
                    )
                    state = await graph.ainvoke(input_state)
                    _require(len(boundary.executions) == 1)
                    replay = await graph.ainvoke(input_state)
                    _require(
                        replay["tool_authorizations"][0].reason_code == "confirmation_replayed"
                    )
                    _require(len(boundary.executions) == 1)
                _require(provider.local_calls == 0)
                serialized = json.dumps(endpoint.payloads, ensure_ascii=False)
                _require(
                    (_PRIVATE_MARKER in serialized)
                    == (mode.endswith("_remote") and workload != "topic_change")
                )
                _require(
                    max(
                        (
                            len(json.dumps(item["messages"], ensure_ascii=False).encode())
                            for item in endpoint.payloads
                        ),
                        default=0,
                    )
                    <= MAX_OUTGOING_PROMPT_BYTES
                )
                if workload == "code_review":
                    _require(not boundary.executions and len(state["tool_results"]) == 1)
                    result = state["tool_results"][0]
                    _require(result.success and result.tool_name == "filesystem_read_text")
                    _require(
                        result.metadata["path"] == review_path.name
                        and not result.metadata["truncated"]
                    )
                    _require("fixture.py:2" in state["final_result"].content)
                    _require(
                        all(
                            tool["function"]["name"] == "filesystem_read_text"
                            for payload in endpoint.payloads
                            for tool in payload.get("tools", [])
                        )
                    )
                elif workload in {"forged_tool", "plugin_revoked", "topic_change"}:
                    _require(not boundary.executions)
                    if workload == "topic_change":
                        _require("skill" not in state and not state.get("skills"))
                        _require(state["final_result"].content == "cambio de tema confirmado")
                    elif workload == "forged_tool":
                        _require(state["tool_authorizations"][0].decision is PolicyDecision.DENY)
                    else:
                        _require(not state["tool_results"][0].success)
                elif workload == "continuity" and mode == "baseline":
                    _require(not boundary.executions)
                else:
                    _require(boundary.executions == [tool])
                    _require(state["tool_results"][0].success)
                    _require(state["tool_authorizations"][0].decision is PolicyDecision.ALLOW)
                    _require(
                        ("available=41" if workload == "conflict" else "available=38")
                        in state["final_result"].content
                    )
                    if workload == "conflict":
                        _require(
                            "available=38" in state["final_result"].content
                            and "superseded=true" in state["final_result"].content
                        )
        except AssertionError:
            failure = "flow_contract_failed"
        except Exception as error:
            failure = f"internal_{type(error).__name__}"
        prompt_sizes = [
            len(json.dumps(item["messages"], ensure_ascii=False).encode())
            for item in endpoint.payloads
        ]
        return SkillFlowResult(
            f"{mode}.{workload}",
            mode,
            workload,
            failure is None,
            (time.perf_counter_ns() - started) // 1_000_000,
            len(endpoint.payloads),
            sum(prompt_sizes),
            max(prompt_sizes, default=0),
            sum(
                len(json.dumps(item.get("tools", []), ensure_ascii=False).encode())
                for item in endpoint.payloads
            ),
            len(boundary.executions)
            + (len(state.get("tool_results", ())) if workload == "code_review" else 0),
            len(state.get("skills", ())) or int("skill" in state),
            failure,
        )

    async def run_catalogue_case(
        self, root: Path, mode: str, case: CatalogueCase
    ) -> SkillFlowResult:
        return await _run_catalogue_case(root, mode, case)


@dataclass(frozen=True, slots=True)
class CatalogueCase:
    case_id: str
    query: str
    skill_id: str | None
    tool_name: str | None = None
    arguments: dict[str, Any] | None = None
    expected: dict[str, Any] | None = None
    fixture: str | None = None
    filename: str = "fixture.py"
    history: tuple[str, ...] = ()
    denied: bool = False
    checkpoint_status: str | None = None


_ACCOUNT = "owner@example.com"
_WORKSPACE_SKILL = "google-workspace-expert"
_DOCUMENT_SKILL = "document-analysis-expert"
_GOOGLE_DATA = {
    "search_gmail_messages": {
        "messages": [
            {
                "id": f"m{index}",
                "subject": f"Synthetic {index}",
                "from": "sender@example.com",
                "unread": True,
            }
            for index in range(1, 6)
        ],
        "nextPageToken": "synthetic-next-page",
        "resultSizeEstimate": 99,
    },
    "get_events": {
        "events": [
            {"id": "e1", "start": "2026-10-08T14:00:00Z", "end": "2026-10-08T15:00:00Z"},
            {"id": "e2", "start": "2026-10-08T14:30:00Z", "end": "2026-10-08T15:30:00Z"},
        ],
    },
    "search_drive_files": {
        "files": [
            {"id": "f1", "name": "ATLAS", "revision": 1, "current": False},
            {"id": "f2", "name": "ATLAS", "revision": 2, "current": True},
        ],
    },
}

CATALOGUE_CASES = (
    CatalogueCase(
        "gmail_page_not_total",
        "Revisa Gmail y devuelve JSON de la página de cinco no leídos: "
        "listed, total_unread (null si desconocido), last_id y next_page.",
        _WORKSPACE_SKILL,
        "search_gmail_messages",
        {
            "user_google_email": _ACCOUNT,
            "query": "in:inbox is:unread",
            "page_size": 5,
            "include_headers": True,
        },
        {"listed": 5, "total_unread": None, "last_id": "m5", "next_page": True},
    ),
    CatalogueCase(
        "calendar_timezone_overlap",
        "Revisa Google Calendar en America/Guayaquil y calcula "
        "el solapamiento. Devuelve JSON con overlap_minutes y first_local_start.",
        _WORKSPACE_SKILL,
        "get_events",
        {
            "user_google_email": _ACCOUNT,
            "calendar_id": "primary",
            "time_min": "2026-10-08T00:00:00-05:00",
            "time_max": "2026-10-09T00:00:00-05:00",
            "max_results": 10,
            "timezone": "America/Guayaquil",
        },
        {"overlap_minutes": 30, "first_local_start": "2026-10-08T09:00:00-05:00"},
    ),
    CatalogueCase(
        "drive_revision_ids",
        "Busca en Drive las revisiones de ATLAS. Devuelve JSON con "
        "current_id, previous_id y content_read (false si sólo consultaste metadatos).",
        _WORKSPACE_SKILL,
        "search_drive_files",
        {
            "user_google_email": _ACCOUNT,
            "query": "name = 'ATLAS' and trashed = false",
            "page_size": 10,
        },
        {"current_id": "f2", "previous_id": "f1", "content_read": False},
    ),
    CatalogueCase(
        "workspace_wrong_account",
        "Revisa Gmail y consulta cinco correos no leídos.",
        _WORKSPACE_SKILL,
        "search_gmail_messages",
        {
            "user_google_email": "other@example.com",
            "query": "in:inbox is:unread",
            "page_size": 5,
            "include_headers": True,
        },
        denied=True,
    ),
    CatalogueCase(
        "workspace_unoffered_send",
        "Revisa Gmail; no tienes permiso para enviar mensajes.",
        _WORKSPACE_SKILL,
        "send_gmail_message",
        {"user_google_email": _ACCOUNT, "to": "recipient@example.com", "body": "fixture"},
        denied=True,
    ),
    *(
        CatalogueCase(
            case.case_id,
            f"{case.trigger}. {case.request}",
            case.expected_skill_id,
            "filesystem_read_text",
            {"path": case.filename, "max_bytes": 2048},
            case.expected,
            case.fixture,
            case.filename,
        )
        for case in _ADVERSARIAL_CASES
    ),
    CatalogueCase(
        "memory_correction",
        "Resume el documento de decisiones recordadas de ATLAS. "
        "Devuelve JSON con current_eur y previous_eur; conserva la corrección del dueño.",
        _DOCUMENT_SKILL,
        expected={"current_eur": 730, "previous_eur": 810},
    ),
    CatalogueCase(
        "scoped_continuity",
        "continúa por favor",
        _DOCUMENT_SKILL,
        "filesystem_read_text",
        {"path": "fixture.json", "max_bytes": 2048},
        _ADVERSARIAL_CASES[2].expected,
        _ADVERSARIAL_CASES[2].fixture,
        "fixture.json",
        history=(_ADVERSARIAL_CASES[2].trigger, "La comparación sigue pendiente.", "gracias"),
    ),
    CatalogueCase(
        "conceptual_gmail", "¿Qué es Gmail? Responde únicamente 17 sin acceder a mi correo.", None
    ),
    CatalogueCase(
        "quoted_mail", "Traduce esta cita: «revisa mi correo». Responde únicamente 17.", None
    ),
    CatalogueCase(
        "negated_mail", "No revises mi correo ni abras Gmail. Responde únicamente 17.", None
    ),
    CatalogueCase(
        "topic_change",
        "Cambiemos de tema: responde únicamente 17.",
        None,
        history=("Revisa Gmail y consulta mis correos.", "La consulta está pendiente."),
    ),
    CatalogueCase(
        "cancelled_followup",
        "continúa por favor",
        None,
        history=(
            "Revisa Gmail y consulta mis correos.",
            "He propuesto la consulta.",
            "Cancela la consulta; no quiero revisar el correo.",
        ),
    ),
    CatalogueCase(
        "scoped_continuity_checkpoint",
        "continúa por favor",
        _DOCUMENT_SKILL,
        "filesystem_read_text",
        {"path": "fixture.json", "max_bytes": 2048},
        _ADVERSARIAL_CASES[2].expected,
        _ADVERSARIAL_CASES[2].fixture,
        "fixture.json",
        history=(_ADVERSARIAL_CASES[2].trigger, "La comparación sigue pendiente.", "gracias"),
        checkpoint_status="needs_action",
    ),
    CatalogueCase(
        "cancelled_checkpoint",
        "continúa por favor",
        None,
        history=(_ADVERSARIAL_CASES[2].trigger, "La comparación sigue pendiente."),
        checkpoint_status="cancelled",
    ),
    CatalogueCase(
        "conceptual_code",
        "Explica qué significa código sin leer archivos. Responde únicamente 17.",
        None,
    ),
)
EXPECTED_CASES += len(CATALOGUE_CASES) * 2
_EXPECTED_IDS |= frozenset(
    f"{mode}.catalogue_{case.case_id}"
    for case in CATALOGUE_CASES
    for mode in ("baseline", "builtin")
)


class _WorkspaceFixturePeer:
    """Only the external stdio boundary is replaced; admission/guard/relay stay real."""

    available = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        value = _GOOGLE_DATA[name]
        return {
            "isError": False,
            "structuredContent": value,
            "content": [{"type": "text", "text": json.dumps(value)}],
        }

    async def close(self):
        self.available = False


def _workspace_fixture_host() -> tuple[McpHost, _WorkspaceFixturePeer]:
    scope = IntegrationScope(provider="google-workspace", google_accounts={_ACCOUNT})
    schemas = {}
    for name, properties in {
        "search_gmail_messages": {
            "query": {"type": "string"},
            "page_size": {"type": "integer"},
            "include_headers": {"type": "boolean"},
        },
        "get_events": {
            "calendar_id": {"type": "string"},
            "time_min": {"type": "string"},
            "time_max": {"type": "string"},
            "max_results": {"type": "integer"},
            "timezone": {"type": "string"},
        },
        "search_drive_files": {"query": {"type": "string"}, "page_size": {"type": "integer"}},
    }.items():
        schemas[name] = {
            "type": "object",
            "properties": {"user_google_email": {"type": "string"}, **properties},
            "required": ["user_google_email"],
            "additionalProperties": False,
        }
    configuration = McpServerConfiguration(
        server_id="google-fixture",
        command=Path(sys.executable).resolve(),
        capability=Capability.WEB_READ,
        integration=scope,
        enabled=True,
        allow_explicit_requests=True,
        approved_tools={
            name: reviewed_schema_sha256(schema, scope, name) for name, schema in schemas.items()
        },
    )
    host = McpHost(McpHostConfiguration(servers=(configuration,)))
    peer = _WorkspaceFixturePeer()
    # No process is started: generated schemas pass the production admission path,
    # and the production handler revalidates the exact approval before this peer.
    bindings = host._admit_tools(
        configuration,
        tuple({"name": name, "inputSchema": schema} for name, schema in schemas.items()),
        set(),
    )
    host._bindings = {item.exposed_name: item for item in bindings}
    host._clients = {configuration.server_id: peer}
    host._started = True
    return host, peer


def _fixture_json_answer(evidence: str, filename: str) -> dict[str, Any]:
    """Independent fixture interpreter: no skill body or expected answer is read."""
    if filename.endswith(".csv"):
        rows = list(enumerate(csv.DictReader(io.StringIO(evidence)), 2))
        current = [
            (line, row)
            for line, row in rows
            if row["project"] == "ATLAS" and row["unit"] == "items" and row["status"] == "current"
        ]
        stock = next((line, row) for line, row in current if row["kind"] == "stock")
        reserved = next((line, row) for line, row in current if row["kind"] == "reserved")
        return {
            "available": int(stock[1]["quantity"]) - int(reserved[1]["quantity"]),
            "unit": stock[1]["unit"],
            "stock_citation": f"{filename}:{stock[0]}",
            "reserved_citation": f"{filename}:{reserved[0]}",
            "ignored_rows": [line for line, row in rows if (line, row) not in current],
        }
    if filename.endswith(".json"):
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
    findings = []
    for function in ast.parse(evidence).body:
        if any(isinstance(item, ast.If) for item in function.body):
            continue
        node = next(item for item in function.body if isinstance(item, ast.Return))
        findings.append(
            {
                "code": "empty_index" if isinstance(node.value, ast.Subscript) else "zero_division",
                "line": node.lineno,
                "citation": f"{filename}:{node.lineno}",
            }
        )
    return {"findings": findings}


def _catalogue_answer(references: list[dict], case: CatalogueCase) -> dict[str, Any] | None:
    for reference in references:
        for result in reference.get("tool_results", []):
            if not result.get("success"):
                continue
            if result["tool_name"] == "filesystem_read_text":
                return _fixture_json_answer(result["output"], result["metadata"]["path"])
            data = json.loads(result["output"])["structuredContent"]
            if "messages" in data:
                return {
                    "listed": len(data["messages"]),
                    "total_unread": None,
                    "last_id": data["messages"][-1]["id"],
                    "next_page": bool(data.get("nextPageToken")),
                }
            if "events" in data:
                from zoneinfo import ZoneInfo

                first, second = data["events"]
                start = max(datetime.fromisoformat(item["start"]) for item in (first, second))
                end = min(datetime.fromisoformat(item["end"]) for item in (first, second))
                return {
                    "overlap_minutes": int((end - start).total_seconds() // 60),
                    "first_local_start": datetime.fromisoformat(first["start"])
                    .astimezone(ZoneInfo("America/Guayaquil"))
                    .isoformat(),
                }
            current = next(item for item in data["files"] if item["current"])
            old = next(item for item in data["files"] if not item["current"])
            return {"current_id": current["id"], "previous_id": old["id"], "content_read": False}
    if case.case_id == "memory_correction":
        material = json.dumps(references)
        current = re.search(r"decision_current=(\d+)", material)
        old = re.search(r"decision_previous=(\d+)", material)
        if current and old:
            return {"current_eur": int(current[1]), "previous_eur": int(old[1])}
    return None


class _CatalogueEndpoint:
    def __init__(self, case: CatalogueCase, exposed_tool: str | None):
        self.case, self.tool = case, exposed_tool
        self.payloads: list[dict] = []

    async def __call__(self, request):
        payload = json.loads(request.content)
        self.payloads.append(payload)
        references = _references(payload["messages"])
        answer = _catalogue_answer(references, self.case)
        if "skill_ids" in payload["messages"][0]["content"]:
            message = {"content": '{"skill_ids":[]}'}
        elif answer is not None:
            message = {"content": json.dumps(answer)}
        elif self.case.tool_name is not None and payload.get("tools"):
            message = {
                "content": None,
                "tool_calls": [
                    {
                        "id": "catalogue-call",
                        "type": "function",
                        "function": {
                            "name": self.tool,
                            "arguments": json.dumps(self.case.arguments),
                        },
                    }
                ],
            }
        else:
            message = {"content": "17"}
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": message,
                        "finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
                    }
                ]
            },
        )


async def _run_catalogue_case(root: Path, mode: str, case: CatalogueCase) -> SkillFlowResult:
    absence_control = mode == "baseline" and case.case_id.startswith("scoped_continuity")
    root.mkdir(mode=0o700)
    if case.fixture is not None:
        (root / case.filename).write_text(case.fixture, encoding="utf-8")
    host, peer = _workspace_fixture_host()
    definitions = host.tool_definitions()
    names = {item.name.rpartition("__")[2]: item.name for item in definitions}
    tool = (
        "filesystem_read_text"
        if case.tool_name == "filesystem_read_text"
        else names.get(case.tool_name, "mcp_google_fixture__send_gmail_message")
        if case.tool_name is not None
        else None
    )
    broker = build_default_tool_broker(definitions)
    confirmations = OneTimeConfirmationStore()
    context = PolicyContext(workspace_root=root, confirmation_store=confirmations)
    registry = SkillRegistry(
        broker,
        SkillStore(root / "skills"),
        **({"builtins": ()} if mode == "baseline" else {}),
        policy=SkillPolicyStore(root / "skill_policy.json"),
    )
    retriever = None
    if case.case_id == "memory_correction":
        store = SQLiteMemoryStore(root / "memory.sqlite3", encryption_secret=b"s" * 32)
        store.initialize()
        for content in (
            "ATLAS owner decision_previous=810 EUR, superseded.",
            "ATLAS owner decision_current=730 EUR, explicit correction of 810.",
        ):
            store.put(namespace="user.default", kind=MemoryKind.SEMANTIC, content=content)
        retriever = HybridMemoryRetriever(store)
    endpoint = _CatalogueEndpoint(case, tool)
    state: dict[str, Any] = {}
    selection = evidence = synthesis = oracle = False
    failure = None
    started = time.perf_counter_ns()
    try:
        async with NvidiaNimClient(
            Settings.model_construct(model_preferences_path=root / "models.json"),
            lambda: "synthetic",
            transport=httpx.MockTransport(endpoint),
            cooldown=NvidiaGlobalCooldown(),
        ) as client:
            provider = _NvidiaOnlyBoundary(client)
            graph = build_swarm_graph(
                provider,
                tool_broker=broker,
                policy_context=context,
                skill_registry=registry,
                tool_executor=ReadOnlyToolExecutor(extra_async_handlers=host.handlers()),
                memory_retriever=retriever,
                inference_nvidia_only=True,
                dynamic_tool_names=lambda request: host.tool_names_for_request(request.text, None),
            )
            request = UserRequest(text=case.query)
            input_state = {"request": request, "conversation_history": _history(*case.history)}
            if case.checkpoint_status is not None:
                input_state["task_checkpoint"] = TaskCheckpoint(
                    active_intent=case.history[0],
                    status=case.checkpoint_status,
                    next_action=case.history[0],
                    sequence=len(case.history),
                )
            async with asyncio.timeout(8):
                state = await graph.ainvoke(input_state)
                authorizations = state.get("tool_authorizations", ())
                if (
                    authorizations
                    and authorizations[0].decision is PolicyDecision.REQUIRE_CONFIRMATION
                ):
                    _require(not peer.calls and "final_result" not in state)
                    confirmations.issue(
                        state["specialist_result"].tool_calls[0],
                        authorizations[0],
                        approved_by="synthetic-owner",
                        request_id=request.request_id,
                    )
                    state = await graph.ainvoke(input_state)
            selected = tuple(item.manifest.skill_id for item in state.get("skills", ()))
            selection = (
                case.skill_id in selected if mode == "builtin" and case.skill_id else not selected
            )
            if case.denied:
                evidence = (
                    not peer.calls
                    and bool(state.get("tool_authorizations"))
                    and all(
                        item.decision is PolicyDecision.DENY
                        for item in state["tool_authorizations"]
                    )
                )
                oracle = evidence
            elif absence_control:
                # The baseline has no procedure to validate a historical file scope.
                # Preserve the failed task oracle while checking that no read occurred.
                evidence = (
                    not peer.calls
                    and not state.get("tool_results")
                    and all(not payload.get("tools") for payload in endpoint.payloads)
                )
                oracle = _oracle(state["final_result"].content, case.expected)[0]
            elif case.tool_name is not None:
                results = state.get("tool_results", ())
                evidence = len(results) == 1 and results[0].success and results[0].tool_name == tool
                if case.fixture is not None:
                    evidence = (
                        evidence
                        and results[0].output == case.fixture
                        and results[0].metadata.get("truncated") is False
                    )
                else:
                    evidence = evidence and peer.calls == [(case.tool_name, case.arguments)]
                oracle = _oracle(state["final_result"].content, case.expected)[0]
            elif case.case_id == "memory_correction":
                evidence = len(state.get("memory_hits", ())) == 2 and not peer.calls
                oracle = _oracle(state["final_result"].content, case.expected)[0]
            else:
                evidence = (
                    not peer.calls
                    and not state.get("tool_results")
                    and all(not payload.get("tools") for payload in endpoint.payloads)
                )
                oracle = state["final_result"].content == "17"
            synthesis_payloads = [
                payload
                for payload in endpoint.payloads
                if not payload.get("tools")
                and any(
                    reference.get("tool_results") or reference.get("analyses")
                    for reference in _references(payload["messages"])
                )
            ]
            synthesis = all(
                {
                    entry["skill_id"]
                    for reference in _references(payload["messages"])
                    for entry in reference.get("selected_skills", [])
                }
                == set(selected)
                for payload in synthesis_payloads
            )
            _require(provider.local_calls == 0)
            for passed, code in (
                (selection, "catalogue_selection"),
                (evidence, "catalogue_evidence"),
                (synthesis, "catalogue_synthesis_guidance"),
                (oracle or absence_control, "catalogue_oracle"),
            ):
                if not passed:
                    failure = code
                    break
    except Exception as error:
        failure = f"internal_{type(error).__name__}"
    finally:
        await host.close()
    sizes = [
        len(json.dumps(item["messages"], ensure_ascii=False).encode()) for item in endpoint.payloads
    ]
    return SkillFlowResult(
        case_id=f"{mode}.catalogue_{case.case_id}",
        mode=mode,
        workload=f"catalogue_{case.case_id}",
        passed=failure is None,
        latency_ms=(time.perf_counter_ns() - started) // 1_000_000,
        provider_calls=len(endpoint.payloads),
        prompt_bytes=sum(sizes),
        maximum_prompt_bytes=max(sizes, default=0),
        tool_schema_bytes=sum(
            len(json.dumps(item.get("tools", [])).encode()) for item in endpoint.payloads
        ),
        executions=len(peer.calls)
        + sum(
            item.tool_name == "filesystem_read_text" and item.success
            for item in state.get("tool_results", ())
        ),
        active_skills=len(state.get("skills", ())),
        failure_code=failure,
        selected_skill_ids=tuple(item.manifest.skill_id for item in state.get("skills", ())),
        selection_verified=selection,
        evidence_verified=evidence,
        synthesis_guidance_verified=synthesis,
        oracle_passed=oracle,
        task_completed=oracle if case.expected is not None else None,
        expected_task_completion=(not absence_control) if case.expected is not None else None,
    )


def main() -> int:
    report = asyncio.run(SkillFlowBenchmark().run())
    print(json.dumps(report.public_dict(), ensure_ascii=True, sort_keys=True))
    return 0 if report.gate_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
