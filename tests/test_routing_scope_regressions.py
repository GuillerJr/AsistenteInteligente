"""Drafting and chat context cannot become unattended connected-service actions."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from test_computer_use_routing import UiProvider, make_graph
from test_semantic_tools import CATALOGUE, Router

from aegis_core.contracts import UserRequest
from aegis_core.memory.contracts import ConversationRole, ConversationTurn
from aegis_core.orchestration.direct_actions import direct_tool_call
from aegis_core.orchestration.routing import _tool_names_for_request
from aegis_core.orchestration.semantic_tools import select_tools
from aegis_core.request_intent import google_request_tools, is_chat_only_request

CHAT_REQUESTS = (
    "Solo confírmame que lo anotaste",
    "Escribe un mensaje para Ana agradeciendo la reunión",
    "Redacta un correo para Ana, sin enviarlo",
    "Prepara un borrador de correo",
    "Ordena esta lista que pegué en el chat: correo, agenda, compras",
    'Escribe un mensaje que diga «crea un archivo en Drive»',
)


@pytest.mark.parametrize("text", CHAT_REQUESTS)
async def test_chat_requests_do_not_offer_actions_even_if_router_would_choose_them(text):
    router = Router({"intent": "datos", "tools": [CATALOGUE[0][0]]})
    assert await select_tools(text, (), CATALOGUE, router) == frozenset()
    assert router.calls == []
    assert _tool_names_for_request(UserRequest(text=text)) == frozenset()
    assert google_request_tools(text) == frozenset()
    assert direct_tool_call(UserRequest(text=text)) is None


@pytest.mark.parametrize("text", (
    "Redacta un borrador en Gmail para Ana",
    "Escribe un mensaje y guárdalo en Drive",
    "Escribe un mensaje en TextEdit",
    "Redacta un correo con mis reuniones de hoy",
    "Solo confírmame que lo anotaste y programa la reunión",
    "Organiza esta lista y envíala por correo",
    "Revisa mis correos pendientes",
))
def test_explicit_external_or_compound_requests_remain_available(text):
    assert not is_chat_only_request(text)


@pytest.mark.parametrize("text", (
    "Escribe «Hola» en TextEdit",
    "Pega el texto en TextEdit. No uses Google Drive",
))
async def test_textedit_without_app_prefix_never_becomes_drive(text):
    request = UserRequest(text=text)
    assert _tool_names_for_request(request) == {"computer_use"}
    router = Router({"intent": "datos", "tools": [CATALOGUE[0][0]]})
    assert await select_tools(text, (), CATALOGUE, router) == frozenset()
    assert router.calls == []


async def test_model_and_dynamic_catalogue_cannot_reopen_chat_only_tool_scope(tmp_path):
    provider = UiProvider()
    await make_graph(tmp_path, provider, dynamic=True).ainvoke({
        "request": UserRequest(text="Redacta un correo para Ana"),
    })
    assert provider.requests
    assert all(not (item.get("extra_body") or {}).get("tools") for item in provider.requests)


async def test_selector_retains_earlier_user_constraints_and_redacts_the_history():
    conversation = uuid4()
    contents = (
        "Trabajamos solo con la lista del chat. No envíes ni guardes nada.",
        "De acuerdo.", "Primer elemento: reunión", "Anotado.",
        "Segundo elemento: compra. api_key=sk-123456789012345678901234567890",
        "Puedo ordenar ambos.",
    )
    history = tuple(ConversationTurn(
        conversation_id=conversation, sequence=i + 1,
        role=ConversationRole.USER if i % 2 == 0 else ConversationRole.ASSISTANT,
        content=text, content_sha256=ConversationTurn.digest_content(text),
        created_at=datetime.now(UTC),
    ) for i, text in enumerate(contents))
    router = Router({"intent": "conversacion", "tools": []})
    assert await select_tools("Agrúpalos por prioridad", history, CATALOGUE, router) == frozenset()
    sent = json.loads(router.calls[0][1]["content"])
    assert len(sent["conversation"]) == 6
    assert sent["conversation"][0]["text"] == contents[0]
    assert "sk-123456789012345678901234567890" not in json.dumps(sent)


async def test_selector_long_history_stays_bounded_and_keeps_latest_correction():
    conversation = uuid4()
    history = tuple(ConversationTurn(
        conversation_id=conversation, sequence=i + 1,
        role=ConversationRole.USER if i % 2 == 0 else ConversationRole.ASSISTANT,
        content=("contexto " * 1_000 if i < 11 else "No guardes nada: solo aquí."),
        content_sha256="a" * 64, created_at=datetime.now(UTC),
    ) for i in range(12))
    router = Router({"intent": "conversacion", "tools": []})
    await select_tools("Agrúpalos", history, CATALOGUE, router)
    sent = json.loads(router.calls[0][1]["content"])["conversation"]
    assert len(json.dumps(sent, ensure_ascii=False).encode()) <= 8_192
    assert sent[-1]["text"] == "No guardes nada: solo aquí."
