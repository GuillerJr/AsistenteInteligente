import asyncio
from pathlib import Path

import pytest

from aegis_core.contracts import AgentResult, AgentRole, UserRequest
from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.jobs import JobStatus, SwarmJobManager
from aegis_core.tcc_privacy import TCCPrivacyIpcService
from aegis_core.tools.audit import HashChainAuditLog

AUTHENTICATOR = IpcAuthenticator(bytes.fromhex("6d" * 32))


class BlockingGraph:
    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def ainvoke(self, input: dict[str, object]) -> dict[str, object]:
        del input
        await self.release.wait()
        return {
            "final_result": AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id="test/local",
                content="completed",
            )
        }


@pytest.mark.asyncio
async def test_tcc_denial_cancels_exact_job_without_retry_and_audits(
    tmp_path: Path,
) -> None:
    graph = BlockingGraph()
    jobs = SwarmJobManager(graph)
    audit = HashChainAuditLog(tmp_path / "audit" / "audit.jsonl")
    service = TCCPrivacyIpcService(jobs, audit)
    submitted = await jobs.submit(UserRequest(text="Analiza la ventana activa"))
    request = AUTHENTICATOR.create_request(
        "privacy.permission.denied",
        {
            "error_code": "tcc_permission_denied",
            "permission": "screen_recording",
            "operation": "screen_turn",
            "job_id": str(submitted.job_id),
        },
    )

    try:
        response = await service.handle(request)
        snapshot = await jobs.status(submitted.job_id)
    finally:
        graph.release.set()
        await jobs.close()

    assert response.ok is True
    assert response.payload == {"acknowledged": True, "job_cancelled": True}
    assert snapshot.status is JobStatus.CANCELLED
    record = audit.verify()[-1]
    assert record.event_type == "tcc_permission_denied"
    assert record.call_id == request.nonce
    assert record.data == {
        "error_code": "tcc_permission_denied",
        "permission": "screen_recording",
        "operation": "screen_turn",
        "job_present": True,
        "job_cancelled": True,
        "job_state": "cancelled",
        "retry_allowed": False,
    }


@pytest.mark.asyncio
async def test_tcc_denial_without_job_is_acknowledged_and_not_retried(
    tmp_path: Path,
) -> None:
    jobs = SwarmJobManager(BlockingGraph())
    audit = HashChainAuditLog(tmp_path / "audit" / "audit.jsonl")
    service = TCCPrivacyIpcService(jobs, audit)
    request = AUTHENTICATOR.create_request(
        "privacy.permission.denied",
        {
            "error_code": "tcc_permission_denied",
            "permission": "accessibility",
            "operation": "computer_control",
        },
    )

    try:
        response = await service.handle(request)
    finally:
        await jobs.close()

    assert response.ok is True
    assert response.payload == {"acknowledged": True, "job_cancelled": False}
    assert audit.verify()[-1].data["retry_allowed"] is False


@pytest.mark.asyncio
async def test_tcc_service_rejects_unrecognized_error_code(tmp_path: Path) -> None:
    jobs = SwarmJobManager(BlockingGraph())
    audit = HashChainAuditLog(tmp_path / "audit" / "audit.jsonl")
    service = TCCPrivacyIpcService(jobs, audit)

    try:
        response = await service.handle(
            AUTHENTICATOR.create_request(
                "privacy.permission.denied",
                {
                    "error_code": "capture_failed",
                    "permission": "screen_recording",
                    "operation": "screen_turn",
                },
            )
        )
    finally:
        await jobs.close()

    assert response.ok is False
    assert response.error_code == "invalid_payload"
    assert audit.verify() == ()
