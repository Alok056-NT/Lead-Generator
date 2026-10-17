"""CSV export (PLAN section 15.1, tool 29): generic, lemlist and Instantly files.

`export_campaign` asks `review.export_gate` which messages may leave, builds one row per person
(messages per channel in their own columns) and writes three files into
`exports/<campaign-slug>/`: `<format>-<YYYYMMDD-HHMM>.csv` (the leads), `qa_report.csv` (every
person with status, issues and AI verdict notes) and `removed.csv` (people removed or rejected,
with reasons). All files are UTF-8 with a BOM so Excel opens them correctly. Cells that start
with `=`, `+`, `-`, `@` (or a tab / carriage return) get a leading `'` so a spreadsheet never
runs them as a formula.

`build_rows` is also the lead list for `lemlist.push_to_lemlist`, so the CSV and the API push
always agree on who is ready.

Include modes: `approved` (default; strict: every message needs the AI's `pass`), `qa_pass`
(no QA block and no `fix` / `reject` verdict, AI review not required) and `all_confirmed` (every
confirmed lead that has no lead-level block, with the messages that have no message-level block;
withheld messages are counted). Suppressed people (L16) are never exported in any mode.

Callers must run `db.init_db()` first.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from leadgen import db
from leadgen.core import paths
from leadgen.core.text import slugify
from leadgen.services import drafting, review
from leadgen.services.emails import is_role_address
from leadgen.services.qa_leads import Issue

FORMATS = ("generic", "lemlist", "instantly")
INCLUDES = ("approved", "qa_pass", "all_confirmed")
M12_REPORT_LIMIT = 1500  # above this many messages qa_report skips the near-duplicate check

# --- columns ------------------------------------------------------------------------------------

LEMLIST_COLUMNS = (
    "email", "firstName", "lastName", "companyName", "jobTitle", "linkedinUrl", "greetingName",
    "designation", "linkedinMessage", "linkedinInvite", "emailSubject", "emailBody",
    "companyDomain", "emailStatus", "evidenceOrigin",
)  # fmt: skip

GENERIC_COLUMNS = (
    "personId", "firstName", "lastName", "greetingName", "jobTitle", "designation", "seniority",
    "country", "companyName", "companyDomain", "website", "sector", "linkedinUrl", "email",
    "emailStatus", "evidenceOrigin", "evidenceSnippet", "linkedinInvite", "linkedinMessage",
    "emailSubject", "emailBody",
)  # fmt: skip

# Instantly column -> internal row key. Instantly's built-in fields are email, first_name,
# last_name, company_name, personalization, website (and job title / LinkedIn); every other
# column becomes a custom variable named after its header (DECISIONS, T12).
INSTANTLY_COLUMNS: dict[str, str] = {
    "email": "email",
    "first_name": "firstName",
    "last_name": "lastName",
    "company_name": "companyName",
    "personalization": "emailBody",
    "website": "companyDomain",
    "job_title": "jobTitle",
    "linkedin_url": "linkedinUrl",
    "greeting_name": "greetingName",
    "designation": "designation",
    "linkedin_message": "linkedinMessage",
    "linkedin_invite": "linkedinInvite",
    "email_subject": "emailSubject",
    "email_body": "emailBody",
    "email_status": "emailStatus",
    "evidence_origin": "evidenceOrigin",
}

QA_COLUMNS = (
    "person_id", "firstName", "lastName", "companyName", "jobTitle", "linkedinUrl",
    "evidence_origin", "spot_check", "person_status", "exported", "email", "email_status",
    "messages", "blocking_issues", "warnings", "ai_verdict_notes", "review_reasons",
)  # fmt: skip

REMOVED_COLUMNS = (
    "person_id", "firstName", "lastName", "companyName", "jobTitle", "linkedinUrl",
    "evidence_origin", "status", "reasons", "ai_verdict_notes",
)  # fmt: skip

CHANNEL_KEYS = {
    "email": ("emailSubject", "emailBody"),
    "linkedin_invite": (None, "linkedinInvite"),
    "linkedin_message": (None, "linkedinMessage"),
}

LEMLIST_NEXT_STEPS = (
    "In lemlist, import this CSV into a campaign (map the columns; keep the names exactly as "
    "they are, lemlist variables are case-sensitive). Every lemlist CSV row needs an email "
    "address; leads without one must be added from their LinkedIn profile instead.",
    "LinkedIn sequence: step 1 a LinkedIn invitation left blank (no note) or, if you want a "
    "note, {{linkedinInvite}}; then a condition 'Accepted invite' with a wait of at least 1 day; "
    "then a LinkedIn message step with the text {{linkedinMessage}}.",
    "Email steps: subject {{emailSubject}} and body {{emailBody}}.",
    "Plan note: the 'Accepted invite' condition needs a lemlist plan with advanced multichannel "
    "conditions (Multichannel Expert or Enterprise). Check your plan before building the "
    "sequence.",
    "Do not edit {{variables}} inside the messages; they were checked word for word.",
)
GENERIC_NEXT_STEPS = (
    "Open the file in Excel or Google Sheets. Check 'evidenceOrigin' and 'emailStatus' before "
    "you send anything: 'guess' emails are unverified.",
)
INSTANTLY_NEXT_STEPS = (
    "In Instantly, upload the CSV and check the column mapping: email, first_name, last_name, "
    "company_name, personalization and website map to Instantly's own fields; the other columns "
    "can be mapped as custom variables (use them as {{email_subject}}, {{email_body}} ...).",
    "Instantly needs an email on every lead, so people without one are left out of this file "
    "(see skipped_no_email); they are listed in qa_report.csv.",
)


class ExportError(Exception):
    """An export request failed. `code` is stable text; `hint` tells the AI what to do."""

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


# --- spreadsheet safety and CSV writing ---------------------------------------------------------

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> str:
    """Text for a CSV cell. A value a spreadsheet could run as a formula gets a leading `'`."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


