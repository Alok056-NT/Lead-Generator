# Adapted from Harvey (MIT, Ethan Rogers): reference/harvey/integrations/email_finder.py
"""Email finder: one best-guess work email per confirmed person, graded honestly.

Flow per person (PLAN section 12): MX lookup (google / microsoft / gateway / other) -> the
domain's email pattern (cache, known examples, Hunter, the company website, default
`{first}.{last}`) -> one candidate address -> verify it (Reoon, ZeroBounce, Hunter, then an SMTP
probe only for small self-hosted mail servers) -> status `verified`, `risky` (catch-all domain),
`guess` (nothing could check it), `invalid` or `unknown`. Role addresses (info@, hr@ ...) and
suppressed addresses are never stored. Long runs are chunked (`find_emails` returns a
`next_cursor`). Callers must run `db.init_db()` first.
"""

from __future__ import annotations

import html
import json
import logging
import random
import re
import smtplib
import time
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote

import dns.exception
import dns.resolver
import httpx

from leadgen import db
from leadgen.core.config import load_settings
from leadgen.providers import registry
from leadgen.providers.base import ProviderError, RateLimiter
from leadgen.providers.hunter import HunterProvider
from leadgen.providers.reoon import ReoonProvider
from leadgen.providers.zerobounce import ZeroBounceProvider
from leadgen.services import budget, offer_web
from leadgen.services import campaign as campaign_mod

logger = logging.getLogger(__name__)

STATUSES = ("verified", "risky", "guess", "invalid", "unknown")
DEFAULT_PATTERN = "{first}.{last}"
# Pattern words are Hunter's tokens: {first}, {last}, {f} (first initial), {l} (last initial).
PATTERNS = (
    "{first}.{last}",
    "{first}{last}",
    "{f}{last}",
    "{first}",
    "{first}_{last}",
    "{last}.{first}",
    "{f}.{last}",
    "{first}{l}",
    "{last}",
    "{first}-{last}",
    "{last}{f}",
)
ROLE_LOCALS = frozenset(
    {
        "info", "hr", "careers", "career", "admin", "administrator", "contact", "hello", "sales",
        "support", "office", "enquiries", "enquiry", "inquiries", "inquiry", "mail", "jobs",
        "job", "recruitment", "recruiting", "hiring", "talent", "team", "marketing", "press",
        "media", "billing", "accounts", "accounting", "finance", "help", "service", "services",
        "noreply", "no-reply", "donotreply", "webmaster", "postmaster", "hostmaster", "abuse",
        "security", "privacy", "legal", "general", "reception", "customerservice", "hi", "hey",
        "welcome", "feedback", "orders", "booking", "bookings", "operations", "ops",
    }
)  # fmt: skip
GATEWAY_WORDS = (
    "pphosted", "mimecast", "barracuda", "proofpoint", "messagelabs", "cisco", "fortinet",
    "trendmicro",
)  # fmt: skip
SITE_PATHS = ("", "/contact", "/contact-us", "/about", "/about-us", "/team", "/people")
SITE_MAX_PAGES = 5  # pages asked for per domain (the first five of SITE_PATHS)
PAGE_SIZE_MAX = 25
_DEFERRED = "deferred"
CALL_TIME_LIMIT_S = 25.0  # no new person is started after this many seconds of one call
VERIFY_DEADLINE_S = 30.0  # no new verifier call is started after this (a call must end < 60 s)
HUNTER_NONE_DAYS = 30  # Hunter is not asked again about a domain it had no pattern for
HTTP_FAILURES_BEFORE_SKIP = 2  # a provider failing this often in one call is skipped for it
SMTP_TIMEOUT_S = 10
SMTP_PROBE = True  # the SMTP RCPT probe (only ever used for `other` mail servers)
REAL_SOURCES = ("known_email", "hunter", "website", "ai_search")  # patterns seen on real addresses

_mx_cache: dict[str, tuple[str | None, bool]] = {}
_site_cache: dict[str, list[tuple[str, str]]] = {}
_site_limiter = RateLimiter(1.0)

_SPECIAL = str.maketrans(
    {"ß": "ss", "ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe", "đ": "d",
     "Đ": "d", "ł": "l", "Ł": "l", "þ": "th", "ð": "d", "ı": "i"}
)  # fmt: skip
_TOKEN_RE = re.compile(r"\{(first|last|f|l)\}")
_POLICY_CODE_RE = re.compile(rb"\s*5\.7\.\d+")
_MAILTO_RE = re.compile(r"mailto:([^\"'<>\s?#]+)", re.IGNORECASE)
_ADDRESS_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


class EmailError(Exception):
    """An email request failed. `code` is stable text; `hint` tells the AI what to do.

    `response` may carry a ready-made structured reply (needs_key, budget_reached, ...).
    """

    def __init__(
        self, code: str, message: str, hint: str = "", response: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.response = response

    def as_response(self) -> dict[str, Any]:
        """The reply to give the AI."""
        if self.response is not None:
            return self.response
        return {
            "ok": False,
            "error": {"code": self.code, "message": self.message, "hint": self.hint},
        }


# --- names and patterns ---------------------------------------------------------------------


def _letters(text: str) -> str:
    """Lower-case ASCII letters only: accents removed (NFKD), ß/ø/æ... transliterated."""
    folded = unicodedata.normalize("NFKD", (text or "").translate(_SPECIAL))
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"[^a-z]", "", folded.lower())


