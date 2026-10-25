"""Tavily web search (free 1,000 credits a month, no card). See DECISIONS (V13)."""

from __future__ import annotations

import re
from typing import Any

import httpx

from leadgen.core import http, secrets
from leadgen.providers import base
from leadgen.providers.base import ProviderError, ProviderInfo
from leadgen.providers.search import SearchResult

SEARCH_URL = "https://api.tavily.com/search"
USAGE_URL = "https://api.tavily.com/usage"
ENV_KEY = "TAVILY_API_KEY"
MAX_RESULTS = 20

INFO = ProviderInfo(
    key="tavily",
    name="Tavily (web search)",
    purpose="Finds companies and people with a general web search",
    env_keys=[ENV_KEY],
    free_tier="1,000 free credits every month, no card",
    signup_url="https://app.tavily.com",
    costs="1 credit per search",
    paid=False,
    default=False,
)

_limiter = base.RateLimiter(5)
_SITE_RE = re.compile(r"\bsite:(\S+)", re.IGNORECASE)


class TavilyProvider:
    """Tavily search (`POST .../search`) and the free usage call. The key goes in a header."""

    info = INFO

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def configured(self) -> bool:
        """True when TAVILY_API_KEY is set."""
        return bool(secrets.get(ENV_KEY))

    def _headers(self) -> dict[str, str]:
        key = secrets.get(ENV_KEY)
        if not key:
            raise ProviderError("not_configured", f"{ENV_KEY} is not set.")
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def search(self, query: str, country_gl: str = "", num: int = 10) -> list[SearchResult]:
        """One basic search. `site:x` in the query becomes include_domains=[x] (Tavily is not
        Google). `country_gl` is ignored.
        shortcut: no country filter, add one if results are off."""
        headers = self._headers()
        if not query.strip():
            raise ValueError("query must not be empty")
        sites = [s.strip("/").lower() for s in _SITE_RE.findall(query)]
        body: dict[str, Any] = {
            "query": _SITE_RE.sub(" ", query).strip() or query,
            "search_depth": "basic",
            "max_results": max(1, min(int(num), MAX_RESULTS)),
        }
        if sites:
            body["include_domains"] = sites

        def send() -> httpx.Response:
            _limiter.acquire()
            if self._client is not None:
                return self._client.post(SEARCH_URL, json=body, headers=headers)
            with http.client() as client:
                return client.post(SEARCH_URL, json=body, headers=headers)

        response = base.send_with_retry(send, retry_read_timeout=False)
        error = base.error_for_status(response, "Tavily")
        if error is not None:
            raise error
        data = base.json_object(response, "Tavily")
        items = data.get("results") or []
        if not isinstance(items, list):
            raise ProviderError("parse", "Tavily returned an unexpected 'results' list.")
        results = [
            SearchResult(
                title=str(item.get("title") or ""),
                link=item["url"],
                snippet=str(item.get("content") or ""),
                position=index,
            )
            for index, item in enumerate(items, start=1)
            if isinstance(item, dict) and isinstance(item.get("url"), str)
        ]
        base.record_usage(INFO.key, calls=1, credits=1)
        return results

    def test(self) -> tuple[bool, str]:
        """GET the usage endpoint (runs no search, costs nothing)."""
        try:
            headers = self._headers()

            def send() -> httpx.Response:
                _limiter.acquire()
                if self._client is not None:
                    return self._client.get(USAGE_URL, headers=headers)
                with http.client() as client:
                    return client.get(USAGE_URL, headers=headers)

            response = base.send_with_retry(send)
            error = base.error_for_status(response, "Tavily")
            if error is not None:
                raise error
            data = base.json_object(response, "Tavily")
        except ProviderError as exc:
            return False, exc.message
        account = data.get("account") or {}
        used, limit = account.get("plan_usage"), account.get("plan_limit")
        if isinstance(used, (int, float)) and isinstance(limit, (int, float)):
            return True, f"Tavily key works ({max(0, limit - used):g} credits left)."
        return True, "Tavily key works."
