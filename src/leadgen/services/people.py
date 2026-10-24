# Adapted from Harvey (MIT, Ethan Rogers): reference/harvey/collectors/accounts.py (find_people)
"""People finder: search Google for public LinkedIn profiles of each approved company's
designation groups and keep a person as `confirmed` only when the result proves every rule of
PLAN section 11 (`services/confirm.py`); everyone else is stored as `review` with reasons.

Only the title, link and snippet that Google returned are stored; profile pages are never
fetched. Long runs are chunked (`find_people` returns a `next_cursor`). Callers must run
`db.init_db()` first.

Projects (T23): the campaign's size limits (`max_people`, `max_companies`, `max_per_company`)
and the project's contact memory apply when a person is stored. A person the rules confirm but
who is already in another active campaign of the project (L25) or was found or contacted inside
the project's cooldown (L24) is stored as `review` with that reason; a confirmed person beyond a
limit is stored as `reserve` (kept for a later campaign, see `move_reserve`).
"""

from __future__ import annotations

import math
import sqlite3
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from leadgen import db
from leadgen.core import countries as countries_mod
from leadgen.core.config import load_settings
from leadgen.core.text import normalize_whitespace
from leadgen.providers import registry, search
from leadgen.providers.base import ProviderError
from leadgen.providers.search import SearchResult
from leadgen.services import budget, contacts_import, projects
from leadgen.services import campaign as campaign_mod
from leadgen.services import confirm as rules
from leadgen.services.seniority import LEVELS

PAGE_SIZE_MAX = 50
SEARCH_NUM = 10
CALL_TIME_LIMIT_S = 50.0
MAX_SEARCHES_PER_CALL = 30  # find_people default; ai_search.fill_from_cache uses it too
STATUSES = ("confirmed", "review", "rejected", "removed", "reserve")
MANUAL_PREFIX = "MANUAL:"
SAMPLE_SIZE = 5
_HARD_BLOCKS = ("L03", "L16")  # a person who fails these is never promoted by hand
# Reasons added by the AI-search checks (ai_search.py, spot_check.py); a re-check keeps them.
AI_SEARCH_REASON_CODES = ("L21", "L22")


class PeopleError(Exception):
    """A people request failed. `code` is stable text; `hint` tells the AI what to do.

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
        """The `{"ok": False, ...}` shape used by every tool."""
        if self.response is not None:
            return self.response
        return {
            "ok": False,
            "error": {"code": self.code, "message": self.message, "hint": self.hint},
        }


# --- queries ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Search:
    company_id: int
    company_name: str
    group_label: str
    query: str
    gl: str


def _term(term: str) -> str:
    """A search term without quotes and extra spaces (it is quoted when the query is built)."""
    return normalize_whitespace(str(term).replace('"', ""))


def _quote(term: str) -> str:
    return '"' + _term(term) + '"'


def _company_countries(
    campaign: Mapping[str, Any], company: Mapping[str, Any]
) -> list[countries_mod.Country | None]:
    """Countries to search for a company: its own, else the campaign's (None = no country)."""
    values = [company["country"]] if company.get("country") else []
    found = _known_countries(values) or _known_countries(campaign.get("countries") or [])
    return found or [None]


def _known_countries(values: Iterable[str]) -> list[countries_mod.Country]:
    found: list[countries_mod.Country] = []
    for value in values:
        try:
            found.append(countries_mod.get(value))
        except countries_mod.UnknownCountryError:
            continue
    return found


def _searches(campaign: Mapping[str, Any], company: Mapping[str, Any]) -> list[_Search]:
    out: list[_Search] = []
    for country in _company_countries(campaign, company):
        for group in campaign.get("designations") or []:
            raw_terms = (_term(t) for t in group.get("search_terms") or [])
            terms = [t for t in dict.fromkeys(raw_terms) if t]
            if not terms:
                continue
            parts = [
                "site:linkedin.com/in",
                "(" + " OR ".join(_quote(t) for t in terms) + ")",
                _quote(company["name"]),
            ]
            if country is not None:
                parts.append(country.name)
            out.append(
                _Search(
                    company_id=company.get("id") or 0,
                    company_name=company["name"],
                    group_label=group["label"],
                    query=" ".join(parts),
                    gl=country.gl if country is not None else "",
                )
            )
    return out


def build_queries(campaign: Mapping[str, Any], company: Mapping[str, Any]) -> list[tuple[str, str]]:
    """`(group_label, query)` for every designation group of the campaign (PLAN section 11):
    `site:linkedin.com/in ("term" OR "term") "Company" Country`. Groups without search terms
    are skipped; a company with no country of its own is searched in each campaign country."""
    return [(s.group_label, s.query) for s in _searches(campaign, company)]


def search_plan(
    campaign: Mapping[str, Any], company: Mapping[str, Any]
) -> list[tuple[str, str, str]]:
    """`(group_label, query, gl)` for every search the API path would run for a company row of
    the `companies` table (used by AI search mode to queue the same queries)."""
    return [(s.group_label, s.query, s.gl) for s in _searches(campaign, _company_dict(company))]


def approved_company_rows(campaign_id: int) -> list[dict[str, Any]]:
    """The campaign's approved companies (rows of the `companies` table) that a search covers:
    oldest first, or only the first `max_companies` of them (by company id, the order added)."""
    return _searched_companies(_campaign(campaign_id), None)


