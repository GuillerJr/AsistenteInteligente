from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from aegis_core.contracts import AgentRole, Capability, RiskLevel
from aegis_core.mcp.host_manager import McpHostManager
from aegis_core.tools.broker import ToolDefinition


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Host:
    def __init__(self, definitions: tuple[ToolDefinition, ...]) -> None:
        self.definitions = definitions

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def tool_definitions(self) -> tuple[ToolDefinition, ...]:
        return self.definitions

    def handlers(self):
        return {}


def definition(name: str, capability: Capability) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=name,
        arguments_model=Arguments,
        capability=capability,
        risk=RiskLevel.HIGH,
        allowed_roles=frozenset({AgentRole.PLANNER}),
    )


async def test_mcp_tools_use_constant_time_active_application_lookup() -> None:
    manager = McpHostManager(
        Host(
            (
                definition("camera_capture", Capability.SCREEN_CAPTURE),
                definition("screen_observe", Capability.SCREEN_CAPTURE),
                definition("mail_recent", Capability.MAIL_READ),
            )
        )
    )
    await manager.start()

    assert manager.tool_names_for_application(None) == frozenset()
    assert manager.tool_names_for_application("com.apple.Mail") == frozenset({"mail_recent"})
    assert manager.tool_names_for_application("com.apple.FaceTime") == frozenset(
        {"camera_capture"}
    )
    assert manager.tool_names_for_application("com.google.Chrome") == frozenset(
        {"screen_observe"}
    )
    assert manager.tool_names_for_application("invalid.example") == frozenset()
