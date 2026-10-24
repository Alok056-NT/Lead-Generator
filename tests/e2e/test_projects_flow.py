"""End-to-end test (T29): the v1.1 projects flow, driven through the MCP tools.

Same invented universe as T19 (`universe.py`, a VR training studio and 12 invented companies).
One project, three campaigns:

1. the owner uploads an old contacts file (exclude): a dry run first, then for real;
2. campaign A (p = 2 people per company) finds people: two per company at most, the rest are
   kept as reserve, the two imported people are held back with L24;
3. campaign B (also p = 2) takes A's reserve, and then searches itself: nobody sits in both
   campaigns;
4. A is written, reviewed and exported; A is archived and campaign C searches the same companies
   again: nobody already exported is offered again inside the cooldown.

The T19 API and AI-search flows (`test_full_flow.py`) keep running next to this one.
"""

from __future__ import annotations

import csv
from collections import Counter
from datetime import UTC, datetime, timedelta

from world import (
    all_people,
    draft_channel,
    new_campaign,
    new_offer,
    read_csv,
    review_all,
    run_chunked,
)

from leadgen import db
from leadgen.services import projects as projects_svc

LINKEDIN_ONLY = ("linkedin",)


def sql(query: str, params: tuple = ()) -> list[dict]:
    with db.session() as conn:
        return db.query(conn, query, params)


def _slug(person: dict) -> str:
    return person["linkedin_url"].rsplit("/in/", 1)[1].strip("/")


def _contacts_file(path) -> None:
    """A tiny old-contacts file in the template's columns (two invented people)."""
    recent = (datetime.now(UTC) - timedelta(days=10)).date().isoformat()
    header = [
        "first_name", "last_name", "full_name", "email", "linkedin_url", "company",
        "company_domain", "title", "country", "last_contacted", "channel", "relationship", "notes",
    ]  # fmt: skip
    rows = [
        ["Dev", "Iyer", "", "", "https://www.linkedin.com/in/dev-iyer", "Aster Harbour Logistics",
         "", "Learning and Development Manager", "SG", recent, "linkedin", "contacted", ""],
        ["Tara", "Vance", "", "", "", "Driftwood Foods", "driftwoodfoods.example.com",
         "Training Manager", "SG", recent, "email", "contacted", ""],
    ]  # fmt: skip
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _per_company(mcp, cid: int, status: str) -> Counter:
    return Counter(p["company_id"] for p in all_people(mcp, cid, status))


def _held_back(mcp, cid: int) -> dict[str, list[str]]:
    """Review people of a campaign: slug -> reason codes."""
    return {
        _slug(p): sorted(r.split(":")[0] for r in p["reasons"])
        for p in all_people(mcp, cid, "review")
    }