def company_view(row: Mapping[str, Any]) -> dict[str, Any]:
    """A `companies` row in the shape `process_results` expects."""
    return _company_dict(row)


# --- context ----------------------------------------------------------------------------------


def _campaign(campaign_id: int) -> dict[str, Any]:
    return campaign_mod.get_campaign(campaign_id)  # raises CampaignError(not_found)


def _int_list(values: Any) -> list[int]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise PeopleError("invalid_value", "Ids must be a list of whole numbers.")
    out: list[int] = []
    for v in values:
        if isinstance(v, bool) or not isinstance(v, int):
            raise PeopleError("invalid_value", "Ids must be a list of whole numbers.")
        out.append(v)
    return list(dict.fromkeys(out))


def _company_rows(
    campaign_id: int, company_ids: Sequence[int] | None = None, approved_only: bool = True
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM companies WHERE campaign_id = ?"
    params: list[Any] = [campaign_id]
    if approved_only:
        sql += " AND status = 'approved'"
    if company_ids:
        sql += f" AND id IN ({', '.join('?' for _ in company_ids)})"
        params.extend(company_ids)
    with db.session() as conn:
        rows = db.query(conn, sql + " ORDER BY id", params)
    return rows


def _limits(campaign: Mapping[str, Any]) -> tuple[int | None, int | None, int | None]:
    """`(max_people, max_companies, max_per_company)` of a campaign (None = no limit)."""
    options = campaign.get("options") or {}
    return options.get("max_people"), options.get("max_companies"), options.get("max_per_company")


def _searched_companies(
    camp: Mapping[str, Any], company_ids: Sequence[int] | None
) -> list[dict[str, Any]]:
    """The approved companies a search covers. With `max_companies` only the first c approved
    ones count (company id, the order they were added: it never changes, unlike `updated_at`),
    whatever `company_ids` asks for."""
    _n, max_companies, _p = _limits(camp)
    rows = _company_rows(camp["id"])
    if max_companies:
        rows = rows[:max_companies]  # `_company_rows` is ordered by id
    if company_ids:
        wanted = set(company_ids)
        rows = [r for r in rows if r["id"] in wanted]
    return rows


def _company_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "aliases": row.get("aliases_json") or [],
        "domain": row.get("domain") or "",
        "country": row.get("country") or "",
        "status": row.get("status") or "approved",
    }


def _company_ctx(company: Mapping[str, Any], campaign: Mapping[str, Any]) -> rules.CompanyCtx:
    own = [c.iso2 for c in _company_countries(campaign, company) if c is not None]
    narrowed = tuple(own) if company.get("country") else ()
    return rules.CompanyCtx(
        name=company["name"],
        aliases=tuple(company.get("aliases") or ()),
        domain=company.get("domain") or "",
        status=company.get("status") or "approved",
        countries=narrowed,
        id=company.get("id"),
    )


def _suppressed_values() -> list[str]:
    with db.session() as conn:
        rows = db.query(conn, "SELECT value FROM suppression WHERE kind = 'linkedin'")
    return [r["value"] for r in rows]


def _campaign_ctx(campaign: Mapping[str, Any]) -> rules.CampaignCtx:
    return rules.CampaignCtx.from_campaign(campaign, _suppressed_values())


def _result_to_obj(item: Any, position: int) -> SearchResult:
    if isinstance(item, SearchResult):
        return item
    return SearchResult(
        title=str(item.get("title") or ""),
        link=str(item.get("link") or item.get("url") or ""),
        snippet=str(item.get("snippet") or ""),
        position=int(item.get("position") or position),
    )


# --- storing ----------------------------------------------------------------------------------


def _person_shape(row: Mapping[str, Any], company_name: str = "") -> dict[str, Any]:
    return {
        "id": row["id"],
        "company_id": row["company_id"],
        "company": company_name,
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "greeting_name": row["greeting_name"],
        "raw_name": row["raw_name"],
        "title": row["title"],
        "designation": row["designation"],
        "seniority": row["seniority"],
        "country": row["country"],
        "linkedin_url": row["linkedin_url"],
        "evidence_title": row["evidence_title"],
        "evidence_snippet": row["evidence_snippet"],
        "source_query": row["source_query"],
        "evidence_origin": row["evidence_origin"],
        "spot_check": row["spot_check"],
        "confirmed": bool(row["confirmed"]),
        "reasons": row["reasons_json"] or [],
        "status": row["status"],
    }


person_shape = _person_shape  # public name for the dashboard views (services/browse.py)


def _brief(cand: rules.Candidate, company_name: str) -> dict[str, Any]:
    return {
        "name": cand.raw_name,
        "title": cand.headline,
        "company": company_name,
        "designation": cand.designation,
        "linkedin_url": cand.linkedin_url,
        "reasons": cand.reasons,
    }


def _note(
    outcomes: list[dict[str, Any]] | None, position: int, url: str, outcome: str, reasons: list[str]
) -> None:
    if outcomes is not None:
        outcomes.append(
            {"index": position - 1, "url": url, "outcome": outcome, "reasons": list(reasons)}
        )


@dataclass
class _Entry:
    """One parsed result waiting for its status (see `process_results`)."""

    position: int
    obj: SearchResult
    cand: rules.Candidate
    status: str = "review"


