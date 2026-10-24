"""Projects, contact memory, campaign size limits, reserve people and the L24/L25 checks (T23).

All people, companies and addresses are invented. No network: searches use FakeSearchProvider.
"""

from __future__ import annotations

import itertools
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from leadgen import db
from leadgen.providers.fake import FakeSearchProvider
from leadgen.providers.search import SearchResult
from leadgen.services import campaign as camp
from leadgen.services import contact_checks, people, projects, qa_leads
from leadgen.services.campaign import CampaignError
from leadgen.services.people import PeopleError
from leadgen.services.projects import ProjectError

_names = itertools.count(1)


def days_ago(n: int) -> str:
    return (datetime.now(UTC) - timedelta(days=n)).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


@pytest.fixture
def offer_id(ready_db):
    with db.session() as conn:
        return db.insert(conn, "offers", name="Northwind Training", source_type="text")


@pytest.fixture
def project(offer_id):
    return projects.create_project("Northwind Training", offer_id=offer_id)


def make_campaign(project_id, name=None, **options):
    name = name or f"Campaign {next(_names)}"
    return camp.create_campaign(
        name=name, project_id=project_id, countries=["SG"], sectors=["logistics"],
        company_size="200-1000", channels=["linkedin"], designations=["ld", "hr"],
        options=options,
    )["id"]  # fmt: skip


def add_company(cid, name, status="approved", domain=""):
    with db.session() as conn:
        return db.insert(
            conn, "companies", campaign_id=cid, name=name, domain=domain, status=status
        )


def hit(name, headline, company):
    slug = name.lower().replace(" ", "-")
    return SearchResult(
        title=f"{name} - {headline} at {company} | LinkedIn",
        link=f"https://www.linkedin.com/in/{slug}",
        snippet=f"{headline} at {company} · Singapore",
        position=1,
    )


def responder(mapping):
    """Fake provider: answers every query naming a company with that company's results."""

    def respond(query, gl, num):
        out = []
        for company, results in mapping.items():
            if f'"{company}"' in query:
                out.extend(results)
        return out

    return FakeSearchProvider(respond)


def find(cid, mapping, **kwargs):
    return people.find_people(cid, provider=responder(mapping), **kwargs)


def rows(cid, status=None):
    sql = "SELECT * FROM people WHERE campaign_id = ?"
    params = [cid]
    if status:
        sql += " AND status = ?"
        params.append(status)
    with db.session() as conn:
        return db.query(conn, sql + " ORDER BY id", params)


def names(cid, status):
    return [r["raw_name"] for r in rows(cid, status)]


def log_rows(project_id):
    with db.session() as conn:
        return db.query(
            conn, "SELECT * FROM contact_log WHERE project_id = ? ORDER BY id", (project_id,)
        )


# --- the v1 -> v2 migration ---------------------------------------------------------------------

V1_TABLES = (
    "offers", "campaigns", "companies", "people", "search_tasks", "emails", "messages",
    "qa_issues", "suppression", "sends", "events", "settings", "api_usage", "search_cache",
)  # fmt: skip


def build_v1_database(path):
    """A database as v1.0 left it: every table filled with invented data."""
    real = db.MIGRATIONS
    db.MIGRATIONS = []
    try:
        assert db.init_db(path) == 1
    finally:
        db.MIGRATIONS = real
    with db.session(path) as conn:
        o1 = db.insert(conn, "offers", name="Northwind Training", source_type="text")
        o2 = db.insert(conn, "offers", name="northwind training", source_type="url")
        o3 = db.insert(conn, "offers", name="", source_type="file")
        db.insert(conn, "offers", name="Lonely offer", source_type="text")
        c1 = db.insert(conn, "campaigns", name="SG launch", offer_id=o1)
        c2 = db.insert(conn, "campaigns", name="UK launch", offer_id=o1)
        c3 = db.insert(conn, "campaigns", name="Second offer run", offer_id=o2)
        c4 = db.insert(conn, "campaigns", name="No offer yet", offer_id=None)
        c5 = db.insert(conn, "campaigns", name="Unnamed offer run", offer_id=o3)
        person_ids = []
        for cid, tag in ((c1, "a"), (c2, "b"), (c3, "c"), (c4, "d"), (c5, "e")):
            company = db.insert(
                conn, "companies", campaign_id=cid, name=f"Northwind {tag}",
                domain=f"northwind-{tag}.example", status="approved",
            )  # fmt: skip
            for i, status in enumerate(("confirmed", "review", "confirmed")):
                pid = db.insert(
                    conn, "people", campaign_id=cid, company_id=company, first_name=f"Aria{tag}",
                    last_name=f"Tan{i}", raw_name=f"Aria{tag} Tan{i}", status=status,
                    linkedin_url=f"https://www.linkedin.com/in/aria-{tag}-{i}",
                )  # fmt: skip
                person_ids.append(pid)
                db.insert(conn, "emails", person_id=pid, email=f"a{pid}@example.com",
                          status="verified")  # fmt: skip
            msg = db.insert(conn, "messages", person_id=person_ids[-1], channel="email",
                            subject="Hello", body="Hi", status="sent")  # fmt: skip
            send = db.insert(conn, "sends", message_id=msg, status="sent",
                             to_email=f"a{person_ids[-1]}@example.com",
                             sent_at="2026-09-01T09:00:00.000Z")  # fmt: skip
            db.insert(conn, "events", send_id=send, type="reply", at="2026-09-02T09:00:00.000Z")
            db.insert(conn, "qa_issues", entity_type="person", entity_id=person_ids[-1],
                      code="L04", severity="block")  # fmt: skip
            db.insert(conn, "search_tasks", campaign_id=cid, kind="people", query="q")
        db.insert(conn, "suppression", value="nobody@example.com", kind="email")
        db.insert(conn, "settings", key="k", value="v")
        db.insert(conn, "api_usage", provider="serper", day="2026-09-01", calls=3, credits=3)
        db.insert(conn, "search_cache", provider="serper", query_hash="h", fetched_at="2026-09-01")


def counts(path):
    with db.session(path) as conn:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in V1_TABLES}