def _first_part(first: str) -> str:
    for token in (first or "").split():
        cleaned = _letters(token)
        if cleaned:
            return cleaned
    return ""


def _last_parts(last: str) -> tuple[str, str]:
    """`(main, joined)`: the last word of the surname, and all its words run together."""
    tokens = [t for t in (_letters(w) for w in (last or "").split()) if t]
    if not tokens:
        return "", ""
    return tokens[-1], "".join(tokens)


def clean_domain(domain: str) -> str:
    """Lower-case host without scheme, path or `www.`."""
    value = (domain or "").strip().lower()
    value = re.sub(r"^[a-z]+://", "", value).split("/")[0].split("?")[0].split("#")[0]
    value = value.rsplit("@", 1)[-1].split(":")[0].rstrip(".")
    return value[4:] if value.startswith("www.") else value


def is_pattern(pattern: str) -> bool:
    """True for a pattern made only of `{first} {last} {f} {l}` and `.`, `_`, `-`."""
    return bool(
        pattern
        and _TOKEN_RE.search(pattern)
        and re.fullmatch(r"(?:\{(?:first|last|f|l)\}|[._-])+", pattern)
    )


def _render(pattern: str, first: str, last: str) -> str:
    values = {"first": first, "last": last, "f": first[:1], "l": last[:1]}
    return _TOKEN_RE.sub(lambda m: values[m.group(1)], pattern)


def build_email(
    pattern: str, first: str, last: str, domain: str, joined_last: bool = False
) -> str:
    """The address for `pattern`, or "" when it cannot be built.

    Names are reduced to plain a-z (accents removed, hyphens and apostrophes dropped). A
    multi-word surname uses its last word for `{last}`; `joined_last=True` joins all its words.
    """
    if not is_pattern(pattern):
        return ""
    first_part = _first_part(first)
    main, joined = _last_parts(last)
    host = clean_domain(domain)
    surname = joined if joined_last else main
    if not first_part or not surname or not host:
        return ""
    return f"{_render(pattern, first_part, surname)}@{host}"


def candidate_emails(
    pattern: str, first: str, last: str, domain: str, from_example: bool = False
) -> list[str]:
    """The address to try, plus the joined-surname form when the surname has several words and
    the pattern came from a real example (otherwise just one address)."""
    primary = build_email(pattern, first, last, domain)
    if not primary:
        return []
    out = [primary]
    if from_example:
        joined = build_email(pattern, first, last, domain, joined_last=True)
        if joined and joined != primary:
            out.append(joined)
    return out


def infer_pattern(email: str, first: str, last: str) -> str | None:
    """Which pattern produced the known address `email` for this person, or None."""
    local = (email or "").strip().lower().split("@", 1)[0]
    first_part = _first_part(first)
    main, joined = _last_parts(last)
    if "@" not in (email or "") or not first_part or not main:
        return None
    for surname in dict.fromkeys((main, joined)):
        for pattern in PATTERNS:
            if _render(pattern, first_part, surname) == local:
                return pattern
    return None


def infer_pattern_from_local(local: str) -> str | None:
    """Best pattern for an address when the person's name is unknown (a site contact address).

    Only the shapes that are unambiguous: `a.b`, `a_b`, `a-b` (two words) and `x.surname`.
    A bare word could be `{first}` or `{last}`, so it gives None.
    """
    local = (local or "").strip().lower()
    for sep, pattern in ((".", "{first}.{last}"), ("_", "{first}_{last}"), ("-", "{first}-{last}")):
        parts = local.split(sep)
        if len(parts) == 2 and all(p.isalpha() and p.isascii() for p in parts):
            if sep == "." and len(parts[0]) == 1 and len(parts[1]) > 1:
                return "{f}.{last}"
            if len(parts[0]) > 1 and len(parts[1]) > 1:
                return pattern
    return None


def is_role_address(email: str) -> bool:
    """True for info@, hr@, careers@, admin@, contact@, hello@, sales@ and similar."""
    local = (email or "").strip().lower().split("@", 1)[0]
    return local in ROLE_LOCALS or re.sub(r"[._-]", "", local) in ROLE_LOCALS


# --- mail servers ---------------------------------------------------------------------------


def classify_mx(host: str) -> str:
    """`google`, `microsoft`, `gateway` (security filters) or `other` for a mail host name."""
    h = (host or "").lower()
    if "google" in h or "googlemail" in h or "aspmx.l" in h:
        return "google"
    if "outlook" in h or "microsoft" in h:
        return "microsoft"
    if any(word in h for word in GATEWAY_WORDS):
        return "gateway"
    return "other"


def _mx_lookup(domain: str) -> tuple[str | None, bool]:
    """`(primary MX host or None, definitive)`; transient DNS failures are not cached."""
    domain = clean_domain(domain)
    if not domain:
        return None, True
    if domain in _mx_cache:
        return _mx_cache[domain]
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5.0)
        records = sorted(answers, key=lambda r: r.preference)
        host = str(records[0].exchange).rstrip(".").lower() if records else ""
        result: tuple[str | None, bool] = (host or None, True)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        result = (None, True)
    except dns.exception.DNSException as exc:
        logger.debug("MX lookup failed for %s: %s", domain, type(exc).__name__)
        return None, False
    _mx_cache[domain] = result
    return result


