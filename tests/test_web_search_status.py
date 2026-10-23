from __future__ import annotations

import json

import httpx
import pytest

from aegis_core.contracts import PolicyDecision, UserRequest
from aegis_core.orchestration.graph import build_nvidia_swarm_graph
from aegis_core.tools.execution import ReadOnlyToolExecutor
from aegis_core.tools.web import PublicWebClient, WebAccessError

_CHALLENGE = '<form id="challenge-form"><div class="anomaly-modal">Blocked</div></form>'
_NO_RESULTS = '<div class="no-results"><h1>No results found</h1></div>'
_EMPTY_RSS = "<rss><channel><title>Bing search</title></channel></rss>"
_ZERO_RSS = (
    '<rss xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
    "<channel><opensearch:totalResults>0</opensearch:totalResults></channel></rss>"
)
_PRIMARY_RESULT = (
    '<a class="result__a" href="https://source.example/guide">Swift concurrency</a>'
    '<div class="result__snippet">Swift concurrency evidence.</div>'
)
_RSS_RESULT = (
    "<rss><channel><item><title>Swift concurrency</title>"
    "<link>https://source.example/guide</link>"
    "<description>Swift concurrency evidence.</description></item></channel></rss>"
)


def _client(primary: tuple[int, str], fallback: tuple[int, str], *, page: str | None = None):
    visited = []

    def transport(request: httpx.Request) -> httpx.Response:
        visited.append(request.url.host)
        if request.url.host == "html.duckduckgo.com":
            status, body = primary
            return httpx.Response(status, headers={"content-type": "text/html"}, text=body)
        if request.url.host == "www.bing.com":
            status, body = fallback
            return httpx.Response(status, headers={"content-type": "text/xml"}, text=body)
        assert request.url.host == "source.example"
        if page is None:
            return httpx.Response(503)
        return httpx.Response(200, headers={"content-type": "text/plain"}, text=page)

    return PublicWebClient(
        transport=httpx.MockTransport(transport), resolver=lambda _: ("93.184.216.34",),
    ), visited


@pytest.mark.parametrize(
    "primary, reason",
    [
        ((202, _CHALLENGE), "http_202"),
        ((200, _CHALLENGE), "challenge"),
        ((200, _CHALLENGE + _PRIMARY_RESULT), "challenge"),
        ((200, "<html><main>Provider unavailable</main></html>"), "invalid_response"),
        ((200, ""), "empty_response"),
    ],
)
@pytest.mark.parametrize("rss", ["", "   ", _EMPTY_RSS])
def test_challenge_and_empty_rss_are_provider_failure_not_empty_success(primary, reason, rss):
    client, visited = _client(primary, (200, rss))
    try:
        with pytest.raises(WebAccessError, match="providers are unavailable") as failure:
            client.research("Swift concurrency", max_results=1)
    finally:
        client.close()

    assert failure.value.provider_failures == {"duckduckgo": reason, "bing": "empty_response"}
    assert visited == ["html.duckduckgo.com", "www.bing.com"]


@pytest.mark.parametrize(
    "rss",
    [
        "<html>Search unavailable</html>",
        "<rss><channel>",
        "<rss><channel><item><description>Incomplete result</description></item></channel></rss>",
    ],
)
def test_malformed_fallback_is_a_provider_failure(rss):
    client, _ = _client((202, _CHALLENGE), (200, rss))
    try:
        with pytest.raises(WebAccessError) as failure:
            client.research("Swift concurrency", max_results=1)
    finally:
        client.close()
    assert failure.value.provider_failures["bing"] == "invalid_response"


@pytest.mark.parametrize(
    "primary, fallback",
    [
        ((200, _NO_RESULTS), (503, "")),
        ((200, _NO_RESULTS), (200, _EMPTY_RSS)),
        ((202, _CHALLENGE), (200, _ZERO_RSS)),
        ((200, _NO_RESULTS), (200, _ZERO_RSS)),
    ],
)
def test_explicit_no_results_remains_a_valid_empty_search(primary, fallback):
    client, visited = _client(primary, fallback)
    try:
        assert client.research("Swift concurrency", max_results=1) == []
    finally:
        client.close()
    assert visited == ["html.duckduckgo.com", "www.bing.com"]


