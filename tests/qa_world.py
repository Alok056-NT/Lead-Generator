"""Shared builders for the QA tests (T11). Every person, company and offer here is invented.

Two styles: plain dicts (`person_row`, `company_row`, `campaign_dict`) for the pure checks, and
database builders (`make_campaign`, `add_company`, `add_person`, ...) for `run_qa` and the review
loop. The default person passes every lead check; the default message passes every message check.
"""

from __future__ import annotations

import itertools
from typing import Any

from leadgen import db
from leadgen.services import campaign as camp
from leadgen.services import offer, projects
from leadgen.services.designation_templates import TEMPLATE_DATA

PROFILE: dict[str, Any] = {
    "name": "Bright Harbour Training",
    "one_liner": "Practical training for supervisors and managers.",
    "description": "We run short courses for supervisors. Trainers are working practitioners.",
    "services": [
        {"name": "Supervisor courses", "what": "Short supervisor courses", "best_for": ["ops"]},
        {"name": "Leadership programmes", "what": "For new managers", "best_for": ["L&D"]},
    ],
    "audiences": ["HR and L&D heads"],
    "confirmed_claims": ["Courses are taught by working practitioners"],
    "forbidden_claims": ["Guaranteed results", "We cut accidents at all client sites by half"],
    "proof_points": [{"claim": "Trained 1,200 managers", "source": "About page"}],
    "ctas": ["15-minute call"],
    "sender": {"name": "Priya Nair", "company": "Bright Harbour Training", "role": "Partner"},
    "tone": "professional, plain, no hype",
    "language": "en-GB",
}

COMPANY = "Northwind Logistics"
_n = itertools.count(1)


# --- plain dicts (pure checks) ------------------------------------------------------------------


def group_dicts(*keys: str) -> list[dict[str, Any]]:
    return [
        {
            "label": TEMPLATE_DATA[k]["label"],
            "keywords": list(TEMPLATE_DATA[k]["keywords"]),
            "search_terms": list(TEMPLATE_DATA[k]["search_terms"]),
        }
        for k in keys
    ]


def campaign_dict(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 1,
        "countries": ["SG"],
        "sectors": ["logistics"],
        "designations": group_dicts("ld", "hr"),
        "seniority_min": "manager",
        "exclusions": {"companies": [], "domains": [], "people": [], "competitors": []},
        "channels": ["linkedin", "email"],
        "options": {"allow_risky_emails": False, "allow_guess_emails": False},
        "locale": "en-GB",
    }
    base.update(over)
    return base


def company_row(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 1, "name": COMPANY, "aliases": [], "domain": "northwind.example", "country": "",
        "status": "approved", "sector": "logistics", "size_note": "", "notes": "",
    }  # fmt: skip
    base.update(over)
    return base


def person_row(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 1, "campaign_id": 1, "company_id": 1, "first_name": "Aria", "last_name": "Tan",
        "greeting_name": "Aria", "raw_name": "Aria Tan",
        "title": "Head of Learning & Development", "designation": "L&D / Training heads",
        "seniority": "director", "country": "Singapore",
        "linkedin_url": "https://www.linkedin.com/in/aria-tan-1a2b3c",
        "evidence_title": f"Aria Tan - Head of Learning & Development at {COMPANY} | LinkedIn",
        "evidence_snippet": (
            f"Head of Learning & Development at {COMPANY} · Singapore · Experience: {COMPANY}"
        ),
        "evidence_origin": "api", "spot_check": None, "confirmed": 1, "reasons_json": [],
        "status": "confirmed",
    }  # fmt: skip
    base.update(over)
    return base


def email_row(status: str = "verified", address: str = "aria.tan@northwind.example") -> dict:
    return {"id": 1, "person_id": 1, "email": address, "status": status}


