"""The router may only pick connected tools from the catalogue it was shown."""

from __future__ import annotations

import json

import pytest
from test_nvidia_dialogue import exchange

from aegis_core.orchestration.semantic_tools import select_tools

CATALOGUE = (
    ("mcp_google_workspace__search_gmail_messages", "google-workspace: search_gmail_messages"),
    ("mcp_google_workspace__get_events", "google-workspace: get_events"),
)


class Router:
    def __init__(self, answer: object) -> None:
        self.answer = answer
        self.calls: list[list[dict[str, str]]] = []

    async def __call__(self, messages):
        self.calls.append(messages)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer if isinstance(self.answer, str) else json.dumps(self.answer)


@pytest.mark.asyncio
async def test_router_choice_from_the_catalogue_is_offered():
    router = Router({"intent": "datos", "tools": ["mcp_google_workspace__search_gmail_messages"]})
    chosen = await select_tools("resúmeme mis correos", exchange(), CATALOGUE, router)
    assert chosen == {"mcp_google_workspace__search_gmail_messages"}
    sent = json.loads(router.calls[0][1]["content"])
    assert [entry["name"] for entry in sent["catalogue"]] == [name for name, _ in CATALOGUE]
    assert [turn["role"] for turn in sent["conversation"]] == ["usuario", "jarvis"]


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    {"intent": "datos", "tools": ["mcp_google_workspace__delete_everything"]},
    {"intent": "datos", "tools": ["mcp_google_workspace__search_gmail_messages"] * 5},
    {"intent": "datos", "tools": "mcp_google_workspace__get_events"},
    {"intent": "datos", "tools": [], "extra": True},
    {"intent": "conversacion", "tools": ["mcp_google_workspace__search_gmail_messages"]},
    {"tools": ["mcp_google_workspace__search_gmail_messages"]},
    "not json", "x" * 5_000, TimeoutError(),
])
async def test_unusable_or_out_of_catalogue_answers_select_nothing(answer):
    assert await select_tools("resúmeme mis correos", (), CATALOGUE, Router(answer)) == frozenset()


@pytest.mark.asyncio
async def test_empty_catalogue_or_request_never_asks_the_router():
    router = Router({"tools": []})
    assert await select_tools("hola", (), (), router) == frozenset()
    assert await select_tools("  ", (), CATALOGUE, router) == frozenset()
    assert router.calls == []


def test_host_catalogue_uses_reviewed_purposes_not_upstream_descriptions():
    from aegis_core.mcp.integrations import TOOL_PURPOSES, IntegrationId, integration_tools

    for provider in (IntegrationId.GOOGLE, IntegrationId.GITHUB, IntegrationId.FILESYSTEM):
        for name in integration_tools(provider):
            assert TOOL_PURPOSES.get(name), name
