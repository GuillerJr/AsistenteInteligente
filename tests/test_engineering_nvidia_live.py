"""Explicit opt-in: synthetic conversations/source sent to NVIDIA, no private memory."""

import ast
import asyncio
import json
import os
import time
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from aegis_core.brain.hybrid_client import HybridBrainClient
from aegis_core.config import Settings
from aegis_core.contracts import UserRequest
from aegis_core.engineering import _repository_manifest
from aegis_core.memory.contracts import ConversationRole, ConversationTurn
from aegis_core.orchestration.graph import build_swarm_graph
from aegis_core.providers.nvidia import NvidiaNimClient
from aegis_core.secrets import MacOSKeychain
from aegis_core.tools.defaults import default_policy_context

pytestmark = pytest.mark.skipif(
    os.environ.get("AEGIS_RUN_NVIDIA_ENGINEERING_PROBE") != "1",
    reason="explicit opt-in required for NVIDIA synthetic inference",
)


class NoLocal:
    async def complete(self, **kwargs):
        pytest.fail("NVIDIA engineering probe invoked a local model")

    async def complete_with_confidence(self, **kwargs):
        pytest.fail("NVIDIA engineering probe invoked local confidence")


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["new_app", "context_choice", "source_read", "code_followup"])
async def test_nvidia_engineering_scenario(tmp_path, scenario):
    settings = Settings()
    key = await asyncio.to_thread(
        MacOSKeychain(
            settings.nvidia_keychain_service,
            settings.nvidia_keychain_account,
        ).get
    )
    source = "def precio(cantidad, unitario):\n    return cantidad + unitario\n"
    prompts = {
        "code_followup": [
            "Explícame en dos frases qué es una función en Python.",
            "Ahora dame un ejemplo mínimo que sume dos números. Devuelve solo un bloque Python, "
            "sin explicación fuera del bloque.",
        ],
        "new_app": ["una app", "Una app web para gestionar reservas de una barbería"],
        "context_choice": [
            "Quiero una web para una veterinaria. Dame dos alternativas: "
            "primero React y segundo HTML con CSS y JavaScript sin framework.",
            "La segunda",
        ],
        "source_read": [
            "Lee precio.py: debe calcular cantidad por precio unitario. "
            "Encuentra el error y propone una corrección. No modifiques archivos."
        ],
    }[scenario]
    if scenario == "source_read":
        (tmp_path / "precio.py").write_text(source)
    history = []
    conversation_id = uuid4()
    async with NvidiaNimClient(settings, lambda: key) as nim:
        graph = build_swarm_graph(
            HybridBrainClient(NoLocal(), nim),
            local_provider=NoLocal(),
            policy_context=default_policy_context(tmp_path),
        )
        for prompt in prompts:
            started = time.monotonic()
            partial_times = []

            def on_delta(delta, times=partial_times, start=started):
                if delta and not times:
                    times.append(time.monotonic() - start)

            state = await graph.ainvoke(
                {
                    "request": UserRequest(
                        text=prompt,
                        metadata={
                            "interaction_surface": "engineering_cli",
                            "engineering_inference_policy": "nvidia_only",
                            "engineering_research_policy": "offline",
                            "engineering_workspace": ".",
                            "engineering_repository_manifest": _repository_manifest(
                                tmp_path, prefix=None
                            ),
                        },
                    ),
                    "conversation_history": tuple(history),
                    "stream_callback": on_delta,
                }
            )
            result = state["final_result"]
            print(
                json.dumps(
                    {
                        "scenario": scenario,
                        "model": result.model_id,
                        "seconds": round(time.monotonic() - started, 2),
                        "first_partial_seconds": round(partial_times[0], 2)
                        if partial_times
                        else None,
                        "response": result.content,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            assert not result.model_id.startswith(("local/", "apple/", "mlx"))
            for role, content in (("user", prompt), ("assistant", result.content)):
                history.append(
                    ConversationTurn(
                        conversation_id=conversation_id,
                        sequence=len(history) + 1,
                        role=ConversationRole(role),
                        content=content,
                        created_at=datetime.now(UTC),
                        content_sha256=ConversationTurn.digest_content(content),
                    )
                )
        answer = result.content.casefold()
        if scenario == "new_app":
            assert "barber" in answer
            assert "reserva" in answer
            assert "repositorio está vacío" not in answer
        elif scenario == "context_choice":
            assert "html" in answer and "css" in answer
        elif scenario == "source_read":
            assert state["tool_results"][0].output == source
            assert state["tool_results"][0].success
            assert "cantidad * unitario" in answer or "multiplica" in answer
            assert (tmp_path / "precio.py").read_text() == source
        else:
            code = result.content.strip()
            assert code.startswith("```python\n") and code.endswith("```")
            module = ast.parse(code[len("```python\n") : -len("```")])
            assert any(isinstance(node, ast.Add) for node in ast.walk(module))
