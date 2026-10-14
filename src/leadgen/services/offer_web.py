"""Web sources for offer intake: HTML to text, a polite same-domain crawler, public Google Docs.

The crawler is a synchronous port of Harvey's `FallbackCrawler` (see header below). Changes from
Harvey: robots.txt is honoured, requests are limited to 1 per second, priority pages are
followed first by *score* (links found on the page), and the whole crawl stops at a time limit
so a tool call never runs for minutes.
"""

# Adapted from Harvey (MIT, Ethan Rogers): reference/harvey/trainer.py (FallbackCrawler)

from __future__ import annotations

import heapq
import ipaddress
import itertools
import logging
import re
import socket
import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, NavigableString

from leadgen.core import http
from leadgen.core.text import normalize_whitespace
from leadgen.providers.base import RateLimiter

logger = logging.getLogger("leadgen.offer_web")

MAX_PAGES = 15
REQUESTS_PER_SECOND = 1.0
MAX_CRAWL_DELAY = 10.0  # we never wait longer than this per request, whatever robots.txt asks
DEADLINE_SECONDS = 50.0  # stay well under the ~60 s a tool call should take
MAX_PAGE_BYTES = 2_000_000
MAX_PAGE_CHARS = 50_000

# Pages worth reading first, in order. Also tried as guesses when links run out.
PRIORITY_PATHS = [
    "/about",
    "/about-us",
    "/services",
    "/solutions",
    "/industries",
    "/case-studies",
    "/faq",
    "/contact",
    "/products",
    "/product",
    "/pricing",
    "/features",
    "/how-it-works",
    "/why-us",
    "/customers",
    "/testimonials",
    "/team",
    "/our-team",
    "/resources",
]
_SKIP_EXTENSIONS = (
    ".pdf", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".css", ".js",
    ".xml", ".json", ".mp4", ".mp3", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
)  # fmt: skip
_DROP_TAGS = ("script", "style", "noscript", "iframe", "svg", "template", "nav", "footer")
_BLOCK_TAGS = (
    "p", "div", "section", "article", "main", "header", "aside", "ul", "ol", "dl", "dd", "dt",
    "tr", "table", "blockquote", "figcaption", "pre", "form", "fieldset",
)  # fmt: skip


class WebError(Exception):
    """A web source could not be read. `code` is stable text for the AI; `message` is plain."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class CrawledPage:
    """One page that produced text."""

    url: str
    text: str


@dataclass
class _Fetched:
    status: int  # 0 = network error
    url: str
    content_type: str
    html: str


@dataclass
class _Frontier:
    heap: list[tuple[float, int, str]] = field(default_factory=list)
    queued: set[str] = field(default_factory=set)
    counter: itertools.count = field(default_factory=itertools.count)


# --- HTML to text ---------------------------------------------------------------------------


def html_to_text(html: str) -> tuple[str, str]:
    """Return `(title, text)` from an HTML page.

    Navigation, footer, scripts and styles are dropped. Headings become `## ` lines and list
    items `- ` lines; every other block is one line. Capped at `MAX_PAGE_CHARS`.
    """
    soup = BeautifulSoup(html, "html.parser")
    title = normalize_whitespace(soup.title.get_text(" ")) if soup.title else ""
    for tag in soup(["head", *_DROP_TAGS]):
        tag.decompose()
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        heading = normalize_whitespace(tag.get_text(" "))
        tag.replace_with(NavigableString(f"\n## {heading}\n" if heading else "\n"))
    for tag in soup.find_all("br"):
        tag.replace_with(NavigableString("\n"))
    for tag in soup.find_all("li"):
        tag.insert_before(NavigableString("\n- "))
        tag.insert_after(NavigableString("\n"))
    for tag in soup.find_all(["td", "th"]):
        tag.insert_after(NavigableString(" | "))
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_before(NavigableString("\n"))
        tag.insert_after(NavigableString("\n"))
    lines: list[str] = []
    for raw in soup.get_text().splitlines():
        line = normalize_whitespace(raw).strip(" |")
        if not line or line == "-" or (lines and lines[-1] == line):
            continue
        lines.append(line)
    if title and f"## {title}" not in lines:
        lines.insert(0, f"## {title}")
    return title, "\n".join(lines)[:MAX_PAGE_CHARS]


# --- URL helpers ----------------------------------------------------------------------------


def _check_public_url(url: str) -> None:
    """Raise `WebError("invalid_url")` unless `url` is http(s) and its host is public-looking.

    Checks the address as written only: `localhost`, `.local`/`.internal` names and IP literals
    that are not global are refused. A trailing dot on the host is ignored, and old-style IPv4
    forms such as `127.1`, `2130706433` and `0x7f000001` are read the way the OS resolver reads
    them. DNS is not resolved, so a hostname that points at a private address is not caught.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.endswith("."):
        host = host[:-1]
    if parsed.scheme not in ("http", "https") or not host:
        raise WebError("invalid_url", "That does not look like a web address (use https://...).")
    local = host == "localhost" or host.endswith((".local", ".internal", ".localhost"))
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            ip = ipaddress.ip_address(socket.inet_aton(host))
        except OSError:
            ip = None
    if ip is not None:
        local = local or not ip.is_global
    if local:
        raise WebError("invalid_url", "Local and private network addresses cannot be read.")


