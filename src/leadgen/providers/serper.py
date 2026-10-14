"""Serper.dev Google search provider (the default search provider)."""

from __future__ import annotations

import math
from typing import Any

import httpx

from leadgen.core import http, secrets
from leadgen.providers import base
from leadgen.providers.base import ProviderError, ProviderInfo
from leadgen.providers.search import SearchResult

ENDPOINT = "https://google.serper.dev/search"
ENV_KEY = "SERPER_API_KEY"
MAX_NUM = 100

INFO = ProviderInfo(
    key="serper",
    name="Serper.dev (Google search)",
    purpose="Finds people (via Google results for LinkedIn profiles) and company information",
    env_keys=[ENV_KEY],
    free_tier="2,500 free searches, no credit card",
    signup_url="https://serper.dev",
    paid=False,
    default=True,
)

_limiter = base.RateLimiter(5)


def credits_for(num: int) -> int:
    """Credits one call is counted as: 1 for up to 10 results, then 1 more per 10 (upper bound)."""
    return max(1, math.ceil(num / 10))


class SerperProvider:
    """`POST https://google.serper.dev/search` with the `X-API-KEY` header (see DECISIONS)."""

    info = INFO

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def configured(self) -> bool:
        """True when SERPER_API_KEY is set."""
        return bool(secrets.get(ENV_KEY))

    def search(self, query: str, country_gl: str = "", num: int = 10) -> list[SearchResult]:
        """Run one Google search. Records usage after a successful response only."""
        key = secrets.get(ENV_KEY)
        if not key:
            raise ProviderError("not_configured", f"{ENV_KEY} is not set.")
        if not query.strip():
            raise ValueError("query must not be empty")
        num = max(1, min(int(num), MAX_NUM))
        body: dict[str, Any] = {"q": query, "num": num}
        if country_gl.strip():
            body["gl"] = country_gl.strip().lower()
        headers = {"X-API-KEY": key, "Content-Type": "application/json"}

        def send() -> httpx.Response:
            _limiter.acquire()
            if self._client is not None:
                return self._client.post(ENDPOINT, json=body, headers=headers)
            with http.client() as client:
                return client.post(ENDPOINT, json=body, headers=headers)

        response = base.send_with_retry(send)
        error = base.error_for_status(response, "Serper")
        if error is not None:
            raise error
        results = self._parse(response)
        base.record_usage(INFO.key, calls=1, credits=credits_for(num))
        return results[:num]

    @staticmethod
    def _parse(response: httpx.Response) -> list[SearchResult]:
        try:
            data = response.json()
        except ValueError:
            raise ProviderError("parse", "Serper returned something that is not JSON.") from None
        if not isinstance(data, dict):
            raise ProviderError("parse", "Serper returned an unexpected response.")
        organic = data.get("organic") or []
        if not isinstance(organic, list):
            raise ProviderError("parse", "Serper returned an unexpected 'organic' list.")
        results: list[SearchResult] = []
        for index, item in enumerate(organic, start=1):
            if not isinstance(item, dict) or not isinstance(item.get("link"), str):
                continue
            position = item.get("position")
            results.append(
                SearchResult(
                    title=str(item.get("title") or ""),
                    link=item["link"],
                    snippet=str(item.get("snippet") or ""),
                    position=position if isinstance(position, int) else index,
                )
            )
        return results

    def test(self) -> tuple[bool, str]:
        """One real search with `num=1` (1 credit)."""
        if not self.configured():
            return False, f"{ENV_KEY} is not set."
        try:
            self.search("lead generator connection test", num=1)
        except ProviderError as exc:
            return False, exc.message
        return True, "Serper key works (used 1 credit)."