def mx_type(domain: str) -> str | None:
    """`google`, `microsoft`, `gateway` or `other` from the domain's MX record; None when it has
    none (or DNS failed). Cached per process."""
    host, _definitive = _mx_lookup(domain)
    return classify_mx(host) if host else None


# --- settings storage (pattern and catch-all cache) -----------------------------------------


def _setting(key: str) -> str | None:
    with db.session() as conn:
        rows = db.query(conn, "SELECT value FROM settings WHERE key = ?", (key,))
    return rows[0]["value"] if rows else None


def _set_setting(key: str, value: str) -> None:
    stamp = db.now()
    with db.session() as conn:
        conn.execute(
            "INSERT INTO settings (created_at, updated_at, key, value) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (stamp, stamp, key, value),
        )


def _cached_pattern(domain: str) -> tuple[str, str] | None:
    raw = _setting(f"pattern:{domain}")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    pattern = data.get("pattern") if isinstance(data, dict) else None
    if not isinstance(pattern, str) or not is_pattern(pattern):
        return None
    return pattern, str(data.get("source") or "cache")


def _cache_pattern(domain: str, pattern: str, source: str, confidence: float) -> None:
    _set_setting(
        f"pattern:{domain}",
        json.dumps({"pattern": pattern, "source": source, "confidence": round(confidence, 2)}),
    )


def _hunter_none_fresh(domain: str) -> bool:
    """True when Hunter recently had no pattern for `domain` (so it is not asked again)."""
    raw = _setting(f"hunter_none:{domain}")
    if not raw:
        return False
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return datetime.now(UTC) - stamp < timedelta(days=HUNTER_NONE_DAYS)


def _catch_all_cached(domain: str) -> bool | None:
    value = _setting(f"catchall:{domain}")
    return None if value is None else value == "1"


def _cache_catch_all(domain: str, value: bool) -> None:
    _set_setting(f"catchall:{domain}", "1" if value else "0")


# --- run state ------------------------------------------------------------------------------


@dataclass
class _Run:
    """What one call learned about providers: who is out of credits, and why."""

    dead: set[str] = field(default_factory=set)
    issues: dict[str, dict[str, Any]] = field(default_factory=dict)
    hunter_blocked: dict[str, Any] | None = None
    spent: Counter = field(default_factory=Counter)
    http_failures: Counter = field(default_factory=Counter)
    deadline: float | None = None  # time.monotonic() after which no new verifier call starts
    # (None = no limit; `find_emails` leaves it unset for the first person of a call)

    def failed(self, provider_key: str, exc: ProviderError) -> None:
        self.issues[provider_key] = registry.error_response(provider_key, exc)
        if exc.code in ("quota", "auth", "not_configured"):
            self.dead.add(provider_key)
        elif exc.code == "http":
            self.http_failures[provider_key] += 1
            if self.http_failures[provider_key] >= HTTP_FAILURES_BEFORE_SKIP:
                self.dead.add(provider_key)  # e.g. it keeps timing out: stop waiting for it

    def out_of_time(self) -> bool:
        """True once the call's verification deadline has passed."""
        return self.deadline is not None and time.monotonic() > self.deadline


@dataclass
class Verification:
    """The outcome of `verify`: `status` (verified/risky/guess/invalid), who decided it
    (`verifier`: reoon, zerobounce, hunter, smtp, catch_all_cache or "" for a plain guess) and,
    when a campaign budget stopped the check, the `blocked` reply."""

    status: str
    verifier: str = ""
    raw_status: str = ""
    catch_all: bool = False
    blocked: dict[str, Any] | None = None
    timed_out: bool = False  # the call ran out of time before a check could be made


# --- pattern derivation ---------------------------------------------------------------------


def _address_parts(item: Any) -> tuple[str, str, str]:
    """`(email, first, last)` from a bare address or an `(email, first, last)` tuple."""
    if isinstance(item, str):
        return item.strip().lower(), "", ""
    email, first, last = (list(item) + ["", ""])[:3]
    return str(email).strip().lower(), str(first or ""), str(last or "")


def _pattern_from_examples(domain: str, examples: Iterable[Any]) -> str | None:
    """The most common pattern among real addresses of `domain` (role addresses ignored)."""
    votes: Counter[str] = Counter()
    for item in examples:
        email, first, last = _address_parts(item)
        if "@" not in email or clean_domain(email) != domain or is_role_address(email):
            continue
        pattern = infer_pattern(email, first, last) if first and last else None
        pattern = pattern or infer_pattern_from_local(email.split("@", 1)[0])
        if pattern:
            votes[pattern] += 1
    return votes.most_common(1)[0][0] if votes else None


def _extract_addresses(page_html: str, domain: str) -> list[str]:
    text = unquote(html.unescape(page_html))
    found: list[str] = []
    for match in _MAILTO_RE.findall(text):
        found.extend(_ADDRESS_RE.findall(match))
    found.extend(_ADDRESS_RE.findall(text))
    out = []
    for address in found:
        address = address.strip(".").lower()
        if clean_domain(address) == domain and address not in out:
            out.append(address)
    return out