def test_migration_v1_to_v2_on_a_copy_keeps_everything(tmp_path):
    original = tmp_path / "v1.db"
    build_v1_database(original)
    before = counts(original)
    copy = tmp_path / "copy" / "leadgen.db"
    copy.parent.mkdir()
    shutil.copy2(original, copy)

    assert db.init_db(copy) == db.SCHEMA_VERSION == 5  # v1 -> ... -> v5

    assert counts(copy) == before  # nothing deleted or added in the v1 tables
    with db.session(original) as conn:
        assert db.current_version(conn) == 1  # the original file was not touched
    with db.session(copy) as conn:
        assert conn.execute("SELECT COUNT(*) FROM campaigns WHERE project_id IS NULL").fetchone()[
            0
        ] == 0
        proj = db.query(conn, "SELECT * FROM projects ORDER BY id")
        by_campaign = {
            r["name"]: r["project_id"] for r in db.query(conn, "SELECT * FROM campaigns")
        }
        offers = {r["id"]: r for r in db.query(conn, "SELECT * FROM offers")}
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    assert fk == []
    # one project per offer (4) plus one for the campaign that had no offer
    assert len(proj) == 5
    assert [p["name"] for p in proj] == [
        "Northwind Training", "northwind training (2)", "Offer 3", "Lonely offer",
        "Campaigns without an offer",
    ]  # fmt: skip
    assert len({p["name"].casefold() for p in proj}) == 5
    assert all(p["cooldown_days"] == 90 and p["status"] == "active" for p in proj)
    assert by_campaign["SG launch"] == by_campaign["UK launch"] == proj[0]["id"]
    assert by_campaign["Second offer run"] == proj[1]["id"]
    assert by_campaign["Unnamed offer run"] == proj[2]["id"]
    assert by_campaign["No offer yet"] == proj[4]["id"]
    assert proj[3]["offer_id"] in offers and proj[4]["offer_id"] is None
    # the memory is seeded: confirmed people are `found`, the emailed address is `email_sent`
    with db.session(copy) as conn:
        found = conn.execute("SELECT COUNT(*) FROM contact_log WHERE channel='found'").fetchone()[0]
        sent = conn.execute(
            "SELECT COUNT(*) FROM contact_log WHERE channel='email_sent'"
        ).fetchone()[0]
        review_logged = conn.execute(
            "SELECT COUNT(*) FROM contact_log l JOIN people p ON p.id = l.person_id "
            "WHERE p.status = 'review' AND l.channel = 'found'"
        ).fetchone()[0]
    assert found == 10 and sent == 5 and review_logged == 0
    # running it again changes nothing, and a v2 database is never migrated back
    assert db.init_db(copy) == db.SCHEMA_VERSION
    assert counts(copy) == before
    with db.session(copy) as conn:
        assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 5
        assert conn.execute("SELECT COUNT(*) FROM contact_log").fetchone()[0] == found + sent


def test_a_campaign_cannot_exist_without_a_project(ready_db):
    with db.session() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            db.insert(conn, "campaigns", name="Orphan")
        project_id = db.insert(conn, "projects", name="P")
        cid = db.insert(conn, "campaigns", name="Fine", project_id=project_id)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE campaigns SET project_id = NULL WHERE id = ?", (cid,))
        with pytest.raises(sqlite3.IntegrityError):  # a project with campaigns is not deleted
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


def test_failed_v2_migration_rolls_back(tmp_path, monkeypatch):
    path = tmp_path / "v1.db"
    build_v1_database(path)

    def boom(conn):
        raise RuntimeError("stop")

    sql = db.MIGRATIONS[0][1]
    monkeypatch.setattr(db, "MIGRATIONS", [(2, sql, boom)])
    with pytest.raises(RuntimeError):
        db.init_db(path)
    with db.session(path) as conn:
        assert db.current_version(conn) == 1
        names_ = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        assert "projects" not in names_ and "contact_log" not in names_
        assert "project_id" not in {r[1] for r in conn.execute("PRAGMA table_info(campaigns)")}


# --- project CRUD -------------------------------------------------------------------------------


def test_create_get_list_update_archive(offer_id):
    made = projects.create_project(
        "  Lanternfish   VR ", description=" training ", cooldown_days=30
    )
    assert made["name"] == "Lanternfish VR" and made["description"] == "training"
    assert made["cooldown_days"] == 30 and made["status"] == "active" and made["offer_id"] is None
    assert made["counts"]["campaigns"] == {"draft": 0, "active": 0, "archived": 0, "total": 0}
    assert made["counts"]["people"] == dict.fromkeys(projects.PEOPLE_STATUSES, 0)
    assert made["counts"]["contacted_in_cooldown"] == 0

    got = projects.update_project(
        made["id"], name="Lanternfish", cooldown_days=0, offer_id=offer_id
    )
    assert got["name"] == "Lanternfish" and got["cooldown_days"] == 0
    assert got["offer_id"] == offer_id and got["offer_name"] == "Northwind Training"
    assert projects.set_project_offer(made["id"], offer_id)["offer_id"] == offer_id

    other = projects.create_project("Second")
    listing = projects.list_projects()
    assert [p["name"] for p in listing] == ["Second", "Lanternfish"]
    assert listing[1]["offer_name"] == "Northwind Training" and listing[1]["campaigns"] == 0
    projects.archive_project(other["id"])
    assert projects.get_project(other["id"])["status"] == "archived"
    assert [p["name"] for p in projects.list_projects(include_archived=False)] == ["Lanternfish"]
    assert [p["name"] for p in projects.list_projects()] == ["Second", "Lanternfish"]


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"name": ""}, "invalid_name"),
        ({"name": "   "}, "invalid_name"),
        ({"name": 5}, "invalid_name"),
        ({"name": "x" * 121}, "invalid_name"),
        ({"name": "A", "cooldown_days": -1}, "invalid_cooldown"),
        ({"name": "A", "cooldown_days": 3651}, "invalid_cooldown"),
        ({"name": "A", "cooldown_days": True}, "invalid_cooldown"),
        ({"name": "A", "cooldown_days": "90"}, "invalid_cooldown"),
        ({"name": "A", "offer_id": 999}, "invalid_offer"),
        ({"name": "A", "offer_id": True}, "invalid_offer"),
        ({"name": "A", "description": 5}, "invalid_value"),
    ],
)
def test_create_project_validation(ready_db, kwargs, code):
    with pytest.raises(ProjectError) as err:
        projects.create_project(**kwargs)
    assert err.value.code == code
    assert err.value.as_response()["error"]["code"] == code
    assert projects.list_projects() == []


def test_cooldown_limits_are_allowed(ready_db):
    assert projects.create_project("Zero", cooldown_days=0)["cooldown_days"] == 0
    assert projects.create_project("Max", cooldown_days=3650)["cooldown_days"] == 3650


def test_project_names_are_unique_ignoring_case(project):
    with pytest.raises(ProjectError) as err:
        projects.create_project("northwind TRAINING")
    assert err.value.code == "duplicate_name"
    other = projects.create_project("Other")
    with pytest.raises(ProjectError) as err:
        projects.update_project(other["id"], name="NORTHWIND training")
    assert err.value.code == "duplicate_name"
    renamed = projects.update_project(project["id"], name="northwind training")
    assert renamed["name"] == "northwind training"  # a project may change its own case


def test_update_project_errors(project):
    for call, code in (
        (lambda: projects.update_project(999, name="x"), "not_found"),
        (lambda: projects.get_project(999), "not_found"),
        (lambda: projects.get_project(True), "not_found"),
        (lambda: projects.update_project(project["id"], colour="red"), "unknown_field"),
        (lambda: projects.update_project(project["id"], status="gone"), "invalid_value"),
        (lambda: projects.update_project(project["id"], cooldown_days=-5), "invalid_cooldown"),
        (lambda: projects.update_project(project["id"], offer_id=999), "invalid_offer"),
    ):
        with pytest.raises(ProjectError) as err:
            call()
        assert err.value.code == code


