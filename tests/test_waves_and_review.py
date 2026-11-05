"""V14: waves list, the review drawer's routes (edit, approve, skip) and "export all waves".

Everything is invented; no network."""

from __future__ import annotations

import csv

import pytest
from dashboard_world import seed
from fastapi.testclient import TestClient
from qa_world import add_company, add_email, add_message, add_person, get_message

from leadgen import db
from leadgen.dashboard import app as dash
from leadgen.services import ai_setup, browse, export, message_review, projects, review
from leadgen.services import campaign as campaign_svc

BASE = "http://127.0.0.1:8765"
CSRF = {"X-LeadGen": "1"}


@pytest.fixture
def client(ready_db, monkeypatch):
    monkeypatch.setattr(ai_setup, "get_status", lambda refresh=False: {"ready": False, "mode": ""})
    return TestClient(dash.create_app(), base_url=BASE)


def seeded() -> tuple[dict, int]:
    data = seed()
    return data, campaign_svc.get_campaign(data["campaign"])["project_id"]


def email_id(person_id: int) -> int:
    with db.session() as conn:
        return db.query(
            conn, "SELECT id FROM messages WHERE person_id = ? AND channel = 'email'", (person_id,)
        )[0]["id"]


# --- results carry what the drawer and the wave table show ------------------------------------


def test_results_carry_messages_email_status_and_message_state(ready_db):
    data, pid = seeded()
    leads = {lead["name"]: lead for lead in browse.studio_results(pid)["leads"]}
    aria = leads["Aria Tan"]
    assert aria["email_status"] == "verified" and aria["message_state"] == "written"
    assert {m["channel"] for m in aria["messages"]} == {"email", "linkedin_message"}
    assert all(m["owner_choice"] == "" for m in aria["messages"])
    assert leads["Ben Carter"]["email_status"] == "guess"
    message_review.set_choice(email_id(data["aria"]), "approved")
    message_review.set_choice(
        next(m["id"] for m in aria["messages"] if m["channel"] == "linkedin_message"), "approved"
    )
    again = {lead["name"]: lead for lead in browse.studio_results(pid)["leads"]}
    assert again["Aria Tan"]["message_state"] == "approved"


def test_message_state_words():
    state = browse._message_state
    assert state([]) == ""
    one = {"status": "qa_pass", "owner_choice": ""}
    assert state([one]) == "written"
    assert state([{**one, "owner_choice": "skipped"}]) == "skipped"
    assert state([{**one, "owner_choice": "approved"}, one]) == "written"
    assert state([{**one, "owner_choice": "skipped"}, {**one, "status": "sent"}]) == "sent"


# --- approve, skip, edit ----------------------------------------------------------------------


def test_choice_is_stored_and_can_be_undone(ready_db):
    data, _ = seeded()
    mid = email_id(data["aria"])
    assert message_review.set_choice(mid, "skipped")["message"]["owner_choice"] == "skipped"
    assert get_message(mid)["owner_choice"] == "skipped"
    message_review.set_choice(mid, "")
    assert get_message(mid)["owner_choice"] == ""


def test_choice_refuses_bad_values_unknown_and_sent_messages(ready_db):
    data, _ = seeded()
    mid = email_id(data["aria"])
    with pytest.raises(message_review.MessageReviewError) as bad:
        message_review.set_choice(mid, "maybe")
    assert bad.value.code == "invalid_value"
    with pytest.raises(message_review.MessageReviewError) as missing:
        message_review.set_choice(999999, "approved")
    assert missing.value.code == "not_found"
    with db.session() as conn:
        db.update(conn, "messages", mid, status="sent")
    with pytest.raises(message_review.MessageReviewError) as sent:
        message_review.set_choice(mid, "approved")
    assert sent.value.code == "already_sent"


def test_a_skipped_message_never_leaves(ready_db):
    data, pid = seeded()
    cid = data["campaign"]
    mid = email_id(data["aria"])
    assert mid in review.export_gate(cid, "export")["messages"]["exportable_ids"]
    message_review.set_choice(mid, "skipped")
    assert mid not in review.export_gate(cid, "export")["messages"]["exportable_ids"]
    assert mid not in review.export_gate(cid, "send")["messages"]["exportable_ids"]
    rows = export.build_rows(cid, "approved").rows
    assert all(not r["emailBody"] for r in rows)


