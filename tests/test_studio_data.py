"""V12 Studio data (C1): migration v5, sign-off name, chat session, delete project, AI mode and
the drafting sign-off. All people, companies and addresses are invented. No network."""

from __future__ import annotations

import anyio
import anyio.from_thread
import pytest
from mail_world import add_send
from mcp import Client
from qa_world import PROFILE, add_company, add_email, add_message, add_person, make_campaign

from leadgen import db, mcp_server
from leadgen.core import paths, secrets
from leadgen.core.text import slugify
from leadgen.services import browse, drafting, preferences, projects
from leadgen.services.preferences import PreferencesError
from leadgen.services.projects import ProjectError

pytestmark = pytest.mark.usefixtures("ready_db")


def _count(sql: str, *params) -> int:
    with db.session() as conn:
        return conn.execute(sql, params).fetchone()[0]


def test_migration_v5_keeps_every_row(tmp_path):
    path = tmp_path / "v4.db"
    with pytest.MonkeyPatch.context() as m:
        m.setattr(db, "MIGRATIONS", db.MIGRATIONS[:3])
        assert db.init_db(path) == 4
    with db.session(path) as conn:
        db.insert(conn, "projects", name="Northwind course")

    assert db.init_db(path) == db.SCHEMA_VERSION  # later migrations run too

    with db.session(path) as conn:
        row = db.query(conn, "SELECT * FROM projects")[0]
        assert row["name"] == "Northwind course"
        assert row["sender_name"] == "" and row["chat_session_id"] == ""
        assert conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 0


def test_sender_name_is_saved_trimmed_and_validated():
    pid = make_campaign()["project_id"]

    saved = projects.update_project(pid, sender_name="  Priya   Nair ")
    assert saved["sender_name"] == "Priya Nair"
    for bad in ("x" * 121, 12):
        with pytest.raises(ProjectError) as err:
            projects.update_project(pid, sender_name=bad)
        assert err.value.code == "invalid_value"

    assert next(p for p in projects.list_projects() if p["id"] == pid)["sender_name"] == (
        "Priya Nair"
    )
    assert next(c for c in browse.project_cards() if c["id"] == pid)["sender_name"] == "Priya Nair"
    assert projects.update_project(pid, sender_name="")["sender_name"] == ""


def test_chat_session_helpers():
    pid = make_campaign()["project_id"]
    before = projects.get_project(pid)["updated_at"]

    assert projects.get_chat_session(pid) == ""
    projects.set_chat_session(pid, "session-123")
    assert projects.get_chat_session(pid) == "session-123"
    assert projects.get_project(pid)["updated_at"] == before
    projects.set_chat_session(pid, "")
    assert projects.get_chat_session(pid) == ""

    with pytest.raises(ProjectError) as err:
        projects.get_chat_session(99999)
    assert err.value.code == "not_found"
    with pytest.raises(ProjectError) as err:
        projects.set_chat_session(99999, "session-123")
    assert err.value.code == "not_found"


