"""Projects and contact memory (Sprint v1.1, T23).

A project is one product, service, content piece or website: it has one offer and many
campaigns. The project also remembers every person who was found or contacted in it
(`contact_log`), so the same person is not offered again until the project's cooldown has passed.

Matching a person against the memory tries the LinkedIn profile first, then the email address,
then the name together with the company. The LinkedIn key is the profile slug (`in/<slug>`, see
`confirm.url_key`), so `www.linkedin.com/in/x` and `sg.linkedin.com/in/x/` are the same person.
Callers must run `db.init_db()` first. Errors are raised as `ProjectError`.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from leadgen import db
from leadgen.core.text import normalize_whitespace
from leadgen.services import confirm as rules

_log = logging.getLogger("leadgen.projects")

STATUSES = ("active", "archived")
CHANNELS = ("found", "exported_csv", "lemlist", "email_sent", "imported")
PEOPLE_STATUSES = ("confirmed", "reserve", "review", "rejected", "removed")
DEFAULT_COOLDOWN_DAYS = 90
MAX_COOLDOWN_DAYS = 3650
MAX_NAME = 120
EMAIL_POLICIES = ("verified_only", "allow_risky")
DEFAULT_EMAIL_POLICY = "verified_only"
_FIELDS = ("name", "description", "cooldown_days", "offer_id", "status", "email_policy")


class ProjectError(Exception):
    """A project request failed. `code` is stable text; `hint` tells the AI what to do."""

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


# --- validation -------------------------------------------------------------------------------


def _name(value: Any) -> str:
    if not isinstance(value, str) or not normalize_whitespace(value):
        raise ProjectError("invalid_name", "The project needs a name.")
    name = normalize_whitespace(value)
    if len(name) > MAX_NAME:
        raise ProjectError(
            "invalid_name", f"The project name is longer than {MAX_NAME} characters."
        )
    return name


def _check_unique_name(conn: sqlite3.Connection, name: str, own_id: int | None) -> None:
    for row in db.query(conn, "SELECT id, name FROM projects"):
        if row["id"] != own_id and row["name"].casefold() == name.casefold():
            raise ProjectError(
                "duplicate_name",
                f"Project '{row['name']}' (id {row['id']}) already has this name.",
                "Pick a different name, or use list_projects to continue that project.",
            )


def _cooldown(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_COOLDOWN_DAYS:
        raise ProjectError(
            "invalid_cooldown",
            f"cooldown_days must be a whole number from 0 to {MAX_COOLDOWN_DAYS}.",
            "0 means people can be offered again straight away.",
        )
    return value


def _email_policy(value: Any) -> str:
    if value not in EMAIL_POLICIES:
        raise ProjectError(
            "invalid_email_policy",
            f"email_policy must be one of: {', '.join(EMAIL_POLICIES)}.",
            "verified_only: only checked addresses are exported or sent (recommended). "
            "allow_risky: also addresses on catch-all domains. Guessed addresses are never used.",
        )
    return str(value)


def _description(value: Any) -> str:
    if not isinstance(value, str):
        raise ProjectError("invalid_value", "description must be text.")
    return value.strip()


def _check_offer(conn: sqlite3.Connection, offer_id: Any) -> int:
    if isinstance(offer_id, bool) or not isinstance(offer_id, int):
        raise ProjectError("invalid_offer", "offer_id must be the number of an existing offer.")
    if db.get(conn, "offers", offer_id) is None:
        raise ProjectError(
            "invalid_offer",
            f"There is no offer with id {offer_id}.",
            "Use list_offers to see them.",
        )
    return offer_id


def _row(conn: sqlite3.Connection, project_id: Any) -> dict[str, Any]:
    row = (
        db.get(conn, "projects", project_id)
        if isinstance(project_id, int) and not isinstance(project_id, bool)
        else None
    )
    if row is None:
        raise ProjectError(
            "not_found", f"There is no project with id {project_id}.",
            "Use list_projects to see them.",
        )  # fmt: skip
    return row


# --- reading ----------------------------------------------------------------------------------


def _shape(row: Mapping[str, Any], offer_name: str = "") -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "offer_id": row["offer_id"],
        "offer_name": offer_name,
        "cooldown_days": row["cooldown_days"],
        "email_policy": row["email_policy"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _offer_name(conn: sqlite3.Connection, offer_id: int | None) -> str:
    if offer_id is None:
        return ""
    row = db.get(conn, "offers", offer_id)
    return str(row["name"]) if row else ""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(text: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _contacted_in_cooldown(conn: sqlite3.Connection, project_id: int, days: int) -> int:
    """Distinct people (by their best key) logged inside the cooldown window."""
    if days <= 0:
        return 0
    cutoff = _stamp(_utcnow() - timedelta(days=days))
    row = conn.execute(
        "SELECT COUNT(DISTINCT COALESCE(NULLIF(key_linkedin, ''), NULLIF(key_email, ''), "
        "NULLIF(key_name_company, ''), 'log' || id)) FROM contact_log "
        "WHERE project_id = ? AND at >= ?",
        (project_id, cutoff),
    ).fetchone()
    return int(row[0] or 0)


def get_project(project_id: int) -> dict[str, Any]:
    """One project with `counts`: campaigns by status (and `total`), people by status over all
    its campaigns, and `contacted_in_cooldown` (people the project will not offer again yet).
    Raises `ProjectError` (`not_found`)."""
    with db.session() as conn:
        row = _row(conn, project_id)
        campaigns = {
            r["status"]: r["n"]
            for r in db.query(
                conn,
                "SELECT status, COUNT(*) AS n FROM campaigns WHERE project_id = ? GROUP BY status",
                (row["id"],),
            )
        }
        people = {
            r["status"]: r["n"]
            for r in db.query(
                conn,
                "SELECT p.status AS status, COUNT(*) AS n FROM people p "
                "JOIN campaigns c ON c.id = p.campaign_id WHERE c.project_id = ? "
                "GROUP BY p.status",
                (row["id"],),
            )
        }
        contacted = _contacted_in_cooldown(conn, row["id"], row["cooldown_days"])
        name = _offer_name(conn, row["offer_id"])
    counts = {
        "campaigns": {
            **dict.fromkeys(("draft", "active", "archived"), 0),
            **campaigns,
            "total": sum(campaigns.values()),
        },
        "people": {**dict.fromkeys(PEOPLE_STATUSES, 0), **people},
        "contacted_in_cooldown": contacted,
    }
    return {**_shape(row, name), "counts": counts}


def list_projects(include_archived: bool = True) -> list[dict[str, Any]]:
    """Short summaries of the projects (newest first) with the number of campaigns and the
    number of confirmed people over all their campaigns."""
    with db.session() as conn:
        rows = db.query(conn, "SELECT * FROM projects ORDER BY id DESC")
        names = {r["id"]: r["name"] for r in db.query(conn, "SELECT id, name FROM offers")}
        camp = {
            r["project_id"]: r["n"]
            for r in db.query(
                conn,
                "SELECT project_id, COUNT(*) AS n FROM campaigns WHERE status <> 'archived' "
                "GROUP BY project_id",
            )
        }
        confirmed = {
            r["project_id"]: r["n"]
            for r in db.query(
                conn,
                "SELECT c.project_id AS project_id, COUNT(*) AS n FROM people p "
                "JOIN campaigns c ON c.id = p.campaign_id WHERE p.status = 'confirmed' "
                "GROUP BY c.project_id",
            )
        }
    out = []
    for row in rows:
        if row["status"] == "archived" and not include_archived:
            continue
        shaped = _shape(row, names.get(row["offer_id"], ""))
        shaped["campaigns"] = camp.get(row["id"], 0)
        shaped["confirmed_people"] = confirmed.get(row["id"], 0)
        out.append(shaped)
    return out


# --- writing ----------------------------------------------------------------------------------


def create_project(
    name: str,
    description: str = "",
    cooldown_days: int = DEFAULT_COOLDOWN_DAYS,
    offer_id: int | None = None,
    email_policy: str = DEFAULT_EMAIL_POLICY,
) -> dict[str, Any]:
    """Create an active project and return it (see `get_project`).

    `cooldown_days` (0 to 3650, default 90) is how long a person who was found or contacted is
    not offered again; 0 turns the memory off. `offer_id` may be set later (`update_project`).
    `email_policy` is `verified_only` (default: only verified addresses are exported or sent) or
    `allow_risky` (also addresses on catch-all domains); it sets `allow_risky_emails` for the
    campaigns made afterwards. Raises `ProjectError`.
    """
    values: dict[str, Any] = {
        "name": _name(name),
        "description": _description(description),
        "cooldown_days": _cooldown(cooldown_days),
        "email_policy": _email_policy(email_policy),
        "status": "active",
    }
    with db.session() as conn:
        _check_unique_name(conn, values["name"], None)
        if offer_id is not None:
            values["offer_id"] = _check_offer(conn, offer_id)
        new_id = db.insert(conn, "projects", **values)
    return get_project(new_id)


def update_project(project_id: int, **changes: Any) -> dict[str, Any]:
    """Change `name`, `description`, `cooldown_days`, `offer_id`, `email_policy` or `status`
    and return the project.

    Existing campaigns keep the offer they were made with when `offer_id` changes, and keep their
    stored `allow_risky_emails` when `email_policy` changes (it applies to new campaigns).
    Raises `ProjectError`.
    """
    unknown = set(changes) - set(_FIELDS)
    if unknown:
        raise ProjectError(
            "unknown_field",
            f"Cannot change: {', '.join(sorted(unknown))}. Changeable: {', '.join(_FIELDS)}.",
        )
    with db.session() as conn:
        row = _row(conn, project_id)
        values: dict[str, Any] = {}
        if "name" in changes:
            values["name"] = _name(changes["name"])
            _check_unique_name(conn, values["name"], row["id"])
        if "description" in changes:
            values["description"] = _description(changes["description"])
        if "cooldown_days" in changes:
            values["cooldown_days"] = _cooldown(changes["cooldown_days"])
        if "offer_id" in changes:
            values["offer_id"] = _check_offer(conn, changes["offer_id"])
        if "email_policy" in changes:
            values["email_policy"] = _email_policy(changes["email_policy"])
        if "status" in changes:
            if changes["status"] not in STATUSES:
                raise ProjectError(
                    "invalid_value", f"status must be one of: {', '.join(STATUSES)}."
                )
            values["status"] = changes["status"]
        if values:
            db.update(conn, "projects", row["id"], **values)
    return get_project(project_id)


def set_project_offer(project_id: int, offer_id: int) -> dict[str, Any]:
    """Set the project's offer (the product, service or page its campaigns promote)."""
    return update_project(project_id, offer_id=offer_id)