def addresses_on_domain(text: str, domain: str) -> list[str]:
    """Email addresses written in `text` (a search title or snippet) that belong to `domain`."""
    return _extract_addresses(text or "", clean_domain(domain))


def known_pattern(domain: str) -> tuple[str, str] | None:
    """The saved `(pattern, source)` for a domain, or None."""
    return _cached_pattern(clean_domain(domain))


def scrape_site_sources(domain: str, max_pages: int = SITE_MAX_PAGES) -> list[tuple[str, str]]:
    """`(email, page url)` for addresses on the company's own domain, found on its home page,
    `/contact`, `/contact-us`, `/about`, `/about-us`, `/team` and `/people` (the first
    `max_pages` of them are asked for).

    Free, no key. Uses the T05 URL guard (public addresses only, also on redirects), honours
    robots.txt and asks for at most one page per second. Cached per process (by domain); every
    failure gives `[]`. Each address appears once, with the first page that showed it.
    """
    domain = clean_domain(domain)
    if not domain:
        return []
    if domain in _site_cache:
        return _site_cache[domain]
    base_url = f"https://{domain}"
    found: list[tuple[str, str]] = []
    try:
        offer_web._check_public_url(base_url)
        with offer_web._client() as client:
            _site_limiter.acquire()
            robots = offer_web._load_robots(client, base_url)
            agent = offer_web.http.USER_AGENT
            for path in SITE_PATHS[: max(1, int(max_pages))]:
                url = base_url + (path or "/")
                if not robots.can_fetch(agent, url):
                    continue
                _site_limiter.acquire()
                page = offer_web._fetch(client, url)
                if page.status == 200 and page.html:
                    seen = {a for a, _u in found}
                    found.extend(
                        (a, url) for a in _extract_addresses(page.html, domain) if a not in seen
                    )
    except (offer_web.WebError, httpx.HTTPError, ValueError) as exc:
        logger.debug("site scrape failed for %s: %s", domain, type(exc).__name__)
    _site_cache[domain] = found
    return found


def scrape_site_emails(domain: str, max_pages: int = SITE_MAX_PAGES) -> list[str]:
    """Addresses on the company's own domain found on its public contact pages (see
    `scrape_site_sources`, which also returns the page of each address). Role addresses are
    included here; pattern inference ignores them."""
    return [address for address, _url in scrape_site_sources(domain, max_pages)]


def learn_pattern_from_examples(
    domain: str, emails: Iterable[Any], source_urls: Sequence[str] | None = None
) -> dict[str, Any]:
    """Save the domain's email pattern from real addresses the AI found by web search.

    `emails` are bare addresses or `(email, first, last)` tuples; only addresses on `domain`
    count, and role addresses (info@, hr@ ...) are ignored for the pattern (they only show that
    the domain receives mail). The pattern is saved like a Hunter one (source `ai_search`) unless
    the domain already has a saved pattern from a real source. Returns `pattern`, `saved`,
    `learned` (from these examples), `examples_used`, `role_addresses` and `source_urls`.
    `pattern` is the one now in effect for the domain.
    """
    domain = clean_domain(domain)
    addresses = [_address_parts(e) for e in emails]
    on_domain = [a for a in addresses if "@" in a[0] and clean_domain(a[0]) == domain]
    people = [a for a in on_domain if not is_role_address(a[0])]
    pattern = _pattern_from_examples(domain, people)
    cached = _cached_pattern(domain)
    saved = False
    if pattern and (cached is None or cached[1] not in REAL_SOURCES or cached[1] == "ai_search"):
        _cache_pattern(domain, pattern, "ai_search", 0.8)
        saved = True
    return {
        "domain": domain,
        "pattern": pattern if saved else (cached[0] if cached else pattern),
        "learned": pattern,
        "saved": saved,
        "examples_used": len(people),
        "role_addresses": len(on_domain) - len(people),
        "source_urls": list(dict.fromkeys(source_urls or []))[:10],
    }


@dataclass
class _Derived:
    pattern: str
    source: str
    confidence: float


def _budget_gate(
    campaign_id: int | None,
    budget_map: Mapping[str, Any] | None,
    kind: str,
    needed: float,
    confirm: bool,
) -> dict[str, Any] | None:
    if campaign_id is None:
        return None
    return budget.check(campaign_id, budget_map, kind, needed, confirm)


def _derive(
    domain: str,
    known_emails: Sequence[Any] | None,
    hunter: Any,
    scrape: bool,
    campaign_id: int | None,
    budget_map: Mapping[str, Any] | None,
    confirm: bool,
    run: _Run,
) -> _Derived:
    cached = _cached_pattern(domain)
    if cached:
        return _Derived(cached[0], cached[1], 0.6)
    known = _pattern_from_examples(domain, known_emails or ())
    if known:
        _cache_pattern(domain, known, "known_email", 0.9)
        return _Derived(known, "known_email", 0.9)
    if (
        hunter is not None
        and hunter.configured()
        and "hunter" not in run.dead
        and not _hunter_none_fresh(domain)
    ):
        blocked = _budget_gate(campaign_id, budget_map, "hunter", 1, confirm)
        if blocked is not None:
            run.hunter_blocked = blocked
        else:
            try:
                found = hunter.domain_pattern(domain)
            except ProviderError as exc:
                run.failed("hunter", exc)
            else:
                run.spent["hunter"] += 1
                if campaign_id is not None:
                    budget.charge(campaign_id, "hunter", 1)
                if found:
                    _cache_pattern(domain, found[0], "hunter", found[1])
                    return _Derived(found[0], "hunter", found[1])
                _set_setting(f"hunter_none:{domain}", db.now())  # ask Hunter once per domain
    if scrape:
        site = _pattern_from_examples(domain, scrape_site_emails(domain))
        if site:
            _cache_pattern(domain, site, "website", 0.7)
            return _Derived(site, "website", 0.7)
    return _Derived(DEFAULT_PATTERN, "default", 0.3)


