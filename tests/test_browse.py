"""Dashboard read views (leads, messages, exports, replies). All people are invented."""

from __future__ import annotations

import os
import subprocess

import pytest
from dashboard_world import seed
from qa_world import add_email

from leadgen import db
from leadgen.core import paths
from leadgen.core.text import slugify
from leadgen.services import browse
from leadgen.services import campaign as camp
from leadgen.services.browse import BrowseError
from leadgen.services.campaign import CampaignError


@pytest.fixture
def world():
    return seed()


def names(reply):
    return [x["raw_name"] for x in reply["leads"]]


def test_list_leads_shape_and_row_states(world):
    reply = browse.list_leads(world["campaign"])
    assert reply["total"] == 4 and reply["has_more"] is False
    by = {x["raw_name"]: x for x in reply["leads"]}
    assert by["Aria Tan"]["email_status"] == "verified" and by["Aria Tan"]["ai_verdict"] == "pass"
    assert by["Aria Tan"]["email"] == "aria.tan@northwind.example"
    assert by["Ben Carter"]["email_status"] == "guess" and by["Ben Carter"]["qa_block"] > 0
    assert by["Ben Carter"]["ai_verdict"] == "pending"
    assert by["Cara Ng"]["email_status"] == "none" and by["Cara Ng"]["ai_verdict"] == "no_messages"
    assert by["Dev Rao"]["evidence_origin"] == "ai_web_search"
    assert by["Dev Rao"]["spot_check"] == "pending"


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"status": "review"}, ["Cara Ng"]),
        ({"designation": "HR / People heads"}, ["Ben Carter"]),
        ({"email_status": "verified"}, ["Aria Tan"]),
        ({"email_status": "none"}, ["Cara Ng"]),
        ({"email_status": "risky"}, ["Dev Rao"]),
        ({"qa": "block"}, ["Ben Carter", "Dev Rao"]),
        ({"qa": "clean"}, ["Cara Ng"]),
        ({"qa": "warn"}, ["Aria Tan"]),
        ({"ai_verdict": "pass"}, ["Aria Tan"]),
        ({"ai_verdict": "pending"}, ["Ben Carter", "Dev Rao"]),
        ({"ai_verdict": "no_messages"}, ["Cara Ng"]),
        ({"search": "harbour"}, ["Ben Carter", "Cara Ng"]),
        ({"search": "ARIA"}, ["Aria Tan"]),
        ({"search": "nobody"}, []),
        ({"search": "%"}, []),
        ({"search": "_"}, []),
    ],
)
def test_list_leads_filters(world, filters, expected):
    assert names(browse.list_leads(world["campaign"], **filters)) == expected


def test_company_filter_and_paging(world):
    reply = browse.list_leads(world["campaign"], company_id=world["company_a"])
    assert names(reply) == ["Aria Tan", "Dev Rao"]
    page2 = browse.list_leads(world["campaign"], page=2, page_size=3)
    assert names(page2) == ["Dev Rao"] and page2["has_more"] is False and page2["total"] == 4
    assert browse.list_leads(world["campaign"], page_size=1000)["page_size"] == browse.PAGE_SIZE_MAX


def test_ai_verdict_fix_and_reject_win_over_pending(world):
    with db.session() as conn:
        conn.execute("UPDATE messages SET ai_review = 'fix' WHERE person_id = ?", (world["ben"],))
        conn.execute(
            "UPDATE messages SET ai_review = 'reject' WHERE person_id = ?", (world["dev"],)
        )
    assert names(browse.list_leads(world["campaign"], ai_verdict="fix")) == ["Ben Carter"]
    assert names(browse.list_leads(world["campaign"], ai_verdict="reject")) == ["Dev Rao"]


def test_bad_filter_values_and_unknown_campaign(world):
    for key in ("email_status", "qa", "ai_verdict"):
        with pytest.raises(BrowseError) as err:
            browse.list_leads(world["campaign"], **{key: "bogus"})
        assert err.value.as_response()["error"]["code"] == "invalid_value"
    with pytest.raises(BrowseError):
        browse.list_leads(world["campaign"], page="x")
    with pytest.raises(CampaignError):
        browse.list_leads(999)


def test_lead_filters(world):
    reply = browse.lead_filters(world["campaign"])
    assert "L&D / Training heads" in reply["designations"]
    assert {c["name"] for c in reply["companies"]} == {"Northwind Logistics", "Harbour Freight"}
    assert reply["people_counts"] == {"confirmed": 3, "review": 1}


