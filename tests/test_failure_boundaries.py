import ast
import asyncio
from pathlib import Path

import pytest

from aegis_core.completion_boundary import optional_completion
from aegis_core.failure_boundary import record_boundary_error


def test_boundary_logs_type_only_without_exception_contents_or_traceback(caplog):
    record_boundary_error(RuntimeError("private-owner-message nvapi-secret"))
    assert "error_type=RuntimeError" in caplog.text
    assert "private-owner-message" not in caplog.text and "nvapi-secret" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
async def test_optional_completion_hides_provider_contents_and_preserves_cancellation(caplog):
    async def unavailable(messages):
        raise RuntimeError("sensitive prompt echoed by provider")

    assert await optional_completion(unavailable, [{"role": "user", "content": "private"}]) == ""
    assert "error_type=RuntimeError" in caplog.text
    assert "sensitive" not in caplog.text and "private" not in caplog.text

    async def cancelled(messages):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await optional_completion(cancelled, [])


def test_generic_exception_boundaries_always_record_error_type():
    root = Path(__file__).resolve().parents[1] / "src/aegis_core"
    missing = []
    for path in root.rglob("*.py"):
        if " 2." in path.name:
            continue
        source = path.read_text()
        for handler in ast.walk(ast.parse(source)):
            if (isinstance(handler, ast.ExceptHandler)
                    and isinstance(handler.type, ast.Name) and handler.type.id == "Exception"):
                body = ast.get_source_segment(source, handler)
                if not any(marker in body for marker in
                           ("record_boundary_error(", ".__name__", "self._failures.")):
                    missing.append(f"{path.relative_to(root)}:{handler.lineno}")
    assert not missing, "Fronteras sin error_type: " + ", ".join(missing)


@pytest.mark.asyncio
async def test_deep_optional_json_fails_closed_in_every_semantic_parser():
    from datetime import UTC, datetime

    from test_task_progress import checkpoint

    from aegis_core.memory.semantic_profile import interpret_owner_memory
    from aegis_core.orchestration.clarification import structured_clarification
    from aegis_core.orchestration.semantic_reference import semantic_reference
    from aegis_core.orchestration.semantic_tools import select_tools
    from aegis_core.scheduling import interpret_schedule_request

    async def nested_json(messages):
        # Fits the 2048-byte parsers but can exceed the decoder's recursion depth.
        return "[" * 1000 + "0" + "]" * 1000

    assert await select_tools("Revisa mi correo", (), (("mail_list_recent", "Leer correo"),),
                              nested_json) == frozenset()
    assert await interpret_owner_memory("Prefiero respuestas breves", (), nested_json) is None
    assert await interpret_schedule_request("Revisa cada mañana", now=datetime.now(UTC),
                                            complete=nested_json) is None
    assert await structured_clarification(
        "Agenda una reunión", "Para agendar necesito fecha y hora. ¿Me las indicas?",
        nested_json,
    ) is None
    resolution = await semantic_reference("y el contenido?", checkpoint(), nested_json)
    assert resolution.identifier is None and resolution.identifiers == ()
