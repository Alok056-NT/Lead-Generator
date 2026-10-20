"""AI search mode: the host AI's own web search replaces Serper/Hunter (PLAN section 7a).

The tool keeps a queue of search tasks (the same query strings the API path would run). The AI
fetches a batch (`get_search_tasks`), runs each query with **its own** web search tool and hands
the raw results back (`submit_search_results`), copied verbatim. The results then go through the
same confirmation code as API results (`people.process_results`, stored with
`evidence_origin = "ai_web_search"`) plus anti-fabrication checks: a profile URL must fit the
person's name (L22), the snippet must be real text (at least 40 characters, no paraphrase
words, L21), and one link must not be used for two different names. Anything doubtful goes to
`review`, never to `confirmed`. Callers must run `db.init_db()` first.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from importlib import resources
from typing import Any
from urllib.parse import urlsplit

from leadgen import db
from leadgen.providers.search import SearchResult, cached_results
from leadgen.services import campaign as campaign_mod
from leadgen.services import companies as companies_mod
from leadgen.services import confirm as rules
from leadgen.services import emails, people

KINDS = ("people", "email", "company")
BATCH_DEFAULT = 10
BATCH_MAX = 20
MAX_RESULTS_PER_TASK = 30
MIN_SNIPPET_CHARS = 40
PARAPHRASE_MARKERS = (
    "appears to be", "likely", "probably", "i found", "seems to", "based on",
)  # fmt: skip
_PARAPHRASE_RE = re.compile(
    r"(?<![A-Za-z])(?:" + "|".join(re.escape(m) for m in PARAPHRASE_MARKERS) + r")(?![A-Za-z])",
    re.IGNORECASE,
)
_SEARCH_PROVIDERS = ("serper", "brave", "ddg")  # whose cached API searches can be reused
_NOT_VERBATIM_HINT = (
    "Copy titles, URLs and snippets exactly from your search tool; don't build URLs."
)

INSTRUCTIONS = {
    "people": [
        "Run each task's `query` with YOUR OWN web search tool (do not change the query).",
        "Submit what the search returned with submit_search_results(task_id, results, "
        "tool_name). Each result is {title, url, snippet}.",
        "Copy `title`, `url` and `snippet` VERBATIM from the search tool output: character for "
        "character, no summarising, no translating, no tidying.",
        "Never build, guess, shorten or 'fix' a URL. Never add a result the tool did not "
        "return. Never describe a result in your own words.",
        "The snippet is the text excerpt the search tool shows for that result. If your tool "
        "shows no excerpt for a result, submit it without a snippet (it can then only go to "
        "review).",
        "If the search found nothing relevant, submit an empty list for that task.",
        "Do not open LinkedIn pages; use search results only.",
        "Submit every task of the batch, then report progress to the user (done, confirmed, "
        "to review, remaining) before asking for the next batch.",
    ],
    "email": [
        "Run each task's `query` with YOUR OWN web search tool.",
        "Submit the results exactly as returned: {title, url, snippet}, copied verbatim.",
        "Only addresses that appear in a title or snippet are used (to learn the company's "
        "email pattern). Never write an address yourself or guess one.",
        "If nothing was found, submit an empty list. Do not open LinkedIn pages.",
    ],
    "company": [
        "Run each task's `query` with your own web search tool and read the results.",
        "Submit the results (title, url, snippet, copied verbatim) so the task is closed, "
        "then add the companies you found with add_companies. Never invent companies.",
    ],
}
RESULT_FIELDS = {
    "title": "required",
    "url": "required",
    "snippet": "optional, but needed to confirm anyone",
}


class AISearchError(Exception):
    """An AI search request failed. `code` is stable text; `hint` tells the AI what to do."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def as_response(self) -> dict[str, Any]:
        """The `{"ok": False, "error": {...}}` shape used by every tool."""
        return {
            "ok": False,
            "error": {"code": self.code, "message": self.message, "hint": self.hint},
        }


def guide_text() -> str:
    """The AI search guide for the AI (`guides/ai_search.md`)."""
    guide = resources.files("leadgen").joinpath("guides", "ai_search.md")
    return guide.read_text(encoding="utf-8")


# --- small helpers ----------------------------------------------------------------------------