def derive_pattern(
    domain: str,
    known_emails: Sequence[Any] | None = None,
    *,
    hunter: Any = None,
    scrape: bool = True,
) -> tuple[str, str]:
    """`(pattern, source)` for a domain: the saved pattern, then `known_emails` (addresses as
    `(email, first, last)` tuples or bare strings, e.g. found by the AI), then Hunter (if its
    key is set), then addresses on the company website, then the default `{first}.{last}`.

    `source` is `cache`-d text of the original source, `known_email`, `hunter`, `website` or
    `default`. Real patterns are saved per domain (`settings` key `pattern:<domain>`); the
    default is not, so a later Hunter key can still improve it.
    """
    domain = clean_domain(domain)
    if hunter is None:
        candidate = HunterProvider()
        hunter = candidate if candidate.configured() else None
    result = _derive(domain, known_emails, hunter, scrape, None, None, False, _Run())
    return result.pattern, result.source


# --- verification ---------------------------------------------------------------------------


def _default_verifiers() -> list[Any]:
    """Configured verifiers in order: Reoon, ZeroBounce, Hunter."""
    return [p for p in (ReoonProvider(), ZeroBounceProvider(), HunterProvider()) if p.configured()]


def _policy_rejection(message: Any) -> bool:
    """True when an SMTP reply carries an enhanced code 5.7.x (refused for policy reasons:
    our sender, our IP, SPF/RBL ... not because the mailbox is missing)."""
    raw = message if isinstance(message, bytes) else str(message).encode("utf-8", "replace")
    return bool(_POLICY_CODE_RE.match(raw))


def _smtp_rcpt(host: str, email: str) -> bool | None:
    """Ask the mail server whether it accepts `email`: True (250), False (550/551/553), else None.

    The sender is the null sender `<>`. The answer is None (unknown) when the server refuses the
    sender (any MAIL FROM reply but 250), when it rejects the address with a 5.7.x policy code,
    or when it also rejects `postmaster@<domain>` (which RFC 5321 requires every server to
    accept) in the same session: then the refusal is about us, not the mailbox. Never called for
    Google, Microsoft or gateway mail (they accept everything or tarpit). Port 25 is blocked on
    many networks; every failure gives None.
    """
    server = smtplib.SMTP(timeout=SMTP_TIMEOUT_S)
    try:
        server.connect(host, 25)
        server.ehlo_or_helo_if_needed()
        sender_code, _reply = server.mail("")
        if sender_code != 250:
            return None
        code, message = server.rcpt(email)
        if code == 250:
            return True
        if code not in (550, 551, 553) or _policy_rejection(message):
            return None
        postmaster_code, _reply = server.rcpt(f"postmaster@{email.rsplit('@', 1)[-1]}")
        return False if postmaster_code == 250 else None
    except (OSError, smtplib.SMTPException):
        return None
    finally:
        try:
            server.close()
        except (OSError, smtplib.SMTPException):  # pragma: no cover - best effort
            pass


def _smtp_verify(email: str) -> Verification | None:
    host, _definitive = _mx_lookup(email.rsplit("@", 1)[1])
    if not host:
        return None
    domain = clean_domain(email)
    answer = _smtp_rcpt(host, email)
    if answer is False:
        return Verification("invalid", "smtp", "rcpt rejected")
    if answer is not True:
        return None
    fake = f"zz-no-such-user-{random.randrange(10**5, 10**6)}@{domain}"
    probe = _smtp_rcpt(host, fake)
    if probe is True:
        _cache_catch_all(domain, True)
        return Verification("risky", "smtp", "accepts any address", catch_all=True)
    if probe is False:
        _cache_catch_all(domain, False)
        return Verification("verified", "smtp", "rcpt accepted")
    return None  # accepted, but we could not rule out a catch-all server: stay a guess


