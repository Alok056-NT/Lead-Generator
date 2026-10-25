"""Read-only views for the dashboard: filtered lead and message lists, one lead in full, the
latest export files, recent replies and the "open the exports folder" action.

Nothing here changes lead data. The shapes are plain dicts with no secrets.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from leadgen import db
from leadgen.core import paths
from leadgen.core.text import slugify
from leadgen.services import campaign as campaign_mod
from leadgen.services import (
    contacts_import,
    projects,
    qa_leads,
    qa_messages,
    spot_check,
)
from leadgen.services import offer as offer_mod
from leadgen.services.people import person_shape

PAGE_SIZE_MAX = 50
EMAIL_STATUSES = ("verified", "risky", "guess", "unknown", "invalid", "none")
QA_FILTERS = ("block", "warn", "clean")
AI_VERDICTS = ("pass", "fix", "reject", "pending", "no_messages")
MESSAGE_AI = ("pass", "fix", "reject", "none")
CHANNELS = ("email", "linkedin_invite", "linkedin_message")
MAX_EXPORT_FILES = 12
REPLY_LIMIT = 20


class BrowseError(Exception):
    """A dashboard read failed. `code` is stable text (`not_found`, `invalid_value`)."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def as_response(self) -> dict[str, Any]:
        """The `{"ok": False, ...}` shape used everywhere."""
        return {
            "ok": False,
            "error": {"code": self.code, "message": self.message, "hint": self.hint},
        }


def _title(code: str) -> str:
    return qa_leads.LEAD_TITLES.get(code) or qa_messages.MESSAGE_TITLES.get(code, "")


def _page(page: Any, page_size: Any) -> tuple[int, int]:
    try:
        page_no = max(1, int(page))
        size = min(max(1, int(page_size)), PAGE_SIZE_MAX)
    except (TypeError, ValueError):
        raise BrowseError("invalid_value", "page and page_size must be numbers.") from None
    return page_no, size


def _choice(value: str | None, allowed: tuple[str, ...], name: str) -> str | None:
    if value is None or value == "":
        return None
    if value not in allowed:
        raise BrowseError("invalid_value", f"{name} must be one of: {', '.join(allowed)}.")
    return value


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# --- leads --------------------------------------------------------------------------------------

_RANK_SQL = (
    "CASE e.status "
    + " ".join(f"WHEN '{status}' THEN {rank}" for status, rank in qa_leads._STATUS_RANK.items())
    + " ELSE 9 END"
)

_LEAD_SELECT = f"""
SELECT p.*, c.name AS company_name,
  (SELECT e.status FROM emails e WHERE e.person_id = p.id
     ORDER BY {_RANK_SQL}, e.id DESC LIMIT 1) AS best_email_status,
  (SELECT e.email FROM emails e WHERE e.person_id = p.id
     ORDER BY {_RANK_SQL}, e.id DESC LIMIT 1) AS best_email,
  (SELECT COUNT(*) FROM qa_issues q WHERE q.severity = 'block' AND (
     (q.entity_type = 'person' AND q.entity_id = p.id) OR
     (q.entity_type = 'message' AND q.entity_id IN
        (SELECT m.id FROM messages m WHERE m.person_id = p.id)))) AS qa_block,
  (SELECT COUNT(*) FROM qa_issues q WHERE q.severity = 'warn' AND (
     (q.entity_type = 'person' AND q.entity_id = p.id) OR
     (q.entity_type = 'message' AND q.entity_id IN
        (SELECT m.id FROM messages m WHERE m.person_id = p.id)))) AS qa_warn,
  (SELECT COUNT(*) FROM messages m WHERE m.person_id = p.id) AS n_messages,
  (SELECT COUNT(*) FROM messages m WHERE m.person_id = p.id AND m.ai_review = 'reject') AS n_reject,
  (SELECT COUNT(*) FROM messages m WHERE m.person_id = p.id AND m.ai_review = 'fix') AS n_fix,
  (SELECT COUNT(*) FROM messages m WHERE m.person_id = p.id AND m.ai_review = '') AS n_pending
FROM people p JOIN companies c ON c.id = p.company_id
WHERE p.campaign_id = ?
"""


