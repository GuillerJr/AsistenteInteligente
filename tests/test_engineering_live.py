"""Opt-in native-model checks with synthetic dialogue; no personal store or network."""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from aegis_core.brain.hybrid_client import HybridBrainClient, LocalFoundationCascadeClient
from aegis_core.contracts import UserRequest
from aegis_core.engineering import (
    ENGINEERING_DOMAIN_METADATA,
    ENGINEERING_INFERENCE_METADATA,
    ENGINEERING_MANIFEST_METADATA,
    ENGINEERING_RESEARCH_METADATA,
    ENGINEERING_SURFACE_METADATA,
    ENGINEERING_WORKSPACE_METADATA,
    _repository_manifest,
)
from aegis_core.memory.contracts import ConversationRole, ConversationTurn
from aegis_core.orchestration.graph import build_swarm_graph
from aegis_core.providers.apple import AppleLocalModelClient

pytestmark = pytest.mark.skipif(
    os.environ.get("AEGIS_RUN_LOCAL_ENGINEERING_PROBE") != "1",
    reason="requires explicit opt-in to the installed Apple Foundation Models helper",
)


class ForbiddenRemote:
    async def complete(self, **kwargs):
        raise AssertionError("The local engineering probe must never call a remote provider")


@pytest.mark.asyncio
async def test_native_engineering_helps_define_a_new_app(tmp_path: Path) -> None:
    apple = AppleLocalModelClient(
        Path.home() / "Applications/Jarvis.app/Contents/Helpers/jarvis-local-brain",
        timeout_seconds=30,
        first_event_timeout_seconds=10,
    )
    assert apple.is_available(), "The installed native model must be available"
    cascade = LocalFoundationCascadeClient(None, apple)
    brain = HybridBrainClient(cascade, ForbiddenRemote())
    graph = build_swarm_graph(brain)
    conversation_id = uuid4()
    history: list[ConversationTurn] = []
    try:
        for prompt in ("hola", "una app", "Una app web para gestionar reservas de una barbería"):
            started = time.monotonic()
            state = await graph.ainvoke(
                {
                    "request": UserRequest(
                        text=prompt,
                        metadata={
                            "interaction_surface": ENGINEERING_SURFACE_METADATA,
                            ENGINEERING_DOMAIN_METADATA: "auto",
                            ENGINEERING_WORKSPACE_METADATA: ".",
                            ENGINEERING_RESEARCH_METADATA: "offline",
                            ENGINEERING_INFERENCE_METADATA: "local_only",
                            ENGINEERING_MANIFEST_METADATA: _repository_manifest(
                                tmp_path, prefix=None
                            ),
                        },
                    ),
                    "conversation_history": tuple(history),
                }
            )
            result = state["final_result"]
            print(
                json.dumps(
                    {
                        "input": prompt,
                        "response": result.content,
                        "model": result.model_id,
                        "elapsed_s": round(time.monotonic() - started, 2),
                    },
                    ensure_ascii=False,
                )
            )
            assert not result.tool_calls
            assert not state.get("tool_results")
            if prompt == "una app":
                assert "¿" in result.content
                assert result.content.count("?") == 1
                assert len(result.content.split()) <= 80
                assert not any(
                    term in result.content.casefold()
                    for term in (
                        "sample_complete",
                        "repository",
                        "inventario",
                        "cannot be fulfilled",
                    )
                )
            elif "barbería" in prompt:
                assert (
                    "reservas" in result.content.casefold()
                    or "barbería" in result.content.casefold()
                )
                assert any(
                    term in result.content.casefold()
                    for term in ("horario", "disponibilidad", "servicio", "agenda")
                ), "A concrete goal needs an actionable proposal, not a paraphrase"
                assert not any(
                    term in result.content.casefold()
                    for term in (
                        "he creado",
                        "he ejecutado",
                        "archivos creados",
                        "pruebas aprobadas",
                    )
                )
            for role, text in (
                (ConversationRole.USER, prompt),
                (ConversationRole.ASSISTANT, result.content),
            ):
                history.append(
                    ConversationTurn(
                        conversation_id=conversation_id,
                        sequence=len(history) + 1,
                        role=role,
                        content=text,
                        created_at=datetime.now(UTC),
                        content_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    )
                )
        assert list(tmp_path.iterdir()) == []
    finally:
        await cascade.aclose()
