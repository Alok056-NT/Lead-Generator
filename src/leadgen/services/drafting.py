"""Drafting workflow: give the host AI what it needs to write one message per person per
channel, in batches, and store what it writes with an instant quality check.

The tool never writes messages itself (DECISIONS D2). `get_drafting_batch` returns confirmed
people (with a short evidence line), the offer profile, the channel limits and, on the first
batch of a session, the style guide. `save_drafts` upserts the drafts, keeps the last three
versions and returns a per-draft `qa_pass` / `qa_fail` with the issues to fix.

`CHANNEL_SPECS` is the single source of truth for the channel limits (QA reads it too).
Callers must run `db.init_db()` first.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from typing import Any

from leadgen import db
from leadgen.core.text import normalize_whitespace, truncate
from leadgen.services import campaign as campaign_mod
from leadgen.services import confirm as rules
from leadgen.services import contacts_import
from leadgen.services import offer as offer_mod

PAGE_SIZE_MAX = 50
DEFAULT_BATCH = 20
MAX_DRAFTS_PER_CALL = 50
EVIDENCE_CHARS = 300
HISTORY_KEEP = 3
# `not_saved` reasons that rewriting the text cannot fix: skip the person and tell the user.
UNFIXABLE_CODES = frozenset(
    {"no_sendable_email", "person_not_confirmed", "already_sent", "channel_not_enabled",
     "unknown_person"}
)  # fmt: skip
CHECKER = "qa_messages"  # which checks produced the issues stored in `qa_json`


# --- channel limits (PLAN section 13) -----------------------------------------------------------


@dataclass(frozen=True)
class ChannelSpec:
    """The hard limits of one channel. `body_unit` is "chars" or "words"."""

    key: str
    label: str
    body_unit: str
    body_min: int
    body_max: int
    has_subject: bool = False
    subject_max: int = 0
    links_allowed: bool = True
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        """The JSON shape shown to the AI."""
        return {
            "channel": self.key,
            "label": self.label,
            "body_unit": self.body_unit,
            "body_min": self.body_min,
            "body_max": self.body_max,
            "needs_subject": self.has_subject,
            "subject_max_chars": self.subject_max if self.has_subject else None,
            "links_allowed": self.links_allowed,
            "note": self.note,
        }


CHANNEL_SPECS: dict[str, ChannelSpec] = {
    "linkedin_invite": ChannelSpec(
        key="linkedin_invite",
        label="LinkedIn connection note",
        body_unit="chars",
        body_min=1,
        body_max=200,
        links_allowed=False,
        note="Up to 200 characters including spaces. No subject, no links.",
    ),
    "linkedin_message": ChannelSpec(
        key="linkedin_message",
        label="LinkedIn message (after they accept)",
        body_unit="chars",
        body_min=150,
        body_max=700,
        links_allowed=False,
        note="150 to 700 characters including spaces. No subject, no links.",
    ),
    "email": ChannelSpec(
        key="email",
        label="Email",
        body_unit="words",
        body_min=40,
        body_max=160,
        has_subject=True,
        subject_max=60,
        links_allowed=True,
        note="Subject up to 60 characters; body 40 to 160 words. Plain text. "
        "No footer (it is added when the email is sent).",
    ),
}


class DraftError(Exception):
    """A drafting request failed. `code` is stable text; `hint` tells the AI what to do."""

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
    """The writing rules for the AI (`guides/messaging_style.md`)."""
    guide = resources.files("leadgen").joinpath("guides", "messaging_style.md")
    return guide.read_text(encoding="utf-8")


def sendable_email_statuses(campaign: Mapping[str, Any]) -> tuple[str, ...]:
    """Email statuses that count as sendable: `verified`, plus `risky` if the campaign has
    `options.allow_risky_emails` and `guess` if it has `options.allow_guess_emails` (PLAN 7a.6,
    L23). T11's L17 uses this too, so drafting and the lead checks agree."""
    options = campaign.get("options") or {}
    statuses = ["verified"]
    if options.get("allow_risky_emails"):
        statuses.append("risky")
    if options.get("allow_guess_emails"):
        statuses.append("guess")
    return tuple(statuses)


