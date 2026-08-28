from __future__ import annotations

import httpx
import pytest

from aegis_core.tools.web import PublicWebClient, WebAccessError


def test_web_client_blocks_private_addresses_before_transport() -> None:
    def forbidden_transport(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"transport must not receive {request.url}")

    client = PublicWebClient(
        transport=httpx.MockTransport(forbidden_transport),
        resolver=lambda _: ("127.0.0.1",),
    )
    try:
        with pytest.raises(WebAccessError, match="outside public Internet"):
            client.fetch("https://localhost/private")
    finally:
        client.close()


def test_web_client_researches_only_bounded_public_https_text() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.host == "html.duckduckgo.com":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=(
                    '<a class="result__a" href="https://example.com/report">Report</a>'
                    '<div class="result__snippet">A current public result.</div>'
                ),
            )
        assert str(request.url) == "https://example.com/report"
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=(
                "<html><head><title>Public report</title></head>"
                "<body><script>ignore()</script><main><p>Verified content.</p></main></body></html>"
            ),
        )

    client = PublicWebClient(
        transport=httpx.MockTransport(transport),
        resolver=lambda _: ("93.184.216.34",),
    )
    try:
        results = client.research("public report", max_results=1)
    finally:
        client.close()

    assert results == [
        {
            "content": "Verified content.",
            "snippet": "A current public result.",
            "title": "Public report",
            "url": "https://example.com/report",
        }
    ]


def test_web_client_recovers_from_search_challenge_with_bounded_rss() -> None:
    observed_hosts: list[str] = []

    def transport(request: httpx.Request) -> httpx.Response:
        observed_hosts.append(request.url.host)
        if request.url.host == "html.duckduckgo.com":
            return httpx.Response(202, headers={"content-type": "text/html"}, text="challenge")
        if request.url.host == "www.bing.com":
            return httpx.Response(
                200,
                headers={"content-type": "text/xml; charset=utf-8"},
                text=(
                    "<rss><channel><item><title>Fallback report</title>"
                    "<link>https://example.com/report</link>"
                    "<description>A &lt;b&gt;current&lt;/b&gt; result.</description>"
                    "</item></channel></rss>"
                ),
            )
        assert str(request.url) == "https://example.com/report"
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><main><p>Recovered content.</p></main></html>",
        )

    client = PublicWebClient(
        transport=httpx.MockTransport(transport),
        resolver=lambda _: ("93.184.216.34",),
    )
    try:
        results = client.research("fallback report", max_results=1)
    finally:
        client.close()

    assert observed_hosts == ["html.duckduckgo.com", "www.bing.com", "example.com"]
    assert results == [
        {
            "content": "Recovered content.",
            "snippet": "A current result.",
            "title": "Fallback report",
            "url": "https://example.com/report",
        }
    ]


@pytest.mark.parametrize("primary_status", (200, 503))
def test_web_client_fails_closed_when_search_recovery_is_unavailable(
    primary_status: int,
) -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.host == "html.duckduckgo.com":
            return httpx.Response(primary_status, headers={"content-type": "text/html"})
        return httpx.Response(503, headers={"content-type": "text/html"})

    client = PublicWebClient(
        transport=httpx.MockTransport(transport),
        resolver=lambda _: ("93.184.216.34",),
    )
    try:
        with pytest.raises(WebAccessError, match="providers are unavailable"):
            client.research("unavailable report", max_results=1)
    finally:
        client.close()


@pytest.mark.parametrize(
    "url",
    (
        "http://example.com/report",
        "https://user@example.com/report",
        "https://example.com:8443/report",
        "https://example.com/report#fragment",
    ),
)
def test_web_client_rejects_unsafe_url_shapes(url: str) -> None:
    client = PublicWebClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200)),
        resolver=lambda _: ("93.184.216.34",),
    )
    try:
        with pytest.raises(WebAccessError):
            client.fetch(url)
    finally:
        client.close()
