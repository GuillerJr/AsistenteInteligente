"""Codex's plan mode: Jarvis only reads, and answers with a plan it does not run."""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel, ConfigDict

from aegis_core.contracts import (
    PLAN_MODE_METADATA,
    AgentResult,
    AgentRole,
    Capability,
    RiskLevel,
    ToolCall,
    ToolExecutionResult,
    UserRequest,
)
from aegis_core.ipc.protocol import IpcRequest
from aegis_core.job_contracts import JobStatus
from aegis_core.job_ipc import SubmitJobPayload, SwarmIpcService
from aegis_core.jobs import SwarmJobManager
from aegis_core.orchestration.graph import build_swarm_graph
from aegis_core.tools.broker import PolicyContext, ToolDefinition
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker
from aegis_core.tools.execution import ReadOnlyToolExecutor


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


DEFINITIONS = (
    ToolDefinition(name="demo_read_notes", description="Lee las notas del proyecto.",
                   arguments_model=Empty, capability=Capability.REMINDERS_READ,
                   risk=RiskLevel.LOW, allowed_roles=frozenset({AgentRole.PLANNER})),
    ToolDefinition(name="demo_send_mail", description="Envía un correo.",
                   arguments_model=Empty, capability=Capability.MAIL_WRITE,
                   risk=RiskLevel.HIGH, allowed_roles=frozenset({AgentRole.PLANNER}),
                   requires_confirmation=True),
)

PLAN = ("Objetivo: enviar el resumen.\n1. Leer las notas.\n2. Redactar el resumen.\n"
        "3. Enviarlo por correo (necesita tu aprobación).\n¿Lo implemento?")


class Provider:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    async def complete_nvidia_only(self, **kwargs):
        self.requests.append(kwargs)
        role = kwargs["role"]
        if role is AgentRole.SYNTHESIZER:
            return AgentResult(role=role, model_id="synthetic/nvidia", content=PLAN)
        # The model still tries to act; plan mode must not let it.
        return AgentResult(role=role, model_id="synthetic/nvidia", content=PLAN, tool_calls=(
            ToolCall(call_id="send", requested_by=role, tool_name="demo_send_mail",
                     arguments={}),))


def _jobs(tmp_path, provider, executed):
    grants = OneTimeConfirmationStore()
    context = PolicyContext(workspace_root=tmp_path, confirmation_store=grants)
    broker = build_default_tool_broker(DEFINITIONS)

    async def handle(authorization, _context):
        executed.append(authorization.tool_name)
        return ToolExecutionResult(call_id=authorization.call_id,
                                   tool_name=authorization.tool_name, success=True, output="{}")

    executor = ReadOnlyToolExecutor(extra_async_handlers={
        definition.name: handle for definition in DEFINITIONS})
    graph = build_swarm_graph(
        provider, tool_broker=broker, policy_context=context, tool_executor=executor,
        inference_nvidia_only=True,
        dynamic_tool_names=lambda request: frozenset(d.name for d in DEFINITIONS),
    )
    return SwarmJobManager(graph, tool_broker=broker, policy_context=context,
                           confirmation_store=grants, tool_executor=executor)


async def _finish(jobs, request):
    submitted = await jobs.submit(request)
    for _ in range(600):
        current = await jobs.status(submitted.job_id)
        if current.status not in {JobStatus.QUEUED, JobStatus.RUNNING}:
            return current
        await asyncio.sleep(0.005)
    raise AssertionError("plan job did not finish")


@pytest.mark.asyncio
async def test_plan_mode_offers_only_read_tools_and_never_asks_to_act(tmp_path):
    provider, executed = Provider(), []
    jobs = _jobs(tmp_path, provider, executed)
    result = await _finish(jobs, UserRequest(
        text="Prepara y envía el resumen del proyecto a Ana",
        metadata={PLAN_MODE_METADATA: True}))
    assert result.status is not JobStatus.AWAITING_CONFIRMATION
    assert "demo_send_mail" not in executed
    offered = json.dumps([r.get("extra_body") for r in provider.requests], default=str)
    assert "demo_send_mail" not in offered
    system = json.dumps([r["messages"][0] for r in provider.requests], ensure_ascii=False)
    assert "Modo plan" in system


@pytest.mark.asyncio
async def test_without_plan_mode_the_same_request_is_offered_the_action(tmp_path):
    provider, executed = Provider(), []
    jobs = _jobs(tmp_path, provider, executed)
    await _finish(jobs, UserRequest(text="Prepara y envía el resumen del proyecto a Ana"))
    offered = json.dumps([r.get("extra_body") for r in provider.requests], default=str)
    assert "demo_send_mail" in offered
    system = json.dumps([r["messages"][0] for r in provider.requests], ensure_ascii=False)
    assert "Modo plan" not in system


@pytest.mark.asyncio
async def test_only_swarm_submit_sets_plan_mode():
    assert SubmitJobPayload.model_validate({"text": "hola", "plan_mode": True}).plan_mode
    with pytest.raises(ValueError):
        SubmitJobPayload.model_validate({"text": "hola", "plan_mode": "sí"})

    class Jobs:
        def __init__(self):
            self.requests = []

        async def submit(self, request, **_):
            self.requests.append(request)
            raise RuntimeError("stop")

    jobs = Jobs()
    service = SwarmIpcService(jobs)
    from uuid import uuid4
    for payload in ({"text": "planea", "plan_mode": True}, {"text": "hazlo"}):
        try:
            await service.handle(IpcRequest.model_construct(
                request_id=uuid4(), method="swarm.submit", payload=payload))
        except RuntimeError:
            pass
    assert [r.metadata.get(PLAN_MODE_METADATA) for r in jobs.requests] == [True, None]


class ReadingProvider(Provider):
    async def complete_nvidia_only(self, **kwargs):
        self.requests.append(kwargs)
        role = kwargs["role"]
        if role is AgentRole.SYNTHESIZER:
            return AgentResult(role=role, model_id="synthetic/nvidia", content=PLAN)
        if any(m.get("role") == "tool" for m in kwargs["messages"]):
            return AgentResult(role=role, model_id="synthetic/nvidia", content="Listo.")
        return AgentResult(role=role, model_id="synthetic/nvidia", content="", tool_calls=(
            ToolCall(call_id="read", requested_by=role, tool_name="demo_read_notes",
                     arguments={}),))


@pytest.mark.asyncio
async def test_plan_mode_reads_then_answers_with_the_plan(tmp_path):
    provider, executed = ReadingProvider(), []
    jobs = _jobs(tmp_path, provider, executed)
    result = await _finish(jobs, UserRequest(
        text="Prepara y envía el resumen del proyecto a Ana",
        metadata={PLAN_MODE_METADATA: True}))
    assert executed == ["demo_read_notes"]
    assert result.status is JobStatus.COMPLETED and result.result == PLAN