def _guard_request(request: httpx.Request) -> None:
    """httpx request hook: applies the URL rule to the first request and every redirect hop."""
    _check_public_url(str(request.url))


def _client() -> httpx.Client:
    """The shared HTTP client with the redirect guard attached."""
    return http.client(request_hook=_guard_request)


def normalize_start_url(url: str) -> str:
    """Add `https://` if missing and check the URL is a public-looking http(s) address."""
    url = (url or "").strip()
    if url and "://" not in url:
        url = f"https://{url}"
    _check_public_url(url)
    return url.split("#", 1)[0]


def _site_key(host: str) -> str:
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def _page_key(url: str) -> str:
    parsed = urlparse(url)
    return f"{_site_key(parsed.netloc)}{parsed.path.rstrip('/') or '/'}"


def _clean_link(href: str, base: str) -> str | None:
    href = href.strip()
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "sms:")):
        return None
    parsed = urlparse(urljoin(base, href))
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    if parsed.path.lower().endswith(_SKIP_EXTENSIONS):
        return None
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}"


def _score(url: str) -> float:
    """Lower is better: priority paths in list order, then other pages by depth."""
    path = urlparse(url).path.lower().rstrip("/") or "/"
    for index, wanted in enumerate(PRIORITY_PATHS):
        if path == wanted or path.startswith(wanted + "/"):
            return float(index)
    return 1000.0 + path.count("/")


# --- fetching -------------------------------------------------------------------------------


def _with_path(url: str) -> str:
    parsed = urlparse(url)
    return url if parsed.path else f"{parsed.scheme}://{parsed.netloc}/"


def _fetch(client: httpx.Client, url: str) -> _Fetched:
    try:
        with client.stream("GET", url) as response:
            ctype = response.headers.get("content-type", "")
            status = response.status_code
            final = _with_path(str(response.url))
            if status >= 400 or (ctype and "html" not in ctype.lower()):
                return _Fetched(status, final, ctype, "")
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                chunks.append(chunk[: MAX_PAGE_BYTES - size])
                size += len(chunks[-1])
                if size >= MAX_PAGE_BYTES:
                    break
            encoding = response.encoding or "utf-8"
    except httpx.HTTPError as exc:
        logger.debug("fetch failed for %s: %s", url, type(exc).__name__)
        return _Fetched(0, url, "", "")
    try:
        html = b"".join(chunks).decode(encoding, errors="replace")
    except LookupError:
        html = b"".join(chunks).decode("utf-8", errors="replace")
    return _Fetched(status, final, ctype, html)


def _load_robots(client: httpx.Client, base: str) -> urllib.robotparser.RobotFileParser:
    """Fetch and parse robots.txt. 401/403 = everything disallowed; other failures = allowed."""
    parser = urllib.robotparser.RobotFileParser()
    try:
        response = client.get(f"{base}/robots.txt")
    except httpx.HTTPError:
        parser.parse([])
        return parser
    if response.status_code in (401, 403):
        parser.disallow_all = True
    elif response.status_code == 200:
        parser.parse(response.text[:500_000].splitlines())
    else:
        parser.parse([])
    return parser


# --- crawler --------------------------------------------------------------------------------