def _write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    try:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            for row in rows:
                writer.writerow([safe_cell(row.get(c, "")) for c in columns])
    except PermissionError:
        raise ExportError(
            "file_locked",
            f"Cannot write {path.name}: the file is open in another program.",
            "Close the file (for example in Excel) and export again.",
        ) from None


def _stamp_path(folder: Path, fmt: str) -> Path:
    base = f"{fmt}-{datetime.now().strftime('%Y%m%d-%H%M')}"
    path = folder / f"{base}.csv"
    n = 2
    while path.exists():
        path = folder / f"{base}-{n}.csv"
        n += 1
    return path


# --- building the rows --------------------------------------------------------------------------


@dataclass
class ExportData:
    """The people ready to leave a campaign, plus everything the reports need."""

    campaign: dict[str, Any]
    include: str
    rows: list[dict[str, Any]]  # internal rows keyed like LEMLIST/GENERIC columns
    gate: dict[str, Any]
    ev: review.Evaluation
    message_ids: dict[int, list[int]]  # person id -> exported message ids
    withheld: int = 0  # all_confirmed: messages left out because of a message-level block


def _check(fmt: str | None, include: str | None) -> None:
    if fmt is not None and fmt not in FORMATS:
        raise ExportError("invalid_value", f"format must be one of: {', '.join(FORMATS)}.")
    if include not in INCLUDES:
        raise ExportError("invalid_value", f"include must be one of: {', '.join(INCLUDES)}.")


def _export_email(
    ev: review.Evaluation, person: dict[str, Any], email_row: dict[str, Any] | None
) -> tuple[str, str]:
    """(address, status) to put in the file; ("", "") when the address must not leave."""
    if not email_row:
        return "", ""
    address = str(email_row.get("email") or "").strip()
    status = str(email_row.get("status") or "")
    allowed = {*drafting.sendable_email_statuses(ev.campaign), "guess"}
    if not address or status not in allowed or is_role_address(address):
        return "", ""
    if address.lower() in ev.leads.context.suppressed_emails:
        return "", ""
    return address, status