def test_get_project_counts_campaigns_and_people(project):
    a = make_campaign(project["id"])
    b = make_campaign(project["id"])
    camp.archive_campaign(b)
    co = add_company(a, "Northwind Health")
    with db.session() as conn:
        for status in ("confirmed", "confirmed", "review", "reserve"):
            db.insert(conn, "people", campaign_id=a, company_id=co, status=status)
    got = projects.get_project(project["id"])
    assert got["counts"]["campaigns"] == {"draft": 1, "active": 0, "archived": 1, "total": 2}
    assert got["counts"]["people"]["confirmed"] == 2 and got["counts"]["people"]["reserve"] == 1
    assert projects.list_projects()[0]["campaigns"] == 1  # archived campaigns are not counted
    assert projects.list_projects()[0]["confirmed_people"] == 2


def test_ensure_project_for_offer(offer_id):
    first = projects.ensure_project_for_offer(offer_id)
    assert projects.ensure_project_for_offer(offer_id) == first
    assert projects.get_project(first)["name"] == "Northwind Training"
    projects.create_project("Other", offer_id=None)
    projects.update_project(first, status="archived")
    second = projects.ensure_project_for_offer(offer_id)
    assert second != first and projects.get_project(second)["name"] == "Northwind Training (2)"
    with pytest.raises(ProjectError):
        projects.ensure_project_for_offer(999)


# --- campaigns inside projects ------------------------------------------------------------------

CAMPAIGN_ARGS = {
    "countries": ["SG"], "sectors": ["x"], "company_size": "any", "channels": ["linkedin"],
    "designations": ["ld"],
}  # fmt: skip


def test_create_campaign_needs_a_project(offer_id):
    with pytest.raises(CampaignError) as err:
        camp.create_campaign(name="A", offer_id=offer_id, **CAMPAIGN_ARGS)
    assert err.value.code == "project_required"
    with pytest.raises(CampaignError) as err:
        camp.create_campaign(name="A", project_id=999, **CAMPAIGN_ARGS)
    assert err.value.code == "invalid_project"
    with pytest.raises(CampaignError) as err:
        camp.create_campaign(name="A", project_id=True, **CAMPAIGN_ARGS)
    assert err.value.code == "invalid_project"


def test_the_campaign_offer_defaults_to_the_projects_offer(project, offer_id):
    got = camp.create_campaign(name="A", project_id=project["id"], **CAMPAIGN_ARGS)
    assert got["offer_id"] == offer_id and got["project_id"] == project["id"]
    assert camp.list_campaigns(project_id=project["id"])[0]["project_id"] == project["id"]
    assert camp.list_campaigns(project_id=project["id"] + 1) == []
    assert len(camp.list_campaigns()) == 1


def test_a_project_without_an_offer_has_to_name_one(ready_db, offer_id):
    empty = projects.create_project("No offer yet")
    with pytest.raises(CampaignError) as err:
        camp.create_campaign(name="A", project_id=empty["id"], **CAMPAIGN_ARGS)
    assert err.value.code == "project_has_no_offer"
    got = camp.create_campaign(name="A", project_id=empty["id"], offer_id=offer_id, **CAMPAIGN_ARGS)
    assert got["offer_id"] == offer_id
    assert projects.get_project(empty["id"])["offer_id"] == offer_id  # adopted


def test_another_offer_than_the_projects_is_refused(project):
    with db.session() as conn:
        other = db.insert(conn, "offers", name="Other", source_type="text")
    with pytest.raises(CampaignError) as err:
        camp.create_campaign(name="A", project_id=project["id"], offer_id=other, **CAMPAIGN_ARGS)
    assert err.value.code == "offer_mismatch"


def test_an_archived_project_takes_no_new_campaigns(project):
    projects.archive_project(project["id"])
    with pytest.raises(CampaignError) as err:
        camp.create_campaign(name="A", project_id=project["id"], **CAMPAIGN_ARGS)
    assert err.value.code == "project_archived"


@pytest.mark.parametrize("key", ["max_people", "max_companies", "max_per_company"])
@pytest.mark.parametrize("bad", [0, -1, 10_001, True, "5", 2.5])
def test_size_limits_are_validated(project, key, bad):
    with pytest.raises(CampaignError) as err:
        camp.create_campaign(
            name="A", project_id=project["id"], options={key: bad}, **CAMPAIGN_ARGS
        )
    assert err.value.code == "invalid_value" and key in err.value.message


def test_size_limits_can_be_set_changed_and_removed(project):
    got = camp.create_campaign(
        name="A", project_id=project["id"],
        options={"max_people": 10_000, "max_companies": 1, "max_per_company": 3}, **CAMPAIGN_ARGS,
    )  # fmt: skip
    assert got["options"]["max_people"] == 10_000 and got["options"]["max_per_company"] == 3
    got = camp.update_campaign(got["id"], max_people=50, options={"max_companies": 7})
    assert (got["options"]["max_people"], got["options"]["max_companies"]) == (50, 7)
    got = camp.update_campaign(got["id"], max_people=None)
    assert got["options"]["max_people"] is None and got["options"]["max_per_company"] == 3
    with pytest.raises(CampaignError):
        camp.update_campaign(got["id"], max_per_company=0)
    plain = camp.create_campaign(name="B", project_id=project["id"], **CAMPAIGN_ARGS)
    assert plain["options"]["max_people"] is None


# --- keys and the contact log -------------------------------------------------------------------


def test_make_keys_reads_every_input_shape():
    full = projects.make_keys(
        {
            "linkedin_url": "https://SG.linkedin.com/in/Aria-Tan/?trk=x",
            "email": " Aria@Example.COM ",
            "first_name": "Aria", "last_name": "Tan", "company": "Northwind  Logistics",
        }
    )  # fmt: skip
    assert full == {
        "key_linkedin": "in/aria-tan", "key_email": "aria@example.com",
        "key_name_company": "aria tan|northwind logistics",
    }  # fmt: skip
    export_row = projects.make_keys(
        {"firstName": "Aria", "lastName": "Tan", "companyName": "Northwind Logistics",
         "linkedinUrl": "https://www.linkedin.com/in/aria-tan", "email": ""}
    )  # fmt: skip
    assert export_row["key_linkedin"] == "in/aria-tan" and export_row["key_email"] == ""
    assert export_row["key_name_company"] == full["key_name_company"]
    raw = projects.make_keys({"raw_name": "Aria Tan", "company": "Northwind Logistics"})
    assert raw["key_name_company"] == full["key_name_company"]
    # no company or no name: no name key; a company page is not a profile; not an address
    assert projects.make_keys({"first_name": "Aria", "last_name": "Tan"})["key_name_company"] == ""
    assert projects.make_keys({"company": "Northwind"})["key_name_company"] == ""
    assert projects.make_keys({"linkedin_url": "https://www.linkedin.com/company/x"}) == {
        "key_linkedin": "", "key_email": "", "key_name_company": "",
    }  # fmt: skip
    assert projects.make_keys({"email": "not an address"})["key_email"] == ""
    ready = {"key_linkedin": "in/x", "key_email": "A@B.COM", "key_name_company": ""}
    assert projects.make_keys(ready) == {**ready, "key_email": "a@b.com"}


