from __future__ import annotations

import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aegis_core.capability_learning import (
    CAPABILITY_RESEARCH_TTL,
    CapabilityLearningCoordinator,
    CapabilityLearningIpcService,
    CapabilityLearningStatus,
    CapabilityLearningStore,
    is_capability_gap_request,
    normalize_capability_goal,
)
from aegis_core.contracts import InputModality, ToolExecutionResult, UserRequest
from aegis_core.ipc.protocol import IpcAuthenticator

AUTHENTICATOR = IpcAuthenticator(bytes.fromhex("42" * 32))


def _research_result() -> ToolExecutionResult:
    return ToolExecutionResult(
        call_id="research-1",
        tool_name="web_research",
        success=True,
        output=json.dumps(
            {
                "query": "sincronizar fotos macOS documentación oficial",
                "results": [
                    {
                        "url": "https://support.apple.com/guide/photos/welcome/mac",
                        "title": "Manual de Fotos para Mac",
                        "content": "Documentación pública para importar y organizar una fototeca.",
                    },
                    {
                        "url": "https://developer.apple.com/documentation/photos",
                        "title": "Photos framework",
                        "content": "Referencia pública para integrar acceso autorizado a fotos.",
                    },
                ],
            }
        ),
        metadata={"source": "public_https", "results": 2},
    )


@pytest.mark.parametrize(
    "text",
    [
        "Sincroniza fotos con mi servidor",
        "Abre Photoshop y prepara el lienzo",
        "Aprende a exportar mi biblioteca de Fotos",
        "Descubre cómo configurar esta integración",
    ],
)
def test_unknown_operational_request_is_a_capability_gap(text: str) -> None:
    assert is_capability_gap_request(
        UserRequest(text=text),
        known_tool_names=frozenset(),
        has_skill=False,
    )


@pytest.mark.parametrize(
    "text",
    [
        "No sincronices fotos con mi servidor",
        "Cuéntame una historia",
        "Mi token nvapi-secret-value no debe salir",
    ],
)
def test_negation_conversation_and_secrets_do_not_trigger_learning(text: str) -> None:
    assert not is_capability_gap_request(
        UserRequest(text=text),
        known_tool_names=frozenset(),
        has_skill=False,
    )


def test_known_tool_or_skill_prevents_capability_scouting() -> None:
    request = UserRequest(text="Configura el volumen")

    assert not is_capability_gap_request(
        request,
        known_tool_names=frozenset({"system_audio_set"}),
        has_skill=False,
    )
    assert not is_capability_gap_request(
        request,
        known_tool_names=frozenset(),
        has_skill=True,
    )


def test_store_persists_private_bounded_research_and_recalls_paraphrase(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "capabilities"
    store = CapabilityLearningStore(directory)
    now = datetime(2026, 8, 27, 12, tzinfo=UTC)

    record = store.record_research(
        "Sincroniza fotos con mi servidor",
        _research_result(),
        now=now,
    )

    assert record is not None
    assert record.status is CapabilityLearningStatus.RESEARCHED
    assert record.expires_at == now + CAPABILITY_RESEARCH_TTL
    assert len(record.sources) == 2
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / f"{record.gap_id}.json").stat().st_mode) == 0o600
    recalled = store.recall("Con mi servidor sincroniza fotos", now=now + timedelta(days=1))
    assert recalled is not None
    assert recalled.gap_id == record.gap_id
    assert store.recall(record.normalized_goal, now=now + timedelta(days=31)) is None


def test_integrity_tampering_is_ignored(tmp_path: Path) -> None:
    store = CapabilityLearningStore(tmp_path / "capabilities")
    record = store.observe("Organiza estas descargas")
    assert record is not None
    path = store.directory / f"{record.gap_id}.json"
    payload = path.read_text(encoding="utf-8").replace("organiza", "elimina", 1)
    path.write_text(payload, encoding="utf-8")

    assert store.get(record.gap_id) is None
    assert store.load_all() == ()


@pytest.mark.asyncio
async def test_unverified_voice_cannot_create_adaptive_memory(tmp_path: Path) -> None:
    store = CapabilityLearningStore(tmp_path / "capabilities")
    coordinator = CapabilityLearningCoordinator(store)
    request = UserRequest(
        text="Sincroniza fotos con mi servidor",
        modalities=frozenset({InputModality.TEXT, InputModality.AUDIO}),
        metadata={"speech_on_device": True},
    )

    assert await coordinator.observe(request, _research_result()) is None
    assert store.load_all() == ()


@pytest.mark.asyncio
async def test_status_ipc_exposes_counts_without_private_goals(tmp_path: Path) -> None:
    store = CapabilityLearningStore(tmp_path / "capabilities")
    store.observe("Organiza estas descargas")
    service = CapabilityLearningIpcService(store)

    response = await service.handle(AUTHENTICATOR.create_request("capabilities.status"))

    assert response.ok is True
    assert response.payload == {"observed": 1, "researched": 0, "total": 1}
    assert "organiza" not in repr(response)


def test_normalization_redacts_personal_identifiers_and_rejects_secrets() -> None:
    normalized = normalize_capability_goal("Envía el informe a owner@example.com")

    assert normalized is not None
    assert "owner@example.com" not in normalized[0]
    assert normalize_capability_goal("usa nvapi-secret-value") is None
