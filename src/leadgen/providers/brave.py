"""Brave Search API provider (optional search provider)."""

from __future__ import annotations

import html
import re
from typing import Any

import httpx

from leadgen.core import http, secrets
from leadgen.providers import base
from leadgen.providers.base import ProviderError, ProviderInfo
from leadgen.providers.search import SearchResult

ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
ENV_KEY = "BRAVE_API_KEY"
MAX_COUNT = 20
_TAG_RE = re.compile(r"<[^>]+>")
# A rate-limit window longer than a day means the monthly allowance is used up.
_QUOTA_WINDOW_SECONDS = 86_400

INFO = ProviderInfo(
    key="brave",
    name="Brave Search API",
    purpose="Backup web search when Serper is unavailable or out of credits",
    env_keys=[ENV_KEY],
    free_tier="About $5 of free credit each month (about 1,000 searches); a card is required",
    signup_url="https://api-dashboard.search.brave.com/register",
    paid=True,
    default=False,
)

_limiter = base.RateLimiter(5)


def _clean(text: Any) -> str:
    return html.unescape(_TAG_RE.sub("", str(text or ""))).strip()


def _quota_hit(response: httpx.Response) -> bool:
    """True for a 429 whose reset header points more than a day away (monthly quota)."""
    values = response.headers.get("x-ratelimit-reset", "")
    numbers = [int(n) for n in re.findall(r"\d+", values)]
    return bool(numbers) and max(numbers) > _QUOTA_WINDOW_SECONDS


class BraveProvider:
    """`GET https://api.search.brave.com/res/v1/web/search` with `X-Subscription-Token`."""

    info = INFO

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def configured(self) -> bool:
        """True when BRAVE_API_KEY is set."""
        return bool(secrets.get(ENV_KEY))

    def search(self, query: str, country_gl: str = "", num: int = 10) -> list[SearchResult]:
        """Run one web search (at most 20 results per call). Records usage on success."""
        key = secrets.get(ENV_KEY)
        if not key:
            raise ProviderError("not_configured", f"{ENV_KEY} is not set.")
        if not query.strip():
            raise ValueError("query must not be empty")
        num = max(1, min(int(num), MAX_COUNT))
        params: dict[str, str | int] = {"q": query, "count": num}
        if country_gl.strip():
            params["country"] = country_gl.strip().upper()
        headers = {"X-Subscription-Token": key, "Accept": "application/json"}

        def send() -> httpx.Response:
            _limiter.acquire()
            if self._client is not None:
                return self._client.get(ENDPOINT, params=params, headers=headers)
            with http.client() as client:
                return client.get(ENDPOINT, params=params, headers=headers)

        response = base.send_with_retry(send)
        if response.status_code == 429 and _quota_hit(response):
            raise ProviderError("quota", "Brave has no credits left for this month (HTTP 429).")
        error = base.error_for_status(response, "Brave")
        if error is not None:
            raise error
        results = self._parse(response)
        base.record_usage(INFO.key, calls=1, credits=1)
        return results[:num]

    @staticmethod
    def _parse(response: httpx.Response) -> list[SearchResult]:
        try:
            data = response.json()
        except ValueError:
            raise ProviderError("parse", "Brave returned something that is not JSON.") from None
        if not isinstance(data, dict):
            raise ProviderError("parse", "Brave returned an unexpected response.")
        web = data.get("web") or {}
        items = web.get("results") if isinstance(web, dict) else None
        if items is None:
            return []
        if not isinstance(items, list):
            raise ProviderError("parse", "Brave returned an unexpected 'results' list.")
        results: list[SearchResult] = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                continue
            results.append(
                SearchResult(
                    title=_clean(item.get("title")),
                    link=item["url"],
                    snippet=_clean(item.get("description")),
                    position=len(results) + 1,
                )
            )
        return results

    def test(self) -> tuple[bool, str]:
        """One real search with `count=1` (1 request)."""
        if not self.configured():
            return False, f"{ENV_KEY} is not set."
        try:
            self.search("lead generator connection test", num=1)
        except ProviderError as exc:
            return False, exc.message
        return True, "Brave key works (used 1 request)."