def channels_for(campaign: Mapping[str, Any]) -> list[str]:
    """The message channels a campaign drafts: `linkedin` gives `linkedin_message` (and
    `linkedin_invite` only when `options.invite_note` is true); `email` gives `email`."""
    options = campaign.get("options") or {}
    out: list[str] = []
    for channel in campaign.get("channels") or []:
        if channel == "linkedin":
            out.append("linkedin_message")
            if options.get("invite_note"):
                out.append("linkedin_invite")
        elif channel == "email":
            out.append("email")
    return list(dict.fromkeys(out))


# --- shared helpers -----------------------------------------------------------------------------


def _campaign(campaign_id: int) -> dict[str, Any]:
    return campaign_mod.get_campaign(campaign_id)  # raises CampaignError(not_found)


def _offer_profile(camp: Mapping[str, Any]) -> dict[str, Any]:
    """The campaign's saved offer profile, or a `no_offer_profile` error (T06 follow-up)."""
    try:
        offer = offer_mod.get_offer(camp["offer_id"])
    except offer_mod.OfferError as exc:
        raise DraftError(exc.code, exc.message, exc.hint) from None
    profile = offer.get("profile")
    if not isinstance(profile, dict) or not profile:
        raise DraftError(
            "no_offer_profile",
            f"The offer '{offer.get('name', '')}' (id {camp['offer_id']}) has no saved profile, "
            "so there is nothing to write from.",
            "Read the offer with ingest_offer_source / read_offer_text, write the profile "
            "following the offer-profile guide and call save_offer_profile. Then draft again.",
        )
    return profile


def _check_channel(camp: Mapping[str, Any], channel: Any) -> str:
    if not isinstance(channel, str) or channel not in CHANNEL_SPECS:
        raise DraftError(
            "invalid_channel",
            f"Unknown channel {channel!r}.",
            f"Use one of: {', '.join(CHANNEL_SPECS)}.",
        )
    enabled = channels_for(camp)
    if channel not in enabled:
        extra = ""
        if channel == "linkedin_invite" and "linkedin" in camp["channels"]:
            extra = " Set the campaign option invite_note to true to draft connection notes."
        raise DraftError(
            "channel_not_enabled",
            f"This campaign does not draft {channel} messages.",
            f"Channels for this campaign: {', '.join(enabled) or 'none'}.{extra}",
        )
    return channel


def _cursor(value: str | int | None) -> int:
    if value in (None, ""):
        return 0
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = -1
    if n < 0:
        raise DraftError("invalid_cursor", "The cursor is not valid.", "Start again without one.")
    return n


def _int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DraftError("invalid_value", f"{name} must be a whole number.")
    return value


def _clean_evidence(snippet: Any, title: Any) -> str:
    text = normalize_whitespace(str(snippet or "")) or normalize_whitespace(str(title or ""))
    return truncate(text, EVIDENCE_CHARS)


# --- flags ----------------------------------------------------------------------------------------

# `recruiter` is for recruitment AGENCIES (headhunters, staffing firms). In-house recruiters,
# HR and talent leaders are normal prospects and get no flag.
_AGENCY_TITLE_RE = re.compile(
    r"\b(?:headhunters?|head[- ]hunting|executive search|"
    r"(?:recruitment|recruiting|staffing|talent acquisition) (?:agency|consultant))\b",
    re.IGNORECASE,
)
_RECRUITER_COMPANY_RE = re.compile(
    r"\b(?:recruiters?|recruitment|recruiting|headhunters?|head[- ]hunting|talent acquisition "
    r"(?:agency|consultant)|staffing|executive search|manpower)\b",
    re.IGNORECASE,
)
_PARTNER_RE = re.compile(
    r"\b(?:partnerships?|alliances?|channel (?:partner|manager)|resellers?|affiliates?|"
    r"strategic partner|business partner programme|business partner program)\b",
    re.IGNORECASE,
)
_PARTNER_COMPANY_RE = re.compile(r"\b(?:resellers?|channel partners?)\b", re.IGNORECASE)