def _verdict(row: dict[str, Any]) -> str:
    if not row["n_messages"]:
        return "no_messages"
    if row["n_reject"]:
        return "reject"
    if row["n_fix"]:
        return "fix"
    if row["n_pending"]:
        return "pending"
    return "pass"


def _lead_shape(row: dict[str, Any]) -> dict[str, Any]:
    shape = person_shape(row, row["company_name"])
    shape.update(
        {
            "email": row["best_email"] or "",
            "email_status": row["best_email_status"] or "none",
            "qa_block": row["qa_block"],
            "qa_warn": row["qa_warn"],
            "messages": row["n_messages"],
            "ai_verdict": _verdict(row),
        }
    )
    return shape


def list_leads(
    campaign_id: int,
    *,
    status: str | None = None,
    designation: str | None = None,
    company_id: int | None = None,
    email_status: str | None = None,
    qa: str | None = None,
    ai_verdict: str | None = None,
    search: str | None = None,
    known_only: bool = False,
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    """One page of a campaign's people with the filters of the dashboard Leads page.

    `qa` is `block` / `warn` / `clean` (worst stored QA issue of the person or their messages),
    `email_status` the status of the person's best address (or `none`), `ai_verdict` the summary of
    their messages' AI review (`pass`, `fix`, `reject`, `pending`, `no_messages`) and `search`
    matches name, title, company and LinkedIn link; `known_only` keeps only the people who are
    imported known contacts of the campaign's project. Returns `leads`, `page`, `page_size`,
    `total`, `has_more`. Raises `BrowseError` / `CampaignError` (not found).
    """
    camp = campaign_mod.get_campaign(campaign_id)
    page_no, size = _page(page, page_size)
    email_status = _choice(email_status, EMAIL_STATUSES, "email_status")
    qa = _choice(qa, QA_FILTERS, "qa")
    ai_verdict = _choice(ai_verdict, AI_VERDICTS, "ai_verdict")
    where: list[str] = []
    params: list[Any] = [campaign_id]
    if status:
        where.append("status = ?")
        params.append(status)
    if designation:
        where.append("designation = ?")
        params.append(designation)
    if company_id is not None:
        where.append("company_id = ?")
        params.append(int(company_id))
    if email_status == "none":
        where.append("best_email_status IS NULL")
    elif email_status:
        where.append("best_email_status = ?")
        params.append(email_status)
    if qa == "block":
        where.append("qa_block > 0")
    elif qa == "warn":
        where.append("qa_block = 0 AND qa_warn > 0")
    elif qa == "clean":
        where.append("qa_block = 0 AND qa_warn = 0")
    if ai_verdict == "no_messages":
        where.append("n_messages = 0")
    elif ai_verdict == "reject":
        where.append("n_reject > 0")
    elif ai_verdict == "fix":
        where.append("n_reject = 0 AND n_fix > 0")
    elif ai_verdict == "pending":
        where.append("n_messages > 0 AND n_reject = 0 AND n_fix = 0 AND n_pending > 0")
    elif ai_verdict == "pass":
        where.append("n_messages > 0 AND n_reject = 0 AND n_fix = 0 AND n_pending = 0")
    if search and search.strip():
        term = _like(search.strip()[:100])
        where.append(
            "(raw_name LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' OR "
            "company_name LIKE ? ESCAPE '\\' OR linkedin_url LIKE ? ESCAPE '\\')"
        )
        params.extend([term] * 4)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    inner = f"SELECT * FROM ({_LEAD_SELECT}){clause}"
    if known_only:
        return _known_leads(camp, inner, params, page_no, size)
    with db.session() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM ({inner})", params).fetchone()[0]
        rows = db.query(
            conn, f"{inner} ORDER BY id LIMIT ? OFFSET ?", [*params, size, (page_no - 1) * size]
        )
    return _leads_reply(camp, rows, total, page_no, size)


def _known_leads(
    camp: dict[str, Any], inner: str, params: list[Any], page_no: int, size: int
) -> dict[str, Any]:
    """The `known_only` page: the matching people are checked against the stored known contacts
    first (the project has few thousand people at most), then paged."""
    with db.session() as conn:
        rows = db.query(conn, f"{inner} ORDER BY id", params)
    known = contacts_import.known_for_people(camp.get("project_id"), [r["id"] for r in rows])
    rows = [r for r in rows if r["id"] in known]
    start = (page_no - 1) * size
    return _leads_reply(camp, rows[start : start + size], len(rows), page_no, size, known)


def _leads_reply(
    camp: dict[str, Any],
    rows: list[dict[str, Any]],
    total: int,
    page_no: int,
    size: int,
    known: dict[int, dict[str, str]] | None = None,
) -> dict[str, Any]:
    leads = [_lead_shape(r) for r in rows]
    if known is None:
        known = contacts_import.known_for_people(camp.get("project_id"), [r["id"] for r in rows])
    for lead in leads:
        if lead["id"] in known:  # imported contact (T24)
            lead["known_contact"] = known[lead["id"]]
    return {
        "ok": True,
        "leads": leads,
        "page": page_no,
        "page_size": size,
        "total": total,
        "has_more": page_no * size < total,
    }


def lead_filters(campaign_id: int) -> dict[str, Any]:
    """The choices for the Leads filters: designation labels, companies, people counts by status."""
    camp = campaign_mod.get_campaign(campaign_id)
    with db.session() as conn:
        companies = db.query(
            conn,
            "SELECT c.id, c.name FROM companies c WHERE c.campaign_id = ? AND EXISTS "
            "(SELECT 1 FROM people p WHERE p.company_id = c.id) ORDER BY c.name",
            (campaign_id,),
        )
        counts = {
            r["status"]: r["n"]
            for r in db.query(
                conn,
                "SELECT status, COUNT(*) AS n FROM people WHERE campaign_id = ? GROUP BY status",
                (campaign_id,),
            )
        }
    return {
        "ok": True,
        "designations": [g["label"] for g in camp["designations"]],
        "companies": companies,
        "people_counts": counts,
    }


def _issues_for(conn: Any, entity_type: str, ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = {i: [] for i in ids}
    if not ids:
        return out
    marks = ",".join("?" * len(ids))
    rows = db.query(
        conn,
        "SELECT entity_id, code, severity, detail FROM qa_issues "
        f"WHERE entity_type = ? AND entity_id IN ({marks}) "
        "ORDER BY (severity = 'block') DESC, code, id",
        [entity_type, *ids],
    )
    for r in rows:
        out[r["entity_id"]].append(
            {
                "code": r["code"],
                "severity": r["severity"],
                "detail": r["detail"],
                "title": _title(r["code"]),
            }
        )
    return out


def _message_shape(row: dict[str, Any], issues: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "person_id": row["person_id"],
        "channel": row["channel"],
        "subject": row["subject"],
        "body": row["body"],
        "status": row["status"],
        "ai_review": row["ai_review"],
        "ai_review_notes": row["ai_review_notes"],
        "issues": issues,
    }


def get_person(person_id: int) -> dict[str, Any]:
    """One lead in full: evidence, company, every email, QA issues and messages with their
    issues. Raises `BrowseError` (`not_found`)."""
    with db.session() as conn:
        row = db.get(conn, "people", int(person_id))
        if row is None:
            raise BrowseError("not_found", f"There is no lead with id {person_id}.")
        company = db.get(conn, "companies", row["company_id"]) or {}
        project_id = (db.get(conn, "campaigns", row["campaign_id"]) or {}).get("project_id")
        emails = db.query(
            conn,
            "SELECT email, status, method, verifier, checked_at FROM emails WHERE person_id = ? "
            f"ORDER BY {_RANK_SQL.replace('e.status', 'status')}, id DESC",
            (row["id"],),
        )
        msg_rows = db.query(
            conn, "SELECT * FROM messages WHERE person_id = ? ORDER BY id", (row["id"],)
        )
        person_issues = _issues_for(conn, "person", [row["id"]])[row["id"]]
        msg_issues = _issues_for(conn, "message", [m["id"] for m in msg_rows])
    shape = person_shape(row, company.get("name", ""))
    known = contacts_import.known_for_people(project_id, [row["id"]]).get(row["id"])
    if known:  # imported contact (T24)
        shape["known_contact"] = known
    return {
        "ok": True,
        "campaign_id": row["campaign_id"],
        "person": shape,
        "company": {
            "id": company.get("id"),
            "name": company.get("name", ""),
            "domain": company.get("domain", ""),
            "sector": company.get("sector", ""),
            "status": company.get("status", ""),
        },
        "email": emails[0] if emails else None,
        "emails": emails,
        "issues": person_issues,
        "messages": [_message_shape(m, msg_issues[m["id"]]) for m in msg_rows],
    }


# --- messages -----------------------------------------------------------------------------------


def list_messages(
    campaign_id: int,
    *,
    channel: str | None = None,
    status: str | None = None,
    ai_review: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """One page of the campaign's messages (oldest first) with the person, company, QA issues and
    AI review notes. `ai_review` is `pass` / `fix` / `reject` / `none` (not reviewed yet).
    Returns `messages`, `page`, `page_size`, `total`, `has_more`, `counts` by channel."""
    campaign_mod.get_campaign(campaign_id)
    page_no, size = _page(page, page_size)
    channel = _choice(channel, CHANNELS, "channel")
    ai_review = _choice(ai_review, MESSAGE_AI, "ai_review")
    where, params = ["p.campaign_id = ?"], [campaign_id]
    if channel:
        where.append("m.channel = ?")
        params.append(channel)
    if status:
        where.append("m.status = ?")
        params.append(status)
    if ai_review:
        where.append("m.ai_review = ?")
        params.append("" if ai_review == "none" else ai_review)
    if search and search.strip():
        term = _like(search.strip()[:100])
        where.append(
            "(p.raw_name LIKE ? ESCAPE '\\' OR c.name LIKE ? ESCAPE '\\' "
            "OR m.body LIKE ? ESCAPE '\\')"
        )
        params.extend([term] * 3)
    clause = " AND ".join(where)
    base = (
        "FROM messages m JOIN people p ON p.id = m.person_id "
        f"JOIN companies c ON c.id = p.company_id WHERE {clause}"
    )
    with db.session() as conn:
        total = conn.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
        rows = db.query(
            conn,
            "SELECT m.*, p.raw_name AS person, p.title AS person_title, c.name AS company "
            f"{base} ORDER BY m.id LIMIT ? OFFSET ?",
            [*params, size, (page_no - 1) * size],
        )
        issues = _issues_for(conn, "message", [r["id"] for r in rows])
        counts = {
            r["channel"]: r["n"]
            for r in db.query(
                conn,
                "SELECT m.channel, COUNT(*) AS n FROM messages m JOIN people p "
                "ON p.id = m.person_id WHERE p.campaign_id = ? GROUP BY m.channel",
                (campaign_id,),
            )
        }
    messages = [
        {
            **_message_shape(r, issues[r["id"]]),
            "person": r["person"],
            "person_title": r["person_title"],
            "company": r["company"],
        }
        for r in rows
    ]
    return {
        "ok": True,
        "messages": messages,
        "page": page_no,
        "page_size": size,
        "total": total,
        "has_more": page_no * size < total,
        "counts": counts,
    }


# --- exports ------------------------------------------------------------------------------------


def _export_folder(campaign_id: int) -> Path:
    camp = campaign_mod.get_campaign(campaign_id)
    slug = slugify(camp["name"])
    return paths.exports_dir(slug)


def _iso(timestamp: float) -> str:
    try:
        return datetime.fromtimestamp(timestamp).astimezone().isoformat()
    except (OSError, OverflowError, ValueError):
        return ""


def list_exports(campaign_id: int) -> dict[str, Any]:
    """The newest files in the campaign's export folder (name, size, time) and the folder path."""
    folder = _export_folder(campaign_id)
    files: list[dict[str, Any]] = []
    for item in folder.iterdir():
        try:
            if item.is_file():
                stat = item.stat()
                files.append(
                    {
                        "name": item.name,
                        "size": stat.st_size,
                        "modified": _iso(stat.st_mtime),
                        "_t": stat.st_mtime,
                    }
                )
        except OSError:
            continue
    files.sort(key=lambda f: f["_t"], reverse=True)
    for f in files:
        del f["_t"]
    return {"ok": True, "folder": str(folder), "files": files[:MAX_EXPORT_FILES]}


def _platform() -> str:
    return sys.platform


def _startfile(folder: Path) -> None:
    os.startfile(folder)  # type: ignore[attr-defined]  # Windows only


def open_exports_folder(campaign_id: int) -> dict[str, Any]:
    """Show the campaign's export folder in the system file browser (Explorer / Finder)."""
    folder = _export_folder(campaign_id)
    try:
        if _platform() == "win32":
            _startfile(folder)
        elif _platform() == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    except OSError:
        return {
            "ok": False,
            "folder": str(folder),
            "error": {
                "code": "cannot_open",
                "message": "The folder could not be opened from here.",
                "hint": f"Open it by hand: {folder}",
            },
        }
    return {"ok": True, "folder": str(folder)}


# --- replies ------------------------------------------------------------------------------------


def recent_replies(campaign_id: int | None = None, limit: int = REPLY_LIMIT) -> dict[str, Any]:
    """The latest replies, unsubscribes and bounces (`events.detail` is already short and has no
    quoted text), newest first, for one campaign or all."""
    limit = min(max(1, int(limit)), 50)
    sql = (
        "SELECT ev.type, ev.at, ev.detail, p.raw_name AS person, c.name AS company, "
        "p.campaign_id AS campaign_id FROM events ev JOIN sends s ON s.id = ev.send_id "
        "JOIN messages m ON m.id = s.message_id JOIN people p ON p.id = m.person_id "
        "JOIN companies c ON c.id = p.company_id "
        "WHERE ev.type IN ('reply', 'unsubscribe', 'bounce')"
    )
    params: list[Any] = []
    if campaign_id is not None:
        campaign_mod.get_campaign(campaign_id)
        sql += " AND p.campaign_id = ?"
        params.append(campaign_id)
    sql += " ORDER BY ev.at DESC, ev.id DESC LIMIT ?"
    params.append(limit)
    with db.session() as conn:
        rows = db.query(conn, sql, params)
    return {"ok": True, "events": rows}


def search_task_counts(campaign_id: int) -> dict[str, int]:
    """Open / submitted / skipped AI-search tasks of a campaign."""
    counts = {"open": 0, "submitted": 0, "skipped": 0}
    with db.session() as conn:
        for r in db.query(
            conn,
            "SELECT status, COUNT(*) AS n FROM search_tasks WHERE campaign_id = ? GROUP BY status",
            (campaign_id,),
        ):
            counts[r["status"]] = r["n"]
    return counts


def campaign_overview(campaign_id: int) -> dict[str, Any]:
    """A campaign with its counts, its AI-search tasks and the spot-check state of its AI leads."""
    camp = campaign_mod.get_campaign(campaign_id)
    with db.session() as conn:
        ai_people = db.query(
            conn,
            "SELECT id, spot_check FROM people WHERE campaign_id = ? AND status = 'confirmed' "
            "AND evidence_origin = 'ai_web_search'",
            (campaign_id,),
        )
    answers: dict[str, int] = {}
    for p in ai_people:
        key = p["spot_check"] or "unchecked"
        answers[key] = answers.get(key, 0) + 1
    return {
        "ok": True,
        "campaign": camp,
        "project": _project_ref(camp.get("project_id")),
        "reserve_sources": reserve_sources(campaign_id),
        "search_tasks": search_task_counts(campaign_id),
        "spot_check": {
            "ai_leads": len(ai_people),
            "state": spot_check.spot_state(ai_people, camp["options"]),
            "answers": answers,
        },
    }


# --- projects (Sprint v1.1) -------------------------------------------------------------------


def _project_ref(project_id: int | None) -> dict[str, Any] | None:
    """`{"id", "name"}` of a project, or None."""
    if project_id is None:
        return None
    with db.session() as conn:
        row = db.get(conn, "projects", int(project_id))
    return {"id": row["id"], "name": row["name"]} if row else None


def reserve_sources(campaign_id: int) -> list[dict[str, Any]]:
    """The other campaigns of the same project that hold people saved for a later campaign:
    `[{"id", "name", "reserve"}]` (the campaign's own reserve is in its people counts)."""
    camp = campaign_mod.get_campaign(campaign_id)
    if camp.get("project_id") is None:
        return []
    with db.session() as conn:
        rows = db.query(
            conn,
            "SELECT c.id AS id, c.name AS name, COUNT(*) AS reserve FROM campaigns c "
            "JOIN people p ON p.campaign_id = c.id AND p.status = 'reserve' "
            "WHERE c.project_id = ? AND c.id <> ? AND c.status <> 'archived' "
            "GROUP BY c.id ORDER BY c.id",
            (camp["project_id"], campaign_id),
        )
    return rows


def _one_liner(profile: Any) -> str:
    return str(profile.get("one_liner") or "") if isinstance(profile, dict) else ""


def _latest(*groups: dict[int, str]) -> dict[int, str]:
    out: dict[int, str] = {}
    for group in groups:
        for key, value in group.items():
            if value and value > out.get(key, ""):
                out[key] = value
    return out


def project_cards() -> list[dict[str, Any]]:
    """One card per open project for the dashboard home: name, what it is about (`about`, the
    saved one-line summary, or `""` when the project is not set up yet), `campaigns`, `leads`
    (ready people), `verified_emails` (ready people with a verified address) and `last_activity`."""
    with db.session() as conn:
        offers = {
            r["id"]: r for r in db.query(conn, "SELECT id, profile_json, source_type FROM offers")
        }
        verified = {
            r["project_id"]: r["n"]
            for r in db.query(
                conn,
                "SELECT c.project_id AS project_id, COUNT(DISTINCT p.id) AS n FROM people p "
                "JOIN campaigns c ON c.id = p.campaign_id JOIN emails e ON e.person_id = p.id "
                "WHERE p.status = 'confirmed' AND e.status = 'verified' GROUP BY c.project_id",
            )
        }
        camp_at = {
            r["project_id"]: r["at"]
            for r in db.query(
                conn,
                "SELECT project_id, MAX(updated_at) AS at FROM campaigns GROUP BY project_id",
            )
        }
        people_at = {
            r["project_id"]: r["at"]
            for r in db.query(
                conn,
                "SELECT c.project_id AS project_id, MAX(p.updated_at) AS at FROM people p "
                "JOIN campaigns c ON c.id = p.campaign_id GROUP BY c.project_id",
            )
        }
    cards = []
    for proj in projects.list_projects(include_archived=False):
        offer = offers.get(proj["offer_id"]) if proj["offer_id"] else None
        recent = _latest(
            {proj["id"]: proj["updated_at"]}, camp_at, people_at
        ).get(proj["id"], "")
        cards.append(
            {
                "id": proj["id"],
                "name": proj["name"],
                "has_source": offer is not None,
                "about": _one_liner(offer["profile_json"]) if offer else "",
                "campaigns": proj["campaigns"],
                "leads": proj["confirmed_people"],
                "verified_emails": verified.get(proj["id"], 0),
                "cooldown_days": proj["cooldown_days"],
                "sender_name": proj["sender_name"],
                "last_activity": recent,
            }
        )
    return cards


def project_page(project_id: int) -> dict[str, Any]:
    """Everything the dashboard project page shows: the project, what it promotes (`source`:
    kind, address or file name, size, saved one-liner), its campaigns with counts, the contact
    memory (`memory`) and the imported known contacts (`known`). Raises `ProjectError`."""
    proj = projects.get_project(project_id)
    source = None
    if proj["offer_id"] is not None:
        offer = offer_mod.get_offer(proj["offer_id"])
        ref = str(offer["source_ref"] or "")
        kind = offer["source_type"]
        source = {
            "offer_id": offer["id"],
            "kind": kind,
            "label": ref if kind == "url" else Path(ref).name if kind == "file" else "",
            "characters": offer["total_chars"],
            "has_profile": offer["has_profile"],
            "about": _one_liner(offer["profile"]),
        }
    campaigns = []
    for short in campaign_mod.list_campaigns(include_archived=False, project_id=proj["id"]):
        full = campaign_mod.get_campaign(short["id"])
        campaigns.append(
            {
                "id": full["id"],
                "name": full["name"],
                "status": full["status"],
                "countries": full["countries"],
                "channels": full["channels"],
                "counts": full["counts"],
                "email_summary": full["email_summary"],
                "options": {
                    k: full["options"].get(k)
                    for k in ("max_people", "max_companies", "max_per_company")
                },
            }
        )
    return {
        "ok": True,
        "project": proj,
        "source": source,
        "campaigns": campaigns,
        "memory": projects.contact_memory(proj["id"]),
        "known": contacts_import.known_summary(proj["id"]),
    }


def studio_results(
    project_id: int, campaign_id: int | None = None, page: int = 1, page_size: int = 25
) -> dict[str, Any]:
    """The ready contacts of one of a project's searches, for the Studio table: name, job,
    company, LinkedIn link, the verified email (only verified ones) and the message texts.
    Without `campaign_id` the newest open search is used. Raises `ProjectError` (project) or
    `BrowseError` (`not_found`: the search is not part of the project)."""
    projects.get_project(project_id)
    shorts = campaign_mod.list_campaigns(include_archived=False, project_id=project_id)
    newest_first = sorted(shorts, key=lambda c: -c["id"])
    campaigns = [{"id": c["id"], "name": c["name"]} for c in newest_first]
    chosen = campaign_id if campaign_id is not None else (campaigns[0]["id"] if campaigns else None)
    if chosen is not None and chosen not in {c["id"] for c in campaigns}:
        raise BrowseError("not_found", "That search is not part of this project.")
    if chosen is None:
        return {"ok": True, "campaigns": [], "campaign_id": None, "leads": [], "page": 1,
                "page_size": page_size, "total": 0, "has_more": False}  # fmt: skip
    base = list_leads(chosen, status="confirmed", page=page, page_size=page_size)
    ids = [lead["id"] for lead in base["leads"]]
    texts: dict[int, dict[str, dict[str, Any]]] = {}
    phones: dict[int, tuple[str, str]] = {}
    if ids:
        with db.session() as conn:
            rows = db.query(
                conn,
                "SELECT person_id, channel, subject, body FROM messages WHERE person_id IN "
                f"({', '.join('?' for _ in ids)})", ids,
            )  # fmt: skip
            phone_rows = db.query(
                conn,
                "SELECT person_id, number, status FROM phones WHERE status IN "
                "('found_not_verified', 'requested') AND person_id IN "
                f"({', '.join('?' for _ in ids)})", ids,
            )  # fmt: skip
        for row in rows:
            texts.setdefault(row["person_id"], {})[row["channel"]] = row
        for row in phone_rows:  # a found number beats a waiting request, whatever the order
            if row["status"] == "found_not_verified":
                phones[row["person_id"]] = (row["number"], "found")
            else:
                phones.setdefault(row["person_id"], ("", "waiting"))
    leads = []
    for lead in base["leads"]:
        mine = texts.get(lead["id"], {})
        linkedin = mine.get("linkedin_message") or mine.get("linkedin_invite") or {}
        email = mine.get("email") or {}
        phone, phone_status = phones.get(lead["id"], ("", ""))
        leads.append({
            "id": lead["id"],
            "name": lead["raw_name"] or f"{lead['first_name']} {lead['last_name']}".strip(),
            "title": lead["title"], "company": lead["company"],
            "linkedin_url": lead["linkedin_url"],
            "email": lead["email"] if lead["email_status"] == "verified" else "",
            "phone": phone, "phone_status": phone_status,
            "linkedin_message": linkedin.get("body", ""),
            "email_subject": email.get("subject", ""), "email_body": email.get("body", ""),
            "checked": lead["ai_verdict"] == "pass" and lead["qa_block"] == 0,
        })  # fmt: skip
    return {"ok": True, "campaigns": campaigns, "campaign_id": chosen, "leads": leads,
            "page": base["page"], "page_size": base["page_size"], "total": base["total"],
            "has_more": base["has_more"]}  # fmt: skip
