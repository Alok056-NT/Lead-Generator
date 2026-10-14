"""Search cache and the fake provider."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from leadgen import db
from leadgen.core import secrets
from leadgen.providers import base, search
from leadgen.providers.base import ProviderError
from leadgen.providers.fake import FakeSearchProvider
from leadgen.providers.search import SearchResult, cache_key, cached_search
from leadgen.providers.serper import SerperProvider

R1 = SearchResult("Aria Tan", "https://example.com/aria", "Head of Training", 1)


def test_cache_key_normalises():
    a = cache_key("serper", "  Head   of Training ", "GB", 10)
    assert a == cache_key("serper", "head of training", "gb", 10)
    assert a != cache_key("brave", "head of training", "gb", 10)
    assert a != cache_key("serper", "head of training", "gb", 20)
    assert a != cache_key("serper", "head of training", "sg", 10)
    assert len(a) == 64


@respx.mock
def test_second_identical_query_hits_cache(sleeps, ready_db):
    secrets.set("SERPER_API_KEY", "test-serper-key-1234")
    route = respx.post("https://google.serper.dev/search").mock(
        return_value=httpx.Response(
            200, json={"organic": [{"title": "T", "link": "https://example.com/", "snippet": "S"}]}
        )
    )
    provider = SerperProvider()
    first = cached_search(provider, "Northwind Logistics", "gb", 10)
    second = cached_search(provider, "  northwind   logistics", "GB", 10)
    assert route.call_count == 1
    assert first == second == [SearchResult("T", "https://example.com/", "S", 1)]
    assert base.usage("serper") == {"calls": 1, "credits": 1.0}  # the hit cost nothing


def test_expired_entry_is_refetched(ready_db):
    fake = FakeSearchProvider({"q": [R1]})
    assert cached_search(fake, "q") == [R1]
    assert cached_search(fake, "q") == [R1]
    assert len(fake.calls) == 1
    old = (datetime.now(UTC) - timedelta(days=31)).isoformat().replace("+00:00", "Z")
    with db.session() as conn:
        conn.execute("UPDATE search_cache SET fetched_at = ?", (old,))
    assert cached_search(fake, "q") == [R1]
    assert len(fake.calls) == 2
    with db.session() as conn:
        assert len(db.query(conn, "SELECT * FROM search_cache")) == 1  # row replaced, not added


def test_ttl_is_configurable(ready_db):
    fake = FakeSearchProvider({"q": [R1]})
    cached_search(fake, "q")
    cached_search(fake, "q", ttl_days=0)
    assert len(fake.calls) == 2


def test_empty_results_are_cached_errors_are_not(ready_db):
    fake = FakeSearchProvider({})
    assert cached_search(fake, "nothing") == []
    assert cached_search(fake, "nothing") == []
    assert len(fake.calls) == 1
    failing = FakeSearchProvider({"q": [R1]}, fail_with=ProviderError("http", "down"))
    with pytest.raises(ProviderError):
        cached_search(failing, "q")
    with db.session() as conn:
        assert db.query(conn, "SELECT * FROM search_cache WHERE query = 'q'") == []


def test_damaged_cache_entry_is_refetched(ready_db):
    fake = FakeSearchProvider({"q": [R1]})
    cached_search(fake, "q")
    with db.session() as conn:
        conn.execute("UPDATE search_cache SET results_json = '[{\"bogus\": 1}]'")
    assert cached_search(fake, "q") == [R1]
    assert len(fake.calls) == 2


def test_cache_separates_providers_and_countries(ready_db):
    one = FakeSearchProvider({"q": [R1]})
    cached_search(one, "q", "gb")
    cached_search(one, "q", "sg")
    assert len(one.calls) == 2


def test_fake_provider_dict_callable_and_errors():
    fake = FakeSearchProvider({"a": [R1, R1]})
    assert fake.search("a", "gb", 1) == [R1]
    assert fake.search("zzz") == []
    assert fake.calls == [("a", "gb", 1), ("zzz", "", 10)]
    assert fake.configured() and fake.test() == (True, "Fake provider works.")

    dynamic = FakeSearchProvider(
        lambda q, gl, n: [SearchResult(q, f"https://{gl}.example.com", "", 1)]
    )
    assert dynamic.search("x", "sg")[0].link == "https://sg.example.com"

    later = FakeSearchProvider({"a": [R1]})
    later.fail_after(1, ProviderError("quota", "out"))
    later.search("a")
    with pytest.raises(ProviderError) as info:
        later.search("a")
    assert info.value.code == "quota"

    broken = FakeSearchProvider(fail_with=ProviderError("auth", "bad"))
    assert broken.test() == (False, "bad")
    with pytest.raises(ProviderError):
        broken.search("a")


def test_fake_satisfies_search_protocol():
    assert isinstance(FakeSearchProvider(), search.SearchProvider)


def test_is_cached_matches_cached_search(ready_db):
    fake = FakeSearchProvider({"q": [R1]})
    assert search.is_cached("fake", "q") is False
    cached_search(fake, "q", "sg", 10)
    assert search.is_cached("fake", "  Q ", "SG", 10) is True
    assert search.is_cached("fake", "q", "gb", 10) is False
    assert search.is_cached("fake", "q", "sg", 10, ttl_days=0) is False
    assert len(fake.calls) == 1  # asking does not search
