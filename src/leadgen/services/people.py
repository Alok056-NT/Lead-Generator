# Adapted from Harvey (MIT, Ethan Rogers): reference/harvey/collectors/accounts.py (find_people)
"""People finder: search Google for public LinkedIn profiles of each approved company's
designation groups and keep a person as `confirmed` only when the result proves every rule of
PLAN section 11 (`services/confirm.py`); everyone else is stored as `review` with reasons.

Only the title, link and snippet that Google returned are stored; profile pages are never
fetched. Long runs are chunked (`find_people` returns a `next_cursor`). Callers must run
`db.init_db()` first.
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
from leadgen.services import budget
from leadgen.services import campaign as campaign_mod
from leadgen.services import confirm as rules

PAGE_SIZE_MAX = 50
SEARCH_NUM = 10
CALL_TIME_LIMIT_S = 50.0
STATUSES = ("confirmed", "review", "rejected", "removed")
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
    """The campaign's approved companies (rows of the `companies` table), oldest first."""
    return _company_rows(campaign_id)


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
    rules. Returns counts (`confirmed`, `review`, `duplicates`, `ignored`) and short samples.

    `screen(candidate, result)` may return extra `Lnn:text` reasons (AI search uses it for its
    anti-fabrication checks); any reason sends the person to `review`. `outcomes`, when given,
    receives one `{index, url, outcome, reasons}` per result (`confirmed`, `review`, `duplicate`
    or `ignored`)."""
    camp_ctx = _campaign_ctx(campaign)
    comp_ctx = _company_ctx(company, campaign)
    stats = {"confirmed": 0, "review": 0, "duplicates": 0, "ignored": 0}
    samples: dict[str, list[dict[str, Any]]] = {"confirmed": [], "review": []}
    with db.session() as conn:
        existing = db.query(
            conn,
            "SELECT linkedin_url, raw_name, company_id FROM people WHERE campaign_id = ?",
            (campaign["id"],),
        )
        urls = {rules.url_key(r["linkedin_url"]) for r in existing}
        names = {(r["company_id"], rules.name_key(r["raw_name"])) for r in existing}
        for position, item in enumerate(results, start=1):
            obj = _result_to_obj(item, position)
            cand = rules.parse_result(obj, comp_ctx, camp_ctx)
            if cand is None:
                stats["ignored"] += 1
                _note(outcomes, position, obj.link, "ignored", ["L01:not a personal profile link"])
                continue
            if screen is not None:
                cand.reasons.extend(r for r in screen(cand, obj) if r not in cand.reasons)
            key = rules.url_key(cand.linkedin_url)
            name_key = (company["id"], rules.name_key(cand.raw_name))
            if key in urls or (cand.raw_name and name_key in names):
                stats["duplicates"] += 1
                _note(outcomes, position, obj.link, "duplicate", cand.reasons)
                continue
            status = "confirmed" if cand.confirmed else "review"
            try:
                db.insert(
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
                _note(outcomes, position, obj.link, "duplicate", cand.reasons)
                continue
            urls.add(key)
            names.add(name_key)
            stats[status] += 1
            _note(outcomes, position, obj.link, status, cand.reasons)
            if len(samples[status]) < SAMPLE_SIZE:
                samples[status].append(_brief(cand, company["name"]))
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
    plan = _all_searches(camp, _company_rows(campaign_id, ids or None))
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
    max_searches_per_call: int = 30,
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
    rows = _company_rows(campaign_id, ids or None)
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

    totals = {"confirmed": 0, "review": 0, "duplicates": 0, "ignored": 0}
    samples: dict[str, list[dict[str, Any]]] = {"confirmed": [], "review": []}
    done = 0
    cache_hits = 0
    credits = 0.0
    stop: dict[str, Any] | None = None
    position = start
    began = time.monotonic()
    while position < len(plan) and done < max_searches:
        if done and time.monotonic() - began > CALL_TIME_LIMIT_S:
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

    finished = position >= len(plan) and stop is None
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
    _campaign(campaign_id)
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
    return {
        "ok": True,
        "people": [_person_shape(r, r["company_name"]) for r in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_more": page * page_size < total,
        "counts": _counts(campaign_id),
    }


def set_person_status(
    campaign_id: int, ids: Sequence[int], status: str, reason: str = ""
) -> dict[str, Any]:
    """Set `confirmed`, `review`, `rejected` or `removed` on people of the campaign.

    Moving someone to `confirmed` by hand needs a `reason` (it is stored with the person as a
    `MANUAL:` entry in `reasons`, and `recheck_people` leaves such people alone). People who fail
    the excluded-company (L03) or suppression (L16) rule cannot be promoted. Returns `updated`,
    `not_found` and `refused`.
    """
    _campaign(campaign_id)
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
                confirmed=int(status == "confirmed"),
                reasons_json=reasons,
            )
            updated.append(person_id)
    return {"ok": True, "updated": len(updated), "ids": updated, "not_found": missing,
            "refused": refused}  # fmt: skip


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
                new_status = "confirmed" if cand.confirmed else "review"
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
                (to_confirmed if new_status == "confirmed" else to_review).append(brief)
            elif changed:
                refreshed += 1
            else:
                unchanged += 1
                continue
            db.update(conn, "people", row["id"], status=new_status, **values)
    return {
        "ok": True,
        "checked": checked,
        "unchanged": unchanged,
        "refreshed": refreshed,
        "kept_manual": manual,
        "to_confirmed": to_confirmed,
        "to_review": to_review,
    }
