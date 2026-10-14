"""Search provider interface, result type and the 30-day search cache."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

from leadgen import db
from leadgen.providers.base import Provider


@dataclass
class SearchResult:
    """One organic search hit. `low_confidence` marks results from unreliable sources (DDG)."""

    title: str
    link: str
    snippet: str
    position: int
    low_confidence: bool = False


@runtime_checkable
class SearchProvider(Provider, Protocol):
    """A provider that can run web searches. `search` records its own usage."""

    def search(self, query: str, country_gl: str = "", num: int = 10) -> list[SearchResult]:
        """Return up to `num` results for `query`, localised to Google country code `country_gl`."""


def normalize_query(query: str) -> str:
    """Case-fold and collapse whitespace so trivially different queries share a cache entry."""
    return " ".join(query.casefold().split())


def cache_key(provider_key: str, query: str, country_gl: str, num: int) -> str:
    """sha256 of `provider|normalized query|gl|num`."""
    raw = f"{provider_key}|{normalize_query(query)}|{country_gl.strip().lower()}|{num}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_time(text: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _read_cache(provider_key: str, key: str, ttl_days: int) -> list[SearchResult] | None:
    with db.session() as conn:
        rows = db.query(
            conn,
            "SELECT results_json, fetched_at FROM search_cache "
            "WHERE provider = ? AND query_hash = ?",
            (provider_key, key),
        )
    if not rows:
        return None
    fetched = _parse_time(rows[0]["fetched_at"])
    if fetched is None or datetime.now(UTC) - fetched > timedelta(days=ttl_days):
        return None
    try:
        return [SearchResult(**item) for item in rows[0]["results_json"]]
    except (TypeError, ValueError):
        return None  # damaged entry: refetch


def _write_cache(
    provider_key: str, key: str, query: str, results: list[SearchResult]
) -> None:
    with db.session() as conn:
        db.upsert(
            conn,
            "search_cache",
            ["provider", "query_hash"],
            provider=provider_key,
            query_hash=key,
            query=normalize_query(query),
            results_json=[asdict(r) for r in results],
            fetched_at=db.now(),
        )


def cached_search(
    provider: SearchProvider,
    query: str,
    country_gl: str = "",
    num: int = 10,
    ttl_days: int = 30,
) -> list[SearchResult]:
    """Search through the cache. A hit makes no HTTP call and records no usage.

    Failed searches (exceptions) are never cached. Empty result lists are cached.
    """
    provider_key = provider.info.key
    key = cache_key(provider_key, query, country_gl, num)
    hit = _read_cache(provider_key, key, ttl_days)
    if hit is not None:
        return hit
    results = provider.search(query, country_gl, num)
    _write_cache(provider_key, key, query, results)
    return results


__all__ = [
    "SearchProvider",
    "SearchResult",
    "cache_key",
    "cached_search",
    "normalize_query",
]
