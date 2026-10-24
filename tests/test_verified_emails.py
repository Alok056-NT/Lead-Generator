"""Verified-only emails (T25, part A): the project's email policy, what an export may carry in
its `email` column, the lemlist push, the counts, and the schema v4 migration.

All people, companies and addresses are invented (`example` domains). No network is used.
"""

from __future__ import annotations

import csv
import shutil
from pathlib import Path

import httpx
import pytest
import respx
from qa_world import (
    add_company,
    add_email,
    add_message,
    add_person,
    make_campaign,
    message_row,
    suppress,
)

from leadgen import db
from leadgen.services import campaign as camp
from leadgen.services import export, projects
from leadgen.services.export import export_campaign
from leadgen.services.lemlist import push_to_lemlist

NAMES = ("Aria", "Marcus", "Lena", "Priya", "Tomas", "Wen")
API = "https://api.lemlist.com/api"


def read(path: str | Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def build(statuses: list[str | None], **campaign) -> tuple[int, list[int]]:
    """A LinkedIn-channel campaign with one person per entry; the entry is that person's email
    status (None = no email). Every person has one approved LinkedIn message."""
    campaign.setdefault("channels", ("linkedin",))
    cid = make_campaign(**campaign)["id"]
    co = add_company(cid)
    ids = []
    for i, status in enumerate(statuses):
        name = NAMES[i]
        pid = add_person(cid, co, name, "Tan")
        if status:
            add_email(pid, status, f"{name.lower()}.tan@northwind.example")
        body = message_row()["body"].replace("Aria", name)
        add_message(pid, "linkedin_message", body=body, ai_review="pass", status="approved")
        ids.append(pid)
    return cid, ids


def by_name(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {r["firstName"]: r for r in rows}


# --- the project's email policy ---------------------------------------------------------------


def test_policy_defaults_to_verified_only(ready_db):
    project = projects.create_project("Northwind Training")
    assert project["email_policy"] == "verified_only"
    assert projects.get_project(project["id"])["email_policy"] == "verified_only"
    assert projects.list_projects()[0]["email_policy"] == "verified_only"
    assert projects.EMAIL_POLICIES == ("verified_only", "allow_risky")


def test_policy_can_be_set_changed_and_is_validated(ready_db):
    project = projects.create_project("Harbour Freight", email_policy="allow_risky")
    assert project["email_policy"] == "allow_risky"
    changed = projects.update_project(project["id"], email_policy="verified_only")
    assert changed["email_policy"] == "verified_only"
    for bad in ("allow_guess", "", None, True):
        with pytest.raises(projects.ProjectError) as info:
            projects.update_project(project["id"], email_policy=bad)
        assert info.value.code == "invalid_email_policy"
    with pytest.raises(projects.ProjectError):
        projects.create_project("Lakeside Co", email_policy="guess")
    assert projects.get_project(project["id"])["email_policy"] == "verified_only"


def new_campaign(project_id, name="SG", **options):
    return camp.create_campaign(
        name=name, project_id=project_id, countries=["SG"], channels=["email"],
        designations=["ld"], options=options
    )


def project_with_offer(policy):
    with db.session() as conn:
        offer_id = db.insert(conn, "offers", name="Bright Harbour", source_type="text")
    return projects.create_project(
        f"Project {policy}", offer_id=offer_id, email_policy=policy
    )["id"]


def test_a_new_campaign_takes_the_projects_policy(ready_db):
    strict = new_campaign(project_with_offer("verified_only"), name="Strict run")
    loose = new_campaign(project_with_offer("allow_risky"), name="Loose run")
    assert strict["options"]["allow_risky_emails"] is False
    assert loose["options"]["allow_risky_emails"] is True
    assert camp.exportable_email_statuses(strict) == ("verified",)
    assert camp.exportable_email_statuses(loose) == ("verified", "risky")


def test_an_explicit_campaign_option_wins_at_creation(ready_db):
    pid = project_with_offer("verified_only")
    got = new_campaign(pid, name="Run A", allow_risky_emails=True)
    assert got["options"]["allow_risky_emails"] is True
    pid2 = project_with_offer("allow_risky")
    got = new_campaign(pid2, name="Run B", allow_risky_emails=False)
    assert got["options"]["allow_risky_emails"] is False


def test_existing_campaigns_keep_their_stored_value_when_the_policy_changes(ready_db):
    pid = project_with_offer("verified_only")
    old = new_campaign(pid, name="Old run")
    projects.update_project(pid, email_policy="allow_risky")
    assert camp.get_campaign(old["id"])["options"]["allow_risky_emails"] is False
    fresh = new_campaign(pid, name="New run")
    assert fresh["options"]["allow_risky_emails"] is True
    projects.update_project(pid, email_policy="verified_only")
    assert camp.get_campaign(fresh["id"])["options"]["allow_risky_emails"] is True


def test_guess_is_never_exportable_whatever_the_options(ready_db):
    for options in ({}, {"allow_risky_emails": True},
                    {"allow_risky_emails": True, "allow_guess_emails": True}):  # fmt: skip
        assert "guess" not in camp.exportable_email_statuses({"options": options})


# --- schema v4 --------------------------------------------------------------------------------


def table_names(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def build_v3_database(path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(db, "MIGRATIONS", db.MIGRATIONS[:2])
        assert db.init_db(path) == 3
    with db.session(path) as conn:
        offer_id = db.insert(conn, "offers", name="Northwind Training", source_type="text")
        project_id = db.insert(conn, "projects", name="Northwind Training", offer_id=offer_id,
                               cooldown_days=30)  # fmt: skip
        campaign_id = db.insert(conn, "campaigns", name="SG", offer_id=offer_id,
                                project_id=project_id,
                                options_json={"allow_risky_emails": True})  # fmt: skip
        company = db.insert(conn, "companies", campaign_id=campaign_id, name="Northwind Logistics",
                            status="approved")  # fmt: skip
        person = db.insert(conn, "people", campaign_id=campaign_id, company_id=company,
                           first_name="Aria", last_name="Tan", raw_name="Aria Tan",
                           status="confirmed")  # fmt: skip
        db.insert(conn, "emails", person_id=person, email="aria.tan@northwind.example",
                  status="risky")  # fmt: skip
        db.insert(conn, "known_contacts", project_id=project_id, key_email="x@y.example",
                  imported_at="2026-09-01", updated_at="2026-09-01")  # fmt: skip
        db.insert(conn, "settings", key="k", value="v")
    return project_id


V3_TABLES = ("offers", "projects", "campaigns", "companies", "people", "emails",
             "known_contacts", "contact_log", "settings")  # fmt: skip


def test_migration_v4_on_a_copy_of_a_v3_database(tmp_path, monkeypatch):
    original = tmp_path / "v3.db"
    project_id = build_v3_database(original, monkeypatch)
    copy = tmp_path / "copy" / "leadgen.db"
    copy.parent.mkdir()
    shutil.copy2(original, copy)

    def counts(path):
        with db.session(path) as conn:
            return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in V3_TABLES}

    before = counts(copy)
    assert db.init_db(copy) == db.SCHEMA_VERSION == 4
    assert counts(copy) == before  # nothing was deleted or added
    with db.session(original) as conn:  # the original file was not touched
        assert db.current_version(conn) == 3
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(projects)")}
        assert "email_policy" not in columns
    with db.session(copy) as conn:
        assert db.current_version(conn) == 4
        row = db.get(conn, "projects", project_id)
        assert row["email_policy"] == "verified_only" and row["cooldown_days"] == 30
        stored = db.query(conn, "SELECT options_json FROM campaigns")[0]["options_json"]
        assert stored["allow_risky_emails"] is True  # the old campaign keeps its own value
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert db.init_db(copy) == 4  # running it again changes nothing


def test_failed_v4_migration_rolls_back(tmp_path, monkeypatch):
    path = tmp_path / "v3.db"
    build_v3_database(path, monkeypatch)
    monkeypatch.setattr(
        db, "MIGRATIONS",
        [*db.MIGRATIONS[:2], (4, db._V4_SQL + "\nINSERT INTO no_such_table VALUES (1);")],
    )  # fmt: skip
    import sqlite3

    with pytest.raises(sqlite3.Error):
        db.init_db(path)
    with db.session(path) as conn:
        assert db.current_version(conn) == 3
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(projects)")}
        assert "email_policy" not in columns


# --- exports: the email column ----------------------------------------------------------------


def test_only_verified_emails_reach_the_email_column(ready_db):
    cid, _ = build(["verified", "risky", "guess", "unknown", "invalid", None])
    reply = export_campaign(cid, "generic", include="qa_pass")
    rows = by_name(read(reply["path"]))
    assert rows["Aria"]["email"] == "aria.tan@northwind.example"
    assert rows["Aria"]["emailStatus"] == "verified"
    assert rows["Aria"]["unverifiedEmail"] == ""
    for name, status in (("Marcus", "risky"), ("Lena", "guess"), ("Priya", "unknown")):
        assert rows[name]["email"] == "" and rows[name]["emailStatus"] == ""
        assert rows[name]["unverifiedEmail"] == f"{name.lower()}.tan@northwind.example"
        assert rows[name]["unverifiedEmailStatus"] == status
    for name in ("Tomas", "Wen"):  # an invalid address and no address: nothing at all
        assert rows[name]["email"] == "" and rows[name]["unverifiedEmail"] == ""
    assert reply["verified_emails"] == 1 and reply["found_not_verified"] == 3
    assert any("not verified" in w and "Reoon" in w for w in reply["warnings"])


def test_the_lemlist_file_follows_the_same_rule(ready_db):
    cid, _ = build(["verified", "guess"])
    reply = export_campaign(cid, "lemlist", include="qa_pass")
    rows = by_name(read(reply["path"]))
    header = list(rows["Aria"])
    assert header.index("unverifiedEmail") > header.index("email")
    assert rows["Marcus"]["email"] == ""
    assert rows["Marcus"]["unverifiedEmail"] == "marcus.tan@northwind.example"
    assert any("unverifiedEmail" in step for step in reply["next_steps"])


def test_instantly_has_no_unverified_address_and_leaves_those_people_out(ready_db):
    cid, _ = build(["verified", "guess"])
    reply = export_campaign(cid, "instantly", include="qa_pass")
    rows = read(reply["path"])
    assert [r["first_name"] for r in rows] == ["Aria"]
    text = Path(reply["path"]).read_text(encoding="utf-8-sig")
    assert "marcus.tan@northwind.example" not in text
    assert reply["skipped_no_email"] == 1
    assert any("no verified email" in w for w in reply["warnings"])


def test_risky_counts_only_when_the_campaign_allows_it(ready_db):
    strict, _ = build(["risky"])
    assert read(export_campaign(strict, "generic", include="qa_pass")["path"])[0]["email"] == ""
    loose, _ = build(["risky"], options={"allow_risky_emails": True})
    row = read(export_campaign(loose, "generic", include="qa_pass")["path"])[0]
    assert row["email"] == "aria.tan@northwind.example" and row["emailStatus"] == "risky"
    assert row["unverifiedEmail"] == ""


def test_a_guess_stays_out_even_with_the_expert_flag(ready_db):
    cid, _ = build(["guess"], options={"allow_guess_emails": True, "allow_risky_emails": True})
    reply = export_campaign(cid, "generic", include="qa_pass")
    row = read(reply["path"])[0]
    assert row["email"] == "" and row["unverifiedEmail"] == "aria.tan@northwind.example"


def test_role_and_suppressed_addresses_are_in_neither_column(ready_db):
    cid, ids = build(["verified", "guess", "verified"])
    with db.session() as conn:
        conn.execute("UPDATE emails SET email = 'info@northwind.example' WHERE person_id = ?",
                     (ids[0],))  # fmt: skip
    suppress("email", "marcus.tan@northwind.example")
    reply = export_campaign(cid, "generic", include="qa_pass")
    rows = by_name(read(reply["path"]))
    assert rows["Aria"]["email"] == "" and rows["Aria"]["unverifiedEmail"] == ""
    assert "Marcus" not in rows  # a suppressed person never leaves (L16)
    assert rows["Lena"]["email"] == "lena.tan@northwind.example"


def test_every_address_in_an_email_column_is_verified(ready_db):
    cid, _ = build(["verified", "risky", "guess", "unknown", "invalid"])
    reply = export_campaign(cid, "generic", include="all_confirmed")
    rows = read(reply["path"])
    filled = [r for r in rows if r["email"]]
    assert [r["firstName"] for r in filled] == ["Aria"] and filled[0]["emailStatus"] == "verified"
    qa = read(reply["qa_report_path"])
    # the report is for reading, not importing, and its column names say so
    assert "email" not in qa[0] and {"found_email", "found_email_status"} <= set(qa[0])
    assert {r["firstName"]: r["found_email_status"] for r in qa} == {
        "Aria": "verified", "Marcus": "risky", "Lena": "guess", "Priya": "unknown",
        "Tomas": "invalid",
    }


def test_the_columns_are_the_documented_ones():
    assert export.GENERIC_COLUMNS[export.GENERIC_COLUMNS.index("email") + 1 :][:3] == (
        "emailStatus", "unverifiedEmail", "unverifiedEmailStatus",
    )
    assert "unverifiedEmail" not in export.INSTANTLY_COLUMNS.values()


# --- lemlist push -----------------------------------------------------------------------------


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("LEMLIST_API_KEY", "test-key-1234")


@respx.mock
def test_lemlist_pushes_verified_emails_only(ready_db, key):
    cid, ids = build(["verified", "risky", "guess", "unknown"])
    dry = push_to_lemlist(cid, "cam_abc123", allow_incomplete=True)
    assert dry["ready"] == 4 and dry["to_push"] == 1
    assert dry["no_email_for_lemlist_api"] == 3 and dry["found_not_verified"] == 3
    route = respx.post(f"{API}/campaigns/cam_abc123/leads/").mock(
        return_value=httpx.Response(200, json={"_id": "lea_1"})
    )
    real = push_to_lemlist(cid, "cam_abc123", dry_run=False, confirm=True, allow_incomplete=True)
    assert real["pushed"]["created"] == 1 and route.call_count == 1
    sent = route.calls[0].request.content.decode()
    assert "aria.tan@northwind.example" in sent
    for name in ("marcus", "lena", "priya"):
        assert name not in sent


@respx.mock
def test_lemlist_pushes_risky_when_the_campaign_allows_it(ready_db, key):
    cid, _ = build(["risky"], options={"allow_risky_emails": True})
    dry = push_to_lemlist(cid, "cam_abc123", allow_incomplete=True)
    assert dry["to_push"] == 1 and dry["found_not_verified"] == 0
    assert dry["preview"][0]["payload"]["emailStatus"] == "risky"


# --- counts -----------------------------------------------------------------------------------


def test_campaign_counts_verified_and_not_verified_emails(ready_db):
    cid, ids = build(["verified", "verified", "risky", "guess", "invalid", None])
    summary = camp.get_campaign(cid)["email_summary"]
    assert summary == {
        "verified_emails": 2, "found_not_verified": 2, "invalid_emails": 1, "no_email": 1,
    }
    assert camp.email_summary(cid) == summary
    camp.update_campaign(cid, allow_risky_emails=True)
    assert camp.email_summary(cid)["verified_emails"] == 3
    assert camp.email_summary(cid)["found_not_verified"] == 1


def test_a_person_with_two_addresses_counts_once_by_the_best_one(ready_db):
    cid, ids = build(["guess"])
    add_email(ids[0], "verified", "aria.t@northwind.example")
    assert camp.email_summary(cid)["verified_emails"] == 1
    assert camp.email_summary(cid)["found_not_verified"] == 0


def test_the_dashboard_campaign_list_carries_the_counts(ready_db):
    from fastapi.testclient import TestClient

    from leadgen.dashboard import app as dash

    cid, _ = build(["verified", "guess"])
    client = TestClient(dash.create_app(), base_url="http://127.0.0.1:8765")
    reply = client.get("/api/campaigns").json()
    row = next(c for c in reply["campaigns"] if c["id"] == cid)
    assert row["email_summary"]["verified_emails"] == 1
    assert row["email_summary"]["found_not_verified"] == 1
    page = client.get(f"/api/campaigns/{cid}").json()
    assert page["campaign"]["email_summary"]["found_not_verified"] == 1


# --- the dashboard cannot switch guessed emails on -----------------------------------------------


def test_the_dashboard_has_no_way_to_allow_guessed_emails():
    root = Path(export.__file__).parents[1] / "dashboard"
    for path in (root / "app.py", *sorted((root / "static").glob("*"))):
        text = path.read_text(encoding="utf-8")
        assert "allow_guess" not in text, path.name


def test_no_dashboard_route_changes_campaign_options(ready_db):
    from fastapi.testclient import TestClient

    from leadgen.dashboard import app as dash

    cid, _ = build(["guess"])
    client = TestClient(dash.create_app(), base_url="http://127.0.0.1:8765")
    headers = {"X-LeadGen": "1"}
    for method, path in (
        ("POST", f"/api/campaigns/{cid}"), ("PUT", f"/api/campaigns/{cid}"),
        ("PATCH", f"/api/campaigns/{cid}"), ("POST", f"/api/campaigns/{cid}/options"),
    ):  # fmt: skip
        reply = client.request(method, path, json={"allow_guess_emails": True}, headers=headers)
        assert reply.status_code in (404, 405)
    reply = client.post("/api/settings", json={"allow_guess_emails": True}, headers=headers)
    assert reply.status_code in (400, 422)  # unknown fields are refused
    assert camp.get_campaign(cid)["options"]["allow_guess_emails"] is False


# --- every address, whatever its source, passes a verifier ------------------------------------


def test_only_the_email_finder_stores_addresses():
    import re

    src = Path(export.__file__).parents[1]
    writer = re.compile(r"(?:insert|upsert)\(\s*conn,\s*[\"']emails[\"']")
    offenders = []
    for path in sorted(src.rglob("*.py")):
        if path.name == "emails.py" and path.parent.name == "services":
            continue
        text = path.read_text(encoding="utf-8")
        if "store_email(" in text or "INTO emails" in text or writer.search(text):
            offenders.append(path.name)
    assert offenders == []