def verify(
    email: str,
    mx: str | None,
    *,
    verifiers: Sequence[Any] | None = None,
    campaign_id: int | None = None,
    budget_map: Mapping[str, Any] | None = None,
    confirm: bool = False,
    run: _Run | None = None,
) -> Verification:
    """Grade one address: Reoon, then ZeroBounce, then Hunter's verifier, then (only when `mx`
    is `other`) an SMTP probe, else `guess`.

    A verifier that says `unknown` or fails is skipped for the next one; the first clear answer
    wins. The API verifiers report catch-all domains themselves (`risky`); the SMTP path probes
    a random address. A domain found to be catch-all is remembered, so its other people are
    `risky` without spending credits. With a campaign, each call is checked against and counted
    in its `verify` budget (Hunter also in `hunter`); a stop is returned as `blocked`.
    """
    run = run if run is not None else _Run()
    domain = clean_domain(email)
    if _catch_all_cached(domain) is True:
        return Verification("risky", "catch_all_cache", "known catch-all domain", True)
    chain = list(verifiers) if verifiers is not None else _default_verifiers()
    for verifier in chain:
        key = verifier.info.key
        if key in run.dead:
            continue
        if run.out_of_time():
            return Verification("guess", timed_out=True)
        blocked = _budget_gate(campaign_id, budget_map, "verify", 1, confirm)
        if blocked is None and key == "hunter":
            blocked = _budget_gate(campaign_id, budget_map, "hunter", 0.5, confirm)
        if blocked is not None:
            return Verification("guess", blocked=blocked)
        try:
            verdict = verifier.verify(email)
        except ProviderError as exc:
            run.failed(key, exc)
            continue
        run.spent["verify"] += 1
        if campaign_id is not None:
            budget.charge(campaign_id, "verify", 1)
            if key == "hunter":
                budget.charge(campaign_id, "hunter", 0.5)
                run.spent["hunter"] += 0.5
        if verdict.catch_all:
            _cache_catch_all(domain, True)
            return Verification("risky", key, verdict.raw_status, True)
        if verdict.status == "unknown":
            continue
        return Verification(verdict.status, key, verdict.raw_status)
    if mx == "other" and SMTP_PROBE:
        if run.out_of_time():
            return Verification("guess", timed_out=True)
        smtp = _smtp_verify(email)
        if smtp is not None:
            return smtp
    return Verification("guess")


# --- storing --------------------------------------------------------------------------------


def _suppressed(email: str) -> bool:
    email = email.strip().lower()
    host = clean_domain(email)
    with db.session() as conn:
        rows = db.query(
            conn, "SELECT value, kind FROM suppression WHERE kind IN ('email', 'domain')"
        )
    for row in rows:
        value = str(row["value"]).strip().lower()
        if row["kind"] == "email" and value == email:
            return True
        if row["kind"] == "domain":
            value = clean_domain(value)
            if value and (host == value or host.endswith("." + value)):
                return True
    return False