@pytest.mark.parametrize("primary_status", [200, 202])
def test_challenge_falls_back_to_verified_public_sources(primary_status):
    client, visited = _client(
        (primary_status, _CHALLENGE), (200, _RSS_RESULT), page="Swift concurrency documentation.",
    )
    try:
        results = client.research("Swift concurrency", max_results=1)
    finally:
        client.close()
    assert results[0]["url"] == "https://source.example/guide"
    assert results[0]["content"] == "Swift concurrency documentation."
    assert visited == ["html.duckduckgo.com", "www.bing.com", "source.example"]


def test_ordinary_results_can_mention_captcha_without_becoming_a_challenge():
    client, visited = _client(
        (200, _PRIMARY_RESULT.replace("evidence", "captcha evidence")), (200, _EMPTY_RSS),
        page="Swift concurrency captcha documentation.",
    )
    try:
        assert len(client.research("Swift concurrency", max_results=1)) == 1
    finally:
        client.close()
    assert visited == ["html.duckduckgo.com", "source.example"]


@pytest.mark.parametrize("page", [None, ""])
def test_unreadable_result_pages_do_not_turn_into_empty_success(page):
    client, _ = _client((200, _PRIMARY_RESULT), (200, _EMPTY_RSS), page=page)
    try:
        with pytest.raises(WebAccessError, match="result pages are unavailable") as failure:
            client.research("Swift concurrency", max_results=1)
    finally:
        client.close()
    assert failure.value.provider_failures == {
        "duckduckgo": "result_pages_unavailable", "bing": "empty_response",
    }


def test_web_diagnostics_allow_only_known_providers_and_categorical_reasons():
    error = WebAccessError("Untrusted exception text", provider_failures={
        "duckduckgo": "http_429",
        "bing": "https://private.example/query?text=private",
        "query": "private text",
    })
    assert error.provider_failures == {"duckduckgo": "http_429"}


class _NoInference:
    async def complete_nvidia_only(self, **_kwargs):
        raise AssertionError("A failed exact public search must not request model inference")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "primary, fallback, page, expected_message, expected_failures",
    [
        (
            (202, _CHALLENGE), (200, _EMPTY_RSS), None,
            "No pude consultar los proveedores de búsqueda web. Inténtalo de nuevo más tarde.",
            {"duckduckgo": "http_202", "bing": "empty_response"},
        ),
        (
            (200, _PRIMARY_RESULT), (503, ""), None,
            "Encontré referencias, pero no pude leer sus páginas para verificarlas.",
            {"duckduckgo": "result_pages_unavailable", "bing": "http_503"},
        ),
    ],
)
async def test_real_web_client_failure_reaches_graph_as_error_with_private_diagnostics(
    primary, fallback, page, expected_message, expected_failures,
):
    client, _ = _client(primary, fallback, page=page)
    graph = build_nvidia_swarm_graph(
        _NoInference(), tool_executor=ReadOnlyToolExecutor(web_client_factory=lambda: client),
    )
    state = await graph.ainvoke({"request": UserRequest(text="Busca Swift concurrency")})
    result = state["tool_results"][0]
    assert state["tool_authorizations"][0].decision is PolicyDecision.ALLOW
    assert result.success is False
    assert result.output == ""
    assert result.error_code == "web_access_failed"
    assert result.metadata == {"search_provider_failures": expected_failures}
    assert "Swift concurrency" not in json.dumps(result.metadata)
    assert state["final_result"].content == expected_message
    assert client._client.is_closed


@pytest.mark.asyncio
async def test_real_no_results_reaches_graph_as_empty_success():
    client, _ = _client((200, _NO_RESULTS), (200, _EMPTY_RSS))
    graph = build_nvidia_swarm_graph(
        _NoInference(), tool_executor=ReadOnlyToolExecutor(web_client_factory=lambda: client),
    )
    state = await graph.ainvoke({"request": UserRequest(text="Busca Swift concurrency")})
    result = state["tool_results"][0]
    assert result.success is True
    assert json.loads(result.output)["results"] == []
    assert state["final_result"].content == "No encontré resultados públicos para esa búsqueda."