def replace_offer(project_id: int, offer_id: int) -> dict[str, Any]:
    """Make `offer_id` the project's one source, replacing the old one (owner decision 24 Oct).

    Campaigns already made keep the offer they were created with. The old offer is deleted when
    nothing refers to it any more (no campaign, no other project), so a project never piles up
    old sources. Returns `{"replaced_offer_id": id or None, "old_offer_deleted": bool}`.
    Raises `ProjectError`.
    """
    with db.session() as conn:
        row = _row(conn, project_id)
        new = _check_offer(conn, offer_id)
        old = row["offer_id"]
        if old == new:
            return {"replaced_offer_id": None, "old_offer_deleted": False}
        db.update(conn, "projects", row["id"], offer_id=new)
        deleted = False
        if old is not None:
            used = conn.execute(
                "SELECT (SELECT COUNT(*) FROM campaigns WHERE offer_id = ?) "
                "+ (SELECT COUNT(*) FROM projects WHERE offer_id = ?)",
                (old, old),
            ).fetchone()[0]
            if not used:
                conn.execute("DELETE FROM offers WHERE id = ?", (old,))
                deleted = True
    return {"replaced_offer_id": old, "old_offer_deleted": deleted}


def archive_project(project_id: int) -> dict[str, Any]:
    """Mark a project archived (its campaigns, people and memory are kept) and return it."""
    return update_project(project_id, status="archived")