def build_rows(campaign_id: int, include: str = "approved") -> ExportData:
    """Select the people and messages that may be exported and build one row per person.

    Raises `ExportError` for a bad `include`; `CampaignError` / `ReviewError` pass through.
    """
    _check(None, include)
    strict = include == "approved"
    gate = review.export_gate(campaign_id, "export", strict=strict)
    count = int(gate["messages"]["total"])
    ev = review.evaluate(campaign_id, strict, near_duplicates=count <= M12_REPORT_LIMIT)
    people_by_id = {p["id"]: p for p in ev.leads.people}
    messages_by_person: dict[int, list[dict[str, Any]]] = {}
    for row in ev.messages:
        messages_by_person.setdefault(row["person_id"], []).append(row)

    withheld = 0
    chosen: dict[int, list[dict[str, Any]]] = {}
    if include == "all_confirmed":
        clean = set(gate["leads"]["exportable_ids"])
        for pid in sorted(clean):
            keep = []
            for row in messages_by_person.get(pid, []):
                if any(i.severity == "block" for i in ev.msg_issues.get(row["id"], [])):
                    withheld += 1
                else:
                    keep.append(row)
            chosen[pid] = keep
    else:
        ok_ids = set(gate["messages"]["exportable_ids"])
        for pid in sorted(messages_by_person):
            keep = [r for r in messages_by_person[pid] if r["id"] in ok_ids]
            if keep:
                chosen[pid] = keep

    rows: list[dict[str, Any]] = []
    message_ids: dict[int, list[int]] = {}
    for pid, msgs in chosen.items():
        person = people_by_id.get(pid)
        if person is None or _suppressed(ev, pid):
            continue
        company = ev.leads.companies[person["company_id"]]
        address, status = _export_email(ev, person, ev.leads.emails.get(pid))
        domain = str(company.get("domain") or "")
        row: dict[str, Any] = {
            "personId": pid,
            "email": address,
            "emailStatus": status,
            "firstName": person.get("first_name") or "",
            "lastName": person.get("last_name") or "",
            "greetingName": person.get("greeting_name") or person.get("first_name") or "",
            "companyName": company.get("name") or "",
            "companyDomain": domain,
            "website": domain,
            "jobTitle": person.get("title") or "",
            "designation": person.get("designation") or "",
            "seniority": person.get("seniority") or "",
            "country": person.get("country") or "",
            "sector": company.get("sector") or "",
            "linkedinUrl": person.get("linkedin_url") or "",
            "evidenceOrigin": person.get("evidence_origin") or "api",
            "evidenceSnippet": person.get("evidence_snippet") or "",
            "linkedinInvite": "",
            "linkedinMessage": "",
            "emailSubject": "",
            "emailBody": "",
        }
        for msg in msgs:
            subject_key, body_key = CHANNEL_KEYS.get(msg["channel"], (None, None))
            if subject_key:
                row[subject_key] = msg.get("subject") or ""
            if body_key:
                row[body_key] = msg.get("body") or ""
        rows.append(row)
        message_ids[pid] = [m["id"] for m in msgs]
    return ExportData(ev.campaign, include, rows, gate, ev, message_ids, withheld)


def _suppressed(ev: review.Evaluation, person_id: int) -> bool:
    return any(i.code == "L16" for i in ev.lead_issues.get(person_id, []))


def _format_rows(
    fmt: str, rows: list[dict[str, Any]]
) -> tuple[tuple[str, ...], list[dict[str, Any]], int]:
    """(columns, rows keyed by those columns, number of people left out for lacking an email)."""
    if fmt == "lemlist":
        return LEMLIST_COLUMNS, rows, 0
    if fmt == "generic":
        return GENERIC_COLUMNS, rows, 0
    kept = [r for r in rows if r["email"]]
    out = [{col: r[key] for col, key in INSTANTLY_COLUMNS.items()} for r in kept]
    return tuple(INSTANTLY_COLUMNS), out, len(rows) - len(kept)


# --- reports ------------------------------------------------------------------------------------


def _fmt_issues(issues: list[Issue], severity: str, prefix: str = "") -> list[str]:
    return [f"{prefix}{i.code}: {i.detail}" for i in issues if i.severity == severity]