def _campaign(campaign_id: int) -> dict[str, Any]:
    return campaign_mod.get_campaign(campaign_id)  # raises CampaignError(not_found)


def _check_kind(kind: str) -> str:
    if kind not in KINDS:
        raise AISearchError("invalid_value", f"kind must be one of: {', '.join(KINDS)}.")
    return kind


def _note_key(task_id: int) -> str:
    return f"ai_search_task:{task_id}"


def _save_note(task_id: int, **data: Any) -> None:
    stamp = db.now()
    with db.session() as conn:
        conn.execute(
            "INSERT INTO settings (created_at, updated_at, key, value) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (stamp, stamp, _note_key(task_id), json.dumps(data)),
        )


def task_note(task_id: int) -> dict[str, Any]:
    """What was recorded when a task was submitted or skipped (`tool_name`, `reason`)."""
    with db.session() as conn:
        rows = db.query(conn, "SELECT value FROM settings WHERE key = ?", (_note_key(task_id),))
    try:
        value = json.loads(rows[0]["value"]) if rows else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _task(task_id: Any) -> dict[str, Any]:
    if isinstance(task_id, bool) or not isinstance(task_id, int):
        raise AISearchError("invalid_value", "task_id must be a whole number.")
    with db.session() as conn:
        row = db.get(conn, "search_tasks", task_id)
    if row is None:
        raise AISearchError(
            "task_not_found", f"No search task {task_id}.", "Call get_search_tasks for open ones."
        )
    return row


def _open_count(campaign_id: int, kind: str, after: int = 0) -> int:
    with db.session() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM search_tasks WHERE campaign_id = ? AND kind = ? "
            "AND status = 'open' AND id > ?",
            (campaign_id, kind, after),
        ).fetchone()[0]


# --- creating tasks ---------------------------------------------------------------------------


def _groups_with_people(campaign_id: int, company_id: int) -> set[str]:
    """Designation groups that already have people for this company (an earlier search)."""
    with db.session() as conn:
        rows = db.query(
            conn,
            "SELECT DISTINCT designation FROM people WHERE campaign_id = ? AND company_id = ?",
            (campaign_id, company_id),
        )
    return {r["designation"] for r in rows}


def _existing_keys(campaign_id: int, kind: str) -> set[tuple[int | None, str]]:
    with db.session() as conn:
        rows = db.query(
            conn,
            "SELECT company_id, query FROM search_tasks WHERE campaign_id = ? AND kind = ?",
            (campaign_id, kind),
        )
    return {(r["company_id"], r["query"]) for r in rows}


def _insert_tasks(campaign_id: int, kind: str, items: Sequence[tuple[int | None, str, str]]) -> int:
    if not items:
        return 0
    with db.session() as conn:
        for company_id, group_label, query in items:
            db.insert(
                conn, "search_tasks", campaign_id=campaign_id, kind=kind, company_id=company_id,
                group_label=group_label, query=query, status="open",
            )  # fmt: skip
    return len(items)


def _cached_for(query: str, gl: str) -> list[SearchResult] | None:
    for key in _SEARCH_PROVIDERS:
        hit = cached_results(key, query, gl, people.SEARCH_NUM)
        if hit is not None:
            return hit
    return None


def fill_from_cache(campaign_id: int) -> int:
    """Load cached API searches into this campaign and return how many pairs were filled.

    The search cache is shared by all campaigns, so a company x group pair that an earlier
    campaign searched through an API already has its results on disk. For every pair of this
    campaign that has neither people nor a task yet, the cached results go through
    `people.process_results` (free, `evidence_origin = "api"`) and a `submitted` task row
    records that the pair is done, so running it again does nothing.
    """
    camp = _campaign(campaign_id)
    have = _existing_keys(campaign_id, "people")
    filled = 0
    for row in people.approved_company_rows(campaign_id):
        done_groups = _groups_with_people(campaign_id, row["id"])
        company = people.company_view(row)
        for label, query, gl in people.search_plan(camp, row):
            if label in done_groups or (row["id"], query) in have:
                continue
            hit = _cached_for(query, gl)
            if hit is None:
                continue
            people.process_results(camp, company, label, query, hit, origin="api")
            have.add((row["id"], query))
            with db.session() as conn:
                task_id = db.insert(
                    conn, "search_tasks", campaign_id=campaign_id, kind="people",
                    company_id=row["id"], group_label=label, query=query, status="submitted",
                    submitted_at=db.now(), result_count=len(hit),
                )  # fmt: skip
            _save_note(task_id, tool_name="api_cache")
            filled += 1
    return filled


