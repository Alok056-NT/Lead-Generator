"""Brave and DuckDuckGo providers (HTTP mocked with respx)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from leadgen.core import config, secrets
from leadgen.providers import base, brave, ddg
from leadgen.providers.base import ProviderError

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
FIXTURE = Path(__file__).parent / "fixtures" / "ddg_results.html"

BRAVE_PAYLOAD = {
    "type": "search",
    "web": {
        "type": "search",
        "results": [
            {
                "title": "Aria Tan | Northwind &amp; Co",
                "url": "https://example.com/aria",
                "description": "Head of <strong>Training</strong> at Northwind",
            },
            {"title": "No url"},
            {"title": "Second", "url": "https://example.com/two"},
        ],
    },
}


@pytest.fixture
def brave_key(sleeps, ready_db):
    secrets.set("BRAVE_API_KEY", "test-brave-key-5678")


@respx.mock
def test_brave_request_and_parse(brave_key):
    route = respx.get(BRAVE_URL).mock(return_value=httpx.Response(200, json=BRAVE_PAYLOAD))
    results = brave.BraveProvider().search("aria tan", "gb", 50)
    request = route.calls[0].request
    assert request.headers["x-subscription-token"] == "test-brave-key-5678"
    assert dict(request.url.params) == {"q": "aria tan", "count": "20", "country": "GB"}
    assert [r.link for r in results] == ["https://example.com/aria", "https://example.com/two"]
    assert results[0].title == "Aria Tan | Northwind & Co"
    assert results[0].snippet == "Head of Training at Northwind"
    assert [r.position for r in results] == [1, 2]
    assert base.usage("brave") == {"calls": 1, "credits": 1.0}


@respx.mock
def test_brave_country_outside_allowed_list_sends_all(brave_key):
    route = respx.get(BRAVE_URL).mock(return_value=httpx.Response(200, json=BRAVE_PAYLOAD))
    brave.BraveProvider().search("q", "sg")
    assert route.calls[0].request.url.params["country"] == "ALL"
    brave.BraveProvider().search("q", "")
    assert "country" not in route.calls[1].request.url.params
    assert "GB" in brave.BRAVE_COUNTRIES and "SG" not in brave.BRAVE_COUNTRIES
    assert len(brave.BRAVE_COUNTRIES) == 38


@respx.mock
def test_brave_no_web_section_is_empty(brave_key):
    respx.get(BRAVE_URL).mock(return_value=httpx.Response(200, json={"type": "search"}))
    assert brave.BraveProvider().search("q") == []


@respx.mock
def test_brave_errors(brave_key, sleeps):
    provider = brave.BraveProvider()
    route = respx.get(BRAVE_URL)
    route.mock(return_value=httpx.Response(401))
    with pytest.raises(ProviderError) as info:
        provider.search("q")
    assert info.value.code == "auth"

    def limited(**headers):
        return httpx.Response(429, headers=headers)

    # Per-second window empty, monthly window fine: a plain rate limit, not quota.
    route.mock(
        return_value=limited(
            **{
                "x-ratelimit-policy": "1;w=1, 15000;w=2592000",
                "x-ratelimit-remaining": "0, 500",
                "x-ratelimit-reset": "1, 1419704",
            }
        )
    )
    with pytest.raises(ProviderError) as info:
        provider.search("q")
    assert info.value.code == "rate_limited"

    # Monthly window empty: quota.
    route.mock(
        return_value=limited(
            **{
                "x-ratelimit-policy": "1;w=1, 15000;w=2592000",
                "x-ratelimit-remaining": "1, 0",
                "x-ratelimit-reset": "1, 1419704",
            }
        )
    )
    with pytest.raises(ProviderError) as info:
        provider.search("q")
    assert info.value.code == "quota"

    # Without a Policy header the windows are sized by their reset times.
    no_policy = {"x-ratelimit-reset": "1, 1419704"}
    route.mock(return_value=limited(**no_policy, **{"x-ratelimit-remaining": "0, 500"}))
    with pytest.raises(ProviderError) as info:
        provider.search("q")
    assert info.value.code == "rate_limited"
    route.mock(return_value=limited(**no_policy, **{"x-ratelimit-remaining": "1, 0"}))
    with pytest.raises(ProviderError) as info:
        provider.search("q")
    assert info.value.code == "quota"

    # Unlimited monthly plan (limit 0): a per-second 429 is never quota, even at Remaining 0.
    route.mock(
        return_value=limited(
            **{
                "x-ratelimit-policy": "50;w=1, 0;w=2592000",
                "x-ratelimit-remaining": "0, 0",
                "x-ratelimit-reset": "1, 1419704",
            }
        )
    )
    with pytest.raises(ProviderError) as info:
        provider.search("q")
    assert info.value.code == "rate_limited"

    # Same without Policy: limits come from X-RateLimit-Limit by position.
    route.mock(
        return_value=limited(
            **{
                "x-ratelimit-limit": "50, 0",
                "x-ratelimit-remaining": "0, 0",
                "x-ratelimit-reset": "1, 1419704",
            }
        )
    )
    with pytest.raises(ProviderError) as info:
        provider.search("q")
    assert info.value.code == "rate_limited"

    # No rate-limit headers at all: rate_limited.
    route.mock(return_value=httpx.Response(429))
    with pytest.raises(ProviderError) as info:
        provider.search("q")
    assert info.value.code == "rate_limited"

    route.mock(return_value=httpx.Response(200, text="nope"))
    with pytest.raises(ProviderError) as info:
        provider.search("q")
    assert info.value.code == "parse"


@respx.mock
def test_brave_test_and_not_configured(brave_key):
    respx.get(BRAVE_URL).mock(return_value=httpx.Response(200, json=BRAVE_PAYLOAD))
    assert brave.BraveProvider().test()[0] is True
    secrets.delete("BRAVE_API_KEY")
    assert not brave.BraveProvider().configured()
    assert brave.BraveProvider().test() == (False, "BRAVE_API_KEY is not set.")
    with pytest.raises(ProviderError):
        brave.BraveProvider().search("q")


# --- DuckDuckGo -----------------------------------------------------------------------------


def test_ddg_parser_on_fixture():
    results = ddg.parse_html(FIXTURE.read_text(encoding="utf-8"))
    assert [r.link for r in results] == [
        "https://www.northwind-logistics.example.com/about?a=1&b=2",
        "https://www.example.org/direct",
    ]
    assert results[0].title == "Northwind Logistics | About us"
    assert results[0].snippet == "Northwind Logistics is a fictional freight company in Singapore."
    assert results[1].snippet == ""
    assert [r.position for r in results] == [1, 2]
    assert all(r.low_confidence for r in results)


def test_unwrap_link():
    wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fx%3D1&rut=zz"
    assert ddg.unwrap_link(wrapped) == "https://example.com/a?x=1"
    assert ddg.unwrap_link("https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com") == (
        "https://example.com"
    )
    assert ddg.unwrap_link("https://example.com/x") == "https://example.com/x"
    assert ddg.unwrap_link("//example.com/x") == "https://example.com/x"
    assert ddg.unwrap_link("https://duckduckgo.com/l/?nothing=1").startswith("https://duckduckgo")


def _allow_ddg():
    settings = config.load_settings()
    settings.allow_ddg_fallback = True
    config.save_settings(settings)


def test_ddg_disabled_by_default(ready_db):
    provider = ddg.DuckDuckGoProvider()
    assert not provider.configured()
    with pytest.raises(ProviderError) as info:
        provider.search("q")
    assert info.value.code == "not_configured"
    assert provider.test()[0] is False


@respx.mock
def test_ddg_search_posts_query_and_records_free_usage(sleeps, ready_db):
    _allow_ddg()
    route = respx.post(ddg.ENDPOINT).mock(
        return_value=httpx.Response(200, text=FIXTURE.read_text(encoding="utf-8"))
    )
    provider = ddg.DuckDuckGoProvider()
    assert provider.configured()
    results = provider.search("northwind logistics", num=1)
    assert len(results) == 1 and results[0].low_confidence
    assert b"q=northwind+logistics" in route.calls[0].request.content
    assert base.usage("ddg") == {"calls": 1, "credits": 0.0}
    assert provider.test()[0] is True


@respx.mock
def test_ddg_challenge_page_is_rate_limited(sleeps, ready_db):
    _allow_ddg()
    respx.post(ddg.ENDPOINT).mock(
        return_value=httpx.Response(200, text='<div class="anomaly-modal">Are you human?</div>')
    )
    with pytest.raises(ProviderError) as info:
        ddg.DuckDuckGoProvider().search("q")
    assert info.value.code == "rate_limited"
    assert base.usage("ddg")["calls"] == 0


@respx.mock
def test_ddg_results_mentioning_anomaly_are_not_a_challenge(sleeps, ready_db):
    _allow_ddg()
    page = (
        '<div class="result"><a class="result__a" href="https://www.example.org/anomaly">'
        "Anomaly detection explained</a>"
        '<a class="result__snippet">Anomaly is a fictional creative agency.</a></div>'
    )
    respx.post(ddg.ENDPOINT).mock(return_value=httpx.Response(200, text=page))
    results = ddg.DuckDuckGoProvider().search("anomaly detection")
    assert [r.link for r in results] == ["https://www.example.org/anomaly"]
    assert base.usage("ddg")["calls"] == 1
    assert ddg.is_challenge('<script src="/anomaly.js"></script>')
    assert not ddg.is_challenge(page)