def ensure_project_for_offer(offer_id: int) -> int:
    """The id of the oldest active project that uses this offer; one named after the offer is
    created when there is none (the same naming as the v2 migration)."""
    with db.session() as conn:
        _check_offer(conn, offer_id)
        row = conn.execute(
            "SELECT id FROM projects WHERE offer_id = ? AND status = 'active' ORDER BY id LIMIT 1",
            (offer_id,),
        ).fetchone()
        if row is not None:
            return int(row[0])
        offer = db.get(conn, "offers", offer_id) or {}
        base = normalize_whitespace(str(offer.get("name") or "")) or f"Offer {offer_id}"
        base = base[:MAX_NAME].strip()
        used = {str(r["name"]).casefold() for r in db.query(conn, "SELECT name FROM projects")}
        name, n = base, 1
        while name.casefold() in used:
            n += 1
            name = f"{base} ({n})"
        return db.insert(conn, "projects", name=name, offer_id=offer_id)


# --- keys -------------------------------------------------------------------------------------


def _text(person: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = person.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def make_keys(person: Mapping[str, Any]) -> dict[str, str]:
    """The three memory keys of a person: `key_linkedin`, `key_email`, `key_name_company`.

    Reads `linkedin_url` (or `linkedinUrl`), `email`, `first_name` + `last_name` (or the camel
    case export names, or `raw_name`) and `company` (or `companyName`, `company_name`). A key is
    "" when its input is missing. Keys already present in `person` are kept as they are.
    """
    if any(k in person for k in ("key_linkedin", "key_email", "key_name_company")):
        return {
            "key_linkedin": str(person.get("key_linkedin") or ""),
            "key_email": str(person.get("key_email") or "").lower(),
            "key_name_company": str(person.get("key_name_company") or ""),
        }
    linkedin = rules.url_key(_text(person, "linkedin_url", "linkedinUrl"))
    email = _text(person, "email").lower()
    if "@" not in email:
        email = ""
    first = _text(person, "first_name", "firstName")
    last = _text(person, "last_name", "lastName")
    name = rules.name_key(f"{first} {last}") if (first or last) else ""
    if not name:
        name = rules.name_key(_text(person, "raw_name", "name"))
    company = rules.name_key(_text(person, "company", "companyName", "company_name"))
    return {
        "key_linkedin": linkedin,
        "key_email": email,
        "key_name_company": f"{name}|{company}" if name and company else "",
    }


def _has_key(keys: Mapping[str, str]) -> bool:
    return any(keys.get(k) for k in ("key_linkedin", "key_email", "key_name_company"))


# --- contact memory ---------------------------------------------------------------------------


# The key index the duplicate check searches by: the first key the entry has. Named explicitly
# (`INDEXED BY`) because SQLite otherwise prefers `idx_contact_log_person` (person_id IS NULL
# for imports), which scans every earlier imported row and makes a large import quadratic.
_KEY_INDEXES = (
    ("key_linkedin", "idx_contact_log_linkedin"),
    ("key_email", "idx_contact_log_email"),
    ("key_name_company", "idx_contact_log_name"),
)


def _same_log_sql(keys: Mapping[str, str]) -> tuple[str, str]:
    """`(sql, first key value)` finding the logged row identical to an entry, through the index
    of the entry's first key."""
    column, index = next((k, i) for k, i in _KEY_INDEXES if keys.get(k))
    sql = (
        f"SELECT id, at FROM contact_log INDEXED BY {index} "
        f"WHERE project_id = ? AND {column} = ? AND campaign_id IS ? "
        "AND channel = ? AND person_id IS ? AND key_linkedin = ? AND key_email = ? "
        "AND key_name_company = ?"
    )
    return sql, keys[column]


def _insert_log(
    conn: sqlite3.Connection,
    project_id: int,
    entries: Iterable[tuple[dict[str, str], int | None]],
    channel: str,
    campaign_id: int | None,
    source: str,
    at: str,
    refresh: bool = False,
) -> tuple[int, int]:
    """Insert `(keys, person_id)` entries; identical rows are not repeated. `(logged, skipped)`.

    With `refresh` (an import that carries a real contact date), an identical row whose stored
    date is older than `at` is moved forward to `at` (and counted as logged), so a newer
    `last_contacted` is never lost; a same or older date changes nothing."""
    logged = skipped = 0
    for keys, person_id in entries:
        if not _has_key(keys):
            skipped += 1
            continue
        sql, first = _same_log_sql(keys)
        rows = conn.execute(
            sql,
            (project_id, first, campaign_id, channel, person_id, keys["key_linkedin"],
             keys["key_email"], keys["key_name_company"]),
        ).fetchall()  # fmt: skip
        if rows:
            newest = max(rows, key=lambda r: (r[1], r[0]))
            if refresh and newest[1] < at:
                conn.execute("UPDATE contact_log SET at = ? WHERE id = ?", (at, newest[0]))
                logged += 1
            else:
                skipped += 1
            continue
        db.insert(
            conn, "contact_log", project_id=project_id, campaign_id=campaign_id,
            person_id=person_id, channel=channel, at=at, source=source[:200], **keys,
        )  # fmt: skip
        logged += 1
    return logged, skipped


def log_found_in(
    conn: sqlite3.Connection, project_id: int, campaign_id: int, people: Iterable[Mapping[str, Any]]
) -> None:
    """Log newly confirmed people (`found`) inside the caller's open transaction."""
    entries = [(make_keys(p), p.get("person_id") or p.get("id")) for p in people]
    _insert_log(conn, project_id, entries, "found", campaign_id, "", db.now())


def forget_found(conn: sqlite3.Connection, person_ids: Iterable[int]) -> None:
    """Remove the `found` entries of people who are no longer confirmed (inside the caller's
    transaction), so a wrong person does not block a later campaign."""
    ids = list(dict.fromkeys(person_ids))
    if ids:
        conn.execute(
            f"DELETE FROM contact_log WHERE channel = 'found' AND person_id IN "
            f"({', '.join('?' for _ in ids)})",
            ids,
        )


def log_contacts(
    project_id: int,
    rows: Iterable[Mapping[str, Any]],
    channel: str,
    campaign_id: int | None = None,
    source: str = "",
    at: str | None = None,
) -> dict[str, int]:
    """Remember people in a project. `rows` are mappings read by `make_keys` (plus an optional
    `person_id`); `channel` is one of found, exported_csv, lemlist, email_sent, imported.
    `at` (ISO date or time, UTC) defaults to now; an import may pass the real contact date.
    A row may carry its own `at`, which wins over the call's. Rows with no usable key, and rows
    already logged identically, are skipped.
    Returns `{"logged", "skipped"}`. Raises `ProjectError`."""
    with db.session() as conn:
        return log_contacts_in(conn, project_id, rows, channel, campaign_id, source, at)


def _at_stamp(at: Any) -> str:
    moment = _parse(at) if isinstance(at, str) and at.strip() else None
    if moment is None:
        raise ProjectError("invalid_value", "at must be an ISO date such as 2026-09-01.")
    return _stamp(moment)


def log_contacts_in(
    conn: sqlite3.Connection,
    project_id: int,
    rows: Iterable[Mapping[str, Any]],
    channel: str,
    campaign_id: int | None = None,
    source: str = "",
    at: str | None = None,
) -> dict[str, int]:
    """`log_contacts` inside the caller's transaction (the caller commits or rolls back)."""
    if channel not in CHANNELS:
        raise ProjectError("invalid_channel", f"channel must be one of: {', '.join(CHANNELS)}.")
    default = db.now() if at is None else _at_stamp(at)
    _row(conn, project_id)
    logged = skipped = 0
    for row in rows:
        stamp = _at_stamp(row["at"]) if row.get("at") else default
        n_logged, n_skipped = _insert_log(
            conn, project_id, [(make_keys(row), row.get("person_id"))], channel, campaign_id,
            source, stamp, refresh=bool(row.get("at")) or at is not None,
        )  # fmt: skip
        logged += n_logged
        skipped += n_skipped
    return {"logged": logged, "skipped": skipped}


def log_people(
    person_ids: Sequence[int],
    channel: str,
    source: str = "",
    emails: Mapping[int, str] | None = None,
) -> dict[str, int]:
    """Log existing people (rows of `people`) in the project of their campaign. `emails` maps a
    person id to the address used (exports, lemlist, sending). Returns `{"logged", "skipped"}`."""
    ids = list(dict.fromkeys(int(i) for i in person_ids))
    if not ids:
        return {"logged": 0, "skipped": 0}
    emails = emails or {}
    logged = skipped = 0
    stamp = db.now()
    with db.session() as conn:
        rows = db.query(
            conn,
            "SELECT p.id, p.campaign_id, p.linkedin_url, p.first_name, p.last_name, p.raw_name, "
            "co.name AS company, c.project_id AS project_id FROM people p "
            "JOIN companies co ON co.id = p.company_id JOIN campaigns c ON c.id = p.campaign_id "
            f"WHERE p.id IN ({', '.join('?' for _ in ids)}) ORDER BY p.id",
            ids,
        )
        for row in rows:
            if row["project_id"] is None:
                skipped += 1
                continue
            keys = make_keys({**row, "email": emails.get(row["id"], "")})
            n_logged, n_skipped = _insert_log(
                conn, row["project_id"], [(keys, row["id"])], channel, row["campaign_id"],
                source, stamp,
            )  # fmt: skip
            logged += n_logged
            skipped += n_skipped
    return {"logged": logged, "skipped": skipped}


def remember(
    person_ids: Sequence[int],
    channel: str,
    source: str = "",
    emails: Mapping[int, str] | None = None,
) -> None:
    """`log_people` for the places that already did the real work (a CSV was written, a lead
    was pushed, an email was sent): a problem with the log is recorded in the log file (kind
    only) and never undoes or hides that work."""
    try:
        log_people(person_ids, channel, source, emails)
    except Exception as exc:  # the memory is a convenience; the caller's work already happened
        _log.error("contact memory not updated (%s): %s", channel, type(exc).__name__)


@dataclass
class ContactMemory:
    """The project's log inside its cooldown, indexed by key (see `load_memory`)."""

    project_id: int
    cooldown_days: int
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)

    def lookup(
        self,
        keys: Mapping[str, str],
        exclude_campaign_id: int | None = None,
        below_person_id: int | None = None,
    ) -> dict[str, Any] | None:
        """The latest logged contact matching the LinkedIn key, else the email, else the name
        and company, or None (also None when the cooldown is 0). Entries of
        `exclude_campaign_id` are ignored: a campaign does not block its own people. With
        `below_person_id`, `found` entries of a later person (a higher person id) are ignored
        too, so the earlier of two copies of one person is not blocked by the later one;
        exports, sends and imports still count."""
        if self.cooldown_days <= 0:
            return None
        for kind in ("key_linkedin", "key_email", "key_name_company"):
            value = keys.get(kind) or ""
            if not value:
                continue
            rows = [
                r for r in self.by_key.get((kind, value), [])
                if (exclude_campaign_id is None or r["campaign_id"] != exclude_campaign_id)
                and not (
                    below_person_id is not None
                    and r["channel"] == "found"
                    and r["person_id"] is not None
                    and r["person_id"] > below_person_id
                )
            ]  # fmt: skip
            if rows:
                return _hit(max(rows, key=lambda r: (r["at"], r["id"])), self.cooldown_days)
        return None