def store_email(
    person_id: int,
    email: str,
    status: str,
    *,
    method: str = "",
    pattern: str = "",
    verifier: str = "",
) -> str:
    """Save one address for a person. Returns `stored`, `role_address` or `suppressed` (and
    saves nothing for the last two). Any other `guess`/`unknown` row of the person is replaced."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of: {', '.join(STATUSES)}")
    email = email.strip().lower()
    if is_role_address(email):
        return "role_address"
    if _suppressed(email):
        return "suppressed"
    with db.session() as conn:
        conn.execute(
            "DELETE FROM emails WHERE person_id = ? AND email <> ? "
            "AND status IN ('guess', 'unknown')",
            (person_id, email),
        )
        db.upsert(
            conn, "emails", ("person_id", "email"),
            person_id=person_id, email=email, method=method, pattern=pattern,
            status=status, verifier=verifier, checked_at=db.now(),
        )  # fmt: skip
    return "stored"


# --- selecting people -----------------------------------------------------------------------


def _campaign(campaign_id: int) -> dict[str, Any]:
    return campaign_mod.get_campaign(campaign_id)  # raises CampaignError(not_found)


def _int_list(values: Any) -> list[int]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise EmailError("invalid_value", "Ids must be a list of whole numbers.")
    out: list[int] = []
    for v in values:
        if isinstance(v, bool) or not isinstance(v, int):
            raise EmailError("invalid_value", "Ids must be a list of whole numbers.")
        out.append(v)
    return list(dict.fromkeys(out))


def _cursor(value: str | int | None) -> int:
    if value in (None, ""):
        return 0
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = -1
    if n < 0:
        raise EmailError("invalid_cursor", "The cursor is not valid.", "Start again without one.")
    return n


def _check_channel(camp: Mapping[str, Any]) -> None:
    if "email" not in (camp.get("channels") or []):
        raise EmailError(
            "no_email_channel",
            "This campaign does not use the email channel.",
            "Add 'email' to the campaign's channels with update_campaign first.",
        )


def _people(
    campaign_id: int, after: int = 0, person_ids: Sequence[int] | None = None
) -> list[dict[str, Any]]:
    """Confirmed people of the campaign (id above `after`) with company domain and email rows."""
    sql = (
        "SELECT p.id, p.first_name, p.last_name, p.company_id, c.domain AS domain "
        "FROM people p JOIN companies c ON c.id = p.company_id "
        "WHERE p.campaign_id = ? AND p.status = 'confirmed' AND p.id > ?"
    )
    params: list[Any] = [campaign_id, after]
    if person_ids:
        sql += f" AND p.id IN ({', '.join('?' for _ in person_ids)})"
        params.extend(person_ids)
    with db.session() as conn:
        rows = db.query(conn, sql + " ORDER BY p.id", params)
        have = db.query(
            conn,
            "SELECT e.person_id, e.status FROM emails e JOIN people p ON p.id = e.person_id "
            "WHERE p.campaign_id = ?",
            (campaign_id,),
        )
    by_person: dict[int, list[str]] = {}
    for row in have:
        by_person.setdefault(row["person_id"], []).append(row["status"])
    for row in rows:
        row["email_statuses"] = by_person.get(row["id"], [])
    return rows


def _skip_reason(row: Mapping[str, Any], explicit: bool) -> str | None:
    """Why a person is not searched (None = search), by the rules in `find_emails`."""
    statuses = row["email_statuses"]
    if statuses and not (explicit and all(s in ("guess", "unknown") for s in statuses)):
        return "already_has_email"
    if not clean_domain(row.get("domain") or ""):
        return "no_company_domain"
    if not _first_part(row["first_name"] or "") or not _last_parts(row["last_name"] or "")[0]:
        return "no_usable_name"
    return None


def _verifier_state() -> tuple[bool, bool]:
    """`(any API verifier set, Hunter set)`."""
    return bool(_default_verifiers()), HunterProvider().configured()


def estimate_email_search(
    campaign_id: int, person_ids: Sequence[int] | None = None
) -> dict[str, Any]:
    """What an email search would use: `people`, `domains`, `hunter_credits` (one pattern lookup
    per domain without a saved pattern, when Hunter is set), `verify_credits` (one per person,
    when a verifier is set) and `budget_left` for the campaign."""
    any_verifier, has_hunter = _verifier_state()
    return _estimate(campaign_id, _int_list(person_ids), has_hunter, any_verifier)


def _estimate(
    campaign_id: int, ids: Sequence[int], has_hunter: bool, any_verifier: bool
) -> dict[str, Any]:
    camp = _campaign(campaign_id)
    todo = [r for r in _people(campaign_id, 0, ids or None) if not _skip_reason(r, bool(ids))]
    domains = {clean_domain(r["domain"]) for r in todo}
    hunter_credits = (
        sum(1 for d in domains if _cached_pattern(d) is None and not _hunter_none_fresh(d))
        if has_hunter
        else 0
    )
    verify_credits = len(todo) if any_verifier else 0
    threshold = load_settings().confirm_threshold_credits
    return {
        "ok": True,
        "people": len(todo),
        "domains": len(domains),
        "hunter_credits": hunter_credits,
        "verify_credits": verify_credits,
        "budget_left": {
            "hunter": budget.limit(camp["budget"], "hunter") - budget.used(campaign_id, "hunter"),
            "verify": budget.limit(camp["budget"], "verify") - budget.used(campaign_id, "verify"),
        },
        "verifier_configured": any_verifier,
        "confirm_threshold": threshold,
        "needs_confirmation": hunter_credits + verify_credits > threshold,
    }


def _email_counts(campaign_id: int) -> dict[str, int]:
    with db.session() as conn:
        rows = db.query(
            conn,
            "SELECT e.status AS status, COUNT(*) AS n FROM emails e "
            "JOIN people p ON p.id = e.person_id WHERE p.campaign_id = ? GROUP BY e.status",
            (campaign_id,),
        )
    found = {r["status"]: r["n"] for r in rows}
    return {s: found.get(s, 0) for s in STATUSES}


# --- find_emails ----------------------------------------------------------------------------


def find_emails(
    campaign_id: int,
    person_ids: Sequence[int] | None = None,
    confirm: bool = False,
    cursor: str | int | None = None,
    max_people_per_call: int = PAGE_SIZE_MAX,
    hunter: Any = None,
    verifiers: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Find and grade one email for each confirmed person of an email-channel campaign.

    Searches people without an email yet (with `person_ids`, also those whose row is only a
    `guess`/`unknown`). Pass `confirm=true` after telling the user the cost: without it a run
    needing more credits than `settings.confirm_threshold_credits`, or more than the campaign's
    `hunter` / `verify` budget, returns the estimate (`needs_confirmation`) or the
    `budget_reached` reply. Each call handles at most `max_people_per_call` people (25) and
    stops starting new people after about 25 seconds (a slow check can add up to 25 more),
    returning `next_cursor` until done. Missing or exhausted verifier keys never stop the run:
    those addresses stay `guess`, and the reply says why. `hunter` and
    `verifiers` let tests pass fakes.
    """
    camp = _campaign(campaign_id)
    _check_channel(camp)
    ids = _int_list(person_ids)
    start = _cursor(cursor)
    limit = min(max(1, int(max_people_per_call)), PAGE_SIZE_MAX)
    chain = list(verifiers) if verifiers is not None else _default_verifiers()
    if hunter is None:
        candidate = HunterProvider()
        hunter = candidate if candidate.configured() else None

    if start == 0 and not confirm:
        est = _estimate(campaign_id, ids, hunter is not None and hunter.configured(), bool(chain))
        if est["needs_confirmation"]:
            credits = est["hunter_credits"] + est["verify_credits"]
            return {
                **est,
                "message": f"This needs about {credits:g} credits (Hunter and email checks). "
                "Ask the user, then repeat the call with confirm=true.",
            }

    rows = _people(campaign_id, start, ids or None)
    found_ids = {r["id"] for r in rows}
    unknown_ids = sorted(set(ids) - found_ids) if start == 0 else []
    run = _Run()
    totals = dict.fromkeys(STATUSES, 0)
    skipped: Counter[str] = Counter()
    patterns: dict[str, dict[str, str]] = {}
    stop: dict[str, Any] | None = None
    done = 0
    last_id = start
    remaining = False
    began = time.monotonic()
    for row in rows:
        reason = _skip_reason(row, bool(ids))
        if reason is None and done >= limit:
            remaining = True
            break
        if reason is None and done and time.monotonic() - began > CALL_TIME_LIMIT_S:
            remaining = True
            break
        last_id = row["id"]
        if reason is not None:
            skipped[reason] += 1
            continue
        # The first person of a call always gets the whole verifier chain, however long its
        # pattern step took (it is never deferred); the deadline only guards later people.
        run.deadline = began + VERIFY_DEADLINE_S if done > 0 else None
        outcome = _process_person(
            camp, row, bool(ids), confirm, hunter, chain, run, patterns, can_defer=done > 0
        )
        if outcome == _DEFERRED:  # out of time mid-person: nothing stored, redo it next call
            remaining = True
            last_id = row["id"] - 1
            break
        if isinstance(outcome, dict):
            stop = outcome
            last_id = row["id"] - 1 if row["id"] > start else start
            break
        if outcome in totals:
            totals[outcome] += 1
        else:
            skipped[outcome] += 1
        done += 1

    finished = stop is None and not remaining
    reply: dict[str, Any] = {
        **(stop if stop is not None else {"ok": True}),
        "people_done": done,
        "this_call": totals,
        "skipped": dict(skipped),
        "campaign_emails": _email_counts(campaign_id),
        "patterns_used": dict(list(patterns.items())[:5]),
        "credits_used": {k: float(v) for k, v in run.spent.items()},
        "done": finished,
        "next_cursor": None if finished else str(last_id),
    }
    notes = _notes(chain, run)
    if notes:
        reply["notes"] = notes
    if run.issues:
        reply["provider_issues"] = list(run.issues.values())
    if run.hunter_blocked is not None:
        reply["hunter_budget"] = run.hunter_blocked
    if not chain:
        reply["verifier_needs_key"] = registry.needs_key_response(
            "reoon", "to check that the guessed addresses exist"
        )
    if unknown_ids:
        reply["skipped_person_ids"] = unknown_ids
    return reply


