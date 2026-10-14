"""Tests for the HTTP helper (no real network: respx mocks httpx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from leadgen import __version__
from leadgen.core import http


def test_client_defaults():
    with http.client() as c:
        assert c.follow_redirects is True
        assert c.headers["user-agent"] == f"LeadGenerator/{__version__}"
        assert c.timeout.connect == 10.0 and c.timeout.read == 20.0


@respx.mock
def test_get_text_sends_user_agent_and_decodes():
    route = respx.get("https://example.com/a").mock(
        return_value=httpx.Response(200, text="Café", headers={"content-type": "text/html"})
    )
    assert http.get_text("https://example.com/a") == "Café"
    assert route.calls[0].request.headers["user-agent"].startswith("LeadGenerator/")


@respx.mock
def test_get_text_respects_max_bytes():
    respx.get("https://example.com/big").mock(return_value=httpx.Response(200, content=b"a" * 5000))
    assert http.get_text("https://example.com/big", max_bytes=100) == "a" * 100


@respx.mock
def test_get_text_follows_redirects_and_raises_on_error():
    respx.get("https://example.com/old").mock(
        return_value=httpx.Response(301, headers={"location": "https://example.com/new"})
    )
    respx.get("https://example.com/new").mock(return_value=httpx.Response(200, text="moved"))
    assert http.get_text("https://example.com/old") == "moved"
    respx.get("https://example.com/missing").mock(return_value=httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        http.get_text("https://example.com/missing")