def _hit(row: Mapping[str, Any], days: int) -> dict[str, Any]:
    moment = _parse(row["at"])
    again = (moment + timedelta(days=days)).date().isoformat() if moment else ""
    return {
        "at": row["at"],
        "channel": row["channel"],
        "campaign_id": row["campaign_id"],
        "campaign_name": row.get("campaign_name") or "",
        "source": row.get("source") or "",
        "again_after": again,
    }


def _log_rows(
    conn: sqlite3.Connection, project_id: int, cutoff: str, keys: Mapping[str, str] | None = None
) -> list[dict[str, Any]]:
    sql = (
        "SELECT l.*, c.name AS campaign_name FROM contact_log l "
        "LEFT JOIN campaigns c ON c.id = l.campaign_id WHERE l.project_id = ? AND l.at >= ?"
    )
    params: list[Any] = [project_id, cutoff]
    if keys is not None:
        parts = []
        for kind in ("key_linkedin", "key_email", "key_name_company"):
            if keys.get(kind):
                parts.append(f"l.{kind} = ?")
                params.append(keys[kind])
        if not parts:
            return []
        sql += " AND (" + " OR ".join(parts) + ")"
    return db.query(conn, sql, params)


def load_memory(project_id: int) -> ContactMemory:
    """Read the project's log inside the cooldown once, for checking many people."""
    with db.session() as conn:
        project = _row(conn, project_id)
        days = project["cooldown_days"]
        rows = (
            _log_rows(conn, project_id, _stamp(_utcnow() - timedelta(days=days)))
            if days > 0
            else []
        )
    memory = ContactMemory(project_id, days)
    for row in rows:
        for kind in ("key_linkedin", "key_email", "key_name_company"):
            if row[kind]:
                memory.by_key.setdefault((kind, row[kind]), []).append(row)
    return memory


