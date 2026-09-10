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
        _helper_path(),
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
                assert result.model_id == "apple/system-language-model"
                assert "¿" in result.content
                assert result.content.count("?") == 1
                assert len(result.content.split()) <= 25
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


def _helper_path() -> Path:
    return Path(
        os.environ.get(
            "AEGIS_ENGINEERING_PROBE_HELPER",
            str(Path.home() / "Applications/Jarvis.app/Contents/Helpers/jarvis-local-brain"),
        )
    )


@pytest.mark.asyncio
async def test_native_persistent_process_does_not_share_implicit_history() -> None:
    apple = AppleLocalModelClient(
        _helper_path(), timeout_seconds=30, first_event_timeout_seconds=10
    )
    assert apple.is_available()
    instruction = (
        "Responde en español. Usa solo los datos de esta petición. Si falta un dato, pregunta."
    )
    try:
        from aegis_core.contracts import AgentRole

        for prompt in (
            "Mi proyecto se llama FARO-7391 y es para una floristería. Confirma que lo entendiste.",
            "¿Cómo se llama mi proyecto?",
        ):
            result = await apple.complete(
                role=AgentRole.CODE_SECURITY,
                messages=[
                    {"role": "system", "content": instruction},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=128,
                temperature=0,
            )
            print(json.dumps({"input": prompt, "response": result.content}, ensure_ascii=False))
        assert "FARO" not in result.content.upper()
        assert "florister" not in result.content.casefold()
        assert "?" in result.content or "no" in result.content.casefold()
    finally:
        await apple.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("history", "prompt", "required", "forbidden"),
    [
        ((), "Necesito algo para mi negocio", ("?",), ("MVP", "hábitos", "inventario")),
        ((), "Arregla eso", ("?",), ("he corregido", "he modificado", "inventario", "```")),
        (
            ("Quiero gestionar citas de una barbería", "¿En web o en móvil?"),
            "web",
            ("cita", "reserva", "barbería"),
            ("¿Qué debe hacer", "hábitos"),
        ),
        (
            ("Una aplicación para una barbería", "Podemos diseñar las reservas."),
            "No, me equivoqué: para una veterinaria",
            ("veterinaria", "mascota", "animal"),
            ("corte de pelo",),
        ),
        (
            ("Necesito una web", "Podemos usar React o HTML sencillo. ¿Cuál prefieres?"),
            "la segunda",
            ("html",),
            ("¿Qué segunda",),
        ),
        (
            (),
            "Explícame qué es una API con una analogía sencilla",
            ("intermediario", "comunic", "mesero", "camarero", "puente", "mensajero"),
            ("repository", "sample_complete"),
        ),
        (
            (),
            "ayudame a pensar una web para vender mis dibujos",
            ("dibuj",),
            ("cannot be fulfilled", "he creado"),
        ),
        (
            ("Quiero una web para una tienda", "Podemos empezar por el catálogo."),
            "Cambiemos de tema: ¿por qué el cielo es azul?",
            ("luz",),
            ("catálogo", "MVP"),
        ),
        (
            (),
            "¿Qué archivos existen en este repositorio?",
            ("vacío", "no hay", "ningún archivo", "sin archivos"),
            ("main.py", "package.json", "README.md"),
        ),
    ],
)
async def test_native_understands_varied_conversational_turns(
    tmp_path: Path,
    history: tuple[str, ...],
    prompt: str,
    required: tuple[str, ...],
    forbidden: tuple[str, ...],
) -> None:
    apple = AppleLocalModelClient(
        _helper_path(), timeout_seconds=30, first_event_timeout_seconds=10
    )
    assert apple.is_available()
    cascade = LocalFoundationCascadeClient(None, apple)
    conversation_id = uuid4()
    turns = tuple(
        ConversationTurn(
            conversation_id=conversation_id,
            sequence=index + 1,
            role=ConversationRole.USER if index % 2 == 0 else ConversationRole.ASSISTANT,
            content=text,
            content_sha256=ConversationTurn.digest_content(text),
            created_at=datetime.now(UTC),
        )
        for index, text in enumerate(history)
    )
    try:
        started = time.monotonic()
        state = await build_swarm_graph(HybridBrainClient(cascade, ForbiddenRemote())).ainvoke(
            {
                "request": UserRequest(
                    text=prompt,
                    metadata={
                        "interaction_surface": ENGINEERING_SURFACE_METADATA,
                        ENGINEERING_DOMAIN_METADATA: "auto",
                        ENGINEERING_WORKSPACE_METADATA: ".",
                        ENGINEERING_INFERENCE_METADATA: "local_only",
                        ENGINEERING_RESEARCH_METADATA: "offline",
                        ENGINEERING_MANIFEST_METADATA: _repository_manifest(tmp_path, prefix=None),
                    },
                ),
                "conversation_history": turns,
            }
        )
        result = state["final_result"]
        print(
            json.dumps(
                {
                    "input": prompt,
                    "response": result.content,
                    "elapsed_s": round(time.monotonic() - started, 2),
                },
                ensure_ascii=False,
            )
        )
        text = result.content.casefold()
        assert any(term.casefold() in text for term in required)
        assert not any(term.casefold() in text for term in forbidden)
        assert not result.tool_calls and not state.get("tool_results")
        assert not list(tmp_path.iterdir())
    finally:
        await cascade.aclose()
