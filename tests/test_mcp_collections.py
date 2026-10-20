"""Collection contracts tie list results to their read tools for every integration."""

from __future__ import annotations

import pytest
from test_task_progress import result

from aegis_core.mcp.collections import (
    COLLECTIONS,
    contract_for_list_tool,
    contract_for_read_tool,
    contract_for_word,
)
from aegis_core.mcp.google_workspace import GOOGLE_WORKSPACE_REVIEWED_SCHEMAS
from aegis_core.mcp.integrations import IntegrationId, integration_tools
from aegis_core.orchestration.task_progress import (
    advance_checkpoint,
    requested_task_steps,
    resolve_task_reference,
)
from aegis_core.request_intent import google_request_tools


def test_every_contract_tool_is_a_reviewed_and_admitted_google_tool():
    admitted = set(integration_tools(IntegrationId.GOOGLE))
    for contract in COLLECTIONS.values():
        # A derived batch read is built by the host from the reviewed detail tool.
        upstream = {*contract.list_tools, *contract.read_tools} - (
            {contract.batch_tool} if contract.batch_is_local else set()
        )
        assert upstream <= admitted
        assert upstream <= set(GOOGLE_WORKSPACE_REVIEWED_SCHEMAS)
        for tool in contract.list_tools:
            assert contract_for_list_tool(tool) is contract
        for tool in contract.read_tools:
            assert contract_for_read_tool(tool) is contract
        for word in contract.synonyms:
            assert contract_for_word(word) is contract


def drive_checkpoint():
    files = result(name="mcp_google_workspace__search_drive_files", payload={"files": [
        {"id": "file-a", "name": "Presupuesto"}, {"id": "file-b", "name": "Contrato"},
    ]}, call_id="drive-1")
    return advance_checkpoint(None, request="Busca archivos en Drive", response="Hay dos.",
                              sequence=2, tool_results=(files,))


def test_single_item_reads_use_the_contract_of_the_listed_kind():
    resolution = resolve_task_reference("Abre el segundo archivo", drive_checkpoint())
    assert (resolution.tool_suffix, resolution.identifier) == ("get_drive_file_content", "file-b")


@pytest.mark.parametrize("prompt", ["Lee todos los archivos", "Muéstrame esos documentos"])
def test_every_drive_file_uses_the_host_derived_batch_read(prompt):
    resolution = resolve_task_reference(prompt, drive_checkpoint())
    assert resolution.tool_suffix == "get_drive_file_content_many"
    assert resolution.identifiers == ("file-a", "file-b")
    assert "get_drive_file_content_many" in google_request_tools(resolution.scope_text)
    assert requested_task_steps(resolution.scope_text)[0].tool_suffixes == (
        "get_drive_file_content_many",
    )


def test_more_items_than_a_batch_allows_asks_for_fewer():
    files = result(name="mcp_google_workspace__search_drive_files", payload={"files": [
        {"id": f"file-{i}", "name": f"F{i}"} for i in range(7)
    ]}, call_id="drive-7")
    many = advance_checkpoint(None, request="Busca archivos en Drive", response="Hay siete.",
                              sequence=2, tool_results=(files,))
    resolution = resolve_task_reference("Lee todos los archivos", many)
    assert resolution.clarification and resolution.identifiers == ()


def test_kind_named_in_the_request_must_match_the_listed_kind():
    resolution = resolve_task_reference("Lee todos los correos", drive_checkpoint())
    assert resolution.clarification and resolution.tool_suffix is None


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if arguments["file_id"] == "missing":
            return {"content": [{"type": "text", "text": "not found"}], "isError": True}
        return {"content": [{"type": "text", "text": "body of " + arguments["file_id"]}],
                "isError": False}


def drive_detail_binding():
    from aegis_core.contracts import AgentRole, Capability, RiskLevel
    from aegis_core.mcp.client import McpToolBinding
    from aegis_core.mcp.integrations import IntegrationScope

    scope = IntegrationScope(provider="google-workspace", google_accounts={"o@example.com"})
    return McpToolBinding(
        server_id="google-workspace", remote_name="get_drive_file_content",
        exposed_name="mcp_google_workspace__get_drive_file_content",
        input_schema={"type": "object", "properties": {
            "file_id": {"type": "string", "maxLength": 64},
            "user_google_email": {"type": "string", "enum": ["o@example.com"]},
        }, "required": ["file_id", "user_google_email"], "additionalProperties": False},
        capability=Capability.WEB_READ, risk=RiskLevel.HIGH,
        allowed_roles=frozenset({AgentRole.PLANNER}), integration=scope,
    )


def test_host_derives_a_bounded_batch_from_the_reviewed_detail_schema():
    from aegis_core.mcp.client import _derived_batch_bindings

    detail = drive_detail_binding()
    (derived,) = _derived_batch_bindings([detail], {detail.exposed_name})
    assert derived.exposed_name == "mcp_google_workspace__get_drive_file_content_many"
    ids = derived.input_schema["properties"]["file_ids"]
    assert ids["items"] == {"type": "string", "maxLength": 64}
    assert (ids["maxItems"], ids["uniqueItems"]) == (5, True)
    assert derived.input_schema["required"] == ["file_ids", "user_google_email"]
    assert derived.fan_out == ("get_drive_file_content", "file_ids", "file_id", 5)
    assert derived.capability is detail.capability


@pytest.mark.asyncio
async def test_derived_batch_calls_the_detail_tool_once_per_id_with_its_guard(tmp_path):
    from aegis_core.contracts import PolicyDecision, ToolAuthorization
    from aegis_core.mcp.client import McpHost, McpHostConfiguration, _derived_batch_bindings
    from aegis_core.tools.broker import PolicyViolation
    from aegis_core.tools.defaults import default_policy_context

    detail = drive_detail_binding()
    (derived,) = _derived_batch_bindings([detail], {detail.exposed_name})
    host = McpHost(McpHostConfiguration())
    host._bindings = {detail.exposed_name: detail, derived.exposed_name: derived}
    client = FakeClient()
    authorization = ToolAuthorization.model_construct(
        call_id="c", tool_name=derived.exposed_name, decision=PolicyDecision.ALLOW,
        normalized_arguments={"file_ids": ["file-a", "missing"],
                              "user_google_email": "o@example.com"},
    )
    context = default_policy_context(tmp_path)
    result = await host._fan_out(derived, client, authorization, context)
    assert [arguments["file_id"] for _, arguments in client.calls] == ["file-a", "missing"]
    assert all(name == "get_drive_file_content" for name, _ in client.calls)
    text = result["content"][0]["text"]
    assert "[1/2] file_id: file-a\nbody of file-a" in text and "not found" in text
    assert result["isError"] is False

    outside = authorization.model_copy(update={"normalized_arguments": {
        "file_ids": ["file-a"], "user_google_email": "intruder@example.com"}})
    intruder_client = FakeClient()
    with pytest.raises(PolicyViolation):
        await host._fan_out(derived, intruder_client, outside, context)
    assert intruder_client.calls == []