def test_log_contacts_validates_and_skips(project):
    pid = project["id"]
    assert projects.log_contacts(pid, [{"email": "a@example.com"}], "email_sent") == {
        "logged": 1, "skipped": 0,
    }  # fmt: skip
    again = projects.log_contacts(pid, [{"email": "a@example.com"}, {"first_name": "Only"}],
                                  "email_sent")  # fmt: skip
    assert again == {"logged": 0, "skipped": 2}  # identical row; row without any key
    assert projects.log_contacts(pid, [{"email": "a@example.com"}], "lemlist")["logged"] == 1
    assert [r["channel"] for r in log_rows(pid)] == ["email_sent", "lemlist"]
    for call, code in (
        (lambda: projects.log_contacts(pid, [], "carrier_pigeon"), "invalid_channel"),
        (lambda: projects.log_contacts(pid, [], "found", at="yesterday"), "invalid_value"),
        (lambda: projects.log_contacts(999, [], "found"), "not_found"),
    ):
        with pytest.raises(ProjectError) as err:
            call()
        assert err.value.code == code


def test_recently_contacted_matches_each_key(project):
    pid = project["id"]
    cid = make_campaign(pid, "Sent from here")
    projects.log_contacts(
        pid,
        [
            {"linkedin_url": "https://www.linkedin.com/in/aria-tan"},
            {"email": "marcus@example.com"},
            {"first_name": "Lena", "last_name": "Okafor", "company": "Northwind Logistics"},
        ],
        "email_sent", campaign_id=cid, source="test",
    )  # fmt: skip
    by_url = projects.recently_contacted(
        pid, {"linkedin_url": "https://sg.linkedin.com/in/ARIA-TAN/"}
    )
    assert by_url["channel"] == "email_sent" and by_url["campaign_id"] == cid
    assert by_url["campaign_name"] == "Sent from here" and by_url["source"] == "test"
    assert projects.recently_contacted(pid, {"email": "Marcus@Example.com"}) is not None
    by_name = {"first_name": "lena", "last_name": "OKAFOR", "company": "NORTHWIND logistics"}
    assert projects.recently_contacted(pid, by_name) is not None
    # something else about the person differs: no match
    assert projects.recently_contacted(pid, {"email": "other@example.com"}) is None
    assert projects.recently_contacted(pid, {"first_name": "Lena", "last_name": "Okafor"}) is None
    wrong_co = {"first_name": "Lena", "last_name": "Okafor", "company": "Elsewhere"}
    assert projects.recently_contacted(pid, wrong_co) is None
    assert projects.recently_contacted(pid, {}) is None
    # LinkedIn is tried first, then the email, then the name: any one match is enough
    mixed = {
        "linkedin_url": "https://www.linkedin.com/in/someone-else", "email": "marcus@example.com"
    }  # fmt: skip
    assert projects.recently_contacted(pid, mixed)["channel"] == "email_sent"
    # the campaign's own entries are ignored on request
    assert projects.recently_contacted(pid, by_name, exclude_campaign_id=cid) is None


def test_the_cooldown_decides_and_expires(project):
    pid = project["id"]
    row = {"email": "aria@example.com"}
    projects.log_contacts(pid, [row], "exported_csv", at=days_ago(100))
    assert projects.recently_contacted(pid, row) is None  # 100 days > the default 90
    projects.update_project(pid, cooldown_days=120)
    hit = projects.recently_contacted(pid, row)
    assert hit is not None and hit["channel"] == "exported_csv"
    expected = (datetime.now(UTC) - timedelta(days=100) + timedelta(days=120)).date().isoformat()
    assert hit["again_after"] == expected
    assert projects.get_project(pid)["counts"]["contacted_in_cooldown"] == 1
    projects.update_project(pid, cooldown_days=0)  # 0 = never block
    assert projects.recently_contacted(pid, row) is None
    assert projects.get_project(pid)["counts"]["contacted_in_cooldown"] == 0
    projects.update_project(pid, cooldown_days=3650)
    assert projects.recently_contacted(pid, row) is not None
    # the newest entry is the one reported
    projects.log_contacts(pid, [row], "email_sent", at=days_ago(5))
    assert projects.recently_contacted(pid, row)["channel"] == "email_sent"


def test_each_project_has_its_own_memory(project):
    other = projects.create_project("Other project")
    projects.log_contacts(project["id"], [{"email": "aria@example.com"}], "email_sent")
    assert projects.recently_contacted(project["id"], {"email": "aria@example.com"})
    assert projects.recently_contacted(other["id"], {"email": "aria@example.com"}) is None


def test_contact_messages_are_plain_english():
    base = {"at": "2026-09-01T10:00:00.000Z", "campaign_name": "SG launch",
            "again_after": "2026-11-30"}  # fmt: skip
    sent = projects.contact_message({**base, "channel": "email_sent"})
    assert sent == (
        "Already contacted on 2026-09-01 in campaign 'SG launch'; "
        "can be contacted again after 2026-11-30"
    )
    assert projects.contact_message({**base, "channel": "found"}).startswith("Already found on")
    assert "imported" in projects.contact_message({**base, "channel": "imported"})
    assert projects.contact_message(
        {"at": "2026-09-01T10:00:00.000Z", "channel": "lemlist", "campaign_name": "",
         "again_after": ""}  # fmt: skip
    ) == "Already contacted on 2026-09-01"


# --- find_people: limits ------------------------------------------------------------------------

HEAD = "Head of Training"  # L&D group, vp
MANAGER = "Training Manager"  # L&D group, manager
HRDIR = "HR Director"  # HR group, director
PAIR = {"Alpha Co": [hit("Mira Lee", MANAGER, "Alpha Co"), hit("Omar Haddad", HEAD, "Alpha Co")]}


def test_max_companies_searches_only_the_first_approved_companies(project):
    cid = make_campaign(project["id"], max_companies=2)
    a, b, c = (add_company(cid, n) for n in ("Alpha Co", "Beta Co", "Gamma Co"))
    est = people.estimate_people_search(cid)
    assert est["companies"] == 2 and est["searches"] == 4
    mapping = {n: [hit(f"Person {n[:2]}", MANAGER, n)] for n in ("Alpha Co", "Beta Co", "Gamma Co")}
    out = find(cid, mapping)
    assert out["searches_total"] == 4 and out["done"] and "limit_reached" not in out
    assert sorted({r["company_id"] for r in rows(cid)}) == [a, b]
    # the covered set is the first c by id: bumping updated_at (any edit does) never changes it
    cid2 = make_campaign(project["id"], max_companies=2)
    ids = [add_company(cid2, n) for n in ("Alpha Co", "Beta Co", "Gamma Co")]
    before = [r["id"] for r in people.approved_company_rows(cid2)]
    assert before == ids[:2]
    with db.session() as conn:
        db.update(conn, "companies", ids[0], updated_at="2099-01-01T00:00:00.000Z")
        db.update(conn, "companies", ids[2], updated_at="2000-01-01T00:00:00.000Z")
    assert [r["id"] for r in people.approved_company_rows(cid2)] == before
    assert people.estimate_people_search(cid2, [ids[2]])["searches"] == 0  # Gamma is outside
    out = find(cid2, mapping, company_ids=[ids[0], ids[2]])
    assert out["skipped_company_ids"] == [ids[2]]


