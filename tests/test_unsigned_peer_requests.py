from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from test_graph import FakeProvider
from test_plugins import _package

from aegis_core.contracts import (
    UNSIGNED_PEER_METADATA,
    AgentRole,
    PolicyDecision,
    ToolCall,
    UserRequest,
)
from aegis_core.engineering import ENGINEERING_SURFACE_METADATA, EngineeringIpcService
from aegis_core.ipc.server import ipc_peer_signed
from aegis_core.jobs import JobSnapshot, JobStatus
from aegis_core.memory.profile import OwnerProfile
from aegis_core.orchestration.graph import build_nvidia_swarm_graph as build_swarm_graph
from aegis_core.plugins.store import PluginStore
from aegis_core.tools.defaults import default_policy_context


class RecordingJobs:
    def __init__(self) -> None:
        self.requests: list[tuple[UserRequest, UUID | None]] = []

    async def submit(
        self,
        request: UserRequest,
        *,
        conversation_id: UUID | None = None,
        persist_conversation: bool = False,
    ) -> JobSnapshot:
        del persist_conversation
        self.requests.append((request, conversation_id))
        return JobSnapshot(
            job_id=uuid4(),
            request_id=request.request_id,
            conversation_id=conversation_id or uuid4(),
            status=JobStatus.QUEUED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )


def _submit(workspace: Path, conversation_id: UUID | None = None) -> SimpleNamespace:
    payload: dict[str, object] = {
        "text": "Revisa el backend",
        "workspace_path": str(workspace),
        "domain": "backend",
        "research_policy": "offline",
        "inference_policy": "nvidia_only",
    }
    if conversation_id is not None:
        payload["conversation_id"] = str(conversation_id)
    return SimpleNamespace(method="engineering.submit", payload=payload)


@pytest.mark.asyncio
async def test_unsigned_engineering_jobs_are_marked_and_kept_out_of_owner_conversations(
    tmp_path: Path,
) -> None:
    jobs = RecordingJobs()
    service = EngineeringIpcService(jobs, tmp_path)
    owner_conversation = uuid4()

    token = ipc_peer_signed.set(False)
    try:
        first = await service.handle(_submit(tmp_path))
        own_conversation = UUID(first.payload["conversation_id"])
        followup = await service.handle(_submit(tmp_path, own_conversation))
        foreign = await service.handle(_submit(tmp_path, owner_conversation))
    finally:
        ipc_peer_signed.reset(token)
    signed = await service.handle(_submit(tmp_path, owner_conversation))

    assert first.ok is True and followup.ok is True
    assert foreign.ok is False
    assert foreign.error_code == "peer_not_trusted"
    assert signed.ok is True
    (unsigned_request, _), (_, followup_conversation), (signed_request, _) = jobs.requests
    assert unsigned_request.metadata[UNSIGNED_PEER_METADATA] is True
    assert followup_conversation == own_conversation
    assert UNSIGNED_PEER_METADATA not in signed_request.metadata


def test_unsigned_requests_never_speak_for_the_owner() -> None:
    assert OwnerProfile.speaks_for_owner(UserRequest(text="Recuerda que prefiero té")) is True
    assert (
        OwnerProfile.speaks_for_owner(
            UserRequest(text="Recuerda que prefiero té", metadata={UNSIGNED_PEER_METADATA: True})
        )
        is False
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments", "decision"),
    [
        ("network_discover_hosts", {"target": "192.168.1.0/24", "ports": [22]}, "deny"),
        ("filesystem_read_text", {"path": "evidence.txt", "max_bytes": 64}, "allow"),
    ],
)
async def test_unsigned_requests_only_reach_workspace_and_research_tools(
    tmp_path: Path, tool_name: str, arguments: dict[str, object], decision: str
) -> None:
    (tmp_path / "evidence.txt").write_text("observación", encoding="utf-8")
    call = ToolCall(
        call_id="call-1",
        tool_name=tool_name,
        arguments=arguments,
        requested_by=AgentRole.CODE_SECURITY,
    )
    graph = build_swarm_graph(
        FakeProvider(tool_calls=(call,)),
        policy_context=default_policy_context(tmp_path),
    )

    state = await graph.ainvoke(
        {
            "request": UserRequest(
                text="Escanea mi red local y revisa el archivo de evidencia",
                metadata={
                    "interaction_surface": ENGINEERING_SURFACE_METADATA,
                    UNSIGNED_PEER_METADATA: True,
                },
            )
        }
    )

    authorization = state["tool_authorizations"][0]
    if decision == "deny":
        assert authorization.decision is PolicyDecision.DENY
    else:
        assert authorization.decision is PolicyDecision.ALLOW


def test_plugin_records_sealed_with_the_ipc_key_are_resealed_once(tmp_path: Path) -> None:
    directory = tmp_path / "plugins"
    ipc_key = b"i" * 32
    seal_key = b"p" * 32
    legacy = PluginStore(directory, lambda: ipc_key)
    legacy.install(_package())
    original = next(directory.glob("*.json")).read_bytes()

    assert PluginStore(directory, lambda: seal_key).load_all() == ()
    migrated = PluginStore(directory, lambda: seal_key, legacy_seal_key_loader=lambda: ipc_key)
    records = migrated.load_all()

    assert len(records) == 1
    assert next(directory.glob("*.json")).read_bytes() != original
    # Once resealed, the IPC key alone can no longer vouch for the record.
    assert len(PluginStore(directory, lambda: seal_key).load_all()) == 1
    assert PluginStore(directory, lambda: ipc_key).load_all() == ()


def test_a_broken_legacy_loader_does_not_break_the_store(tmp_path: Path) -> None:
    def unavailable() -> bytes:
        raise OSError("keychain locked")

    store = PluginStore(tmp_path / "plugins", lambda: b"p" * 32, legacy_seal_key_loader=unavailable)
    store.install(_package())

    assert len(store.load_all()) == 1