class _ProjectScreen(projects.ContactScreen):
    """`projects.ContactScreen` for a campaign dict; `reasons` gives `Lnn:text` strings."""

    def __init__(self, campaign: Mapping[str, Any], also_exclude: Sequence[int] = ()) -> None:
        super().__init__(campaign.get("project_id"), campaign["id"], also_exclude)

    def reasons(  # type: ignore[override]
        self, keys: Mapping[str, str], below_person_id: int | None = None
    ) -> list[str]:
        return [f"{code}:{text}" for code, text in self.findings(keys, below_person_id)]


class _Capacity:
    """How many more confirmed people the campaign and each company can take."""

    def __init__(self, campaign: Mapping[str, Any]) -> None:
        self.max_people, _c, self.max_per_company = _limits(campaign)
        self.total = 0
        self.per_company: dict[int, int] = {}
        if self.max_people or self.max_per_company:
            with db.session() as conn:
                rows = db.query(
                    conn,
                    "SELECT company_id, COUNT(*) AS n FROM people "
                    "WHERE campaign_id = ? AND status = 'confirmed' GROUP BY company_id",
                    (campaign["id"],),
                )
            self.per_company = {r["company_id"]: r["n"] for r in rows}
            self.total = sum(self.per_company.values())

    def take(self, company_id: int) -> str:
        """`confirmed` (and use the room) or `reserve` when a limit is reached."""
        if self.max_people and self.total >= self.max_people:
            return "reserve"
        if self.max_per_company and self.per_company.get(company_id, 0) >= self.max_per_company:
            return "reserve"
        self.total += 1
        self.per_company[company_id] = self.per_company.get(company_id, 0) + 1
        return "confirmed"


def _rank(campaign: Mapping[str, Any], designation: str, seniority: str) -> tuple[int, int]:
    """Sort key: designation group order (first group first), then seniority (highest first)."""
    labels = [g["label"] for g in campaign.get("designations") or []]
    group = labels.index(designation) if designation in labels else len(labels)
    level = LEVELS.index(seniority) if seniority in LEVELS else -1
    return group, -level


def _candidate_keys(cand: rules.Candidate, company_name: str) -> dict[str, str]:
    return projects.make_keys(
        {
            "linkedin_url": cand.linkedin_url, "first_name": cand.first_name,
            "last_name": cand.last_name, "raw_name": cand.raw_name, "company": company_name,
        }
    )  # fmt: skip


