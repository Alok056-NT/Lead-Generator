"""Prospect-list export — the list itself is the product.

Produces a CSV that drops straight into Instantly, Smartlead, or any
sequencer (email / first_name / last_name / company_name / personalization
columns), plus Harvey's own quality columns (score, email_status, signals)
so a human can eyeball what they're getting. ``designation`` buckets each
title into one of four labels for easy filtering.
"""

import csv
import io
import logging
import re

from harvey.state import StateManager

logger = logging.getLogger("harvey.export")

# Column order chosen so the file imports cleanly into cold-email tools
# without remapping; extra columns are simply available as custom variables.
CSV_COLUMNS = [
    "email", "first_name", "last_name", "company_name", "title", "designation",
    "website", "linkedin_url", "industry", "personalization",
    "email_status", "score", "seniority", "source", "status", "created_at",
]

DEFAULT_EMAIL_STATUSES = ["verified", "risky"]

# Filterable bucket for a job title, so a list can be cut by "who is this
# person" without reading every title. First matching bucket wins, so a
# "Head of Training & Safety" lands with L&D, not Safety.
#
# Each rule is matched against the title as space-separated words (so "hr"
# matches "HR Director" but not "Chrome"), and a multi-word rule must appear
# as a consecutive run of words. A trailing "*" means "starts with".
DESIGNATION_RULES = [
    ("L&D / Training heads", (
        "learning", "l&d", "training", "trainer",
        "talent development", "capability", "organisational development",
        "organizational development", "organisation development",
        "organization development", "leadership development",
        "employee development", "people development", "staff development",
        "instructional", "elearning",
    )),
    ("Safety / Operations", (
        "safety", "ehs", "wsh", "hse", "operation*", "facilit*", "quality",
        "compliance", "maintenance", "production", "plant", "site manager",
    )),
    ("HR leaders", (
        "hr", "hrbp", "human resource*", "people", "talent", "recruit*", "culture",
        "employee experience", "chro",
    )),
    ("Business owners / MDs", (
        "ceo", "founder", "co-founder", "cofounder", "owner", "proprietor",
        "managing director", "managing partner", "md", "general manager",
        "president", "chairman", "principal", "executive director",
        "country manager", "country head",
    )),
]
DESIGNATION_OTHER = "Other"

# Phrases that contain a rule word but mean something else entirely
# ("Machine Learning Engineer" is not in L&D). Removed before matching.
_DESIGNATION_FALSE_FRIENDS = re.compile(
    r"\b(machine|deep|reinforcement|statistical)\s+learning\b"
    r"|\bmodel\s+training\b|\bquality\s+of\s+life\b"
    r"|\b(it|network|cloud|security|cyber|data|it infrastructure)\s+operations\b"
)


def _title_words(title: str) -> list[str]:
    """Lower-case words of a title; punctuation splits words, but
    "L&D" / "L & D" / "L and D" are kept whole as the token "l&d"."""
    t = _DESIGNATION_FALSE_FRIENDS.sub(" ", (title or "").lower())
    # A vice president is not a company president (owner bucket).
    t = re.sub(r"\bvice[\s-]+president\b", "vp", t)
    t = re.sub(r"\bl\s*(?:&|and)\s*d\b", "l&d", t)
    t = re.sub(r"[^a-z0-9&]+", " ", t)
    words: list[str] = []
    for w in t.split():
        if w == "l&d":
            words.append(w)
        else:
            words.extend(x for x in w.split("&") if x)
    return words


def _rule_matches(rule: str, words: list[str]) -> bool:
    rule_words = rule.replace("-", " ").split()
    n = len(rule_words)
    for i in range(len(words) - n + 1):
        ok = True
        for rw, w in zip(rule_words, words[i:i + n]):
            if rw.endswith("*"):
                ok = w.startswith(rw[:-1])
            else:
                ok = w == rw
            if not ok:
                break
        if ok:
            return True
    return False


def classify_designation(title: str) -> str:
    """Map a job title to one of four filterable labels (or "Other")."""
    if not _title_words(title):
        return ""
    # Classify the role, not the employer: in "CEO at ISS Facility Services"
    # the word "Facility" belongs to the company name.
    role = re.split(r"\s+at\s+|\s*@\s*", title or "", maxsplit=1)[0]
    words = _title_words(role) or _title_words(title)
    for label, rules in DESIGNATION_RULES:
        if any(_rule_matches(r, words) for r in rules):
            return label
    return DESIGNATION_OTHER


async def collect_prospects(
    state: StateManager,
    email_statuses: list[str] | None = None,
    min_score: int = 0,
    statuses: list[str] | None = None,
    include_all: bool = False,
) -> list[dict]:
    """Fetch prospects matching the filters as export-ready dicts.

    Default view is the *deliverable* list (verified + risky emails).
    ``include_all`` disables every filter for a full dump.
    """
    import aiosqlite

    where = []
    params: list = []
    if not include_all:
        wanted = email_statuses if email_statuses is not None else DEFAULT_EMAIL_STATUSES
        if wanted:
            placeholders = ",".join("?" for _ in wanted)
            where.append(f"email != '' AND email_status IN ({placeholders})")
            params.extend(wanted)
        if min_score > 0:
            where.append("score >= ?")
            params.append(min_score)
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            where.append(f"status IN ({placeholders})")
            params.extend(statuses)

    sql = "SELECT * FROM prospects"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY score DESC, created_at DESC"

    async with aiosqlite.connect(state.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cursor:
            rows = [dict(r) for r in await cursor.fetchall()]

    out = []
    for r in rows:
        out.append({
            "email": r.get("email", ""),
            "first_name": r.get("first_name", ""),
            "last_name": r.get("last_name", ""),
            "company_name": r.get("company", ""),
            "title": r.get("title", ""),
            "designation": classify_designation(r.get("title", "")),
            "website": r.get("source_url", ""),
            "linkedin_url": r.get("linkedin_url", ""),
            "industry": r.get("industry", ""),
            "personalization": r.get("personalization_notes", ""),
            "email_status": r.get("email_status", ""),
            "score": r.get("score", 0),
            "seniority": r.get("seniority", ""),
            "source": r.get("source", ""),
            "status": r.get("status", ""),
            "created_at": r.get("created_at", ""),
        })
    return out


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


async def export_prospects_csv(
    state: StateManager,
    out_path: str | None = None,
    email_statuses: list[str] | None = None,
    min_score: int = 0,
    statuses: list[str] | None = None,
    include_all: bool = False,
) -> tuple[int, str]:
    """Export prospects to CSV. Returns (row_count, csv_text).

    Writes to ``out_path`` when given; caller handles printing/serving.
    """
    rows = await collect_prospects(
        state,
        email_statuses=email_statuses,
        min_score=min_score,
        statuses=statuses,
        include_all=include_all,
    )
    text = to_csv(rows)
    if out_path:
        with open(out_path, "w", newline="") as f:
            f.write(text)
        logger.info(f"Exported {len(rows)} prospects to {out_path}")
    return len(rows), text
