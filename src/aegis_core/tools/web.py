from __future__ import annotations

import html
import http.client
import ipaddress
import socket
import ssl
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

import httpx


class WebAccessError(RuntimeError):
    pass


Resolver = Callable[[str], tuple[str, ...]]


def validate_public_https_url(url: str) -> str:
    return _validate_public_https_url(url, _resolve_host)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, address: str) -> None:
        super().__init__(
            hostname,
            port=443,
            timeout=8,
            context=ssl.create_default_context(),
        )
        self._validated_address = address

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._validated_address, self.port),
            self.timeout,
            self.source_address,
        )
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


class _PinnedHTTPSTransport(httpx.BaseTransport):
    def __init__(self, resolver: Resolver, maximum_bytes: int) -> None:
        self._resolver = resolver
        self._maximum_bytes = maximum_bytes

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = _validate_public_https_url(str(request.url), self._resolver)
        parsed = urlparse(url)
        hostname = parsed.hostname
        if hostname is None:
            raise WebAccessError("web hostname is invalid")
        addresses = self._resolver(hostname)
        _validate_public_addresses(addresses)
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        connection = _PinnedHTTPSConnection(hostname, addresses[0])
        try:
            connection.request(
                "GET",
                target,
                headers={key: value for key, value in request.headers.items()},
            )
            response = connection.getresponse()
            body = response.read(self._maximum_bytes + 1)
            headers = [(key, value) for key, value in response.getheaders()]
            return httpx.Response(
                response.status,
                headers=headers,
                content=body,
                request=request,
            )
        except (OSError, http.client.HTTPException) as error:
            raise WebAccessError("web request failed") from error
        finally:
            connection.close()


class _SearchParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._href: str | None = None
        self._title: list[str] = []
        self._snippet: list[str] = []
        self._capture_title = False
        self._capture_snippet = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "a" and "result__a" in classes:
            self._href = attributes.get("href")
            self._title = []
            self._snippet = []
            self._capture_title = True
        elif "result__snippet" in classes and self._href is not None:
            self._capture_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture_title:
            self._capture_title = False
        if self._capture_snippet and tag in {"a", "div", "span"}:
            self._capture_snippet = False
            self._commit()

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self._title.append(data)
        elif self._capture_snippet:
            self._snippet.append(data)

    def close(self) -> None:
        super().close()
        self._commit()

    def _commit(self) -> None:
        if self._href is None:
            return
        title = _normalize_text(" ".join(self._title))
        snippet = _normalize_text(" ".join(self._snippet))
        if title:
            self.results.append(
                {
                    "title": title[:300],
                    "url": _unwrap_duckduckgo_url(self._href),
                    "snippet": snippet[:800],
                }
            )
        self._href = None
        self._title = []
        self._snippet = []


