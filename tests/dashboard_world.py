"""A small invented campaign for the dashboard tests: people, emails, messages, QA, sends.

Everybody here is made up; domains are example.com style.
"""

from __future__ import annotations

from typing import Any

from mail_world import add_send
from qa_world import add_company, add_email, add_message, add_person, make_campaign

from leadgen import db
from leadgen.services import review

SECRET = "sk-dash-0123456789abcdef"


def seed() -> dict[str, Any]:
    """Two companies, four people with different email / QA / AI-review states, a few sends.

    Returns ids: `campaign`, `company_a`, `company_b`, `aria` (clean, reviewed, verified email),
    `ben` (guess email, message with a blocking issue), `cara` (no email, no messages, review
    status) and `dev` (AI-search lead, risky email, pending AI review).
    """
    db.init_db()
    camp = make_campaign(channels=("email", "linkedin"))
    cid = camp["id"]
    company_a = add_company(cid, "Northwind Logistics", "northwind.example")
    company_b = add_company(cid, "Harbour Freight", "harbour.example")
    aria = add_person(cid, company_a, "Aria", "Tan")
    add_email(aria, "verified", "aria.tan@northwind.example")
    add_message(aria, "linkedin_message", ai_review="pass", ai_review_notes="Accurate and polite.")
    mail = add_message(aria, "email", ai_review="pass")
    ben = add_person(
        cid,
        company_b,
        "Ben",
        "Carter",
        company="Harbour Freight",
        designation="HR / People heads",
        title="Head of HR",
    )
    add_email(ben, "guess", "ben.carter@harbour.example")
    add_message(ben, "linkedin_message", body="Hi Ben, we guarantee 100% results! {{company}}")
    cara = add_person(cid, company_b, "Cara", "Ng", company="Harbour Freight", status="review")
    dev = add_person(
        cid, company_a, "Dev", "Rao", evidence_origin="ai_web_search", spot_check="pending"
    )
    add_email(dev, "risky", "dev.rao@northwind.example")
    add_message(dev, "linkedin_message")
    review.run_qa(cid)
    send = add_send(
        mail, "aria.tan@northwind.example", "<dash-1@example.com>", "2026-10-12T09:00:00.000Z"
    )
    with db.session() as conn:
        db.insert(
            conn,
            "events",
            send_id=send,
            type="reply",
            at="2026-10-12T12:00:00.000Z",
            detail="Thanks, send details.",
        )
    return {
        "campaign": cid,
        "company_a": company_a,
        "company_b": company_b,
        "aria": aria,
        "ben": ben,
        "cara": cara,
        "dev": dev,
        "send": send,
    }
