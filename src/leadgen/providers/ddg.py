"""DuckDuckGo HTML search: last-resort, unreliable, off unless `allow_ddg_fallback` is true."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

from leadgen.core import config, http
from leadgen.providers import base
from leadgen.providers.base import ProviderError, ProviderInfo
from leadgen.providers.search import SearchResult

ENDPOINT = "https://html.duckduckgo.com/html/"

INFO = ProviderInfo(
    key="ddg",
    name="DuckDuckGo (HTML page)",
    purpose="Last-resort web search when no search key is set; unreliable and rate limited",
    env_keys=[],
    free_tier="Free, no key, but often blocks automated use",
    signup_url="",
    paid=False,
    default=False,
)

_limiter = base.RateLimiter(0.5)


def unwrap_link(href: str) -> str:
    """Return the real URL from a DuckDuckGo redirect link (`//duckduckgo.com/l/?uddg=<url>`).

    Direct links are returned unchanged (protocol-relative ones get `https:`).
    """
    href = href.strip()
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    host = (parsed.hostname or "").lower()
    if (host == "duckduckgo.com" or host.endswith(".duckduckgo.com")) and parsed.path.startswith(
        "/l/"
    ):
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return target[0]
    return href


def parse_html(page: str) -> list[SearchResult]:
    """Parse a DuckDuckGo HTML results page. Ads are skipped; every result is low confidence."""
    soup = BeautifulSoup(page, "html.parser")
    results: list[SearchResult] = []
    for block in soup.select("div.result"):
        if "result--ad" in (block.get("class") or []):
            continue
        anchor = block.select_one("a.result__a")
        href = anchor.get("href") if anchor else None
        if anchor is None or not isinstance(href, str) or not href.strip():
            continue
        link = unwrap_link(href)
        if not link.lower().startswith(("http://", "https://")):
            continue
        snippet = block.select_one(".result__snippet")
        results.append(
            SearchResult(
                title=anchor.get_text(" ", strip=True),
                link=link,
                snippet=snippet.get_text(" ", strip=True) if snippet else "",
                position=len(results) + 1,
                low_confidence=True,
            )
        )
    return results


def is_challenge(page: str) -> bool:
    """True for DuckDuckGo's human-check page (detected by its markup, not by the word)."""
    head = page[:50000].lower()
    return "anomaly-modal" in head or "anomaly.js" in head


def _allowed() -> bool:
    try:
        return config.load_settings().allow_ddg_fallback
    except config.ConfigError:
        return False


class DuckDuckGoProvider:
    """POSTs the query to the DuckDuckGo HTML endpoint and parses the page."""

    info = INFO

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def configured(self) -> bool:
        """True only when `allow_ddg_fallback` is switched on in settings."""
        return _allowed()

    def search(self, query: str, country_gl: str = "", num: int = 10) -> list[SearchResult]:
        """Run one search (the country is ignored; put it in the query text)."""
        if not _allowed():
            raise ProviderError("not_configured", "DuckDuckGo fallback is switched off.")
        if not query.strip():
            raise ValueError("query must not be empty")

        def send() -> httpx.Response:
            _limiter.acquire()
            if self._client is not None:
                return self._client.post(ENDPOINT, data={"q": query})
            with http.client() as client:
                return client.post(ENDPOINT, data={"q": query})

        response = base.send_with_retry(send)
        error = base.error_for_status(response, "DuckDuckGo")
        if error is not None:
            raise error
        results = parse_html(response.text)
        if response.status_code == 202 or (not results and is_challenge(response.text)):
            raise ProviderError("rate_limited", "DuckDuckGo asked for a human check.")
        base.record_usage(INFO.key, calls=1, credits=0)
        return results[: max(1, num)]

    def test(self) -> tuple[bool, str]:
        """One search; no credits involved."""
        if not self.configured():
            return False, "DuckDuckGo fallback is switched off (allow_ddg_fallback)."
        try:
            found = self.search("example domain", num=1)
        except ProviderError as exc:
            return False, exc.message
        return (True, "DuckDuckGo answered.") if found else (False, "DuckDuckGo returned nothing.")
