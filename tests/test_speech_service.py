import io
import os
import stat
import wave
from pathlib import Path

import pytest

from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.providers.nvidia import NvidiaNimError
from aegis_core.speech import (
    SpeechArtifactError,
    SpeechArtifactStore,
    SpeechSynthesisIpcService,
)

AUTHENTICATOR = IpcAuthenticator(bytes.fromhex("77" * 32))


def _wav_bytes(*, frames: int = 441) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(44_100)
        wav.writeframes(b"\0\0" * frames)
    return output.getvalue()


def test_speech_artifact_store_creates_private_digest_bound_file(tmp_path: Path) -> None:
    directory = tmp_path / "speech"
    store = SpeechArtifactStore(directory)
    audio = _wav_bytes()

    artifact = store.create(audio)
    path = directory / artifact.file_name

    assert artifact.file_name == f"jarvis-tts-{artifact.token}.wav"
    assert artifact.byte_count == len(audio)
    assert len(artifact.sha256) == 64
    assert path.read_bytes() == audio
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert store.release(artifact.token) is True
    assert not path.exists()
    assert store.release(artifact.token) is False


def test_speech_artifact_store_expires_only_recognized_private_audio(
    tmp_path: Path,
) -> None:
    now = 1_000.0
    directory = tmp_path / "speech"
    store = SpeechArtifactStore(directory, ttl_seconds=10, clock=lambda: now)
    stale = store.create(_wav_bytes())
    stale_path = directory / stale.file_name
    os.utime(stale_path, (now - 11, now - 11))
    unrelated = directory / "keep.txt"
    unrelated.write_text("preserve", encoding="utf-8")

    current = store.create(_wav_bytes(frames=220))

    assert not stale_path.exists()
    assert unrelated.read_text(encoding="utf-8") == "preserve"
    assert (directory / current.file_name).exists()


def test_speech_artifact_store_rejects_symlinked_directory(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    linked = tmp_path / "speech"
    linked.symlink_to(target, target_is_directory=True)

    with pytest.raises(SpeechArtifactError, match="directory is unsafe"):
        SpeechArtifactStore(linked)


def test_speech_artifact_store_closes_only_its_own_files(tmp_path: Path) -> None:
    directory = tmp_path / "speech"
    store = SpeechArtifactStore(directory)
    foreign = directory / f"jarvis-tts-{'a' * 32}.wav"
    foreign.write_bytes(_wav_bytes())
    foreign.chmod(0o600)

    store.close()

    assert foreign.exists()


@pytest.mark.asyncio
async def test_speech_ipc_synthesizes_and_releases_without_returning_text(
    tmp_path: Path,
) -> None:
    received: list[str] = []

    async def synthesize(text: str) -> bytes:
        received.append(text)
        return _wav_bytes()

    directory = tmp_path / "speech"
    service = SpeechSynthesisIpcService(
        synthesize,
        SpeechArtifactStore(directory),
    )
    result = await service.handle(
        AUTHENTICATOR.create_request(
            "speech.synthesize",
            {"text": "  Estado   de seguridad. "},
        )
    )

    assert result.ok is True
    assert received == ["Estado de seguridad."]
    assert "Estado" not in result.model_dump_json()
    assert (directory / result.payload["file_name"]).exists()

    released = await service.handle(
        AUTHENTICATOR.create_request(
            "speech.release",
            {"token": result.payload["token"]},
        )
    )
    assert released.payload == {"released": True}
    assert not (directory / result.payload["file_name"]).exists()


@pytest.mark.asyncio
async def test_speech_ipc_fails_closed_and_maps_provider_errors(tmp_path: Path) -> None:
    calls = 0

    async def unavailable(_: str) -> bytes:
        nonlocal calls
        calls += 1
        raise NvidiaNimError("private upstream detail")

    service = SpeechSynthesisIpcService(
        unavailable,
        SpeechArtifactStore(tmp_path / "speech"),
    )
    invalid = await service.handle(AUTHENTICATOR.create_request("speech.synthesize", {"text": " "}))
    unavailable_result = await service.handle(
        AUTHENTICATOR.create_request("speech.synthesize", {"text": "Hola"})
    )
    unknown = await service.handle(AUTHENTICATOR.create_request("speech.unknown"))

    assert invalid.error_code == "invalid_payload"
    assert unavailable_result.error_code == "speech_provider_unavailable"
    assert "private upstream detail" not in unavailable_result.model_dump_json()
    assert unknown.error_code == "method_not_found"
    assert calls == 1