def ensure_tasks(campaign_id: int, kind: str = "people") -> int:
    """Create the search tasks for work not done yet and return how many were created.

    people: one per approved company x designation group, with the same query string as the API
    path (`people.search_plan`); a pair is skipped when this campaign already has people of that
    company and group or a task for it. Pairs whose search is in the shared cache are first
    filled from it (`fill_from_cache`), never skipped without their results. email: for each
    approved company with a domain, confirmed people and no saved email pattern, `"@<domain>"`
    and `site:<domain> (contact OR email)`. company: the `companies.research_queries` strings.
    Running it again creates nothing new.
    """
    _check_kind(kind)
    camp = _campaign(campaign_id)
    if kind == "people":
        fill_from_cache(campaign_id)
    have = _existing_keys(campaign_id, kind)
    new: list[tuple[int | None, str, str]] = []

    def add(company_id: int | None, label: str, query: str) -> None:
        if (company_id, query) not in have:
            have.add((company_id, query))
            new.append((company_id, label, query))

    if kind == "company":
        for query in companies_mod.research_queries(campaign_id):
            add(None, "", query)
        return _insert_tasks(campaign_id, kind, new)

    rows = people.approved_company_rows(campaign_id)
    if kind == "people":
        for row in rows:
            done_groups = _groups_with_people(campaign_id, row["id"])
            for label, query, _gl in people.search_plan(camp, row):
                if label not in done_groups:
                    add(row["id"], label, query)
    else:
        with db.session() as conn:
            with_people = {
                r["company_id"]
                for r in db.query(
                    conn,
                    "SELECT DISTINCT company_id FROM people "
                    "WHERE campaign_id = ? AND status = 'confirmed'",
                    (campaign_id,),
                )
            }
        for row in rows:
            domain = emails.clean_domain(row.get("domain") or "")
            if not domain or row["id"] not in with_people or emails.known_pattern(domain):
                continue
            add(row["id"], "email", f'"@{domain}"')
            add(row["id"], "email", f"site:{domain} (contact OR email)")
    return _insert_tasks(campaign_id, kind, new)


# --- handing tasks out ------------------------------------------------------------------------


def _cursor(value: str | int | None) -> int:
    if value in (None, ""):
        return 0
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = -1
    if n < 0:
        raise AISearchError(
            "invalid_cursor", "The cursor is not valid.", "Start again without one."
        )
    return n


def get_search_tasks(
    campaign_id: int,
    kind: str = "people",
    batch_size: int = BATCH_DEFAULT,
    cursor: str | int | None = None,
) -> dict[str, Any]:
    """The next open search tasks for the AI to run with its own web search tool.

    Creates the tasks first if none exist yet. Returns `tasks` (`task_id`, `query`, company and
    group), the `instructions` block (copy results verbatim, which fields to copy, submit an
    empty list when nothing relevant was found, never open LinkedIn pages), `remaining` (open
    tasks after this batch) and `next_cursor`. Work in batches of 10 and report progress after
    each batch. Raises `AISearchError`.
    """
    _check_kind(kind)
    camp = _campaign(campaign_id)
    size = min(max(1, int(batch_size)), BATCH_MAX)
    after = _cursor(cursor)
    filled = fill_from_cache(campaign_id) if kind == "people" and after == 0 else 0
    created = ensure_tasks(campaign_id, kind) if after == 0 else 0
    with db.session() as conn:
        rows = db.query(
            conn,
            "SELECT t.*, c.name AS company_name, c.domain AS company_domain "
            "FROM search_tasks t LEFT JOIN companies c ON c.id = t.company_id "
            "WHERE t.campaign_id = ? AND t.kind = ? AND t.status = 'open' AND t.id > ? "
            "ORDER BY t.id LIMIT ?",
            (campaign_id, kind, after, size),
        )
    tasks = []
    for row in rows:
        item: dict[str, Any] = {"task_id": row["id"], "query": row["query"]}
        if row["company_name"]:
            item["company"] = row["company_name"]
        if kind == "people":
            item["designation_group"] = row["group_label"]
        if kind == "email":
            item["domain"] = row["company_domain"]
        tasks.append(item)
    last = rows[-1]["id"] if rows else after
    remaining = _open_count(campaign_id, kind, last) if rows else 0
    reply: dict[str, Any] = {
        "ok": True,
        "campaign_id": campaign_id,
        "kind": kind,
        "search_mode": camp["options"].get("search_mode"),
        "tasks": tasks,
        "batch_size": size,
        "remaining": remaining,
        "open_total": _open_count(campaign_id, kind),
        "tasks_created": created,
        "filled_from_cache": filled,
        "next_cursor": str(last) if remaining else None,
        "result_fields": RESULT_FIELDS,
        "instructions": INSTRUCTIONS[kind],
    }
    if filled:
        reply["cache_note"] = (
            f"{filled} company x group searches were already in the search cache from an "
            "earlier run; their results were loaded for free (evidence origin api)."
        )
    if not tasks:
        reply["message"] = "No open search tasks."
    return reply