def test_without_limits_every_company_is_searched_in_id_order(project):
    cid = make_campaign(project["id"])
    ids = [add_company(cid, n) for n in ("Alpha Co", "Beta Co", "Gamma Co")]
    assert [r["id"] for r in people.approved_company_rows(cid)] == ids
    assert people.estimate_people_search(cid)["companies"] == 3


def test_max_per_company_keeps_the_best_ranked_and_reserves_the_rest(project):
    cid = make_campaign(project["id"], max_per_company=1)
    add_company(cid, "Alpha Co")
    mapping = {
        "Alpha Co": [
            hit("Hana Reyes", HRDIR, "Alpha Co"),  # HR group is second, whatever the rank
            hit("Mira Lee", MANAGER, "Alpha Co"),
            hit("Omar Haddad", HEAD, "Alpha Co"),  # first group, highest seniority
        ]
    }
    out = find(cid, mapping)
    assert out["this_call"]["confirmed"] == 1 and out["this_call"]["reserve"] == 2
    assert names(cid, "confirmed") == ["Omar Haddad"]
    assert sorted(names(cid, "reserve")) == ["Hana Reyes", "Mira Lee"]
    assert out["campaign_people"]["confirmed"] == 1 and out["campaign_people"]["reserve"] == 2
    reserve = rows(cid, "reserve")[0]
    assert reserve["confirmed"] == 1 and reserve["reasons_json"] == []
    # only the confirmed person is in the project's memory (reserve people are not offered yet)
    assert [r["person_id"] for r in log_rows(project["id"])] == [rows(cid, "confirmed")[0]["id"]]
    assert log_rows(project["id"])[0]["channel"] == "found"


def test_max_per_company_counts_people_across_searches(project):
    cid = make_campaign(project["id"], max_per_company=1)
    add_company(cid, "Alpha Co")
    first = find(cid, {"Alpha Co": [hit("Mira Lee", MANAGER, "Alpha Co")]}, max_searches_per_call=1)
    assert not first["done"] and names(cid, "confirmed") == ["Mira Lee"]
    second = find(
        cid, {"Alpha Co": [hit("Omar Haddad", HRDIR, "Alpha Co")]}, cursor=first["next_cursor"]
    )
    assert second["done"] and names(cid, "reserve") == ["Omar Haddad"]


def test_max_people_stops_the_search_and_reserves_the_overflow(project):
    cid = make_campaign(project["id"], max_people=2)
    for name in ("Alpha Co", "Beta Co", "Gamma Co"):
        add_company(cid, name)
    mapping = {
        "Alpha Co": [hit("Mira Lee", MANAGER, "Alpha Co")],
        "Beta Co": [hit("Omar Haddad", MANAGER, "Beta Co")],
        "Gamma Co": [hit("Hana Reyes", MANAGER, "Gamma Co")],
    }
    out = find(cid, mapping)
    assert out["done"] and out["next_cursor"] is None and out["limit_reached"] is True
    assert "Limit reached" in out["message"] and "max_people" in out["message"]
    assert out["searches_done"] < out["searches_total"] == 6
    assert sorted(names(cid, "confirmed")) == ["Mira Lee", "Omar Haddad"]
    assert rows(cid, "reserve") == []  # Gamma Co was never searched
    again = find(cid, mapping)  # nothing more to do, and no search is made
    assert again["done"] and again["limit_reached"] and again["searches_done"] == 0


def test_max_people_overflow_inside_one_batch_goes_to_reserve(project):
    cid = make_campaign(project["id"], max_people=2)
    add_company(cid, "Alpha Co")
    mapping = {
        "Alpha Co": [
            hit("Mira Lee", MANAGER, "Alpha Co"),
            hit("Omar Haddad", HEAD, "Alpha Co"),
            hit("Hana Reyes", HRDIR, "Alpha Co"),
        ]
    }
    out = find(cid, mapping)
    assert out["limit_reached"] is True
    assert sorted(names(cid, "confirmed")) == ["Mira Lee", "Omar Haddad"]  # the L&D group first
    assert names(cid, "reserve") == ["Hana Reyes"]


def test_limits_work_together(project):
    cid = make_campaign(project["id"], max_people=3, max_per_company=2, max_companies=2)
    for name in ("Alpha Co", "Beta Co", "Gamma Co"):
        add_company(cid, name)
    mapping = {
        n: [hit(f"A {n[:2]}", HEAD, n), hit(f"B {n[:2]}", MANAGER, n), hit(f"C {n[:2]}", HRDIR, n)]
        for n in ("Alpha Co", "Beta Co", "Gamma Co")
    }
    out = find(cid, mapping)
    # Alpha Co fills its two places; Beta Co's best-ranked person takes the last of the three
    assert sorted(names(cid, "confirmed")) == ["A Al", "A Be", "B Al"]
    assert sorted(names(cid, "reserve")) == ["B Be", "C Al", "C Be"]
    assert out["limit_reached"] is True
    searched = {r["id"] for r in people.approved_company_rows(cid)}
    assert {r["company_id"] for r in rows(cid)} == searched


def test_manual_promotion_ignores_the_limits(project):
    cid = make_campaign(project["id"], max_per_company=1)
    add_company(cid, "Alpha Co")
    find(cid, PAIR)
    reserve = rows(cid, "reserve")[0]
    out = people.set_person_status(cid, [reserve["id"]], "confirmed", "checked by hand")
    assert out["updated"] == 1 and sorted(names(cid, "confirmed")) == ["Mira Lee", "Omar Haddad"]
    assert people.list_people(cid, status="reserve")["total"] == 0
    # leaving `confirmed` takes the person out of the memory, joining it puts them in
    logged = {r["person_id"] for r in log_rows(project["id"])}
    assert logged == {r["id"] for r in rows(cid, "confirmed")}
    people.set_person_status(cid, [reserve["id"]], "rejected")
    assert reserve["id"] not in {r["person_id"] for r in log_rows(project["id"])}
    people.set_person_status(cid, [reserve["id"]], "reserve")
    assert people.list_people(cid, status="reserve")["total"] == 1
    assert reserve["id"] not in {r["person_id"] for r in log_rows(project["id"])}


# --- find_people: memory (L24, L25) -------------------------------------------------------------

MIRA = {"Alpha Co": [hit("Mira Lee", MANAGER, "Alpha Co")]}
OMAR = hit("Omar Haddad", MANAGER, "Alpha Co")


def test_a_person_in_another_active_campaign_goes_to_review_with_l25(project):
    first = make_campaign(project["id"], "First")
    add_company(first, "Alpha Co")
    find(first, MIRA)
    assert names(first, "confirmed") == ["Mira Lee"]
    second = make_campaign(project["id"], "Second")
    add_company(second, "Alpha Co")
    out = find(second, MIRA)
    assert out["this_call"]["review"] == 1 and out["this_call"]["confirmed"] == 0
    person = rows(second)[0]
    assert person["status"] == "review" and person["confirmed"] == 0
    assert [r.split(":")[0] for r in person["reasons_json"]] == ["L25"]  # not also L24
    assert "'First'" in person["reasons_json"][0]
    assert [r["campaign_id"] for r in log_rows(project["id"])] == [first]  # review is not logged


