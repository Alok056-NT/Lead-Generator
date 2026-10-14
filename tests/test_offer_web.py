"""Offer web sources: HTML to text, the crawler (HTTP mocked with respx) and Google Docs links."""

from __future__ import annotations

import re

import httpx
import pytest
import respx

from leadgen.services import offer_web
from leadgen.services.offer_web import WebError

BASE = "https://www.northwind.example.com"


@pytest.fixture(autouse=True)
def fast_crawl(monkeypatch):
    """No waiting between requests in tests."""
    monkeypatch.setattr(offer_web, "REQUESTS_PER_SECOND", 10_000.0)


def page(body: str, title: str = "Northwind Logistics") -> str:
    return f"<html><head><title>{title}</title></head><body>{body}</body></html>"


def mock_site(pages: dict[str, str | int], base: str = BASE, robots: str | None = None):
    """Register `pages` ({path: html or status code}); every other path answers 404."""
    routes = {}
    if robots is not None:
        routes["/robots.txt"] = respx.get(f"{base}/robots.txt").mock(
            return_value=httpx.Response(200, text=robots)
        )
    for path, content in pages.items():
        if isinstance(content, int):
            response = httpx.Response(content, text="error")
        else:
            response = httpx.Response(200, html=content)
        routes[path] = respx.get(f"{base}{path}").mock(return_value=response)
    respx.get(url__regex=rf"^{re.escape(base)}/.*").mock(return_value=httpx.Response(404))
    return routes


# --- html_to_text -----------------------------------------------------------------------------


def test_html_to_text_drops_noise_keeps_headings():
    html = page(
        """
        <nav><a href="/x">Home</a> Menu item</nav>
        <script>var secret = 1;</script><style>.a{color:red}</style>
        <h1>We train   teams</h1>
        <p>Safety courses for <b>warehouses</b>.</p>
        <ul><li>Forklift</li><li>First aid</li></ul>
        <table><tr><th>Plan</th><td>Basic</td></tr></table>
        <footer>Copyright junk</footer>
        """
    )
    title, text = offer_web.html_to_text(html)
    assert title == "Northwind Logistics"
    lines = text.splitlines()
    assert lines[0] == "## Northwind Logistics"
    assert "## We train teams" in lines
    assert "Safety courses for warehouses." in lines
    assert "- Forklift" in lines and "- First aid" in lines
    assert "Plan | Basic" in lines
    for junk in ("Menu item", "secret", "color:red", "Copyright junk", "Home"):
        assert junk not in text


def test_html_to_text_empty_page():
    assert offer_web.html_to_text("<html><body><script>x()</script></body></html>") == ("", "")


# --- URL checks -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url", ["", "ftp://example.com", "http://localhost:8000", "http://192.168.1.5/", "http://[::1]/"]
)
def test_invalid_start_urls(url):
    with pytest.raises(WebError) as info:
        offer_web.normalize_start_url(url)
    assert info.value.code == "invalid_url"


def test_normalize_adds_scheme_and_drops_fragment():
    assert offer_web.normalize_start_url("northwind.example.com/a#top") == (
        "https://northwind.example.com/a"
    )


@respx.mock
def test_crawl_start_url_redirecting_to_loopback_is_refused():
    respx.get(f"{BASE}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE}/").mock(
        return_value=httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})
    )
    internal = respx.get("http://127.0.0.1/admin").mock(return_value=httpx.Response(200))
    with pytest.raises(WebError) as info:
        offer_web.crawl(BASE)
    assert info.value.code == "invalid_url"
    assert not internal.called


@respx.mock
def test_crawl_skips_subpage_redirecting_to_private_address():
    mock_site({"/": page('<a href="/about">About</a>')}, robots="")
    respx.get(f"{BASE}/about").mock(
        return_value=httpx.Response(301, headers={"location": "http://192.168.0.1/"})
    )
    internal = respx.get("http://192.168.0.1/").mock(return_value=httpx.Response(200))
    pages, _warnings = offer_web.crawl(BASE)
    assert [p.url for p in pages] == [f"{BASE}/"]
    assert not internal.called


@respx.mock
def test_google_doc_redirect_to_local_address_is_refused():
    respx.get(EXPORT).mock(
        return_value=httpx.Response(302, headers={"location": "http://localhost:8080/secret"})
    )
    internal = respx.get("http://localhost:8080/secret").mock(return_value=httpx.Response(200))
    with pytest.raises(WebError) as info:
        offer_web.fetch_google_doc(EXPORT)
    assert info.value.code == "invalid_url"
    assert not internal.called


# --- crawler ----------------------------------------------------------------------------------


@respx.mock
def test_crawl_follows_same_domain_only():
    external = respx.get("https://other.example.org/page").mock(return_value=httpx.Response(200))
    mock_site(
        {
            "/": page(
                '<h1>Home</h1><p>Welcome</p><a href="/about">About</a>'
                '<a href="https://other.example.org/page">Elsewhere</a>'
                '<a href="mailto:a@example.com">Mail</a><a href="/brochure.pdf">PDF</a>'
            ),
            "/about": page("<h1>About</h1><p>We exist.</p>"),
        }
    )
    pages, warnings = offer_web.crawl(BASE + "/")
    assert [p.url for p in pages] == [BASE + "/", BASE + "/about"]
    assert not external.called
    assert warnings == []


