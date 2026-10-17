"""The do-not-contact list: emails, domains and LinkedIn profiles that every check respects.

`add` (the `add_suppression` MCP tool and the mailbox sync use it), `is_suppressed`, `list` and
`remove`. `remove` is for the CLI only: the AI can add to the list but never take anyone off it.
Values are normalised before they are stored (lower-case email, bare host name, canonical
`https://www.linkedin.com/in/<slug>` link), so the same person is never stored twice.
"""

from __future__ import annotations

import builtins
import re
import sqlite3
from typing import Any

from leadgen import db
from leadgen.services.confirm import canonical_url, url_key
from leadgen.services.emails import clean_domain

KINDS = ("email", "domain", "linkedin")
MAX_VALUES = 500
_EMAIL_RE = re.compile(r"[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+")
_DOMAIN_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+")


class SuppressionError(Exception):
    """A do-not-contact request was refused. `code` is stable text; `hint` says what to do."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def as_response(self) -> dict[str, Any]:
        """The reply to give the AI."""
        return {
            "ok": False,
            "error": {"code": self.code, "message": self.message, "hint": self.hint},
        }


def normalize(value: str, kind: str) -> str:
    """The stored form of `value` for `kind`, or "" when it is not a valid value of that kind."""
    text = (value or "").strip()
    if kind == "email":
        text = text.lower()
        return text if _EMAIL_RE.fullmatch(text) else ""
    if kind == "domain":
        host = clean_domain(text)
        return host if _DOMAIN_RE.fullmatch(host) else ""
    if kind == "linkedin":
        return canonical_url(text)
    return ""


def _check_kind(kind: str) -> None:
    if kind not in KINDS:
        raise SuppressionError(
            "invalid_value",
            f"kind must be one of: {', '.join(KINDS)}.",
            "Call again with kind email, domain or linkedin.",
        )


def add_in(
    conn: sqlite3.Connection, values: builtins.list[str], kind: str, reason: str = ""
) -> dict[str, Any]:
    """`add` on an open connection (the caller commits). Same reply as `add`."""
    _check_kind(kind)
    if not isinstance(values, (builtins.list, tuple)):
        raise SuppressionError("invalid_value", "values must be a list of text values.")
    if not values:
        raise SuppressionError("invalid_value", "values is empty.", "Give at least one value.")
    if len(values) > MAX_VALUES:
        raise SuppressionError(
            "too_many_values", f"At most {MAX_VALUES} values per call.", "Split them up."
        )
    reason = " ".join(str(reason or "").split())[:300]
    added: builtins.list[str] = []
    present: builtins.list[str] = []
    invalid: builtins.list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in values:
        stored = normalize(str(raw), kind)
        if not stored:
            invalid.append({"value": str(raw)[:200], "reason": f"not a valid {kind}"})
            continue
        if stored in seen:
            continue
        seen.add(stored)
        exists = db.query(
            conn, "SELECT id FROM suppression WHERE kind = ? AND value = ?", (kind, stored)
        )
        if exists:
            present.append(stored)
            continue
        db.insert(conn, "suppression", value=stored, kind=kind, reason=reason)
        added.append(stored)
    return {"ok": True, "kind": kind, "added": added, "already_listed": present, "invalid": invalid}


def add(values: builtins.list[str], kind: str, reason: str = "") -> dict[str, Any]:
    """Put emails, domains or LinkedIn links on the do-not-contact list.

    Returns `added`, `already_listed` and `invalid` (values that are not of that kind).
    """
    with db.session() as conn:
        result = add_in(conn, values, kind, reason)
        total = conn.execute("SELECT COUNT(*) FROM suppression").fetchone()[0]
    result["total_on_list"] = int(total)
    return result


def _entries(conn: sqlite3.Connection) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {k: set() for k in KINDS}
    for row in db.query(conn, "SELECT value, kind FROM suppression"):
        if row["kind"] in out:
            out[row["kind"]].add(str(row["value"]).strip().lower())
    return out


def _domain_listed(host: str, domains: set[str]) -> bool:
    host = clean_domain(host)
    while host:
        if host in domains:
            return True
        host = host.partition(".")[2]
    return False


def is_suppressed(
    email: str | None = None, domain: str | None = None, linkedin_url: str | None = None
) -> bool:
    """True when any given value is on the list: the email itself, the email's or `domain`'s
    host (a listed parent domain covers its sub-domains), or the LinkedIn profile."""
    with db.session() as conn:
        entries = _entries(conn)
    if email:
        address = email.strip().lower()
        if address in entries["email"] or _domain_listed(address, entries["domain"]):
            return True
    if domain and _domain_listed(domain, entries["domain"]):
        return True
    if linkedin_url:
        key = url_key(linkedin_url)
        if key and any(url_key(v) == key for v in entries["linkedin"]):
            return True
    return False


def list(kind: str | None = None) -> builtins.list[dict[str, Any]]:
    """Everything on the list (newest first), optionally only one `kind`."""
    if kind is not None:
        _check_kind(kind)
    sql = "SELECT value, kind, reason, created_at FROM suppression"
    args: tuple[str, ...] = ()
    if kind:
        sql += " WHERE kind = ?"
        args = (kind,)
    with db.session() as conn:
        rows = db.query(conn, sql + " ORDER BY id DESC", args)
    return [dict(r) for r in rows]


def remove(value: str, kind: str) -> bool:
    """Take one value off the list (CLI only). Returns whether it was on the list."""
    _check_kind(kind)
    stored = normalize(value, kind) or (value or "").strip().lower()
    with db.session() as conn:
        found = db.query(
            conn, "SELECT id FROM suppression WHERE kind = ? AND value = ?", (kind, stored)
        )
        if not found:
            return False
        conn.execute("DELETE FROM suppression WHERE id = ?", (found[0]["id"],))
    return True
