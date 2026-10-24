"""Target companies: add, de-duplicate, verify the domain, list, approve, import, research.

The AI proposes companies (from its own knowledge and from `search_web`); this module cleans
them up, drops duplicates, marks excluded names, checks that each domain really exists, and
keeps the list for the user to approve. A failed domain check never blocks adding a company.

Problems that need a human look are stored as lines starting with `Warning: ` in the company's
`notes`, and `approve_all` skips any company that has one. Callers must run `db.init_db()`
first. Errors are raised as `CompanyError` (or `CampaignError` for an unknown campaign), each
with `as_response()` so adapters can return the structured reply.
"""

from __future__ import annotations

import csv
import io
import logging
import math
import re
import sqlite3
import time
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import dns.exception
import dns.resolver
import httpx
import idna

from leadgen import db
from leadgen.core import countries as countries_mod
from leadgen.core import http
from leadgen.core.text import normalize_whitespace
from leadgen.providers import registry, search
from leadgen.providers.base import ProviderError
from leadgen.services import budget, campaign, contacts_import, offer_web

logger = logging.getLogger("leadgen.companies")

STATUSES = ("candidate", "approved", "excluded")
SOURCES = ("ai_knowledge", "search", "user")
MAX_BATCH = 200  # companies per add_companies call
MAX_CSV_ROWS = 2000
MAX_CSV_BYTES = 5_000_000
MAX_NAME_CHARS = 200
PAGE_SIZE_MAX = 50
CHECK_WORKERS = 4
CHECK_TIMEOUT = 5.0  # seconds, for the DNS phase and for each HTTP request
CHECK_DEADLINE = 45.0  # seconds for all domain checks of one call; the rest wait for check_domains
SEARCH_NUM_MAX = 20
MIN_ALIAS_CHARS = 3
SHORT_CAPS_MAX = 5

WARNING_PREFIX = "Warning: "
CHECK_PREFIX = "Domain check: "
_DOMAIN_WARNING_PREFIX = "Warning: domain "

# All-caps aliases of up to 5 letters that are common words or ambiguous abbreviations
# (PLAN section 11: "avoid BD, SAL"). They are kept but flagged for a human look.
AMBIGUOUS_CAPS = frozenset(
    {"BD", "SAL", "SIT", "SP", "AI", "HR", "IT", "PR", "CS", "OD", "MD",
     "ALL", "ONE", "NOW", "NEW", "TOP", "BIG", "OPEN", "NEXT", "FIRST"}
)  # fmt: skip
# Words that say nothing about which company is meant; never kept as an alias on their own.
GENERIC_WORDS = frozenset(
    {"group", "groups", "holdings", "holding", "company", "companies", "corporation", "corp",
     "co", "limited", "ltd", "pte", "inc", "llc", "plc", "international", "global", "asia",
     "pacific", "apac", "emea", "services", "solutions", "technologies", "industries",
     "enterprises", "the", "and", "of"}
)  # fmt: skip
# Legal endings ignored when comparing names for duplicates.
_LEGAL_SUFFIXES = frozenset(
    {"ltd", "limited", "pte", "inc", "incorporated", "llc", "plc", "corp", "corporation", "co",
     "company", "gmbh", "ag", "sa", "bv", "nv", "pty", "llp", "bhd", "sdn", "pvt", "private"}
)  # fmt: skip

_LABEL = r"(?!-)[a-z0-9-]{1,63}(?<!-)"
_DOMAIN_RE = re.compile(rf"{_LABEL}(?:\.{_LABEL})+")


