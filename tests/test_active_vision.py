from __future__ import annotations

import pytest

from aegis_core.brain.vision_processor import ActiveVisionPayload, VisionProcessor
from aegis_core.tools.silent_executor import SilentAction, SilentExecutor


def payload(*, sequence: int, label: str) -> ActiveVisionPayload:
    return ActiveVisionPayload.model_validate(
        {
            "source": "screen",
            "bundle_identifier": "com.google.Chrome",
            "pixel_width": 1000,
            "pixel_height": 800,
            "frame_sequence": sequence,
            "captured_monotonic_ns": sequence + 1,
            "scene_summary": "Página local",
            "elements": [
                {
                    "kind": "text",
                    "label": label,
                    "confidence": 0.94,
                    "bounds": {"x": 0.1, "y": 0.2, "width": 0.2, "height": 0.1},
                }
            ],
        }
    )


def test_active_vision_is_metadata_only_and_spatially_grounded() -> None:
    processor = VisionProcessor()
    state = processor.process(payload(sequence=1, label="Reproducir"))

    assert state.locate("reproducir") == (200, 200)
    assert len(state.state_sha256) == 64
    assert state.state_sha256 == processor.process(
        payload(sequence=999, label="Reproducir")
    ).state_sha256
    with pytest.raises(ValueError):
        ActiveVisionPayload.model_validate(
            {**payload(sequence=1, label="x").model_dump(), "image_data": "forbidden"}
        )


class Bridge:
    def __init__(self) -> None:
        self.processor = VisionProcessor()
        self.states = [
            self.processor.process(payload(sequence=1, label="Buscar")),
            self.processor.process(payload(sequence=2, label="Resultados")),
        ]
        self.ax_calls = 0
        self.pid_calls = 0

    async def observe(self, process_identifier: int, bundle_identifier: str):
        del process_identifier, bundle_identifier
        return self.states.pop(0)

    async def accessibility_action(self, action: SilentAction) -> bool:
        del action
        self.ax_calls += 1
        return True

    async def process_event(self, action: SilentAction) -> bool:
        del action
        self.pid_calls += 1
        return True


@pytest.mark.asyncio
async def test_silent_executor_uses_accessibility_first_and_verifies_state() -> None:
    bridge = Bridge()
    before = bridge.states[0]
    result = await SilentExecutor(bridge, verification_delay_seconds=0.01).execute(
        SilentAction(
            kind="press",
            process_identifier=42,
            bundle_identifier="com.google.Chrome",
            accessibility_label="Buscar",
            expected_state_sha256=before.state_sha256,
        )
    )

    assert result.success
    assert result.pathway == "accessibility"
    assert bridge.ax_calls == 1
    assert bridge.pid_calls == 0