def recently_contacted(
    project_id: int,
    person_or_keys: Mapping[str, Any],
    exclude_campaign_id: int | None = None,
) -> dict[str, Any] | None:
    """Whether the project already found or contacted this person within its cooldown.

    `person_or_keys` is a person (see `make_keys`) or the three `key_*` values. Matching tries
    the LinkedIn profile, then the email, then name + company. Returns the latest entry as
    `{"at", "channel", "campaign_id", "campaign_name", "again_after"}` or None.
    """
    keys = make_keys(person_or_keys)
    with db.session() as conn:
        project = _row(conn, project_id)
        days = project["cooldown_days"]
        if days <= 0 or not _has_key(keys):
            return None
        rows = _log_rows(conn, project_id, _stamp(_utcnow() - timedelta(days=days)), keys)
    memory = ContactMemory(project_id, days)
    for row in rows:
        for kind in ("key_linkedin", "key_email", "key_name_company"):
            if row[kind]:
                memory.by_key.setdefault((kind, row[kind]), []).append(row)
    return memory.lookup(keys, exclude_campaign_id)


def contact_message(hit: Mapping[str, Any]) -> str:
    """Plain-English text for a `recently_contacted` result (used by L24)."""
    day = str(hit["at"])[:10]
    where = f" in campaign '{hit['campaign_name']}'" if hit.get("campaign_name") else ""
    again = f"; can be contacted again after {hit['again_after']}" if hit.get("again_after") else ""
    channel = hit["channel"]
    if channel == "found":
        what = f"Already found on {day}{where}"
    elif channel == "imported":
        what = f"Already in the contacts you imported (on {day})"
    else:
        what = f"Already contacted on {day}{where}"
    return what + again


