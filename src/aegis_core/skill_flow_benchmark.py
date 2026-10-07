"""Offline graph-flow coverage, not an assessment of real NVIDIA model quality.

Run with ``python -m aegis_core.skill_flow_benchmark``. Every model response and
external tool boundary is synthetic; routing, prompts, broker, execution, skill
policy and synthesis traverse the production graph. Only aggregate diagnostics
are returned, never captured prompts or resource contents.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
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
from aegis_core.memory.contracts import ConversationRole, ConversationTurn
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
                "Coverage and payload costs only; scores are not model-quality gains."
            ),
            "gate_passed": self.gate_passed,
            "passed": sum(item.passed for item in self.results),
            "total": len(self.results),
            "modes": modes,
            "coverage": dict(Counter(item.workload for item in self.results)),
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
        self, *, request_id=None, on_delta=None, review_dialogue=False, **kwargs
    ) -> AgentResult:
        del request_id, review_dialogue
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
                    local_provider=provider,
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


def main() -> int:
    report = asyncio.run(SkillFlowBenchmark().run())
    print(json.dumps(report.public_dict(), ensure_ascii=True, sort_keys=True))
    return 0 if report.gate_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