def message_row(**over: Any) -> dict[str, Any]:
    body = (
        f"Hi Aria, I see you lead learning and development at {COMPANY}. We run practical "
        "programmes for new managers, and our trainers are working practitioners. "
        "Would a short outline be useful to you?"
    )
    base: dict[str, Any] = {
        "id": None, "channel": "linkedin_message", "subject": "", "body": body,
        "ai_review": "", "status": "qa_pass",
    }  # fmt: skip
    base.update(over)
    return base


def email_message(**over: Any) -> dict[str, Any]:
    filler = (
        "At Bright Harbour Training we run short supervisor courses that fit around shift "
        "rosters. The courses are practical and taught by working practitioners, and they suit "
        "teams that cannot leave site for long. We also run programmes for new managers."
    )
    body = (
        f"Hi Aria,\n\nI saw that you lead learning and development at {COMPANY}.\n\n{filler}\n\n"
        "Would a 15-minute call to compare notes be useful?\n\nKind regards,\nPriya"
    )
    values = {"channel": "email", "subject": f"Training for {COMPANY}", "body": body, **over}
    return message_row(**values)


# --- database builders (run_qa, review) ---------------------------------------------------------


def make_campaign(
    channels=("email", "linkedin"), options=None, sectors=("logistics",), profile=True, **extra
) -> dict[str, Any]:
    with db.session() as conn:
        offer_id = db.insert(conn, "offers", name="Bright Harbour", source_type="text")
    if profile:
        offer.save_profile(offer_id, PROFILE)
    return camp.create_campaign(
        name=f"SG logistics {next(_n)}",
        offer_id=offer_id,
        project_id=projects.ensure_project_for_offer(offer_id),
        countries=["SG"],
        sectors=list(sectors),
        company_size="200-1000",
        channels=list(channels),
        designations=["ld", "hr"],
        options=options,
        **extra,
    )


def add_company(cid: int, name: str = COMPANY, domain: str = "northwind.example", **fields) -> int:
    with db.session() as conn:
        return db.insert(
            conn, "companies", campaign_id=cid, name=name, domain=domain, status="approved",
            sector="logistics", **fields,
        )  # fmt: skip


def add_person(
    cid: int, company_id: int, first="Aria", last="Tan", company=COMPANY, **fields
) -> int:
    n = next(_n)
    values = person_row(
        first_name=first,
        last_name=last,
        greeting_name=first,
        raw_name=f"{first} {last}",
        linkedin_url=f"https://www.linkedin.com/in/{first.lower()}-{last.lower()}-{n:04d}ab",
        evidence_title=f"{first} {last} - Head of Learning & Development at {company} | LinkedIn",
        evidence_snippet=(
            f"Head of Learning & Development at {company} · Singapore · Experience: {company}"
        ),
    )
    values.update(fields)
    for key in ("id", "aliases"):
        values.pop(key, None)
    values["campaign_id"], values["company_id"] = cid, company_id
    with db.session() as conn:
        return db.insert(conn, "people", **values)


def add_email(person_id: int, status: str = "verified", address: str | None = None) -> int:
    with db.session() as conn:
        return db.insert(
            conn, "emails", person_id=person_id, status=status,
            email=address or f"p{person_id}@northwind.example",
        )  # fmt: skip


def add_message(person_id: int, channel: str = "linkedin_message", **fields) -> int:
    base = message_row() if channel != "email" else email_message()
    values = {k: base[k] for k in ("subject", "body")}
    values.update(channel=channel, status="qa_pass", ai_review="")
    values.update(fields)
    with db.session() as conn:
        return db.insert(conn, "messages", person_id=person_id, **values)


def get_message(message_id: int) -> dict[str, Any]:
    with db.session() as conn:
        return db.get(conn, "messages", message_id)


def get_person(person_id: int) -> dict[str, Any]:
    with db.session() as conn:
        return db.get(conn, "people", person_id)


def suppress(kind: str, value: str) -> None:
    with db.session() as conn:
        db.insert(conn, "suppression", kind=kind, value=value, reason="test")