# --- checking submitted results ---------------------------------------------------------------


def _valid_url(url: str) -> bool:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and "." in (parts.hostname or "")


def _shape_problem(item: Any) -> str:
    if not isinstance(item, Mapping):
        return "each result must be an object with title, url and snippet"
    for key in ("url", "title"):
        value = item.get(key)
        if not isinstance(value, str) or not value.strip():
            return f"missing {key}"
    snippet = item.get("snippet")
    if snippet is not None and not isinstance(snippet, str):
        return "snippet must be text"
    return ""


def _snippet_problem(snippet: str, title: str = "") -> str:
    """Why a snippet cannot support a confirmation ("" when it can)."""
    text = (snippet or "").strip()
    if not text:
        return "no snippet was submitted, so nothing proves the person or the company"
    if len(text) < MIN_SNIPPET_CHARS:
        return f"the snippet is shorter than {MIN_SNIPPET_CHARS} characters"
    match = _PARAPHRASE_RE.search(f"{title} {text}")
    if match:
        return f"the text reads like a paraphrase ('{match.group(0).lower()}'), not a copied result"
    return ""


def _normalise(results: Any) -> list[Any]:
    if not isinstance(results, (list, tuple)):
        raise AISearchError(
            "invalid_value", "results must be a list of {title, url, snippet} objects."
        )
    if len(results) > MAX_RESULTS_PER_TASK:
        raise AISearchError(
            "too_many_results",
            f"At most {MAX_RESULTS_PER_TASK} results per task.",
            "Submit the first results of the search tool only.",
        )
    return list(results)


def _screen_factory(
    existing: Mapping[str, str],
    notes: dict[int, list[str]],
    conflicts: dict[str, set[str]],
) -> Callable[[rules.Candidate, SearchResult], list[str]]:
    """The `process_results` hook: AI-search checks on top of the confirmation rules."""
    seen: dict[str, str] = {}  # url key -> name key within this submission

    def screen(cand: rules.Candidate, result: SearchResult) -> list[str]:
        reasons: list[str] = []
        problem = _snippet_problem(result.snippet, result.title)
        if problem:
            reasons.append(f"L21:{problem}")
        verdict = rules.slug_matches_name(cand.linkedin_url, cand.first_name, cand.last_name)
        if verdict == "mismatch":
            reasons.append("L22:the LinkedIn address does not contain any part of the name")
        elif verdict == "no_tokens":
            notes.setdefault(result.position, []).append(
                "the LinkedIn address is an ID or the name cannot be read, so it was not matched"
            )
        key = rules.url_key(cand.linkedin_url)
        name = rules.name_key(cand.raw_name)
        other = seen.get(key)
        if other is None and key in existing:
            other = existing[key]
        if other is not None and name and other and other != name:
            conflicts.setdefault(key, set()).update({name, other})
            reasons.append("L22:the same LinkedIn address was submitted for a different name")
        seen.setdefault(key, name)
        return reasons

    return screen