class CompanyError(Exception):
    """A company request failed. `code` is stable text; `hint` tells the AI what to do.

    `response` may carry a ready-made structured reply (needs_key, budget_reached, ...).
    """

    def __init__(
        self,
        code: str,
        message: str,
        hint: str = "",
        response: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.response = response

    def as_response(self) -> dict[str, Any]:
        """The `{"ok": False, ...}` shape used by every tool."""
        if self.response is not None:
            return self.response
        return {
            "ok": False,
            "error": {"code": self.code, "message": self.message, "hint": self.hint},
        }


# --- normalisation --------------------------------------------------------------------------


def normalize_domain(value: str) -> str:
    """A bare lower-case host from a URL, email or domain: no scheme, path, port or `www.`.

    Internationalised names are returned in ASCII (punycode) form, e.g. `bücher.de` ->
    `xn--bcher-kva.de`. Returns "" for empty or invalid input (spaces, no dot, an IP address).
    """
    text = (value or "").strip()
    if not text:
        return ""
    try:
        host = urlparse(text if "://" in text else f"//{text}").hostname or ""
    except ValueError:
        return ""
    host = host.strip().rstrip(".").lower()
    if not host.isascii():  # UTS 46 mapping, as browsers and httpx: straße.de -> xn--strae-oqa.de
        try:
            host = idna.encode(host, uts46=True).decode("ascii")
        except (idna.IDNAError, UnicodeError):
            return ""
        host = host.lower()
    if host.startswith("www.") and "." in host[4:]:
        host = host[4:]
    if len(host) > 253 or not _DOMAIN_RE.fullmatch(host):
        return ""
    tld = host.rsplit(".", 1)[1]
    if len(tld) < 2 or tld.isdigit():
        return ""
    return host


def _norm_name(name: str) -> str:
    """Name used to spot duplicates: folded case and accents, `&` as `and`, no punctuation, no
    trailing legal endings (`Pte Ltd`, `Inc`)."""
    folded = unicodedata.normalize("NFKD", name.replace("&", " and "))
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch)).casefold()
    tokens = re.sub(r"[^\w]+", " ", folded).split()
    trimmed = list(tokens)
    while len(trimmed) > 1 and trimmed[-1] in _LEGAL_SUFFIXES:
        trimmed.pop()
    return " ".join(trimmed)


def _clean_aliases(name: str, aliases: Iterable[str]) -> tuple[list[str], list[dict], list[str]]:
    """Apply the alias rules (PLAN section 11). Returns `(aliases, reports, warning lines)`.

    The name is always first and always kept. Aliases under 3 characters and generic words or
    country names on their own are dropped; all-caps aliases of up to 5 letters that are on the
    ambiguous list are kept but flagged (`warning lines` are stored on the company).
    """
    kept = [name]
    seen = {name.casefold()}
    reports: list[dict] = []
    warnings: list[str] = []

    def report(alias: str, action: str, reason: str) -> None:
        reports.append({"company": name, "alias": alias, "action": action, "reason": reason})

    candidates = [(name, True), *((a, False) for a in aliases)]
    for alias, is_name in candidates:
        alias = normalize_whitespace(alias)
        if not alias:
            continue
        if not is_name:
            if alias.casefold() in seen:
                continue
            if len(alias) < MIN_ALIAS_CHARS:
                report(alias, "dropped", f"shorter than {MIN_ALIAS_CHARS} characters")
                continue
            tokens = re.sub(r"[^\w]+", " ", alias.casefold()).split()
            if not tokens or all(t in GENERIC_WORDS for t in tokens):
                report(alias, "dropped", "generic word on its own")
                continue
            try:
                countries_mod.get(alias)
            except countries_mod.UnknownCountryError:
                pass
            else:
                report(alias, "dropped", "a country name on its own")
                continue
            seen.add(alias.casefold())
            kept.append(alias)
        if alias.isupper() and len(alias) <= SHORT_CAPS_MAX and alias in AMBIGUOUS_CAPS:
            reason = "short all-caps form that is a common word or an ambiguous abbreviation"
            report(alias, "kept", reason)
            warnings.append(f"{WARNING_PREFIX}alias '{alias}' is ambiguous ({reason})")
    return kept, reports, warnings


# --- notes ----------------------------------------------------------------------------------


def _join_notes(lines: Iterable[str]) -> str:
    return "\n".join(line for line in lines if line.strip())


def _replace_lines(notes: str, drop_prefixes: tuple[str, ...], add: Iterable[str]) -> str:
    """`notes` without lines starting with any of `drop_prefixes`, plus the `add` lines."""
    keep = [ln for ln in notes.splitlines() if not ln.startswith(drop_prefixes)]
    return _join_notes([*keep, *add])


def _warnings_in(notes: str) -> list[str]:
    return [ln[len(WARNING_PREFIX) :] for ln in notes.splitlines() if ln.startswith(WARNING_PREFIX)]


# --- domain check ---------------------------------------------------------------------------


@dataclass
class DomainCheck:
    """Result of checking one domain. `ok` is None when the check was skipped (time limit)."""

    ok: bool | None
    note: str
    final_host: str = ""
    redirect_warning: str = ""


def _dns_exists(host: str) -> tuple[bool, str]:
    """True when `host` has an A, AAAA or MX record. The second value explains a failure."""
    deadline = time.monotonic() + CHECK_TIMEOUT
    problem = ""
    for rdtype in ("A", "AAAA", "MX"):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False, "DNS lookup timed out"
        try:
            dns.resolver.resolve(host, rdtype, lifetime=remaining)
            return True, ""
        except dns.resolver.NXDOMAIN:
            return False, "the domain does not exist (no DNS records)"
        except dns.resolver.NoAnswer:
            problem = "no A, AAAA or MX records"
        except dns.exception.Timeout:
            return False, "DNS lookup timed out"
        except dns.exception.DNSException as exc:
            return False, f"DNS lookup failed ({type(exc).__name__})"
    return False, problem or "no A, AAAA or MX records"