def _flags(row: Mapping[str, Any], camp: Mapping[str, Any]) -> list[str]:
    """Keyword-hint flags: partner, competitor, recruiter, job_seeker (see the style guide).

    `recruiter` needs an agency signal (company name, sector or notes, or an agency-only title
    word); `partner` comes from a partnership/alliance/channel/reseller title or a reseller
    company. When the campaign's own `sectors` name that kind of firm (staffing or recruitment
    firms, resellers) those people are the target, so that flag is not set at all, from the
    title or from the company.
    """
    flags: list[str] = []
    title = str(row.get("title") or "")
    company_text = " ".join(
        str(row.get(k) or "") for k in ("c_name", "c_sector", "c_notes")
    )
    sectors = " ".join(str(s) for s in camp.get("sectors") or [])
    if not _RECRUITER_COMPANY_RE.search(sectors) and (
        _AGENCY_TITLE_RE.search(title) or _RECRUITER_COMPANY_RE.search(company_text)
    ):
        flags.append("recruiter")
    if not _PARTNER_COMPANY_RE.search(sectors) and (
        _PARTNER_RE.search(title) or _PARTNER_COMPANY_RE.search(company_text)
    ):
        flags.append("partner")
    competitors = (camp.get("exclusions") or {}).get("competitors", [])
    if competitors:
        names = {rules.name_key(c) for c in competitors}
        domains = {
            c.lower().removeprefix("www.") for c in competitors if "." in c and " " not in c
        }
        domain = str(row.get("c_domain") or "").lower().removeprefix("www.")
        if rules.name_key(str(row.get("c_name") or "")) in names or (
            domain and any(domain == d or domain.endswith("." + d) for d in domains)
        ):
            flags.append("competitor")
    reasons = row.get("reasons_json") or []
    if any(str(r).startswith("L15") for r in reasons):
        flags.append("job_seeker")
    return flags


def _clean_notes(notes: Any) -> str:
    """Company notes without the tool's own `Domain check:` / `Warning:` lines."""
    kept = [
        line.strip()
        for line in str(notes or "").splitlines()
        if line.strip() and not line.strip().startswith(("Domain check:", "Warning:"))
    ]
    return truncate(normalize_whitespace(" ".join(kept)), EVIDENCE_CHARS)


# --- drafting batch -------------------------------------------------------------------------------

_ELIGIBLE_FROM = """
FROM people p
JOIN companies c ON c.id = p.company_id
LEFT JOIN messages m ON m.person_id = p.id AND m.channel = ?
WHERE p.campaign_id = ? AND p.status = 'confirmed'
  AND (m.id IS NULL OR (m.status != 'sent' AND (m.status = 'qa_fail' OR m.ai_review = 'fix')))
"""


def _eligible_sql(channel: str, camp: Mapping[str, Any]) -> tuple[str, list[Any]]:
    """The shared FROM/WHERE for the eligible people and its bound parameters."""
    sql = _ELIGIBLE_FROM
    params: list[Any] = [channel, camp["id"]]
    if channel == "email":
        statuses = sendable_email_statuses(camp)
        marks = ", ".join("?" for _ in statuses)
        sql += (
            "  AND EXISTS (SELECT 1 FROM emails e WHERE e.person_id = p.id "
            f"AND e.status IN ({marks}))\n"
        )
        params += list(statuses)
    return sql, params


def _offer_payload(profile: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "name", "one_liner", "services", "confirmed_claims", "forbidden_claims",
        "proof_points", "ctas", "sender", "tone", "language",
    )  # fmt: skip
    return {k: profile.get(k) for k in keys}


def _person_payload(
    row: Mapping[str, Any], camp: Mapping[str, Any], known: Mapping[str, str] | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "person_id": row["id"],
        "greeting_name": row["greeting_name"] or row["first_name"],
        "full_name": normalize_whitespace(f"{row['first_name']} {row['last_name']}"),
        "title": row["title"],
        "designation": row["designation"],
        "seniority": row["seniority"],
        "company": {
            "name": row["c_name"],
            "sector": row["c_sector"],
            "size_note": row["c_size_note"],
            "notes": _clean_notes(row["c_notes"]),
        },
        "evidence": _clean_evidence(row["evidence_snippet"], row["evidence_title"]),
        "flags": _flags(row, camp),
    }
    if known:  # an imported contact (T24): the AI may only say "we spoke before" because of this
        out["known_contact"] = {
            "relationship": known["relationship"],
            "last_contacted": known["last_contacted"],
        }
    if row["m_id"] is not None:  # a redraft: show what was wrong
        qa = row["m_qa_json"] if isinstance(row["m_qa_json"], dict) else {}
        out["previous_draft"] = {"subject": row["m_subject"], "body": row["m_body"]}
        out["previous_issues"] = [
            {k: i.get(k) for k in ("code", "severity", "detail")}
            for i in qa.get("issues", [])
            if isinstance(i, dict)
        ]
        if row["m_ai_review"] == "fix":
            out["review_notes"] = row["m_ai_review_notes"]
    return out


