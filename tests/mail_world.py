"""Builders for the mailbox and analytics tests (T15). Every person and address is invented.

The cast (see tests/fixtures/mail): the user is Jordan Reyes <jordan.reyes@example.com>; each
fixture answers one of the sends seeded by `seed_world`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qa_world import add_company, add_email, add_message, add_person, make_campaign

from leadgen import db

MAIL_DIR = Path(__file__).parent / "fixtures" / "mail"
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)

# (first, last, address, Message-ID the tool sent, sent_at)
SENDS = {
    "send-001": ("Aria", "Tan", "aria.tan@example.org", "2026-10-12T02:02:40.000Z"),
    "send-002": ("Chris", "Lowe", "chris.lowe@example.org", "2026-10-12T02:02:41.000Z"),
    "send-003": ("Sam", "Dolan", "sam.dolan@example.org", "2026-10-13T06:11:07.000Z"),
    "send-004": ("Maya", "Ortiz", "maya.ortiz@example.net", "2026-10-12T09:00:00.000Z"),
    "send-005": ("Dana", "Whitfield", "dana.whitfield@example.org", "2026-10-14T01:00:00.000Z"),
    "send-006": ("Felix", "Brandt", "felix.brandt@example.net", "2026-10-15T09:00:00.000Z"),
    "send-007": ("Nina", "Vogel", "nina.vogel@example.net", "2026-10-15T09:30:00.000Z"),
    "send-008": ("Dev", "Park", "dev.park@example.org", "2026-10-14T09:30:00.000Z"),
    "send-009": ("Ben", "Carter", "ben.carter@example.net", "2026-10-10T09:00:00.000Z"),
}


def fixture(name: str) -> bytes:
    """The raw bytes of a fixture mail (CRLF line endings are part of what is tested)."""
    return (MAIL_DIR / name).read_bytes()


def add_send(
    message_id: int,
    to_email: str,
    provider_message_id: str,
    sent_at: str | None,
    status: str = "sent",
) -> int:
    with db.session() as conn:
        return db.insert(
            conn, "sends", message_id=message_id, provider="smtp", to_email=to_email,
            provider_message_id=provider_message_id, status=status, sent_at=sent_at,
        )  # fmt: skip


def seed_person(campaign_id: int, company_id: int, first: str, last: str, address: str, **kw):
    """A person with an email row and an approved email message: `(person_id, message_id)`."""
    person = add_person(campaign_id, company_id, first, last)
    add_email(person, address=address)
    return person, add_message(person, "email", status="approved", **kw)


def seed_world() -> dict[str, Any]:
    """One campaign, one company and one sent email per entry of `SENDS`.

    Returns `{"campaign": id, "company": id, "send": {name: send_id}, "person": {name: id}}`.
    """
    db.init_db()
    camp = make_campaign(channels=("email",))
    cid = camp["id"]
    company = add_company(cid)
    sends: dict[str, int] = {}
    people: dict[str, int] = {}
    for name, (first, last, address, sent_at) in SENDS.items():
        person, message = seed_person(cid, company, first, last, address)
        people[name] = person
        sends[name] = add_send(message, address, f"<{name}@example.com>", sent_at)
    return {"campaign": cid, "company": company, "send": sends, "person": people}


def events(send_id: int | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM events" + (" WHERE send_id = ?" if send_id else "") + " ORDER BY id"
    with db.session() as conn:
        return db.query(conn, sql, (send_id,) if send_id else ())


def suppressed(kind: str = "email") -> set[str]:
    with db.session() as conn:
        rows = db.query(conn, "SELECT value FROM suppression WHERE kind = ?", (kind,))
    return {r["value"] for r in rows}


def send_row(send_id: int) -> dict[str, Any]:
    with db.session() as conn:
        return db.get(conn, "sends", send_id)


def email_status(person_id: int) -> str:
    with db.session() as conn:
        return db.query(conn, "SELECT status FROM emails WHERE person_id = ?", (person_id,))[0][
            "status"
        ]


def eml(
    from_addr: str,
    subject: str,
    body: str,
    in_reply_to: str = "",
    extra_headers: str = "",
    date: str = "Wed, 14 Oct 2026 10:00:00 +0000",
) -> bytes:
    """A small plain-text mail with CRLF line endings."""
    lines = [
        f"From: {from_addr}",
        "To: Jordan Reyes <jordan.reyes@example.com>",
        f"Date: {date}",
        f"Subject: {subject}",
        "Message-ID: <test-msg@example.net>",
    ]
    if in_reply_to:
        lines += [f"In-Reply-To: {in_reply_to}", f"References: {in_reply_to}"]
    if extra_headers:
        lines.append(extra_headers)
    lines += ["MIME-Version: 1.0", "Content-Type: text/plain; charset=UTF-8", "", body]
    return "\r\n".join(lines).encode("utf-8")
