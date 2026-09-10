"""Cloud-only contracts: no native inference, policy downgrade or private memory upload."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from aegis_core.brain.errors import RemoteAuthenticationError, RemoteRateLimitedError
from aegis_core.brain.hybrid_client import HybridBrainClient
from aegis_core.config import Settings
from aegis_core.contracts import AgentRole, UserRequest
from aegis_core.engineering import _repository_manifest
from aegis_core.memory.contracts import ConversationRole, ConversationTurn
from aegis_core.orchestration.engineering_dialogue import remote_engineering_messages
from aegis_core.orchestration.graph import build_swarm_graph
from aegis_core.providers.nvidia import NvidiaGlobalCooldown, NvidiaNimClient
from aegis_core.tools.defaults import default_policy_context


class ForbiddenLocal:
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        pytest.fail(f"NVIDIA-only must not touch a local model: {name}")


def turn(role: str, content: str, sequence: int = 1) -> ConversationTurn:
    return ConversationTurn(
        conversation_id=uuid4(),
        sequence=sequence,
        role=ConversationRole(role),
        content=content,
        created_at=datetime.now(UTC),
        content_sha256=ConversationTurn.digest_content(content),
    )


def request(text: str, root: Path) -> UserRequest:
    return UserRequest(
        text=text,
        metadata={
            "interaction_surface": "engineering_cli",
            "engineering_inference_policy": "nvidia_only",
            "engineering_research_policy": "offline",
            "engineering_workspace": ".",
            "engineering_repository_manifest": _repository_manifest(root, prefix=None),
        },
    )


def response(content: str, *, stream: bool) -> httpx.Response:
    if stream:
        events = [
            {"choices": [{"delta": {"content": content}, "finish_reason": "stop"}]},
        ]
        return httpx.Response(
            200,
            text="".join("data: " + json.dumps(event) + "\n\n" for event in events)
            + "data: [DONE]\n\n",
        )
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"content": content},
                    "finish_reason": "stop",
                }
            ]
        },
    )


@pytest.mark.asyncio
async def test_remote_keeps_dialogue_and_reads_source_without_any_local_model(tmp_path):
    source = "def precio(cantidad, unitario):\n    return cantidad + unitario\n"
    (tmp_path / "price.py").write_text(source)
    payloads = []

    def handler(req):
        payload = json.loads(req.content)
        payloads.append(payload)
        if len(payloads) == 1:
            assert payload["messages"][-1]["content"] == "Lee price.py y corrige el cálculo"
            assert "barbería" in json.dumps(payload, ensure_ascii=False)
            assert payload["tools"]
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "read1",
                                        "type": "function",
                                        "function": {
                                            "name": "filesystem_read_text",
                                            "arguments": json.dumps(
                                                {"path": "price.py", "max_bytes": 4096}
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
        assert source in "".join(m["content"].replace("\\n", "\n") for m in payload["messages"])
        return response(
            "Multiplica cantidad * unitario. Es una propuesta, no una edición.",
            stream=payload["stream"],
        )

    async with NvidiaNimClient(
        Settings(), lambda: "test", transport=httpx.MockTransport(handler)
    ) as nim:
        graph = build_swarm_graph(
            HybridBrainClient(ForbiddenLocal(), nim),
            local_provider=ForbiddenLocal(),
            policy_context=default_policy_context(tmp_path),
        )
        state = await graph.ainvoke(
            {
                "request": request("Lee price.py y corrige el cálculo", tmp_path),
                "conversation_history": (turn("user", "Una app para una barbería"),),
            }
        )
    assert len(payloads) == 2
    assert state["tool_results"][0].success
    assert state["final_result"].model_id == "deepseek-ai/deepseek-v4-pro-0813"
    assert (tmp_path / "price.py").read_text() == source


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,exception",
    [
        (401, RemoteAuthenticationError),
        (403, RemoteAuthenticationError),
        (429, RemoteRateLimitedError),
    ],
)
async def test_cloud_failure_never_downgrades_to_local(status, exception):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(status)

    cooldown = NvidiaGlobalCooldown()
    async with NvidiaNimClient(
        Settings(), lambda: "test", transport=httpx.MockTransport(handler), cooldown=cooldown
    ) as nim:
        brain = HybridBrainClient(ForbiddenLocal(), nim)
        with pytest.raises(exception):
            await brain.complete_nvidia_only(
                role=AgentRole.CODE_SECURITY, messages=[{"role": "user", "content": "Prueba"}]
            )
        if status == 429:
            assert cooldown.active()
            with pytest.raises(RemoteRateLimitedError):
                await brain.complete_nvidia_only(
                    role=AgentRole.PLANNER, messages=[{"role": "user", "content": "Otra"}]
                )
    assert len(calls) == 1


def test_remote_context_allowlist_and_credential_shield():
    with pytest.raises(ValueError, match="unapproved"):
        remote_engineering_messages(
            instruction="", request="", history=(), reference={"retrieved_memory": "private"}
        )
    messages = remote_engineering_messages(
        instruction="Usa evidencia",
        request="Explica",
        history=[
            {
                "role": "user",
                "content": "correo: person@example.com, password=ultraprivate",
            }
        ],
        reference={"tool_results": [{"output": "token=anotherprivatevalue"}]},
    )
    encoded = json.dumps(messages)
    for secret in ("person@example.com", "ultraprivate", "anotherprivatevalue"):
        assert secret not in encoded
    assert "REDACTED" in encoded