def test_editing_saves_the_text_clears_the_choice_and_the_ai_review(ready_db):
    data, _ = seeded()
    mid = email_id(data["aria"])
    message_review.set_choice(mid, "approved")
    old = get_message(mid)
    body = old["body"].replace("Hi Aria", "Dear Aria")
    out = message_review.edit_message(mid, body, "A better subject line")
    row = get_message(mid)
    assert out["ok"] and row["body"].startswith("Dear Aria")
    assert row["subject"] == "A better subject line"
    assert row["owner_choice"] == "" and row["ai_review"] == ""
    assert out["message"]["status"] in ("qa_pass", "qa_fail")


def test_editing_a_sent_message_is_refused(ready_db):
    data, _ = seeded()
    mid = email_id(data["aria"])
    with db.session() as conn:
        db.update(conn, "messages", mid, status="sent")
    with pytest.raises(message_review.MessageReviewError) as err:
        message_review.edit_message(mid, "Hello again")
    assert err.value.code == "already_sent"


def test_message_routes(client):
    data, _ = seeded()
    mid = email_id(data["aria"])
    ok = client.post(f"/api/messages/{mid}/choice", json={"choice": "approved"}, headers=CSRF)
    assert ok.status_code == 200 and ok.json()["message"]["owner_choice"] == "approved"
    assert client.post(
        f"/api/messages/{mid}/choice", json={"choice": "nope"}, headers=CSRF
    ).status_code == 422
    assert client.post(
        "/api/messages/999999/choice", json={"choice": "skipped"}, headers=CSRF
    ).status_code == 404
    body = get_message(mid)["body"] + "\n\nP.S. See you soon."
    edited = client.patch(f"/api/messages/{mid}", json={"body": body}, headers=CSRF)
    assert edited.status_code == 200 and edited.json()["message"]["body"].endswith("See you soon.")
    assert client.patch(f"/api/messages/{mid}", json={"body": "x"}).status_code == 403  # no header
    assert client.patch(
        "/api/messages/999999", json={"body": "x"}, headers=CSRF
    ).status_code == 404


# --- waves ------------------------------------------------------------------------------------


def second_wave(pid: int, name: str = "Wave 2") -> int:
    first = projects.get_project(pid)
    cid = campaign_svc.create_campaign(
        name=name, offer_id=first["offer_id"], project_id=pid, countries=["SG"],
        sectors=["logistics"], channels=["email"], designations=["ld"],
    )["id"]
    return cid


def test_waves_list_counts_and_plain_status(ready_db):
    data, pid = seeded()
    empty = second_wave(pid)
    waves = browse.project_waves(pid)["waves"]
    assert [w["id"] for w in waves] == [empty, data["campaign"]]  # newest first
    newest, first = waves
    assert newest["name"] == "Wave 2" and newest["status"] == "Planning"
    assert newest["people"] == 0 and newest["messages"] == 0 and newest["sent"] == 0
    assert first["status"] == "Ready" and first["people"] == 3
    assert first["companies"] == 2 and first["messages"] == 4 and first["verified_emails"] == 1
    assert first["started"] and first["style"].startswith("Match each person")


@pytest.mark.parametrize(
    ("counts", "ready", "word"),
    [
        ({"companies": {"approved": 0}, "people": {}, "messages": {"sent": 0}}, 0, "Planning"),
        ({"companies": {"approved": 2}, "people": {}, "messages": {"sent": 0}}, 0,
         "Finding people"),
        ({"companies": {"approved": 2}, "people": {"review": 1}, "messages": {"sent": 0}}, 0,
         "Finding people"),
        ({"companies": {"approved": 2}, "people": {"confirmed": 3}, "messages": {"sent": 0}}, 3,
         "Checking emails"),
        ({"companies": {"approved": 2}, "people": {"confirmed": 3},
          "messages": {"draft": 2, "sent": 0}}, 3, "Ready"),
        ({"companies": {"approved": 2}, "people": {"confirmed": 3}, "messages": {"sent": 2}}, 3,
         "Sent"),
    ],
)
def test_wave_status_words(counts, ready, word):
    assert browse._wave_status(counts, ready) == word