class _PageTextParser(HTMLParser):
    _TEXT_TAGS = frozenset({"article", "h1", "h2", "h3", "li", "main", "p", "title"})
    _SKIP_TAGS = frozenset({"script", "style", "svg", "noscript"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.parts: list[str] = []
        self._active: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        if self._skip_depth == 0 and tag in self._TEXT_TAGS:
            self._active.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if self._active and self._active[-1] == tag:
            self._active.pop()

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not self._active:
            return
        normalized = _normalize_text(data)
        if not normalized:
            return
        if self._active[-1] == "title" and not self.title:
            self.title = normalized[:300]
        else:
            self.parts.append(normalized)


class _InlineTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        normalized = _normalize_text(data)
        if normalized:
            self.parts.append(normalized)


class PublicWebClient:
    SEARCH_URL = "https://html.duckduckgo.com/html/?q={query}"
    FALLBACK_SEARCH_URL = "https://www.bing.com/search?q={query}&format=rss"
    MAX_RESPONSE_BYTES = 524_288
    MAX_REDIRECTS = 3
    MAX_SEARCH_CANDIDATES = 10
    RSS_CONTENT_TYPES = frozenset({"application/rss+xml", "application/xml", "text/xml"})

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        self._resolver = resolver or _resolve_host
        resolved_transport = transport or _PinnedHTTPSTransport(
            self._resolver, self.MAX_RESPONSE_BYTES
        )
        self._client = httpx.Client(
            transport=resolved_transport,
            timeout=httpx.Timeout(8, connect=3),
            follow_redirects=False,
            trust_env=False,
            headers={
                "Accept": "text/html,text/plain;q=0.9",
                "Accept-Encoding": "identity",
                "User-Agent": "Jarvis/1.0 (+local autonomous research)",
            },
        )

    def close(self) -> None:
        self._client.close()

    def fetch(self, url: str, *, max_characters: int = 8_000) -> dict[str, str]:
        if not 512 <= max_characters <= 16_000:
            raise WebAccessError("web character limit is invalid")
        final_url, content_type, body = self._request(url)
        decoded = body.decode("utf-8", errors="replace")
        if content_type.startswith("text/plain"):
            return {
                "url": final_url,
                "title": urlparse(final_url).hostname or "",
                "content": _normalize_text(decoded)[:max_characters],
            }
        parser = _PageTextParser()
        parser.feed(decoded)
        parser.close()
        content = _normalize_text("\n".join(parser.parts))[:max_characters]
        return {"url": final_url, "title": parser.title, "content": content}

    def research(self, query: str, *, max_results: int = 3) -> list[dict[str, str]]:
        normalized_query = _normalize_text(query)
        if not normalized_query or len(normalized_query) > 300 or not 1 <= max_results <= 5:
            raise WebAccessError("web research arguments are invalid")
        search_url = self.SEARCH_URL.format(query=quote_plus(normalized_query))
        candidates: list[dict[str, str]] = []
        try:
            _, _, body = self._request(search_url)
        except WebAccessError:
            pass
        else:
            parser = _SearchParser()
            parser.feed(body.decode("utf-8", errors="replace"))
            parser.close()
            candidates = parser.results[: self.MAX_SEARCH_CANDIDATES]

        seen: set[str] = set()
        results = self._read_research_candidates(candidates, max_results=max_results, seen=seen)
        if results:
            return results

        fallback_url = self.FALLBACK_SEARCH_URL.format(query=quote_plus(normalized_query))
        try:
            _, _, body = self._request(
                fallback_url,
                accepted_content_types=self.RSS_CONTENT_TYPES,
            )
        except WebAccessError as error:
            raise WebAccessError("web search providers are unavailable") from error
        fallback_candidates = _parse_rss_results(body)[: self.MAX_SEARCH_CANDIDATES]
        results = self._read_research_candidates(
            fallback_candidates,
            max_results=max_results,
            seen=seen,
        )
        if results:
            return results
        if candidates or fallback_candidates:
            raise WebAccessError("web result pages are unavailable")
        return []

    def _read_research_candidates(
        self,
        candidates: list[dict[str, str]],
        *,
        max_results: int,
        seen: set[str],
    ) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        for candidate in candidates:
            if candidate["url"] in seen:
                continue
            seen.add(candidate["url"])
            try:
                page = self.fetch(candidate["url"], max_characters=6_000)
            except WebAccessError:
                continue
            page["snippet"] = candidate["snippet"]
            if not page["title"]:
                page["title"] = candidate["title"]
            results.append(page)
            if len(results) >= max_results:
                break
        return results

    def _request(
        self,
        url: str,
        *,
        accepted_content_types: frozenset[str] = frozenset({"text/html", "text/plain"}),
    ) -> tuple[str, str, bytes]:
        current = url
        for redirect_count in range(self.MAX_REDIRECTS + 1):
            current = _validate_public_https_url(current, self._resolver)
            try:
                with self._client.stream("GET", current) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location or redirect_count >= self.MAX_REDIRECTS:
                            raise WebAccessError("web redirect is invalid")
                        current = urljoin(current, location)
                        continue
                    if response.status_code != 200:
                        raise WebAccessError("web response status is unavailable")
                    content_type = response.headers.get("content-type", "").split(";", 1)[0]
                    if content_type not in accepted_content_types:
                        raise WebAccessError("web content type is unsupported")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > self.MAX_RESPONSE_BYTES:
                            raise WebAccessError("web response exceeds limit")
                    return current, content_type, bytes(body)
            except httpx.HTTPError as error:
                raise WebAccessError("web request failed") from error
        raise WebAccessError("web redirect limit exceeded")


def _resolve_host(host: str) -> tuple[str, ...]:
    try:
        values = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as error:
        raise WebAccessError("web host resolution failed") from error
    return tuple(sorted({value[4][0] for value in values}))


def _parse_rss_results(body: bytes) -> list[dict[str, str]]:
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as error:
        raise WebAccessError("web search response is invalid") from error
    if root.tag != "rss":
        raise WebAccessError("web search response is invalid")
    channel = root.find("channel")
    if channel is None:
        raise WebAccessError("web search response is invalid")
    results: list[dict[str, str]] = []
    for item in channel.findall("item"):
        title = _normalize_text(item.findtext("title", default=""))[:300]
        url = _normalize_text(item.findtext("link", default=""))
        description = item.findtext("description", default="")
        snippet_parser = _InlineTextParser()
        snippet_parser.feed(description)
        snippet_parser.close()
        snippet = _normalize_text(" ".join(snippet_parser.parts))[:800]
        if title and url:
            results.append({"title": title, "url": url, "snippet": snippet})
    return results


def _validate_public_https_url(url: str, resolver: Resolver) -> str:
    if len(url) > 2_048:
        raise WebAccessError("web URL exceeds limit")
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or parsed.fragment
    ):
        raise WebAccessError("web URL is not a public HTTPS URL")
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise WebAccessError("web hostname is invalid") from error
    addresses = resolver(hostname)
    _validate_public_addresses(addresses)

    return parsed._replace(netloc=hostname if parsed.port is None else f"{hostname}:443").geturl()


def _validate_public_addresses(addresses: tuple[str, ...]) -> None:
    if not addresses:
        raise WebAccessError("web host has no address")
    for address in addresses:
        try:
            parsed_address = ipaddress.ip_address(address)
        except ValueError as error:
            raise WebAccessError("web host address is invalid") from error
        if not parsed_address.is_global:
            raise WebAccessError("web host resolved outside public Internet")


def _unwrap_duckduckgo_url(url: str) -> str:
    decoded = html.unescape(url)
    if decoded.startswith("//"):
        decoded = "https:" + decoded
    parsed = urlparse(decoded)
    target = (
        parse_qs(parsed.query).get("uddg")
        if parsed.hostname
        in {
            "duckduckgo.com",
            "html.duckduckgo.com",
        }
        else None
    )
    return target[0] if target else decoded


def _normalize_text(value: str) -> str:
    return " ".join(value.split())
