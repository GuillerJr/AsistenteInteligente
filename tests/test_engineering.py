from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from aegis_core.contracts import AgentRole, InputModality, UserRequest
from aegis_core.engineering import (
    ENGINEERING_DOMAIN_METADATA,
    ENGINEERING_INFERENCE_METADATA,
    ENGINEERING_MANIFEST_METADATA,
    ENGINEERING_RESEARCH_METADATA,
    ENGINEERING_SURFACE_METADATA,
    ENGINEERING_WORKSPACE_METADATA,
    EngineeringIpcService,
    _repository_manifest,
    engineering_system_instruction,
)
from aegis_core.engineering_cli import EngineeringCLI
from aegis_core.jobs import JobSnapshot, JobStatus
from aegis_core.orchestration.graph import _route_request, _tool_names_for_request


class FakeJobs:
    def __init__(self) -> None:
        self.request: UserRequest | None = None
        self.conversation_id = uuid4()

    async def submit(
        self,
        request: UserRequest,
        *,
        conversation_id=None,
        persist_conversation: bool = False,
    ) -> JobSnapshot:
        self.request = request
        assert persist_conversation is True
        return JobSnapshot(
            job_id=uuid4(),
            request_id=request.request_id,
            conversation_id=conversation_id or self.conversation_id,
            status=JobStatus.QUEUED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )


class FakePreflightClient:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def call(self, method: str, payload: dict[str, object]):
        self.calls.append((method, payload))
        return SimpleNamespace(
            ok=True,
            payload={"status": "ok", "workspace_path": str(self.workspace)},
            error_code=None,
        )


class RecoverableFailureCLI(EngineeringCLI):
    def __init__(self, client, *, workspace: Path, stdin: StringIO, stdout: StringIO) -> None:
        super().__init__(client, workspace=workspace, stdin=stdin, stdout=stdout)
        self.requests: list[str] = []

    async def _submit_and_render(self, text: str) -> int:
        self.requests.append(text)
        self._render_error("swarm_execution_failed")
        return 1


@pytest.mark.asyncio
async def test_engineering_service_confines_and_marks_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "company-project"
    workspace.mkdir()
    (workspace / "service.py").write_text("pass\n", encoding="utf-8")
    jobs = FakeJobs()
    service = EngineeringIpcService(jobs, tmp_path)
    request = SimpleNamespace(
        method="engineering.submit",
        payload={
            "text": "Revisa la arquitectura del backend",
            "workspace_path": str(workspace),
            "domain": "backend",
            "research_policy": "offline",
            "inference_policy": "local_only",
        },
    )

    response = await service.handle(request)

    assert response.ok is True
    assert jobs.request is not None
    assert jobs.request.modalities == frozenset({InputModality.TEXT})
    assert jobs.request.metadata == {
        "interaction_surface": ENGINEERING_SURFACE_METADATA,
        ENGINEERING_DOMAIN_METADATA: "backend",
        ENGINEERING_WORKSPACE_METADATA: "company-project",
        ENGINEERING_RESEARCH_METADATA: "offline",
        ENGINEERING_INFERENCE_METADATA: "local_only",
        ENGINEERING_MANIFEST_METADATA: {
            "schema_version": "1.0",
            "observed_file_count": 1,
            "sample_complete": True,
            "top_level_counts": {".": 1},
            "sampled_paths": ["company-project/service.py"],
        },
    }


