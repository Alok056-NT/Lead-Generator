"""The dashboard's projects home, project page, new campaign, reserve moves and file uploads (T26).

Everything is invented (example.com style); no network. Uploads go through FastAPI's TestClient.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient
from qa_world import add_company, add_person, make_campaign
from test_projects import HEAD, HRDIR, MANAGER, find, hit
from test_projects import add_company as add_company_to
from test_projects import make_campaign as make_project_campaign

from leadgen import db
from leadgen.dashboard import app as dash
from leadgen.services import browse, contacts_import, people, projects
from leadgen.services import campaign as camp_svc

BASE = "http://127.0.0.1:8765"
CSRF = {"X-LeadGen": "1"}
STATIC = Path(dash.STATIC_DIR)


@pytest.fixture
def client(ready_db):
    return TestClient(dash.create_app(), base_url=BASE)


@pytest.fixture
def project(ready_db):
    with db.session() as conn:
        offer_id = db.insert(conn, "offers", name="Northwind Training", source_type="text")
    return projects.create_project("Northwind Training", offer_id=offer_id)


def post(client, path, body=None, **kw):
    return client.post(path, json=body, headers=CSRF, **kw)


def upload(client, path, name, data, fields=None, headers=CSRF):
    files = {"file": (name, io.BytesIO(data), "application/octet-stream")}
    return client.post(path, files=files, data=fields or {}, headers=headers)


def campaign_body(project_id, **extra):
    body = {
        "name": "Singapore logistics", "project_id": project_id, "countries": ["SG"],
        "sectors": ["logistics"], "channels": ["email", "linkedin"], "designations": ["ld", "hr"],
        "seniority_min": "manager", "max_people": None, "max_companies": None,
        "max_per_company": None, "take_reserve_from": None,
    }  # fmt: skip
    return {**body, **extra}


# --- projects: home cards, create, page, change -------------------------------------------------


def test_the_home_cards_start_empty_and_grow(client):
    assert client.get("/api/projects").json() == {"ok": True, "projects": []}
    made = post(client, "/api/projects", {"name": "Harbour Freight site"}).json()
    assert made["ok"] and made["project"]["cooldown_days"] == 90
    assert made["project"]["email_policy"] == "verified_only"
    card = client.get("/api/projects").json()["projects"][0]
    assert card["name"] == "Harbour Freight site" and card["about"] == ""
    assert card["campaigns"] == 0 and card["leads"] == 0 and card["verified_emails"] == 0
    assert card["has_source"] is False and card["last_activity"]


def test_a_card_shows_the_saved_one_liner_people_and_verified_emails(client):
    seeded = make_campaign()  # a profile with a one-liner, one project
    cid = seeded["id"]
    company = add_company(cid)
    ready = add_person(cid, company)
    add_person(cid, company, "Ben", "Carter", status="review")
    with db.session() as conn:
        db.insert(conn, "emails", person_id=ready, email="aria@northwind.example",
                  status="verified")  # fmt: skip
    card = client.get("/api/projects").json()["projects"][0]
    assert card["about"] and card["campaigns"] == 1
    assert card["leads"] == 1 and card["verified_emails"] == 1 and card["has_source"] is True


def test_create_project_checks_the_values_and_names_stay_unique(client):
    assert post(client, "/api/projects", {"name": "A", "cooldown_days": -1}).status_code == 422
    assert post(client, "/api/projects", {"name": "A", "email_policy": "guess"}).status_code == 422
    assert post(client, "/api/projects", {"name": "A", "surprise": 1}).status_code == 422
    assert post(client, "/api/projects", {"name": " "}).status_code == 400
    assert post(client, "/api/projects", {"name": "A"}).status_code == 200
    again = post(client, "/api/projects", {"name": "a"})
    assert again.status_code == 400 and again.json()["ok"] is False


def test_the_project_page_carries_everything_it_shows(client, project):
    page = client.get(f"/api/projects/{project['id']}").json()
    assert page["ok"] and page["project"]["name"] == "Northwind Training"
    assert page["source"]["kind"] == "text" and page["source"]["has_profile"] is False
    assert page["campaigns"] == [] and page["known"] == {"total": 0, "by_relationship": {}}
    assert page["memory"] == {
        "cooldown_days": 90, "blocked": 0, "first_free": "", "last_free": "",
    }  # fmt: skip
    assert client.get("/api/projects/999").status_code == 404


def test_the_project_page_has_no_source_before_one_is_attached(client):
    made = post(client, "/api/projects", {"name": "Empty"}).json()["project"]
    assert client.get(f"/api/projects/{made['id']}").json()["source"] is None


def test_changing_the_cooldown_and_the_email_choice(client, project):
    url = f"/api/projects/{project['id']}"
    out = client.patch(url, json={"cooldown_days": 30, "email_policy": "allow_risky"}, headers=CSRF)
    assert out.status_code == 200
    assert out.json()["project"]["cooldown_days"] == 30
    assert out.json()["project"]["email_policy"] == "allow_risky"
    bad = client.patch(url, json={"email_policy": "guess"}, headers=CSRF)
    assert bad.status_code == 422 and "guess" not in bad.text  # the value is never echoed
    assert client.patch(url, json={"cooldown_days": 99999}, headers=CSRF).status_code == 422
    assert client.patch(url, json={"status": "archived"}, headers=CSRF).status_code == 422


def test_the_memory_says_how_many_are_held_back_and_until_when(client, project):
    cid = make_project_campaign(project["id"], "One")
    add_company_to(cid, "Alpha Co", domain="alpha.example")
    find(cid, {"Alpha Co": [hit("Omar Haddad", HEAD, "Alpha Co")]})
    memory = client.get(f"/api/projects/{project['id']}").json()["memory"]
    assert memory["blocked"] == 1 and len(memory["first_free"]) == 10
    assert memory["first_free"] <= memory["last_free"]
    projects.update_project(project["id"], cooldown_days=0)
    off = projects.contact_memory(project["id"])
    assert off == {"cooldown_days": 0, "blocked": 0, "first_free": "", "last_free": ""}


# --- the product or service: pasted text, files ------------------------------------------------


def test_pasted_text_becomes_the_projects_source(client, project):
    body = {"text": "Northwind runs safety training for warehouse teams."}
    out = post(client, f"/api/projects/{project['id']}/source", body).json()
    assert out["ok"] and out["done"] is True and out["project_id"] == project["id"]
    assert "text" not in out and out["summary"]
    assert projects.get_project(project["id"])["offer_id"] == out["offer_id"]


def test_an_uploaded_text_file_replaces_the_source(client, project):
    data = b"Northwind runs safety training.\n\nWe also sell forklift courses."
    out = upload(client, f"/api/projects/{project['id']}/source-file", "about us.txt", data)
    assert out.status_code == 200, out.text
    reply = out.json()
    assert reply["ok"] and reply["summary"] == "Read the file about us.txt"
    assert "text" not in reply and reply["done"] is True
    page = client.get(f"/api/projects/{project['id']}").json()
    assert page["source"]["kind"] == "file" and page["source"]["label"] == "about us.txt"


def test_an_uploaded_file_is_deleted_after_reading(client, project, tmp_path, monkeypatch):
    seen = []
    real = dash.tempfile.TemporaryDirectory

    def spy(*args, **kwargs):
        folder = real(*args, **kwargs)
        seen.append(Path(folder.name))
        return folder

    monkeypatch.setattr(dash.tempfile, "TemporaryDirectory", spy)
    upload(client, f"/api/projects/{project['id']}/source-file", "x.txt", b"Some text here.")
    assert seen and not seen[0].exists()


@pytest.mark.parametrize("name", ["notes.exe", "no_extension", "..", ""])
def test_upload_refuses_other_kinds_of_files(client, project, name):
    out = upload(client, f"/api/projects/{project['id']}/source-file", name or "x", b"abc")
    assert out.status_code in (400, 422) and out.json()["ok"] is False
    if name:
        assert "abc" not in out.text


def test_upload_uses_only_the_base_name_and_survives_odd_names(client, project):
    url = f"/api/projects/{project['id']}/source-file"
    out = upload(client, url, "..\\..\\evil/CON.txt", b"Northwind safety training text.")
    assert out.status_code == 200 and out.json()["summary"] == "Read the file file-CON.txt"


def test_a_file_over_the_limit_is_refused(client, project, monkeypatch):
    monkeypatch.setattr(dash, "UPLOAD_LIMIT", 100)
    out = upload(client, f"/api/projects/{project['id']}/source-file", "big.txt", b"x" * 500)
    assert out.status_code == 413 and out.json()["error"]["code"] == "file_too_big"
    assert projects.get_project(project["id"])["offer_id"] is not None  # untouched


def test_a_big_file_that_is_allowed_gets_through_the_parser(client, project):
    big = b"Northwind safety training for warehouse teams. " * 100_000  # 4.6 MB
    out = upload(client, f"/api/projects/{project['id']}/source-file", "big.txt", big)
    assert out.status_code == 200 and out.json()["ok"]


def test_an_unreadable_file_gives_a_plain_error(client, project):
    out = upload(client, f"/api/projects/{project['id']}/source-file", "empty.txt", b"   ")
    assert out.status_code == 400 and out.json()["ok"] is False
    fake_pdf = upload(client, f"/api/projects/{project['id']}/source-file", "a.pdf", b"not a pdf")
    assert fake_pdf.status_code == 400 and "not a pdf" not in fake_pdf.text


# --- contacts you already have -----------------------------------------------------------------


def test_the_templates_download(client):
    csv_reply = client.get("/api/contacts-template/csv")
    assert csv_reply.status_code == 200 and "attachment" in csv_reply.headers["content-disposition"]
    assert csv_reply.content.startswith(b"\xef\xbb\xbf") or b"email" in csv_reply.content
    xlsx_reply = client.get("/api/contacts-template/xlsx")
    assert xlsx_reply.status_code == 200
    book = openpyxl.load_workbook(io.BytesIO(xlsx_reply.content))
    assert "Contacts" in book.sheetnames
    assert client.get("/api/contacts-template/exe").status_code == 422


def contacts_csv(*people_rows) -> bytes:
    header = ",".join(contacts_import.COLUMNS)
    lines = [header]
    for values in people_rows:
        lines.append(",".join(values.get(c, "") for c in contacts_import.COLUMNS))
    return ("\n".join(lines) + "\n").encode("utf-8-sig")


NEW_CONTACTS = contacts_csv(
    {"first_name": "Aria", "last_name": "Tan", "email": "aria@northwind.example",
     "company": "Northwind Logistics", "last_contacted": "2026-09-30",
     "relationship": "contacted"},
    {"first_name": "Ben", "last_name": "Carter", "email": "ben@harbour.example",
     "company": "Harbour Freight", "last_contacted": "2026-09-01", "relationship": "customer"},
    {"first_name": "Cara", "last_name": "Ng", "email": "cara@optout.example",
     "company": "Opt Out Co", "relationship": "do_not_contact"},
)  # fmt: skip


def import_url(project_id):
    return f"/api/projects/{project_id}/contacts"


def test_a_dry_run_shows_the_counts_and_writes_nothing(client, project):
    out = upload(client, import_url(project["id"]), "old.csv", NEW_CONTACTS,
                 {"mode": "both", "dry_run": "true"}).json()  # fmt: skip
    assert out["ok"] and out["dry_run"] is True and out["contacts"] == 3
    assert out["would_exclude"] >= 2 and out["would_signal"] == 2 and out["do_not_contact"] == 1
    assert "next_step" not in out
    assert contacts_import.known_summary(project["id"])["total"] == 0
    assert projects.contact_memory(project["id"])["blocked"] == 0


def test_applying_writes_the_contacts_and_shows_on_the_project_page(client, project):
    fields = {"mode": "both", "dry_run": "false"}
    out = upload(client, import_url(project["id"]), "old.csv", NEW_CONTACTS, fields).json()
    assert out["ok"] and out["dry_run"] is False and out["signalled"] == 2
    page = client.get(f"/api/projects/{project['id']}").json()
    assert page["known"]["total"] == 2 and page["memory"]["blocked"] >= 1
    again = upload(client, import_url(project["id"]), "old.csv", NEW_CONTACTS, fields).json()
    assert again["ok"] and again["already_logged"] >= 2  # nothing is added twice


def test_the_import_mode_is_limited_to_three_choices(client, project):
    out = upload(client, import_url(project["id"]), "old.csv", NEW_CONTACTS,
                 {"mode": "delete-everything"})  # fmt: skip
    assert out.status_code == 422 and "delete-everything" not in out.text


def test_import_refuses_other_file_types_and_unreadable_files(client, project):
    assert upload(client, import_url(project["id"]), "list.pdf", b"%PDF").status_code == 400
    bad = upload(client, import_url(project["id"]), "list.csv", b"nothing useful here\n")
    assert bad.status_code == 400 and bad.json()["ok"] is False
    assert upload(client, import_url(999), "old.csv", NEW_CONTACTS).status_code == 404


def test_imported_people_get_the_known_contact_filter(client, project):
    cid = make_project_campaign(project["id"], "One")
    add_company_to(cid, "Alpha Co", domain="alpha.example")
    find(cid, {"Alpha Co": [hit("Omar Haddad", HEAD, "Alpha Co"),
                            hit("Mira Lee", MANAGER, "Alpha Co")]})  # fmt: skip
    rows = contacts_csv({"first_name": "Mira", "last_name": "Lee", "company": "Alpha Co",
                         "relationship": "replied"})  # fmt: skip
    upload(
        client, import_url(project["id"]), "old.csv", rows, {"mode": "signal", "dry_run": "false"}
    )
    everyone = client.get(f"/api/campaigns/{cid}/leads").json()
    assert everyone["total"] == 2
    only = client.get(f"/api/campaigns/{cid}/leads?known=true").json()
    assert only["total"] == 1 and only["leads"][0]["raw_name"] == "Mira Lee"
    assert only["leads"][0]["known_contact"]["relationship"] == "replied"
    assert (
        client.get(f"/api/campaigns/{cid}/leads?known=true&page_size=1&page=2").json()["leads"]
        == []
    )
    assert browse.list_leads(cid, known_only=True)["total"] == 1


# --- campaigns: options, create with n / c / p, reserve ----------------------------------------


def test_the_form_choices_are_plain_lists(client):
    opts = client.get("/api/campaign-options").json()
    assert opts["ok"] and {"code": "SG", "name": "Singapore"} in opts["countries"]
    assert {"key": "ld", "label": "L&D"} in opts["roles"] or any(
        r["key"] == "ld" for r in opts["roles"]
    )
    assert opts["seniority"] == ["individual", "manager", "director", "vp", "c_suite"]


def test_create_a_campaign_with_n_c_and_p(client, project):
    body = campaign_body(project["id"], max_people=10, max_companies=4, max_per_company=2)
    out = post(client, "/api/campaigns", body).json()
    assert out["ok"] and out["reserve"] is None
    made = camp_svc.get_campaign(out["campaign"]["id"])
    assert made["options"]["max_people"] == 10 and made["options"]["max_companies"] == 4
    assert made["options"]["max_per_company"] == 2
    assert made["channels"] == ["email", "linkedin"] and made["project_id"] == project["id"]
    assert made["options"]["allow_risky_emails"] is False  # the project's choice decides


def test_a_campaign_cannot_set_the_email_switches(client, project):
    for key in ("options", "allow_risky_emails", "allow_guess_emails"):
        out = post(client, "/api/campaigns", campaign_body(project["id"], **{key: {"x": 1}}))
        assert out.status_code == 422
    risky = projects.create_project(
        "Risky", email_policy="allow_risky", offer_id=project["offer_id"]
    )
    out = post(client, "/api/campaigns", campaign_body(risky["id"], name="R")).json()
    stored = camp_svc.get_campaign(out["campaign"]["id"])["options"]
    assert stored["allow_risky_emails"] is True and stored["allow_guess_emails"] is False


@pytest.mark.parametrize(
    ("change", "status"),
    [
        ({"max_per_company": 0}, 422), ({"max_people": 10_001}, 422),
        ({"channels": ["sms"]}, 422), ({"channels": []}, 400), ({"countries": []}, 400),
        ({"countries": ["Atlantis"]}, 400), ({"designations": []}, 400),
        ({"designations": ["nonsense"]}, 400), ({"seniority_min": "boss"}, 400),
        ({"name": ""}, 400), ({"project_id": 999}, 400), ({"project_id": 0}, 422),
    ],
)  # fmt: skip
def test_bad_campaign_forms_get_plain_errors(client, project, change, status):
    body = {**campaign_body(project["id"]), **change}
    out = post(client, "/api/campaigns", body)
    assert out.status_code == status and out.json()["ok"] is False


def test_two_campaigns_with_p_2_split_the_people_through_reserve(client, project):
    first = make_project_campaign(project["id"], "First", max_per_company=2)
    add_company_to(first, "Alpha Co", domain="alpha.example")
    mapping = {"Alpha Co": [hit("Omar Haddad", HEAD, "Alpha Co"),
                            hit("Mira Lee", MANAGER, "Alpha Co"),
                            hit("Hana Reyes", HRDIR, "Alpha Co"),
                            hit("Nia Park", MANAGER, "Alpha Co")]}  # fmt: skip
    find(first, mapping)
    counts = camp_svc.get_campaign(first)["counts"]["people"]
    assert counts["confirmed"] == 2 and counts["reserve"] == 2
    overview = client.get(f"/api/campaigns/{first}").json()
    assert overview["project"]["name"] == "Northwind Training" and overview["reserve_sources"] == []
    # a second campaign takes the saved people at once, with the same p = 2
    body = campaign_body(project["id"], name="Second", max_per_company=2, take_reserve_from=first)
    out = post(client, "/api/campaigns", body).json()
    assert out["ok"] and out["reserve"]["moved"] == 2 and out["reserve"]["left_in_reserve"] == 0
    second = out["campaign"]["id"]
    assert out["campaign"]["counts"]["people"]["confirmed"] == 2
    assert camp_svc.get_campaign(first)["counts"]["people"]["reserve"] == 0
    got = client.get(f"/api/campaigns/{second}/leads?status=confirmed").json()
    assert got["total"] == 2


def test_the_move_route_moves_reserve_and_the_page_lists_the_sources(client, project):
    first = make_project_campaign(project["id"], "First", max_per_company=1)
    add_company_to(first, "Alpha Co", domain="alpha.example")
    find(first, {"Alpha Co": [hit("Omar Haddad", HEAD, "Alpha Co"),
                              hit("Mira Lee", MANAGER, "Alpha Co")]})  # fmt: skip
    second = make_project_campaign(project["id"], "Second")
    sources = client.get(f"/api/campaigns/{second}").json()["reserve_sources"]
    assert sources == [{"id": first, "name": "First", "reserve": 1}]
    out = post(client, f"/api/campaigns/{second}/move-reserve", {"from_campaign_id": first}).json()
    assert out["ok"] and out["moved"] == 1 and out["left_in_reserve"] == 0
    assert client.get(f"/api/campaigns/{second}").json()["reserve_sources"] == []


def test_the_move_route_refuses_other_projects_and_bad_values(client, project):
    first = make_project_campaign(project["id"], "First")
    elsewhere = projects.create_project("Elsewhere", offer_id=project["offer_id"])
    other = make_project_campaign(elsewhere["id"], "Other")
    out = post(client, f"/api/campaigns/{other}/move-reserve", {"from_campaign_id": first})
    assert out.status_code == 400 and out.json()["error"]["code"] == "different_project"
    same = post(client, f"/api/campaigns/{first}/move-reserve", {"from_campaign_id": first})
    assert same.status_code == 400
    assert (
        post(client, f"/api/campaigns/{first}/move-reserve", {"from_campaign_id": 0}).status_code
        == 422
    )
    assert post(client, f"/api/campaigns/{first}/move-reserve", {}).status_code == 422


def test_taking_reserve_from_another_project_creates_nothing(client, project):
    elsewhere = projects.create_project("Elsewhere", offer_id=project["offer_id"])
    other = make_project_campaign(elsewhere["id"], "Other")
    body = campaign_body(project["id"], name="Never made", take_reserve_from=other)
    out = post(client, "/api/campaigns", body)
    assert out.status_code == 400 and out.json()["error"]["code"] == "different_project"
    assert camp_svc.list_campaigns(project_id=project["id"]) == []
    gone = post(client, "/api/campaigns", campaign_body(project["id"], take_reserve_from=999))
    assert (
        gone.status_code in (400, 404) and camp_svc.list_campaigns(project_id=project["id"]) == []
    )


def test_a_campaign_list_can_be_limited_to_one_project(client, project):
    mine = make_project_campaign(project["id"], "Mine")
    elsewhere = projects.create_project("Elsewhere", offer_id=project["offer_id"])
    make_project_campaign(elsewhere["id"], "Theirs")
    both = client.get("/api/campaigns").json()["campaigns"]
    assert {c["project"] for c in both} == {"Northwind Training", "Elsewhere"}
    only = client.get(f"/api/campaigns?project_id={project['id']}").json()["campaigns"]
    assert [c["id"] for c in only] == [mine]
    created = people.create_campaign_taking_reserve(
        None,
        **{
            k: v
            for k, v in campaign_body(project["id"], name="Direct").items()
            if k not in ("take_reserve_from", "max_people", "max_companies", "max_per_company")
        },  # fmt: skip
    )
    assert created["ok"] and created["reserve"] is None


# --- safety: the same rules for the new routes -------------------------------------------------

NEW_CHANGES = [
    ("POST", "/api/projects", {"name": "X"}),
    ("PATCH", "/api/projects/1", {"cooldown_days": 5}),
    ("POST", "/api/projects/1/source", {"text": "hello there"}),
    ("POST", "/api/campaigns", {}),
    ("POST", "/api/campaigns/1/move-reserve", {"from_campaign_id": 2}),
]


@pytest.mark.parametrize(("method", "path", "body"), NEW_CHANGES)
def test_new_changes_need_the_header_and_a_same_origin_request(client, method, path, body):
    assert client.request(method, path, json=body).status_code == 403
    foreign = client.request(
        method, path, json=body, headers={**CSRF, "Origin": "http://evil.example"}
    )
    assert foreign.status_code == 403
    cross = client.request(
        method, path, json=body, headers={**CSRF, "Sec-Fetch-Site": "cross-site"}
    )
    assert cross.status_code == 403


@pytest.mark.parametrize("path", ["/api/projects/1/source-file", "/api/projects/1/contacts"])
def test_uploads_need_the_header_and_a_same_origin_request(client, project, path):
    name = "a.txt" if "source" in path else "a.csv"
    assert upload(client, path, name, b"abc", headers={}).status_code == 403
    evil = {**CSRF, "Origin": "http://evil.example"}
    assert upload(client, path, name, b"abc", headers=evil).status_code == 403


def test_the_host_check_covers_the_new_routes(project):
    foreign = TestClient(dash.create_app(), base_url="http://evil.example")
    assert foreign.get("/api/projects").status_code == 400
    assert foreign.get("/api/contacts-template/csv").status_code == 400


def test_replies_to_bad_input_never_echo_it(client, project):
    marker = "SECRET-MARKER-12345"
    bodies = [
        ("/api/projects", {"name": marker, "cooldown_days": "x" + marker}),
        ("/api/campaigns", {"name": marker, "countries": marker}),
        ("/api/projects/1/source", {"url": marker, "max_pages": marker}),
        ("/api/campaigns/1/move-reserve", {"from_campaign_id": marker}),
    ]
    for path, body in bodies:
        reply = post(client, path, body)
        assert reply.status_code in (400, 422) and marker not in reply.text
    odd = upload(client, import_url(project["id"]), marker + ".csv", b"junk\n", {"mode": marker})
    assert odd.status_code == 422 and marker not in odd.text


def test_a_failing_service_gives_a_safe_500_without_the_error_text(client, project, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("password=hunter2 /secret/path")

    monkeypatch.setattr(browse, "project_cards", boom)
    reply = client.get("/api/projects")
    assert reply.status_code == 500 and "hunter2" not in reply.text
    assert reply.json()["error"]["code"] == "internal_error"


def test_the_guess_email_switch_is_unreachable_from_the_dashboard():
    sources = [Path(dash.__file__)] + sorted(STATIC.glob("*.*"))
    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert "allow_guess" not in text, path.name
        if path.suffix == ".py":  # the page may read a campaign's setting, the server never sets it
            assert "allow_risky_emails" not in text
    schema = dash.CampaignBody.model_json_schema()
    assert "options" not in schema["properties"]
    for model in (dash.ProjectBody, dash.ProjectChangeBody):
        values = model.model_json_schema()["properties"]["email_policy"]
        assert "guess" not in str(values)


# --- plain language: no internal words in the pages --------------------------------------------

BANNED = (
    r"\bQA\b",
    r"\bdesignations?\b",
    r"\bL\d\d\b",
    r"ai_web_search",
    r"confirm=true",
    r"\bevidence\b",
    r"\brole groups?\b",
    r"\bAI review\b",
    r"\bquality checks?\b",
    r"\breserve\b",
    r"\bsignals?\b",
    r"\bcooldown\b",
    r"\bsuppression\b",
    r"\boffer profile\b",
    r"\bdry.run\b",
    r"\bconfirmed\b",
    r"\bMCP\b",
    r"\bcursor\b",
    r"\bendpoint\b",
    r"\bJSON\b",
    r"\bpayload\b",
    r"\bAPI keys?\b",
    r"\bdomain_ok\b",
    r"\bemail_policy\b",
)
ALLOWED_STRINGS = {"leadgen suppression remove"}
CASE_SENSITIVE = {r"\bQA\b", r"\bL\d\d\b", r"\bMCP\b", r"\bJSON\b"}
_CODE_VALUE = re.compile(r"^[a-z0-9_\-./:#?=&\[\]@%{}$+*|!<>~^\,()'\"]*$")  # no spaces, no capitals
_STRING = re.compile(r"""'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"|`(?:[^`\\]|\\.)*`""", re.DOTALL)


