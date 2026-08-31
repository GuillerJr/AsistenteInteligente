from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from aegis_core.biometric_training_service import BiometricTrainingService
from aegis_core.brain.behavior_tree import (
    BehaviorStatus,
    TacticalUIBehaviorTree,
)
from aegis_core.brain.vision_fallback import LocalizedVisionAnalyzer, LocalizedVisionError
from aegis_core.memory.contracts import MemoryKind
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.providers.mlx_distributed import (
    DistributedMLXProvider,
    ThunderboltPeer,
    ThunderboltPeerDiscovery,
)


@pytest.mark.asyncio
async def test_tactical_tree_uses_historical_modal_hint_and_recovers() -> None:
    calls: list[str] = []
    primary_attempts = 0

    async def primary() -> bool:
        nonlocal primary_attempts
        primary_attempts += 1
        calls.append("primary")
        return primary_attempts > 1

    async def action(name: str) -> bool:
        calls.append(name)
        return True

    tree = TacticalUIBehaviorTree(
        try_action=primary,
        detect_modal=lambda: False,
        dismiss_modal=lambda: action("dismiss"),
        reevaluate=lambda: action("reevaluate"),
        reload_action=lambda: action("reload"),
        retry_parent=primary,
    )
    context = await tree.run(graph_context="Historical solution: dismiss cookie modal")

    assert context.trace[-1].status is BehaviorStatus.SUCCESS
    assert calls == ["primary", "dismiss", "reevaluate", "reload", "primary"]


@pytest.mark.asyncio
async def test_tactical_tree_stops_repeated_action_cycle() -> None:
    interventions: list[str] = []

    async def fail() -> bool:
        return False

    tree = TacticalUIBehaviorTree(
        try_action=fail,
        detect_modal=lambda: True,
        dismiss_modal=lambda: _true(),
        reevaluate=lambda: _true(),
        reload_action=lambda: _true(),
        retry_parent=fail,
        on_user_intervention=lambda reason: _capture(interventions, reason),
    )

    context = await tree.run()

    assert context.intervention_reason == "behavior_action_repeated_three_times"
    assert interventions == ["behavior_action_repeated_three_times"]


@pytest.mark.asyncio
async def test_localized_vision_rejects_non_120_png_before_launch(tmp_path: Path) -> None:
    analyzer = LocalizedVisionAnalyzer(
        tmp_path / "helper",
        model_directory=tmp_path / "model",
    )
    with pytest.raises(LocalizedVisionError, match="120x120"):
        await analyzer.analyze(
            image_png=b"not-a-png",
            target_description="button",
            expected_x=60,
            expected_y=60,
        )


def test_spotlight_records_are_decrypted_only_on_explicit_export(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3", encryption_secret=b"m" * 32)
    store.initialize()
    notifications: list[str] = []
    store.set_graph_change_listener(notifications.append)
    store.put(
        namespace="user.default",
        kind=MemoryKind.SEMANTIC,
        content="Guillermo trabaja en el proyecto Jarvis.",
    )

    records = store.spotlight_graph_records(namespace="user.default", limit=100)

    assert records
    assert notifications == ["user.default"]
    assert any("Jarvis" in record.name or "Guillermo" in record.name for record in records)


def test_authenticated_thunderbolt_discovery_rejects_replay() -> None:
    secret = b"d" * 32
    discovery = ThunderboltPeerDiscovery(secret=secret)
    discovery._interfaces = (("en7", "10.42.0.1", 24),)
    node_id = uuid4()
    body = {
        "version": "1.0",
        "node_id": str(node_id),
        "host": "peer-mac",
        "address": "10.42.0.2",
        "timestamp": int(time.time()),
        "nonce": "a" * 32,
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    packet = json.dumps(
        {**body, "hmac": hmac.new(secret, canonical, hashlib.sha256).hexdigest()},
        separators=(",", ":"),
    ).encode()

    discovery.receive(packet, "10.42.0.2")
    discovery.receive(packet, "10.42.0.2")

    assert len(discovery.peers) == 1
    assert discovery.peers[0].node_id == node_id


def test_jaccl_hostfile_requires_complete_authenticated_mesh(tmp_path: Path) -> None:
    discovery = ThunderboltPeerDiscovery(secret=b"d" * 32)
    discovery._host = "local-mac"
    remote = ThunderboltPeer(
        node_id=uuid4(),
        host="remote-mac",
        address="192.168.42.2",
        last_seen=time.monotonic(),
    )
    hostfile = tmp_path / "jaccl.json"
    hostfile.write_text(
        json.dumps(
            [
                {
                    "ssh": "local-mac",
                    "ips": ["10.0.0.1"],
                    "rdma": [None, "rdma_en2"],
                },
                {
                    "ssh": "remote-mac",
                    "ips": [],
                    "rdma": ["rdma_en2", None],
                },
            ]
        )
    )
    hostfile.chmod(0o600)
    provider = DistributedMLXProvider(
        discovery=discovery,
        hostfile=hostfile,
        model_id="mlx-community/Qwen2.5-32B-Instruct-4bit",
        local_fallback=_fallback,
    )

    validated, required = provider._validated_hostfile((remote,))

    assert validated == hostfile
    assert required == frozenset({"192.168.42.2"})


def test_biometric_envelope_authenticates_owner_and_caf() -> None:
    key = b"k" * 32
    owner = b"guillermo"
    caf = b"caff" + b"\0" * 8 + b"lpcm" + b"\0" * 64
    header = b"AEGBIO1\0" + uuid4().bytes + bytes([len(owner)]) + owner
    header += hashlib.sha256(caf).digest()
    nonce = b"n" * 12
    envelope = header + nonce + AESGCM(key).encrypt(nonce, caf, header)
    sample = Path("sample.caf.enc")

    class MemoryPath:
        def read_bytes(self) -> bytes:
            return envelope

    decoded_owner, decoded = BiometricTrainingService._decrypt_sample(
        MemoryPath(),  # type: ignore[arg-type]
        key,
    )
    assert sample.name == "sample.caf.enc"
    assert decoded_owner == "guillermo"
    assert decoded == caf


async def _true() -> bool:
    return True


async def _capture(target: list[str], value: str) -> None:
    target.append(value)


async def _fallback(prompt: str, maximum_tokens: int) -> str:
    del prompt, maximum_tokens
    return "local"