@respx.mock
def test_crawl_respects_robots_disallow():
    routes = mock_site(
        {
            "/": page('<p>Home</p><a href="/private/plans">Plans</a><a href="/about">About</a>'),
            "/about": page("<p>About us</p>"),
            "/private/plans": page("<p>Secret plans</p>"),
        },
        robots="User-agent: *\nDisallow: /private\n",
    )
    pages, warnings = offer_web.crawl(BASE + "/")
    urls = [p.url for p in pages]
    assert BASE + "/about" in urls and BASE + "/private/plans" not in urls
    assert not routes["/private/plans"].called
    assert any("robots.txt" in w for w in warnings)


@respx.mock
def test_crawl_blocked_start_url():
    mock_site({"/": page("<p>Home</p>")}, robots="User-agent: *\nDisallow: /\n")
    with pytest.raises(WebError) as info:
        offer_web.crawl(BASE + "/")
    assert info.value.code == "robots_blocked"


@respx.mock
def test_crawl_robots_403_blocks_everything():
    respx.get(f"{BASE}/robots.txt").mock(return_value=httpx.Response(403))
    with pytest.raises(WebError) as info:
        offer_web.crawl(BASE + "/")
    assert info.value.code == "robots_blocked"


@respx.mock
def test_crawl_stops_at_max_pages():
    links = "".join(f'<a href="/p{i}">P{i}</a>' for i in range(10))
    pages = {"/": page(f"<p>Home</p>{links}")}
    pages.update({f"/p{i}": page(f"<p>Page {i} text</p>") for i in range(10)})
    mock_site(pages)
    result, _ = offer_web.crawl(BASE + "/", max_pages=3)
    assert len(result) == 3


@respx.mock
def test_crawl_hard_cap_is_15_pages():
    links = "".join(f'<a href="/p{i}">P{i}</a>' for i in range(30))
    pages = {"/": page(f"<p>Home</p>{links}")}
    pages.update({f"/p{i}": page(f"<p>Page {i} text</p>") for i in range(30)})
    mock_site(pages)
    result, _ = offer_web.crawl(BASE + "/", max_pages=100)
    assert len(result) == 15


@respx.mock
def test_crawl_reads_priority_pages_first():
    mock_site(
        {
            "/": page(
                '<p>Home</p><a href="/blog/news">News</a><a href="/careers">Careers</a>'
                '<a href="/services/training">Training</a><a href="/about">About</a>'
            ),
            "/blog/news": page("<p>News</p>"),
            "/careers": page("<p>Jobs</p>"),
            "/services/training": page("<p>Training</p>"),
            "/about": page("<p>About</p>"),
        }
    )
    result, _ = offer_web.crawl(BASE + "/", max_pages=3)
    assert [p.url.removeprefix(BASE) for p in result] == ["/", "/about", "/services/training"]


@respx.mock
def test_crawl_tries_common_paths_when_page_has_no_links():
    mock_site({"/": page("<p>Home only</p>"), "/about": page("<p>About page</p>")})
    result, _ = offer_web.crawl(BASE + "/")
    assert [p.url.removeprefix(BASE) for p in result] == ["/", "/about"]


@respx.mock
def test_crawl_follows_www_redirect():
    respx.get("https://northwind.example.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://northwind.example.com/").mock(
        return_value=httpx.Response(301, headers={"location": BASE + "/"})
    )
    respx.get(url__regex=r"^https://northwind\.example\.com/.+").mock(
        return_value=httpx.Response(404)
    )
    mock_site({"/": page('<p>Home</p><a href="/about">About</a>'), "/about": page("<p>About</p>")})
    result, _ = offer_web.crawl("northwind.example.com")
    assert [p.url for p in result] == [BASE + "/", BASE + "/about"]


@respx.mock
def test_crawl_start_url_errors():
    mock_site({"/": 404})
    with pytest.raises(WebError) as info:
        offer_web.crawl(BASE + "/")
    assert info.value.code == "http_error" and "404" in info.value.message


@respx.mock
def test_crawl_unreachable_not_html_and_no_text():
    respx.get(f"{BASE}/robots.txt").mock(return_value=httpx.Response(404))
    route = respx.get(f"{BASE}/").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(WebError) as info:
        offer_web.crawl(BASE + "/")
    assert info.value.code == "unreachable"

    route.mock(return_value=httpx.Response(200, content=b"%PDF", headers={"content-type": "x/pdf"}))
    with pytest.raises(WebError) as info:
        offer_web.crawl(BASE + "/")
    assert info.value.code == "not_html"

    route.mock(return_value=httpx.Response(200, html="<html><body><script>app()</script></body>"))
    with pytest.raises(WebError) as info:
        offer_web.crawl(BASE + "/")
    assert info.value.code == "no_text"