# --- people already in another campaign of the project (L25) ----------------------------------


@dataclass
class ActivePeople:
    """Confirmed and reserve people of the project's other campaigns (see `load_active_people`)."""

    by_key: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)

    def lookup(
        self, keys: Mapping[str, str], below_person_id: int | None = None
    ) -> dict[str, Any] | None:
        """The earliest matching person (LinkedIn, then email, then name + company). With
        `below_person_id` only people created before that person count."""
        for kind in ("key_linkedin", "key_email", "key_name_company"):
            value = keys.get(kind) or ""
            hit = self.by_key.get((kind, value)) if value else None
            if hit is not None and (below_person_id is None or hit["person_id"] < below_person_id):
                return hit
        return None


def load_active_people(
    project_id: int, exclude_campaign_ids: Iterable[int] = ()
) -> ActivePeople:
    """Index the people who are `confirmed` or `reserve` in the project's campaigns that are not
    archived, except those of `exclude_campaign_ids`."""
    out = ActivePeople()
    with db.session() as conn:
        sql = (
            "SELECT p.id AS person_id, p.campaign_id, c.name AS campaign_name, p.status, "
            "p.linkedin_url, p.first_name, p.last_name, p.raw_name, co.name AS company "
            "FROM people p JOIN campaigns c ON c.id = p.campaign_id "
            "JOIN companies co ON co.id = p.company_id "
            "WHERE c.project_id = ? AND c.status <> 'archived' "
            "AND p.status IN ('confirmed', 'reserve')"
        )
        params: list[Any] = [project_id]
        excluded = list(dict.fromkeys(exclude_campaign_ids))
        if excluded:
            sql += f" AND c.id NOT IN ({', '.join('?' for _ in excluded)})"
            params.extend(excluded)
        people = db.query(conn, sql + " ORDER BY p.id", params)
        addresses: dict[int, list[str]] = {}
        if people:
            for row in db.query(
                conn,
                "SELECT e.person_id, e.email FROM emails e JOIN people p ON p.id = e.person_id "
                "JOIN campaigns c ON c.id = p.campaign_id WHERE c.project_id = ? "
                "AND e.status <> 'invalid'",
                (project_id,),
            ):
                addresses.setdefault(row["person_id"], []).append(str(row["email"]).lower())
    for person in people:
        hit = {
            "person_id": person["person_id"],
            "campaign_id": person["campaign_id"],
            "campaign_name": person["campaign_name"],
            "status": person["status"],
        }
        keys = make_keys(person)
        pairs = [
            ("key_linkedin", keys["key_linkedin"]),
            ("key_name_company", keys["key_name_company"]),
        ]
        pairs += [("key_email", a) for a in addresses.get(person["person_id"], [])]
        for kind, value in pairs:
            if value:
                out.by_key.setdefault((kind, value), hit)  # people are ordered: earliest wins
    return out