def test_a_reserve_person_in_another_campaign_is_not_offered_again(project):
    first = make_campaign(project["id"], "First", max_per_company=1)
    add_company(first, "Alpha Co")
    find(first, {"Alpha Co": [hit("Mira Lee", HEAD, "Alpha Co"), OMAR]})
    second = make_campaign(project["id"], "Second")
    add_company(second, "Alpha Co")
    find(second, {"Alpha Co": [hit("Omar Haddad", MANAGER, "Alpha Co")]})
    person = next(r for r in rows(second) if r["raw_name"] == "Omar Haddad")
    assert person["status"] == "review" and person["reasons_json"][0].startswith("L25")
    assert "reserve" in person["reasons_json"][0]


def test_the_same_person_in_another_project_is_not_blocked(project, offer_id):
    first = make_campaign(project["id"], "First")
    add_company(first, "Alpha Co")
    find(first, MIRA)
    elsewhere = projects.create_project("Another product", offer_id=offer_id)
    second = make_campaign(elsewhere["id"], "Second")
    add_company(second, "Alpha Co")
    find(second, MIRA)
    assert names(second, "confirmed") == ["Mira Lee"]


def test_an_archived_campaign_gives_l24_not_l25(project):
    first = make_campaign(project["id"], "First")
    add_company(first, "Alpha Co")
    find(first, MIRA)
    camp.archive_campaign(first)
    second = make_campaign(project["id"], "Second")
    add_company(second, "Alpha Co")
    find(second, MIRA)
    person = rows(second)[0]
    assert person["status"] == "review"
    assert [r.split(":")[0] for r in person["reasons_json"]] == ["L24"]
    assert person["reasons_json"][0].startswith("L24:Already found on")
    reason = person["reasons_json"][0]
    assert "'First'" in reason and "can be contacted again after" in reason


def test_contacted_people_are_held_back_until_the_cooldown_passes(project):
    pid = project["id"]
    projects.log_contacts(
        pid, [{"first_name": "Mira", "last_name": "Lee", "company": "Alpha Co"}], "imported",
        at=days_ago(10), source="old list.csv",
    )  # fmt: skip
    cid = make_campaign(pid, "Fresh")
    add_company(cid, "Alpha Co")
    find(cid, MIRA)
    person = rows(cid)[0]
    assert person["status"] == "review" and "imported" in person["reasons_json"][0]
    # the cooldown shrinks below the contact's age: recheck frees the person
    projects.update_project(pid, cooldown_days=5)
    out = people.recheck_people(cid)
    assert [p["name"] for p in out["to_confirmed"]] == ["Mira Lee"]
    assert names(cid, "confirmed") == ["Mira Lee"]
    assert [r["channel"] for r in log_rows(pid) if r["campaign_id"] == cid] == ["found"]
    # and back: a longer cooldown makes recheck hold the person again, memory entries of the
    # campaign itself never count against it
    projects.update_project(pid, cooldown_days=90)
    out = people.recheck_people(cid)
    assert [p["name"] for p in out["to_review"]] == ["Mira Lee"]
    assert rows(cid)[0]["reasons_json"][0].startswith("L24:")
    assert [r for r in log_rows(pid) if r["campaign_id"] == cid] == []  # no longer "found" here


def test_cooldown_zero_never_blocks(project):
    pid = project["id"]
    projects.update_project(pid, cooldown_days=0)
    projects.log_contacts(
        pid, [{"first_name": "Mira", "last_name": "Lee", "company": "Alpha Co"}], "email_sent"
    )
    cid = make_campaign(pid)
    add_company(cid, "Alpha Co")
    find(cid, MIRA)
    assert names(cid, "confirmed") == ["Mira Lee"]


def test_recheck_keeps_confirmed_people_and_respects_limits(project):
    cid = make_campaign(project["id"], max_per_company=1)
    add_company(cid, "Alpha Co")
    find(cid, PAIR)
    out = people.recheck_people(cid)
    assert out["to_review"] == [] and out["to_confirmed"] == []
    assert names(cid, "reserve") == ["Mira Lee"]  # reserve people are not re-checked
    # a review person that the rules now confirm joins as reserve when the company is full
    with db.session() as conn:
        db.insert(
            conn, "people", campaign_id=cid, company_id=rows(cid)[0]["company_id"],
            first_name="Hana", last_name="Reyes", raw_name="Hana Reyes", status="review",
            linkedin_url="https://www.linkedin.com/in/hana-reyes", title=HRDIR,
            evidence_title=hit("Hana Reyes", HRDIR, "Alpha Co").title,
            evidence_snippet=hit("Hana Reyes", HRDIR, "Alpha Co").snippet,
        )  # fmt: skip
    people.recheck_people(cid)
    assert sorted(names(cid, "reserve")) == ["Hana Reyes", "Mira Lee"]


# --- move_reserve -------------------------------------------------------------------------------


def reserve_setup(project, per_company=1):
    first = make_campaign(project["id"], "First", max_per_company=per_company)
    add_company(first, "Alpha Co", domain="alpha.example")
    mapping = {
        "Alpha Co": [
            hit("Omar Haddad", HEAD, "Alpha Co"),
            hit("Mira Lee", MANAGER, "Alpha Co"),
            hit("Hana Reyes", HRDIR, "Alpha Co"),
        ]
    }
    find(first, mapping)
    assert names(first, "confirmed") == ["Omar Haddad"]
    return first


def test_move_reserve_moves_people_into_another_campaign_of_the_project(project):
    first = reserve_setup(project)
    second = make_campaign(project["id"], "Second", max_per_company=1)
    out = people.move_reserve(first, second)
    assert out["ok"] and out["moved"] == 1 and out["left_in_reserve"] == 1
    assert out["limit_reached"] == 1 and out["moved_ids"]
    moved = rows(second, "confirmed")
    assert [r["raw_name"] for r in moved] == ["Mira Lee"]  # L&D group before HR
    assert names(first, "reserve") == ["Hana Reyes"]
    assert names(first, "confirmed") == ["Omar Haddad"]
    # the person row moved with its company, which was copied into the target
    with db.session() as conn:
        company = db.get(conn, "companies", moved[0]["company_id"])
    assert company["campaign_id"] == second and company["domain"] == "alpha.example"
    assert company["status"] == "approved"
    found = [r for r in log_rows(project["id"]) if r["campaign_id"] == second]
    assert [r["person_id"] for r in found] == [moved[0]["id"]]
    # a second call has room only when the limits allow it
    assert people.move_reserve(first, second)["moved"] == 0
    third = make_campaign(project["id"], "Third")
    again = people.move_reserve(first, third)
    assert again["moved"] == 1 and names(first, "reserve") == []
    assert names(third, "confirmed") == ["Hana Reyes"]