def test_delete_project_removes_everything_under_it():
    camp = make_campaign()
    pid, cid, offer_id = camp["project_id"], camp["id"], camp["offer_id"]
    name = projects.get_project(pid)["name"]
    company = add_company(cid)
    first = add_person(cid, company, "Aria", "Tan")
    second = add_person(cid, company, "Ben", "Lim")
    add_email(first, address="aria@example.com")
    add_email(second, address="ben@example.com")
    email_msg = add_message(first, "email")
    linkedin_msg = add_message(second, "linkedin_message")
    sent = add_send(email_msg, "aria@example.com", "<sent1@example.com>", None, status="sent")
    projects.log_contacts(pid, [{"email": "a@example.com"}], "imported")
    with db.session() as conn:
        db.insert(conn, "events", send_id=sent, type="reply", at=db.now())
        db.insert(conn, "known_contacts", project_id=pid, imported_at=db.now())
        db.insert(conn, "chat_messages", project_id=pid, role="user", text="hi", at=db.now())
        for kind, entity in (("person", first), ("message", email_msg)):
            db.insert(conn, "qa_issues", entity_type=kind, entity_id=entity, code="X1",
                      severity="warn")  # fmt: skip
        task = db.insert(conn, "search_tasks", campaign_id=cid, kind="people", query="q")
        for key in (
            f"budget_used:{cid}:search", f"lemlist_push:{cid}:cam_x", f"compliance_ack:{cid}:SG",
            f"ai_search_task:{task}", f"offer_crawl:{offer_id}",
        ):  # fmt: skip
            db.insert(conn, "settings", key=key, value="1")
    export = paths.exports_dir(slugify(camp["name"]))
    (export / "people.csv").write_text("x", encoding="utf-8")
    uploads = paths.home() / "uploads" / str(pid)
    uploads.mkdir(parents=True)
    (uploads / "a.txt").write_text("x", encoding="utf-8")

    other = make_campaign()
    other_company = add_company(other["id"])
    other_person = add_person(other["id"], other_company, "Cara", "Ng")
    add_email(other_person, address="cara@example.com")
    with db.session() as conn:
        db.insert(conn, "suppression", kind="email", value="blocked@example.com")

    reply = projects.delete_project(pid, name.upper())

    assert reply == {"deleted": True, "project_id": pid, "campaigns": 1, "people": 2, "messages": 2}
    assert _count("SELECT COUNT(*) FROM projects WHERE id = ?", pid) == 0
    assert _count("SELECT COUNT(*) FROM campaigns WHERE id = ?", cid) == 0
    assert _count("SELECT COUNT(*) FROM companies WHERE campaign_id = ?", cid) == 0
    assert _count("SELECT COUNT(*) FROM people WHERE campaign_id = ?", cid) == 0
    assert _count("SELECT COUNT(*) FROM emails WHERE person_id IN (?, ?)", first, second) == 0
    assert _count("SELECT COUNT(*) FROM messages WHERE id IN (?, ?)", email_msg, linkedin_msg) == 0
    assert _count("SELECT COUNT(*) FROM sends WHERE id = ?", sent) == 0
    assert _count("SELECT COUNT(*) FROM events WHERE send_id = ?", sent) == 0
    assert _count("SELECT COUNT(*) FROM qa_issues WHERE entity_id IN (?, ?)", first, email_msg) == 0
    assert _count("SELECT COUNT(*) FROM contact_log WHERE project_id = ?", pid) == 0
    assert _count("SELECT COUNT(*) FROM known_contacts WHERE project_id = ?", pid) == 0
    assert _count("SELECT COUNT(*) FROM chat_messages WHERE project_id = ?", pid) == 0
    assert _count("SELECT COUNT(*) FROM search_tasks WHERE id = ?", task) == 0
    assert _count("SELECT COUNT(*) FROM offers WHERE id = ?", offer_id) == 0
    assert _count(
        "SELECT COUNT(*) FROM settings WHERE key LIKE ? OR key LIKE ? OR key LIKE ? "
        "OR key IN (?, ?)",
        f"budget_used:{cid}:%", f"lemlist_push:{cid}:%", f"compliance_ack:{cid}:%",
        f"ai_search_task:{task}", f"offer_crawl:{offer_id}",
    ) == 0  # fmt: skip
    assert not export.exists() and not uploads.exists()

    # the other project and the do-not-contact list are untouched
    assert _count("SELECT COUNT(*) FROM people WHERE campaign_id = ?", other["id"]) == 1
    assert _count("SELECT COUNT(*) FROM emails WHERE person_id = ?", other_person) == 1
    assert _count("SELECT COUNT(*) FROM suppression WHERE value = ?", "blocked@example.com") == 1


def test_delete_project_wrong_name_changes_nothing():
    camp = make_campaign()
    pid = camp["project_id"]

    with pytest.raises(ProjectError) as err:
        projects.delete_project(pid, "Some other name")

    assert err.value.code == "name_mismatch"
    assert _count("SELECT COUNT(*) FROM campaigns WHERE id = ?", camp["id"]) == 1
    assert projects.get_project(pid)["counts"]["campaigns"]["total"] == 1