def _ascii_host(url: httpx.URL) -> str:
    """The host as sent on the wire (punycode for IDN names); `url.host` is decoded to Unicode."""
    return url.raw_host.decode("ascii").lower()


def _homepage(client: httpx.Client, host: str) -> tuple[int | None, str, str]:
    """`(status, final host, problem)` for the homepage: HTTPS first, then HTTP; HEAD, then GET
    when HEAD is refused. Any answer below 500 counts as reachable."""
    problem = "the website did not answer"
    for scheme in ("https", "http"):
        url = f"{scheme}://{host}/"
        status: int | None = None
        final = host
        try:
            response = client.request("HEAD", url)
            status, final = response.status_code, _ascii_host(response.url)
            if status >= 400:
                with client.stream("GET", url) as got:
                    status, final = got.status_code, _ascii_host(got.url)
        except offer_web.WebError:
            return None, "", "it redirects to a local or private address"
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            if status is None:
                problem = f"the website did not answer ({type(exc).__name__})"
                continue
        if status is not None and status < 500:
            return status, (final or host).lower(), ""
        problem = f"the website answered HTTP {status}"
    return None, "", problem


def _same_site(a: str, b: str) -> bool:
    a, b = a.removeprefix("www."), b.removeprefix("www.")
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def check_domain(host: str) -> DomainCheck:
    """Check one normalised domain: DNS (A, AAAA or MX), then the homepage. Never raises."""
    exists, problem = _dns_exists(host)
    if not exists:
        return DomainCheck(False, f"{CHECK_PREFIX}failed, {problem}")
    try:
        with http.client(request_hook=offer_web._guard_request) as client:
            client.timeout = httpx.Timeout(CHECK_TIMEOUT)
            status, final, problem = _homepage(client, host)
    except Exception as exc:  # never let one bad domain stop the others
        logger.debug("domain check crashed for %s: %s", host, type(exc).__name__)
        return DomainCheck(False, f"{CHECK_PREFIX}failed, check error ({type(exc).__name__})")
    if status is None:
        return DomainCheck(False, f"{CHECK_PREFIX}failed, DNS exists but {problem}")
    note = f"{CHECK_PREFIX}ok"
    if status >= 400:
        note += f" (homepage answered HTTP {status}; the site exists but may block checks)"
    warning = ""
    if final and not _same_site(final, host):
        note += f", redirects to {final}"
        warning = f"{_DOMAIN_WARNING_PREFIX}redirects to a different site ({final}); check it"
    return DomainCheck(True, note, final, warning)


def _run_checks(
    rows: list[dict[str, Any]],
    check: Callable[[str], DomainCheck] | None = None,
    deadline_seconds: float = CHECK_DEADLINE,
) -> dict[str, Any]:
    """Check the domains of `rows` (dicts with id, name, domain, notes) in 4 threads, save the
    results, and return `{"checked", "domain_failures", "unchecked"}`."""
    check = check or check_domain
    deadline = time.monotonic() + deadline_seconds

    def work(row: dict[str, Any]) -> DomainCheck:
        if time.monotonic() > deadline:
            return DomainCheck(None, "")
        return check(row["domain"])

    # Do not wait for checks still running at the deadline (a slow site could hold the call for
    # a long time): they are left unchecked, and their threads finish in the background.
    pool = ThreadPoolExecutor(max_workers=CHECK_WORKERS)
    futures = [pool.submit(work, row) for row in rows]
    wait(futures, timeout=max(0.0, deadline - time.monotonic()))
    pool.shutdown(wait=False, cancel_futures=True)
    results = [
        f.result() if f.done() and not f.cancelled() else DomainCheck(None, "") for f in futures
    ]
    failures: list[dict[str, Any]] = []
    unchecked: list[dict[str, Any]] = []
    checked = 0
    with db.session() as conn:
        for row, res in zip(rows, results, strict=True):
            if res.ok is None:
                unchecked.append({"id": row["id"], "name": row["name"]})
                continue
            checked += 1
            notes = _replace_lines(
                row["notes"],
                (CHECK_PREFIX, _DOMAIN_WARNING_PREFIX),
                [res.note, res.redirect_warning],
            )
            db.update(conn, "companies", row["id"], domain_ok=int(res.ok), notes=notes)
            if not res.ok:
                failures.append(
                    {
                        "id": row["id"],
                        "name": row["name"],
                        "domain": row["domain"],
                        "note": res.note.removeprefix(CHECK_PREFIX),
                    }
                )
    return {"checked": checked, "domain_failures": failures, "unchecked": unchecked}