def test_get_person_full_view(world):
    reply = browse.get_person(world["ben"])
    assert reply["person"]["raw_name"] == "Ben Carter"
    assert reply["company"]["name"] == "Harbour Freight"
    assert reply["email"]["status"] == "guess" and len(reply["emails"]) == 1
    msg = reply["messages"][0]
    assert msg["channel"] == "linkedin_message"
    assert {i["code"] for i in msg["issues"]} >= {"M03", "M05"}
    assert all(i["title"] for i in msg["issues"])


def test_get_person_best_email_first_and_not_found(world):
    add_email(world["ben"], "verified", "ben@harbour.example")
    reply = browse.get_person(world["ben"])
    assert [e["status"] for e in reply["emails"]] == ["verified", "guess"]
    assert reply["email"]["email"] == "ben@harbour.example"
    with pytest.raises(BrowseError) as err:
        browse.get_person(9999)
    assert err.value.code == "not_found"


def test_list_messages_filters_and_counts(world):
    reply = browse.list_messages(world["campaign"])
    assert reply["total"] == 4 and reply["counts"] == {"email": 1, "linkedin_message": 3}
    assert all("person" in m and "company" in m for m in reply["messages"])
    assert browse.list_messages(world["campaign"], channel="email")["total"] == 1
    assert browse.list_messages(world["campaign"], ai_review="pass")["total"] == 2
    assert browse.list_messages(world["campaign"], ai_review="none")["total"] == 2
    assert browse.list_messages(world["campaign"], status="qa_fail")["total"] == 2
    assert browse.list_messages(world["campaign"], search="guarantee")["total"] == 1
    ben = browse.list_messages(world["campaign"], search="Carter")["messages"][0]
    assert ben["issues"] and ben["status"] == "qa_fail"
    with pytest.raises(BrowseError):
        browse.list_messages(world["campaign"], channel="sms")
    with pytest.raises(BrowseError):
        browse.list_messages(world["campaign"], ai_review="maybe")


def test_list_and_open_exports(world, monkeypatch):
    folder = paths.exports_dir(slugify(camp.get_campaign(world["campaign"])["name"]))
    (folder / "old.csv").write_text("a", encoding="utf-8")
    newer = folder / "new.csv"
    newer.write_text("bb", encoding="utf-8")
    os.utime(folder / "old.csv", (1, 1))
    (folder / "sub").mkdir()
    reply = browse.list_exports(world["campaign"])
    assert reply["folder"] == str(folder)
    assert [f["name"] for f in reply["files"]] == ["new.csv", "old.csv"]
    assert reply["files"][0]["size"] == 2

    opened = []
    monkeypatch.setattr(browse, "_platform", lambda: "win32")
    monkeypatch.setattr(browse, "_startfile", opened.append)
    assert browse.open_exports_folder(world["campaign"]) == {"ok": True, "folder": str(folder)}
    assert opened == [folder]


def test_open_folder_other_systems_and_failure(world, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd: calls.append(cmd))
    monkeypatch.setattr(browse, "_platform", lambda: "darwin")
    assert browse.open_exports_folder(world["campaign"])["ok"]
    monkeypatch.setattr(browse, "_platform", lambda: "linux")
    assert browse.open_exports_folder(world["campaign"])["ok"]
    assert [c[0] for c in calls] == ["open", "xdg-open"]

    def boom(cmd):
        raise OSError("no opener")

    monkeypatch.setattr(subprocess, "Popen", boom)
    reply = browse.open_exports_folder(world["campaign"])
    assert reply["ok"] is False and reply["error"]["code"] == "cannot_open"


def test_recent_replies_and_filter(world):
    reply = browse.recent_replies()
    assert [e["type"] for e in reply["events"]] == ["reply"]
    assert reply["events"][0]["person"] == "Aria Tan"
    assert browse.recent_replies(world["campaign"])["events"]
    with pytest.raises(CampaignError):
        browse.recent_replies(999)


def test_campaign_overview_spot_check_and_tasks(world):
    with db.session() as conn:
        db.insert(conn, "search_tasks", campaign_id=world["campaign"], kind="people", query="q1")
        db.insert(
            conn,
            "search_tasks",
            campaign_id=world["campaign"],
            kind="people",
            query="q2",
            status="submitted",
        )
    reply = browse.campaign_overview(world["campaign"])
    assert reply["campaign"]["name"].startswith("SG logistics")
    assert reply["search_tasks"] == {"open": 1, "submitted": 1, "skipped": 0}
    assert reply["spot_check"] == {"ai_leads": 1, "state": "incomplete", "answers": {"pending": 1}}