def test_move_reserve_arguments_tighten_the_limits(project):
    first = make_campaign(project["id"], "First", max_per_company=1)
    add_company(first, "Alpha Co")
    people_ = [hit(f"Person {n}", MANAGER, "Alpha Co") for n in ("Ann", "Bob", "Cy", "Di")]
    find(first, {"Alpha Co": people_})
    assert len(names(first, "reserve")) == 3
    second = make_campaign(project["id"], "Second")
    assert people.move_reserve(first, second, max_per_company=2)["moved"] == 2
    assert people.move_reserve(first, second, max_people=2)["moved"] == 0  # already 2 there
    assert people.move_reserve(first, second)["moved"] == 1  # the target has no limit of its own


def test_move_reserve_respects_max_people_and_max_companies_of_the_target(project):
    first = make_campaign(project["id"], "First", max_per_company=1)
    for name in ("Alpha Co", "Beta Co", "Gamma Co"):
        add_company(first, name)
    mapping = {
        n: [hit(f"A {n[:2]}", HEAD, n), hit(f"B {n[:2]}", MANAGER, n)]
        for n in ("Alpha Co", "Beta Co", "Gamma Co")
    }
    find(first, mapping)
    assert len(names(first, "reserve")) == 3
    narrow = make_campaign(project["id"], "Narrow", max_companies=1)
    out = people.move_reserve(first, narrow)
    assert out["moved"] == 1 and out["limit_reached"] == 2  # one company, not three
    capped = make_campaign(project["id"], "Capped", max_people=1)
    out = people.move_reserve(first, capped)
    assert out["moved"] == 1 and out["limit_reached"] == 1 and out["left_in_reserve"] == 1


def test_move_reserve_refuses_other_projects_and_bad_arguments(project, offer_id):
    first = reserve_setup(project)
    elsewhere = projects.create_project("Elsewhere", offer_id=offer_id)
    other = make_campaign(elsewhere["id"], "Other")
    for call, code in (
        (lambda: people.move_reserve(first, other), "different_project"),
        (lambda: people.move_reserve(first, first), "invalid_value"),
        (lambda: people.move_reserve(first, 999), "not_found"),
        (lambda: people.move_reserve(first, make_campaign(project["id"], "Z"), max_people=0),
         "invalid_value"),
        (lambda: people.move_reserve(first, make_campaign(project["id"], "Y"),
                                     max_per_company=True), "invalid_value"),
    ):  # fmt: skip
        with pytest.raises((PeopleError, CampaignError)) as err:
            call()
        assert err.value.code == code
    archived = make_campaign(project["id"], "Old")
    camp.archive_campaign(archived)
    with pytest.raises(PeopleError) as err:
        people.move_reserve(first, archived)
    assert err.value.code == "campaign_archived"


def test_move_reserve_leaves_people_the_target_does_not_confirm_or_already_has(project):
    first = reserve_setup(project)
    seniors = make_campaign(project["id"], "Seniors")
    camp.update_campaign(seniors, seniority_min="vp")
    out = people.move_reserve(first, seniors)
    assert out["moved"] == 0 and out["not_matching"] == 2  # a manager and a director are below vp
    both = make_campaign(project["id"], "Both")
    with db.session() as conn:  # the target already holds one of them (same LinkedIn profile)
        co = db.insert(conn, "companies", campaign_id=both, name="Alpha Co", status="approved")
        db.insert(conn, "people", campaign_id=both, company_id=co, status="review",
                  linkedin_url="https://www.linkedin.com/in/mira-lee",
                  raw_name="Mira Lee")  # fmt: skip
    out = people.move_reserve(first, both)
    assert out["duplicates"] == 1 and out["moved"] == 1
    assert names(both, "confirmed") == ["Hana Reyes"]


def test_move_reserve_leaves_people_who_were_contacted_since(project):
    first = reserve_setup(project)
    projects.log_contacts(
        project["id"], [{"first_name": "Mira", "last_name": "Lee", "company": "Alpha Co"}],
        "email_sent",
    )  # fmt: skip
    second = make_campaign(project["id"], "Second")
    out = people.move_reserve(first, second)
    assert out["already_contacted"] == 1 and out["moved"] == 1
    assert names(first, "reserve") == ["Mira Lee"]


# --- QA checks L24 and L25 ----------------------------------------------------------------------


def check(cid):
    camp_dict = camp.get_campaign(cid)
    data = qa_leads.load_leads(camp_dict)
    return {pid: {i.code: i for i in issues} for pid, issues in qa_leads.check_all(data).items()}


def confirmed_person(cid, company_id, first, last, url=None, **extra):
    with db.session() as conn:
        return db.insert(
            conn, "people", campaign_id=cid, company_id=company_id, first_name=first,
            last_name=last, raw_name=f"{first} {last}", greeting_name=first, status="confirmed",
            confirmed=1, title=MANAGER, designation="L&D / Training heads", seniority="manager",
            country="SG", linkedin_url=url or f"https://www.linkedin.com/in/{first}-{last}".lower(),
            evidence_title=hit(f"{first} {last}", MANAGER, "Alpha Co").title,
            evidence_snippet=hit(f"{first} {last}", MANAGER, "Alpha Co").snippet, **extra,
        )  # fmt: skip


def test_the_checks_are_registered_and_titled():
    assert contact_checks.check in qa_leads.EXTRA_LEAD_CHECKS
    contact_checks.register()
    assert qa_leads.EXTRA_LEAD_CHECKS.count(contact_checks.check) == 1
    for code in ("L24", "L25"):
        assert qa_leads.LEAD_SEVERITY[code] == "block" and qa_leads.LEAD_TITLES[code]


def test_l25_blocks_the_later_person_only(project):
    a = make_campaign(project["id"], "First")
    b = make_campaign(project["id"], "Second")
    co_a, co_b = add_company(a, "Alpha Co"), add_company(b, "Alpha Co")
    early = confirmed_person(a, co_a, "Mira", "Lee")
    late = confirmed_person(b, co_b, "Mira", "Lee")
    issues_a, issues_b = check(a)[early], check(b)[late]
    assert "L25" not in issues_a and "L24" not in issues_a
    assert issues_b["L25"].severity == "block" and "'First'" in issues_b["L25"].detail
    # an archived campaign no longer counts
    camp.archive_campaign(a)
    assert "L25" not in check(b)[late]


def test_l25_finds_people_by_email_too(project):
    a = make_campaign(project["id"], "First")
    b = make_campaign(project["id"], "Second")
    co_a, co_b = add_company(a, "Alpha Co"), add_company(b, "Beta Co")
    early = confirmed_person(a, co_a, "Mira", "Lee", url="https://www.linkedin.com/in/m-one")
    late = confirmed_person(b, co_b, "M", "Lee", url="https://www.linkedin.com/in/m-two")
    for pid in (early, late):
        with db.session() as conn:
            db.insert(conn, "emails", person_id=pid, email="mira@alpha.example", status="verified")
    assert check(b)[late]["L25"].severity == "block"