class ContactScreen:
    """Checks people against the project's memory (L24) and its other active campaigns (L25).

    The memory and the people index are read from the database on first use, so one screen can
    check many people. `campaign_id` (and `also_exclude`) are the campaigns whose own people
    and log entries never count against a person.
    """

    def __init__(
        self, project_id: int | None, campaign_id: int, also_exclude: Iterable[int] = ()
    ) -> None:
        self.project_id = project_id
        self.campaign_id = campaign_id
        self.excluded = [campaign_id, *also_exclude]
        self._memory: ContactMemory | None = None
        self._active: ActivePeople | None = None

    def findings(
        self, keys: Mapping[str, str], below_person_id: int | None = None
    ) -> list[tuple[str, str]]:
        """`(code, plain-English text)` pairs: `L25` when a person with these keys is `confirmed`
        or `reserve` in another active campaign of the project (only people created before
        `below_person_id` when given), `L24` when the project found or contacted them inside
        its cooldown (`found` entries of later people are ignored when `below_person_id` is
        given). A `found` entry of the very campaign that already gave L25 is not repeated
        as L24."""
        if self.project_id is None:
            return []
        if self._active is None or self._memory is None:
            self._active = load_active_people(self.project_id, self.excluded)
            self._memory = load_memory(self.project_id)
        out: list[tuple[str, str]] = []
        other = self._active.lookup(keys, below_person_id)
        if other is not None:
            out.append(
                (
                    "L25",
                    f"already in campaign '{other['campaign_name']}' of this project "
                    f"(person {other['person_id']}, {other['status']})",
                )
            )
        hit = self._memory.lookup(keys, self.campaign_id, below_person_id)
        if hit is not None and not (
            other is not None
            and hit["channel"] == "found"
            and hit["campaign_id"] == other["campaign_id"]
        ):
            out.append(("L24", contact_message(hit)))
        return out


