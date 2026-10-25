"""Message templates (V13 C2): the nine built-in ones, a search's choice, the project's own
templates, the drafting batch and the routes. Everything is invented; no network."""

from __future__ import annotations

import pytest
from dashboard_world import seed
from fastapi.testclient import TestClient
from qa_world import PROFILE, add_company, add_email, add_person, make_campaign

from leadgen.dashboard import app as dash
from leadgen.services import ai_setup, drafting, projects, qa_messages
from leadgen.services import campaign as campaign_svc
from leadgen.services import templates as tpl
from leadgen.services.campaign import CampaignError

BASE = "http://127.0.0.1:8765"
CSRF = {"X-LeadGen": "1"}
FILL = {
    "first_name": "Aria", "company": "Northwind Logistics", "their_title": "Head of Learning",
    "topic": "manager training",
    "evidence_line": "I see you lead learning and development for the warehouse and driver teams "
                     "at Northwind Logistics.",
    "offer_line": "We run short, practical programmes that help first-time managers lead their "
                  "teams with confidence.",
    "outcome_line": "Courses are taught by working trainers.",
    "proof_line": "Sessions are delivered by certified trainers.",
    "sender_line": "I work with training teams in logistics firms.",
    "cta": "Would a short outline be useful?",
    "sign_off": "Priya Nair\nHead of Partnerships\nBright Harbour Training",
}  # fmt: skip
SENDER = {
    "name": "Priya Nair",
    "title": "Head of Partnerships",
    "company": "Bright Harbour Training",
}
PROFILE_WITH_CLAIMS = {
    **PROFILE,
    "confirmed_claims": [
        *PROFILE["confirmed_claims"],
        "Courses are taught by working trainers",
        "Sessions are delivered by certified trainers",
    ],
}


def make_search(options=None, channels=("email", "linkedin")):
    """A campaign with one company, one confirmed person and a verified email."""
    camp = make_campaign(channels=channels, options=options)
    cid = camp["id"]
    person = add_person(cid, add_company(cid), "Aria", "Tan", seniority="vp")
    add_email(person, "verified", "aria.tan@northwind.example")
    return cid, person


@pytest.fixture
def world(ready_db):
    return make_search()


# --- built-in templates -----------------------------------------------------------------------


def test_builtin_has_nine_complete_templates():
    assert len(tpl.BUILTIN) == 9
    for key, item in tpl.BUILTIN.items():
        assert key == f"{item['level']}-{item['depth']}"
        assert set(item) == {"name", "level", "depth", "use_case", "notes", "subject", "body"}
        for text in (item["subject"], item["body"]):
            assert set(tpl._PH_RE.findall(text)) <= set(tpl.PLACEHOLDERS), key
        assert item["body"].startswith("Hi {first_name},")
        assert item["body"].endswith("Kind regards,\n{sign_off}")


@pytest.mark.parametrize("key", list(tpl.BUILTIN))
def test_builtin_passes_the_message_checks(key):
    item = tpl.BUILTIN[key]
    subject, body = item["subject"].format_map(FILL), item["body"].format_map(FILL)
    assert len(subject) <= 60
    message = {"channel": "email", "subject": subject, "body": body}
    person = {"greeting_name": "Aria", "first_name": "Aria", "last_name": "Test",
              "title": "Head of Learning"}  # fmt: skip
    issues = qa_messages.check_message(
        message, person, {"name": "Northwind Logistics"},
        {"locale": "en-GB", "sectors": ["logistics"]}, PROFILE_WITH_CLAIMS,
        companies=[], sender=SENDER,
    )  # fmt: skip
    assert [i.code for i in issues if i.severity == "block"] == [], key


# --- the project's own templates --------------------------------------------------------------

GOOD = {"name": "My note", "level": "manager", "depth": "short", "use_case": "Quick note",
        "subject": "{topic} for you", "body": "Hi {first_name},\n\n{cta}"}  # fmt: skip


def test_save_inserts_and_updates(world):
    pid = campaign_svc.get_campaign(world[0])["project_id"]
    made = tpl.save_template(pid, GOOD)["template"]
    assert made["key"] == f"custom-{made['id']}" and made["custom"] is True
    assert made["body"].endswith("\n\nKind regards,\n{sign_off}")  # the closing was added
    changed = tpl.save_template(pid, {**GOOD, "name": "Renamed"}, made["id"])["template"]
    assert changed["name"] == "Renamed" and changed["id"] == made["id"]


def test_unknown_placeholder_is_refused(world):
    pid = campaign_svc.get_campaign(world[0])["project_id"]
    with pytest.raises(tpl.TemplateError) as err:
        tpl.save_template(pid, {**GOOD, "body": "Hi {first_name}, {price} {sign_off}"})
    assert err.value.code == "unknown_placeholder" and "{price}" in err.value.message


def test_too_many_custom_templates_are_refused(world):
    pid = campaign_svc.get_campaign(world[0])["project_id"]
    for n in range(tpl.MAX_CUSTOM):
        tpl.save_template(pid, {**GOOD, "name": f"T{n}"})
    with pytest.raises(tpl.TemplateError) as err:
        tpl.save_template(pid, {**GOOD, "name": "One too many"})
    assert err.value.code == "too_many"


def test_another_projects_template_is_not_found(world):
    pid = campaign_svc.get_campaign(world[0])["project_id"]
    other = projects.create_project("Other project")["id"]
    mine = tpl.save_template(pid, GOOD)["template"]
    with pytest.raises(tpl.TemplateError) as err:
        tpl.save_template(other, GOOD, mine["id"])
    assert err.value.code == "not_found"
    with pytest.raises(tpl.TemplateError):
        tpl.delete_template(other, mine["id"])