def get_drafting_batch(
    campaign_id: int,
    channel: str,
    batch_size: int = DEFAULT_BATCH,
    cursor: str | int | None = None,
    include_guide: bool | None = None,
) -> dict[str, Any]:
    """People to write a `channel` message for, with the offer and the channel limits.

    Eligible people are `confirmed`, have no message for this channel yet (or one in `qa_fail`,
    or one the AI review marked `fix`), and for `email` have a sendable email (`verified`, plus
    `risky` / `guess` when the campaign options `allow_risky_emails` / `allow_guess_emails` are
    on). Each batch has the offer profile and `channel_spec`; `guide` (the style guide) is
    included on the first batch (no `cursor`) unless `include_guide` says otherwise. Call again
    with `next_cursor` until `done`. Raises `DraftError` (`no_offer_profile`, `invalid_channel`,
    `channel_not_enabled`, ...).
    """
    camp = _campaign(campaign_id)
    profile = _offer_profile(camp)
    channel = _check_channel(camp, channel)
    size = min(max(1, _int(batch_size, "batch_size")), PAGE_SIZE_MAX)
    after = _cursor(cursor)
    show_guide = (after == 0) if include_guide is None else bool(include_guide)

    base, params = _eligible_sql(channel, camp)
    with db.session() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) {base} AND p.id > ?", [*params, after]
        ).fetchone()[0]
        rows = db.query(
            conn,
            "SELECT p.*, c.name AS c_name, c.sector AS c_sector, c.size_note AS c_size_note, "
            "c.notes AS c_notes, c.domain AS c_domain, m.id AS m_id, m.subject AS m_subject, "
            "m.body AS m_body, m.qa_json AS m_qa_json, m.ai_review AS m_ai_review, "
            "m.ai_review_notes AS m_ai_review_notes "
            f"{base} AND p.id > ? ORDER BY p.id LIMIT ?",
            [*params, after, size],
        )
    known = contacts_import.known_for_people(camp.get("project_id"), [r["id"] for r in rows])
    remaining = max(0, total - len(rows))
    last_id = rows[-1]["id"] if rows else after
    reply: dict[str, Any] = {
        "ok": True,
        "campaign_id": camp["id"],
        "channel": channel,
        "locale": camp["locale"],
        "campaign": {
            "name": camp["name"],
            "sectors": camp["sectors"],
            "countries": camp["countries"],
            "target_designations": [g.get("label") for g in camp["designations"]],
        },
        "channel_spec": CHANNEL_SPECS[channel].as_dict(),
        "offer": _offer_payload(profile),
        "people": [_person_payload(r, camp, known.get(r["id"])) for r in rows],
        "count": len(rows),
        "remaining": remaining,
        "done": remaining == 0,
        "next_cursor": str(last_id) if remaining else None,
    }
    if show_guide:
        reply["guide"] = guide_text()
    if not rows and after == 0:
        reply["message"] = (
            "Nobody needs a draft for this channel. "
            + (
                "People need a verified email first (run find_emails)."
                if channel == "email"
                else "Confirm people first, or every confirmed person already has a draft."
            )
        )
    return reply


# --- instant message checks ------------------------------------------------------------


def _issue(code: str, severity: str, detail: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "detail": detail}


def _qa() -> Any:
    """The message checks (`qa_messages`), imported late because that module reads
    `CHANNEL_SPECS` from this one."""
    from leadgen.services import qa_messages

    return qa_messages


@dataclass
class _SaveContext:
    """What `save_drafts` loads once per call for the message checks."""

    profile: Mapping[str, Any]
    companies: list[dict[str, Any]]
    index: dict[int, Any]


# --- saving ---------------------------------------------------------------------------------------


def _clean_body(value: str) -> str:
    """Normalise line endings, trim line ends and limit blank lines; keep paragraphs."""
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _not_saved(
    index: int, person_id: Any, channel: Any, code: str, detail: str
) -> dict[str, Any]:
    return {
        "index": index,
        "person_id": person_id,
        "channel": channel,
        "status": "not_saved",
        "issues": [_issue(code, "block", detail)],
    }