def _apply_conflicts(campaign_id: int, conflicts: Mapping[str, set[str]]) -> int:
    """Send people whose link was also submitted for another name to review. Returns how many."""
    changed = 0
    with db.session() as conn:
        rows = db.query(
            conn,
            "SELECT id, linkedin_url, status, reasons_json FROM people WHERE campaign_id = ?",
            (campaign_id,),
        )
        for row in rows:
            if rules.url_key(row["linkedin_url"]) not in conflicts or row["status"] != "confirmed":
                continue
            reasons = list(row["reasons_json"] or [])
            reasons.append("L22:the same LinkedIn address was submitted for a different name")
            db.update(conn, "people", row["id"], status="review", confirmed=0, reasons_json=reasons)
            changed += 1
    return changed


def _mark(task_id: int, status: str, result_count: int, **note: Any) -> None:
    with db.session() as conn:
        db.update(
            conn, "search_tasks", task_id, status=status, submitted_at=db.now(),
            result_count=result_count,
        )  # fmt: skip
    _save_note(task_id, **note)


def _submit_people(
    camp: Mapping[str, Any], task: Mapping[str, Any], items: list[Any], tool_name: str
) -> dict[str, Any]:
    with db.session() as conn:
        company_row = db.get(conn, "companies", task["company_id"])
        existing_rows = db.query(
            conn,
            "SELECT linkedin_url, raw_name FROM people WHERE campaign_id = ?",
            (camp["id"],),
        )
    if company_row is None:
        raise AISearchError("company_missing", "The company of this task no longer exists.")
    company = people.company_view(company_row)
    existing = {
        rules.url_key(r["linkedin_url"]): rules.name_key(r["raw_name"]) for r in existing_rows
    }
    outcomes: list[dict[str, Any]] = [{} for _ in items]
    valid: list[tuple[int, SearchResult]] = []
    for index, item in enumerate(items):
        problem = _shape_problem(item)
        if problem:
            outcomes[index] = {"index": index, "outcome": "rejected", "reasons": [problem]}
            continue
        link = item["url"].strip()
        outcomes[index] = {"index": index, "url": link, "title": item["title"].strip()}
        if not rules.canonical_url(link):
            outcomes[index].update(
                outcome="rejected", reasons=["L01:not a personal LinkedIn profile link"]
            )
            continue
        valid.append(
            (
                index,
                SearchResult(
                    title=item["title"].strip(), link=link,
                    snippet=(item.get("snippet") or "").strip(), position=index + 1,
                ),
            )
        )  # fmt: skip
    if items and not valid:
        raise AISearchError(
            "not_verbatim", "No result is a LinkedIn profile link.", _NOT_VERBATIM_HINT
        )
    notes: dict[int, list[str]] = {}
    conflicts: dict[str, set[str]] = {}
    screen = _screen_factory(existing, notes, conflicts)
    processed: list[dict[str, Any]] = []
    people.process_results(
        camp, company, task["group_label"], task["query"], [r for _i, r in valid],
        origin="ai_web_search", screen=screen, outcomes=processed,
    )  # fmt: skip
    for (index, result), done in zip(valid, processed, strict=True):
        outcomes[index].update(outcome=done["outcome"], reasons=done["reasons"])
        if notes.get(result.position):
            outcomes[index]["notes"] = notes[result.position]
    downgraded = _apply_conflicts(camp["id"], conflicts) if conflicts else 0
    if conflicts:
        for out in outcomes:
            key = rules.url_key(out.get("url", ""))
            if key in conflicts and out.get("outcome") in ("confirmed", "duplicate"):
                out["outcome"] = "review"
    counts = {"confirmed": 0, "review": 0, "rejected": 0, "duplicate": 0}
    for out in outcomes:
        name = out.get("outcome", "rejected")
        name = "rejected" if name == "ignored" else name
        out["outcome"] = name
        counts[name] = counts.get(name, 0) + 1
    _mark(task["id"], "submitted", len(items), tool_name=tool_name)
    return {"summary": counts, "results": outcomes, "link_conflicts": downgraded}