def _qa_report_rows(
    data: ExportData, exported_ids: set[int], no_email_ids: set[int]
) -> list[dict[str, Any]]:
    ev = data.ev
    cid = data.campaign["id"]
    with db.session() as conn:
        people = db.query(
            conn,
            "SELECT p.*, c.name AS company_name FROM people p "
            "JOIN companies c ON c.id = p.company_id WHERE p.campaign_id = ? ORDER BY p.id",
            (cid,),
        )
        messages = db.query(
            conn,
            "SELECT m.* FROM messages m JOIN people p ON p.id = m.person_id "
            "WHERE p.campaign_id = ? ORDER BY m.id",
            (cid,),
        )
        email_rows = db.query(
            conn,
            "SELECT e.* FROM emails e JOIN people p ON p.id = e.person_id "
            "WHERE p.campaign_id = ? ORDER BY e.id",
            (cid,),
        )
    by_person: dict[int, list[dict[str, Any]]] = {}
    for m in messages:
        by_person.setdefault(m["person_id"], []).append(m)
    best_email: dict[int, dict[str, Any]] = {}
    order = {"verified": 0, "risky": 1, "guess": 2, "unknown": 3, "invalid": 4}
    for e in email_rows:
        cur = best_email.get(e["person_id"])
        if cur is None or order.get(e["status"], 9) < order.get(cur["status"], 9):
            best_email[e["person_id"]] = e
    out: list[dict[str, Any]] = []
    for p in people:
        pid = p["id"]
        blocks: list[str] = []
        warns: list[str] = []
        notes: list[str] = []
        parts: list[str] = []
        issues = ev.lead_issues.get(pid, [])
        blocks += _fmt_issues(issues, "block")
        warns += _fmt_issues(issues, "warn")
        for m in by_person.get(pid, []):
            parts.append(f"{m['channel']}={m['status']}")
            m_issues = [*ev.msg_issues.get(m["id"], []), *ev.review_issues.get(m["id"], [])]
            blocks += _fmt_issues(m_issues, "block", f"[{m['channel']}] ")
            warns += _fmt_issues(m_issues, "warn", f"[{m['channel']}] ")
            if m.get("ai_review") in ("fix", "reject"):
                blocks.append(f"[{m['channel']}] M16: the AI review verdict is '{m['ai_review']}'")
            verdict = m.get("ai_review") or "not reviewed"
            note = str(m.get("ai_review_notes") or "").strip()
            notes.append(f"[{m['channel']}] {verdict}" + (f": {note}" if note else ""))
        email = best_email.get(pid, {})
        status = "yes" if pid in exported_ids else "no"
        if pid in no_email_ids:
            status = "no (no email for this format)"
        out.append(
            {
                "person_id": pid,
                "firstName": p["first_name"],
                "lastName": p["last_name"],
                "companyName": p["company_name"],
                "jobTitle": p["title"],
                "linkedinUrl": p["linkedin_url"],
                "evidence_origin": p["evidence_origin"],
                "spot_check": p["spot_check"] or "",
                "person_status": p["status"],
                "exported": status,
                "email": email.get("email", ""),
                "email_status": email.get("status", ""),
                "messages": "; ".join(parts),
                "blocking_issues": " | ".join(blocks),
                "warnings": " | ".join(warns),
                "ai_verdict_notes": " | ".join(notes),
                "review_reasons": " | ".join(str(r) for r in (p["reasons_json"] or []))
                if p["status"] != "confirmed"
                else "",
            }
        )
    return out


def _removed_rows(campaign_id: int) -> list[dict[str, Any]]:
    with db.session() as conn:
        people = db.query(
            conn,
            "SELECT p.*, c.name AS company_name FROM people p "
            "JOIN companies c ON c.id = p.company_id "
            "WHERE p.campaign_id = ? AND p.status IN ('removed', 'rejected') ORDER BY p.id",
            (campaign_id,),
        )
        notes = db.query(
            conn,
            "SELECT m.person_id, m.channel, m.ai_review, m.ai_review_notes FROM messages m "
            "JOIN people p ON p.id = m.person_id WHERE p.campaign_id = ? "
            "AND m.ai_review_notes <> '' ORDER BY m.id",
            (campaign_id,),
        )
    by_person: dict[int, list[str]] = {}
    for n in notes:
        by_person.setdefault(n["person_id"], []).append(
            f"[{n['channel']}] {n['ai_review'] or 'note'}: {n['ai_review_notes']}"
        )
    return [
        {
            "person_id": p["id"],
            "firstName": p["first_name"],
            "lastName": p["last_name"],
            "companyName": p["company_name"],
            "jobTitle": p["title"],
            "linkedinUrl": p["linkedin_url"],
            "evidence_origin": p["evidence_origin"],
            "status": p["status"],
            "reasons": " | ".join(str(r) for r in (p["reasons_json"] or [])),
            "ai_verdict_notes": " | ".join(by_person.get(p["id"], [])),
        }
        for p in people
    ]


