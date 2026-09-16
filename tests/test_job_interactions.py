from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from aegis_core.contracts import PolicyDecision, ToolAuthorization
from aegis_core.conversation_quality import ConversationQualityEvaluator
from aegis_core.job_contracts import BrainTarget, JobEvaluation, JobStatus
from aegis_core.job_interactions import confirmation_preview, task_input_request
from aegis_core.job_state import MutableJob


def _authorization(arguments):
    return ToolAuthorization(
        call_id=str(uuid4()),
        tool_name="mail_send_message",
        call_digest="a" * 64,
        decision=PolicyDecision.REQUIRE_CONFIRMATION,
        reason_code="confirmation_required",
        normalized_arguments=arguments,
    )


def _evaluation(request="Agenda una reunión mañana", result="¿A qué hora será la reunión?"):
    quality = ConversationQualityEvaluator().evaluate(request=request, response=result)
    return JobEvaluation(
        brain=BrainTarget.NVIDIA,
        total_latency_ms=100,
        stream_chunks=0,
        succeeded=True,
        outcome_verified=False,
        voice_request=False,
        owner_verified=False,
        dialogue_mode=quality.dialogue_mode,
        response_quality_score=quality.score,
        response_quality_passed=quality.passed,
        response_word_count=quality.word_count,
        response_sentence_count=quality.sentence_count,
        response_quality_flags=quality.flags,
    )


def test_preview_preserves_entire_body_and_all_normalized_arguments():
    body = "Primera línea.\n" + "Contenido a revisar. " * 80
    arguments = {"recipients": ["example@example.com"], "subject": "Reunión", "body": body}
    preview = confirmation_preview(_authorization(arguments))
    fields = {field.label: field.value for field in preview.fields}
    assert fields["Contenido"] == body
    assert "example@example.com" in fields["Destinatarios"]
    assert len(fields) == len(arguments)


@pytest.mark.parametrize(
    "arguments",
    [
        {"body": "x" * 16_001},
        {f"field_{i}": "valor" for i in range(33)},
        {"first": "x" * 13_000, "second": "y" * 13_000},
        {"body": "-----BEGIN PRIVATE KEY-----\ncredential\n-----END PRIVATE KEY-----"},
    ],
)
def test_preview_rejects_incomplete_or_secret_material(arguments):
    with pytest.raises(ValueError):
        confirmation_preview(_authorization(arguments))


def test_clarification_is_explicit_bounded_and_expires():
    now = datetime.now(UTC)
    input_request = task_input_request(
        request_text="Agenda una reunión mañana",
        result="¿A qué hora será la reunión?",
        evaluation=_evaluation(),
        now=now,
    )
    assert input_request is not None
    assert input_request.question == "¿A qué hora será la reunión?"
    assert input_request.expires_at == now + timedelta(minutes=5)


@pytest.mark.parametrize(
    ("user_text", "result"),
    [
        ("Hola, ¿cómo estás?", "¿Qué tal tu día?"),
        ("Agenda una reunión", "La reunión está creada. ¿Quieres algo más?"),
        ("Agenda una reunión", "¿A qué hora? ¿Con quién?"),
        ("Agenda una reunión", "El archivo dice: ¿A qué hora? Texto posterior."),
        ("Agenda una reunión", "¿Quieres que haga algo más?"),
    ],
)
def test_social_or_unstructured_questions_do_not_open_forms(user_text, result):
    assert (
        task_input_request(
            request_text=user_text,
            result=result,
            evaluation=_evaluation(user_text, result),
            now=datetime.now(UTC),
        )
        is None
    )


def test_tool_result_never_opens_input_form():
    evaluation = _evaluation().model_copy(update={"tool_name": "calendar_create_event"})
    assert (
        task_input_request(
            request_text="Agenda una reunión",
            result="¿A qué hora será?",
            evaluation=evaluation,
            now=datetime.now(UTC),
        )
        is None
    )


def test_snapshot_publishes_clarification_only_after_completed_non_tool_task():
    now = datetime.now(UTC)
    job = MutableJob(
        job_id=uuid4(),
        request_id=uuid4(),
        authorization_request=None,
        request_text="Agenda una reunión mañana",
        conversation_id=None,
        status=JobStatus.RUNNING,
        created_at=now,
        updated_at=now,
    )
    assert job.snapshot().input_request is None
    job.status = JobStatus.COMPLETED
    job.result = "¿A qué hora será la reunión?"
    job.evaluation = _evaluation()
    assert job.snapshot().input_request is not None
    assert job.snapshot().input_request == job.snapshot().input_request