def test_waves_route(client):
    data, pid = seeded()
    reply = client.get(f"/api/projects/{pid}/waves").json()
    assert reply["ok"] and reply["waves"][0]["id"] == data["campaign"]
    assert client.get("/api/projects/999999/waves").status_code == 404


def test_project_cards_say_whether_the_sign_off_is_set(ready_db):
    _, pid = seeded()
    card = next(c for c in browse.project_cards() if c["id"] == pid)
    assert card["sign_off_set"] is False
    projects.update_project(
        pid, sender_name="Priya Nair", sender_title="Founder", sender_company="Bright Harbour"
    )
    card = next(c for c in browse.project_cards() if c["id"] == pid)
    assert card["sign_off_set"] is True


# --- export all waves -------------------------------------------------------------------------


def wave_with_aria(pid: int) -> int:
    """A second wave that finds the same person (same email) plus one new person."""
    cid = second_wave(pid, "Wave 2")
    company = add_company(cid, "Northwind Logistics", "northwind.example")
    # a repeat is normally held back (L25); a hand-accepted one stays in, which is the case to cover
    again = add_person(cid, company, "Aria", "Tan", reasons_json=["MANUAL: wanted in both"])
    add_email(again, "verified", "aria.tan@northwind.example")
    add_message(again, "email", ai_review="pass")
    new = add_person(cid, company, "Eli", "Moss")
    add_email(new, "verified", "eli.moss@northwind.example")
    add_message(new, "email", ai_review="pass")
    review.run_qa(cid)
    return cid


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_export_all_waves_lists_a_person_once_with_every_wave(ready_db):
    data, pid = seeded()
    wave_with_aria(pid)
    name = campaign_svc.get_campaign(data["campaign"])["name"]
    out = export.export_project(pid, "generic", "all_confirmed")
    assert out["ok"] and out["waves"] == 2 and out["path"]
    rows = read_csv(out["path"])
    assert out["exported"] == len(rows)
    by_email = {r["email"]: r for r in rows if r["email"]}
    assert by_email["aria.tan@northwind.example"]["Wave"] == f"{name}; Wave 2"
    assert by_email["eli.moss@northwind.example"]["Wave"] == "Wave 2"
    assert [r["email"] for r in rows].count("aria.tan@northwind.example") == 1
    assert out["path"].endswith(".csv") and "all-waves" in out["path"]


def test_export_all_waves_instantly_and_empty(ready_db):
    data, pid = seeded()
    wave_with_aria(pid)
    rows = read_csv(export.export_project(pid, "instantly", "all_confirmed")["path"])
    assert rows and all(r["email"] and r["Wave"] for r in rows)
    empty = projects.create_project("Nothing yet")["id"]
    out = export.export_project(empty, "generic", "approved")
    assert out["ok"] and out["path"] is None and out["exported"] == 0 and "message" in out


def test_export_all_waves_remembers_the_people(ready_db):
    _, pid = seeded()
    export.export_project(pid, "generic", "all_confirmed")
    with db.session() as conn:
        n = db.query(conn, "SELECT COUNT(*) AS n FROM contact_log WHERE project_id = ?", (pid,))
    assert n[0]["n"] > 0


def test_export_all_waves_route_and_bad_values(client):
    _, pid = seeded()
    ok = client.post(
        f"/api/projects/{pid}/exports", json={"format": "lemlist", "include": "all_confirmed"},
        headers=CSRF,
    )
    assert ok.status_code == 200 and ok.json()["exported"] > 0
    bad = client.post(f"/api/projects/{pid}/exports", json={"format": "pdf"}, headers=CSRF)
    assert bad.status_code == 422
    assert client.post("/api/projects/999999/exports", json={}, headers=CSRF).status_code == 404


def test_project_folder_opens(client, monkeypatch):
    _, pid = seeded()
    seen = []
    monkeypatch.setattr(browse, "_platform", lambda: "win32")
    monkeypatch.setattr(browse, "_startfile", lambda folder: seen.append(folder))
    reply = client.post(f"/api/projects/{pid}/exports/open-folder", headers=CSRF).json()
    assert reply["ok"] and seen and seen[0].name.endswith("-all-waves")