def _notes(chain: Sequence[Any], run: _Run) -> list[str]:
    notes: list[str] = []
    if not chain:
        notes.append(
            "No email verifier key is set, so addresses are `guess` (not checked). Add a "
            "Reoon key (free) to verify them."
        )
    if run.hunter_blocked is not None:
        notes.append("The Hunter budget was reached, so patterns came from the website or default.")
    return notes


def _process_person(
    camp: Mapping[str, Any],
    row: Mapping[str, Any],
    explicit: bool,
    confirm: bool,
    hunter: Any,
    chain: Sequence[Any],
    run: _Run,
    patterns: dict[str, dict[str, str]],
    can_defer: bool = False,
) -> str | dict[str, Any]:
    """Find, grade and store one person's email. Returns the stored status, a skip word, or a
    budget reply (the run must stop and this person is not stored). With `can_defer`, running
    out of time before a check could be made returns `_DEFERRED` (nothing is stored)."""
    domain = clean_domain(row["domain"])
    mx_host, definitive = _mx_lookup(domain)
    pattern_info = _derive(
        domain, None, hunter, True, camp["id"], camp["budget"], confirm, run
    )
    patterns.setdefault(domain, {"pattern": pattern_info.pattern, "source": pattern_info.source})
    pattern, source = pattern_info.pattern, pattern_info.source
    from_example = source in REAL_SOURCES
    first, last = row["first_name"], row["last_name"]
    candidates = candidate_emails(pattern, first, last, domain, from_example)
    if not candidates:
        candidates = candidate_emails(DEFAULT_PATTERN, first, last, domain)
        pattern, source = DEFAULT_PATTERN, "default"
    if not candidates:
        return "no_usable_name"
    if any(is_role_address(c) for c in candidates):
        return "role_address"
    if _suppressed(candidates[0]):
        return "suppressed"
    method = f"pattern:{source}"
    if mx_host is None:
        if definitive:
            status, verifier = "invalid", "mx"
        else:
            status, verifier = "unknown", ""
        chosen = candidates[0]
    else:
        mx = classify_mx(mx_host)
        chosen = candidates[0]
        verdict = Verification("guess")
        for candidate in candidates:
            verdict = verify(
                candidate, mx, verifiers=chain, campaign_id=camp["id"],
                budget_map=camp["budget"], confirm=confirm, run=run,
            )  # fmt: skip
            if verdict.blocked is not None:
                return verdict.blocked
            if verdict.timed_out and can_defer:
                return _DEFERRED
            chosen = candidate
            if verdict.status != "invalid":
                break
        status, verifier = verdict.status, verdict.verifier
    result = store_email(
        row["id"], chosen, status, method=method, pattern=pattern, verifier=verifier
    )
    return status if result == "stored" else result


__all__ = [
    "EmailError",
    "Verification",
    "addresses_on_domain",
    "build_email",
    "candidate_emails",
    "classify_mx",
    "derive_pattern",
    "estimate_email_search",
    "find_emails",
    "infer_pattern",
    "is_role_address",
    "known_pattern",
    "learn_pattern_from_examples",
    "mx_type",
    "scrape_site_emails",
    "scrape_site_sources",
    "store_email",
    "verify",
]