# --- shaping --------------------------------------------------------------------------------


def _shape(row: dict[str, Any]) -> dict[str, Any]:
    ok = row["domain_ok"]
    return {
        "id": row["id"],
        "name": row["name"],
        "aliases": row["aliases_json"] or [],
        "domain": row["domain"],
        "country": row["country"],
        "sector": row["sector"],
        "size_note": row["size_note"],
        "source": row["source"],
        "domain_ok": None if ok is None else bool(ok),
        "status": row["status"],
        "notes": row["notes"],
        "warnings": _warnings_in(row["notes"]),
    }


def _campaign(campaign_id: int) -> dict[str, Any]:
    return campaign.get_campaign(campaign_id)  # raises CampaignError(not_found)


# --- search_web -----------------------------------------------------------------------------


class _BudgetReached(Exception):
    pass


class _MeteredProvider:
    """Wraps a search provider for one `search_web` call. `search` runs only on a cache miss, so
    it is where a campaign's search budget is checked and the credits are counted."""

    def __init__(self, inner: Any, so_far: float, cap: float, confirm: bool) -> None:
        self._inner = inner
        self.info = inner.info
        self._so_far = so_far
        self._cap = cap
        self._confirm = confirm
        self.spent = 0.0

    def search(self, query: str, country_gl: str = "", num: int = 10):
        """Run the real search unless the budget would be exceeded."""
        cost = float(max(1, math.ceil(num / 10)))
        if not self._confirm and self._so_far + self.spent + cost > self._cap:
            raise _BudgetReached
        results = self._inner.search(query, country_gl, num)
        self.spent += cost
        return results


def _registry_key(key: str) -> str:
    return key if key in registry.ALL_PROVIDERS else "serper"


def search_web(
    query: str,
    country: str | None = None,
    num: int = 10,
    campaign_id: int | None = None,
    confirm: bool = False,
    provider: Any = None,
) -> list[dict[str, Any]]:
    """Raw Google-style results (`title`, `link`, `snippet`, `position`) for the AI's research.

    Goes through the 30-day cache, so a repeated query costs nothing. With `campaign_id`, real
    searches are counted against the campaign's `search` budget; when that would be exceeded
    and `confirm` is false it raises `CompanyError` carrying the `budget_reached` reply.
    A missing key or no credits raise `CompanyError` with the `needs_key` / `quota_exhausted`
    reply. `provider` lets tests pass a fake; normally the configured one is used.
    """
    query = normalize_whitespace(query or "")
    if not query:
        raise CompanyError("invalid_value", "The search query is empty.")
    if isinstance(num, bool) or not isinstance(num, int) or not 1 <= num <= SEARCH_NUM_MAX:
        raise CompanyError(
            "invalid_value", f"num must be a whole number from 1 to {SEARCH_NUM_MAX}."
        )
    gl = ""
    if country:
        try:
            gl = countries_mod.get(country).gl
        except countries_mod.UnknownCountryError as exc:
            raise CompanyError("invalid_country", str(exc)) from None
    so_far, cap = 0.0, float("inf")
    if campaign_id is not None:
        camp = _campaign(campaign_id)
        so_far, cap = budget.used(campaign_id, "search"), budget.limit(camp["budget"], "search")
    try:
        inner = provider if provider is not None else registry.get_search_provider()
    except ProviderError as exc:
        raise CompanyError(
            exc.code,
            exc.message,
            response=registry.error_response("serper", exc, "to search the web"),
        ) from None
    metered = _MeteredProvider(inner, so_far, cap, confirm)
    try:
        results = search.cached_search(metered, query, gl, num)
    except _BudgetReached:
        raise CompanyError(
            "budget_reached",
            "The campaign's search budget would be exceeded.",
            response=registry.budget_response("search", so_far, cap),
        ) from None
    except ProviderError as exc:
        raise CompanyError(
            exc.code,
            exc.message,
            response=registry.error_response(
                _registry_key(inner.info.key), exc, "to search the web"
            ),
        ) from None
    finally:
        if campaign_id is not None and metered.spent:
            budget.charge(campaign_id, "search", metered.spent)
    out = []
    for r in results:
        item: dict[str, Any] = {
            "title": r.title, "link": r.link, "snippet": r.snippet, "position": r.position,
        }  # fmt: skip
        if r.low_confidence:
            item["low_confidence"] = True
        out.append(item)
    return out


