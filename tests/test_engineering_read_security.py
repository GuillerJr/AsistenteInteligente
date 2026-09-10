from pathlib import Path

import pytest

from aegis_core.contracts import AgentResult, AgentRole, ToolCall, UserRequest
from aegis_core.engineering import _repository_manifest
from aegis_core.orchestration.graph import build_swarm_graph
from aegis_core.tools.defaults import default_policy_context


class ReadPlanner:
    def __init__(self, path: str):
        self.path = path
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        assert self.calls == 1, "Denied or secret-bearing data must not go to synthesis"
        return AgentResult(
            role=AgentRole.CODE_SECURITY, model_id="local/test", content="",
            tool_calls=(ToolCall(
                call_id="read-1", tool_name="filesystem_read_text",
                arguments={"path": self.path, "max_bytes": 4096},
                requested_by=AgentRole.CODE_SECURITY,
            ),),
        )


class NoRemote:
    async def complete(self, **kwargs):
        pytest.fail("Private engineering data must not leave the device")


@pytest.mark.asyncio
async def test_read_cannot_escape_selected_subproject(tmp_path: Path) -> None:
    (tmp_path / "chosen").mkdir()
    (tmp_path / "sibling.txt").write_text("private")
    graph = build_swarm_graph(
        NoRemote(), local_provider=ReadPlanner("sibling.txt"),
        policy_context=default_policy_context(tmp_path),
    )
    state = await graph.ainvoke({"request": UserRequest(
        text="Revisa el archivo", metadata={
            "interaction_surface": "engineering_cli", "engineering_workspace": "chosen",
            "engineering_inference_policy": "local_only",
        },
    )})
    assert state["tool_authorizations"][0].reason_code == "engineering_workspace_mismatch"
    assert not state["tool_results"]
    assert state["final_result"].model_id == "local/engineering-read-denied"


@pytest.mark.asyncio
async def test_file_credentials_never_reach_synthesis(tmp_path: Path) -> None:
    (tmp_path / "settings.txt").write_text("NVIDIA_API_KEY=nvapi-" + "x" * 64)
    graph = build_swarm_graph(
        NoRemote(), local_provider=ReadPlanner("settings.txt"),
        policy_context=default_policy_context(tmp_path),
    )
    state = await graph.ainvoke({"request": UserRequest(
        text="Revisa la configuración", metadata={
            "interaction_surface": "engineering_cli", "engineering_workspace": ".",
            "engineering_inference_policy": "local_only",
        },
    )})
    assert state["final_result"].model_id == "local/engineering-secret-shield"
    assert "nvapi" not in state["final_result"].content


def test_manifest_bounds_directories_not_just_file_count(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("aegis_core.engineering._MAX_MANIFEST_SCAN_ENTRIES", 8)
    for index in range(12):
        (tmp_path / f"dir{index}").mkdir()
    result = _repository_manifest(tmp_path, prefix=None)
    assert result["observed_file_count"] == 0
    assert result["sample_complete"] is False  # Unscanned does not mean empty.
