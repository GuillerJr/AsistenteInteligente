"""The router may only pick positions of the fresh verified list; ids stay local."""

from __future__ import annotations

import json

import pytest
from test_task_progress import checkpoint

from aegis_core.orchestration.semantic_reference import semantic_reference


class Router:
    def __init__(self, answer: object) -> None:
        self.answer = answer
        self.calls: list[list[dict[str, str]]] = []

    async def __call__(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer if isinstance(self.answer, str) else json.dumps(self.answer)


@pytest.mark.asyncio
async def test_summarize_all_listed_mail_becomes_one_batch_read():
    router = Router({"refers": True, "positions": "all"})
    resolution = await semantic_reference("Detállame y resúmeme los correos", checkpoint(), router)
    assert resolution.tool_suffix == "get_gmail_messages_content_batch"
    assert resolution.identifiers == ("abc123", "def456")
    sent = json.loads(router.calls[0][1]["content"])
    # The router sees positions and titles only, never identifiers.
    assert sent["list"]["items"] == [{"position": 1, "title": "Reunión"},
                                     {"position": 2, "title": "Factura"}]
    assert "abc123" not in json.dumps(router.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("positions,expected", [([2], "def456"), ([2, 2], "def456")])
async def test_one_named_item_becomes_a_single_read(positions, expected):
    resolution = await semantic_reference(
        "léeme el de la factura", checkpoint(), Router({"refers": True, "positions": positions}),
    )
    assert (resolution.tool_suffix, resolution.identifier) == (
        "get_gmail_message_content", expected,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    {"refers": False, "positions": "all"},
    {"refers": True, "positions": [3]},
    {"refers": True, "positions": [True]},
    {"refers": True, "positions": []},
    {"refers": True, "positions": "some"},
    {"refers": True, "positions": "all", "message_ids": ["evil"]},
    {"refers": "true", "positions": "all"},
    "not json",
    "x" * 2_000,
    TimeoutError(),
])
async def test_unusable_router_answers_change_nothing(answer):
    resolution = await semantic_reference("y el contenido?", checkpoint(), Router(answer))
    assert resolution.scope_text is None and resolution.identifiers == ()
    assert resolution.identifier is None


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "busca correos de Ana", 'explica "resume los correos"', "x" * 300,
])
async def test_new_searches_quotes_and_long_text_skip_the_router(text):
    router = Router({"refers": True, "positions": "all"})
    resolution = await semantic_reference(text, checkpoint(), router)
    assert router.calls == [] and resolution.identifiers == ()


@pytest.mark.asyncio
async def test_without_a_fresh_verified_list_the_router_is_not_asked():
    router = Router({"refers": True, "positions": "all"})
    assert (await semantic_reference("resúmeme los correos", None, router)).scope_text is None
    assert router.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["resúmeme esos correos", "cuéntame de todos"])
async def test_router_failure_on_a_list_follow_up_asks_with_the_list(text):
    resolution = await semantic_reference(text, checkpoint(), Router(TimeoutError()))
    assert resolution.scope_text is None
    assert "1. Reunión" in resolution.clarification
    assert "2. Factura" in resolution.clarification and "«todos»" in resolution.clarification


@pytest.mark.asyncio
async def test_router_failure_on_unrelated_text_changes_nothing():
    resolution = await semantic_reference("qué hora es", checkpoint(), Router(TimeoutError()))
    assert resolution.clarification is None and resolution.scope_text is None