def research_queries(campaign_id: int) -> list[str]:
    """Search queries the AI can run to find target companies (it decides which to use).

    One set per sector and country of the campaign. With no sector the word "companies" is used.
    """
    camp = _campaign(campaign_id)
    sectors = camp["sectors"] or [""]
    names = [countries_mod.get(code).name for code in camp["countries"]]
    out: list[str] = []
    for sector in sectors:
        for country in names:
            noun = f"{sector} companies" if sector else "companies"
            employers = f"top {sector} employers" if sector else "top employers"
            wiki = f"list of {sector} companies" if sector else "list of companies"
            out += [
                f"largest {noun} in {country}",
                f"{employers} {country} list",
                f"{noun} {country} 1000 employees",
                f"site:wikipedia.org {wiki} {country}",
            ]
    return list(dict.fromkeys(out))


# --- add_companies --------------------------------------------------------------------------


def _text(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    return normalize_whitespace(value) if isinstance(value, str) else ""


def _prepare(raw: Any, camp: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """Clean one input company. Returns `(prepared, "")` or `(None, reason)`."""
    if not isinstance(raw, Mapping):
        return None, "each company must be an object with at least a name"
    name = _text(raw, "name")
    if not name:
        return None, "the company has no name"
    if len(name) > MAX_NAME_CHARS:
        return None, f"the name is longer than {MAX_NAME_CHARS} characters"
    source = _text(raw, "source") or "ai_knowledge"
    if source not in SOURCES:
        return None, f"source must be one of: {', '.join(SOURCES)}"
    aliases = raw.get("aliases") or []
    if isinstance(aliases, str):
        aliases = aliases.split(";")
    if not isinstance(aliases, (list, tuple)) or not all(isinstance(a, str) for a in aliases):
        return None, "aliases must be a list of text values"
    kept, alias_reports, warnings = _clean_aliases(name, aliases)
    domain_text = _text(raw, "domain")
    domain = normalize_domain(domain_text)
    if domain_text and not domain:
        warnings.append(
            f"{_DOMAIN_WARNING_PREFIX}'{domain_text}' is not a valid domain and was not stored"
        )
    country = ""
    country_text = _text(raw, "country")
    if country_text:
        try:
            country = countries_mod.get(country_text).iso2
        except countries_mod.UnknownCountryError:
            warnings.append(f"{WARNING_PREFIX}country '{country_text}' is not recognised")
    if not country and len(camp["countries"]) == 1:
        country = camp["countries"][0]
    return {
        "name": name,
        "aliases": kept,
        "alias_reports": alias_reports,
        "domain": domain,
        "bad_domain": domain_text if domain_text and not domain else "",
        "country": country,
        "sector": _text(raw, "sector"),
        "size_note": _text(raw, "size_note"),
        "source": source,
        "notes": normalize_whitespace(raw["notes"]) if isinstance(raw.get("notes"), str) else "",
        "warnings": warnings,
    }, ""


class _Exclusions:
    """Names and domains the campaign never wants (`exclusions` companies, domains, competitors)."""

    def __init__(self, exclusions: Mapping[str, list[str]]) -> None:
        self.names: dict[str, str] = {}
        self.domains: dict[str, str] = {}
        for label, entries in (
            ("excluded company", exclusions.get("companies", [])),
            ("excluded domain", exclusions.get("domains", [])),
            ("competitor", exclusions.get("competitors", [])),
        ):
            for entry in entries:
                why = f"{label} '{entry}'"
                domain = normalize_domain(entry)
                is_domain = label == "excluded domain" or "." in entry and " " not in entry
                if domain and is_domain:
                    self.domains.setdefault(domain, why)
                # a company or competitor is also matched by name (`Rival.io` may come with no
                # domain); an excluded-domain entry is only ever a domain
                if label != "excluded domain" or not domain:
                    self.names.setdefault(_norm_name(entry), why)

    def match(self, name: str, aliases: list[str], domain: str) -> str:
        """Why this company is excluded, or ""."""
        if domain:
            for blocked, why in self.domains.items():
                if domain == blocked or domain.endswith("." + blocked):
                    return why
        for candidate in (name, *aliases):
            why = self.names.get(_norm_name(candidate))
            if why:
                return why
        return ""


def _add(campaign_id: int, companies: list[Any], run_checks: bool) -> dict[str, Any]:
    camp = _campaign(campaign_id)
    exclusions = _Exclusions(camp["exclusions"])
    added: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    alias_warnings: list[dict[str, Any]] = []
    to_check: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    with db.session() as conn:
        existing = db.query(
            conn, "SELECT id, name, domain FROM companies WHERE campaign_id = ?", (campaign_id,)
        )
        domains = {r["domain"]: r["id"] for r in existing if r["domain"]}
        names = {_norm_name(r["name"]): r["id"] for r in existing}
        names_no_domain = {_norm_name(r["name"]): r["id"] for r in existing if not r["domain"]}

        for index, raw in enumerate(companies):
            prepared, reason = _prepare(raw, camp)
            if prepared is None:
                rejected.append({"index": index, "reason": reason})
                continue
            alias_warnings += prepared["alias_reports"]
            name, domain = prepared["name"], prepared["domain"]
            norm = _norm_name(name)
            same: int | None = None
            why = ""
            if domain and domain in domains:
                same, why = domains[domain], "same domain"
            elif domain and norm in names_no_domain:
                same, why = names_no_domain[norm], "same name as a company with no domain"
            elif not domain and norm in names:
                same, why = names[norm], "same name"
            if why:
                duplicates.append(
                    {"name": name, "domain": domain, "existing_id": same or None, "reason": why}
                )
                continue
            exclusion = exclusions.match(name, prepared["aliases"], domain)
            lines = [prepared["notes"], *prepared["warnings"]]
            if exclusion:
                lines.append(f"Excluded: matches {exclusion}")
            try:
                company_id = db.insert(
                    conn,
                    "companies",
                    campaign_id=campaign_id,
                    name=name,
                    aliases_json=prepared["aliases"],
                    domain=domain,
                    country=prepared["country"],
                    sector=prepared["sector"],
                    size_note=prepared["size_note"],
                    source=prepared["source"],
                    status="excluded" if exclusion else "candidate",
                    notes=_join_notes(lines),
                )
            except sqlite3.IntegrityError:  # the same domain was added a moment ago elsewhere
                duplicates.append(
                    {"name": name, "domain": domain, "existing_id": None, "reason": "same domain"}
                )
                continue
            if domain:
                domains[domain] = company_id
            names[norm] = company_id
            if not domain:
                names_no_domain[norm] = company_id
            entry = {
                "id": company_id,
                "name": name,
                "domain": domain,
                "status": "excluded" if exclusion else "candidate",
            }
            if exclusion:
                excluded.append({**entry, "reason": exclusion, "domain_ok": None})
            else:
                added.append(entry)
                if domain:
                    to_check.append(
                        {
                            "id": company_id,
                            "name": name,
                            "domain": domain,
                            "notes": _join_notes(lines),
                        }
                    )
            if prepared["bad_domain"]:
                failures.append(
                    {
                        "id": company_id,
                        "name": name,
                        "domain": prepared["bad_domain"],
                        "note": "not a valid domain; nothing was stored",
                    }
                )

    unchecked: list[dict[str, Any]] = []
    checked = 0
    if run_checks and to_check:
        outcome = _run_checks(to_check)
        failures += outcome["domain_failures"]
        unchecked, checked = outcome["unchecked"], outcome["checked"]
    elif to_check:
        unchecked = [{"id": r["id"], "name": r["name"]} for r in to_check]
    _fill_domain_ok(added)
    result: dict[str, Any] = {
        "ok": True,
        "added": len(added),
        "companies": added + excluded,
        "duplicates": duplicates,
        "excluded": excluded,
        "domain_failures": failures,
        "alias_warnings": alias_warnings,
        "rejected": rejected,
        "domains_checked": checked,
    }
    if unchecked:
        result["domains_not_checked"] = unchecked
        result["next_step"] = (
            "Some domains were not checked yet (time limit). Call check_domains to finish."
        )
    return result


def add_companies(campaign_id: int, companies: list[dict]) -> dict[str, Any]:
    """Clean, de-duplicate, store (as candidates) and domain-check up to 200 companies.

    Each company: `name` (required), `aliases`, `domain` (may be a URL), `sector`, `size_note`,
    `source` (ai_knowledge | search | user), `notes`, and optionally `country`. Companies that
    match the campaign's excluded names, domains or competitors are stored with status
    `excluded`. A failed domain check never blocks adding. Returns `added` (new candidates),
    `duplicates`, `excluded`, `domain_failures`, `alias_warnings` and `rejected` (bad input),
    plus `companies` (ids of what was stored). Raises `CompanyError` / `CampaignError`.
    """
    if not isinstance(companies, list) or not companies:
        raise CompanyError("invalid_value", "Give a non-empty list of companies.")
    if len(companies) > MAX_BATCH:
        raise CompanyError(
            "too_many",
            f"Add at most {MAX_BATCH} companies per call (got {len(companies)}).",
            "Split the list into smaller calls.",
        )
    return _add(campaign_id, companies, run_checks=True)


def check_domains(campaign_id: int, company_ids: list[int] | None = None) -> dict[str, Any]:
    """Check (or re-check) company domains. With no `company_ids` it checks every non-excluded
    company that has a domain and no result yet. Returns `checked`, `domain_failures`, and
    `unchecked` (call again for those)."""
    _campaign(campaign_id)
    with db.session() as conn:
        if company_ids is None:
            sql = (
                "SELECT id, name, domain, notes FROM companies WHERE campaign_id = ? "
                "AND domain <> '' AND status <> 'excluded' AND domain_ok IS NULL ORDER BY id"
            )
            rows = db.query(conn, sql, (campaign_id,))
        else:
            ids = _int_list(company_ids)
            rows = db.query(
                conn,
                "SELECT id, name, domain, notes FROM companies WHERE campaign_id = ? "
                f"AND domain <> '' AND id IN ({','.join('?' for _ in ids)}) ORDER BY id",
                [campaign_id, *ids],
            )
    return {"ok": True, **_run_checks(rows)}


# --- list, status, approve ------------------------------------------------------------------


def _int_list(values: Any) -> list[int]:
    if (
        isinstance(values, (str, bytes))
        or not isinstance(values, (list, tuple))
        or not values
        or not all(isinstance(v, int) and not isinstance(v, bool) for v in values)
    ):
        raise CompanyError("invalid_value", "ids must be a non-empty list of company ids.")
    return list(dict.fromkeys(values))


def _check_status(status: Any) -> str:
    if status not in STATUSES:
        raise CompanyError("invalid_status", f"status must be one of: {', '.join(STATUSES)}.")
    return status


def list_companies(
    campaign_id: int, status: str | None = None, page: int = 1, page_size: int = PAGE_SIZE_MAX
) -> dict[str, Any]:
    """One page of the campaign's companies (oldest first), optionally filtered by status.

    Returns `companies`, `page`, `page_size` (at most 50), `total`, `has_more` and
    `counts` (every status).
    """
    camp = _campaign(campaign_id)
    if status is not None:
        _check_status(status)
    page = max(1, int(page))
    page_size = min(max(1, int(page_size)), PAGE_SIZE_MAX)
    where, params = "campaign_id = ?", [campaign_id]
    if status is not None:
        where += " AND status = ?"
        params.append(status)
    with db.session() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM companies WHERE {where}", params).fetchone()[0]
        rows = db.query(
            conn,
            f"SELECT * FROM companies WHERE {where} ORDER BY id LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        )
        counts = {s: 0 for s in STATUSES}
        for r in db.query(
            conn,
            "SELECT status, COUNT(*) AS n FROM companies WHERE campaign_id = ? GROUP BY status",
            (campaign_id,),
        ):
            counts[r["status"]] = r["n"]
    shapes = [_shape(r) for r in rows]
    warm = contacts_import.warm_counts(camp.get("project_id"), shapes)
    for shape in shapes:
        if shape["id"] in warm:  # companies with contacts the owner imported (T24)
            shape["warm_contacts"] = warm[shape["id"]]
    return {
        "ok": True,
        "companies": shapes,
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_more": page * page_size < total,
        "counts": counts,
    }


def set_company_status(
    campaign_id: int, ids: list[int], status: str, reason: str = ""
) -> dict[str, Any]:
    """Set `candidate`, `approved` or `excluded` on companies of the campaign.

    `reason` is added to each company's notes. Returns `updated`, `not_found` (ids not in this
    campaign) and `unverified` (approved companies whose domain check failed).
    """
    _campaign(campaign_id)
    status = _check_status(status)
    id_list = _int_list(ids)
    reason = normalize_whitespace(reason or "")
    updated, missing, unverified = 0, [], []
    with db.session() as conn:
        for company_id in id_list:
            row = db.get(conn, "companies", company_id)
            if row is None or row["campaign_id"] != campaign_id:
                missing.append(company_id)
                continue
            values: dict[str, Any] = {"status": status}
            if reason:
                values["notes"] = _join_notes([row["notes"], f"Status note ({status}): {reason}"])
            db.update(conn, "companies", company_id, **values)
            updated += 1
            if status == "approved" and row["domain_ok"] == 0:
                unverified.append({"id": company_id, "name": row["name"]})
    return {"ok": True, "updated": updated, "not_found": missing, "unverified": unverified}


def approve_all(campaign_id: int) -> dict[str, Any]:
    """Approve every candidate whose domain is verified (or that has no domain) and that has no
    warnings. Returns `approved` and `skipped` (each with the reason)."""
    _campaign(campaign_id)
    approved = 0
    skipped: list[dict[str, Any]] = []
    with db.session() as conn:
        rows = db.query(
            conn,
            "SELECT * FROM companies WHERE campaign_id = ? AND status = 'candidate' ORDER BY id",
            (campaign_id,),
        )
        for row in rows:
            reasons = _warnings_in(row["notes"])
            if row["domain"] and row["domain_ok"] is None:
                reasons.append("domain not checked yet")
            elif row["domain_ok"] == 0:
                reasons.append("domain check failed")
            if reasons:
                skipped.append({"id": row["id"], "name": row["name"], "reasons": reasons})
                continue
            db.update(conn, "companies", row["id"], status="approved")
            approved += 1
    return {"ok": True, "approved": approved, "skipped": skipped}


# --- CSV import -----------------------------------------------------------------------------


def _read_csv_text(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        raise CompanyError(
            "file_not_found", f"Cannot read the file {path.name!r}.", "Check the path."
        ) from None
    if size > MAX_CSV_BYTES:
        raise CompanyError(
            "file_too_big", f"The file is larger than {MAX_CSV_BYTES // 1_000_000} MB."
        )
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252"):  # Excel on Windows often saves cp1252
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise CompanyError("unreadable_file", "The file is not UTF-8 or Windows-1252 text.")


def _cell(row: Mapping[str, Any], fields: Mapping[str, str], column: str) -> str:
    key = fields.get(column)
    return (row.get(key) or "").strip() if key else ""


def import_companies_csv(campaign_id: int, path: str) -> dict[str, Any]:
    """Add companies from a CSV with columns `company, aliases, domain, industry, notes`
    (aliases separated by `;`; `industry` becomes the sector). Rows are stored with source
    `user`. Domains are then checked until the time limit; `check_domains` finishes the rest.
    Returns what `add_companies` returns plus `rows_read` and `rows_skipped`."""
    _campaign(campaign_id)
    text = _read_csv_text(Path(path))
    reader = csv.DictReader(io.StringIO(text, newline=""))
    fields = {(f or "").strip().lower(): f for f in (reader.fieldnames or [])}
    name_col = fields.get("company") or fields.get("name")
    if not name_col:
        raise CompanyError(
            "bad_csv",
            "The CSV needs a 'company' column.",
            "Columns: company, aliases, domain, industry, notes.",
        )
    items: list[dict[str, Any]] = []
    skipped = 0
    for row in reader:
        name = (row.get(name_col) or "").strip()
        if not name:
            skipped += 1
            continue
        if len(items) >= MAX_CSV_ROWS:
            raise CompanyError(
                "too_many", f"The CSV has more than {MAX_CSV_ROWS} rows.", "Split the file."
            )
        items.append(
            {
                "name": name,
                "aliases": [a for a in _cell(row, fields, "aliases").split(";") if a.strip()],
                "domain": _cell(row, fields, "domain"),
                "sector": _cell(row, fields, "industry"),
                "notes": _cell(row, fields, "notes"),
                "source": "user",
            }
        )
    if not items:
        raise CompanyError("bad_csv", "The CSV has no company rows.")
    result = _add(campaign_id, items, run_checks=False)
    pending = result.pop("domains_not_checked", [])
    result.pop("next_step", None)
    if pending:
        with db.session() as conn:
            rows = db.query(
                conn,
                "SELECT id, name, domain, notes FROM companies WHERE id IN "
                f"({','.join('?' for _ in pending)})",
                [p["id"] for p in pending],
            )
        outcome = _run_checks(rows)
        result["domain_failures"] += outcome["domain_failures"]
        result["domains_checked"] = outcome["checked"]
        if outcome["unchecked"]:
            result["domains_not_checked"] = outcome["unchecked"]
            result["next_step"] = (
                "Some domains were not checked yet (time limit). Call check_domains to finish."
            )
        _fill_domain_ok(result["companies"])
    result["rows_read"] = len(items) + skipped
    result["rows_skipped"] = skipped
    return result


def _fill_domain_ok(entries: list[dict[str, Any]]) -> None:
    """Refresh `domain_ok` on result entries from the database."""
    if not entries:
        return
    with db.session() as conn:
        found = {
            r["id"]: r["domain_ok"]
            for r in db.query(
                conn,
                "SELECT id, domain_ok FROM companies WHERE id IN "
                f"({','.join('?' for _ in entries)})",
                [e["id"] for e in entries],
            )
        }
    for entry in entries:
        ok = found.get(entry["id"])
        entry["domain_ok"] = None if ok is None else bool(ok)
