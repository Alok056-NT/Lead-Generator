"""The Studio API (V12 C4): the stepper, the results table, edit and delete of a project, and the
send and lemlist routes (the AI cannot call those). Everything is invented; no network."""

from __future__ import annotations

import httpx
import pytest
import respx
from dashboard_world import seed
from fastapi.testclient import TestClient
from qa_world import make_campaign

from leadgen import db
from leadgen.core import secrets
from leadgen.dashboard import app as dash
from leadgen.services import ai_setup, browse, chat, projects
from leadgen.services import campaign as campaign_svc
from leadgen.services import status as status_svc

BASE = "http://127.0.0.1:8765"
CSRF = {"X-LeadGen": "1"}
STEP_KEYS = [key for key, _ in status_svc.STUDIO_STEPS]
LEMLIST_KEY = "lem-key-1234567890"


@pytest.fixture
def client(ready_db, monkeypatch):
    monkeypatch.setattr(ai_setup, "get_status", lambda refresh=False: {"ready": False, "mode": ""})
    return TestClient(dash.create_app(), base_url=BASE)


def seeded_project() -> tuple[dict, int]:
    data = seed()
    return data, campaign_svc.get_campaign(data["campaign"])["project_id"]


def test_progress_for_an_empty_project(ready_db):
    pid = projects.create_project("Empty project")["id"]
    progress = status_svc.project_progress(pid)
    assert progress["campaign"] is None and progress["current"] == "product"
    assert [step["key"] for step in progress["steps"]] == STEP_KEYS
    assert [step["state"] for step in progress["steps"]] == ["active"] + ["todo"] * 7
    assert progress["summary"] == {
        "ready_contacts": 0, "verified_emails": 0, "messages": 0, "approved": 0,
    }  # fmt: skip


def test_progress_follows_the_data(ready_db):
    data, pid = seeded_project()
    cid = data["campaign"]
    progress = status_svc.project_progress(pid)
    next_step = next(c for c in status_svc.get_status()["campaigns"] if c["id"] == cid)
    assert progress["current"] == status_svc._STEP_OF[next_step["next_step"]["step"]]
    current = STEP_KEYS.index(progress["current"])
    states = [step["state"] for step in progress["steps"]]
    assert states[:current] == ["done"] * current
    assert states[current] == "active"
    assert states[current + 1 :] == ["todo"] * (len(STEP_KEYS) - current - 1)
    assert progress["campaign"] == {"id": cid, "name": campaign_svc.get_campaign(cid)["name"]}
    emails = progress["steps"][STEP_KEYS.index("emails")]
    assert emails["state"] != "skipped"
    assert progress["steps"][STEP_KEYS.index("people")]["detail"] == "3 ready"
    assert progress["summary"]["ready_contacts"] == 3


def test_a_linkedin_only_campaign_skips_the_emails_step(ready_db):
    camp = make_campaign(channels=("linkedin",))
    progress = status_svc.project_progress(camp["project_id"])
    emails = progress["steps"][STEP_KEYS.index("emails")]
    assert emails["state"] == "skipped" and emails["detail"] == "Not using email"


def test_progress_for_a_project_with_an_offer_but_no_profile(ready_db):
    with db.session() as conn:
        offer_id = db.insert(conn, "offers", name="Northwind Training", source_type="text")
    pid = projects.create_project("Training project", offer_id=offer_id)["id"]
    progress = status_svc.project_progress(pid)
    assert progress["current"] == "product"
    assert progress["steps"][0]["detail"] == "Northwind Training"


def test_unknown_project_for_progress_is_not_found(ready_db):
    with pytest.raises(projects.ProjectError) as err:
        status_svc.project_progress(999999)
    assert err.value.code == "not_found"


def test_results_show_only_ready_people_and_verified_emails(ready_db):
    data, pid = seeded_project()
    cid = data["campaign"]
    reply = browse.studio_results(pid)
    by_name = {lead["name"]: lead for lead in reply["leads"]}
    assert set(by_name) == {"Aria Tan", "Ben Carter", "Dev Rao"}
    aria = by_name["Aria Tan"]
    assert aria["email"] == "aria.tan@northwind.example" and aria["checked"] is True
    assert aria["linkedin_message"] and aria["email_body"]
    assert by_name["Ben Carter"]["email"] == "" and by_name["Ben Carter"]["checked"] is False
    assert by_name["Dev Rao"]["email"] == ""
    assert reply["total"] == 3 and reply["campaign_id"] == cid
    assert reply["campaigns"] == [{"id": cid, "name": campaign_svc.get_campaign(cid)["name"]}]
    page = browse.studio_results(pid, page_size=1)
    assert len(page["leads"]) == 1 and page["has_more"] is True and page["total"] == 3


def test_results_refuse_a_search_of_another_project(ready_db):
    data, pid = seeded_project()
    other = make_campaign()
    with pytest.raises(browse.BrowseError) as err:
        browse.studio_results(pid, other["id"])
    assert err.value.code == "not_found"