def js_strings(source: str) -> list[str]:
    """The string literals of a script, without comments (a rough but strict reader)."""
    without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    without_comments = re.sub(r"(?m)^\s*//.*$|\s//\s.*$", "", without_block)
    return [m.group(0)[1:-1] for m in _STRING.finditer(without_comments)]


def visible(text: str) -> bool:
    """Whether a literal is words a person can read (code values and ids are lower case)."""
    bare = re.sub(r"\$\{[^}]*\}", "", text)  # a template's `${...}` parts are code
    return text not in ALLOWED_STRINGS and not _CODE_VALUE.match(bare)


def banned_in(text: str) -> list[str]:
    text = text.replace("Lead Generator", "")  # the product's own name
    text = re.sub(r"\$\{[^}]*\}", "", text)  # a template's `${...}` parts are code, not words
    return [
        pattern
        for pattern in BANNED
        if re.search(pattern, text, 0 if pattern in CASE_SENSITIVE else re.IGNORECASE)
    ]


def test_the_pages_use_no_internal_words():
    problems = []
    for text in js_strings((STATIC / "app.js").read_text(encoding="utf-8")):
        if visible(text):
            problems += [(pattern, text) for pattern in banned_in(text)]
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    page = re.sub(r"<(?:script|style)[^>]*>.*?</(?:script|style)>", "", html, flags=re.DOTALL)
    problems += [(pattern, "index.html") for pattern in banned_in(re.sub(r"<[^>]+>", " ", page))]
    assert not problems, problems


def test_the_text_scan_catches_a_banned_word():
    strings = js_strings("const a = 'Run the QA check'; // QA in a comment\nconst b = 'qa_pass';")
    assert [s for s in strings if visible(s)] == ["Run the QA check"]
    assert banned_in("Run the QA check") == [r"\bQA\b"] and banned_in("Lead Generator") == []
    assert banned_in("Saved for a later campaign") == [] and banned_in("Use the cooldown")


def test_the_status_labels_follow_the_plain_words():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    for expected in ("Ready", "Needs a look", "Saved for a later campaign", "Checked by AI",
                     "Job role", "Where we found them", "Not verified (won't be used)",
                     "Known contact", "Warm"):  # fmt: skip
        assert expected in js, expected
