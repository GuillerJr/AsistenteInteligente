from __future__ import annotations

import math
from collections.abc import Callable, Iterable

from aegis_core.conversation_quality import (
    ConversationQualityEvaluator,
    ConversationQualityFlag,
)
from aegis_core.feedback import OwnerFeedback
from aegis_core.job_contracts import BrainTarget, JobEvaluation, JobStatus
from aegis_core.job_state import MutableJob

QUALITY_MINIMUM_SAMPLES = 20
QUALITY_SUCCESS_RATE_TARGET = 0.95
QUALITY_FIRST_PARTIAL_P95_TARGET_MS = 2_000
QUALITY_CONVERSATION_P95_TARGET_MS = 8_000
QUALITY_RESPONSE_PASS_RATE_TARGET = 0.95
QUALITY_OWNER_RECOGNITION_TARGET = 0.90
QUALITY_OWNER_FEEDBACK_TARGET = 0.80
QUALITY_OWNER_FEEDBACK_MINIMUM_SAMPLES = 5
QUALITY_REPAIR_RECOVERY_TARGET = 0.80
QUALITY_REPAIR_RECOVERY_MINIMUM_SAMPLES = 3

_CONVERSATION_QUALITY_EVALUATOR = ConversationQualityEvaluator()


def brain_target(model_id: str | None, tool_name: str | None) -> BrainTarget:
    if model_id == "apple/system-language-model":
        return BrainTarget.LOCAL
    if model_id is not None and model_id.startswith("local/"):
        return BrainTarget.DETERMINISTIC
    if model_id:
        return BrainTarget.NVIDIA
    if tool_name:
        return BrainTarget.DETERMINISTIC
    return BrainTarget.UNKNOWN


def evaluate_job(
    job: MutableJob,
    status: JobStatus,
    *,
    monotonic_clock: Callable[[], float],
) -> JobEvaluation:
    model_id = job.model_id
    finished = monotonic_clock()
    wall_latency = max(0, round((finished - job.started_monotonic) * 1_000))
    wall_first_partial = (
        round((job.first_partial_monotonic - job.started_monotonic) * 1_000)
        if job.first_partial_monotonic is not None
        else None
    )
    conversation_quality = (
        _CONVERSATION_QUALITY_EVALUATOR.evaluate(
            request=job.request_text,
            response=job.result,
        )
        if status is JobStatus.COMPLETED
        and job.tool_name is None
        and job.result
        and not job.owner_feedback_request
        else None
    )
    return JobEvaluation(
        brain=brain_target(model_id, job.tool_name),
        model_id=model_id,
        total_latency_ms=max(0, wall_latency - job.confirmation_wait_ms),
        wall_latency_ms=wall_latency,
        confirmation_wait_ms=job.confirmation_wait_ms,
        first_partial_latency_ms=job.first_partial_active_latency_ms,
        wall_first_partial_latency_ms=(
            max(0, wall_first_partial) if wall_first_partial is not None else None
        ),
        stream_chunks=job.stream_chunks,
        tool_name=job.tool_name,
        succeeded=status is JobStatus.COMPLETED,
        outcome_verified=(
            status is JobStatus.COMPLETED and (job.tool_name is None or job.action_verified)
        ),
        voice_request=job.voice_request,
        owner_verified=job.owner_verified,
        dialogue_mode=(
            conversation_quality.dialogue_mode if conversation_quality is not None else None
        ),
        response_quality_score=(
            conversation_quality.score if conversation_quality is not None else None
        ),
        response_quality_passed=(
            conversation_quality.passed if conversation_quality is not None else None
        ),
        response_word_count=(
            conversation_quality.word_count if conversation_quality is not None else None
        ),
        response_sentence_count=(
            conversation_quality.sentence_count if conversation_quality is not None else None
        ),
        response_quality_flags=(
            conversation_quality.flags if conversation_quality is not None else ()
        ),
        feedback_event=job.owner_feedback_request,
        repair_attempt=job.repair_attempt,
    )


