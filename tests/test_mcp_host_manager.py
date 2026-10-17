from __future__ import annotations

from datetime import UTC, datetime

from test_mcp_host import server_configuration

from aegis_core.ipc.protocol import IpcRequest
from aegis_core.mcp.client import McpHost, McpHostConfiguration
from aegis_core.mcp.host_manager import McpHostManager


async def test_application_scope_is_explicit_not_inferred_from_tool_names() -> None:
    manager = McpHostManager(McpHost(McpHostConfiguration(servers=(server_configuration(),))))
    await manager.start()
    try:
        assert manager.tool_names_for_application("com.apple.Terminal") == frozenset(
            {"mcp_test_server__read_status"}
        )
        assert manager.tool_names_for_application("COM.APPLE.TERMINAL") == frozenset(
            {"mcp_test_server__read_status"}
        )
        for bundle in (None, "com.apple.Mail", "com.apple.Safari", "invalid.example"):
            assert manager.tool_names_for_application(bundle) == frozenset()
    finally:
        await manager.close()
    assert manager.tool_names_for_application("com.apple.Terminal") == frozenset()


async def test_status_is_metadata_only_and_rejects_arguments() -> None:
    manager = McpHostManager(McpHost(McpHostConfiguration()))
    await manager.start()
    request = IpcRequest(
        method="mcp.status", timestamp=datetime.now(UTC), nonce="0" * 32, auth_tag="0" * 64
    )
    response = await manager.ipc_handlers()["mcp.status"](request)
    assert response.ok
    assert response.payload["servers"] == []
    assert response.payload["external_certification"] is False
    invalid = await manager.handle_status(request.model_copy(update={"payload": {"run": True}}))
    assert invalid.error_code == "invalid_payload"
    wrong = await manager.handle_status(request.model_copy(update={"method": "mcp.enable"}))
    assert wrong.error_code == "method_not_found"
    await manager.close()
