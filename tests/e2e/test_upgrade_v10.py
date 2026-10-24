"""Upgrade test (T29): a v1.0 workspace is copied and opened by v1.1; everything is still there.

The v1.0 database is built at schema version 1 (the migrations are switched off while it is
made) and filled from the T19 universe: the VR studio's offer, one campaign with 12 companies,
every honest search result as a person (confirmed or review as the T19 comments say), some
verified addresses, approved messages, one sent email with a reply, a do-not-contact entry and
a stored setting. The whole workspace folder is then copied (as an owner would back it up),
the copy is opened by the current code and:

- every row of the old tables is still there, nothing was added to them;
- the old campaign sits in a project that carries its offer, and the contact memory knows the
  confirmed people and the emailed address;
- the dashboard answers every request its pages make (status, home, project, campaign, people,
  messages, quality, exports, analytics, mailbox, settings, keys, suppression, uploads/templates).
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from universe import COMPANIES, OFFER_TEXT, SEARCH_RESULTS
from world import PROFILE, expected_outcomes

from leadgen import db
from leadgen.core import paths
from leadgen.dashboard import app as dash
from leadgen.services.designations import resolve_groups

BASE = "http://127.0.0.1:8765"
OLD_TABLES = (
    "offers", "campaigns", "companies", "people", "search_tasks", "emails", "messages",
    "qa_issues", "suppression", "sends", "events", "settings", "api_usage", "search_cache",
)  # fmt: skip
DESIGNATIONS = {"HR leaders": "HR / People heads", "L&D / Training heads": "L&D / Training heads"}


def _iso(days_ago: int) -> str:
    when = datetime.now(UTC) - timedelta(days=days_ago)
    return when.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_v10_workspace() -> dict[str, int]:
    """Create the workspace folder and a schema-1 database in it. Returns counts to check."""
    paths.ensure_workspace()
    real = db.MIGRATIONS
    db.MIGRATIONS = []
    try:
        assert db.init_db() == 1
    finally:
        db.MIGRATIONS = real
    outcomes = expected_outcomes()
    domains = {c["name"]: c for c in COMPANIES}
    with db.session() as conn:
        offer = db.insert(
            conn, "offers", name="Lanternfish VR", source_type="text", raw_text=OFFER_TEXT,
            profile_json=PROFILE,
        )
        campaign = db.insert(
            conn, "campaigns", name="Lanternfish SG+UK", offer_id=offer,
            countries_json=["SG", "GB"], sectors_json=["logistics", "healthcare"],
            company_size="200+ employees", channels_json=["email", "linkedin"],
            designations_json=[g.to_dict() for g in resolve_groups(["ld", "hr"])], status="active",
        )  # fmt: skip
        company_ids = {}
        for c in COMPANIES:
            company_ids[c["name"]] = db.insert(
                conn, "companies", campaign_id=campaign, name=c["name"], aliases_json=c["aliases"],
                domain=c["domain"], country=c["country_code"], sector=c["sector"],
                domain_ok=1, status="approved",
            )  # fmt: skip
        people_ids = []
        for key, rows in SEARCH_RESULTS.items():
            company, label = key.split("|")
            for row in rows:
                if row.get("fabricated"):
                    continue
                first, last = row["title"].split(" - ")[0].split(" ", 1)
                outcome = outcomes[row["link"]].split()[0]
                status = "confirmed" if outcome == "confirm" else "review"
                person = db.insert(
                    conn, "people", campaign_id=campaign, company_id=company_ids[company],
                    first_name=first, last_name=last, greeting_name=first,
                    raw_name=f"{first} {last}", title=row["title"].split(" - ")[1].split(" | ")[0],
                    designation=DESIGNATIONS[label], seniority="manager",
                    country=domains[company]["country_code"], linkedin_url=row["link"],
                    evidence_title=row["title"], evidence_snippet=row["snippet"],
                    confirmed=int(status == "confirmed"), status=status,
                )  # fmt: skip
                if status == "confirmed":
                    people_ids.append((person, first, last, domains[company]["domain"]))
        last_message = send = None
        for n, (person, first, last, domain) in enumerate(people_ids):
            address = f"{first}.{last}@{domain}".lower().replace(" ", "")
            if n % 3 == 0:
                db.insert(conn, "emails", person_id=person, email=address, status="verified",
                          verifier="reoon", checked_at=_iso(20))  # fmt: skip
            li = db.insert(
                conn, "messages", person_id=person, channel="linkedin_message",
                body=f"Hi {first}, would a short example be useful?", status="approved",
                ai_review="pass",
            )  # fmt: skip
            if n == 0:
                last_message = db.insert(
                    conn, "messages", person_id=person, channel="email",
                    subject="VR practice scenarios", body=f"Hi {first},\n\nWould a call help?",
                    status="sent", ai_review="pass",
                )  # fmt: skip
                send = db.insert(
                    conn, "sends", message_id=last_message, to_email=address, status="sent",
                    provider_message_id="<old-1@example.com>", sent_at=_iso(12),
                )  # fmt: skip
                db.insert(conn, "events", send_id=send, type="reply", at=_iso(11),
                          detail="Thanks, send details.")  # fmt: skip
            assert li
        db.insert(conn, "suppression", value="nobody@example.com", kind="email")
        db.insert(conn, "settings", key="note", value="kept")
        db.insert(conn, "api_usage", provider="serper", day="2026-10-01", calls=24, credits=24)
        return {"people": len(people_ids)}


def counts(path) -> dict[str, int]:
    with db.session(path) as conn:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in OLD_TABLES}


def get(client: TestClient, path: str):
    reply = client.get(path)
    assert reply.status_code == 200, f"{path}: {reply.status_code} {reply.text[:200]}"
    return reply


def test_a_v10_workspace_opens_in_v11_and_the_dashboard_loads_every_page(monkeypatch, tmp_path):
    info = build_v10_workspace()
    old_home = paths.home()
    before = counts(paths.db_path())
    assert before["people"] > 100 and before["companies"] == 12 and info["people"] > 50
    with db.session() as conn:
        assert db.current_version(conn) == 1

    # copy the whole workspace folder, as an owner backing it up would, and open the copy
    copy_home = tmp_path / "LeadGenerator_copy"
    shutil.copytree(old_home, copy_home)
    monkeypatch.setenv("LEADGEN_HOME", str(copy_home))
    assert paths.home() == copy_home

    assert db.init_db() == db.SCHEMA_VERSION
    assert counts(paths.db_path()) == before  # nothing deleted, nothing added in the old tables
    with db.session(old_home / paths.db_path().name) as conn:
        assert db.current_version(conn) == 1
    with db.session() as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        campaigns = db.query(conn, "SELECT * FROM campaigns")
        assert [c["project_id"] for c in campaigns] != [None]
        project = db.get(conn, "projects", campaigns[0]["project_id"])
        assert project["name"] == "Lanternfish VR"
        assert project["offer_id"] == campaigns[0]["offer_id"]
        assert project["cooldown_days"] == 90 and project["email_policy"] == "verified_only"
        found = conn.execute(
            "SELECT COUNT(*) FROM contact_log WHERE channel = 'found'"
        ).fetchone()[0]
        sent = conn.execute(
            "SELECT COUNT(*) FROM contact_log WHERE channel = 'email_sent'"
        ).fetchone()[0]
        assert found == info["people"] and sent == 1
        assert conn.execute("SELECT value FROM settings WHERE key = 'note'").fetchone()[0] == "kept"
    assert db.init_db() == db.SCHEMA_VERSION  # opening again changes nothing
    assert counts(paths.db_path()) == before

    # the dashboard: every request its pages make
    client = TestClient(dash.create_app(), base_url=BASE)
    html = get(client, "/").text
    assert "<html" in html.lower()
    get(client, "/api/ping")
    assert get(client, "/api/status").json()["ok"]
    get(client, "/api/providers")
    get(client, "/api/offer-source-help")
    for kind in ("csv", "xlsx"):
        assert get(client, f"/api/contacts-template/{kind}").content
    listed = get(client, "/api/projects").json()
    assert "Lanternfish VR" in str(listed)
    pid = project["id"]
    page = get(client, f"/api/projects/{pid}").json()
    assert "Lanternfish VR" in str(page)
    get(client, "/api/campaign-options")
    camps = get(client, "/api/campaigns").json()
    assert "Lanternfish SG+UK" in str(camps)
    get(client, f"/api/campaigns?project_id={pid}")
    cid = campaigns[0]["id"]
    overview = get(client, f"/api/campaigns/{cid}").json()
    assert "Lanternfish SG+UK" in str(overview)
    assert get(client, f"/api/campaigns/{cid}/companies").json()
    leads = get(client, f"/api/campaigns/{cid}/leads").json()
    assert leads["total"] == before["people"]
    get(client, f"/api/campaigns/{cid}/leads?known=1")
    get(client, f"/api/campaigns/{cid}/lead-filters")
    assert get(client, f"/api/leads/{leads['leads'][0]['id']}").json()["ok"]
    get(client, f"/api/campaigns/{cid}/messages")
    get(client, f"/api/campaigns/{cid}/qa")
    get(client, f"/api/campaigns/{cid}/exports")
    get(client, "/api/analytics")
    get(client, "/api/replies")
    get(client, "/api/sending")
    get(client, "/api/mailbox")
    get(client, "/api/suppression")
    get(client, "/api/settings")
    with pytest.raises(AssertionError):  # the check itself works: a missing page is caught
        get(client, "/api/campaigns/99999")