def build_job_metrics(evaluations: Iterable[JobEvaluation]) -> dict[str, object]:
    samples = tuple(evaluations)
    brains = tuple(brain_target(item.model_id, item.tool_name) for item in samples)
    latencies = sorted(item.total_latency_ms for item in samples)
    wall_latencies = sorted(
        item.wall_latency_ms if item.wall_latency_ms is not None else item.total_latency_ms
        for item in samples
    )
    confirmation_waits = sorted(
        item.confirmation_wait_ms for item in samples if item.confirmation_wait_ms > 0
    )
    first_partials = sorted(
        item.first_partial_latency_ms
        for item in samples
        if item.first_partial_latency_ms is not None
    )
    wall_first_partials = sorted(
        item.wall_first_partial_latency_ms
        if item.wall_first_partial_latency_ms is not None
        else item.first_partial_latency_ms
        for item in samples
        if item.first_partial_latency_ms is not None
    )
    completed = sum(item.succeeded for item in samples)
    conversations = tuple(
        item for item in samples if item.tool_name is None and not item.feedback_event
    )
    actions = tuple(item for item in samples if item.tool_name is not None)
    feedback_events = tuple(item for item in samples if item.feedback_event)
    conversation_latencies = sorted(item.total_latency_ms for item in conversations)
    action_successes = sum(item.succeeded and item.outcome_verified for item in actions)
    success_rate = round(completed / len(samples), 4) if samples else 0.0
    action_success_rate = round(action_successes / len(actions), 4) if actions else None
    voice_jobs = tuple(item for item in samples if item.voice_request)
    owner_recognition_rate = (
        round(sum(item.owner_verified for item in voice_jobs) / len(voice_jobs), 4)
        if voice_jobs
        else None
    )
    quality_assessed = tuple(
        item for item in conversations if item.response_quality_passed is not None
    )
    response_quality_pass_rate = (
        round(
            sum(item.response_quality_passed is True for item in quality_assessed)
            / len(quality_assessed),
            4,
        )
        if quality_assessed
        else None
    )
    response_quality_scores = sorted(
        item.response_quality_score
        for item in quality_assessed
        if item.response_quality_score is not None
    )
    response_quality_flags = {
        flag.value: sum(flag in item.response_quality_flags for item in quality_assessed)
        for flag in ConversationQualityFlag
    }
    feedback_evaluations = tuple(item for item in samples if item.owner_feedback is not None)
    owner_feedback_helpful_rate = (
        round(
            sum(item.owner_feedback is OwnerFeedback.HELPFUL for item in feedback_evaluations)
            / len(feedback_evaluations),
            4,
        )
        if feedback_evaluations
        else None
    )
    repair_attempts = tuple(item for item in samples if item.repair_attempt)
    rated_repairs = tuple(item for item in repair_attempts if item.owner_feedback is not None)
    repair_recovery_rate = (
        round(
            sum(item.owner_feedback is OwnerFeedback.HELPFUL for item in rated_repairs)
            / len(rated_repairs),
            4,
        )
        if rated_repairs
        else None
    )
    first_partial_p95 = percentile(first_partials, 0.95)
    conversation_p95 = percentile(conversation_latencies, 0.95)
    quality_checks = {
        "success_rate": success_rate >= QUALITY_SUCCESS_RATE_TARGET,
        "first_partial_p95_ms": (
            first_partial_p95 is not None
            and first_partial_p95 <= QUALITY_FIRST_PARTIAL_P95_TARGET_MS
        ),
        "conversation_p95_ms": (
            conversation_p95 is not None
            and conversation_p95 <= QUALITY_CONVERSATION_P95_TARGET_MS
        ),
        "action_success_rate": (
            action_success_rate >= QUALITY_SUCCESS_RATE_TARGET
            if action_success_rate is not None
            else None
        ),
        "owner_recognition_rate": (
            owner_recognition_rate >= QUALITY_OWNER_RECOGNITION_TARGET
            if owner_recognition_rate is not None
            else None
        ),
        "response_quality_pass_rate": (
            response_quality_pass_rate >= QUALITY_RESPONSE_PASS_RATE_TARGET
            if response_quality_pass_rate is not None
            else None
        ),
        "owner_feedback_helpful_rate": (
            owner_feedback_helpful_rate >= QUALITY_OWNER_FEEDBACK_TARGET
            if len(feedback_evaluations) >= QUALITY_OWNER_FEEDBACK_MINIMUM_SAMPLES
            and owner_feedback_helpful_rate is not None
            else None
        ),
        "repair_recovery_rate": (
            repair_recovery_rate >= QUALITY_REPAIR_RECOVERY_TARGET
            if len(rated_repairs) >= QUALITY_REPAIR_RECOVERY_MINIMUM_SAMPLES
            and repair_recovery_rate is not None
            else None
        ),
    }
    required_checks = tuple(value for value in quality_checks.values() if value is not None)
    quality_status = (
        "insufficient_data"
        if len(samples) < QUALITY_MINIMUM_SAMPLES
        else "competitive"
        if required_checks and all(required_checks)
        else "needs_attention"
    )
    return {
        "jobs": len(samples),
        "completed": completed,
        "failed_or_cancelled": len(samples) - completed,
        "success_rate": success_rate,
        "latency_ms": {
            "active_total_p50_ms": percentile(latencies, 0.50),
            "active_total_p95_ms": percentile(latencies, 0.95),
            "active_first_partial_p50_ms": percentile(first_partials, 0.50),
            "active_first_partial_p95_ms": first_partial_p95,
            "wall_time_p50_ms": percentile(wall_latencies, 0.50),
            "wall_time_p95_ms": percentile(wall_latencies, 0.95),
            "active_p50": percentile(latencies, 0.50),
            "active_p95": percentile(latencies, 0.95),
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "wall_p95": percentile(wall_latencies, 0.95),
            "confirmation_wait_p95": percentile(confirmation_waits, 0.95),
            "active_first_partial_p50": percentile(first_partials, 0.50),
            "active_first_partial_p95": first_partial_p95,
            "first_partial_p50": percentile(first_partials, 0.50),
            "first_partial_p95": first_partial_p95,
            "wall_first_partial_p95": percentile(wall_first_partials, 0.95),
            "active_conversation_p95": conversation_p95,
            "conversation_p95": conversation_p95,
        },
        "brain": {
            target.value: sum(brain is target for brain in brains) for target in BrainTarget
        },
        "quality": {
            "status": quality_status,
            "minimum_samples": QUALITY_MINIMUM_SAMPLES,
            "targets": {
                "success_rate": QUALITY_SUCCESS_RATE_TARGET,
                "first_partial_p95_ms": QUALITY_FIRST_PARTIAL_P95_TARGET_MS,
                "conversation_p95_ms": QUALITY_CONVERSATION_P95_TARGET_MS,
                "action_success_rate": QUALITY_SUCCESS_RATE_TARGET,
                "owner_recognition_rate": QUALITY_OWNER_RECOGNITION_TARGET,
                "response_quality_pass_rate": QUALITY_RESPONSE_PASS_RATE_TARGET,
                "owner_feedback_helpful_rate": QUALITY_OWNER_FEEDBACK_TARGET,
                "owner_feedback_minimum_samples": QUALITY_OWNER_FEEDBACK_MINIMUM_SAMPLES,
                "repair_recovery_rate": QUALITY_REPAIR_RECOVERY_TARGET,
                "repair_recovery_minimum_samples": QUALITY_REPAIR_RECOVERY_MINIMUM_SAMPLES,
            },
            "observed": {
                "success_rate": success_rate,
                "first_partial_p95_ms": first_partial_p95,
                "conversation_p95_ms": conversation_p95,
                "action_success_rate": action_success_rate,
                "owner_recognition_rate": owner_recognition_rate,
                "response_quality_pass_rate": response_quality_pass_rate,
                "response_quality_score_p50": percentile(response_quality_scores, 0.50),
                "response_quality_assessed": len(quality_assessed),
                "response_quality_flags": response_quality_flags,
                "owner_feedback_helpful_rate": owner_feedback_helpful_rate,
                "owner_feedback_count": len(feedback_evaluations),
                "feedback_jobs": len(feedback_events),
                "repair_attempts": len(repair_attempts),
                "repair_rated_count": len(rated_repairs),
                "repair_recovery_rate": repair_recovery_rate,
                "conversation_jobs": len(conversations),
                "action_jobs": len(actions),
                "voice_jobs": len(voice_jobs),
            },
            "passes": quality_checks,
        },
    }


def percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    index = max(0, min(len(values) - 1, math.ceil(len(values) * fraction) - 1))
    return values[index]