def test_l24_blocks_people_contacted_in_another_campaign_or_imported(project):
    pid = project["id"]
    a = make_campaign(pid, "First")
    b = make_campaign(pid, "Second")
    co_a, co_b = add_company(a, "Alpha Co"), add_company(b, "Alpha Co")
    person_a = confirmed_person(a, co_a, "Mira", "Lee")
    person_b = confirmed_person(b, co_b, "Omar", "Haddad")
    other_b = confirmed_person(b, co_b, "Hana", "Reyes")
    projects.log_people([person_a], "email_sent", emails={person_a: "mira@alpha.example"})
    projects.log_contacts(pid, [{"first_name": "Omar", "last_name": "Haddad",
                                 "company": "Alpha Co"}], "imported", at=days_ago(3))  # fmt: skip
    # the campaign's own log entry never blocks its own person
    assert "L24" not in check(a)[person_a]
    issues = check(b)
    assert issues[person_b]["L24"].severity == "block"
    assert "imported" in issues[person_b]["L24"].detail
    assert "L24" not in issues[other_b]
    # an entry from another campaign blocks: move the person's log entry to campaign b's view
    with db.session() as conn:
        conn.execute("UPDATE contact_log SET campaign_id = ? WHERE person_id = ?", (b, person_a))
    again = check(a)
    assert "L24" in again[person_a] and "email_sent" not in again[person_a]["L24"].detail
    assert "Already contacted on" in again[person_a]["L24"].detail


def test_a_manual_promotion_turns_the_blocks_into_warnings(project):
    pid = project["id"]
    a = make_campaign(pid, "First")
    b = make_campaign(pid, "Second")
    co_a, co_b = add_company(a, "Alpha Co"), add_company(b, "Alpha Co")
    confirmed_person(a, co_a, "Mira", "Lee")
    late = confirmed_person(b, co_b, "Mira", "Lee", reasons_json=["MANUAL:confirmed: I checked"])
    issue = check(b)[late]["L25"]
    assert issue.severity == "warn" and "accepted by hand: confirmed: I checked" in issue.detail


def test_a_later_hand_promoted_duplicate_does_not_block_the_earlier_person(project):
    pid = project["id"]
    first = make_campaign(pid, "First")
    add_company(first, "Alpha Co")
    find(first, MIRA)
    mira = rows(first, "confirmed")[0]["id"]
    second = make_campaign(pid, "Second")
    add_company(second, "Alpha Co")
    find(second, MIRA)  # L25: goes to review
    dup = rows(second)[0]["id"]
    assert people.set_person_status(second, [dup], "confirmed", "checked by hand")["updated"] == 1
    assert [r["campaign_id"] for r in log_rows(pid) if r["channel"] == "found"] == [first, second]
    issues = check(first)[mira]
    assert "L24" not in issues and "L25" not in issues  # the later copy's entry is ignored
    out = people.recheck_people(first)
    assert out["to_review"] == [] and names(first, "confirmed") == ["Mira Lee"]
    assert "L24" not in check(first)[mira]
    # the later (hand-accepted) copy still shows the warning
    assert check(second)[dup]["L25"].severity == "warn"


def test_a_migrated_duplicate_blocks_only_the_later_copy_with_l25(project):
    pid = project["id"]
    a = make_campaign(pid, "First")
    b = make_campaign(pid, "Second")
    co_a, co_b = add_company(a, "Alpha Co"), add_company(b, "Alpha Co")
    early = confirmed_person(a, co_a, "Mira", "Lee")
    late = confirmed_person(b, co_b, "Mira", "Lee")
    with db.session() as conn:
        conn.execute("DELETE FROM contact_log")
        projects.backfill_v1_contacts(conn)  # what the v2 migration logs for v1 data
    assert [r["person_id"] for r in log_rows(pid)] == [early, late]
    assert set(check(a)[early]) & {"L24", "L25"} == set()
    assert set(check(b)[late]) & {"L24", "L25"} == {"L25"}
    assert people.recheck_people(a)["to_review"] == []
    # exports, sends and imports of a later person still count
    projects.log_contacts(
        pid, [{"first_name": "Mira", "last_name": "Lee", "company": "Alpha Co"}], "imported"
    )
    assert "L24" in check(a)[early]


def test_campaigns_without_a_project_get_no_project_checks():
    from qa_world import campaign_dict, company_row, person_row

    context = qa_leads.LeadContext.basic(campaign_dict())
    assert contact_checks.check(person_row(), company_row(), campaign_dict(), None, context) == []
    assert context.extras == {}


# --- the memory is fed by exports, lemlist pushes and sending -----------------------------------


def test_log_people_uses_the_project_of_each_persons_campaign(project, offer_id):
    elsewhere = projects.create_project("Elsewhere", offer_id=offer_id)
    a, b = make_campaign(project["id"], "A"), make_campaign(elsewhere["id"], "B")
    p1 = confirmed_person(a, add_company(a, "Alpha Co"), "Mira", "Lee")
    p2 = confirmed_person(b, add_company(b, "Alpha Co"), "Omar", "Haddad")
    out = projects.log_people([p1, p2, p1, 9999], "lemlist", source="x", emails={p1: "m@x.example"})
    assert out == {"logged": 2, "skipped": 0}
    first, second = log_rows(project["id"]), log_rows(elsewhere["id"])
    assert len(first) == 1 and first[0]["key_email"] == "m@x.example"
    assert first[0]["campaign_id"] == a and first[0]["person_id"] == p1
    assert len(second) == 1 and second[0]["key_email"] == ""
    assert projects.log_people([], "lemlist") == {"logged": 0, "skipped": 0}


def test_remember_never_raises(project, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(projects, "log_people", boom)
    assert projects.remember([1], "email_sent") is None


def test_deleting_a_campaign_keeps_the_memory(project):
    cid = make_campaign(project["id"], "Gone soon")
    pid = confirmed_person(cid, add_company(cid, "Alpha Co"), "Mira", "Lee")
    projects.log_people([pid], "found")
    with db.session() as conn:
        conn.execute("DELETE FROM campaigns WHERE id = ?", (cid,))
    rest = log_rows(project["id"])
    assert len(rest) == 1 and rest[0]["campaign_id"] is None and rest[0]["person_id"] is None
    assert projects.recently_contacted(
        project["id"], {"first_name": "Mira", "last_name": "Lee", "company": "Alpha Co"}
    )  # fmt: skip


# --- AI search mode follows the limits too ------------------------------------------------------


def test_ai_search_tasks_follow_the_company_and_people_limits(project):
    from leadgen.services import ai_search

    cid = make_campaign(project["id"], max_companies=1, max_people=1, search_mode="ai")
    add_company(cid, "Alpha Co")
    add_company(cid, "Beta Co")
    reply = ai_search.get_search_tasks(cid)
    assert {t["company"] for t in reply["tasks"]} == {"Alpha Co"} and len(reply["tasks"]) == 2
    first = reply["tasks"][0]
    result = hit("Mira Lee", MANAGER, "Alpha Co")
    out = ai_search.submit_search_results(
        first["task_id"],
        [{"title": result.title, "url": result.link,
          "snippet": result.snippet + " with a full team of trainers"}],
        "test search",
    )  # fmt: skip
    assert out["summary"]["confirmed"] == 1
    assert [r["channel"] for r in log_rows(project["id"])] == ["found"]
    after = ai_search.get_search_tasks(cid)
    assert after["tasks"] == [] and after["limit_reached"] is True and after["next_cursor"] is None
    assert "max_people" in after["message"]