def test_results_for_a_project_without_searches_are_empty(ready_db):
    pid = projects.create_project("Quiet project")["id"]
    reply = browse.studio_results(pid)
    assert reply["campaigns"] == [] and reply["campaign_id"] is None
    assert reply["leads"] == [] and reply["total"] == 0


def test_patch_sender_name_shows_on_the_cards(client):
    pid = projects.create_project("Spring course")["id"]
    reply = client.patch(f"/api/projects/{pid}", json={"sender_name": "Priya"}, headers=CSRF)
    assert reply.status_code == 200 and reply.json()["project"]["sender_name"] == "Priya"
    cards = client.get("/api/projects").json()["projects"]
    assert [c["sender_name"] for c in cards if c["id"] == pid] == ["Priya"]
    too_long = client.patch(f"/api/projects/{pid}", json={"sender_name": "x" * 129}, headers=CSRF)
    assert too_long.status_code == 422


def test_delete_needs_the_header_and_the_exact_name(client):
    data, pid = seeded_project()
    name = projects.get_project(pid)["name"]
    other = projects.create_project("Another project")["id"]
    url = f"/api/projects/{pid}"
    assert client.request("DELETE", url, json={"confirm_name": name}).status_code == 403
    wrong = client.request("DELETE", url, json={"confirm_name": "nope"}, headers=CSRF)
    assert wrong.status_code == 400 and wrong.json()["error"]["code"] == "name_mismatch"
    done = client.request("DELETE", url, json={"confirm_name": name}, headers=CSRF)
    assert done.status_code == 200
    reply = done.json()
    assert reply["ok"] is True and reply["deleted"] is True and reply["project_id"] == pid
    assert reply["campaigns"] == 1 and reply["people"] == 4
    assert client.get(url).status_code == 404
    assert client.get(f"/api/projects/{other}").status_code == 200


def test_studio_and_results_routes(client):
    _, pid = seeded_project()
    studio = client.get(f"/api/projects/{pid}/studio").json()
    assert studio["ok"] is True and studio["project"]["id"] == pid
    assert "sender_name" in studio["project"] and "cooldown_days" in studio["project"]
    assert [step["key"] for step in studio["steps"]] == STEP_KEYS
    assert studio["ai"] == {"ready": False, "mode": ""}
    assert studio["turn"] == {"running": False, "turn_id": None}
    results = client.get(f"/api/projects/{pid}/results?page_size=1").json()
    assert results["ok"] is True and results["total"] == 3 and len(results["leads"]) == 1
    assert client.get(f"/api/projects/{pid}/results?campaign_id=999999").status_code == 404
    assert client.get("/api/projects/999999/studio").status_code == 404
    assert client.get("/api/projects/999999/results").status_code == 404


def test_send_is_a_dry_run_until_confirmed(client):
    cid = seed()["campaign"]
    url = f"/api/campaigns/{cid}/send"
    body = client.post(url, json={"limit": 5}, headers=CSRF).json()
    assert body["ok"] is True and body["dry_run"] is True
    assert isinstance(body["would_be_blocked_by"], list)
    real = client.post(url, json={"dry_run": False, "confirm": False}, headers=CSRF).json()
    assert real["needs_confirmation"] is True
    assert client.post(url, json={"limit": 0}, headers=CSRF).status_code == 422
    assert client.post(url, json={"limit": 51}, headers=CSRF).status_code == 422
    assert client.post(url, json={}).status_code == 403


def test_lemlist_campaigns_need_a_key(client):
    reply = client.get("/api/lemlist/campaigns")
    assert reply.status_code == 400 and reply.json()["error"]["code"] == "needs_key"


@respx.mock
def test_lemlist_push_is_a_preview_until_confirmed(client):
    secrets.set("LEMLIST_API_KEY", LEMLIST_KEY)
    cid = seed()["campaign"]
    route = respx.post(url__regex=r".*").mock(return_value=httpx.Response(200, json={}))
    url = f"/api/campaigns/{cid}/lemlist"
    preview = client.post(url, json={"lemlist_campaign_id": "cam_test123"}, headers=CSRF)
    assert preview.json()["ok"] is True and preview.json()["dry_run"] is True
    assert "to_push" in preview.json()
    real = client.post(
        url, json={"lemlist_campaign_id": "cam_test123", "dry_run": False}, headers=CSRF
    )
    assert real.json()["needs_confirmation"] is True
    assert not route.called


def test_the_ai_cannot_send():
    assert {"send_emails", "push_to_lemlist"} <= chat.WITHHELD_TOOLS
    assert "send_emails" not in chat.STUDIO_TOOLS and "push_to_lemlist" not in chat.STUDIO_TOOLS
    granted = chat.allowed_tools_arg().split(",")
    assert chat.TOOL_PREFIX + "send_emails" not in granted
    assert chat.TOOL_PREFIX + "push_to_lemlist" not in granted