def _submit_email(
    task: Mapping[str, Any], items: list[Any], tool_name: str
) -> dict[str, Any]:
    with db.session() as conn:
        company_row = db.get(conn, "companies", task["company_id"])
    domain = emails.clean_domain((company_row or {}).get("domain") or "")
    if not domain:
        raise AISearchError("company_missing", "The company of this task has no domain.")
    outcomes: list[dict[str, Any]] = []
    addresses: list[str] = []
    sources: list[str] = []
    bad_urls = 0
    for index, item in enumerate(items):
        problem = _shape_problem(item)
        if problem or not _valid_url(item["url"]):
            bad_urls += 1
            outcomes.append(
                {"index": index, "outcome": "rejected", "reasons": [problem or "bad url"]}
            )
            continue
        snippet = (item.get("snippet") or "").strip()
        problem = _snippet_problem(snippet, item["title"])
        found = emails.addresses_on_domain(f"{item['title']} {snippet}", domain)
        if problem:
            outcomes.append({"index": index, "url": item["url"], "outcome": "rejected",
                             "reasons": [problem]})  # fmt: skip
            continue
        if not found:
            outcomes.append({"index": index, "url": item["url"], "outcome": "no_address",
                             "reasons": []})  # fmt: skip
            continue
        addresses.extend(a for a in found if a not in addresses)
        sources.append(item["url"].strip())
        outcomes.append({"index": index, "url": item["url"], "outcome": "used",
                         "reasons": [], "addresses": len(found)})  # fmt: skip
    if items and bad_urls == len(items):
        raise AISearchError("not_verbatim", "No result has a usable URL.", _NOT_VERBATIM_HINT)
    learned = emails.learn_pattern_from_examples(domain, addresses, sources)
    _mark(task["id"], "submitted", len(items), tool_name=tool_name)
    return {"results": outcomes, "email_pattern": learned}


def submit_search_results(
    task_id: int, results: list[dict[str, Any]], tool_name: str
) -> dict[str, Any]:
    """Take the AI's verbatim search results for one task.

    Each result needs `url` and `title`; `snippet` is optional but a result without a real
    snippet (40+ characters, not a paraphrase) can only reach `review`. People results go
    through the same confirmation code as API results plus the anti-fabrication checks (slug
    against name, same link for two names). If no result is a valid link the whole submission
    is refused with `not_verbatim` and the task stays open. An empty list closes the task as
    'nothing found'. Returns per-result outcomes (`confirmed`, `review`, `rejected`,
    `duplicate`) with reasons. Raises `AISearchError`.
    """
    task = _task(task_id)
    if task["status"] != "open":
        raise AISearchError(
            "task_not_open",
            f"Task {task_id} is already {task['status']}.",
            "Fetch the next tasks with get_search_tasks.",
        )
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise AISearchError(
            "invalid_value", "tool_name is required.", "Say which search tool produced the results."
        )
    tool_name = tool_name.strip()[:60]
    items = _normalise(results)
    camp = _campaign(task["campaign_id"])
    if task["kind"] == "people":
        body = _submit_people(camp, task, items, tool_name)
    elif task["kind"] == "email":
        body = _submit_email(task, items, tool_name)
    else:
        bad = [i for i, it in enumerate(items) if _shape_problem(it)]
        if items and len(bad) == len(items):
            raise AISearchError(
                "not_verbatim", "No result has a title and url.", _NOT_VERBATIM_HINT
            )
        _mark(task_id, "submitted", len(items), tool_name=tool_name)
        body = {
            "results": [{"index": i, "outcome": "rejected" if i in bad else "noted"}
                        for i in range(len(items))],
            "next_step": "Add the companies you found with add_companies.",
        }  # fmt: skip
    return {
        "ok": True,
        "task_id": task_id,
        "kind": task["kind"],
        "status": "submitted",
        "result_count": len(items),
        **body,
        "remaining_open": _open_count(task["campaign_id"], task["kind"]),
    }


def skip_task(task_id: int, reason: str) -> dict[str, Any]:
    """Close an open task without results (the AI cannot run it, or the user said to skip it).
    A `reason` is required. Raises `AISearchError`."""
    task = _task(task_id)
    reason = " ".join(str(reason or "").split())
    if not reason:
        raise AISearchError("reason_required", "Say why the task is skipped.")
    if task["status"] != "open":
        raise AISearchError("task_not_open", f"Task {task_id} is already {task['status']}.")
    _mark(task_id, "skipped", 0, reason=reason[:200])
    return {
        "ok": True,
        "task_id": task_id,
        "status": "skipped",
        "remaining_open": _open_count(task["campaign_id"], task["kind"]),
    }