@respx.mock
def test_crawl_honours_crawl_delay(monkeypatch):
    rates: list[float] = []

    class Recorder:
        def __init__(self, per_second: float) -> None:
            rates.append(per_second)

        def acquire(self) -> None:
            pass

    monkeypatch.setattr(offer_web, "RateLimiter", Recorder)
    monkeypatch.setattr(offer_web, "REQUESTS_PER_SECOND", 1.0)
    mock_site({"/": page("<p>Home</p>")}, robots="User-agent: *\nCrawl-delay: 4\n")
    offer_web.crawl(BASE + "/", max_pages=1)
    assert rates == [0.25]


@respx.mock
def test_crawl_default_rate_is_one_request_per_second(monkeypatch):
    rates: list[float] = []

    class Recorder:
        def __init__(self, per_second: float) -> None:
            rates.append(per_second)

        def acquire(self) -> None:
            pass

    monkeypatch.setattr(offer_web, "RateLimiter", Recorder)
    monkeypatch.setattr(offer_web, "REQUESTS_PER_SECOND", 1.0)
    mock_site({"/": page("<p>Home</p>")})
    offer_web.crawl(BASE + "/", max_pages=1)
    assert rates == [1.0]


@respx.mock
def test_crawl_time_limit(monkeypatch):
    monkeypatch.setattr(offer_web, "DEADLINE_SECONDS", -1.0)
    mock_site({"/": page('<p>Home</p><a href="/about">About</a>'), "/about": page("<p>About</p>")})
    result, warnings = offer_web.crawl(BASE + "/")
    assert len(result) == 1
    assert any("time limit" in w for w in warnings)


# --- Google Docs ------------------------------------------------------------------------------

DOC_ID = "1AbC_dEf-0123456789"
EXPORT = f"https://docs.google.com/document/d/{DOC_ID}/export?format=txt"


@pytest.mark.parametrize(
    "url",
    [
        f"https://docs.google.com/document/d/{DOC_ID}/edit",
        f"https://docs.google.com/document/d/{DOC_ID}/edit?usp=sharing",
        f"https://docs.google.com/document/u/0/d/{DOC_ID}/view",
        f"docs.google.com/document/d/{DOC_ID}",
    ],
)
def test_google_doc_url_rewriting(url):
    assert offer_web.google_doc_export_url(url) == EXPORT


def test_google_other_links():
    assert offer_web.google_doc_export_url("https://www.northwind.example.com/") is None
    for url in (
        "https://docs.google.com/spreadsheets/d/abc/edit",
        "https://drive.google.com/file/d/abc/view",
    ):
        with pytest.raises(WebError) as info:
            offer_web.google_doc_export_url(url)
        assert info.value.code == "unsupported_google_link"


@respx.mock
def test_fetch_google_doc_public():
    respx.get(EXPORT).mock(
        return_value=httpx.Response(
            200, content="﻿Title\r\n\r\nWe train teams.  \r\n".encode(),
            headers={"content-type": "text/plain; charset=utf-8"},
        )
    )
    assert offer_web.fetch_google_doc(EXPORT) == "Title\n\nWe train teams."


@respx.mock
@pytest.mark.parametrize("status", [401, 403, 404])
def test_fetch_google_doc_private_status(status):
    respx.get(EXPORT).mock(return_value=httpx.Response(status, html="<html>denied</html>"))
    with pytest.raises(WebError) as info:
        offer_web.fetch_google_doc(EXPORT)
    assert info.value.code == "private_document"


@respx.mock
def test_fetch_google_doc_login_redirect_and_html_page():
    login = "https://accounts.google.com/ServiceLogin?continue=x"
    respx.get(EXPORT).mock(return_value=httpx.Response(302, headers={"location": login}))
    respx.get(login).mock(return_value=httpx.Response(200, html="<html>Sign in</html>"))
    with pytest.raises(WebError) as info:
        offer_web.fetch_google_doc(EXPORT)
    assert info.value.code == "private_document"

    respx.get(EXPORT).mock(return_value=httpx.Response(200, html="<html>Sign in</html>"))
    with pytest.raises(WebError) as info:
        offer_web.fetch_google_doc(EXPORT)
    assert info.value.code == "private_document"


@respx.mock
def test_fetch_google_doc_errors():
    route = respx.get(EXPORT).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(WebError) as info:
        offer_web.fetch_google_doc(EXPORT)
    assert info.value.code == "unreachable"
    route.mock(return_value=httpx.Response(500, text="oops"))
    with pytest.raises(WebError) as info:
        offer_web.fetch_google_doc(EXPORT)
    assert info.value.code == "http_error"
    blank = httpx.Response(200, text="  \n", headers={"content-type": "text/plain"})
    route.mock(return_value=blank)
    with pytest.raises(WebError) as info:
        offer_web.fetch_google_doc(EXPORT)
    assert info.value.code == "no_text"
