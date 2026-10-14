"""Serper provider (HTTP mocked with respx; no real network)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from leadgen.core import secrets
from leadgen.providers import base, serper
from leadgen.providers.base import ProviderError

KEY = "test-serper-key-1234"
URL = "https://google.serper.dev/search"

PAYLOAD = {
    "searchParameters": {"q": "x", "gl": "gb", "num": 10},
    "organic": [
        {
            "title": "Aria Tan - Head of Training - Northwind Logistics | LinkedIn",
            "link": "https://sg.linkedin.com/in/aria-tan-example",
            "snippet": "Aria Tan. Head of Training at Northwind Logistics.",
            "position": 1,
        },
        {"title": "Northwind Logistics", "link": "https://northwind.example.com/", "position": 2},
        {"title": "No link here"},
    ],
}


@pytest.fixture
def key(sleeps, ready_db):
    secrets.set("SERPER_API_KEY", KEY)


@respx.mock
def test_request_shape_and_parse(key):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=PAYLOAD))
    results = serper.SerperProvider().search("site:linkedin.com/in Aria", "GB", 10)
    request = route.calls[0].request
    assert request.headers["x-api-key"] == KEY
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {"q": "site:linkedin.com/in Aria", "num": 10, "gl": "gb"}
    assert [r.link for r in results] == [
        "https://sg.linkedin.com/in/aria-tan-example",
        "https://northwind.example.com/",
    ]
    assert results[0].position == 1 and results[1].snippet == ""
    assert base.usage("serper") == {"calls": 1, "credits": 1.0}


@respx.mock
def test_gl_omitted_when_blank_and_num_clamped(key):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={}))
    assert serper.SerperProvider().search("q", "", 500) == []
    body = json.loads(route.calls[0].request.content)
    assert body == {"q": "q", "num": 100}
    assert base.usage("serper")["credits"] == 10.0  # upper-bound estimate for num=100


def test_not_configured_makes_no_call(ready_db):
    provider = serper.SerperProvider()
    assert not provider.configured()
    with pytest.raises(ProviderError) as info:
        provider.search("q")
    assert info.value.code == "not_configured"
    assert provider.test() == (False, "SERPER_API_KEY is not set.")


def test_empty_query_rejected(key):
    with pytest.raises(ValueError):
        serper.SerperProvider().search("   ")


@respx.mock
def test_401_maps_to_auth_without_retry(key, sleeps):
    route = respx.post(URL).mock(return_value=httpx.Response(401, json={"message": "no"}))
    with pytest.raises(ProviderError) as info:
        serper.SerperProvider().search("q")
    assert info.value.code == "auth" and route.call_count == 1 and sleeps == []
    assert KEY not in info.value.message
    assert base.usage("serper")["calls"] == 0


@respx.mock
def test_429_retries_then_rate_limited(key, sleeps):
    route = respx.post(URL).mock(return_value=httpx.Response(429))
    with pytest.raises(ProviderError) as info:
        serper.SerperProvider().search("q")
    assert info.value.code == "rate_limited" and route.call_count == 3 and sleeps == [1.0, 2.0]


@respx.mock
def test_429_then_ok_succeeds(key):
    route = respx.post(URL).mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json=PAYLOAD)]
    )
    assert len(serper.SerperProvider().search("q")) == 2
    assert route.call_count == 2
    assert base.usage("serper")["calls"] == 1  # only the successful call is counted


@respx.mock
def test_500_retries_then_http_error(key, sleeps):
    route = respx.post(URL).mock(return_value=httpx.Response(500))
    with pytest.raises(ProviderError) as info:
        serper.SerperProvider().search("q")
    assert info.value.code == "http" and route.call_count == 3


@respx.mock
def test_out_of_credits_maps_to_quota(key):
    respx.post(URL).mock(
        return_value=httpx.Response(400, json={"message": "Not enough credits"})
    )
    with pytest.raises(ProviderError) as info:
        serper.SerperProvider().search("q")
    assert info.value.code == "quota"


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json=["a"]),
        httpx.Response(200, json={"organic": "oops"}),
    ],
)
def test_bad_body_maps_to_parse(key, response):
    respx.post(URL).mock(return_value=response)
    with pytest.raises(ProviderError) as info:
        serper.SerperProvider().search("q")
    assert info.value.code == "parse"
    assert base.usage("serper")["calls"] == 0


@respx.mock
def test_test_uses_one_credit(key):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=PAYLOAD))
    ok, message = serper.SerperProvider().test()
    assert ok and "1 credit" in message
    assert json.loads(route.calls[0].request.content)["num"] == 1
    assert base.usage("serper")["credits"] == 1.0


@respx.mock
def test_test_reports_failure(key):
    respx.post(URL).mock(return_value=httpx.Response(403))
    ok, message = serper.SerperProvider().test()
    assert not ok and "rejected" in message and KEY not in message


def test_credits_for():
    assert [serper.credits_for(n) for n in (1, 10, 11, 20, 100)] == [1, 1, 2, 2, 10]