@pytest.mark.asyncio
async def test_engineering_service_rejects_workspace_escape(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    service = EngineeringIpcService(FakeJobs(), root)

    response = await service.handle(
        SimpleNamespace(
            method="engineering.preflight",
            payload={"workspace_path": str(outside)},
        )
    )

    assert response.ok is False
    assert response.error_code == "invalid_engineering_request"


@pytest.mark.asyncio
async def test_engineering_preflight_defaults_to_daemon_workspace(tmp_path: Path) -> None:
    service = EngineeringIpcService(FakeJobs(), tmp_path)

    response = await service.handle(SimpleNamespace(method="engineering.preflight", payload={}))

    assert response.ok is True
    assert response.payload["workspace"] == "."
    assert response.payload["workspace_path"] == str(tmp_path.resolve())


@pytest.mark.asyncio
async def test_engineering_cli_adopts_daemon_workspace_when_omitted(
    tmp_path: Path,
) -> None:
    client = FakePreflightClient(tmp_path)
    session = EngineeringCLI(client, workspace=None)

    await session.preflight()

    assert client.calls == [(EngineeringIpcService.PREFLIGHT_METHOD, {})]
    assert session._workspace == tmp_path.resolve()


def test_engineering_cli_renders_actionable_bounded_provider_failure(tmp_path: Path) -> None:
    output = StringIO()
    session = EngineeringCLI(
        FakePreflightClient(tmp_path),
        workspace=tmp_path,
        stdin=StringIO(),
        stdout=output,
    )

    session._render_error("brain_unavailable")

    rendered = output.getvalue()
    assert "cerebro local" in rendered.lower()
    assert "Apple Intelligence" in rendered
    assert "private provider exception" not in rendered


def test_engineering_route_and_prompt_are_cli_specific() -> None:
    request = UserRequest(
        text="Diseña la nueva API",
        metadata={
            "interaction_surface": ENGINEERING_SURFACE_METADATA,
            ENGINEERING_DOMAIN_METADATA: "architecture",
            ENGINEERING_WORKSPACE_METADATA: ".",
            ENGINEERING_RESEARCH_METADATA: "offline",
            ENGINEERING_INFERENCE_METADATA: "hybrid",
        },
    )

    route = _route_request(request)
    instruction = engineering_system_instruction(request)

    assert route.role is AgentRole.CODE_SECURITY
    assert "professional Markdown" in instruction
    assert "never claim" in instruction.lower()
    assert "sample_complete" in instruction
    assert "session is offline" in instruction
    assert "never wrap the entire response in a code fence" in instruction
    assert "begin by stating that the inventory is partial" in instruction
    assert _tool_names_for_request(request) == frozenset({"filesystem_read_text"})


def test_repository_inventory_is_fair_across_large_top_level_components(
    tmp_path: Path,
) -> None:
    for component in ("docs", "native", "src", "tests"):
        directory = tmp_path / component
        directory.mkdir()
        for index in range(30):
            (directory / f"{index:02d}.txt").write_text(component, encoding="utf-8")
    ignored = tmp_path / ".vscode"
    ignored.mkdir()
    (ignored / "settings.json").write_text("{}", encoding="utf-8")

    inventory = _repository_manifest(tmp_path, prefix=None)

    assert inventory["observed_file_count"] == 120
    assert inventory["sample_complete"] is False
    assert inventory["top_level_counts"] == {
        "docs": 30,
        "native": 30,
        "src": 30,
        "tests": 30,
    }
    sampled = inventory["sampled_paths"]
    assert isinstance(sampled, list)
    assert len(sampled) == 64
    assert {path.split("/", 1)[0] for path in sampled} == {
        "docs",
        "native",
        "src",
        "tests",
    }


def test_repository_inventory_marks_small_sample_complete(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("Jarvis", encoding="utf-8")
    source = tmp_path / "src"
    source.mkdir()
    (source / "main.py").write_text("pass\n", encoding="utf-8")

    inventory = _repository_manifest(tmp_path, prefix="project")

    assert inventory == {
        "schema_version": "1.0",
        "observed_file_count": 2,
        "sample_complete": True,
        "top_level_counts": {".": 1, "src": 1},
        "sampled_paths": ["project/README.md", "project/src/main.py"],
    }


def test_connected_engineering_research_can_offer_public_web() -> None:
    request = UserRequest(
        text="Investiga la versión actual de Swift y revisa el código",
        metadata={
            "interaction_surface": ENGINEERING_SURFACE_METADATA,
            ENGINEERING_DOMAIN_METADATA: "research",
            ENGINEERING_WORKSPACE_METADATA: ".",
            ENGINEERING_RESEARCH_METADATA: "public_web",
            ENGINEERING_INFERENCE_METADATA: "hybrid",
        },
    )

    assert _tool_names_for_request(request) == frozenset({"filesystem_read_text", "web_research"})


def test_engineering_cli_commands_update_only_local_session(tmp_path: Path) -> None:
    output = StringIO()
    session = EngineeringCLI(
        SimpleNamespace(),
        workspace=tmp_path,
        stdout=output,
    )

    assert session._handle_local_command("/domain frontend") == 0
    assert session._handle_local_command("/research public_web") == 0
    assert session._handle_local_command("/inference local_only") == 0
    assert session._handle_local_command("/status") == 0

    rendered = output.getvalue()
    assert "frontend" in rendered
    assert "public_web" in rendered
    assert "local_only" in rendered


@pytest.mark.asyncio
async def test_engineering_cli_plain_terminal_ui_and_clean_eof(tmp_path: Path) -> None:
    output = StringIO()
    session = EngineeringCLI(
        FakePreflightClient(tmp_path),
        workspace=None,
        stdin=StringIO(""),
        stdout=output,
    )

    assert await session.run_interactive() == 0

    rendered = output.getvalue()
    assert "J A R V I S" in rendered
    assert "ENGINEERING CLI" in rendered
    assert "AsistenteInteligente" not in rendered
    assert "Sesión cerrada.\n" in rendered
    assert rendered.endswith("\n")
    assert "\033[" not in rendered


@pytest.mark.asyncio
async def test_interactive_cli_survives_recoverable_job_failure(tmp_path: Path) -> None:
    output = StringIO()
    session = RecoverableFailureCLI(
        FakePreflightClient(tmp_path),
        workspace=tmp_path,
        stdin=StringIO("primera solicitud\n/exit\n"),
        stdout=output,
    )

    assert await session.run_interactive() == 0
    assert session.requests == ["primera solicitud"]
    assert "La sesión sigue activa" in output.getvalue()
    assert output.getvalue().count("◆ auto →") == 2