def test_delete_project_refuses_while_emails_are_queued():
    camp = make_campaign(channels=("email",))
    pid, cid = camp["project_id"], camp["id"]
    name = projects.get_project(pid)["name"]
    person = add_person(cid, add_company(cid), "Aria", "Tan")
    add_email(person, address="aria@example.com")
    message = add_message(person, "email")
    send = add_send(message, "aria@example.com", "<q1@example.com>", None, status="queued")

    with pytest.raises(ProjectError) as err:
        projects.delete_project(pid, name)
    assert err.value.code == "sending_in_progress"
    assert _count("SELECT COUNT(*) FROM projects WHERE id = ?", pid) == 1

    with db.session() as conn:
        db.update(conn, "sends", send, status="sent")
    assert projects.delete_project(pid, name)["deleted"] is True


def test_delete_project_keeps_an_offer_another_project_uses():
    one = make_campaign()
    two = make_campaign()
    shared = one["offer_id"]
    projects.update_project(two["project_id"], offer_id=shared)

    projects.delete_project(one["project_id"], projects.get_project(one["project_id"])["name"])

    assert _count("SELECT COUNT(*) FROM offers WHERE id = ?", shared) == 1


def test_ai_mode_setting():
    db.init_db()
    assert preferences.get_ai_mode() == ""
    assert preferences.set_ai_mode("claude_plan") == "claude_plan"
    assert preferences.get_ai_mode() == "claude_plan"
    assert preferences.set_ai_mode("api_key") == "api_key"
    assert preferences.get_ai_mode() == "api_key"
    with pytest.raises(PreferencesError):
        preferences.set_ai_mode("magic")
    with db.session() as conn:
        db.upsert(conn, "settings", ["key"], key=preferences.AI_MODE_KEY, value="magic")
    assert preferences.get_ai_mode() == ""


def test_drafting_uses_the_project_sign_off_name():
    camp = make_campaign()
    cid, pid = camp["id"], camp["project_id"]
    before = drafting.get_drafting_batch(cid, "linkedin_message", include_guide=False)
    assert before["offer"]["sender"]["name"] == PROFILE["sender"]["name"]
    assert before["sender_name"] == PROFILE["sender"]["name"]

    projects.update_project(pid, sender_name="Priya")
    after = drafting.get_drafting_batch(cid, "linkedin_message", include_guide=False)

    assert after["offer"]["sender"]["name"] == "Priya" and after["sender_name"] == "Priya"
    assert after["offer"]["sender"]["company"] == before["offer"]["sender"]["company"]
    assert after["offer"]["sender"]["role"] == before["offer"]["sender"]["role"]


def test_sign_off_title_and_company_are_saved_and_validated():
    pid = make_campaign()["project_id"]

    saved = projects.update_project(
        pid, sender_title="  Head of   Partnerships ", sender_company="Bright Harbour Training"
    )
    assert saved["sender_title"] == "Head of Partnerships"
    assert saved["sender_company"] == "Bright Harbour Training"
    for field, label in (("sender_title", "job title"), ("sender_company", "company name")):
        for bad in ("x" * 121, 12):
            with pytest.raises(ProjectError) as err:
                projects.update_project(pid, **{field: bad})
            assert err.value.code == "invalid_value" and label in err.value.message
    assert projects.update_project(pid, sender_title="", sender_company="")["sender_title"] == ""


def test_mcp_update_project_takes_title_and_company():
    pid = make_campaign()["project_id"]
    with anyio.from_thread.start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(Client(mcp_server.build_server())) as client:
            result = portal.call(
                client.call_tool, "update_project",
                {"project_id": pid, "sender_title": "Head of Partnerships",
                 "sender_company": "Bright Harbour Training"},
            )

    assert not result.is_error
    saved = projects.get_project(pid)
    assert saved["sender_title"] == "Head of Partnerships"
    assert saved["sender_company"] == "Bright Harbour Training"


def test_mcp_update_project_takes_sender_name():
    pid = make_campaign()["project_id"]
    with anyio.from_thread.start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(Client(mcp_server.build_server())) as client:
            result = portal.call(
                client.call_tool, "update_project", {"project_id": pid, "sender_name": "Priya"}
            )
            tools = portal.call(client.list_tools).tools

    assert not result.is_error
    assert projects.get_project(pid)["sender_name"] == "Priya"
    assert len(tools) == 53


def test_anthropic_key_is_a_known_secret(monkeypatch):
    assert "ANTHROPIC_API_KEY" in secrets.KNOWN_KEYS
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-1234567890")
    assert secrets.masked("ANTHROPIC_API_KEY") == "…7890"