def _history(existing: Mapping[str, Any], add_current: bool = True) -> list[dict[str, Any]]:
    """Previous versions of a message, newest first. The row being replaced is added in front
    when `add_current` (not for an identical re-save). The caller keeps `HISTORY_KEEP`."""
    qa = existing["qa_json"] if isinstance(existing["qa_json"], dict) else {}
    old = [h for h in qa.get("history", []) if isinstance(h, dict)]
    current = {
        "subject": existing["subject"],
        "body": existing["body"],
        "status": existing["status"],
        "saved_at": existing["updated_at"],
        "issues": [i for i in qa.get("issues", []) if isinstance(i, dict)],
    }
    return ([current] if add_current and existing["body"] else []) + old


def _save_one(
    conn: sqlite3.Connection,
    camp: Mapping[str, Any],
    enabled: Sequence[str],
    draft: Any,
    index: int,
    seen: set[tuple[int, str]],
    ctx: _SaveContext,
) -> dict[str, Any]:
    if not isinstance(draft, Mapping):
        return _not_saved(index, None, None, "invalid_draft", "A draft must be an object.")
    person_id, channel = draft.get("person_id"), draft.get("channel")
    if isinstance(person_id, bool) or not isinstance(person_id, int):
        return _not_saved(
            index, None, channel, "invalid_draft", "person_id must be a whole number."
        )
    if channel not in CHANNEL_SPECS:
        return _not_saved(
            index, person_id, channel, "invalid_draft",
            f"Unknown channel. Use one of: {', '.join(CHANNEL_SPECS)}.",
        )  # fmt: skip
    if channel not in enabled:
        return _not_saved(
            index, person_id, channel, "channel_not_enabled",
            f"This campaign does not draft {channel} messages (enabled: "
            f"{', '.join(enabled) or 'none'}).",
        )  # fmt: skip
    body, subject = draft.get("body"), draft.get("subject", "")
    if not isinstance(body, str):
        return _not_saved(index, person_id, channel, "invalid_draft", "body must be text.")
    if subject is None:
        subject = ""
    if not isinstance(subject, str):
        return _not_saved(index, person_id, channel, "invalid_draft", "subject must be text.")
    if (person_id, channel) in seen:
        return _not_saved(
            index, person_id, channel, "duplicate_in_call",
            "This person and channel appear twice in the call; send one draft each.",
        )  # fmt: skip
    seen.add((person_id, channel))

    person = db.get(conn, "people", person_id)
    if person is None or person["campaign_id"] != camp["id"]:
        return _not_saved(
            index, person_id, channel, "unknown_person",
            f"There is no person {person_id} in this campaign.",
        )  # fmt: skip
    if person["status"] != "confirmed":
        return _not_saved(
            index, person_id, channel, "person_not_confirmed",
            f"This person is '{person['status']}', only confirmed people get drafts.",
        )  # fmt: skip
    if channel == "email":
        statuses = sendable_email_statuses(camp)
        marks = ", ".join("?" for _ in statuses)
        found = conn.execute(
            f"SELECT 1 FROM emails WHERE person_id = ? AND status IN ({marks}) LIMIT 1",
            [person_id, *statuses],
        ).fetchone()
        if found is None:
            return _not_saved(
                index, person_id, channel, "no_sendable_email",
                "This person has no sendable email yet. Run find_emails first.",
            )  # fmt: skip

    existing = db.query(
        conn,
        "SELECT * FROM messages WHERE person_id = ? AND channel = ?",
        (person_id, channel),
    )
    existing_row = existing[0] if existing else None
    if existing_row is not None and existing_row["status"] == "sent":
        return _not_saved(
            index, person_id, channel, "already_sent",
            "This message was already sent and cannot be replaced.",
        )  # fmt: skip

    body = _clean_body(body)
    subject = normalize_whitespace(subject) if CHANNEL_SPECS[channel].has_subject else ""
    company = next((c for c in ctx.companies if c["id"] == person["company_id"]), None)
    if company is None:
        return _not_saved(
            index, person_id, channel, "unknown_person",
            f"Person {person_id} has no company in this campaign.",
        )  # fmt: skip
    qa = _qa()
    message = {
        "id": existing_row["id"] if existing_row is not None else None,
        "channel": channel,
        "subject": subject,
        "body": body,
        "ai_review": "",
    }
    issues = [
        i.as_dict()
        for i in qa.check_message(
            message, person, company, camp, ctx.profile, ctx.index, companies=ctx.companies
        )
    ]
    status = "qa_fail" if any(i["severity"] == "block" for i in issues) else "qa_pass"

    history: list[dict[str, Any]] = []
    if existing_row is not None:
        same = existing_row["body"] == body and existing_row["subject"] == subject
        history = _history(existing_row, add_current=not same)
    qa_json = {
        "issues": issues,
        "checked_at": db.now(),
        "checker": CHECKER,
        "history": history[:HISTORY_KEEP],
    }
    values = {
        "subject": subject,
        "body": body,
        "status": status,
        "qa_json": qa_json,
        "ai_review": "",
        "ai_review_notes": "",
    }
    if existing_row is None:
        message_id = db.insert(conn, "messages", person_id=person_id, channel=channel, **values)
    else:
        message_id = existing_row["id"]
        db.update(conn, "messages", message_id, **values)
    ctx.index[message_id] = qa.index_entry({**message, "id": message_id}, person, company)
    return {
        "index": index,
        "message_id": message_id,
        "person_id": person_id,
        "channel": channel,
        "status": status,
        "issues": issues,
    }