# --- migration helper -------------------------------------------------------------------------

_EMAIL_RANK = {"verified": 0, "risky": 1, "guess": 2, "unknown": 3}


def backfill_v1_contacts(conn: sqlite3.Connection) -> None:
    """Seed the contact log of a workspace migrated from v1 (called by the v2 migration, inside
    its transaction): every confirmed person is logged as `found` (at the time they were
    stored) and every address that received an email as `email_sent`, so a new campaign in the
    same project does not offer them again. Nothing else is touched."""
    people = db.query(
        conn,
        "SELECT p.id, p.campaign_id, p.created_at, p.linkedin_url, p.first_name, p.last_name, "
        "p.raw_name, co.name AS company, c.project_id AS project_id FROM people p "
        "JOIN campaigns c ON c.id = p.campaign_id JOIN companies co ON co.id = p.company_id "
        "WHERE p.status = 'confirmed' AND c.project_id IS NOT NULL ORDER BY p.id",
    )
    best: dict[int, tuple[int, str]] = {}
    for row in db.query(
        conn, "SELECT person_id, email, status FROM emails WHERE status <> 'invalid' ORDER BY id"
    ):
        rank = _EMAIL_RANK.get(row["status"], 9)
        current = best.get(row["person_id"])
        if current is None or rank <= current[0]:
            best[row["person_id"]] = (rank, str(row["email"]).lower())
    for row in people:
        email = best.get(row["id"], (0, ""))[1]
        keys = make_keys({**row, "email": email})
        _insert_log(
            conn, row["project_id"], [(keys, row["id"])], "found", row["campaign_id"],
            "migrated from v1", row["created_at"],
        )  # fmt: skip
    sent = db.query(
        conn,
        "SELECT s.to_email, s.sent_at, p.id, p.campaign_id, p.created_at, p.linkedin_url, "
        "p.first_name, p.last_name, p.raw_name, co.name AS company, c.project_id AS project_id "
        "FROM sends s JOIN messages m ON m.id = s.message_id JOIN people p ON p.id = m.person_id "
        "JOIN campaigns c ON c.id = p.campaign_id JOIN companies co ON co.id = p.company_id "
        "WHERE s.status = 'sent' AND c.project_id IS NOT NULL ORDER BY s.id",
    )
    for row in sent:
        keys = make_keys({**row, "email": row["to_email"]})
        _insert_log(
            conn, row["project_id"], [(keys, row["id"])], "email_sent", row["campaign_id"],
            "migrated from v1", row["sent_at"] or row["created_at"],
        )  # fmt: skip