def crawl(start_url: str, max_pages: int = MAX_PAGES) -> tuple[list[CrawledPage], list[str]]:
    """Crawl `start_url` and same-domain pages; return `(pages, warnings)`.

    Raises `WebError` if the start page cannot be used (`invalid_url`, `robots_blocked`,
    `unreachable`, `http_error`, `not_html`, `no_text`). At most `max_pages` pages with text
    are returned (hard cap 15), at most 1 request per second, and the crawl stops after
    `DEADLINE_SECONDS`.
    """
    max_pages = max(1, min(int(max_pages), MAX_PAGES))
    start = normalize_start_url(start_url)
    parsed = urlparse(start)
    base = f"{parsed.scheme}://{parsed.netloc}"
    warnings: list[str] = []
    started = time.monotonic()

    with _client() as client:
        robots = _load_robots(client, base)
        agent = http.USER_AGENT
        if not robots.can_fetch(agent, start):
            raise WebError("robots_blocked", "This website asks automated tools not to read it.")
        interval = 1.0 / REQUESTS_PER_SECOND
        delay = robots.crawl_delay(agent)
        if delay:
            interval = min(max(float(delay), interval), MAX_CRAWL_DELAY)
        limiter = RateLimiter(1.0 / interval)

        limiter.acquire()
        first = _fetch(client, start)
        if first.status == 0:
            raise WebError("unreachable", "The website could not be reached.")
        if first.status >= 400:
            raise WebError(
                "http_error", f"The website answered with an error (HTTP {first.status})."
            )
        if not first.html:
            raise WebError("not_html", "That address is not a web page.")
        allowed_hosts = {_site_key(parsed.netloc), _site_key(urlparse(first.url).netloc)}
        title, text = html_to_text(first.html)
        if not text:
            raise WebError("no_text", "The page has no readable text (it may need JavaScript).")

        pages = [CrawledPage(first.url, text)]
        frontier = _Frontier()
        frontier.queued.add(_page_key(start))
        frontier.queued.add(_page_key(first.url))
        _enqueue_links(frontier, first.html, first.url, allowed_hosts)

        requests_left = max_pages * 2
        skipped_robots = 0
        guessed = False
        while len(pages) < max_pages and requests_left > 0:
            if time.monotonic() - started > DEADLINE_SECONDS:
                warnings.append("Stopped early (time limit); some pages were not read.")
                break
            if not frontier.heap:
                if guessed:
                    break
                guessed = True
                for path in PRIORITY_PATHS:
                    _push(frontier, f"{base}{path}")
                continue
            _score_value, _order, url = heapq.heappop(frontier.heap)
            if not robots.can_fetch(agent, url):
                skipped_robots += 1
                continue
            limiter.acquire()
            requests_left -= 1
            try:
                result = _fetch(client, url)
            except WebError:
                continue  # redirected to a local/private address: skip this page
            if result.status != 200 or not result.html:
                continue
            if _site_key(urlparse(result.url).netloc) not in allowed_hosts:
                continue  # redirected to another site
            _title, page_text = html_to_text(result.html)
            if not page_text:
                continue
            pages.append(CrawledPage(result.url, page_text))
            _enqueue_links(frontier, result.html, result.url, allowed_hosts)

    if skipped_robots:
        warnings.append(f"Skipped {skipped_robots} page(s) that robots.txt does not allow.")
    return pages, warnings


def _push(frontier: _Frontier, url: str) -> None:
    key = _page_key(url)
    if key in frontier.queued:
        return
    frontier.queued.add(key)
    heapq.heappush(frontier.heap, (_score(url), next(frontier.counter), url))


def _enqueue_links(frontier: _Frontier, html: str, page_url: str, hosts: set[str]) -> None:
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        link = _clean_link(str(anchor["href"]), page_url)
        if link and _site_key(urlparse(link).netloc) in hosts:
            _push(frontier, link)


# --- Google Docs ----------------------------------------------------------------------------

_GOOGLE_HOSTS = ("docs.google.com", "drive.google.com")
_GDOC_PATH_RE = re.compile(r"^/document/(?:u/\d+/)?d/([A-Za-z0-9_-]+)")


def google_doc_export_url(url: str) -> str | None:
    """The plain-text export URL for a Google Doc link, else None.

    `https://docs.google.com/document/d/<id>/edit` -> `.../document/d/<id>/export?format=txt`.
    Raises `WebError("unsupported_google_link")` for other Google Docs/Drive links (Sheets,
    Slides, Drive files), which cannot be read as text this way.
    """
    parsed = urlparse(url.strip() if "://" in url else f"https://{url.strip()}")
    host = (parsed.hostname or "").lower()
    if host not in _GOOGLE_HOSTS:
        return None
    match = _GDOC_PATH_RE.match(parsed.path)
    if host == "docs.google.com" and match:
        return f"https://docs.google.com/document/d/{match.group(1)}/export?format=txt"
    raise WebError(
        "unsupported_google_link",
        "Only Google Docs documents can be read from a link. Download this file instead.",
    )


def fetch_google_doc(export_url: str) -> str:
    """Download a public Google Doc as text. Raises `WebError` if it is private or empty."""
    private = WebError(
        "private_document",
        "This Google Doc is private (or does not exist), so it cannot be read from the link.",
    )
    try:
        with _client() as client:
            response = client.get(export_url)
    except httpx.HTTPError:
        raise WebError("unreachable", "Google Docs could not be reached.") from None
    host = (urlparse(str(response.url)).hostname or "").lower()
    ctype = response.headers.get("content-type", "").lower()
    if response.status_code in (401, 403, 404) or host.startswith("accounts.google."):
        raise private
    if response.status_code >= 400:
        raise WebError("http_error", f"Google Docs answered with HTTP {response.status_code}.")
    if "html" in ctype or (ctype and "text" not in ctype):
        raise private  # a sign-in page instead of the document text
    text = response.text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not text:
        raise WebError("no_text", "The Google Doc is empty.")
    return text
