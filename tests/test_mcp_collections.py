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
    resolve_task_reference,
)


def test_every_contract_tool_is_a_reviewed_and_admitted_google_tool():
    admitted = set(integration_tools(IntegrationId.GOOGLE))
    for contract in COLLECTIONS.values():
        tools = {*contract.list_tools, *contract.read_tools}
        assert tools <= admitted
        assert tools <= set(GOOGLE_WORKSPACE_REVIEWED_SCHEMAS)
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
def test_every_item_without_a_batch_tool_asks_for_one_item(prompt):
    resolution = resolve_task_reference(prompt, drive_checkpoint())
    assert resolution.clarification and resolution.identifiers == ()


def test_kind_named_in_the_request_must_match_the_listed_kind():
    resolution = resolve_task_reference("Lee todos los correos", drive_checkpoint())
    assert resolution.clarification and resolution.tool_suffix is None