def process_results(
    campaign: Mapping[str, Any],
    company: Mapping[str, Any],
    group_label: str,
    query: str,
    results: Sequence[SearchResult | Mapping[str, Any]],
    origin: str = "api",
    screen: Callable[[rules.Candidate, SearchResult], list[str]] | None = None,
    outcomes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Parse and store the results of one search (`campaign`/`company` are the dicts returned by
    `campaign.get_campaign` and `companies.list_companies`). The same function handles results
    that were not fetched by an API (AI-submitted ones, origin `ai_web_search`), with the same
    rules. Returns counts (`confirmed`, `review`, `reserve`, `duplicates`, `ignored`) and short
    samples.

    `screen(candidate, result)` may return extra `Lnn:text` reasons (AI search uses it for its
    anti-fabrication checks); any reason sends the person to `review`. `outcomes`, when given,
    receives one `{index, url, outcome, reasons}` per result (`confirmed`, `review`, `reserve`,
    `duplicate` or `ignored`).

    Project rules (campaigns inside a project): a confirmed person who is in another active
    campaign of the project (L25) or was found or contacted inside the cooldown (L24) goes to
    `review`; the others are kept as `confirmed` until the campaign's `max_people` or
    `max_per_company` is reached, then stored as `reserve`. Within one batch the people who
    rank first (campaign group order, then seniority, then position) get the room first.
    Everyone newly `confirmed` is written to the project's contact log (`found`)."""
    camp_ctx = _campaign_ctx(campaign)
    comp_ctx = _company_ctx(company, campaign)
    stats = {"confirmed": 0, "review": 0, "reserve": 0, "duplicates": 0, "ignored": 0}
    samples: dict[str, list[dict[str, Any]]] = {"confirmed": [], "review": []}
    notes: list[tuple[int, str, str, list[str]]] = []  # (position, url, outcome, reasons)
    with db.session() as conn:
        existing = db.query(
            conn,
            "SELECT linkedin_url, raw_name, company_id FROM people WHERE campaign_id = ?",
            (campaign["id"],),
        )
        urls = {rules.url_key(r["linkedin_url"]) for r in existing}
        names = {(r["company_id"], rules.name_key(r["raw_name"])) for r in existing}
        entries: list[_Entry] = []
        for position, item in enumerate(results, start=1):
            obj = _result_to_obj(item, position)
            cand = rules.parse_result(obj, comp_ctx, camp_ctx)
            if cand is None:
                stats["ignored"] += 1
                notes.append((position, obj.link, "ignored", ["L01:not a personal profile link"]))
                continue
            if screen is not None:
                cand.reasons.extend(r for r in screen(cand, obj) if r not in cand.reasons)
            key = rules.url_key(cand.linkedin_url)
            name_key = (company["id"], rules.name_key(cand.raw_name))
            if key in urls or (cand.raw_name and name_key in names):
                stats["duplicates"] += 1
                notes.append((position, obj.link, "duplicate", cand.reasons))
                continue
            urls.add(key)
            names.add(name_key)
            entries.append(_Entry(position, obj, cand))

        confirmed = [e for e in entries if e.cand.confirmed]
        if confirmed:
            project_screen = _ProjectScreen(campaign)
            capacity = _Capacity(campaign)
            confirmed.sort(
                key=lambda e: (*_rank(campaign, e.cand.designation, e.cand.seniority), e.position)
            )
            for entry in confirmed:
                extra = project_screen.reasons(_candidate_keys(entry.cand, company["name"]))
                if extra:
                    entry.cand.reasons.extend(extra)
                else:
                    entry.status = capacity.take(company["id"])

        found: list[dict[str, Any]] = []
        for entry in entries:
            cand, status = entry.cand, entry.status
            try:
                person_id = db.insert(
                    conn,
                    "people",
                    campaign_id=campaign["id"],
                    company_id=company["id"],
                    first_name=cand.first_name,
                    last_name=cand.last_name,
                    greeting_name=cand.greeting_name,
                    raw_name=cand.raw_name,
                    title=cand.headline,
                    designation=cand.designation,
                    seniority=cand.seniority,
                    country=cand.country,
                    linkedin_url=cand.linkedin_url,
                    evidence_title=cand.evidence_title,
                    evidence_snippet=cand.evidence_snippet,
                    source_query=query,
                    evidence_origin=origin,
                    confirmed=int(cand.confirmed),
                    reasons_json=cand.reasons,
                    status=status,
                )
            except sqlite3.IntegrityError:
                stats["duplicates"] += 1
                notes.append((entry.position, entry.obj.link, "duplicate", cand.reasons))
                continue
            stats[status] += 1
            notes.append((entry.position, entry.obj.link, status, cand.reasons))
            if status == "confirmed":
                found.append({**_candidate_keys(cand, company["name"]), "person_id": person_id})
            if status in samples and len(samples[status]) < SAMPLE_SIZE:
                samples[status].append(_brief(cand, company["name"]))
        if found and campaign.get("project_id") is not None:
            projects.log_found_in(conn, campaign["project_id"], campaign["id"], found)
    for position, url, outcome, reasons in sorted(notes, key=lambda n: n[0]):
        _note(outcomes, position, url, outcome, reasons)
    return {**stats, "group": group_label, "samples": samples}


# --- estimate and find ------------------------------------------------------------------------


def _provider_key() -> str:
    try:
        return registry.get_search_provider().info.key
    except ProviderError:
        return "serper"


def _registry_key(key: str) -> str:
    return key if key in registry.ALL_PROVIDERS else "serper"


def _all_searches(camp: Mapping[str, Any], companies: Sequence[Mapping[str, Any]]) -> list[_Search]:
    return [s for row in companies for s in _searches(camp, _company_dict(row))]


def estimate_people_search(
    campaign_id: int, company_ids: Sequence[int] | None = None
) -> dict[str, Any]:
    """How many searches a people search needs: `companies`, `searches`, `cached` (free),
    `credits_needed` (one per uncached search) and `budget_left` for the campaign."""
    camp = _campaign(campaign_id)
    ids = _int_list(company_ids)
    plan = _all_searches(camp, _searched_companies(camp, ids))
    pkey = _provider_key()
    cached = sum(1 for s in plan if search.is_cached(pkey, s.query, s.gl, SEARCH_NUM))
    needed = len(plan) - cached
    left = budget.limit(camp["budget"], "search") - budget.used(campaign_id, "search")
    threshold = load_settings().confirm_threshold_credits
    return {
        "ok": True,
        "companies": len({s.company_id for s in plan}),
        "searches": len(plan),
        "cached": cached,
        "credits_needed": needed,
        "budget_left": left,
        "confirm_threshold": threshold,
        "needs_confirmation": needed > threshold,
    }


def _counts(campaign_id: int) -> dict[str, int]:
    with db.session() as conn:
        rows = db.query(
            conn,
            "SELECT status, COUNT(*) AS n FROM people WHERE campaign_id = ? GROUP BY status",
            (campaign_id,),
        )
    found = {r["status"]: r["n"] for r in rows}
    return {s: found.get(s, 0) for s in STATUSES}


def _people_limit_reached(camp: Mapping[str, Any]) -> bool:
    """True when the campaign has `max_people` confirmed people (nothing more to search for)."""
    limit = _limits(camp)[0]
    return bool(limit) and _counts(camp["id"])["confirmed"] >= limit


def _limit_message(camp: Mapping[str, Any]) -> str:
    return (
        f"Limit reached: the campaign has its {_limits(camp)[0]} confirmed people "
        "(max_people), so searching stopped. Extra confirmed people are kept as reserve for "
        "a later campaign (move_reserve_people)."
    )


def _cursor(value: str | int | None) -> int:
    if value in (None, ""):
        return 0
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = -1
    if n < 0:
        raise PeopleError("invalid_cursor", "The cursor is not valid.", "Start again without one.")
    return n


def find_people(
    campaign_id: int,
    company_ids: Sequence[int] | None = None,
    confirm: bool = False,
    cursor: str | int | None = None,
    max_searches_per_call: int = MAX_SEARCHES_PER_CALL,
    provider: Any = None,
) -> dict[str, Any]:
    """Search every approved company x designation group and store what is found.

    Pass `confirm=true` after telling the user the cost: without it a run that needs more
    credits than `settings.confirm_threshold_credits`, or more than the campaign's search
    budget, returns the estimate (`needs_confirmation`) or the `budget_reached` reply and
    searches nothing. Each call runs at most `max_searches_per_call` searches (and about
    50 seconds) and returns `next_cursor` until done. Cached searches cost nothing.
    Missing keys or no credits give the structured `needs_key` / `quota_exhausted` reply
    (with the progress made so far). `provider` lets tests pass a fake.

    Campaign limits: only the first `max_companies` approved companies are searched, and the
    search stops (`done`, `limit_reached`) once `max_people` people are confirmed; confirmed
    people beyond `max_per_company` (or `max_people`) are stored as `reserve`.
    """
    camp = _campaign(campaign_id)
    if camp["options"].get("search_mode") == "ai":
        message = "This campaign uses AI search mode, so the tool does not run searches itself."
        hint = "Use get_search_tasks and submit_search_results, or set search_mode to auto or api."
        raise PeopleError(
            "ai_search_mode",
            message,
            hint,
            response={
                "ok": False,
                "use_ai_search": True,
                "hint": "call get_search_tasks",
                "error": {"code": "ai_search_mode", "message": message, "hint": hint},
            },
        )
    ids = _int_list(company_ids)
    start = _cursor(cursor)
    max_searches = min(max(1, int(max_searches_per_call)), 100)
    if _people_limit_reached(camp):
        return {
            "ok": True,
            "searches_done": 0,
            "searches_total": 0,
            "cache_hits": 0,
            "credits_used": 0.0,
            "this_call": dict.fromkeys(
                ("confirmed", "review", "reserve", "duplicates", "ignored"), 0
            ),
            "campaign_people": _counts(campaign_id),
            "sample_confirmed": [],
            "sample_review": [],
            "done": True,
            "next_cursor": None,
            "limit_reached": True,
            "message": _limit_message(camp),
        }
    rows = _searched_companies(camp, ids)
    skipped = sorted(set(ids) - {r["id"] for r in rows})
    plan = _all_searches(camp, rows)
    by_id = {r["id"]: _company_dict(r) for r in rows}

    try:
        inner = provider if provider is not None else registry.get_search_provider()
    except ProviderError as exc:
        raise PeopleError(
            exc.code,
            exc.message,
            response=registry.error_response("serper", exc, "to search for people"),
        ) from None
    pkey = inner.info.key

    if start == 0:
        cached = sum(1 for s in plan if search.is_cached(pkey, s.query, s.gl, SEARCH_NUM))
        needed = len(plan) - cached
        threshold = load_settings().confirm_threshold_credits
        if needed > threshold and not confirm:
            return {
                "ok": True,
                "needs_confirmation": True,
                "companies": len(by_id),
                "searches": len(plan),
                "cached": cached,
                "credits_needed": needed,
                "budget_left": budget.limit(camp["budget"], "search")
                - budget.used(campaign_id, "search"),
                "message": f"This needs about {needed} search credits. Ask the user, then "
                "repeat the call with confirm=true.",
            }

    totals = {"confirmed": 0, "review": 0, "reserve": 0, "duplicates": 0, "ignored": 0}
    samples: dict[str, list[dict[str, Any]]] = {"confirmed": [], "review": []}
    limit_hit = False
    done = 0
    cache_hits = 0
    credits = 0.0
    stop: dict[str, Any] | None = None
    position = start
    began = time.monotonic()
    while position < len(plan) and done < max_searches:
        if done and time.monotonic() - began > CALL_TIME_LIMIT_S:
            break
        if done and _people_limit_reached(camp):
            limit_hit = True
            break
        item = plan[position]
        cached_already = search.is_cached(pkey, item.query, item.gl, SEARCH_NUM)
        if not cached_already:
            blocked = budget.check(campaign_id, camp["budget"], "search", 1, confirm)
            if blocked is not None:
                stop = blocked
                break
        try:
            results = search.cached_search(inner, item.query, item.gl, SEARCH_NUM)
        except ProviderError as exc:
            stop = registry.error_response(
                _registry_key(inner.info.key), exc, "to search for people"
            )
            break
        if cached_already:
            cache_hits += 1
        else:
            credits += float(max(1, math.ceil(SEARCH_NUM / 10)))
            budget.charge(campaign_id, "search", 1)
        out = process_results(
            camp, by_id[item.company_id], item.group_label, item.query, results, "api"
        )
        for key in totals:
            totals[key] += out[key]
        for status in samples:
            room = SAMPLE_SIZE - len(samples[status])
            samples[status].extend(out["samples"][status][:room])
        position += 1
        done += 1
    if not limit_hit and stop is None and position < len(plan) and done >= max_searches:
        limit_hit = _people_limit_reached(camp)

    finished = (position >= len(plan) or limit_hit) and stop is None
    reply: dict[str, Any] = {
        **(stop if stop is not None else {"ok": True}),
        "searches_done": done,
        "searches_total": len(plan),
        "cache_hits": cache_hits,
        "credits_used": credits,
        "this_call": totals,
        "campaign_people": _counts(campaign_id),
        "sample_confirmed": samples["confirmed"],
        "sample_review": samples["review"],
        "done": finished,
        "next_cursor": None if finished else str(position),
    }
    if limit_hit:
        reply["limit_reached"] = True
        reply["message"] = _limit_message(camp)
    if skipped:
        reply["skipped_company_ids"] = skipped
    return reply


# --- reading and changing ---------------------------------------------------------------------


def list_people(
    campaign_id: int,
    status: str | None = None,
    designation: str | None = None,
    company_id: int | None = None,
    has_email: bool | None = None,
    page: int = 1,
    page_size: int = PAGE_SIZE_MAX,
) -> dict[str, Any]:
    """One page of the campaign's people (oldest first) with optional filters.

    Returns `people`, `page`, `page_size` (at most 50), `total`, `has_more` and `counts` by status.
    """
    camp = _campaign(campaign_id)
    if status is not None and status not in STATUSES:
        raise PeopleError("invalid_value", f"status must be one of: {', '.join(STATUSES)}.")
    page = max(1, int(page))
    page_size = min(max(1, int(page_size)), PAGE_SIZE_MAX)
    where, params = ["p.campaign_id = ?"], [campaign_id]
    if status is not None:
        where.append("p.status = ?")
        params.append(status)
    if designation:
        where.append("p.designation = ?")
        params.append(designation)
    if company_id is not None:
        where.append("p.company_id = ?")
        params.append(company_id)
    if has_email is True:
        where.append("EXISTS (SELECT 1 FROM emails e WHERE e.person_id = p.id)")
    elif has_email is False:
        where.append("NOT EXISTS (SELECT 1 FROM emails e WHERE e.person_id = p.id)")
    clause = " AND ".join(where)
    with db.session() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM people p WHERE {clause}", params).fetchone()[0]
        rows = db.query(
            conn,
            "SELECT p.*, c.name AS company_name FROM people p "
            "JOIN companies c ON c.id = p.company_id "
            f"WHERE {clause} ORDER BY p.id LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        )
    shapes = [_person_shape(r, r["company_name"]) for r in rows]
    known = contacts_import.known_for_people(camp.get("project_id"), [r["id"] for r in rows])
    for shape in shapes:
        if shape["id"] in known:
            shape["known_contact"] = known[shape["id"]]
    return {
        "ok": True,
        "people": shapes,
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_more": page * page_size < total,
        "counts": _counts(campaign_id),
    }


def set_person_status(
    campaign_id: int, ids: Sequence[int], status: str, reason: str = ""
) -> dict[str, Any]:
    """Set `confirmed`, `review`, `reserve`, `rejected` or `removed` on people of the campaign.

    Moving someone to `confirmed` by hand needs a `reason` (it is stored with the person as a
    `MANUAL:` entry in `reasons`, and `recheck_people` leaves such people alone). People who fail
    the excluded-company (L03) or suppression (L16) rule cannot be promoted. Returns `updated`,
    `not_found` and `refused`.
    """
    camp = _campaign(campaign_id)
    if status not in STATUSES:
        raise PeopleError("invalid_value", f"status must be one of: {', '.join(STATUSES)}.")
    id_list = _int_list(ids)
    reason = normalize_whitespace(reason or "")
    if status == "confirmed" and not reason:
        raise PeopleError(
            "reason_required",
            "Moving a person to confirmed by hand needs a reason.",
            "Say why the evidence is good enough, e.g. 'checked the profile'.",
        )
    updated, missing, refused = [], [], []
    to_log: list[dict[str, Any]] = []
    to_forget: list[int] = []
    with db.session() as conn:
        for person_id in id_list:
            row = db.get(conn, "people", person_id)
            if row is None or row["campaign_id"] != campaign_id:
                missing.append(person_id)
                continue
            reasons = list(row["reasons_json"] or [])
            if status == "confirmed" and row["status"] != "confirmed":
                blocked = [r for r in reasons if r.split(":", 1)[0] in _HARD_BLOCKS]
                if blocked:
                    refused.append({"id": person_id, "why": blocked[0]})
                    continue
            if reason:
                reasons.append(f"{MANUAL_PREFIX}{status}: {reason}")
            db.update(
                conn,
                "people",
                person_id,
                status=status,
                confirmed=int(status in ("confirmed", "reserve")),
                reasons_json=reasons,
            )
            updated.append(person_id)
            if status == "confirmed" and row["status"] != "confirmed":
                company = db.get(conn, "companies", row["company_id"]) or {}
                to_log.append({**row, "company": company.get("name", ""), "person_id": person_id})
            elif status != "confirmed" and row["status"] == "confirmed":
                to_forget.append(person_id)
        projects.forget_found(conn, to_forget)
        if to_log and camp.get("project_id") is not None:
            projects.log_found_in(conn, camp["project_id"], campaign_id, to_log)
    return {"ok": True, "updated": len(updated), "ids": updated, "not_found": missing,
            "refused": refused}  # fmt: skip


def _limit_arg(name: str, value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10_000:
        raise PeopleError("invalid_value", f"{name} must be a whole number from 1 to 10000.")
    return value


def _smallest(*values: int | None) -> int | None:
    given = [v for v in values if v]
    return min(given) if given else None


def _target_company(
    conn: sqlite3.Connection, target_id: int, source: Mapping[str, Any]
) -> int:
    """The target campaign's company for a source company (same domain, else same name); a new
    approved copy is created when there is none."""
    rows = db.query(conn, "SELECT * FROM companies WHERE campaign_id = ?", (target_id,))
    domain = str(source["domain"] or "")
    for row in rows:
        if domain and row["domain"] == domain:
            return row["id"]
    for row in rows:
        if row["name"].casefold() == str(source["name"]).casefold():
            return row["id"]
    return db.insert(
        conn,
        "companies",
        campaign_id=target_id,
        name=source["name"],
        aliases_json=source["aliases_json"] or [],
        domain=domain,
        country=source["country"],
        sector=source["sector"],
        size_note=source["size_note"],
        source=f"moved from campaign {source['campaign_id']}",
        domain_ok=source["domain_ok"],
        status="approved",
    )


def move_reserve(
    from_campaign_id: int,
    to_campaign_id: int,
    max_per_company: int | None = None,
    max_people: int | None = None,
) -> dict[str, Any]:
    """Move `reserve` people of one campaign into another campaign of the same project.

    They become `confirmed` in the target (best ranked first: the target's group order, then
    seniority, then the order they were found) while the target's limits hold: its own
    `max_people`, `max_companies` and `max_per_company`, and the stricter of those and the
    `max_people` / `max_per_company` given here. A person is left in reserve when the target's
    rules no longer confirm them (the stored evidence is judged again), when they are already in
    the target, or when they were contacted or sit in a third campaign of the project (L24, L25).
    Returns `moved`, `moved_ids`, `left_in_reserve` and why (`limit_reached`, `not_matching`,
    `duplicates`, `already_contacted`). Raises `PeopleError`.
    """
    if from_campaign_id == to_campaign_id:
        raise PeopleError("invalid_value", "Pick two different campaigns.")
    source, target = _campaign(from_campaign_id), _campaign(to_campaign_id)
    if source.get("project_id") is None or source["project_id"] != target.get("project_id"):
        raise PeopleError(
            "different_project",
            "Reserve people can only move between campaigns of the same project.",
            "Check both campaigns with get_campaign.",
        )
    if target["status"] == "archived":
        raise PeopleError("campaign_archived", "The target campaign is archived.")
    per_company = _smallest(_limit_arg("max_per_company", max_per_company), _limits(target)[2])
    total_limit = _smallest(_limit_arg("max_people", max_people), _limits(target)[0])
    max_companies = _limits(target)[1]

    camp_ctx = _campaign_ctx(target)
    screen = _ProjectScreen(target, also_exclude=[from_campaign_id])
    moved: list[int] = []
    why = {"limit_reached": 0, "not_matching": 0, "duplicates": 0, "already_contacted": 0}
    with db.session() as conn:
        reserve = db.query(
            conn,
            "SELECT * FROM people WHERE campaign_id = ? AND status = 'reserve' ORDER BY id",
            (from_campaign_id,),
        )
        reserve.sort(key=lambda r: (*_rank(target, r["designation"], r["seniority"]), r["id"]))
        existing = db.query(
            conn,
            "SELECT linkedin_url, company_id, status FROM people WHERE campaign_id = ?",
            (to_campaign_id,),
        )
        urls = {rules.url_key(r["linkedin_url"]) for r in existing}
        per: dict[int, int] = {}
        for r in existing:
            if r["status"] == "confirmed":
                per[r["company_id"]] = per.get(r["company_id"], 0) + 1
        total = sum(per.values())
        found: list[dict[str, Any]] = []
        for row in reserve:
            key = rules.url_key(row["linkedin_url"])
            if key in urls:
                why["duplicates"] += 1
                continue
            if total_limit and total >= total_limit:
                why["limit_reached"] += 1
                continue
            source_company = db.get(conn, "companies", row["company_id"]) or {}
            company = _company_dict({**source_company, "id": None, "status": "approved"})
            result = SearchResult(
                title=row["evidence_title"], link=row["linkedin_url"],
                snippet=row["evidence_snippet"], position=0,
            )  # fmt: skip
            cand = rules.parse_result(result, _company_ctx(company, target), camp_ctx)
            if cand is None or not cand.confirmed:
                why["not_matching"] += 1
                continue
            if screen.reasons(_candidate_keys(cand, company["name"])):
                why["already_contacted"] += 1
                continue
            known = db.query(
                conn,
                "SELECT id, domain, name FROM companies WHERE campaign_id = ?",
                (to_campaign_id,),
            )
            match = next(
                (
                    c["id"] for c in known
                    if (company["domain"] and c["domain"] == company["domain"])
                    or c["name"].casefold() == company["name"].casefold()
                ),
                None,
            )  # fmt: skip
            if match is None and max_companies and len(per) >= max_companies:
                why["limit_reached"] += 1
                continue
            if match is not None and per_company and per.get(match, 0) >= per_company:
                why["limit_reached"] += 1
                continue
            company_id = _target_company(
                conn, to_campaign_id, {**source_company, "campaign_id": from_campaign_id}
            )
            db.update(
                conn, "people", row["id"],
                campaign_id=to_campaign_id, company_id=company_id, status="confirmed",
                confirmed=1, first_name=cand.first_name, last_name=cand.last_name,
                greeting_name=cand.greeting_name, raw_name=cand.raw_name, title=cand.headline,
                designation=cand.designation, seniority=cand.seniority, country=cand.country,
                reasons_json=[],
            )  # fmt: skip
            urls.add(key)
            total += 1
            per[company_id] = per.get(company_id, 0) + 1
            moved.append(row["id"])
            found.append({**_candidate_keys(cand, company["name"]), "person_id": row["id"]})
        if found:
            projects.log_found_in(conn, target["project_id"], to_campaign_id, found)
    left = len(reserve) - len(moved)
    return {
        "ok": True,
        "from_campaign_id": from_campaign_id,
        "to_campaign_id": to_campaign_id,
        "moved": len(moved),
        "moved_ids": moved,
        "left_in_reserve": left,
        **why,
        "message": f"Moved {len(moved)} reserve people; {left} stay in reserve.",
    }


def create_campaign_taking_reserve(
    take_reserve_from: int | None, **campaign_args: Any
) -> dict[str, Any]:
    """Create a campaign (`campaign.create_campaign` arguments) and, when `take_reserve_from` is a
    campaign of the same project, move that campaign's reserve people into it at once (see
    `move_reserve`; the new campaign's own limits apply).

    Returns `{"ok": True, "campaign": ..., "reserve": <move_reserve reply or None>}`. The other
    campaign is checked before anything is created. Raises `PeopleError` / `CampaignError`.
    """
    if take_reserve_from is not None:
        other = _campaign(take_reserve_from)
        if other.get("project_id") is None or other["project_id"] != campaign_args.get(
            "project_id"
        ):
            raise PeopleError(
                "different_project",
                "Reserve people can only move between campaigns of the same project.",
                "Pick a campaign from the same project.",
            )
    created = campaign_mod.create_campaign(**campaign_args)
    moved = None
    if take_reserve_from is not None:
        moved = move_reserve(take_reserve_from, created["id"])
        created = campaign_mod.get_campaign(created["id"])
    return {"ok": True, "campaign": created, "reserve": moved}


def _is_manual(reasons: Iterable[str]) -> bool:
    return any(r.startswith(MANUAL_PREFIX) for r in reasons)


def _ai_reasons(row: Mapping[str, Any]) -> list[str]:
    """The AI-search reasons (L21, L22) stored on a person whose evidence came from AI search."""
    if row["evidence_origin"] != "ai_web_search":
        return []
    return [
        r for r in row["reasons_json"] or [] if r.split(":", 1)[0] in AI_SEARCH_REASON_CODES
    ]


def recheck_people(campaign_id: int) -> dict[str, Any]:
    """Run the current rules again over the stored evidence of every confirmed or review person.

    Use it after the campaign's countries, designations or seniority changed, or after the rules
    were improved. People move between `confirmed` and `review`; their reasons are refreshed.
    People changed by hand (`MANUAL:` entries), rejected and removed people are left alone.
    Returns `checked`, `unchanged`, `kept_manual`, `to_confirmed`, `to_review` and `refreshed`.
    """
    camp = _campaign(campaign_id)
    camp_ctx = _campaign_ctx(camp)
    companies = {r["id"]: _company_dict(r) for r in _company_rows(campaign_id, None, False)}
    project_screen = _ProjectScreen(camp)
    capacity = _Capacity(camp)
    to_log: list[dict[str, Any]] = []
    to_forget: list[int] = []
    to_confirmed: list[dict[str, Any]] = []
    to_review: list[dict[str, Any]] = []
    unchanged = refreshed = manual = checked = 0
    with db.session() as conn:
        rows = db.query(
            conn,
            "SELECT * FROM people WHERE campaign_id = ? AND status IN ('confirmed', 'review') "
            "ORDER BY id",
            (campaign_id,),
        )
        for row in rows:
            if _is_manual(row["reasons_json"] or []):
                manual += 1
                continue
            checked += 1
            company = companies[row["company_id"]]
            result = SearchResult(
                title=row["evidence_title"], link=row["linkedin_url"],
                snippet=row["evidence_snippet"], position=0,
            )  # fmt: skip
            cand = rules.parse_result(result, _company_ctx(company, camp), camp_ctx)
            kept = _ai_reasons(row)  # anti-fabrication / spot-check findings are not re-derived
            if cand is None:
                reasons = ["L01:not a personal LinkedIn profile link", *kept]
                values: dict[str, Any] = {"reasons_json": reasons, "confirmed": 0}
                new_status = "review"
            else:
                cand.reasons.extend(r for r in kept if r not in cand.reasons)
                if cand.confirmed:  # the project rules (L24, L25) are judged afresh
                    cand.reasons.extend(
                        project_screen.reasons(
                            _candidate_keys(cand, company["name"]), below_person_id=row["id"]
                        )
                    )
                new_status = "confirmed" if cand.confirmed else "review"
                if new_status == "confirmed" and row["status"] != "confirmed":
                    new_status = capacity.take(row["company_id"])
                values = {
                    "first_name": cand.first_name,
                    "last_name": cand.last_name,
                    "greeting_name": cand.greeting_name,
                    "raw_name": cand.raw_name,
                    "title": cand.headline,
                    "designation": cand.designation,
                    "seniority": cand.seniority,
                    "country": cand.country,
                    "confirmed": int(cand.confirmed),
                    "reasons_json": cand.reasons,
                }
            changed = any(row[k] != v for k, v in values.items())
            if new_status != row["status"]:
                brief = {
                    "id": row["id"], "name": row["raw_name"], "reasons": values["reasons_json"],
                }  # fmt: skip
                if new_status == "confirmed":
                    to_confirmed.append(brief)
                    to_log.append(
                        {**row, **values, "company": company["name"], "person_id": row["id"]}
                    )
                elif new_status == "review":
                    to_review.append(brief)
                    to_forget.append(row["id"])
            elif changed:
                refreshed += 1
            else:
                unchanged += 1
                continue
            db.update(conn, "people", row["id"], status=new_status, **values)
        projects.forget_found(conn, to_forget)
        if to_log and camp.get("project_id") is not None:
            projects.log_found_in(conn, camp["project_id"], campaign_id, to_log)
    return {
        "ok": True,
        "checked": checked,
        "unchanged": unchanged,
        "refreshed": refreshed,
        "kept_manual": manual,
        "to_confirmed": to_confirmed,
        "to_review": to_review,
    }
