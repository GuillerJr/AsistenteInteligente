from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from aegis_core.brain.behavior_tree import (
    DeviceActionResult,
    TacticalDeviceBehaviorTree,
)
from aegis_core.memory.contracts import MemoryKind
from aegis_core.memory.graph_extractor import DeterministicGraphExtractor
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.tools.android_automation import (
    AndroidDeviceProfile,
    WirelessADBClient,
    parse_ui_hierarchy,
    sanitize_android_text,
)
from aegis_core.tools.ios_bridge import FocusMode, FocusPriorityState
from aegis_core.tools.smart_tv import SmartTVProfile, build_magic_packet


def test_magic_packet_is_canonical_and_profile_rejects_public_host() -> None:
    packet = build_magic_packet("aa:bb:cc:dd:ee:ff")
    assert packet == b"\xff" * 6 + bytes.fromhex("aabbccddeeff") * 16
    with pytest.raises(ValidationError):
        SmartTVProfile.model_validate(
            {
                "device_id": "living-room",
                "platform": "webos",
                "host": "8.8.8.8",
                "certificate_sha256": "a" * 64,
            }
        )


def test_android_text_and_logical_ui_parser_are_bounded() -> None:
    assert sanitize_android_text("Hola Jarvis") == "Hola%sJarvis"
    with pytest.raises(ValueError):
        sanitize_android_text("hola; reboot")
    xml = b"""<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
    <hierarchy rotation="0">
      <node text="Aceptar" resource-id="app:id/accept" class="android.widget.Button"
            content-desc="Confirmar" clickable="true" enabled="true"
            bounds="[20,40][120,100]" />
    </hierarchy>"""
    nodes = parse_ui_hierarchy(xml)
    assert len(nodes) == 1
    assert nodes[0].resource_id == "app:id/accept"
    assert (nodes[0].center_x, nodes[0].center_y) == (70, 70)


def test_adb_client_resolves_a_private_homebrew_style_symlink(tmp_path: Path) -> None:
    executable = tmp_path / "adb-real"
    executable.write_bytes(b"#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    link = tmp_path / "adb"
    link.symlink_to(executable)
    client = WirelessADBClient(
        (AndroidDeviceProfile(device_id="phone-main", host="192.168.1.50"),),
        adb_path=link,
    )

    assert client._validated_executable() == executable


def test_physical_graph_extraction_preserves_location_and_relationships() -> None:
    result = DeterministicGraphExtractor().extract(
        "El foco inteligente está en la oficina de Guillermo."
    )
    entities = {(entity.type, entity.name) for entity in result.entities}
    assert ("device", "foco inteligente") in entities
    assert ("location", "oficina de Guillermo") in entities
    assert any(edge.type == "CONNECTED_TO" for edge in result.relationships)


def test_physical_graph_does_not_cross_assign_properties_between_devices() -> None:
    result = DeterministicGraphExtractor().extract(
        "El televisor es de Guillermo y su IP es 192.168.1.40. "
        "El foco inteligente está en la oficina y su IP es 192.168.1.41."
    )

    physical = [entity for entity in result.entities if entity.type in {"device", "sensor"}]
    assert len(physical) == 2
    assert all(entity.properties == () for entity in physical)


def test_device_properties_are_aead_encrypted_and_blind_indexed(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    database = tmp_path / "memory.sqlite3"
    store = SQLiteMemoryStore(database, encryption_secret=b"d" * 32)
    store.initialize()
    store.put(
        namespace="user.default",
        kind=MemoryKind.SEMANTIC,
        content=(
            "El televisor pertenece a Guillermo y su IP es 192.168.10.55, "
            "MAC: aa:bb:cc:dd:ee:ff."
        ),
    )

    matches = store.find_graph_nodes_by_property(
        namespace="user.default",
        property_name="ip_address",
        value="192.168.10.55",
    )
    assert len(matches) == 1
    assert matches[0].type == "device"
    assert matches[0].properties["mac_address"] == "aa:bb:cc:dd:ee:ff"
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT property_name, value_digest FROM node_property_index"
        ).fetchall()
        assert {item[0] for item in row} == {"ip_address", "mac_address"}
        assert all(len(item[1]) == 64 for item in row)
    physical = database.read_bytes()
    assert b"192.168.10.55" not in physical
    assert b"aa:bb:cc:dd:ee:ff" not in physical


@pytest.mark.asyncio
async def test_device_behavior_tree_wakes_verifies_and_retries_once() -> None:
    actions: list[str] = []
    waits: list[float] = []

    async def direct() -> DeviceActionResult:
        actions.append("direct")
        return DeviceActionResult(False, "offline", 12)

    async def wake() -> DeviceActionResult:
        actions.append("wake")
        return DeviceActionResult(True, "waking", 7)

    async def verify() -> DeviceActionResult:
        actions.append("verify")
        return DeviceActionResult(True, "online", 18)

    async def retry() -> DeviceActionResult:
        actions.append("retry")
        return DeviceActionResult(True, "command_completed", 21)

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    tree = TacticalDeviceBehaviorTree(
        try_command=direct,
        wake_device=wake,
        verify_connection=verify,
        retry_command=retry,
        sleep=sleep,
    )
    context = await tree.run(graph_context="televisor conectado a oficina")

    assert actions == ["direct", "wake", "verify", "retry"]
    assert waits == [1.5]
    assert context.values["device_state"] == "command_completed"
    assert context.values["network_latency_ms"] == 21
    assert context.intervention_reason is None


def test_focus_priority_state_resets_when_filter_is_inactive() -> None:
    state = FocusPriorityState()
    active = state.update(FocusMode.WORK, True)
    assert (active.mode, active.planning_priority) == (FocusMode.WORK, "focused")
    inactive = state.update(FocusMode.SLEEPING, False)
    assert inactive.mode is FocusMode.NORMAL
    assert inactive.active is False
