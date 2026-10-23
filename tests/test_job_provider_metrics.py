"""Provider provenance is configured by the daemon, independent of model spelling."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from aegis_core.contracts import AgentResult, AgentRole, UserRequest
from aegis_core.evaluation import SQLiteEvaluationStore
from aegis_core.job_contracts import BrainTarget, JobEvaluation, JobStatus
from aegis_core.job_metrics import brain_target
from aegis_core.jobs import SwarmJobManager


class SyntheticGraph:
    async def ainvoke(self, input):
        return {"final_result": AgentResult(
            role=AgentRole.SYNTHESIZER, model_id="shared-model-id", content="Respuesta sintética.",
        )}


async def test_codex_job_metrics_preserve_stored_nvidia_provenance_for_the_same_model(tmp_path):
    tmp_path.chmod(0o700)
    store = SQLiteEvaluationStore(tmp_path / "evaluations.sqlite3")
    store.initialize()
    old = JobEvaluation(
        brain=BrainTarget.NVIDIA, model_id="shared-model-id", total_latency_ms=10,
        first_partial_latency_ms=None, stream_chunks=0, succeeded=True,
        outcome_verified=True, voice_request=False, owner_verified=False,
    )
    store.append(uuid4(), old, datetime.now(UTC))
    manager = SwarmJobManager(
        SyntheticGraph(), inference_provider="codex", evaluation_store=store,
    )
    try:
        pending = await manager.submit(UserRequest(text="Responde una frase"))
        for _ in range(200):
            result = await manager.status(pending.job_id)
            if result.status is JobStatus.COMPLETED:
                break
            await asyncio.sleep(0.001)
        assert result.evaluation.brain is BrainTarget.CODEX
        metrics = await manager.metrics()
        assert metrics["brain"]["codex"] == 1
        assert metrics["brain"]["nvidia"] == 1
        persisted = store.load_recent()
        assert {item.evaluation.brain for item in persisted} == {
            BrainTarget.CODEX, BrainTarget.NVIDIA,
        }
        # Reloading must preserve the explicit provider; bare model IDs do not
        # become NVIDIA merely because the transport's name is absent from them.
        restarted = SwarmJobManager(SyntheticGraph(), evaluation_store=store)
        try:
            assert (await restarted.metrics())["brain"]["codex"] == 1
        finally:
            await restarted.close()
    finally:
        await manager.close()


def test_codex_provenance_keeps_deterministic_and_local_results_separate():
    assert brain_target("bare-model", None, inference_provider="codex") is BrainTarget.CODEX
    assert brain_target("bare-model", None) is BrainTarget.NVIDIA
    assert brain_target("local/greeting", None, inference_provider="codex") \
        is BrainTarget.DETERMINISTIC
    assert brain_target(None, "filesystem_read_text", inference_provider="codex") \
        is BrainTarget.DETERMINISTIC
    assert brain_target("apple/system-language-model", None, inference_provider="codex") \
        is BrainTarget.LOCAL