def test_delete_resets_the_searches_that_used_it(world):
    cid, _ = world
    pid = campaign_svc.get_campaign(cid)["project_id"]
    made = tpl.save_template(pid, GOOD)["template"]
    tpl.set_campaign_template(cid, made["key"])
    result = tpl.delete_template(pid, made["id"])
    assert result == {"ok": True, "deleted": True, "searches_reset": 1}
    assert campaign_svc.get_campaign(cid)["options"]["template_key"] == "auto-standard"


def test_choices_and_listing(world):
    pid = campaign_svc.get_campaign(world[0])["project_id"]
    made = tpl.save_template(pid, GOOD)["template"]
    listed = tpl.list_templates(pid)
    assert [c["key"] for c in listed["choices"][:4]] == [
        "auto-standard", "auto-short", "auto-detailed", "csuite-short",
    ]  # fmt: skip
    assert listed["choices"][-1] == {"key": made["key"], "label": "Yours: My note"}
    assert len(listed["builtin"]) == 9 and listed["custom"][0]["id"] == made["id"]
    assert listed["builtin"][0]["custom"] is False


# --- resolve and the search's choice -----------------------------------------------------------


@pytest.mark.parametrize(
    "key, seniority, expected",
    [
        ("auto-short", "c_suite", "csuite-short"),
        ("auto-standard", "vp", "director-standard"),
        ("auto-detailed", "director", "director-detailed"),
        ("auto-short", "individual", "manager-short"),
        ("auto-standard", "", "manager-standard"),
        ("manager-detailed", "c_suite", "manager-detailed"),
    ],
)
def test_resolve_picks_the_level(key, seniority, expected):
    assert tpl.resolve(None, key, seniority)[0] == expected


def test_resolve_custom_and_deleted_keys(world):
    pid = campaign_svc.get_campaign(world[0])["project_id"]
    made = tpl.save_template(pid, GOOD)["template"]
    key, item = tpl.resolve(pid, made["key"], "vp")
    assert key == made["key"] and item["name"] == "My note"
    tpl.delete_template(pid, made["id"])
    assert tpl.resolve(pid, made["key"], "unknown-seniority")[0] == "manager-standard"


def test_set_campaign_template_checks_the_key(world):
    cid, _ = world
    pid = campaign_svc.get_campaign(cid)["project_id"]
    other = projects.create_project("Other project")["id"]
    foreign = tpl.save_template(other, GOOD)["template"]
    assert tpl.set_campaign_template(cid, "csuite-short")["template_key"] == "csuite-short"
    assert campaign_svc.get_campaign(cid)["options"]["template_key"] == "csuite-short"
    for bad in ("banana", foreign["key"]):
        with pytest.raises(tpl.TemplateError) as err:
            tpl.set_campaign_template(cid, bad)
        assert err.value.code == "unknown_template"
    with pytest.raises(CampaignError) as err:
        campaign_svc.update_campaign(cid, template_key="banana")
    assert err.value.code == "invalid_value"
    assert tpl.get_template(pid, "custom-999999") is None


# --- drafting batch ---------------------------------------------------------------------------


def test_email_batch_carries_the_template(world):
    cid, person = world
    batch = drafting.get_drafting_batch(cid, "email")
    assert batch["people"][0]["template"] == "director-standard"  # seniority vp
    assert "director-standard" in batch["templates"]
    assert batch["placeholders"] == tpl.PLACEHOLDERS


def test_invite_batch_has_no_template(ready_db):
    cid, _ = make_search(options={"invite_note": True}, channels=("linkedin",))
    batch = drafting.get_drafting_batch(cid, "linkedin_invite")
    assert "template" not in batch["people"][0]
    assert "templates" not in batch and "placeholders" not in batch


# --- routes -----------------------------------------------------------------------------------


@pytest.fixture
def client(ready_db, monkeypatch):
    monkeypatch.setattr(ai_setup, "get_status", lambda refresh=False: {"ready": False, "mode": ""})
    return TestClient(dash.create_app(), base_url=BASE)


def test_template_routes(client):
    data = seed()
    pid = campaign_svc.get_campaign(data["campaign"])["project_id"]
    url = f"/api/projects/{pid}/templates"
    listed = client.get(url).json()
    assert listed["ok"] and len(listed["builtin"]) == 9
    made = client.post(url, json=GOOD, headers=CSRF).json()["template"]
    changed = client.put(f"{url}/{made['id']}", json={**GOOD, "name": "Renamed"}, headers=CSRF)
    assert changed.json()["template"]["name"] == "Renamed"
    chosen = client.post(
        f"/api/campaigns/{data['campaign']}/template",
        json={"template_key": made["key"]}, headers=CSRF,
    )  # fmt: skip
    assert chosen.json()["template_key"] == made["key"]
    rows = client.get("/api/campaigns").json()["campaigns"]
    assert [r["template_key"] for r in rows if r["id"] == data["campaign"]] == [made["key"]]
    assert client.delete(f"{url}/{made['id']}", headers=CSRF).json()["searches_reset"] == 1
    banned = client.post(
        f"/api/campaigns/{data['campaign']}/template",
        json={"template_key": "banana"}, headers=CSRF,
    )  # fmt: skip
    assert banned.status_code == 400
    refused = client.post(
        f"/api/campaigns/{data['campaign']}/template",
        json={"allow_guess_emails": True}, headers=CSRF,
    )  # fmt: skip
    assert refused.status_code == 422