# --- export_campaign ----------------------------------------------------------------------------


def export_campaign(
    campaign_id: int, format: str = "generic", include: str = "approved"  # noqa: A002
) -> dict[str, Any]:
    """Write the CSV files for a campaign and return their paths and counts.

    `format`: `generic`, `lemlist` or `instantly`. `include`: `approved` (default),
    `qa_pass` or `all_confirmed` (see the module docstring). The result has `exported` (people
    in the file), `messages_exported`, `blocked_by_code` (why messages were held back, count per
    QA code), `not_reviewed` (live messages without an AI pass), `evidence_origin_counts`,
    `warnings` and `next_steps`. When nobody can be exported no leads file is written (`path` is
    None) but `qa_report.csv` and `removed.csv` still are.
    """
    _check(format, include)
    data = build_rows(campaign_id, include)
    camp = data.campaign
    slug = slugify(camp["name"])
    folder = paths.exports_dir(slug)
    columns, out_rows, skipped = _format_rows(format, data.rows)
    no_email_ids = {r["personId"] for r in data.rows if not r["email"]} if skipped else set()
    exported_rows = [r for r in data.rows if r["personId"] not in no_email_ids]
    exported_ids = {r["personId"] for r in exported_rows}

    path: Path | None = None
    if out_rows:
        path = _stamp_path(folder, format)
        _write_csv(path, columns, out_rows)
    qa_path = folder / "qa_report.csv"
    _write_csv(qa_path, QA_COLUMNS, _qa_report_rows(data, exported_ids, no_email_ids))
    removed_path = folder / "removed.csv"
    removed = _removed_rows(camp["id"])
    _write_csv(removed_path, REMOVED_COLUMNS, removed)

    origin = Counter(str(r["evidenceOrigin"]) for r in exported_rows)
    warnings: list[str] = []
    if origin.get("ai_web_search"):
        state = data.ev.leads.context.ai_spot_check or "none"
        warnings.append(
            f"{origin['ai_web_search']} lead(s) came from AI web search (lower confidence; "
            f"spot check state: {state}). The 'evidenceOrigin' column marks them."
        )
    if any(r["emailStatus"] == "guess" for r in exported_rows):
        warnings.append("Some emails are unverified guesses (emailStatus = guess).")
    not_reviewed = sum(1 for m in data.ev.messages if m.get("ai_review") != "pass")
    if include != "approved" and not_reviewed:
        warnings.append(f"{not_reviewed} message(s) have not been passed by the AI review.")
    if data.withheld:
        warnings.append(
            f"{data.withheld} message(s) were left out because they have a QA block; the "
            "people are still in the file."
        )
    if format == "lemlist":
        no_email = sum(1 for r in exported_rows if not r["email"])
        if no_email:
            warnings.append(
                f"{no_email} lead(s) have no email; lemlist's CSV import needs one on every row."
            )
    if skipped:
        warnings.append(f"{skipped} lead(s) have no email and were left out of the Instantly file.")

    steps = {
        "lemlist": LEMLIST_NEXT_STEPS,
        "instantly": INSTANTLY_NEXT_STEPS,
        "generic": GENERIC_NEXT_STEPS,
    }[format]
    reply: dict[str, Any] = {
        "ok": True,
        "campaign_id": camp["id"],
        "format": format,
        "include": include,
        "path": str(path) if path else None,
        "qa_report_path": str(qa_path),
        "removed_path": str(removed_path),
        "exported": len(exported_rows),
        "messages_exported": sum(
            len(data.message_ids.get(r["personId"], [])) for r in exported_rows
        ),
        "skipped_no_email": skipped,
        "removed": len(removed),
        "blocked_by_code": dict(data.gate["blocked_by"]),
        "messages_blocked": data.gate["messages"]["blocked"],
        "not_reviewed": not_reviewed,
        "evidence_origin_counts": dict(origin),
        "warnings": warnings,
        "next_steps": list(steps),
    }
    if path is None:
        reply["message"] = (
            "Nobody can be exported yet. See blocked_by_code and qa_report.csv for the reasons; "
            "run run_qa, fix the blocks and finish the AI review (get_review_batch, "
            "record_review), or ask the user whether to use include='qa_pass'."
        )
    return reply