def test_projects_flow_end_to_end(mcp, world, tmp_path):
    offer_id, _ = new_offer(mcp)

    # 1. a project, and an old contacts file: the dry run writes nothing, the real run does
    project = mcp("create_project", name="Lanternfish VR", offer_id=offer_id, cooldown_days=90)
    assert project["ok"], project
    pid = project["id"]
    contacts = tmp_path / "old_contacts.csv"
    _contacts_file(contacts)
    preview = mcp("import_contacts", project_id=pid, file_path=str(contacts), mode="exclude")
    assert preview["ok"] and preview["dry_run"] is True, preview
    assert sql("SELECT COUNT(*) AS n FROM contact_log")[0]["n"] == 0
    applied = mcp(
        "import_contacts", project_id=pid, file_path=str(contacts), mode="exclude", dry_run=False
    )
    assert applied["ok"] and applied["dry_run"] is False, applied
    assert sql("SELECT COUNT(*) AS n FROM contact_log WHERE channel = 'imported'")[0]["n"] == 2

    # 2. campaign A: p = 2 people per company, the rest kept as reserve
    a = new_campaign(mcp, offer_id, "Wave A", channels=LINKEDIN_ONLY, project_id=pid,
                     max_per_company=2)  # fmt: skip
    mcp("estimate_people_search", campaign_id=a)
    found = run_chunked(mcp, "find_people", a, confirm=True)
    assert found[-1]["done"] is True
    confirmed_a = all_people(mcp, a, "confirmed")
    reserve_a = all_people(mcp, a, "reserve")
    assert confirmed_a and reserve_a, "p = 2 must leave somebody in reserve"
    assert max(_per_company(mcp, a, "confirmed").values()) <= 2
    # the two imported people are held back with L24, not confirmed
    held = _held_back(mcp, a)
    assert "L24" in held["dev-iyer"] and "L24" in held["tara-vance"], held
    assert {"dev-iyer", "tara-vance"}.isdisjoint({_slug(p) for p in confirmed_a + reserve_a})
    assert world.calls["serper"] == 24  # every search paid once

    # 3. campaign B in the same project, also p = 2: it takes A's reserve
    b = new_campaign(mcp, offer_id, "Wave B", channels=LINKEDIN_ONLY, project_id=pid,
                     max_per_company=2)  # fmt: skip
    moved = mcp("move_reserve_people", from_campaign_id=a, to_campaign_id=b)
    assert moved["ok"] and moved["moved"] > 0, moved
    assert max(_per_company(mcp, b, "confirmed").values()) <= 2
    run_chunked(mcp, "find_people", b, confirm=True)  # cache answers; A's people are L25 here
    assert world.calls["serper"] == 24
    confirmed_b = all_people(mcp, b, "confirmed")
    assert len(confirmed_b) >= moved["moved"]
    assert max(_per_company(mcp, b, "confirmed").values()) <= 2
    # nobody sits in both campaigns
    live = sql(
        "SELECT linkedin_url, COUNT(DISTINCT campaign_id) AS n FROM people "
        "WHERE status IN ('confirmed', 'reserve') GROUP BY linkedin_url HAVING n > 1"
    )
    assert live == []
    assert {_slug(p) for p in confirmed_a}.isdisjoint({_slug(p) for p in confirmed_b})
    assert "L25" in {c for codes in _held_back(mcp, b).values() for c in codes}

    # 4. write, review and export A; then archive it and search again in campaign C
    draft_channel(mcp, a, "linkedin_message")
    mcp("run_qa", campaign_id=a)
    review_all(mcp, a)
    exported = mcp("export_campaign", campaign_id=a, format="generic")
    assert exported["ok"] and exported["exported"] > 0, exported
    _, rows = read_csv(exported["path"])
    exported_slugs = {r["linkedinUrl"].rsplit("/in/", 1)[1].strip("/") for r in rows}
    assert exported_slugs <= {_slug(p) for p in confirmed_a}
    logged = sql("SELECT COUNT(*) AS n FROM contact_log WHERE channel = 'exported_csv'")[0]["n"]
    assert logged >= len(exported_slugs)
    assert mcp("update_campaign", campaign_id=a, status="archived")["ok"]

    c = new_campaign(mcp, offer_id, "Wave C", channels=LINKEDIN_ONLY, project_id=pid,
                     max_per_company=2)  # fmt: skip
    run_chunked(mcp, "find_people", c, confirm=True)
    assert world.calls["serper"] == 24
    confirmed_c = {_slug(p) for p in all_people(mcp, c, "confirmed")}
    assert confirmed_c.isdisjoint(exported_slugs)  # not offered again inside the cooldown
    assert confirmed_c.isdisjoint({"dev-iyer", "tara-vance"})
    held_c = _held_back(mcp, c)
    assert all("L24" in held_c[slug] for slug in exported_slugs), held_c
    assert confirmed_c.isdisjoint({_slug(p) for p in confirmed_b})  # B's people stay in B (L25)

    # the project tells the same story in plain numbers
    shown = projects_svc.get_project(pid)
    assert shown["cooldown_days"] == 90
    assert shown["counts"]["contacted_in_cooldown"] >= len(exported_slugs) + 2
    assert shown["counts"]["campaigns"]["total"] == 3