def save_context(campaign_id: int) -> _SaveContext:
    """Load what `save_drafts` needs for the checks (offer profile, the campaign's companies and
    the near-duplicate index). A caller that saves several single drafts in a row (the review
    loop) builds it once and passes it to each `save_drafts` call; `save_drafts` keeps the
    index up to date after each save."""
    camp = _campaign(campaign_id)
    profile = _offer_profile(camp)
    with db.session() as conn:
        companies = db.query(conn, "SELECT * FROM companies WHERE campaign_id = ?", (camp["id"],))
    return _SaveContext(profile=profile, companies=companies, index=_qa().build_index(camp["id"]))


def save_drafts(
    campaign_id: int, drafts: list[dict[str, Any]], *, context: _SaveContext | None = None
) -> dict[str, Any]:
    """Store drafts (`person_id`, `channel`, `body`, and `subject` for email) and check each.

    One message exists per person per channel: saving again replaces the text and keeps the
    last three versions in `qa_json.history`. A replaced draft needs its AI review again.
    Returns one entry per draft: `status` is `qa_pass` or `qa_fail` (with `issues` to fix; only
    resave the failures) or `not_saved` (the draft was refused, see `issues`; codes in
    `UNFIXABLE_CODES` cannot be fixed by rewriting, so skip those people). Raises
    `DraftError` for a missing offer profile or a bad `drafts` list. `context` is an optional
    `save_context(campaign_id)` to reuse across calls (it is updated as drafts are saved).
    """
    camp = _campaign(campaign_id)
    _offer_profile(camp)  # raises when the offer has no saved profile
    if not isinstance(drafts, (list, tuple)) or not drafts:
        raise DraftError("invalid_drafts", "drafts must be a non-empty list.")
    if len(drafts) > MAX_DRAFTS_PER_CALL:
        raise DraftError(
            "too_many_drafts",
            f"Send at most {MAX_DRAFTS_PER_CALL} drafts per call (got {len(drafts)}).",
            "Split them into several calls.",
        )
    enabled = channels_for(camp)
    seen: set[tuple[int, str]] = set()
    ctx = context if context is not None else save_context(camp["id"])
    with db.session() as conn:
        results = [
            _save_one(conn, camp, enabled, draft, index, seen, ctx)
            for index, draft in enumerate(drafts)
        ]
    counts = {
        s: sum(1 for r in results if r["status"] == s) for s in ("qa_pass", "qa_fail", "not_saved")
    }
    reply: dict[str, Any] = {"ok": True, **counts, "results": results}
    skipped = sorted({
        i["code"] for r in results if r["status"] == "not_saved"
        for i in r["issues"] if i["code"] in UNFIXABLE_CODES
    })  # fmt: skip
    steps: list[str] = []
    if counts["qa_fail"] or any(
        r["status"] == "not_saved" and r["issues"][0]["code"] not in UNFIXABLE_CODES
        for r in results
    ):
        steps.append(
            "Fix the drafts marked qa_fail, and any not_saved draft whose issue is about the "
            "draft itself (invalid_draft, duplicate_in_call), using their issues, and save only "
            "those again."
        )
    if skipped:
        steps.append(
            f"Do not rewrite the not_saved drafts with {', '.join(skipped)}: rewriting cannot "
            "fix them. Skip those people and tell the user why."
        )
    if steps:
        reply["next_step"] = " ".join(steps)
    return reply
