"""Source-boundary guards complement the executable Swift SpeechOutput tests."""

import re
from pathlib import Path

SOURCES = Path(__file__).resolve().parents[1] / "native/AegisAudio/Sources"


def _model_method(name: str) -> str:
    methods = []
    for path in (SOURCES / "AegisMenuBar/Models").glob("MenuBarModel*.swift"):
        source = path.read_text(encoding="utf-8")
        match = re.search(rf"^    (?:private )?func {name}\(", source, re.MULTILINE)
        if match:
            methods.append(source[match.end():].split("\n    }", 1)[0])
    assert len(methods) == 1, f"Expected one implementation of {name}"
    return methods[0]


def test_application_has_no_second_speech_synthesizer() -> None:
    for module in ("AegisAudioCore", "AegisMenuBar"):
        for path in (SOURCES / module).rglob("*.swift"):
            source = path.read_text(encoding="utf-8")
            assert "AVSpeechSynthesizer" not in source, path
            assert "AVSpeechUtterance" not in source, path
            assert '"/usr/bin/say"' not in source, path


def test_failure_feedback_is_visual_without_network_or_cached_speech() -> None:
    body = _model_method("announceVoiceFailure")
    assert "speechOutput.stop()" in body
    assert "voiceOutputText =" in body
    assert "voiceState = .failed" in body
    assert "scheduleWakeWordResume" in body
    assert "speakWithWakeWordIsolation(" not in body
    assert "replayLocally(" not in body
    assert "speechOutput.speak(" not in body


def test_browser_prompt_uses_neural_voice_without_transmitting_browser_inventory() -> None:
    body = _model_method("presentBrowserSelection")
    assert "speakWithWakeWordIsolation(" in body
    assert '"¿Qué navegador quieres usar? Di su nombre o elígelo en pantalla."' in body
    assert "captureSpokenBrowserChoice()" in body
    assert "localOnly:" not in body
    assert "replayOnly:" not in body
    assert "\\(" not in body  # No dynamic inventory or request text in the TTS prompt.
