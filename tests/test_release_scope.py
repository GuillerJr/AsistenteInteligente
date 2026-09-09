from __future__ import annotations

import json
from pathlib import Path

import pytest

from aegis_core.release_scope import ReleaseScopeError, load_release_scope, release_scope_json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCOPE_PATH = PROJECT_ROOT / "packaging/JarvisV1Scope.json"


def test_checked_in_release_scope_is_frozen_and_complete() -> None:
    manifest = load_release_scope(SCOPE_PATH)

    assert manifest.product_name == "Jarvis"
    assert manifest.technical_namespace == "aegis"
    assert manifest.change_policy.feature_additions_frozen is True
    assert {item.capability_id for item in manifest.capabilities} == {
        "active_vision",
        "engineering_cli",
        "hybrid_brain",
        "local_conversation",
        "memory",
        "native_automation",
        "security_broker",
        "voice_interaction",
    }
    voice = next(
        item for item in manifest.capabilities if item.capability_id == "voice_interaction"
    )
    assert voice.state.value == "deferred_certification"
    assert voice.acceptance_gate == "P10-owner-voice"


def test_scope_report_contains_no_user_content() -> None:
    report = json.loads(release_scope_json(SCOPE_PATH))

    assert report["gate_passed"] is True
    assert report["capabilities"] == 8
    assert report["privacy"] == {
        "contains_credentials": False,
        "contains_prompts": False,
        "contains_transcripts": False,
        "network_calls": 0,
    }


def test_scope_loader_rejects_symbolic_links(tmp_path: Path) -> None:
    link = tmp_path / "scope.json"
    link.symlink_to(SCOPE_PATH)

    with pytest.raises(ReleaseScopeError, match="unsafe"):
        load_release_scope(link)
